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
from conary.conaryclient import callbacks
from conary.deps import deps
from conary.lib import log
from conary.trove import Trove
from rmake import compat
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

class DummyMain:
    def _registerCommand(*P, **K):
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
        self.buildcfg.limitToLabels([self.cfg.sourceLabel.asString()])

        self.buildcfg.dropContexts()
        self.buildcfg.initializeFlavors()
        self.buildcfg.buildLabel = self.cfg.sourceLabel
        self.buildcfg.installLabelPath = [self.cfg.sourceLabel] + \
            self.cfg.installLabelPath
        self.buildcfg.resolveTroves = self.cfg.resolveTroves
        self.buildcfg.resolveTroveTups = buildcmd._getResolveTroveTups(
            self.buildcfg, self.nc)

        # Create rMake job
        job = buildjob.BuildJob()
        job.setMainConfig(self.buildcfg)

        # Determine which troves to build
        troveList = self.getMangledTroves(troveSpecs)

        # Add troves to job
        print 'These troves will be built:'
        for name, version, flavor in troveList:
            if flavor is None:
                flavor = deps.parseFlavor('')
            job.addTrove(name, version, flavor, '')
            print '%s=%s[%s]' % (name, version, flavor)

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
        print 'Initializing build'
        toBuild = {}
        def markForBuilding(name, version, flavors):
            toBuild.setdefault(name, (version, set()))[1].update(flavors)

        self.buildcfg.buildTroveSpecs = []
        localRepos = recipeutil.RemoveHostRepos(self.nc,
            self.buildcfg.reposName)
        use.setBuildFlagsFromFlavor(None, self.buildcfg.buildFlavor,
            error=False)

        print 'Iterating sources:'
        for name, version, flavors in self.groupTroveSpecs(troveSpecs):
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

    def commit(self, job):
        target_label = self.getLabelFromTag('test')

        branch_map = {} # source_branch -> target_branch
        nbf_map = {} # name, target_branch, flavor -> trove, source_version

        for trove in job.iterTroves():
            source_name, source_version, _ = trove.getNameVersionFlavor()
            assert source_version.getHost() == self.buildcfg.reposName

            # Determine which branch this will be committed to
            source_branch = source_version.branch()
            origin_branch = source_branch.parentBranch()
            target_branch = origin_branch.createShadow(target_label)

            # Mark said branch for final promote
            branch_map[source_branch] = target_branch

            # Check for different versions of the same trove
            nbf = source_name, target_branch, deps.Flavor()
            if nbf in nbf_map and nbf_map[nbf][1] != source_version:
                bad_version = nbf_map[nbf][1]
                raise RuntimeError("Cannot commit two different versions of "
                    "source component %s: %s and %s" % (source_name,
                    source_version, bad_version))
            nbf_map[nbf] = trove, source_version

            # Add binary troves to mapping
            for bin_name, bin_version, bin_flavor in trove.iterBuiltTroves():
                nbf = bin_name, target_branch, bin_flavor
                # Let's be lazy and not implement code that really does not
                # apply to this specific use case.
                assert nbf not in nbf_map, "This probably should not happen"
                nbf_map[nbf] = trove, bin_version

        # Determine what to clone
        troves_to_clone = []

        for (trv_name, trv_branch, trv_flavor), (trove, trv_version) \
          in nbf_map.iteritems():
            troves_to_clone.append((trv_name, trv_version, trv_flavor))

        # Do the clone
        update_build_info = compat.ConaryVersion()\
            .acceptsPartialBuildReqCloning()
        callback = callbacks.CloneCallback(self.buildcfg,
            self.cfg.commitMessage)
        okay, cs = self.cc.createTargetedCloneChangeSet(
            branch_map, troves_to_clone,
            updateBuildInfo=update_build_info,
            cloneSources=False,
            trackClone=False,
            callback=callback,
            fullRecurse=False)

        # Sanity check the resulting changeset and produce a mapping of
        # committed versions
        mapping = {trove.jobId: {}}
        if okay:
            # First get trove objects for all relative changes
            old_troves = []
            for trove_cs in cs.iterNewTroveList():
                if trove_cs.getOldVersion():
                    old_troves.append(trove_cs.getOldNameVersionFlavor())
            old_dict = {}
            if old_troves:
                for old_trove in self.nc.getTroves(old_troves):
                    old_dict.setdefault(old_trove.getNameVersionFlavor(),
                                        []).append(old_trove)

            # Now iterate over all trove objects, using the old trove and
            # applying changes when necessary
            for trove_cs in cs.iterNewTroveList():
                if trove_cs.getOldVersion():
                    trv = old_dict[trove_cs.getOldNameVersionFlavor()].pop()
                    trv.applyChangeSet(trove_cs)
                else:
                    trv = Trove(trove_cs)

                # Make sure there are no references to the internal repos.
                for _, child_version, _ in trv.iterTroveList(
                  strongRefs=True, weakRefs=True):
                    assert \
                        child_version.getHost() != self.buildcfg.reposName, \
                        "Trove %s references repository" % trv

                #n,v,f = troveCs.getNewNameVersionFlavor()
                trove_name, trove_version, trove_flavor = \
                    trove_cs.getNewNameVersionFlavor()
                trove_branch = trove_version.branch()
                trove, _ = nbf_map[(trove_name, trove_branch, trove_flavor)]
                trove_nvfc = trove.getNameVersionFlavor(withContext=True)
                # map jobId -> trove -> binaries
                mapping[trove.jobId].setdefault(trove_nvfc, []).append(
                    (trove_name, trove_version, trove_flavor))
        else:
            raise RuntimeError('failed to clone finished build')

        if compat.ConaryVersion().signAfterPromote():
            cs = cook.signAbsoluteChangeset(cs)
        filename = 'bob-%s.ccs' % job.jobId
        cs.writeToFile(filename)
        print 'Changeset written to %s' % filename

        return mapping

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

        self.helper = helper.rMakeHelper(buildConfig=self.buildcfg)

        # Watch build (to stdout)
        self.pluginmgr.callClientHook('client_preCommand', DummyMain(),
            None, (self.buildcfg, self.buildcfg), None, None)
        self.pluginmgr.callClientHook('client_preCommand2', DummyMain(),
            self.helper, None)
        self.helper.watch(jobId, commit=False)

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
            mapping = self.commit(job)
        except Exception, e_value:
            self.rc.commitFailed([jobId], str(e_value))
            raise
        else:
            self.rc.commitSucceeded(mapping)

        # Report committed troves
        def _sortCommitted(tup1, tup2):
            return cmp((tup1[0].endswith(':source'), tup1),
                       (tup2[0].endswith(':source'), tup2))
        def _formatTup(tup):
            args = [tup[0], tup[1]]
            if tup[2].isEmpty():
                args.append('')
            else:
                args.append('[%s]' % buildTroveTup[2])
            if not tup[3]:
                args.append('')
            else:
                args.append('{%s}' % buildTroveTup[3])
            return '%s=%s%s%s' % tuple(args)
        for jobId, troveTupleDict in sorted(mapping.iteritems()):
            print
            print 'Committed job %s:\n' % jobId,
            for buildTroveTup, committedList in \
              sorted(troveTupleDict.iteritems()):
                committedList = [ x for x in committedList
                                    if (':' not in x[0]
                                        or x[0].endswith(':source')) ]
                print '    %s ->' % _formatTup(buildTroveTup)
                print ''.join('       %s=%s[%s]\n' % x
                              for x in sorted(committedList,
                                              _sortCommitted))

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

    # = plugins.getPluginManager(sys.argv, buildcfg.BuildConfiguration)
    pluginmgr = getPluginManager()
    pluginmgr.callClientHook('client_preInit', DummyMain(), sys.argv)
    bcfg = buildcfg.BuildConfiguration(True)
    bcfg.readFiles()

    bob = CookBob(bcfg, pluginmgr)
    bob.readPlan(plan)
    sys.exit(bob.run())

