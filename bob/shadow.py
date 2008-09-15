#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
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
from conary.repository import filecontents
from conary.repository.changeset import ChangedFileTypes, ChangeSet
from conary.trove import Trove
from conary.versions import Revision
from rmake import compat

from bob import macro
from bob.mangle import mangle
from bob.util import findFile, makeContainer

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
        self.jobs = []
        self.sourceChangeSet = None
        self.oldChangeSet = None

    def addPackage(self, package):
        self.jobs.append(ShadowJob(package=package))

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

                print 'Loading %s' % job.package.getName()
                recipeObj = _loadRecipe(self.helper, job.package, recipePath)
                print 'Loaded %s=%s' % (job.package.getName(), recipeObj.version)
            finally:
                shutil.rmtree(tempDir)

            recipes.append((finalRecipe, recipeObj))

        return sourceChangeSet, sourceTroves, recipes

    def _merge(self):
        changeSet = ChangeSet()
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

            newTrove = Trove(job.sourceTrove)
            newTrove.changeVersion(newVersion)

            # Remove all auto sources.
            for pathId, path, fileId, fileVer in list(newTrove.iterFileList()):
                fileChange = self.sourceChangeSet.getFileChange(None, fileId)
                fileObj = ThawFile(fileChange, pathId)
                if fileObj.flags.isAutoSource():
                    newTrove.removeFile(pathId)

            # Create a filestream for the recipe.
            recipeFileHelper = filetypes.RegularFile(contents=job.recipe,
                config=True)
            recipePathId = findFile(job.sourceTrove,
                job.package.getRecipeName())[0]
            recipeFile = recipeFileHelper.get(recipePathId)
            recipeFile.flags.isSource(set=True)
            recipeFileId = recipeFile.fileId()

            filesToAdd[recipeFileId] = (recipeFile, recipeFileHelper.contents,
                True)

            # Substitute the recipe into the new trove.
            newTrove.removeFile(recipePathId)
            newTrove.addFile(recipePathId, job.package.getRecipeName(),
                newVersion, recipeFileId)

            # Collect requested auto sources from recipe.
            recipeFiles = dict((os.path.basename(x.getPath()), x)
                for x in job.recipeObj.getSourcePathList())
            sourceFiles = dict((x[1], x) for x in job.sourceTrove.getNewFileList())
            oldFiles = job.oldTrove and dict((x[1], x)
                for x in job.oldTrove.getNewFileList()) or {}
            newFiles = set(x[1] for x in newTrove.iterFileList())

            needFiles = set(recipeFiles) - newFiles
            modified = False
            for autoPath in needFiles:
                if autoPath in sourceFiles:
                    pathId, path, fileId, fileVer = sourceFiles[autoPath]
                    newTrove.addFile(pathId, path, fileVer, fileId)
                elif autoPath in oldFiles:
                    pathId, path, fileId, fileVer = oldFiles[autoPath]
                    newTrove.addFile(pathId, path, fileVer, fileId)
                else:
                    source = recipeFiles[autoPath]
                    cached = source.fetch()

                    autoPathId = os.urandom(16)
                    autoObj = FileFromFilesystem(cached, autoPathId)
                    autoObj.flags.isAutoSource(set=True)
                    autoObj.flags.isSource(set=True)
                    autoFileId = autoObj.fileId()

                    autoContents = filecontents.FromFilesystem(cached)
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

        import sys; sys.exit(0)

        if doCommit:
            if compat.ConaryVersion().signAfterPromote():
                cook.signAbsoluteChangeset(changeSet, None)
            self.helper.getRepos().commitChangeSet(changeSet)

    def _getOldChangeSets(self):
        """
        Fetch old versions of each trove, where they can be found
        and are suitably sane.
        """
        oldSpecs = []
        targetLabel = self.helper.plan.targetLabel
        for job in self.jobs:
            version = macro.expand(job.package.getBaseVersion(), job.package)
            oldSpecs.append((job.package.getName(),
                '%s/%s' % (targetLabel, version), None))

        print 'Finding\n  ' + '\n  '.join('%s=%s' % x[:2] for x in oldSpecs)

        results = self.helper.getRepos().findTroves(None, oldSpecs,
            allowMissing=True)

        toGet = []
        oldVersions = []
        for job, query in zip(self.jobs, oldSpecs):
            package = job.package
            targetBranch = _getTargetBranch(package, targetLabel)
            if query in results:
                # An old version was found.
                assert len(results[query]) == 1
                oldVersion = results[query][0][1]
                parentVersion = package.getUpstreamVersion()

                # In all cases, the old trove must be on the same
                # branch
                saneBranch = oldVersion.branch() == targetBranch

                # In sibling clones, the trailing revision up to the
                # source's parent's shadow count must be identical.
                saneRevision = True
                if package.isSiblingClone() and saneBranch:
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
                if saneBranch and saneRevision:
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

    targetLabel = helper.plan.targetLabel

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
        newBranch = sourceBranch.createShadow(helper.plan.targetLabel)
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
