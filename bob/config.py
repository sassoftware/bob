#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

from conary.conarycfg import CfgFlavor, CfgLabel, CfgInstallLabelPath
from conary.lib import cfg
from conary.lib.cfgtypes import CfgList, CfgString, CfgInt, CfgDict
from conary.lib.cfgtypes import CfgQuotedLineList, CfgBool, ParseError
from conary.versions import Label
from rmake.build.buildcfg import CfgTroveSpec

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
    version                 = CfgString
    sourceLabel             = CfgLabel

class BobWikiSection(cfg.ConfigSection):
    '''
    Configuration for writing coverage reports to mediawiki
    (single section)
    '''
    root                    = CfgString
    subdir                  = CfgString
    page                    = CfgString
    product                 = CfgString

class BobConfig(cfg.SectionedConfigFile):
    targetLabel             = (CfgLabel, Label('bob3.rb.rpath.com@rpl:1'))

    # source
    sourceLabel             = CfgLabel
    macros                  = CfgDict(CfgString)
    resolveTroves           = CfgList(CfgQuotedLineList(CfgTroveSpec))
    hg                      = CfgDict(CfgString)

    # build
    installLabelPath        = CfgInstallLabelPath
    matchTroveRule          = CfgList(CfgString)
    recurseTroveRule        = CfgList(CfgString)
    shortenGroupFlavors     = (CfgBool, True)
    target                  = CfgList(CfgString)

    # misc
    commitMessage           = (CfgString, 'Automated clone by bob3')

    # custom handling of sections
    _sectionMap = {'target': BobTargetSection, 'wiki': BobWikiSection}

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
