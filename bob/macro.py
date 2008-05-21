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

from conary.build.macros import Macros


def expand(raw, package):
    '''Transform a raw string with available configuration data.'''
    data = package.getMangleData()
    macros = {}

    # Basic info
    macros.update(data['plan'].macros)
    macros['start_time'] = time.strftime('%Y%m%d_%H%M%S', time.localtime(
        data['startTime']))

    # Additional info available in trove contexts
    config = package.getTargetConfig()
    if config:
        if config.hg:
            if data['hg'].has_key(config.hg):
                macros['hg'] = data['hg'][config.hg][1]
            else:
                logging.warning('Trove %s references undefined Hg '
                    'repository %s', package.getPackageName(), config.hg)

    _macros = Macros(macros)
    return raw % _macros


def substResolveTroves(resolveTroves, macros):
    '''
    Substitute C{macros} into the config item C{resolveTroves}.

    @type  resolveTroves: CfgList(CfgQuotedLineList(CfgTroveSpec))
    @type  macros: dict or Macros
    '''

    ret = []
    for bucket in resolveTroves:
        newBucket = []
        for troveSpec in bucket:
            substSpec = [x and (x % macros) or x for x in troveSpec]
            newBucket.append(tuple(substSpec))
        ret.append(newBucket)

    return ret
