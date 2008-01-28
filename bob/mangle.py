#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import logging
import re

from bob import macro

filters = []

def mangle(parent, trove, recipe):
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
