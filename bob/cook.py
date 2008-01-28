#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import os
import shutil
import sys
import tempfile

from conary import checkin
from conary import conaryclient
from conary import state
from conary import versions
from conary.build import cook
from conary.build import grouprecipe
from conary.build import use
from conary.deps import deps
from conary.lib import log
from rmake import plugins
from rmake.build import buildcfg
from rmake.build import buildjob
from rmake.cmdline import buildcmd
from rmake.cmdline import helper
from rmake.lib import recipeutil
from rmake.server import client

from bob import config
from bob import mangle
from bob import flavors

class CookBob(object):
    def __init__(self, bcfg, pluginmgr):
        self.cfg = config.BobConfig()
        self.macros = {}
        self.targets = {}
        self.tests = {}
        
        # repo / build info
        self.job = None
        self.hg = {}

        # conary/rmake config
        self.buildcfg = bcfg

        # conary client
        self.cc = conaryclient.ConaryClient(bcfg)
        self.nc = self.cc.getRepos()

        # rmake client
        self.pluginmgr = pluginmgr
        self.rc = client.rMakeClient(bcfg.getServerUri())

    def readPlan(self, plan):
        if plan.startswith('http://') or plan.startswith('https://'):
            self.cfg.readUrl(plan)
        else:
            self.cfg.read(plan)

        for name, section in self.cfg._sections.iteritems():
            sectype, name = name.split(':', 1)
            if sectype == 'target':
                self.targets[name] = section
            elif sectype == 'test':
                self.tests[name] = section
            else:
                assert False

    def getJob(self):
        '''
        Create a rMake build job given the configured target parameters.
        '''
        # Determine the top-level trove specs to build
        troveSpecs = []
        for targetName in self.cfg.target:
            targetCfg = self.targets[targetName]
            for flavor in flavors.expandByTarget(targetCfg):
                flavor = deps.parseFlavor(flavor)
                troveSpecs.append((targetName, self.cfg.sourceLabel, flavor))

        # Pre-build configuration
        self.rc.addRepositoryInfo(self.buildcfg)
        self.buildcfg.limitToLabels([self.cfg.sourceLabel.asString()])

        self.buildcfg.dropContexts()
        self.buildcfg.initializeFlavors()
        self.buildcfg.buildLabel = self.cfg.sourceLabel
        self.buildcfg.installLabelPath = [self.cfg.sourceLabel] + \
            self.cfg.installLabelPath

        # Create rMake job
        job = buildjob.BuildJob()
        job.setMainConfig(self.buildcfg)

        # Determine which troves to build
        troveList = self.getMangledTroves(troveSpecs)

        # Add troves to job
        for name, version, flavor in troveList:
            if flavor is None:
                flavor = deps.parseFlavor('')
            job.addTrove(name, version, flavor, '')

        return job

    def groupTroveSpecs(self, troveList):
        troveSpecs = {}
        for n, v, f in troveList:
            if f is None:
                f = deps.parseFlavor('')
            troveSpecs.setdefault((n, v), []).append(f)
        return [(name, version, flavors)
            for (name, version), flavors in troveSpecs.iteritems()]

    def getMangledTroves(self, troveSpecs):
        # Key is name. value is (version, set(flavors)).
        toBuild = {}
        def markForBuilding(name, version, flavors):
            toBuild.setdefault(name, (version, set()))[1].update(flavors)

        self.buildcfg.buildTroveSpecs = []
        self.buildcfg.resolveTroveTups = buildcmd._getResolveTroveTups(
            self.buildcfg, self.nc)
        localRepos = recipeutil.RemoveHostRepos(self.nc,
            self.buildcfg.reposName)
        use.setBuildFlagsFromFlavor(None, self.buildcfg.buildFlavor,
            error=False)

        for name, version, flavors in self.groupTroveSpecs(troveSpecs):
            name = name.split(':')[0] + ':source'

            # Resolve an exact version to build
            matches = self.nc.findTrove(None, (name, str(version), None))
            version = max(x[1] for x in matches)

            # Mangle the trove before doing group lookup
            if name in toBuild:
                version = toBuild[name][0]
            else:
                newTrove = self.mangleTrove(name, version)
                version = newTrove[1]
            markForBuilding(name, version, flavors)

            # Find all troves included if this is a group.
            if name.startswith('group-'):
                for flavor in flavors:
                    loader, recipeObj, relevantFlavor = \
                        recipeutil.loadRecipe(self.nc, name, version,
                            flavor, defaultFlavor=self.buildcfg.buildFlavor,
                            installLabelPath=self.buildcfg.installLabelPath,
                            buildLabel=self.cfg.sourceLabel,
                            cfg=self.buildcfg)
                    for n, v, f in grouprecipe.findSourcesForGroup(
                      localRepos, recipeObj):
                        if n not in toBuild and \
                          v.trailingLabel() == self.cfg.sourceLabel:
                            # This source has not been mangled but it is on
                            # our configured source label, so it should be
                            # mangled.
                            newTrove = self.mangleTrove(n, v)
                            markForBuilding(n, newTrove[1], [f])
                        elif n in toBuild and f not in toBuild[n][1]:
                            # This source has been mangled but the
                            # particular flavor requested is not in the build
                            # list yet.
                            markForBuilding(n, None, f)

        buildTups = []
        for name, (version, flavors) in toBuild.iteritems():
            for flavor in flavors:
                tup = (name, version, flavor)
                buildTups.append(tup)
                self.buildcfg.buildTroveSpecs.append(tup)
        return buildTups

    def mangleTrove(self, name, version):
        oldKey = self.buildcfg.signatureKey
        oldMap = self.buildcfg.signatureKeyMap
        oldInteractive = self.buildcfg.interactive

        package = name.split(':')[0]
        sourceName = package + ':source'
        newTrove = None

        workDir = tempfile.mkdtemp(prefix='bob-mangle-%s' % package)
        oldWd = os.getcwd()

        try:
            self.buildcfg.signatureKey = None
            self.buildcfg.signatureKeyMap = {}
            self.buildcfg.interactive = False

            # Find source
            matches = self.nc.findTrove(None, (sourceName, str(version), None))
            sourceVersion = max(x[1] for x in matches)

            # Shadow to rMake's internal repos
            log.info('Shadowing %s to rMake repository', package)
            targetLabel = self.buildcfg.getTargetLabel(version)
            skipped, cs = self.cc.createShadowChangeSet(str(targetLabel),
                [(sourceName, sourceVersion, deps.parseFlavor(''))])
            if not skipped:
                cook.signAbsoluteChangeset(cs, None)
                self.nc.commitChangeSet(cs)

            # Check out the shadow
            shadowBranch = sourceVersion.createShadow(targetLabel).branch()
            checkin.checkout(self.nc, self.buildcfg, workDir,
                ['%s=%s' % (name, shadowBranch)])
            os.chdir(workDir)

            # Mangle 
            oldRecipe = open('%s.recipe' % package).read()
            recipe = mangle.mangle(self, package, oldRecipe)
            if recipe != oldRecipe:
                open('%s.recipe' % package, 'w').write(recipe)

                # Commit changes back to the internal repos
                log.resetErrorOccurred()
                checkin.commit(self.nc, self.buildcfg,
                    'Automated commit by Bob the Builder', force=True)
                if log.errorOccurred():
                    raise RuntimeError()

            # Figure out the new version and return
            wd_state = state.ConaryStateFromFile('CONARY',
                self.nc).getSourceState()
            newTrove = wd_state.getNameVersionFlavor()
        finally:
            self.buildcfg.signatureKey = oldKey
            self.buildcfg.signatureKeyMap = oldMap
            self.buildcfg.interactive = oldInteractive
            os.chdir(oldWd)
            shutil.rmtree(workDir)

        return newTrove

    def getLabelFromTag(self, stage='test'):
        return versions.Label(
            self.cfg.labelPrefix + self.cfg.tag + '-' + stage)

    def run(self):
        # Get versions of all hg repositories
        for name, repos in self.cfg.hg.iteritems():
            node = 'tip'
            self.hg[name] = (repos, node)

        job = self.getJob()
        jobId = self.rc.buildJob(job)
        print 'Job %d started' % jobId

        self.helper = helper.rMakeHelper(buildConfig=self.buildcfg)
        self.helper.watch(jobId, showTroveLogs=True, commit=False)

def getPluginManager():
    cfg = buildcfg.BuildConfiguration(True, ignoreErrors=True)
    if not getattr(cfg, 'usePlugins', True):
        return plugins.PluginManager([])
    disabledPlugins = [ x[0] for x in cfg.usePlugin.items() if not x[1] ]
    disabledPlugins.append('monitor')
    p = plugins.PluginManager(cfg.pluginDirs, disabledPlugins)
    p.loadPlugins()
    return p

if __name__ == '__main__':
    try:
        plan = sys.argv[1]
    except IndexError:
        print >>sys.stderr, 'Usage: %s <plan file or URI>' % sys.argv[0]
        sys.exit(1)

    class DummyMain:
        def _registerCommand(*P, **K):
            pass

    # = plugins.getPluginManager(sys.argv, buildcfg.BuildConfiguration)
    pluginmgr = getPluginManager()
    pluginmgr.callClientHook('client_preInit', DummyMain(), sys.argv)
    bcfg = buildcfg.BuildConfiguration(True)
    bcfg.readFiles()

    bob = CookBob(bcfg, pluginmgr)
    bob.readPlan(plan)
    bob.run()
