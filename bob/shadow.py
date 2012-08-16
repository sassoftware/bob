#
# Copyright (c) 2011 rPath, Inc.
#

'''
Tools for manipulating recipes and source troves.
'''

import copy
import logging
import os
import shutil
import tempfile

from conary.build import cook
from conary.build import use
from conary.build.loadrecipe import RecipeLoader
from conary.build.lookaside import RepositoryCache
from conary.build.recipe import isPackageRecipe
from conary.changelog import ChangeLog
from conary.conaryclient import filetypes
from conary.deps import deps
from conary.files import FileFromFilesystem, ThawFile
from conary.lib.util import mkdirChain
from conary.repository import filecontents
from conary.repository import trovesource
from conary.repository.changeset import ChangedFileTypes, ChangeSet
from conary.trove import Trove
from conary.versions import Revision
from rmake import compat

from bob import macro
from bob.mangle import mangle
from bob.util import checkBZ2, findFile, makeContainer

log = logging.getLogger('bob.shadow')

(SP_INIT, SP_GET_UPSTREAM, SP_GET_RECIPE,
    SP_FIND_OLD, SP_GET_OLD, SP_DONE) = range(6)

#pylint: disable-msg=C0103
# makeContainer is a class factory, ergo ShadowJob is a class
ShadowJob = makeContainer('ShadowJob', ['package', 'sourceTrove', 'oldTrove',
    'recipe', 'recipeObj'])


