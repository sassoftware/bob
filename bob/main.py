#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import logging
import os
import shutil
import sys
import time

from conary.build.macros import Macros, MacroKeyError
from rmake import plugins
from rmake.cmdline import buildcmd
from rmake.build import buildcfg

from bob import config
from bob import coverage
from bob import hg
from bob import recurse
from bob import version
from bob.errors import JobFailedError, TestFailureError
from bob.macro import substResolveTroves
from bob.test import TestSuite
from bob.trove import BobPackage
from bob.util import ClientHelper

log = logging.getLogger('bob.main')


class BobMain(object):
    def __init__(self, pluginmgr):
        pluginmgr.callClientHook('client_preInit', self, sys.argv)

        bcfg = buildcfg.BuildConfiguration(True)
        bcfg.readFiles()

        self._cfg = config.BobConfig()
        self._helper = ClientHelper(bcfg, self._cfg, pluginmgr)
        self._targetConfigs = {}
        self._macros = {}
        
        # repo info
        self._hg = {}

        # conary/rmake config
        if not hasattr(self._helper.cfg, 'reposName'):
            self._helper.cfg.reposName = 'localhost'

        self._testSuite = TestSuite()
        self._coverageData = {}

    def readPlan(self, plan):
        for cfgFile in ('/etc/bobrc', os.getenv('HOME', '/') + '/.bobrc'):
            if os.path.exists(cfgFile):
                self._cfg.read(cfgFile)

        log.debug('Fetching plan %s', plan)
        if plan.startswith('http://') or plan.startswith('https://'):
            self._cfg.readUrl(plan)
        else:
            self._cfg.read(plan)

        for name, section in self._cfg._sections.iteritems():
            if not ':' in name:
                continue
            sectype, name = name.split(':', 1)
            if sectype == 'target':
                self._targetConfigs[name] = section
            else:
                assert False

    def loadTargets(self):
        '''
        Locate source troves and yield a BobPackage for each of them.
        '''

        # First, find versions for targets
        query = []
        for name in self._cfg.target:
            sourceName = name.split(':')[0] + ':source'
            section = self._targetConfigs.get(name, None)
            sourceLabel = (section and section.sourceLabel) or \
                self._cfg.sourceLabel
            query.append((sourceName, str(sourceLabel), None))

        response = self._helper.getRepos().findTroves(None, query)

        targetPackages = []
        for matches in response.itervalues():
            latestVersion = max(x[1] for x in matches)
            sourceName, sourceVersion, _ = [
                x for x in matches if x[1] == latestVersion][0]
            packageName = sourceName.split(':')[0]
            targetConfig = self._targetConfigs.get(packageName, None)

            targetPackages.append(BobPackage(sourceName, sourceVersion, 
                                             targetConfig))

        return targetPackages

    def _configure(self):
        '''
        Pre-build setup
        '''
        cfg = self._helper.cfg
        self._macros = Macros(self._cfg.macros)

        cfg.strictMode = True
        cfg.copyInConary = cfg.copyInConfig = False

        # These options translate directly from the plan to rMake
        # or conary
        for x in ('resolveTrovesOnly', 'shortenGroupFlavors',
          'matchTroveRule'):
            cfg[x] = self._cfg[x]

        # And these are a little more indirect
        cfg.buildLabel = self._cfg.targetLabel
        cfg.installLabelPath = [self._cfg.sourceLabel] + \
            self._cfg.installLabelPath
        cfg.resolveTroves = substResolveTroves(self._cfg.resolveTroves,
            self._macros)
        cfg.resolveTroveTups = buildcmd._getResolveTroveTups(
            cfg, self._helper.getRepos())

        cfg.initializeFlavors()

        # Set up global macros
        for key, value in self._cfg.macros.iteritems():
            if key in self._cfg.skipMacros:
                continue
            try:
                cfg.macros[key] = value % self._macros
            except MacroKeyError:
                # Maybe requires a build-time macro
                pass

        self._helper.getrMakeClient().addRepositoryInfo(cfg)
        self._helper.configChanged()


    def _freezeHg(self):
        '''
        Obtain revisions of hg repositories
        '''

        for name, uri in self._cfg.hg.iteritems():
            name %= self._macros
            if ' ' in uri:
                uri, revision = uri.split(' ', 1)
                uri %= self._macros
                revision %= self._macros
            else:
                uri %= self._macros
                revision = hg.get_tip(uri)
            self._hg[name] = (uri, revision)

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
        self._freezeHg()

        mangleData = {  'startTime': time.time(),
                        'hg': self._hg,
                        'plan': self._cfg,
                        }

        # Translate configuration into BobPackage objects
        targetPackages = self.loadTargets()
        allPackages = recurse.getPackagesFromTargets(targetPackages,
            self._helper, mangleData, self._targetConfigs)

        # Run and commit each batch
        for batch in recurse.getBatchFromPackages(self._helper, allPackages):
            try:
                batch.run(self)
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
    root_log.setLevel(logging.INFO)

    # Delete conary's log handler since it puts things on stderr and without
    # any timestamps.
    conary_log = logging.getLogger('conary')
    for handler in conary_log.handlers:
        conary_log.removeHandler(handler)

def main(args):
    rev = version.revision and ' (revision %s)' % version.revision or ''
    print 'Bob the Builder version %s%s' % (version.version, rev)
    print 'Copyright (c) 2008 rPath, Inc.'
    print 'All rights reserved.'
    print

    try:
        plan = args[0]
    except IndexError:
        print >>sys.stderr, 'Usage: %s <plan file or URI>' % sys.argv[0]
        return 1

    addRootLogger()

    pluginmgr = getPluginManager()
    _main = BobMain(pluginmgr)

    # Restore regular exception hook
    sys.excepthook = sys.__excepthook__

    _main.readPlan(plan)
    return _main.run()

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
