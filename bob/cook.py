#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import os
import shutil

from conary import conarycfg
from conary import conaryclient
from conary.build import grouprecipe
from conary.deps import deps
from rmake.build import buildcfg
from rmake.build import buildjob
from rmake.cmdline import buildcmd
from rmake.lib import recipeutil

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
        cfg.buildLabel = self.cfg.sourceLabel

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

    def groupTroveSpecs(self, troveList):
        troveSpecs = {}
        for n, v, f in troveList:
            if f is None:
                f = deps.parseFlavor('')
            troveSpecs.setdefault((n, v), []).append(f)
        return [(name, version, flavors)
            for (name, version), flavors in troveSpecs.iteritems()]

    def getMangledTroves(self, cfg, troveSpecs):
        toBuild = []

        rewrittenTroves = {}
        resolvedGroups = {}

        cfg.buildTroveSpecs = []
        cfg.recurseGroups = buildcmd.BUILD_RECURSE_GROUPS_SOURCE
        cfg.resolveTroveTups = buildcmd._getResolveTroveTups(cfg, self.nc)
        localRepos = recipeutil.RemoveHostRepos(self.nc, cfg.reposName)
        use.setBuildFlagsFromFlavor(None, cfg.buildFlavor, error=False)

        for name, version, flavors in self.groupTroveSpecs(troveSpecs):
            # Don't even bother following recipes that are off-label. We
            # don't need to mangle them, and we don't need to build them.
            if not buildcmd._filterListByMatchSpecs(cfg.reposName,
              cfg.matchTroveRule, [(name, version, None]):
                continue

            # Mangle the trove before doing group lookup
            if rewrittenTroves.has_key((name, version)):
                newTrove = rewrittenTroves[(name, version)]
            else:
                newTrove = self.mangleTrove(cfg, name, version)
                rewrittenTroves[(name, version)] = newTrove
                toBuild.append(newTrove)
                cfg.buildTroveSpecs.append(newTrove)

            # Find all troves included if this is a group.
            if name.startswith('group-'):
                sourceName = name.split(':')[0] + ':source'

                for flavor in flavors:
                    # Don't bother resolving the same NVF twice
                    if resolvedGroups.has_key((sourceName, version, flavor)):
                        continue

                    loader, recipeObj, relevantFlavor = \
                        recipeutil.loadRecipe(self.nc, sourceName, version,
                            flavor, defaultFlavor=cfg.buildFlavor,
                            installLabelPath=cfg.installLabelPath,
                            buildLabel=self.getLabelFromTag('test'))
                    troveTups = grouprecipe.findSourcesForGroup(
                        localRepos, recipeObj)
                    toBuild.extend(troveTups)

        toBuild = _filterListByMatchSpecs(cfg.reposName, cfg.matchTroveRule,
            toBuild)
        return toBuild

    def mangleTrove(self, cfg, name, version):
        oldKey = cfg.signatureKey
        oldMap = cfg.signatureKeyMap
        oldInteractive = cfg.interactive

        package = name.split(':')[0]
        sourceName = package + ':source'
        workDir = tempfile.mkdtemp(prefix='bob-mangle-%s' % package)
        newTrove = None
        try:
            cfg.signatureKey = None
            cfg.signatureKeyMap = {}
            cfg.interactive = False

            # Shadow to rMake's internal repos
            logging.info('Shadowing %s to rMake repository', package)
            targetLabel = cfg.getTargetLabel(version)
            skipped, cs = conaryclient.createShadowChangeSet(
                str(targetLabel), [(sourceName, version, None)])
            if not skipped:
                signAbsoluteChangeset(cs, None)
                self.nc.commitChangeSet(cs)

            # Check out the shadow
            checkin.checkout(self.nc, self.conarycfg,
                ['%s=%s' % (name, version)])

            # Mangle 
            recipe = open('%s.recipe' % package).read()
            recipe = mangle.mangle(self, package, recipe)
            open('%s.recipe' % package).write(recipe)

            # Commit changes back to the internal repos
            logging.resetErrorOccurred()
            checkin.commit(self.nc, self.conarycfg,
                'Automated commit by Bob the Builder', force=True)
            if logging.errorOccurred():
                raise RuntimeError()

            # Figure out the new version and return
            state = ConaryStateFromFile('CONARY', self.nc).getSourceState()
            newTrove = state.getNameVersionFlavor()
        finally:
            cfg.signatureKey = oldKey
            cfg.signatureKeyMap = oldMap
            cfg.interactive = oldInteractive
            shutil.rmtree(workDir)

        return newTrove

    def getLabelFromTag(self, stage='test'):
        return self.cfg.labelPrefix + self.cfg.tag + '-' + stage

    def run(self):
        # Get versions of all hg repositories
        for name, repos in self.cfg.hg.iteritems():
            node = hg_loader.getNode(repos)
            self.hg[name] = (repos, node)

        job = self.getJob()
        jobId = client.buildJob(job)
        print 'Job %d started' % jobId

        self.helper = helper.rMakeHelper(buildConfig=self.buildcfg)
        helper.watch(jobId, showTroveLogs=True, commit=False)