class ShadowBatch(object):
    def __init__(self, helper):
        self.helper = helper
        self.sources = set()
        self.jobs = []
        self.sourceChangeSet = None
        self.oldChangeSet = None

    def addPackage(self, package):
        source = package.getUpstreamNameVersionFlavor()
        if source not in self.sources:
            self.jobs.append(ShadowJob(package=package))
            self.sources.add(source)

    def shadow(self, mangleData):
        if not self.jobs:
            # Short-circuit
            return

        for job in self.jobs:
            job.package.setMangleData(mangleData)

        self.sourceChangeSet, sourceTroves, recipes = self._getRecipes()
        self.oldChangeSet, oldTroves = self._getOldChangeSets()

        for job, sourceTrove, (recipe, recipeObj), oldTrove in zip(
          self.jobs, sourceTroves, recipes, oldTroves):
            job.sourceTrove = sourceTrove
            job.recipe = recipe
            job.recipeObj = recipeObj
            job.oldTrove = oldTrove

        self._merge()

    def _getRecipes(self):
        sourceJob = []
        for job in self.jobs:
            sourceJob.append((job.package.getName(), (None, None),
                (job.package.getUpstreamVersion(), deps.Flavor()), True))

        sourceChangeSet = self.helper.createChangeSet(sourceJob)
        sourceTroves = []
        fileJob = []
        for job in self.jobs:
            sourceTrove = sourceChangeSet.getNewTroveVersion(
                *job.package.getUpstreamNameVersionFlavor())
            fileJob.append(findFile(sourceTrove,
                job.package.getRecipeName())[2:4])
            sourceTroves.append(sourceTrove)

        allContents = self.helper.getRepos().getFileContents(fileJob)

        recipes = []
        for job, contents in zip(self.jobs, allContents):
            recipe = contents.get().read()
            finalRecipe = mangle(job.package, recipe)

            # Write to disk for convenience, then load
            tempDir = tempfile.mkdtemp(prefix=('%s-'
                % job.package.getPackageName()))
            try:
                recipePath = os.path.join(tempDir,
                    job.package.getRecipeName())
                fObj = open(recipePath, 'w')
                fObj.write(finalRecipe)
                fObj.close()

                recipeObj = _loadRecipe(self.helper, job.package, recipePath)
            finally:
                shutil.rmtree(tempDir)

            recipes.append((finalRecipe, recipeObj))

        return sourceChangeSet, sourceTroves, recipes

    def _merge(self):
        changeSet = ChangeSet()
        deleteDirs = set()
        doCommit = False

        for job in self.jobs:
            filesToAdd = {}

            # Create a new trove.
            if job.oldTrove:
                newVersion = job.oldTrove.getNewVersion().copy()
                newVersion.incrementSourceCount()
                assert newVersion.trailingRevision().getVersion(
                    ) == job.recipeObj.version
            else:
                newVersion = _createVersion(job.package, self.helper,
                    job.recipeObj.version)

            newTrove = Trove(job.sourceTrove.getName(), newVersion,
                    deps.Flavor())

            # Copy non-autosource, non-recipe files from the source trove.
            recipePathId = None
            for pathId, path, fileId, fileVer in (
                    job.sourceTrove.getNewFileList()):
                if path == job.package.getRecipeName():
                    recipePathId = pathId
                    continue
                fileChange = self.sourceChangeSet.getFileChange(None, fileId)
                fileObj = ThawFile(fileChange, pathId)
                if fileObj.flags.isAutoSource():
                    continue
                newTrove.addFile(pathId, path, fileVer, fileId)
            assert recipePathId

            # Add the recipe.
            recipeFileHelper = filetypes.RegularFile(contents=job.recipe,
                config=True)
            recipeFile = recipeFileHelper.get(recipePathId)
            recipeFile.flags.isSource(set=True)
            recipeFileId = recipeFile.fileId()

            filesToAdd[recipeFileId] = (recipeFile, recipeFileHelper.contents,
                True)
            newTrove.addFile(recipePathId, job.package.getRecipeName(),
                newVersion, recipeFileId)

            # Collect requested auto sources from recipe.
            modified = False
            if isPackageRecipe(job.recipeObj):
                recipeFiles = dict((os.path.basename(x.getPath()), x)
                    for x in job.recipeObj.getSourcePathList())
                sourceFiles = dict((x[1], x)
                    for x in job.sourceTrove.getNewFileList())
                oldFiles = job.oldTrove and dict((x[1], x)
                    for x in job.oldTrove.getNewFileList()) or {}
                oldFiles = dict()
                newFiles = set(x[1] for x in newTrove.iterFileList())

                needFiles = set(recipeFiles) - newFiles
                for autoPath in needFiles:
                    if autoPath in sourceFiles:
                        pathId, path, fileId, fileVer = sourceFiles[autoPath]
                        newTrove.addFile(pathId, path, fileVer, fileId)
                    elif autoPath in oldFiles:
                        pathId, path, fileId, fileVer = oldFiles[autoPath]
                        newTrove.addFile(pathId, path, fileVer, fileId)
                    else:
                        source = recipeFiles[autoPath]
                        snapshot, delete = _getSnapshot(self.helper, source)
                        if delete:
                            deleteDirs.add(delete)

                        autoPathId = os.urandom(16)
                        autoObj = FileFromFilesystem(snapshot, autoPathId)
                        autoObj.flags.isAutoSource(set=True)
                        autoObj.flags.isSource(set=True)
                        autoFileId = autoObj.fileId()

                        autoContents = filecontents.FromFilesystem(snapshot)
                        filesToAdd[autoFileId] = (autoObj, autoContents, False)
                        newTrove.addFile(autoPathId, autoPath,
                            newVersion, autoFileId)

                        modified = True

            # If no autosources were missing, check if anything
            # has changed at all.
            if not modified and job.oldTrove and _sourcesIdentical(
              job.oldTrove, newTrove, [self.oldChangeSet,
              self.sourceChangeSet, filesToAdd]):
                job.package.setDownstreamVersion(job.oldTrove.getNewVersion())
                log.debug('Skipped %s=%s', job.oldTrove.getName(),
                    job.oldTrove.getNewVersion())
                continue

            # Add files and contents to changeset.
            for fileId, (fileObj, fileContents, cfgFile) in filesToAdd.items():
                changeSet.addFileContents(fileObj.pathId(), fileObj.fileId(),
                    ChangedFileTypes.file, fileContents, cfgFile)
                changeSet.addFile(None, fileObj.fileId(), fileObj.freeze())

            # Create a changelog entry.
            changeLog = ChangeLog(
                name=self.helper.cfg.name, contact=self.helper.cfg.contact,
                message=self.helper.plan.commitMessage + '\n')
            newTrove.changeChangeLog(changeLog)

            # Calculate trove digests and add the trove to the changeset
            newTrove.invalidateDigests()
            newTrove.computeDigests()
            newTroveCs = newTrove.diff(None, absolute=True)[0]
            changeSet.newTrove(newTroveCs)
            doCommit = True

            job.package.setDownstreamVersion(newVersion)
            log.debug('Created %s=%s', newTrove.getName(), newVersion)
            # TODO: maybe save the downstream trove object for group recursion

        if doCommit:
            if compat.ConaryVersion().signAfterPromote():
                cook.signAbsoluteChangeset(changeSet, None)
            self.helper.getRepos().commitChangeSet(changeSet)

        for path in deleteDirs:
            shutil.rmtree(path)

    def _getOldChangeSets(self):
        """
        Fetch old versions of each trove, where they can be found
        and are suitably sane.
        """
        oldSpecs = []
        targetLabel = self.helper.plan.getTargetLabel()
        for job in self.jobs:
            version = macro.expand(job.package.getBaseVersion(), job.package)
            oldSpecs.append((job.package.getName(),
                '%s/%s' % (targetLabel, version), None))

        # Get *all* troves with the right version so that we can find
        # versions that are latest on the correct branch but have
        # been occluded by a newer version on the wrong branch. Also include
        # removed troves so that we don't collide with them when creating
        # versions.
        results = self.helper.getRepos().findTroves(None, oldSpecs,
            allowMissing=True, getLeaves=False,
            troveTypes=trovesource.TROVE_QUERY_ALL)

        toGet = []
        oldVersions = []
        for job, query in zip(self.jobs, oldSpecs):
            package = job.package
            targetBranch = _getTargetBranch(package, targetLabel)
            if query in results:
                # Filter the results to just those on the desired branch.
                oldVersionsForJob = [x[1] for x in results[query]
                                       if x[1].branch() == targetBranch]
                if not oldVersionsForJob:
                    oldVersions.append(None)
                    continue
                oldVersion = _maxVersion(oldVersionsForJob)

                parentVersion = package.getUpstreamVersion()

                # In sibling clones, the trailing revision up to the
                # source's parent's shadow count must be identical.
                saneRevision = True
                if package.isSiblingClone():
                    oldRevision = oldVersion.trailingRevision()
                    oldCount = copy.deepcopy(oldRevision.sourceCount)

                    parentRevision = parentVersion.trailingRevision()
                    parentCount = copy.deepcopy(parentRevision.sourceCount)

                    parentLength = parentVersion.shadowLength() - 1
                    parentCount.truncateShadowCount(parentLength)
                    oldCount.truncateShadowCount(parentLength)

                    saneRevision = oldCount == parentCount

                # If all preconditions match, fetch the old version
                # so we can base the new version off that, or maybe
                # even use it as-is.
                if saneRevision:
                    toGet.append((package.getName(), (None, None),
                        (oldVersion, deps.Flavor()), True))
                    oldVersions.append((package.getName(), oldVersion,
                        deps.Flavor()))
                    continue
            oldVersions.append(None)

        oldChangeSet = self.helper.createChangeSet(toGet)
        oldTroves = []
        for job, oldVersion in zip(self.jobs, oldVersions):
            if oldVersion:
                oldTroves.append(oldChangeSet.getNewTroveVersion(*oldVersion))
            else:
                oldTroves.append(None)
        return oldChangeSet, oldTroves


