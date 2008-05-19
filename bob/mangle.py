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
import sha
import shutil
import tempfile
import time

from conary import checkin
from conary import state
from conary.build import cook
from conary.build import use
from conary.lib import log as conary_log
from conary.lib import util
from rmake import compat

from bob import macro

log = logging.getLogger('bob.mangle')


FILTERS = []


##
## Decorators and helpers
##

def mangle(package, recipe):
    '''
    Feed the given recipe through all available filters.
    '''
    for f in FILTERS:
        recipe = f(package, recipe)
    return recipe


def _register(fun):
    '''Decorator: Add the function as a mangler for all mangled recipes.'''
    FILTERS.append(fun)
    return fun


def _require_target_attribute(*target_attributes):
    '''
    Decorator: require that the given attributes be set on a target section
    for the trove being mangled.
    '''

    def decorate(fun):
        'Actual decorator, returned by invoking the above'
        def wrapper(package, recipe):
            'Wrapper that validates inputs then invokes I{fun}'
            config = package.getTargetConfig()
            if not config:
                return recipe
            for attribute in target_attributes:
                if not config[attribute]:
                    return recipe
            return fun(package, recipe)
        return wrapper
    return decorate


##
## Manglers
##

RE_VERSION = re.compile('^(\s+)version\s*=.*?$', re.M)
@_register
@_require_target_attribute('version')
def mVersion(package, recipe):
    '''
    Update the recipe's version to reflect any configured pattern.
    '''

    rawVersion = package.getTargetConfig().version
    newVersion = macro.expand(rawVersion, package)
    return RE_VERSION.sub(r'\1version = %r' % (newVersion,), recipe)


RE_SOURCE = re.compile(
    r'''^(\s+)(\S+)\.addMercurialSnapshot\s*\(.*?\).*?$''', re.M | re.S)
@_register
@_require_target_attribute('hg')
def mSource(package, recipe):
    '''
    Modify addMercurialSnapshot calls to use the selected revision.
    '''

    repo = package.getTargetConfig().hg
    repoData = package.getMangleData()['hg']
    if not repoData.has_key(repo):
        logging.warning('Trove %s references undefined Hg repository %s',
            package.getPackageName(), repo)

    uri, node = repoData[repo]
    return RE_SOURCE.sub(r'\1\2.addMercurialSnapshot(%r, tag=%r)'
        % (str(uri), str(node)), recipe)


##
## Repository/commit code
##

def prepareTrove(package, mangleData, helper):
    '''
    Check out a given source trove, mangle it, and commit it to a shadow on
    the target repository.
    '''

    _start_time = time.time()
    cfg = helper.cfg

    oldKey = cfg.signatureKey
    oldMap = cfg.signatureKeyMap
    oldInteractive = cfg.interactive

    packageName = package.getPackageName()
    sourceName, sourceVersion = package.getUpstreamNameVersion()
    config = package.getTargetConfig()

    work_dir = tempfile.mkdtemp(prefix='bob-mangle-%s' % packageName)
    upstream_dir = tempfile.mkdtemp(prefix='bob-upstream-%s' % packageName)
    oldWd = os.getcwd()

    try:
        # Prevent any questions from being asked during check-in
        cfg.signatureKey = None
        cfg.signatureKeyMap = {}
        cfg.interactive = False

        # Check out upstream version and fetch recipe
        log.debug('Checking out upstream trove %s=%s',
            sourceName, sourceVersion)
        checkin.checkout(helper.getRepos(), cfg, upstream_dir,
            ['%s=%s' % (sourceName, sourceVersion)])
        upstream_recipe = open(os.path.join(upstream_dir,
            '%s.recipe' % packageName)).read()

        # Shadow or clone to downstream repository
        targetLabel = helper.plan.targetLabel
        sourceTup = package.getUpstreamNameVersionFlavor()
        if config and config.siblingClone:
            # Sibling clone (for derived packages)
            log.debug('Sibling cloning %s to rMake repository', packageName)
            sourceBranch = sourceVersion.branch()
            if not sourceBranch.hasParentBranch():
                raise RuntimeError('Cannot use siblingClone on source '
                    'troves that are not shadows')
            downstreamBranch = sourceBranch.parentBranch().createShadow(
                targetLabel)
            _, changeSet = helper.getClient().createCloneChangeSet(
                downstreamBranch, [sourceTup])
        else:
            # Shadow to the target repository
            log.debug('Shadowing %s to target repository', packageName)
            _, changeSet = helper.getClient().createShadowChangeSet(
                str(targetLabel), [sourceTup])
            downstreamBranch = sourceVersion.createShadow(
                targetLabel).branch()

        if changeSet:
            if compat.ConaryVersion().signAfterPromote():
                cook.signAbsoluteChangeset(changeSet, None)
            helper.getRepos().commitChangeSet(changeSet)

        # Check out the shadow
        log.debug('Checking out downstream trove %s=%s', sourceName,
            downstreamBranch)
        checkin.checkout(helper.getRepos(), cfg, work_dir,
            ['%s=%s' % (sourceName, downstreamBranch)])
        os.chdir(work_dir)

        # Compute the digest of the current downstream checkout
        old_digest = digest_checkout(work_dir)

        # Copy the upstream checkout into the downstream checkout
        clone_checkout(upstream_dir, work_dir)

        # Replace the downstream recipe with a mangled copy
        package.setMangleData(mangleData)
        recipe = mangle(package, upstream_recipe)
        open('%s.recipe' % packageName, 'w').write(recipe)

        # Commit changes back to the internal repos if changes were made
        new_digest = digest_checkout(work_dir)
        if old_digest != new_digest:
            log.debug('Committing mangled %s', sourceName)
            conary_log.resetErrorOccurred()
            use.setBuildFlagsFromFlavor(packageName, cfg.buildFlavor,
                error=False)
            checkin.commit(helper.getRepos(), cfg,
                helper.plan.commitMessage, force=True)
            if conary_log.errorOccurred():
                raise RuntimeError()
        else:
            log.debug('Downstream checkout is up-to-date.')

        # Return the newly-created version (or the old version if nothing has
        # changed).
        wd_state = state.ConaryStateFromFile('CONARY',
            helper.getRepos()).getSourceState()
        newTrove = wd_state.getNameVersionFlavor()
    finally:
        cfg.signatureKey = oldKey
        cfg.signatureKeyMap = oldMap
        cfg.interactive = oldInteractive
        os.chdir(oldWd)
        shutil.rmtree(work_dir)
        shutil.rmtree(upstream_dir)

    _finish_time = time.time()
    log.debug('Committed %s=%s', newTrove[0], newTrove[1])
    log.debug('Mangling took %.03f seconds', _finish_time - _start_time)

    # Set new downstream version on package object
    assert newTrove[0] == package.getName()
    package.setDownstreamVersion(newTrove[1])


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


def digest_checkout(checkout):
    '''
    Compute the SHA-1 digest of everything in a checkout.
    '''

    digest = sha.new()
    source_state = state.ConaryStateFromFile(os.path.join(checkout,
        'CONARY'))
    source_trove_state = source_state.getSourceState()

    for path_id, path, _, _ in source_trove_state.iterFileList():
        file_info = source_trove_state.fileInfo[path_id]
        if file_info.isAutoSource:
            continue

        fobj = open(os.path.join(checkout, path))
        buf = fobj.read(16384)
        while buf:
            digest.update(buf)
            buf = fobj.read(16384)
        fobj.close()

        digest.update(str(file_info.isConfig))

    return digest.digest()
