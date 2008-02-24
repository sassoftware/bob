#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Tools for manipulating recipes and source troves.
'''

import logging
import os
import re
import shutil
import tempfile

from conary import checkin
from conary import state
from conary import versions
from conary.lib import util

from bob import macro

log = logging.getLogger('bob.mangle')


filters = []


def mangle(parent, trove, recipe):
    '''
    Feed the given recipe through all available filters.
    '''
    for f in filters:
        recipe = f(parent, trove, recipe)
    return recipe

def _register(fun):
    filters.append(fun)
    return fun


re_version = re.compile('^(\s+)version\s*=.*?$', re.M)
@_register
def version(parent, trove, recipe):
    '''
    Update the recipe's version to reflect any configured pattern.
    '''

    if not parent.targets.has_key(trove):
        return recipe

    rawVersion = parent.targets[trove].version
    newVersion = macro.expand(rawVersion, parent, trove=trove)
    return re_version.sub(r'\1version = %r' % (newVersion,), recipe)


re_source = re.compile(
    r'''^(\s+)(\S+)\.addMercurialSnapshot\s*\(.*?\).*?$''', re.M | re.S)
@_register
def source(parent, trove, recipe):
    '''
    Modify addMercurialSnapshot calls to use the selected revision.
    '''

    if not parent.targets.has_key(trove):
        return recipe
    if not parent.targets[trove].hg:
        return recipe

    repo = parent.targets[trove].hg
    if not parent.hg.has_key(repo):
        logging.warning('Trove %s references undefined Hg repository %s',
            trove, repo)

    uri, node = parent.hg[repo]
    return re_source.sub(r'\1\2.addMercurialSnapshot(%r, tag=%r)'
        % (str(uri), str(node)), recipe)


def mangleTrove(parent, name, version):
    '''
    Check out a given source trove, mangle it, and commit it to a shadow on
    the internal repository.
    '''

    oldKey = parent.buildcfg.signatureKey
    oldMap = parent.buildcfg.signatureKeyMap
    oldInteractive = parent.buildcfg.interactive

    package = name.split(':')[0]
    sourceName = package + ':source'
    newTrove = None

    work_dir = tempfile.mkdtemp(prefix='bob-mangle-%s' % package)
    upstream_dir = tempfile.mkdtemp(prefix='bob-upstream-%s' % package)
    oldWd = os.getcwd()

    try:
        # Prevent any questions from being asked during check-in
        parent.buildcfg.signatureKey = None
        parent.buildcfg.signatureKeyMap = {}
        parent.buildcfg.interactive = False

        # Find source
        log.debug('Finding trove %s=%s' % (sourceName, version))
        matches = parent.nc.findTrove(None, (sourceName, str(version), None))
        sourceVersion = max(x[1] for x in matches)

        # Check out upstream version and fetch recipe
        log.debug('Checking out upstream trove %s=%s' % (sourceName,
            sourceVersion))
        try:
            checkin.checkout(parent.nc, parent.buildcfg, upstream_dir,
                ['%s=%s' % (sourceName, sourceVersion)])
            upstream_recipe = open(os.path.join(upstream_dir,
                '%s.recipe' % package)).read()
        finally:
            shutil.rmtree(upstream_dir)

        # Shadow to rMake's internal repos
        log.debug('Shadowing %s to rMake repository', package)
        targetLabel = parent.buildcfg.getTargetLabel(version)
        skipped, cs = parent.cc.createShadowChangeSet(str(targetLabel),
            [(sourceName, sourceVersion, deps.parseFlavor(''))])
        if not skipped:
            cook.signAbsoluteChangeset(cs, None)
            parent.nc.commitChangeSet(cs)

        # Check out the shadow
        log.debug('Checking out internal trove %s' % sourceName)
        shadowBranch = sourceVersion.createShadow(targetLabel).branch()
        checkin.checkout(parent.nc, parent.buildcfg, work_dir,
            ['%s=%s' % (sourceName, shadowBranch)])
        os.chdir(work_dir)

        # Copy the upstream checkout into the shadow checkout
        clone_checkout(upstream_dir, work_dir)

        # Mangle the upstream recipe and commit it to the shadow
        recipe = mangle(parent, package, upstream_recipe)
        open('%s.recipe' % package, 'w').write(recipe)

        # Commit changes back to the internal repos
        log.resetErrorOccurred()
        checkin.commit(parent.nc, parent.buildcfg,
            parent.cfg.commitMessage, force=True)
        if log.errorOccurred():
            raise RuntimeError()

        # Figure out the new version and return
        wd_state = state.ConaryStateFromFile('CONARY',
            parent.nc).getSourceState()
        newTrove = wd_state.getNameVersionFlavor()
    finally:
        parent.buildcfg.signatureKey = oldKey
        parent.buildcfg.signatureKeyMap = oldMap
        parent.buildcfg.interactive = oldInteractive
        os.chdir(oldWd)
        shutil.rmtree(work_dir)
        shutil.rmtree(upstream_dir)

    return newTrove

def clone_checkout(source_dir, dest_dir):
    '''
    Copy all the files from one conary checkout into another, and set up the
    target so that when committed, all files in the source checkout will be
    committed, and all files not in the source checkout will be removed.
    '''

    # Collect state objects and file lists from source and dest
    source_state = state.ConaryStateFromFile(os.path.join(source_dir,
        'CONARY'))
    source_trove_state = source_state.getSourceState()
    source_paths = set(x[1] for x in source_trove_state.iterFileList())
    source_info = dict((x[1], source_trove_state.fileInfo[x[0]])
        for x in source_trove_state.iterFileList())

    dest_state = state.ConaryStateFromFile(os.path.join(dest_dir, 'CONARY'))
    dest_trove_state = dest_state.getSourceState()
    dest_paths = set(x[1] for x in dest_trove_state.iterFileList())

    # Copy all files from the source checkout into the dest
    for path in source_paths:
        if source_info[path].isAutoSource:
            continue
        source_path = os.path.join(source_dir, path)
        dest_path = os.path.join(dest_dir, path)
        util.mkdirChain(os.path.dirname(dest_path))
        shutil.copy2(source_path, dest_path)

    # Add files to the dest checkout state that are not currently tracked,
    # and remove files from the dest checkout that should no longer be tracked
    old_cwd = os.getcwd()
    os.chdir(dest_dir)
    try:
        for path in source_paths - dest_paths:
            file_info = source_info[path]
            if file_info.isAutoSource:
                continue
            is_config = file_info.isConfig
            checkin.addFiles([path], text=is_config, binary=not is_config)
        for path in dest_paths - source_paths:
            checkin.removeFile(path)
    finally:
        os.chdir(old_cwd)