def _getTargetBranch(package, targetLabel):
    sourceBranch = package.getUpstreamVersion().branch()
    if not package.isSiblingClone():
        return sourceBranch.createShadow(targetLabel)
    else:
        return sourceBranch.createSibling(targetLabel)


def _createVersion(package, helper, version):
    '''
    Pick a new version for package I{package} using I{version} as the
    new upstream version.
    '''

    targetLabel = helper.plan.getTargetLabel()

    sourceVersion = package.getUpstreamVersion()
    sourceBranch = sourceVersion.branch()
    sourceRevision = sourceVersion.trailingRevision()

    if package.isSiblingClone():
        # Siblings should start with the parent version
        assert sourceVersion.hasParentVersion()
        assert sourceVersion.trailingRevision().version == version
        parentVersion = sourceVersion.parentVersion()
        newVersion = parentVersion.createShadow(targetLabel)
    elif sourceRevision.version == version:
        # If shadowing and the upstream versions match, then start
        # with the source version's source count.
        newVersion = sourceVersion.createShadow(targetLabel)
    else:
        # Otherwise create one with a "modified upstream version."
        # ex. 1.2.3-0.1
        newBranch = sourceBranch.createShadow(helper.plan.getTargetLabel())
        newRevision = Revision('%s-0' % version)
        newVersion = newBranch.createVersion(newRevision)
    newVersion.incrementSourceCount()

    return newVersion


