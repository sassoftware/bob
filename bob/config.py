#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

from conary.conarycfg import CfgFlavor, CfgLabel, CfgInstallLabelPath
from conary.lib import cfg
from conary.lib.cfgtypes import CfgList
from conary.lib.cfgtypes import CfgString
from conary.lib.cfgtypes import CfgInt
from conary.lib.cfgtypes import CfgDict
from conary.lib.cfgtypes import CfgQuotedLineList
from conary.lib.cfgtypes import CfgBool
from conary.lib.cfgtypes import ParseError
from rmake.build.buildcfg import CfgTroveSpec

class BobTargetSection(cfg.ConfigSection):
    hg                      = CfgString
    flavor_set              = CfgString
    flavor                  = CfgList(CfgFlavor)
    version                 = CfgString

class BobTestSection(cfg.ConfigSection):
    strip                   = CfgInt

class BobConfig(cfg.SectionedConfigFile):
    labelPrefix             = (CfgString, 'bob3.rb.rpath.com@rpath:')

    # source
    sourceLabel             = CfgLabel
    macro                   = CfgDict(CfgString)
    resolveTroves           = CfgList(CfgQuotedLineList(CfgTroveSpec))
    hg                      = CfgDict(CfgString)

    # build
    installLabelPath        = CfgInstallLabelPath
    shortenGroupFlavors     = (CfgBool, True)
    tag                     = CfgString
    target                  = CfgList(CfgString)

    # misc
    commitMessage           = (CfgString, 'Automated clone by bob3')

    # custom handling of sections
    _sectionMap = {'target': BobTargetSection, 'test': BobTestSection}

    def setSection(self, sectionName):
        for name, typeobj in self._sectionMap.iteritems():
            if sectionName.startswith(name + ':'):
                self._addSection(sectionName, typeobj(self))
                self._sectionName = sectionName
                return self._sections[sectionName]
        raise ParseError('Unknown section "%s"' % sectionName)
