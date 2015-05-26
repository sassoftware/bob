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
import inspect
import logging
import os
import shutil
import tempfile

from conary.build import cook
from conary.build import lookaside
from conary.build import grouprecipe
from conary.build import recipe as cny_recipe
from conary.build import use
from conary.build.loadrecipe import RecipeLoader
from conary.build.loadrecipe import RecipeLoaderFromSourceDirectory
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
        self._makePlatdef()
        self._makeRecipes()
        if self.helper.plan.depMode:
            return
        self._fetchOldChangeSets()
        self._merge()

    def _makeRecipes(self):
        """Take pristine upstream sources, mangle them, and load the result."""
        if self.helper.plan.dumpRecipes:
            recipeDir = self.helper.plan.recipeDir
        else:
            recipeDir = tempfile.mkdtemp(prefix='bob-')
        # Dump all the recipes out at once in case they have interdependencies
        finalRecipes = []
        for package in self.packages:
            recipe = package.getRecipe()
            finalRecipe = mangle(package, recipe)
            package.recipeFiles[package.getRecipeName()] = finalRecipe
            with open(os.path.join(recipeDir, package.getRecipeName()
                    ), 'w') as fobj:
                fobj.write(finalRecipe)
            finalRecipes.append(finalRecipe)
        for package, finalRecipe in zip(self.packages, finalRecipes):
            recipeObj = _loadRecipe(self.helper, package,
                    os.path.join(recipeDir, package.getRecipeName()))
            self.recipes.append((finalRecipe, recipeObj))
        if not self.helper.plan.dumpRecipes:
            shutil.rmtree(recipeDir)

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

    def _makePlatdef(self):
        pkg = self._getPlatdefPackage()
        if not pkg:
            return
        platDefs = [ x for x in pkg.recipeFiles if
                        x.startswith('plat') and x.endswith('xml') ]
        for fname in platDefs:
            platdef = pkg.recipeFiles.get(fname)
            finalPlatdef = platdef % pkg.getMangleData().get('macros')
            pkg.recipeFiles[fname] = finalPlatdef

    def _getPlatdefPackage(self):
        pkgs = [ x for x in self.packages
            if x.name == 'platform-definition:source' ]
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
                trvCs = self.oldChangeSet.getNewTroveVersion(*oldVersion)
                self.oldTroves.append(Trove(trvCs))
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
                for pathId, path, fileId, fileVer in oldTrove.iterFileList():
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

            # Collect requested auto sources from recipe. Unknown recipe types
            # will not be loaded so recipeObj will be the class, so assume
            # these have no sources.
            if not inspect.isclass(recipeObj):
                recipeFiles = dict((os.path.basename(x.getPath()), x)
                    for x in recipeObj.getSourcePathList())
                newFiles = set(x[1] for x in newTrove.iterFileList())

                needFiles = set(recipeFiles) - newFiles
                for autoPath in needFiles:
                    source = recipeFiles[autoPath]
                    if getattr(source, 'contents', None
                            ) and not source.sourcename:
                        # Ignore trove scripts that have inline contents
                        continue
                    if not autoPath:
                        log.error("bob does not support 'gussed' filenames; "
                                "please use a full path for source '%s' in "
                                "package %s", source.getPath(), package.name)
                        raise RuntimeError("Unsupported source action")
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

                    if not source.ephemeral and snapshot:
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
                package.setDownstreamVersion(oldTrove.getVersion())
                log.debug('Skipped %s=%s', oldTrove.getName(),
                        oldTrove.getVersion())
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
    def getSHA1(fileId):
        for changeSet in changeSets:
            if isinstance(changeSet, ChangeSet):
                fileChange = changeSet.getFileChange(None, fileId)
                if not fileChange:
                    continue

                fileObj = ThawFile(fileChange, None)
                return fileObj.contents.sha1()
            else:
                fileObj = changeSet.get(fileId, None)
                if not fileObj:
                    continue

                return fileObj[0].contents.sha1()

        raise KeyError("file is not in any changeset")

    if oldTrove.getFactory() != newTrove.getFactory():
        return False

    oldPaths = dict((x[1], x) for x in oldTrove.iterFileList())
    newPaths = dict((x[1], x) for x in newTrove.iterFileList())
    if set(oldPaths) != set(newPaths):
        return False

    for path, (oldPathId, _, oldFileId, _) in oldPaths.items():
        newPathId, _, newFileId, _ = newPaths[path]
        if oldFileId == newFileId:
            continue
        if getSHA1(oldFileId) != getSHA1(newFileId):
            return False
    return True


