#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import os.path

from conary import conarycfg
from conary import conaryclient
from conary.deps import deps
from rmake.build import buildcfg
from rmake.build import buildjob
from rmake.cmdline import buildcmd

from bob import config
from bob import flavors
from bob.loader import conary_loader
from bob.loader import hg_loader

class CookBob(object):
    def __init__(self):
        self.cfg = config.BobConfig()
        self.macros = {}
        self.targets = {}
        self.tests = {}
        
        # repo / build info
        self.job = None
        self.hg = {}

        # conary client
        self.conarycfg = conarycfg.ConaryConfiguration(True)
        self.cc = conaryclient.ConaryClient()
        self.nc = self.cc.getRepos()

        # rmake client
        self.buildcfg = buildcfg.BuildConfiguration(True)
        self.rc = client.rMakeClient(self.buildcfg.rmakeUrl)

    def readPlan(self, plan):
        if plan.startsWith('http://') or plan.startswith('https://'):
            self.cfg.readUrl(plan)
        else:
            self.cfg.read(plan)

        for name, section in self.cfg._sections:
            sectype, name = name.split(':', 1)
            if sectype == 'target':
                self.targets[name] = CookTarget(name, section)
            elif sectype == 'test':
                self.tests[name] = CookTest(name, section)
            else:
                assert False

    def getJob(self):
        # Determine the top-level trove specs to build
        troveSpecs = []
        for targetName, targetCfg in self.targets:
            for flavor in flavors.expandByTarget(targetCfg):
                troveSpecs += (targetName, self.cfg.sourceLabel, flavor, '')

        # Pre-build configuration
        self.rc.addRepository(buildConfig)
        self.buildcfg.limitToLabels(self.cfg.sourceLabel)

        cfg = copy.deepcopy(self.buildcfg) # XXX necessary?
        cfg.dropContexts()
        cfg.initializeFlavors()
        cfg.buildLabel = self.getLabelFromTag('test')
        use.setBuildFlagsFromFlavor(None, cfg.buildFlavor, error=False)

        # Create rMake job
        job = buildjob.BuildJob()
        job.setMainConfig(cfg)

        # Determine which troves to build
        troveList = buildcmd.getTrovesToBuild(cfg, self.cc, troveSpecs,
            recurseGroups=buildcmd.BUILD_RECURSE_GROUPS_SOURCE,
            matchSpecs=cfg.matchTroveRule)

        # Add troves to job
        for name, version, flavor in troveList:
            if flavor is None:
                flavor = deps.parseFlavor('')
            job.addTrove(name, version, flavor, '', bt)

        return job

    def getLabelFromTag(self, stage='test'):
        return self.cfg.labelPrefix + self.cfg.tag + '-' + stage

    def run(self):
        # Get versions of all hg repositories
        for name, repos in self.cfg.hg.iteritems():
            node = hg_loader.getNode(repos)
            self.hg[name] = node

        job = self.getJob()
