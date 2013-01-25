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
Mechanism for expanding macros from a trove context.
'''

import logging

from conary.build.macros import Macros
from conary.conaryclient.cmdline import parseTroveSpec
from conary.versions import Label


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
                if repos.kind not in (None, 'hg'):
                    raise RuntimeError("SCM type mismatch on trove %s: "
                            "referenced repository %r which is a %r "
                            "repository using 'hg' directive." % (
                                package.getPackageName(), name, repos.kind))
                macros['hg'] = repos.revision
            else:
                logging.warning('Trove %s references undefined Hg '
                    'repository %s', package.getPackageName(), name)

    _macros = Macros(macros)
    return raw % _macros


def substILP(ilp, macros):
    """
    Substitute C{macros} into the install label path C{ilp}.
    """
    return [Label(x % macros) for x in ilp if x % macros]


def substResolveTroves(resolveTroves, macros):
    '''
    Substitute C{macros} into the config item C{resolveTroves}.

    @type  resolveTroves: C{[[(name, version, flavor)]]}
    @type  macros: dict or Macros
    '''

    ret = []
    for bucket in resolveTroves:
        newBucket = []
        for spec in bucket:
            spec %= macros
            newBucket.append(parseTroveSpec(spec))
        ret.append(newBucket)

    return ret


def substStringList(lst, macros):
    """
    Substitute C{macros} into a list of strings.
    """

    return [ x % macros for x in lst ]