def _makeSourceTrove(package, helper):
    cs = ChangeSet()
    filesToAdd = {}
    ver = macro.expand(package.getBaseVersion(), package)
    version = _createVersion(package, helper, ver)
    latestSpec = (package.getName(), str(version.trailingLabel()), None)
    results = helper.getRepos().findTroves(None,[latestSpec],allowMissing=True,
                        getLeaves=False,troveTypes=trovesource.TROVE_QUERY_ALL)
    if results:
        existingVersions = [x[1] for x in results.get(latestSpec, ())]
        while version in existingVersions:
            version.incrementSourceCount()

    new = Trove(package.name, version, deps.Flavor())
    new.setFactory(package.targetConfig.factory)
    message = "Temporary Source for %s" % version
    message = message.rstrip() + "\n"
    new.changeChangeLog(ChangeLog(name=helper.cfg.name,
                contact=helper.cfg.contact, message=message))
    for path, contents in package.recipeFiles.iteritems():
        isText = path == package.getRecipeName()
        pathId = hashlib.md5(path).digest()
        fileHelper = filetypes.RegularFile(contents=contents,
                    config=isText)
        fileStream = fileHelper.get(pathId)
        fileStream.flags.isSource(set=True)
        fileId = fileStream.fileId()
        fileVersion = new.getVersion()
        key = pathId + fileId
        filesToAdd[key] = (fileStream, fileHelper.contents, isText)
        new.addFile(pathId, path, fileVersion, fileId)
    new.invalidateDigests()
    new.computeDigests()
    for key, (fileObj, fileContents, cfgFile) in filesToAdd.items():
        cs.addFileContents(fileObj.pathId(), fileObj.fileId(),
                    ChangedFileTypes.file, fileContents, cfgFile)
        cs.addFile(None, fileObj.fileId(), fileObj.freeze())
    cs.newTrove(new.diff(None, absolute=True)[0])
    return new.getNameVersionFlavor(), cs


def tempSourceTrove(recipePath, package, helper):
    from conary import state
    from conary import checkin
    from conary import trove
    from conary.lib import util as cnyutil
    pkgname = package.name.split(':')[0]
    nvf, cs = _makeSourceTrove(package, helper)
    targetDir = os.path.join(os.path.dirname(recipePath), pkgname)
    cnyutil.mkdirChain(targetDir)
    sourceStateMap = {}
    pathMap = {}
    conaryStateTargets = {}
    troveCs = cs.getNewTroveVersion(*nvf)
    trv = trove.Trove(troveCs)
    sourceState = state.SourceState(nvf[0], nvf[1], nvf[1].branch())
    if trv.getFactory():
        sourceState.setFactory(trv.getFactory())
    conaryState = state.ConaryState(helper.cfg.context, sourceState)
    sourceStateMap[trv.getNameVersionFlavor()] = sourceState
    conaryStateTargets[targetDir] = conaryState
    for (pathId, path, fileId, version) in troveCs.getNewFileList():
        pathMap[(nvf, path)] = (targetDir, pathId, fileId, version)
    # Explode changeset contents.
    checkin.CheckoutExploder(cs, pathMap, sourceStateMap)
    # Write out CONARY state files.
    for targetDir, conaryState in conaryStateTargets.iteritems():
            conaryState.write(targetDir + '/CONARY')
    return trv, targetDir

def _loadRecipe(helper, package, recipePath):
    # Load the recipe
    use.setBuildFlagsFromFlavor(package.getPackageName(),
            helper.cfg.buildFlavor, error=False)
    if package.targetConfig.factory and package.targetConfig.factory != 'factory':
        sourceTrove, targetDir = tempSourceTrove(recipePath, package, helper)
        loader = RecipeLoaderFromSourceDirectory(sourceTrove, repos=helper.getRepos(),
                            cfg=helper.cfg, parentDir=targetDir,
                            labelPath=helper.plan.installLabelPath
                            )
    else:
        sourceTrove = None
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
    lcache = RepositoryCache(helper.getRepos(),
            refreshFilter=lambda x: helper.plan.refreshSources)
    if recipeClass.getType() == cny_recipe.RECIPE_TYPE_GROUP:
        recipeObj = recipeClass(
                repos=helper.getRepos(),
                cfg=helper.cfg,
                label=dummybranch.label(),
                flavor=helper.cfg.buildFlavor,
                laReposCache=lcache,
                extraMacros=macros,
                )
    elif recipeClass.getType() in [
            cny_recipe.RECIPE_TYPE_PACKAGE,
            cny_recipe.RECIPE_TYPE_INFO,
            cny_recipe.RECIPE_TYPE_CAPSULE,
            ]:
        recipeObj = recipeClass(
                cfg=helper.cfg,
                laReposCache=lcache,
                srcdirs=[],
                extraMacros=macros,
                lightInstance=True,
                )
    else:
        return recipeClass
    if not recipeObj.needsCrossFlags():
        recipeObj.crossRequires = []
    if sourceTrove is None:
        recipeObj.populateLcache()
    recipeObj.sourceVersion = dummyver
    recipeObj.loadPolicy()
    recipeObj.setup()
    return recipeObj


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
