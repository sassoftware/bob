#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import copy
import md5
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
from rmake.build import buildtrove
from rmake.cmdline import buildcmd
from rmake.cmdline import helper
from rmake.cmdline import monitor
from rmake.lib import recipeutil
from rmake.server import client

from bob import commit
from bob import config
from bob import mangle
from bob import flavors

class DummyMain:
    def _registerCommand(*P, **K):
        pass

class StatusOnlyDisplay(monitor.JobLogDisplay):
    '''Display only job and trove status. No log output.'''
    def _troveLogUpdated(self, (jobId, troveTuple), state, status):
        pass
    def _trovePreparingChroot(self, (jobId, troveTuple), host, path):
        pass

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
        if not hasattr(self.buildcfg, 'reposName'):
            self.buildcfg.reposName = 'localhost'

        # conary client
        self.cc = conaryclient.ConaryClient(bcfg)
        self.nc = self.cc.getRepos()

        # rmake client
        self.pluginmgr = pluginmgr
        self.rc = client.rMakeClient(bcfg.getServerUri())
        self.helper = helper.rMakeHelper(buildConfig=self.buildcfg)

        # temporary stuff
        self.flavorContexts = {}

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

    def makeContext(self, build_flavor, search_flavors):
        '''
        Create a context in the local buildcfg out of the specified build
        and search flavors.
        '''

        # Calculate a unique context name based on the specified flavors.
        ctx = md5.new()
        ctx.update(build_flavor.freeze())
        for search_flavor in search_flavors:
            ctx.update(search_flavor.freeze())
        name = ctx.hexdigest()[:12]

        # Add a context if necessary and return the context name.
        if name not in self.flavorContexts:
            context = self.buildcfg.setSection(name)
            context['buildFlavor'] = build_flavor
            context['flavor'] = search_flavors

        return name

    def getJob(self):
        '''
        Create a rMake build job given the configured target parameters.
        '''

        # Pre-build configuration
        self.buildcfg.buildLabel = self.cfg.sourceLabel # XXX should this be the target?
        self.buildcfg.installLabelPath = [self.cfg.sourceLabel] + \
            self.cfg.installLabelPath
        self.buildcfg.resolveTroves = self.cfg.resolveTroves
        self.buildcfg.resolveTroveTups = buildcmd._getResolveTroveTups(
            self.buildcfg, self.nc)
        self.buildcfg.initializeFlavors()

        # Determine the top-level trove specs to build
        troveSpecs = []
        for targetName in self.cfg.target:
            targetCfg = self.targets[targetName]
            for flavor in flavors.expand_targets(targetCfg):
                troveSpecs.append((targetName, self.cfg.sourceLabel, flavor))

        # Determine which troves to build
        troveList = self.getMangledTroves(troveSpecs)

        # Create contexts for all required build configurations
        troves_with_contexts = []
        for name, version, flavor in troveList:
            search_flavors = flavors.guess_search_flavors(flavor)
            context = self.makeContext(flavor, search_flavors)
            troves_with_contexts.append((name, version, flavor, context))

        # Create rMake job
        import epdb;epdb.st()
        job = self.helper.createBuildJob(troves_with_contexts,
            buildConfig=self.buildcfg)

        return job

    def groupTroveSpecs(self, troveList):
        troveSpecs = {}
        for n, v, f in troveList:
            troveSpecs.setdefault((n, v), []).append(f)
        return [(name, version, flavor_list)
            for (name, version), flavor_list in troveSpecs.iteritems()]

    def getMangledTroves(self, troveSpecs):
        print 'Initializing build'

        # Key is name. value is (version, set(flavors)).
        toBuild = {}
        def markForBuilding(name, version, flavor_list):
            toBuild.setdefault(name, (version, set()))[1].update(flavor_list)

        self.buildcfg.buildTroveSpecs = []
        localRepos = recipeutil.RemoveHostRepos(self.nc,
            self.buildcfg.reposName)
        use.setBuildFlagsFromFlavor(None, self.buildcfg.buildFlavor,
            error=False)

        print 'Iterating sources:'
        for name, version, flavor_list in self.groupTroveSpecs(troveSpecs):
            name = name.split(':')[0] + ':source'
            print '  %s' % name

            # Resolve an exact version to build
            matches = self.nc.findTrove(None, (name, str(version), None))
            version = max(x[1] for x in matches)

            # Mangle the trove before doing group lookup
            if name in toBuild:
                version = toBuild[name][0]
            else:
                newTrove = self.mangleTrove(name, version)
                version = newTrove[1]
            markForBuilding(name, version, flavor_list)

            # Find all troves included if this is a group.
            if name.startswith('group-'):
                for flavor in flavor_list:
                    loader, recipeObj, relevantFlavor = \
                        recipeutil.loadRecipe(self.nc, name, version,
                            flavor, defaultFlavor=self.buildcfg.buildFlavor,
                            installLabelPath=self.buildcfg.installLabelPath,
                            buildLabel=self.cfg.sourceLabel,
                            cfg=self.buildcfg)
                    for n, v, f in grouprecipe.findSourcesForGroup(
                      localRepos, recipeObj):
                        if f is None:
                            f = deps.parseFlavor('')
                        merged_flavor = deps.overrideFlavor(flavor, f)
                        if n not in toBuild and \
                          v.trailingLabel() == self.cfg.sourceLabel:
                            print '    %s' % n
                            # This source has not been mangled but it is on
                            # our configured source label, so it should be
                            # mangled.
                            newTrove = self.mangleTrove(n, v)
                            markForBuilding(n, newTrove[1], [merged_flavor])
                        elif n in toBuild and \
                          merged_flavor not in toBuild[n][1]:
                            print '    %s' % n
                            # This source has been mangled but the
                            # particular flavor requested is not in the build
                            # list yet.
                            markForBuilding(n, None, [merged_flavor])

        buildTups = []
        for name, (version, flavor_list) in toBuild.iteritems():
            for flavor in flavor_list:
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
                    self.cfg.commitMessage, force=True)
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

        self.rc.addRepositoryInfo(self.buildcfg)

        job = self.getJob()
        jobId = self.rc.buildJob(job)
        print 'Job %d started' % jobId

        # Watch build (to stdout)
        self.pluginmgr.callClientHook('client_preCommand', DummyMain(),
            None, (self.buildcfg, self.buildcfg), None, None)
        self.pluginmgr.callClientHook('client_preCommand2', DummyMain(),
            self.helper, None)
        monitor.monitorJob(self.helper.client, jobId, exitOnFinish=True,
            displayClass=StatusOnlyDisplay)

        # Check for error condition
        job = self.rc.getJob(jobId, withConfigs=True)
        if job.isFailed():
            print 'Job %d failed' % jobId
            return 2
        elif job.isCommitting():
            print 'Job %d is already committing ' \
                '(probably to the wrong place)' % jobId
            return 3
        elif not job.isFinished():
            print 'Job %d is not done, yet watch returned early!' % jobId
            return 3
        elif not list(job.iterBuiltTroves()):
            print 'Job %d has no built troves' % jobId
            return 3

        # Commit to target repository
        self.rc.startCommit([jobId])
        try:
            mapping = commit.commit(self, job)
        except Exception, e_value:
            self.rc.commitFailed([jobId], str(e_value))
            raise
        else:
            self.rc.commitSucceeded(mapping)

        # Report committed troves
        package_map = {}
        for committed_list in mapping[jobId].itervalues():
            for name, version, flavor in committed_list:
                package = name.split(':')[0]
                package_map.setdefault(package, []).append((name,
                    version, flavor))

        for package in sorted(package_map):
            troves = package_map[package]

            packages = [x for x in troves if not ':' in x[0]]
            if not packages:
                print 'Trove %s has no packages. Tups: %s' % (package,
                    troves)
                continue

            print '%s=%s' % (packages[0][:2])
            flavor_list = set([x[2] for x in troves])
            for flavor in sorted(flavor_list):
                print '  %s' % flavor

def getPluginManager():
    cfg = buildcfg.BuildConfiguration(True, ignoreErrors=True)
    if not getattr(cfg, 'usePlugins', True):
        return plugins.PluginManager([])
    disabledPlugins = [ x[0] for x in cfg.usePlugin.items() if not x[1] ]
    disabledPlugins.append('monitor')
    manager = plugins.PluginManager(cfg.pluginDirs, disabledPlugins)
    manager.loadPlugins()
    return manager

def main(args):
    try:
        plan = args[0]
    except IndexError:
        print >>sys.stderr, 'Usage: %s <plan file or URI>' % sys.argv[0]
        return 1

    # = plugins.getPluginManager(sys.argv, buildcfg.BuildConfiguration)
    pluginmgr = getPluginManager()
    pluginmgr.callClientHook('client_preInit', DummyMain(), sys.argv)
    bcfg = buildcfg.BuildConfiguration(True)
    bcfg.readFiles()

    bob = CookBob(bcfg, pluginmgr)
    bob.readPlan(plan)
    return bob.run()

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
