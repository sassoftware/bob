#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import copy
import logging
import md5
import os
import shutil
import sys
import tempfile

from conary import conaryclient
from conary import versions
from conary.build import grouprecipe
from conary.build import use
from conary.deps import deps
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
from bob import coverage
from bob import flavors
from bob import hg
from bob import mangle
from bob import test

log = logging.getLogger('bob.cook')


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
        log.debug('Fetching plan %s', plan)
        if plan.startswith('http://') or plan.startswith('https://'):
            self.cfg.readUrl(plan)
        else:
            self.cfg.read(plan)

        for name, section in self.cfg._sections.iteritems():
            if not ':' in name:
                continue
            sectype, name = name.split(':', 1)
            if sectype == 'target':
                self.targets[name] = section
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
        log.info('Creating build job')

        # Pre-build configuration
        self.buildcfg.buildLabel = self.cfg.targetLabel
        for x in ('resolveTroves', 'shortenGroupFlavors'):
            self.buildcfg[x] = self.cfg[x]
        self.buildcfg.resolveTroves = self.cfg.resolveTroves
        self.buildcfg.installLabelPath = [self.cfg.sourceLabel] + \
            self.cfg.installLabelPath
        self.buildcfg.resolveTroveTups = buildcmd._getResolveTroveTups(
            self.buildcfg, self.nc)
        self.buildcfg.macros.update(self.cfg.macros)
        self.buildcfg.initializeFlavors()

        # Determine the top-level trove specs to build
        troveSpecs = []
        for targetName in self.cfg.target:
            if targetName not in self.targets:
                raise RuntimeError('No target config section '
                    'for trove "%s"' % targetName)
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
        '''
        Determine the final list of troves to build by following groups.
        Mangle sources before adding to the build list.
        '''
        log.info('Determining which troves to build')

        # Key is name. value is (version, set(flavors)).
        toBuild = {}
        def markForBuilding(name, version, flavor_list):
            toBuild.setdefault(name, (version, set()))[1].update(flavor_list)

        self.buildcfg.buildTroveSpecs = []
        localRepos = recipeutil.RemoveHostRepos(self.nc,
            self.buildcfg.reposName)
        use.setBuildFlagsFromFlavor(None, self.buildcfg.buildFlavor,
            error=False)

        for name, version, flavor_list in self.groupTroveSpecs(troveSpecs):
            package = name.split(':')[0]
            name = package + ':source'
            log.debug('Inspecting %s', name)

            # Resolve an exact version to build
            matches = self.nc.findTrove(None, (name, str(version), None))
            version = max(x[1] for x in matches)

            # Mangle the trove before doing group lookup
            if name in toBuild:
                version = toBuild[name][0]
            else:
                siblingClone = package in self.targets and \
                    self.targets[package].siblingClone
                newTrove = mangle.mangleTrove(self, name, version,
                    siblingClone=siblingClone)
                version = newTrove[1]
            markForBuilding(name, version, flavor_list)

            # Find all troves included if this is a group.
            if name.startswith('group-'):
                log.debug('Following %s', name)
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
                            # This source has not been mangled but it is on
                            # our configured source label, so it should be
                            # mangled.
                            p = n.split(':')[0]
                            siblingClone = p in self.targets and \
                                self.targets[p].siblingClone
                            newTrove = mangle.mangleTrove(self, n, v,
                                siblingClone=siblingClone)
                            markForBuilding(n, newTrove[1], [merged_flavor])
                            log.debug('Adding %s=%s to build list', n, v)
                        elif n in toBuild and \
                          merged_flavor not in toBuild[n][1]:
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

    def run(self):
        # Get versions of all hg repositories
        for name, uri in self.cfg.hg.iteritems():
            self.hg[name] = (uri, hg.get_tip(uri))

        self.rc.addRepositoryInfo(self.buildcfg)

        job = self.getJob()
        jobId = self.rc.buildJob(job)
        log.info('Job %d started', jobId)

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
            log.error('Job %d failed', jobId)
            return 2
        elif not job.isFinished():
            log.error('Job %d is not done, yet watch returned early!', jobId)
            return 3
        elif not list(job.iterBuiltTroves()):
            log.error('Job %d has no built troves', jobId)
            return 3

        # Fetch test/coverage output
        test_suite, cover_data = test.processTests(self, job)
        if os.path.isdir('output'):
            shutil.rmtree('output')

        # Write test output
        os.makedirs('output/tests')
        if test_suite.tests:
            test_suite.write_junit(open('output/tests/junit.xml', 'w'))

        # Write coverage data and print report
        os.makedirs('output/coverage')
        if cover_data:
            report = coverage.process(cover_data)
            coverage.dump(cover_data, open('output/coverage/pickle', 'w'))
            coverage.simple_report(report, sys.stdout)
            if self.cfg.hasSection('wiki'):
                wiki = self.cfg.getSection('wiki')
                coverage.wiki_summary(report, wiki)

        # Bail out without committing if tests failed
        if not test_suite.isSuccessful():
            log.error('Some tests failed, aborting')
            return 4

        # Commit to target repository
        if job.isCommitting():
            log.error('Job %d is already committing ' \
                '(probably to the wrong place)', jobId)
            return 3
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

        log.info('Built:')
        for package in sorted(package_map):
            troves = package_map[package]

            packages = [x for x in troves if not ':' in x[0]]
            if not packages:
                log.warning('Trove %s has no packages. Tups: %s', package,
                    troves)
                continue

            log.info('%s=%s', packages[0][0], packages[0][1])
            flavor_list = set([x[2] for x in troves])
            for flavor in sorted(flavor_list):
                log.info('  %s', str(flavor))

        return 0


def getPluginManager():
    cfg = buildcfg.BuildConfiguration(True, ignoreErrors=True)
    if not getattr(cfg, 'usePlugins', True):
        return plugins.PluginManager([])
    disabledPlugins = [ x[0] for x in cfg.usePlugin.items() if not x[1] ]
    disabledPlugins.append('monitor')
    manager = plugins.PluginManager(cfg.pluginDirs, disabledPlugins)
    manager.loadPlugins()
    return manager

def addRootLogger():
    root_log = logging.getLogger('')
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s')
    handler.setFormatter(formatter)
    root_log.addHandler(handler)
    root_log.setLevel(logging.DEBUG)

    # Delete conary's log handler since it puts things on stderr and without
    # any timestamps.
    conary_log = logging.getLogger('conary')
    for handler in conary_log.handlers:
        conary_log.removeHandler(handler)

def main(args):
    try:
        plan = args[0]
    except IndexError:
        print >>sys.stderr, 'Usage: %s <plan file or URI>' % sys.argv[0]
        return 1

    addRootLogger()

    # = plugins.getPluginManager(sys.argv, buildcfg.BuildConfiguration)
    pluginmgr = getPluginManager()
    pluginmgr.callClientHook('client_preInit', DummyMain(), sys.argv)
    bcfg = buildcfg.BuildConfiguration(True)
    bcfg.readFiles()

    bob = CookBob(bcfg, pluginmgr)
    for cfg_file in ('/etc/bobrc', os.getenv('HOME', '/') + '/.bobrc'):
        if os.path.exists(cfg_file):
            bob.cfg.read(cfg_file)
    bob.readPlan(plan)
    return bob.run()

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
