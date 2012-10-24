#
# Copyright (c) rPath, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
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
