#
# Copyright (c) SAS Institute Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


import copy
import logging
import optparse
import os
import shutil
import sys

from conary.build.macros import MacroKeyError
from conary.lib import util as cny_util
from rmake import plugins
from rmake.cmdline import buildcmd
from rmake.build import buildcfg

from bob import config
from bob import coverage
from bob import flavors
from bob import recurse
from bob import shadow
from bob import util
from bob import version
from bob.errors import JobFailedError, TestFailureError
from bob.macro import substILP, substResolveTroves, substStringList
from bob.rev_file import RevisionFile
from bob.scm import git
from bob.scm import hg
from bob.scm import wms
from bob.test import TestSuite
from bob.trove import BobPackage
from bob.util import ClientHelper, pushStopHandler, reportCommitMap

log = logging.getLogger('bob.main')


class BobMain(object):
    bobCache = '__bob__'

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
            packageName = name.split(':')[0] % self._macros
            sourceName = packageName + ':source'
            targetConfig = self._targetConfigs.get(name, None)
            if not targetConfig.sourceTree:
                raise RuntimeError("Target %s requires a sourceTree setting" %
                        (sourceName,))
            repoName, subpath = targetConfig.sourceTree.split(None, 1)
            repo = self._scm[repoName]
            subpath %= self._macros
            # Make a symlink for the usual conary cache location, so the recipe
            # loader can use it.
            cacheDir = os.path.join(self._helper.cfg.lookaside, packageName)
            if not os.path.islink(cacheDir):
                toDelete = None
                if os.path.exists(cacheDir):
                    toDelete = cacheDir + '.tmp.%d' % os.getpid()
                    os.rename(cacheDir, toDelete)
                os.symlink(self.bobCache, cacheDir)
                if toDelete:
                    cny_util.rmtree(toDelete)
            recipeFiles = repo.getRecipe(subpath)

            package = BobPackage(sourceName, targetConfig, recipeFiles)
            package.setMangleData(mangleData)
            package.addFlavors(flavors.expand_targets(targetConfig))

            targetPackages.append(package)
            batch.addPackage(package)

        batch.shadow()
        if self._cfg.depMode:
            return targetPackages, batch
        else:
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
        for x in (
                'resolveTrovesOnly',
                'rpmRequirements',
                'shortenGroupFlavors',
                ):
            cfg[x] = self._cfg[x]

        # And these are a little more indirect
        cfg.buildLabel = self._cfg.getTargetLabel()
        cfg.cleanAfterCook = not self._cfg.noClean
        cfg.resolveTroves = substResolveTroves(self._cfg.resolveTroves,
            self._macros)
        if not self._cfg.depMode:
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

        if not self._cfg.depMode:
            self._helper.getrMakeClient().addRepositoryInfo(cfg)
        self._helper.configChanged()

    def _freezeScm(self):
        '''
        Obtain revisions of hg repositories
        '''
        rf = RevisionFile()

        cacheDir = os.path.join(self._helper.cfg.lookaside, self.bobCache)
        cny_util.mkdirChain(cacheDir)
        self._scm = {}
        for name, (kind, uri, rev) in self._cfg.getRepositories(
                self._macros).iteritems():
            path = None
            if kind == 'hg':
                repo = hg.HgRepository(cacheDir, uri)
            elif kind == 'git':
                if '?' in uri:
                    path, branch = uri.split('?', 1)
                else:
                    path, branch = uri, 'master'
                repo = git.GitRepository(cacheDir, path, branch)
            elif kind == 'wms':
                if not rev:
                    raise RuntimeError("SCM statements of type 'wms' require "
                            "a branch argument")
                repo = wms.WmsRepository(self._cfg.wmsBase,
                        path=uri, branch=rev)
                # It's a branch, not a hard revision. Still need to consult the
                # revision.txt file.
                rev = None
            else:
                raise TypeError("Invalid SCM type %r in target %r"
                        % (kind, name))
            if rev:
                repo.revision = rev
            elif rf.filename:
                rev = rf.revs.get(uri)
                if not rev:
                    # Try the bare SCM alias
                    rev = rf.revs.get(name)
                if rev:
                    desc = rev['id']
                    if rev.get('branch'):
                        desc += ' on branch ' + rev['branch']
                    log.debug('Selected for %s %s (from %s)',
                            uri, desc, rf.filename)
                    repo.setRevision(rev)
                else:
                    raise RuntimeError('File %s exists but does not '
                            'contain repository %s or alias %s'
                            % (rf.filename, uri, name))
            else:
                if not self._cfg.depMode:
                    log.warning('No explicit revision given for repository %s, '
                            'using latest', uri)
                repo.setFromTip()
            repo.updateCache()
            self._scm[name] = repo
            if not self._cfg.depMode:
                log.info("For repository %s, using %s revision %s", name, uri,
                        repo.getShortRev())

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

        try:
            os.makedirs('output/tests')
        except OSError:
            # The directory is deleted at the beginning of the build, so if two
            # bobs are run in the same directory it might have been recreated
            # already.
            pass
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
        try:
            return self._run()
        finally:
            self._helper.cleanupEphemeralDir()

    def _run(self):
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
                newTroves = batch.run(self)
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
                return 0
            else:
                self._testSuite.merge(batch.getTestSuite())
                coverage.merge(self._coverageData, batch.getCoverageData())
                util.insertResolveTroves(self._helper.cfg, newTroves)
                commitMap.update(newTroves)

        # Output test and coverage results
        self._writeArtifacts()

        # Output built troves
        reportCommitMap(commitMap)

        return 0

    def runDeps(self):
        self._cfg.depMode = True
        self._configure()
        self._freezeScm()
        return self.loadTargets()


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
    print 'Copyright (c) SAS Institute, Inc.'
    print 'All rights reserved.'
    print


def mainFromPlan(plan):
    banner()
    return _main(plan)


def main(args=sys.argv[1:]):
    banner()

    parser = optparse.OptionParser(
            usage='Usage: %prog <plan file or URI> [options]')
    parser.add_option('--set-tag', action='append',
            help='tree=revision')
    parser.add_option('--set-version', action='append',
            help='package=version')
    parser.add_option('--debug', action='store_true')
    options, args = parser.parse_args(args)

    msg = """
%(filename)s:%(lineno)s
%(errtype)s: %(errmsg)s

The complete related traceback has been saved as %(stackfile)s
"""
    cny_hook = cny_util.genExcepthook(
            debug=options.debug,
            prefix='bob-error-',
            error=msg,
            )
    def excepthook(e_class, e_val, e_tb):
        if not options.debug:
            cny_util.formatTrace(e_class, e_val, e_tb, withLocals=False)
        return cny_hook(e_class, e_val, e_tb)
    sys.excepthook = excepthook

    if not args:
        parser.error('A plan file or URI is required')
    planFile = args[0]
    plan = config.openPlan(planFile)

    for val in (options.set_tag or ()):
        name, tag = val.split('=', 1)
        scm = plan.scm.get(name)
        if not scm:
            raise KeyError("scm %r is not in the plan file" % (name,))
        kind, uri = scm.split(' ', 1)
        if ' ' in uri:
            uri = uri.split(' ')[0]
        plan.scm[name] = ' '.join((kind, uri, tag))

    for val in (options.set_version or ()):
        name, version = val.split('=', 1)
        section = plan.setSection('target:' + name)
        section.version = version

    return _main(plan)


if __name__ == '__main__':
    sys.exit(main())
