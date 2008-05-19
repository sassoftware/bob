#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Tools for recursing through a set of targets, and for preparing batches
to build.
'''

import logging

from conary.build.grouprecipe import findSourcesForGroup
from conary.deps import deps
from rmake.cmdline.buildcmd import _filterListByMatchSpecs
from rmake.lib import recipeutil

from bob.cook import Batch
from bob.flavors import expand_targets
from bob.mangle import prepareTrove
from bob.errors import DependencyLoopError
from bob.trove import BobPackage


log = logging.getLogger('bob.recurse')


def recurseGroupInstance(helper, bobPackage, buildFlavor):
    '''
    Given a group I{BobPackage} and a I{buildFlavor}, determine what
    troves in that group could be built and which flavors of it are
    useful.
    '''

    _, recipeObj, _ = recipeutil.loadRecipe(
        helper.getRepos(),
        bobPackage.getName(), bobPackage.getDownstreamVersion(), buildFlavor,
        bobPackage.getDownstreamSourceTrove(helper),
        installLabelPath=helper.cfg.installLabelPath,
        buildLabel=helper.plan.sourceLabel,
        cfg=helper.cfg)

    for n, v, f in findSourcesForGroup(
      helper.getRepos(), recipeObj):
        if f is None:
            f = deps.Flavor()

        if v.trailingLabel() == helper.plan.sourceLabel:
            childFlavor = deps.overrideFlavor(buildFlavor, f)
            yield n, v, childFlavor


def getPackagesFromTargets(targetPackages, helper, mangleData, targetConfigs):
    '''
    Given a set of I{BobPackage}s, recurse through any groups in the
    set and return a list of additional I{BobPackage}s to build.
    '''

    buildPackages = dict((x.getName(), x) for x in targetPackages)

    def _build(name, version, flavor, parent=None):
        '''
        Helper: creates a BobPackage from a NVF and adds it to
        I{buildPackages}
        '''

        # Check that the trove doesn't have a matchTroveRule against it
        if not _filterListByMatchSpecs(helper.cfg.reposName,
          helper.plan.matchTroveRule, [(name, version, deps.Flavor())]):
            log.debug('Skipping %s=%s due to matchTroveRule', name, version)
            return

        # Create a BobPackage if it doesn't exist
        if name not in buildPackages:
            packageName = name.split(':')[0]
            buildPackages[name] = BobPackage(name, version,
                targetConfig=targetConfigs.get(packageName, None))
        package = buildPackages[name]

        # Mangle if needed
        if not package.hasDownstreamVersion():
            prepareTrove(package, mangleData, helper)

        # Add new flavor(s) to package
        package.addFlavors([flavor])

        # If this was pulled in by a group, mark it as a child package
        if parent:
            assert parent in buildPackages
            buildPackages[parent].addChild(name)

        return buildPackages[name]

    for package in targetPackages:
        buildFlavors = expand_targets(package.getTargetConfig())
        
        # Mark the target for building first
        package.addFlavors(buildFlavors)
        if not package.hasDownstreamVersion():
            prepareTrove(package, mangleData, helper)

        # If this is a group, recurse through it
        if package.getName().startswith('group-'):
            # Check against recurseTroveRule to see if we should follow
            if not _filterListByMatchSpecs(helper.cfg.reposName,
              helper.plan.recurseTroveRule,
              [package.getDownstreamNameVersionFlavor()]):
                log.debug('Not following %s due to resolveTroveRule',
                    package.getName())
                continue

            log.debug('Following %s=%s' % package.getDownstreamNameVersion())

            for buildFlavor in buildFlavors:
                # For every build flavor, recurse the group and get a
                # list of tuples to build.
                for newName, newVersion, newBuildFlavor in \
                  recurseGroupInstance(helper, package, buildFlavor):
                    # Convert the tuple to a BobPackage
                    _build(newName, newVersion, newBuildFlavor,
                        parent=package.getName())

            # Clean up the Trove object loaded in recurseGroupInstance
            # so it doesn't waste memory
            package.deleteDownstreamSourceTrove()

    return buildPackages.values()


def getBatchFromPackages(helper, packageList):
    '''
    Given a set of I{BobPackage}s, yield a sequence of I{Batch} objects
    to build and commit.

    @param helper: ClientHelper object
    @param packageList: List of I{BobPackage}s to build
    '''

    built = set() # names
    notBuilt = set(packageList) # BobPackages

    while notBuilt:
        # Determine which troves can be built
        thisRound = set()
        unmetThisRound = dict()
        for bobPackage in notBuilt:
            unmetRequires = bobPackage.getChildren() - built
            if unmetRequires:
                # Not all requirements have been built; skip
                unmetThisRound[bobPackage.getName()] = unmetRequires
                continue
            thisRound.add(bobPackage)

        if not thisRound:
            # Nothing can be built!
            log.error('Unmet dependencies:')
            for name, unmet in unmetThisRound.iteritems():
                log.error('  %s: %s', name, ' '.join(unmet))
            raise DependencyLoopError()

        batch = Batch(helper)
        for bobTrove in thisRound:
            batch.addTrove(bobTrove)

        notBuilt -= thisRound
        built |= set(x.getName() for x in thisRound)

        yield batch
