#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Mechanism for expanding macros from a trove context.
'''

import logging
import time

from conary.versions import Label
from conary.build.macros import Macros


def expand(raw, package):
    '''Transform a raw string with available configuration data.'''
    data = package.getMangleData()
    macros = {}

    # Basic info
    macros.update(data['macros'])

    # Additional info available in trove contexts
    config = package.getTargetConfig()
    if config:
        if config.hg:
            name = config.hg
            if data['scm'].has_key(name):
                repos = data['scm'][name][0]
                if repos.kind != 'hg':
                    raise RuntimeError("SCM type mismatch on trove %s: "
                            "referenced repository %r which is a %r "
                            "repository using 'hg' directive." % (
                                package.getPackageName(), name, repos.kind))
                macros['hg'] = data['scm'][name][0].revision
            else:
                logging.warning('Trove %s references undefined Hg '
                    'repository %s', package.getPackageName(), name)

    _macros = Macros(macros)
    return raw % _macros


def substILP(ilp, macros):
    """
    Substitute C{macros} into the install label path C{ilp}.
    """
    return [Label(x % macros) for x in ilp]


def substResolveTroves(resolveTroves, macros):
    '''
    Substitute C{macros} into the config item C{resolveTroves}.

    @type  resolveTroves: C{[[(name, version, flavor)]]}
    @type  macros: dict or Macros
    '''

    ret = []
    for bucket in resolveTroves:
        newBucket = []
        for name, version, flavor in bucket:
            name = name % macros
            if version:
                version = version % macros
            newBucket.append((name, version, flavor))
        ret.append(newBucket)

    return ret


def substStringList(lst, macros):
    """
    Substitute C{macros} into a list of strings.
    """

    return [ x % macros for x in lst ]
