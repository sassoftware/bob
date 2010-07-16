#
# Copyright (c) 2010 rPath, Inc.
#
# All rights reserved.
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


def clone(hgui, uri, cacheDir):
    """Make or update a clone of the given repository."""
    dirPath = uri.split('//', 1)[-1]
    dirPath = dirPath.replace('/', '_')
    dirPath = os.path.join(cacheDir, dirPath, 'hg')

    if not os.path.isdir(dirPath):
        os.makedirs(dirPath)
    if os.path.isdir(dirPath + '/.hg'):
        repo = hg.repository(hgui, dirPath)
    else:
        repo = hg.repository(hgui, dirPath, create=True)
    remote = hg.repository(hgui, uri)
    repo.pull(remote, force=True)
    return repo


def getRecipe(uri, rev, subpath, cacheDir):
    """Get file contents for a subset of the given hg repository."""
    hgui = ui.ui()
    repo = hg.repository(hgui, uri)
    if not hg.islocal(repo):
        repo = clone(hgui, uri, cacheDir)

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
