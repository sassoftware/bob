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


import os
import time
from conary.build.macros import Macros
from conary.conarycfg import CfgFlavor
from conary.lib import cfg
from conary.lib.cfgtypes import CfgList, CfgString, CfgDict
from conary.lib.cfgtypes import CfgQuotedLineList, CfgBool, ParseError
from conary.versions import Label
from rmake.build.buildcfg import CfgDependency


DEFAULT_PATH = ['/etc/bobrc', '~/.bobrc']


class BobTargetSection(cfg.ConfigSection):
    '''
    Target trove configuration:
    [target:tmpwatch]
    flavor_set plain
    '''

    scm                     = CfgString
    after                   = CfgList(CfgString)
    classVar                = CfgDict(CfgString)
    flavor_mask             = CfgFlavor
    flavor_set              = CfgString
    flavor                  = CfgList(CfgString)
    macros                  = CfgDict(CfgString)
    version                 = CfgString             # macros supported
    sourceTree              = CfgString
    serializeFlavors        = CfgBool
    noCommit                = CfgBool

    def __init__(self, *args, **kwargs):
        cfg.ConfigSection.__init__(self, *args, **kwargs)
        self.addAlias('hg', 'scm')
        self.addAlias('git', 'scm')


class BobConfig(cfg.SectionedConfigFile):
    targetLabel             = CfgString             # macros supported

    # source
    sourceLabel             = CfgString             # DEPRECATED (ignored)
    macros                  = CfgDict(CfgString)
    override                = CfgDict(CfgString)
    resolveTroves           = CfgList(CfgQuotedLineList(
                                        CfgString)) # macros supported
    resolveTrovesOnly       = (CfgBool, False)
    autoLoadRecipes         = (CfgList(CfgString), [])
    scm                     = CfgDict(CfgString)    # macros supported

    # build
    installLabelPath        = CfgQuotedLineList(
                                CfgString)          # macros supported
    noClean                 = (CfgBool, False,
            "Don't clean the rMake chroot immediately "
            "after a successful build.")
    shortenGroupFlavors     = (CfgBool, True)
    target                  = CfgList(CfgString)
    showBuildLogs           = (CfgBool, False)
    defaultBuildReqs        = CfgList(CfgString)
    rpmRequirements         = CfgList(CfgDependency)

    # misc
    commitMessage           = (CfgString, 'Automated clone by bob')
    skipMacros              = (CfgList(CfgString), ['version'])

    # custom handling of sections
    _sectionMap = {'target': BobTargetSection}

    def __init__(self):
        cfg.SectionedConfigFile.__init__(self)
        self.scmPins = {}
        self._macros = None
        self.addDirective('hg', '_hg')

    def read(self, path, **kwargs):
        if path.startswith('http://') or path.startswith('https://'):
            return cfg.SectionedConfigFile.readUrl(self, path, **kwargs)
        else:
            return cfg.SectionedConfigFile.read(self, path, **kwargs)

    def setSection(self, sectionName):
        if not self.hasSection(sectionName):
            found = False
            for name, cls in self._sectionMap.iteritems():
                if sectionName == name or sectionName.startswith(name + ':'):
                    found = True
                    self._addSection(sectionName, cls(self))
            if not found:
                raise ParseError('Unknown section "%s"' % sectionName)
        self._sectionName = sectionName
        return self._sections[sectionName]

    def setPins(self, scmPins):
        self.scmPins = scmPins

    def getMacros(self):
        if self._macros is None:
            macros = Macros(self.macros)
            macros.update(self.override)
            macros['start_time'] = time.strftime('%Y%m%d_%H%M%S')
            macros['target_label'] = self.targetLabel % macros
            self._macros = macros
        return self._macros

    def getRepositories(self, macros=None):
        """
        Get a mapping of SCM repository aliases to the repository
        objects (which specify kind, hostname, path, and pinned
        revision).
        """

        if self.scmPins:
            # Someone already went to the trouble of determining what
            # we have (e.g. they "pinned" the repositories)
            return self.scmPins

        if not macros:
            macros = self.getMacros()

        out = {}
        for name, value in self.scm.iteritems():
            if ' ' not in value:
                raise ValueError("Invalid scm directive %r -- must take the "
                        "form 'scm <hg|git> <uri> [rev]" % (value,))
            kind, uri = value.split(' ', 1)
            name %= macros
            if ' ' in uri:
                uri, revision = uri.split(' ', 1)
                uri %= macros
                revision %= macros
            else:
                uri %= macros
                revision = None
            out[name] = (kind, uri, revision)

        return out

    def getTargetLabel(self):
        return Label(self.targetLabel % self.getMacros())

    def _hg(self, value):
        key, value = value.split(' ', 1)
        self.configLine('scm %s hg %s' % (key, value))


def openPlan(path, preload=DEFAULT_PATH):
    plan = BobConfig()
    for item in preload:
        if item.startswith('~/') and 'HOME' in os.environ:
            item = os.path.join(os.environ['HOME'], item[2:])
        if os.path.isfile(item):
            plan.read(item)
    plan.read(path)
    return plan
