#
# Copyright (c) SAS Institute Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


'''
Tools for manipulating recipes and source troves.
'''

import hashlib
import logging
import os
import shutil
import tempfile

from conary.build import cook
from conary.build import lookaside
from conary.build import recipe as cny_recipe
from conary.build import use
from conary.build.loadrecipe import RecipeLoader
from conary.build.lookaside import RepositoryCache
from conary.changelog import ChangeLog
from conary.conaryclient import filetypes
from conary.deps import deps
from conary.files import FileFromFilesystem, ThawFile
from conary.lib.util import mkdirChain, joinPaths
from conary.repository import filecontents
from conary.repository import trovesource
from conary.repository.changeset import ChangedFileTypes, ChangeSet
from conary.trove import Trove
from conary.versions import Branch, Revision

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

        self._makeProddef()
        self._makeRecipes()
        if self.helper.plan.depMode:
            return
        self._fetchOldChangeSets()
        self._merge()

    def _makeRecipes(self):
        """Take pristine upstream sources, mangle them, and load the result."""
        for package in self.packages:
            recipe = package.getRecipe()
            finalRecipe = mangle(package, recipe)
            package.recipeFiles[package.getRecipeName()] = finalRecipe
            if self.helper.plan.dumpRecipes:
                with open(os.path.join(self.helper.plan.recipeDir,
                        package.getRecipeName()), 'w') as fobj:
                    fobj.write(finalRecipe)
                continue

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

    def _makeProddef(self):
        pkg = self._getProddefPackage()
        if not pkg:
            return

        fname = 'product-definition.xml'
        proddef = pkg.recipeFiles.get(fname)
        finalProddef = proddef % pkg.getMangleData().get('macros')
        pkg.recipeFiles[fname] = finalProddef

    def _getProddefPackage(self):
        pkgs = [ x for x in self.packages
            if x.name == 'product-definition:source' ]
        if pkgs:
            return pkgs[0]
        return False

    def _fetchOldChangeSets(self):
        """
        Fetch old versions of each trove, where they can be found
        and are suitably sane.
        """
        versionSpecs = []
        latestSpecs = []
        targetLabel = self.helper.plan.getTargetLabel()
        for package in self.packages:
            version = macro.expand(package.getBaseVersion(), package)
            versionSpecs.append((package.getName(),
                '%s/%s' % (targetLabel, version), None))
            latestSpecs.append((package.getName(), str(targetLabel), None))

        # Pick the new version for each package by querying all existing
        # versions (including markremoved ones) with the same version.
        results = self.helper.getRepos().findTroves(None, versionSpecs,
            allowMissing=True, getLeaves=False,
            troveTypes=trovesource.TROVE_QUERY_ALL)
        for package, (recipeText, recipeObj), query in zip(
                self.packages, self.recipes, versionSpecs):
            newVersion = _createVersion(package, self.helper,
                    recipeObj.version)
            existingVersions = [x[1] for x in results.get(query, ())]
            while newVersion in existingVersions:
                newVersion.incrementSourceCount()
            package.nextVersion = newVersion

        # Grab the latest existing version so we can reuse autosources from it
        results = self.helper.getRepos().findTroves(None, latestSpecs,
                allowMissing=True)
        toGet = []
        oldVersions = []
        for package, query in zip(self.packages, latestSpecs):
            if not results.get(query):
                oldVersions.append(None)
                continue
            n, v, f = max(results[query])
            toGet.append((n, (None, None), (v, f), True))
            oldVersions.append((n, v, f))

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
        # If this is not None then all ephemeral sources will still be fetched
        # but will be placed in this directory instead.
        if self.helper.plan.ephemeralSourceDir:
            ephDir = self.helper.makeEphemeralDir()
        else:
            ephDir = None

        def _addFile(path, contents, isText):
            if path in oldFiles:
                # Always recycle pathId if available.
                pathId, _, oldFileId, oldFileVersion = oldFiles[path]
            else:
                pathId = hashlib.md5(path).digest()
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
                fileVersion = newTrove.getVersion()

            filesToAdd[fileId] = (fileStream, fileHelper.contents, isText)
            newTrove.addFile(pathId, path, fileVersion, fileId)

        for package, (recipeText, recipeObj), oldTrove in zip(
                self.packages, self.recipes, self.oldTroves):

            filesToAdd = {}
            oldFiles = {}
            if oldTrove is not None:
                for pathId, path, fileId, fileVer in oldTrove.getNewFileList():
                    oldFiles[path] = (pathId, path, fileId, fileVer)
            newTrove = Trove(package.name, package.nextVersion, deps.Flavor())
            newTrove.setFactory(package.targetConfig.factory)

            # Add upstream files to new trove. Recycle pathids from the old
            # version.
            # LAZY: assume that everything other than the recipe is binary.
            # Conary has a magic module, but it only accepts filenames!
            for path, contents in package.recipeFiles.iteritems():
                isText = path == package.getRecipeName()
                _addFile(path, contents, isText)

            # Collect requested auto sources from recipe.
            if cny_recipe.isPackageRecipe(recipeObj):
                recipeFiles = dict((os.path.basename(x.getPath()), x)
                    for x in recipeObj.getSourcePathList())
                newFiles = set(x[1] for x in newTrove.iterFileList())

                needFiles = set(recipeFiles) - newFiles
                for autoPath in needFiles:
                    source = recipeFiles[autoPath]
                    if (autoPath in oldFiles
                            and not self.helper.plan.refreshSources
                            and not source.ephemeral):
                        # File exists in old version.
                        pathId, path, fileId, fileVer = oldFiles[autoPath]
                        newTrove.addFile(pathId, path, fileVer, fileId)
                        continue

                    if source.ephemeral and not ephDir:
                        continue

                    # File doesn't exist; need to create it.
                    if source.ephemeral:
                        laUrl = lookaside.laUrl(source.getPath())
                        tempDir = joinPaths(ephDir,
                                os.path.dirname(laUrl.filePath()))
                        mkdirChain(tempDir)
                    else:
                        tempDir = tempfile.mkdtemp()
                        deleteDirs.add(tempDir)
                    snapshot = _getSnapshot(self.helper, package, source,
                            tempDir)

                    if not source.ephemeral:
                        autoPathId = hashlib.md5(autoPath).digest()
                        autoObj = FileFromFilesystem(snapshot, autoPathId)
                        autoObj.flags.isAutoSource(set=True)
                        autoObj.flags.isSource(set=True)
                        autoFileId = autoObj.fileId()

                        autoContents = filecontents.FromFilesystem(snapshot)
                        filesToAdd[autoFileId] = (autoObj, autoContents, False)
                        newTrove.addFile(autoPathId, autoPath,
                            newTrove.getVersion(), autoFileId)

            # If the old and new troves are identical, just use the old one.
            if oldTrove and _sourcesIdentical(
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

            package.setDownstreamVersion(newTrove.getVersion())
            log.debug('Created %s=%s', newTrove.getName(), newTrove.getVersion())

        if doCommit:
            cook.signAbsoluteChangesetByConfig(changeSet, self.helper.cfg)
            f = tempfile.NamedTemporaryFile(dir=os.getcwd(), suffix='.ccs',
                    delete=False)
            f.close()
            changeSet.writeToFile(f.name)
            try:
                self.helper.getRepos().commitChangeSet(changeSet)
            except:
                log.error("Error committing changeset to repository, "
                        "failed changeset is saved at %s", f.name)
                raise
            else:
                os.unlink(f.name)

        for path in deleteDirs:
            shutil.rmtree(path)


def _createVersion(package, helper, version):
    '''
    Pick a new version for package I{package} using I{version} as the
    new upstream version.
    '''
    newBranch = Branch([helper.plan.getTargetLabel()])
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

    def getFactory(trv):
        if isinstance(trv, Trove):
            return trv.getFactory()
        else:
            return trv.getTroveInfo().factory()

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

    if getFactory(oldTrove) != getFactory(newTrove):
        return False

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
    loader = RecipeLoader(recipePath, helper.cfg, helper.getRepos(),
            directory=helper.plan.recipeDir,
            factory=package.targetConfig.factory,
            )
    recipeClass = loader.getRecipe()

    dummybranch = Branch([helper.plan.getTargetLabel()])
    dummyrev = Revision('1-1')
    dummyver = dummybranch.createVersion(dummyrev)
    macros = {
            'buildlabel': dummybranch.label().asString(),
            'buildbranch': dummybranch.asString(),
            }
    # Instantiate and setup if needed
    if cny_recipe.isPackageRecipe(recipeClass):
        lcache = RepositoryCache(helper.getRepos(),
                refreshFilter=lambda x: helper.plan.refreshSources)

        recipeObj = recipeClass(helper.cfg, lcache, [], macros,
            lightInstance=True)
        recipeObj.sourceVersion = dummyver
        recipeObj.populateLcache()
        if not recipeObj.needsCrossFlags():
            recipeObj.crossRequires = []
        recipeObj.loadPolicy()
        recipeObj.setup()
        return recipeObj
    elif cny_recipe.isGroupRecipe(recipeClass):
        # Only necessary for dependency analysis
        recipeObj = recipeClass(helper.getRepos(), helper.cfg,
                dummybranch.label(), helper.cfg.buildFlavor, None,
                extraMacros=macros)
        recipeObj.sourceVersion = dummyver
        recipeObj.loadPolicy()
        recipeObj.setup()
        return recipeObj

    # Just the class is enough for everything else
    return recipeClass


def _getSnapshot(helper, package, source, tempDir):
    """
    Create a snapshot of a revision-control source in a temporary location.

    Returns a tuple C{(path, delete)} where C{delete} is C{None} or a directory
    that should be deleted after use.
    """
    if not hasattr(source, 'createSnapshot'):
        fullPath = source.fetch(
                refreshFilter=lambda x: helper.plan.refreshSources)
        if not source.ephemeral:
            return fullPath
        name = os.path.basename(fullPath)
        newName = os.path.join(tempDir, name)
        shutil.move(fullPath, newName)
        return newName

    fullPath = source.getFilename()
    snapPath = os.path.join(tempDir, os.path.basename(fullPath))
    scm = package.getSCM()
    fetched = False
    if scm:
        try:
            scm.fetchArchive(source, snapPath)
            fetched = True
        except NotImplementedError:
            pass
    if not fetched:
        reposPath = '/'.join(fullPath.split('/')[:-1] + [ source.name ])
        repositoryDir = source.recipe.laReposCache.getCachePath(
                source.recipe.name, reposPath)
        if not os.path.exists(repositoryDir):
            mkdirChain(os.path.dirname(repositoryDir))
            source.createArchive(repositoryDir)
        else:
            source.updateArchive(repositoryDir)
        source.createSnapshot(repositoryDir, snapPath)

    if fullPath.endswith('.bz2') and not checkBZ2(snapPath):
        raise RuntimeError("Autosource file %r is corrupt!" % (snapPath,))

    return snapPath
