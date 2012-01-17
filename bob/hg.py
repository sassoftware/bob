#
# Copyright (c) rPath, Inc.
#

'''
Helper functions for dealing with mercurial (hg) repositories.
'''

import logging
import os

from mercurial import hg, ui
from mercurial.node import short

log = logging.getLogger('bob.hg')


def get_tip(uri):
    '''
    Fetch the tip of a given repository URI, using the tips file if present.
    '''

    try:
        for line in open('tips'):
            _uri, _tip = line.split(' ', 1)
            if _uri == uri:
                log.debug('Selected for %s revision %s (from tips)',
                    uri, _tip)
                return _tip[:12]
    except IOError:
        log.warning('No explicit revision given for repository %s, '
                'using latest', uri)

        hg_ui = ui.ui()
        repo = hg.repository(hg_ui, uri)
        tip = short(repo.heads()[0])
        log.debug('Selected for %s revision %s (from repo)', uri, tip)
        return tip
    else:
        raise RuntimeError('tips file exists, but does not contain '
            'repository %s' % uri)


def updateCache(hgui, uri, remote, cacheDir):
    """Make or update a clone of the given repository."""
    # Pick a directory to place the clone in. Using the same cache dir as
    # conary's lookaside means that the recipe load can use it as well.
    dirPath = uri.split('//', 1)[-1]
    dirPath = dirPath.replace('/', '_')
    dirPath = os.path.join(cacheDir, dirPath, 'hg')

    # Create the cache repo if needed.
    if not os.path.isdir(dirPath):
        os.makedirs(dirPath)
    if os.path.isdir(dirPath + '/.hg'):
        repo = hg.repository(hgui, dirPath)
    else:
        repo = hg.repository(hgui, dirPath, create=True)

    # Pull all new remote heads.
    log.debug("Pre-clone: repo %s has heads: %s", dirPath,
            ' '.join(sorted(short(x) for x in repo.heads())))
    repo.pull(remote, force=True)
    log.debug("Post-clone: repo %s has heads: %s", dirPath,
            ' '.join(sorted(short(x) for x in repo.heads())))

    # Try to work around weird issue that only happens to the conary tree where
    # .changectx(new_head) fails even though .heads() clearly shows that the
    # head exists.
    repo.invalidate()
    return repo


def getRecipe(uri, rev, subpath, cacheDir):
    """Get file contents for a subset of the given hg repository."""
    # Update the local repository cache.
    hgui = ui.ui()
    repo = hg.repository(hgui, uri)
    if not hg.islocal(repo):
        repo = updateCache(hgui, uri, repo, cacheDir)

    # Pull out and return the recipe file contents.
    subpath = subpath.strip('/').split('/')
    splen = len(subpath)

    files = {}
    cctx = repo.changectx(rev)
    for filepath in cctx:
        name = filepath.split('/')
        if name[:splen] == subpath:
            fctx = cctx.filectx(filepath)
            newname = '/'.join(name[splen:])
            files[newname] = fctx.data()

    return files
