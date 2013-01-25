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
        if hasattr(hg, 'peer'):
            repo = hg.peer(hg_ui, {}, uri)
        else:
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
    if hasattr(hg, 'peer'):
        # hg >= 2.3
        if hg.islocal(uri):
            repo = hg.repository(hgui, uri)
        else:
            remote = hg.peer(hgui, {}, uri)
            repo = updateCache(hgui, uri, remote, cacheDir)
    else:
        # hg < 2.3
        repo = hg.repository(hgui, uri)
        if not hg.islocal(repo):
            repo = updateCache(hgui, uri, repo, cacheDir)
    cctx = repo.changectx(rev)

    # Pull out and return the recipe file contents.
    subpath = subpath.strip('/').split('/')
    splen = len(subpath)

    # Pre-solve directory symlinks
    changed = True
    while changed:
        changed = False
        for n in range(len(subpath), 0, -1):
            filepath = '/'.join(subpath[:n])
            if filepath not in cctx:
                continue
            fctx = cctx.filectx(filepath)
            if 'l' in fctx.flags():
                newpath = os.path.join(os.path.dirname(filepath), fctx.data())
                newpath = os.path.normpath(newpath)
                subpath = newpath.strip('/').split('/')
                splen = len(subpath)
                changed = True
            break


    files = {}
    for filepath in cctx:
        name = filepath.split('/')
        if name[:splen] == subpath:
            fctx = cctx.filectx(filepath)
            while 'l' in fctx.flags():
                # Resolve symlinks
                newpath = os.path.join(os.path.dirname(filepath), fctx.data())
                newpath = os.path.normpath(newpath)
                fctx = cctx.filectx(newpath)
            newname = '/'.join(name[splen:])
            files[newname] = fctx.data()

    return files