def _sourcesIdentical(oldTrove, newTrove, changeSets):
    '''
    Return C{True} if C{oldTrove} and C{newTrove} have the same
    contents.
    '''
    def listFiles(trv):
        if isinstance(trv, Trove):
            return list(trv.iterFileList())
        else:
            return trv.getNewFileList()

    def getSHA1(fileId, pathId):
        for changeSet in changeSets:
            if isinstance(changeSet, ChangeSet):
                fileChange = changeSet.getFileChange(None, fileId)
                if not fileChange:
                    continue

                fileObj = ThawFile(fileChange, pathId)
                return fileObj.contents.sha1()
            else:
                fileObj = changeSet.get(fileId, None)
                if not fileObj:
                    continue

                return fileObj[0].contents.sha1()

        assert False, "file is not in any changeset"

    oldPaths = dict((x[1], x) for x in listFiles(oldTrove))
    newPaths = dict((x[1], x) for x in listFiles(newTrove))

    if set(oldPaths) != set(newPaths):
        # Different paths
        return False

    for path, (oldPathId, _, oldFileId, _) in oldPaths.items():
        newPathId, _, newFileId, _ = newPaths[path]

        if oldFileId == newFileId:
            # Same fileid
            continue

        oldSHA1 = getSHA1(oldFileId, oldPathId)
        newSHA1 = getSHA1(newFileId, newPathId)
        if oldSHA1 != newSHA1:
            # Contents differ
            return False

    return True


def _loadRecipe(helper, package, recipePath):
    # Load the recipe
    buildFlavor = sorted(package.getFlavors())[0]

    buildFlavor = deps.overrideFlavor(helper.cfg.buildFlavor, buildFlavor)
    use.setBuildFlagsFromFlavor(package.getPackageName(), buildFlavor,
        error=False)
    loader = RecipeLoader(recipePath, helper.cfg, helper.getRepos())
    recipeClass = loader.getRecipe()

    # Instantiate and setup if a package recipe
    if isPackageRecipe(recipeClass):
        lcache = RepositoryCache(helper.getRepos())
        macros = {'buildlabel': helper.plan.sourceLabel.asString(),
            'buildbranch': package.getUpstreamVersion().branch().asString()}
        recipeObj = recipeClass(helper.cfg, lcache, [], macros,
            lightInstance=True)
        recipeObj.sourceVersion = package.getUpstreamVersion()
        recipeObj.populateLcache()
        if not recipeObj.needsCrossFlags():
            recipeObj.crossRequires = []
        recipeObj.loadPolicy()
        recipeObj.setup()
        return recipeObj

    # Just the class is enough for everything else
    return recipeClass


def _maxVersion(versions):
    """
    Get the highest-numbered from a set of source C{versions}.

    Uses timestamps to figure out the latest upstream version, then
    compares source counts to compare within all versions with that
    upstream version. This way, versions with oddly-ordered timestamps
    don't throw off the new version generator.

    For example, with these versions and timestamps:
    /abcd-1     10:00
    /efgh-1     15:00
    /efgh-2     13:00

    /efgh-2 would be picked as the maximum version, even though it
    is older than /efgh-1.
    """
    maxRevision = max(versions).trailingRevision().version
    candidates = [x for x in versions
            if x.trailingRevision().version == maxRevision]
    maxSourceCount = max(x.trailingRevision().sourceCount for x in candidates)
    return max(x for x in candidates
            if x.trailingRevision().sourceCount == maxSourceCount)


def _getSnapshot(helper, source):
    """
    Create a snapshot of a revision-control source in a temporary location.

    Returns a tuple C{(path, delete)} where C{delete} is C{None} or a directory
    that should be deleted after use.
    """
    # This function deals exclusively with SCC actions
    if not hasattr(source, 'createSnapshot'):
        return source.fetch(), None

    fullPath = source.getFilename()
    reposPath = '/'.join(fullPath.split('/')[:-1] + [ source.name ])
    repositoryDir = source.recipe.laReposCache.getCachePath(source.recipe.name, reposPath)

    if not os.path.exists(repositoryDir):
        mkdirChain(os.path.dirname(repositoryDir))
        source.createArchive(repositoryDir)
    else:
        source.updateArchive(repositoryDir)

    tempDir = tempfile.mkdtemp()
    snapPath = os.path.join(tempDir, os.path.basename(fullPath))
    source.createSnapshot(repositoryDir, snapPath)

    if fullPath.endswith('.bz2') and not checkBZ2(snapPath):
        raise RuntimeError("Autosource file %r is corrupt!" % (snapPath,))

    return snapPath, tempDir
