#
# Copyright (c) 2010 rPath, Inc.
#
# All rights reserved.
#

import copy
import logging
import os
import shutil
import sys

from conary.build.macros import MacroKeyError
from rmake import plugins
from rmake.cmdline import buildcmd
from rmake.build import buildcfg

from bob import config
from bob import coverage
from bob import hg
from bob import flavors
from bob import recurse
from bob import shadow
from bob import version
from bob.errors import JobFailedError, TestFailureError
from bob.macro import substILP, substResolveTroves, substStringList
from bob.test import TestSuite
from bob.trove import BobPackage
from bob.util import ClientHelper, pushStopHandler, reportCommitMap

log = logging.getLogger('bob.main')


class BobMain(object):
    def __init__(self, pluginmgr):
        pluginmgr.callClientHook('client_preInit', self, sys.argv)

        bcfg = buildcfg.BuildConfiguration(True)
        bcfg.readFiles()

        self._cfg = None
        self._helper = ClientHelper(bcfg, None, pluginmgr)
        self._targetConfigs = {}
        self._macros = {}
        
        # repo info
        self._scm = {}

        # conary/rmake config
        if not hasattr(self._helper.cfg, 'reposName'):
            self._helper.cfg.reposName = 'localhost'

        self._testSuite = TestSuite()
        self._coverageData = {}

    def setPlan(self, plan):
        plan = copy.deepcopy(plan)
        for name, section in plan._sections.iteritems():
            if not ':' in name:
                continue
            sectype, name = name.split(':', 1)
            if sectype == 'target':
                self._targetConfigs[name] = section
            else:
                assert False
        self._cfg = plan
        self._helper.plan = plan

    def loadTargets(self):
        '''
        Locate source troves and yield a BobPackage for each of them.
        '''

        targetPackages = []
        batch = shadow.ShadowBatch(self._helper)
        mangleData = {  'scm': self._scm,
                        'macros': self._macros,
                        'plan': self._cfg,
                        }
        for name in self._cfg.target:
            packageName = name.split(':')[0]
            sourceName = packageName + ':source'
            targetConfig = self._targetConfigs.get(name, None)
            if not targetConfig.sourceTree:
                raise RuntimeError("Target %s requires a sourceTree setting" %
                        (sourceName,))
            repo, subpath = targetConfig.sourceTree.split(None, 1)
            scm, uri = self._scm[repo]
            cacheDir = os.path.join(self._helper.cfg.lookaside, packageName)
            recipeFiles = hg.getRecipe(uri, scm.revision, subpath, cacheDir)

            package = BobPackage(sourceName, targetConfig, recipeFiles)
            package.setMangleData(mangleData)
            package.addFlavors(flavors.expand_targets(targetConfig))

            targetPackages.append(package)
            batch.addPackage(package)

        batch.shadow()


        return targetPackages

    def _configure(self):
        '''
        Pre-build setup
        '''
        cfg = self._helper.cfg
        self._macros = self._cfg.getMacros()

        cfg.strictMode = True
        cfg.copyInConary = cfg.copyInConfig = False

        # These options translate directly from the plan to rMake
        # or conary
        for x in ('resolveTrovesOnly', 'shortenGroupFlavors'):
            cfg[x] = self._cfg[x]

        # And these are a little more indirect
        cfg.buildLabel = self._cfg.targetLabel
        cfg.cleanAfterCook = not self._cfg.noClean
        cfg.resolveTroves = substResolveTroves(self._cfg.resolveTroves,
            self._macros)
        cfg.resolveTroveTups = buildcmd._getResolveTroveTups(
            cfg, self._helper.getRepos())
        cfg.autoLoadRecipes = substStringList(self._cfg.autoLoadRecipes,
                                              self._macros)
        if not self._cfg.isDefault('defaultBuildReqs'):
            cfg.defaultBuildReqs = substStringList(
                    self._cfg.defaultBuildReqs, self._macros)

        installLabelPath = self._cfg.installLabelPath
        cfg.installLabelPath = substILP(installLabelPath, self._macros)

        cfg.initializeFlavors()

        # Set up global macros
        for key, value in self._cfg.macros.iteritems():
            if key in self._cfg.skipMacros:
                continue
            try:
                cfg.configLine('macros %s %s' % (key, value % self._macros))
            except MacroKeyError:
                # Maybe requires a build-time macro
                pass

        self._helper.getrMakeClient().addRepositoryInfo(cfg)
        self._helper.configChanged()

    def _freezeScm(self):
        '''
        Obtain revisions of hg repositories
        '''
        self._scm = {}
        for name, repos in self._cfg.getRepositories(self._macros
                ).iteritems():
            uri = self._cfg.getUriForScm(repos)
            if not repos.revision:
                repos.revision = hg.get_tip(uri)
            self._scm[name] = (repos, uri)
            log.info("For repository %s, using %s revision %s", name, uri,
                    repos.revision)

    def _registerCommand(self, *args, **kwargs):
        'Fake rMake hook'
        pass

    def _cleanArtifacts(self):
        '''
        Delete artifacts like test results and coverage data.
        '''
        if os.path.isdir('output'):
            shutil.rmtree('output')

    def _writeArtifacts(self):
        '''
        Announce test results and write tests and coverage to disk.
        '''
        print self._testSuite.describe()

        os.makedirs('output/tests')
        if self._testSuite.tests:
            self._testSuite.write_junit(open('output/tests/junit.xml', 'w'))

        if self._coverageData:
            report = coverage.process(self._coverageData)
            # build the coverage data objects
            cdo = coverage.CoverageData.parseCoverageData(report)
            # TODO: merge pickle/old school data into coverage data obj
            cdo.pickleCoverageDict = self._coverageData
            cdo.oldSchoolCoverageData = report
            coverage.generate_reports('output/coverage', cdo)
            
    def run(self):
        '''
        Execute the bob plan.
        '''

        log.info('Initializing build')
        self._cleanArtifacts()
        self._configure()
        self._freezeScm()

        # Translate configuration into BobPackage objects
        targetPackages = self.loadTargets()

        # Run and commit each batch
        commitMap = {}
        for batch in recurse.getBatchFromPackages(self._helper, targetPackages):
            try:
                commitMap.update(batch.run(self))
            except JobFailedError, e:
                print 'Job %d failed:' % e.jobId
                print e.why
                return 2
            except TestFailureError:
                self._testSuite.merge(batch.getTestSuite())
                coverage.merge(self._coverageData, batch.getCoverageData())

                # We need to write out the test results early since
                # some failed
                self._writeArtifacts()
                print 'Aborting due to failed tests'
                return 4
            else:
                self._testSuite.merge(batch.getTestSuite())
                coverage.merge(self._coverageData, batch.getCoverageData())

        # Output test and coverage results
        self._writeArtifacts()

        # Output built troves
        reportCommitMap(commitMap)

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
    formatter = logging.Formatter('%(asctime)s %(levelname)s '
        '%(name)s %(message)s')
    handler.setFormatter(formatter)
    root_log.addHandler(handler)
    root_log.setLevel(logging.DEBUG)

    # Delete conary's log handler since it puts things on stderr and without
    # any timestamps.
    conary_log = logging.getLogger('conary')
    for handler in conary_log.handlers:
        conary_log.removeHandler(handler)


def stop(signum, frame):
    '''
    Signal handler to log a message then quit. Replaced by
    L{cook.stopJob<bob.cook.stopJob>} during cooks.
    '''

    log.error('Caught signal %d; aborting.', signum)
    sys.exit('Signalled stop')


def _main(plan):
    pushStopHandler(stop)

    addRootLogger()

    pluginmgr = getPluginManager()
    _main = BobMain(pluginmgr)

    # Restore regular exception hook
    sys.excepthook = sys.__excepthook__

    _main.setPlan(plan)
    return _main.run()


def banner():
    rev = version.revision and ' (revision %s)' % version.revision or ''
    print 'Bob the Constructinator version %s%s' % (version.version, rev)
    print 'Copyright (c) 2010 rPath, Inc.'
    print 'All rights reserved.'
    print


def mainFromPlan(plan):
    banner()
    return _main(plan)


def main(args):
    banner()

    try:
        plan = args[0]
    except IndexError:
        print >>sys.stderr, 'Usage: %s <plan file or URI>' % sys.argv[0]
        return 1

    plan = config.openPlan(plan)
    return _main(plan)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
