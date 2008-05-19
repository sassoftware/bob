#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Tools for manipulating recipes and source troves.
'''

import logging
import re

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
