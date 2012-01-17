#
# Copyright (c) rPath, Inc.
#

'''
Tools for manipulating recipes and source troves.
'''

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
from conary.repository.changeset import ChangedFileTypes, ChangeSet
from conary.trove import Trove
from conary.versions import Branch, Revision
from rmake import compat

from bob import macro
from bob.mangle import mangle
from bob.util import checkBZ2

log = logging.getLogger('bob.shadow')

(SP_INIT, SP_GET_UPSTREAM, SP_GET_RECIPE,
    SP_FIND_OLD, SP_GET_OLD, SP_DONE) = range(6)


class ShadowBatch(object):
    def __init__(self, helper):
        self.helper = helper
        self.sources = set()
        self.oldChangeSet = None

        # Parallel lists
        self.packages = []
        self.recipes = []
        self.oldTroves = []

    def addPackage(self, package):
        if package.name in self.sources:
            return
        self.sources.add(package.name)
        self.packages.append(package)

    def shadow(self):
        if not self.packages:
            return

        self._makeRecipes()
        self._fetchOldChangeSets()
        self._merge()

    def _makeRecipes(self):
        """Take pristine upstream sources, mangle them, and load the result."""
        for package in self.packages:
            recipe = package.getRecipe()
            finalRecipe = mangle(package, recipe)
            package.recipeFiles[package.getRecipeName()] = finalRecipe

            # Write to disk for convenience, then load
            tempDir = tempfile.mkdtemp(prefix=('%s-'
                % package.getPackageName()))
            try:
                recipePath = os.path.join(tempDir, package.getRecipeName())
                fObj = open(recipePath, 'w')
                fObj.write(finalRecipe)
                fObj.close()

                recipeObj = _loadRecipe(self.helper, package, recipePath)
            finally:
                shutil.rmtree(tempDir)

            self.recipes.append((finalRecipe, recipeObj))

    def _fetchOldChangeSets(self):
        """
        Fetch old versions of each trove, where they can be found
        and are suitably sane.
        """
        oldSpecs = []
        targetLabel = self.helper.plan.targetLabel
        targetBranch = Branch([targetLabel])
        for package in self.packages:
            version = macro.expand(package.getBaseVersion(), package)
            oldSpecs.append((package.getName(),
                '%s/%s' % (targetLabel, version), None))

        # Get *all* troves with the right version so that we can find
        # versions that are latest on the correct branch but have
        # been occluded by a newer version on the wrong branch.
        results = self.helper.getRepos().findTroves(None, oldSpecs,
            allowMissing=True, getLeaves=False)

        toGet = []
        oldVersions = []
        for package, query in zip(self.packages, oldSpecs):
            if query not in results:
                oldVersions.append(None)
                continue

            # Ignore versions on the wrong branch.
            oldVersionsForJob = [x[1] for x in results[query]
                    if x[1].branch() == targetBranch]
            if not oldVersionsForJob:
                oldVersions.append(None)
                continue
            oldVersion = _maxVersion(oldVersionsForJob)

            # If all preconditions match, fetch the old version so we can base
            # the new version off that, or maybe even use it as-is.
            toGet.append((package.getName(), (None, None),
                (oldVersion, deps.Flavor()), True))
            oldVersions.append((package.getName(), oldVersion, deps.Flavor()))

        self.oldChangeSet = self.helper.createChangeSet(toGet)
        for package, oldVersion in zip(self.packages, oldVersions):
            if oldVersion:
                self.oldTroves.append(
                        self.oldChangeSet.getNewTroveVersion(*oldVersion))
            else:
                self.oldTroves.append(None)

    def _merge(self):
        changeSet = ChangeSet()
        deleteDirs = set()
        doCommit = False

        def _addFile(path, contents, isText):
            if path in oldFiles:
                # Always recycle pathId if available.
                pathId, _, oldFileId, oldFileVersion = oldFiles[path]
            else:
                pathId = os.urandom(16)
                oldFileId = oldFileVersion = None

            fileHelper = filetypes.RegularFile(contents=contents,
                    config=isText)
            fileStream = fileHelper.get(pathId)
            fileStream.flags.isSource(set=True)
            fileId = fileStream.fileId()

            # If the fileId matches, recycle the fileVersion too.
            if fileId == oldFileId:
                fileVersion = oldFileVersion
            else:
                fileVersion = newVersion

            filesToAdd[fileId] = (fileStream, fileHelper.contents, isText)
            newTrove.addFile(pathId, path, fileVersion, fileId)

        for package, (recipeText, recipeObj), oldTrove in zip(
                self.packages, self.recipes, self.oldTroves):

            filesToAdd = {}
            oldFiles = {}

            # Create a new trove.
            if oldTrove is not None:
                newVersion = oldTrove.getNewVersion().copy()
                newVersion.incrementSourceCount()
                assert newVersion.trailingRevision().getVersion(
                    ) == recipeObj.version

                for pathId, path, fileId, fileVer in oldTrove.getNewFileList():
                    oldFiles[path] = (pathId, path, fileId, fileVer)
            else:
                newVersion = _createVersion(package, self.helper,
                        recipeObj.version)

            newTrove = Trove(package.name, newVersion, deps.Flavor())

            # Add upstream files to new trove. Recycle pathids from the old
            # version.
            # LAZY: assume that everything other than the recipe is binary.
            # Conary has a magic module, but it only accepts filenames!
            for path, contents in package.recipeFiles.iteritems():
                isText = path == package.getRecipeName()
                _addFile(path, contents, isText)

            # Collect requested auto sources from recipe.
            modified = False
            if isPackageRecipe(recipeObj):
                recipeFiles = dict((os.path.basename(x.getPath()), x)
                    for x in recipeObj.getSourcePathList())
                newFiles = set(x[1] for x in newTrove.iterFileList())

                needFiles = set(recipeFiles) - newFiles
                for autoPath in needFiles:
                    if autoPath in oldFiles:
                        # File exists in old version.
                        pathId, path, fileId, fileVer = oldFiles[autoPath]
                        newTrove.addFile(pathId, path, fileVer, fileId)

                    else:
                        # File doesn't exist; need to create it.
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

            # If the old and new troves are identical, just use the old one.
            if not modified and oldTrove and _sourcesIdentical(
                    oldTrove, newTrove, [self.oldChangeSet, filesToAdd]):
                package.setDownstreamVersion(oldTrove.getNewVersion())
                log.debug('Skipped %s=%s', oldTrove.getName(),
                        oldTrove.getNewVersion())
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

            package.setDownstreamVersion(newVersion)
            log.debug('Created %s=%s', newTrove.getName(), newVersion)

        if doCommit:
            if compat.ConaryVersion().signAfterPromote():
                cook.signAbsoluteChangeset(changeSet, None)
            self.helper.getRepos().commitChangeSet(changeSet)

        for path in deleteDirs:
            shutil.rmtree(path)


def _createVersion(package, helper, version):
    '''
    Pick a new version for package I{package} using I{version} as the
    new upstream version.
    '''
    newBranch = Branch([helper.plan.targetLabel])
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
    use.setBuildFlagsFromFlavor(package.getPackageName(),
            helper.cfg.buildFlavor, error=False)
    loader = RecipeLoader(recipePath, helper.cfg, helper.getRepos())
    recipeClass = loader.getRecipe()

    # Instantiate and setup if a package recipe
    if isPackageRecipe(recipeClass):
        lcache = RepositoryCache(helper.getRepos())

        dummybranch = Branch([helper.plan.targetLabel])
        dummyrev = Revision('1-1')
        dummyver = dummybranch.createVersion(dummyrev)
        macros = {
                'buildlabel': dummybranch.label().asString(),
                'buildbranch': dummybranch.asString(),
                }

        recipeObj = recipeClass(helper.cfg, lcache, [], macros,
            lightInstance=True)
        recipeObj.sourceVersion = dummyver
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
