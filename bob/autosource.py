#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import logging
import os

from conary.build.packagerecipe import AbstractPackageRecipe
from conary.build.source import addMercurialSnapshot
from conary.files import FileFromFilesystem
from conary.repository import filecontents
from conary.repository.changeset import ChangedFileTypes

from bob.util import findFile

log = logging.getLogger('bob.autosource')


def getHgSource(troveCs, recipeObj):
    '''
    First, find which file would be created for the mercurial snapshot
    added in C{recipeObj}. Then, try to get the I{pathId} and I{fileId}
    of that file from C{troveCs}. If the snapshot add call is present,
    but the file is not in the changeset (or the changeset was C{None}),
    return the source instance for that snapshot. Otherwise, return
    C{None}.

    If a source instance is returned, that means that the caller needs
    to use it to generate a snapshot and add it to the changeset being
    built.

    @param troveCs:   Trove changeset to search for the snapshot in
    @type  troveCs:   L{TroveChangeSet<conary.trove.TroveChangeSet>}
    @param recipeObj: Package recipe instance which may add a mercurial
                      snapshot
    @type  recipeObj: L{PackageRecipe
                      <conary.build.packagerecipe.PackageRecipe>}
    @rtype: L{addMercurialSnapshot
            <conary.build.source.addMercurialSnapshot>}
    '''

    # Recipe is not a package, so it can't have snapshots.
    if not isinstance(recipeObj, AbstractPackageRecipe):
        return None

    # Look for a mercurial snapshot
    for source in recipeObj._sources:
        if not isinstance(source, addMercurialSnapshot):
            continue

        if not troveCs:
            # If there's no old version, then we definitely need to
            # build a snapshot.
            return source

        path = source.getFilename()
        try:
            findFile(troveCs, path)
        except RuntimeError:
            # File not in the trove; return a source so upstream can
            # generate it
            return source
        else:
            # File is in the trove. Upstream doesn't need to do
            # anything.
            return None

    # No mercurial snapshot is being added. Upstream doesn't need to
    # do anything.
    return None


def addSnapshotToTrove(changeSet, newTrove, recipeObj, hgSource):
    '''
    Create a Hg snapshot from the source C{hgSource} in recipe
    C{recipeObj} and add it to trove C{newTrove} and container changeset
    C{changeSet}.

    @param changeSet: New changeset to add the snapshot to
    @type  changeSet: L{ChangeSet<conary.repository.changeset.ChangeSet>}
    @param newTrove:  New trove to add the snapshot to
    @type  newtrove:  L{Trove<conary.trove.Trove>}
    @param recipeObj: PackageRecipe associated with C{newTrove}
    @type  recipeObj: L{PackageRecipe
                      <conary.build.packagerecipe.PackageRecipe>}
    @param hgSource:  Source object from C{recipeObj} whose snapshot
                      should be generated and added
    @type  hgSource:  L{addMercurialSnapshot
                      <conary.build.source.addMercurialSnapshot>}
    '''

    # Cull files that don't belong, e.g. the old snapshot
    currentFiles = dict((path, pathId)
        for (pathId, path, _, _) in newTrove.iterFileList())
    newPaths = [os.path.basename(x.getPath())
        for x in recipeObj.getSourcePathList()]
    newPaths.append(newTrove.getName().split(':')[0] + '.recipe')

    removePaths = set(currentFiles) - set(newPaths)
    for path in removePaths:
        pathId = currentFiles[path]
        log.debug('Removing source %s' % path)
        newTrove.removeFile(pathId)

    # Generate the snapshot (or pull it from a cache)
    realPath = hgSource.fetch()
    path = os.path.basename(realPath)
    pathId = os.urandom(16)

    # Create a file from the snapshot
    hgFile = FileFromFilesystem(realPath, pathId)
    hgFile.flags.isAutoSource(set=True)
    hgFile.flags.isSource(set=True)
    fileId = hgFile.fileId()

    # Add the file to the new trove
    contents = filecontents.FromFilesystem(realPath)
    changeSet.addFileContents(pathId, fileId, ChangedFileTypes.file,
        contents, False)
    changeSet.addFile(None, fileId, hgFile.freeze())
    newTrove.addFile(pathId, path, newTrove.getVersion(), fileId)
