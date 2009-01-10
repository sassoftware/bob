#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import os
import time
from conary.build.macros import Macros
from conary.conarycfg import CfgFlavor, CfgLabel
from conary.lib import cfg
from conary.lib.cfgtypes import CfgList, CfgString, CfgInt, CfgDict
from conary.lib.cfgtypes import CfgQuotedLineList, CfgBool, ParseError
from conary.versions import Label
from rmake.build.buildcfg import CfgTroveSpec

from bob.util import SCMRepository


DEFAULT_PATH = ['/etc/bobrc', '~/.bobrc']


class BobTargetSection(cfg.ConfigSection):
    '''
    Target trove configuration:
    [target:tmpwatch]
    flavor_set plain
    '''

    hg                      = CfgString
    flavor_mask             = CfgFlavor
    flavor_set              = CfgString
    flavor                  = CfgList(CfgString)
    macros                  = CfgDict(CfgString)
    siblingClone            = (CfgBool, False)
    version                 = CfgString             # macros supported
    sourceLabel             = CfgLabel
    serializeFlavors        = CfgBool
    noCommit                = CfgBool


class BobConfig(cfg.SectionedConfigFile):
    targetLabel             = (CfgLabel, Label('bob3.rb.rpath.com@rpl:1'))

    # source
    sourceLabel             = CfgLabel
    macros                  = CfgDict(CfgString)
    resolveTroves           = CfgList(CfgQuotedLineList(
                                        CfgString)) # macros supported
    resolveTrovesOnly       = (CfgBool, False)
    autoLoadRecipes         = (CfgList(CfgString), [])
    hg                      = CfgDict(CfgString)    # macros supported

    # build
    installLabelPath        = CfgQuotedLineList(
                                CfgString)          # macros supported
    matchTroveRule          = CfgList(CfgString)
    recurseTroveRule        = CfgList(CfgString)
    rebuild                 = (CfgBool, False, "Use rMake's rebuild mode")
    shortenGroupFlavors     = (CfgBool, True)
    target                  = CfgList(CfgString)
    showBuildLogs           = (CfgBool, False)

    # environment
    scmMap                  = CfgList(CfgString)

    # misc
    commitMessage           = (CfgString, 'Automated clone by bob3')
    skipMacros              = (CfgList(CfgString), ['version'])

    # custom handling of sections
    _sectionMap = {'target': BobTargetSection}

    def __init__(self):
        cfg.SectionedConfigFile.__init__(self)
        self.scmPins = {}

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
        macros = Macros(self.macros)
        macros['start_time'] = time.strftime('%Y%m%d_%H%M%S')
        macros['target_label'] = self.targetLabel.asString()
        return macros

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
        for name, uri in self.hg.iteritems():
            name %= macros
            if ' ' in uri:
                uri, revision = uri.split(' ', 1)
                uri %= macros
                revision %= macros
            else:
                uri %= macros
                revision = None

            repos = None
            for scmMap in self.scmMap:
                # A right proper repository handle
                base, target = scmMap.split(' ', 1)
                if uri.startswith(target):
                    scmPath = base + uri[len(target):]
                    repos = SCMRepository.fromString(scmPath)
                    break
            else:
                # Dummy handle that will at least let us go back
                # to the URI later
                repos = SCMRepository(uri=uri)
            repos.revision = revision
            out[name] = repos

        return out

    def getUriForScm(self, repos):
        if isinstance(repos, SCMRepository) and repos.uri:
            return repos.uri
        if not isinstance(repos, basestring):
            repos = repos.asString()
        for scmMap in self.scmMap:
            base, target = scmMap.split(' ', 1)
            if repos.startswith(base):
                return target + repos[len(base):]
        raise RuntimeError("Can't map SCM repository %r to URI "
                "-- please add a scmMap" % repos)


def openPlan(path, preload=DEFAULT_PATH):
    plan = BobConfig()
    for item in preload:
        if item.startswith('~/') and 'HOME' in os.environ:
            item = os.path.join(os.environ['HOME'], item[2:])
        if os.path.isfile(item):
            plan.read(item)
    plan.read(path)
    return plan
