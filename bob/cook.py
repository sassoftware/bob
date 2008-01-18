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
        '''
        Create a rMake build job given the configured target parameters.
        '''
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
        troveList = getMangledTroves(cfg, troveSpecs)

        # Add troves to job
        for name, version, flavor in troveList:
            if flavor is None:
                flavor = deps.parseFlavor('')
            job.addTrove(name, version, flavor, '', bt)

        return job

    def getMangledTroves(self, cfg, troveSpecs):
        toBuild = []
        toFind = {}
        groupsToFind = []
        if not matchSpecs:
            matchSpecs = []

        cfg.resolveTroveTups = buildcmd._getResolveTroveTups(cfg, self.nc)
        cfg.recurseGroups = buildcmd.BUILD_RECURSE_GROUPS_SOURCE

        cfg.buildTroveSpecs = []
        newTroveSpecs = []
        recipesToCook = []
        for troveSpec in list(troveSpecList):
            if troveSpec[2] is None:
                troveSpec = (troveSpec[0], troveSpec[1], deps.parseFlavor(''))

            # Don't even bother following recipes that are off-label. We
            # don't need to mangle them, and we don't need to build them.
            if not buildcmd._filterListByMatchSpecs(cfg.reposName,
              cfg.matchTroveRule, [troveSpec]):
                continue

            newTrove = self.mangleTrove(cfg, troveSpec)
            cfg.buildTroveSpecs.append(newTrove)
            newTroveSpecs.append(newTrove)

            recipesToCook.append((os.path.realpath(troveSpec[0]), troveSpec[2]))
                continue
            cfg.buildTroveSpecs.append(troveSpec)

            if troveSpec[0].startswith('group-'):
                groupsToFind.append(troveSpec)
            newTroveSpecs.append(troveSpec)

        # TODO: make the magic happen
        localTroves = [(_getLocalCook(conaryclient, cfg, x[0], message), x[1])
                         for x in recipesToCook ]
        localTroves = [(x[0][0], x[0][1], x[1]) for x in localTroves]
        compat.ConaryVersion().requireFindGroupSources()
        localGroupTroves = [ x for x in localTroves 
                             if x[0].startswith('group-') ]
        toBuild.extend(_findSourcesForSourceGroup(self.nc, cfg.reposName, cfg,
                                                      groupsToFind,
                                                      localGroupTroves,
                                                      updateSpecs))

        for troveSpec in newTroveSpecs:
            sourceName = troveSpec[0].split(':')[0] + ':source'

            s = toFind.setdefault((sourceName, troveSpec[1], None), [])
            if troveSpec[2] not in s:
                s.append(troveSpec[2])


        results = self.nc.findTroves(cfg.buildLabel, toFind, None)

        for troveSpec, troveTups in results.iteritems():
            flavorList = toFind[troveSpec]
            for troveTup in troveTups:
                for flavor in flavorList:
                    toBuild.append((troveTup[0], troveTup[1], flavor))

        toBuild.extend(localTroves)

        toBuild = _filterListByMatchSpecs(cfg.reposName, cfg.matchTroveRule,
            toBuild)
        return toBuild
   

    def getLabelFromTag(self, stage='test'):
        return self.cfg.labelPrefix + self.cfg.tag + '-' + stage

    def run(self):
        # Get versions of all hg repositories
        for name, repos in self.cfg.hg.iteritems():
            node = hg_loader.getNode(repos)
            self.hg[name] = node

        job = self.getJob()
