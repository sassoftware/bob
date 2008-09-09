#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Tools for recursing through a set of targets, and for preparing batches
to build.
'''

import copy
import logging

from conary.build.grouprecipe import findSourcesForGroup
from conary.build.loadrecipe import RecipeLoaderFromSourceTrove
from conary.build.use import setBuildFlagsFromFlavor
from conary.conarycfg import ConaryConfiguration
from conary.deps import deps
from rmake.cmdline.buildcmd import _filterListByMatchSpecs

from bob.cook import Batch
from bob.flavors import expand_targets
from bob.errors import DependencyLoopError
from bob.shadow import ShadowBatch
from bob.trove import BobPackage


log = logging.getLogger('bob.recurse')


def recurseGroupInstance(helper, bobPackage, buildFlavor):
    '''
    Given a group I{BobPackage} and a I{buildFlavor}, determine what
    troves in that group could be built and which flavors of it are
    useful.
    '''

    setBuildFlagsFromFlavor(bobPackage.getPackageName(), buildFlavor,
        error=False)

    sourceTrove = bobPackage.getDownstreamSourceTrove(helper)
    loader = RecipeLoaderFromSourceTrove(sourceTrove, helper.getRepos(),
        helper.cfg, ignoreInstalled=True)
    recipeClass = loader.getRecipe()

    buildLabel = helper.plan.sourceLabel
    macros = {  'buildlabel': buildLabel.asString(),
                'buildbranch': bobPackage.getDownstreamVersion().
                    branch().parentBranch().asString()}
    recipeObj = recipeClass(helper.getRepos(), helper.cfg,
        buildLabel, buildFlavor, None, extraMacros=macros)
    recipeObj.sourceVersion = bobPackage.getDownstreamVersion()
    recipeObj.setup()

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

    log.info('Loading troves')

    buildPackages = dict((x.getName(), x) for x in targetPackages)
    targetShadows = ShadowBatch()
    childShadows = ShadowBatch()

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
            childShadows.addPackage(package)

        # Add new flavor(s) to package
        package.addFlavors([flavor])

        # If this was pulled in by a group, mark it as a child package
        if parent:
            assert parent in buildPackages
            buildPackages[parent].addChild(name)

        return buildPackages[name]

    # First, mark all targets for building
    for package in targetPackages:
        buildFlavors = expand_targets(package.getTargetConfig())
        package.addFlavors(buildFlavors)
        targetShadows.addPackage(package)
    
    # Shadow and mangle all targets
    targetShadows.shadow(helper, mangleData)

    # Now go back and recurse through the groups
    for package in targetPackages:
        if not package.getName().startswith('group-'):
            continue

        # Check against recurseTroveRule to see if we should follow
        if not _filterListByMatchSpecs(helper.cfg.reposName,
          helper.plan.recurseTroveRule,
          [package.getDownstreamNameVersionFlavor()]):
            log.debug('Not following %s due to resolveTroveRule',
                package.getName())
            continue

        log.info('Following %s' % package.getPackageName())

        for buildFlavor in package.getFlavors():
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

    # Shadow and mangle all child troves
    childShadows.shadow(helper, mangleData)

    return buildPackages.values()


def getBatchFromPackages(helper, packageList):
    '''
    Given a set of I{BobPackage}s, yield a sequence of I{Batch} objects
    to build and commit.

    Packages are split along group inclusion lines, that is, we do not
    care about build requirements (which rMake will handle), but we do
    care about packages that are included in groups which we will also
    build. The packages get built first (usually all in one batch),
    then any groups that can be built with just those packages, then
    any groups containing those groups just built, etc.

    Additionally, if any packages are marked as serialized, those
    packages will be split into one batch per flavor, and those "extra"
    batches will be yielded after any non-serialized packages in the
    current batch. If multiple packages are to be serialized, one
    batch will contain a single flavor from each of the serialized
    packages, rather than running one batch per flavor per package.

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

        notBuilt -= thisRound
        built |= set(x.getName() for x in thisRound)

        # Create a batch and add all non-serialized packages, saving
        # the serialized ones for later.
        toSerialize = []
        batch = Batch(helper)
        for bobTrove in thisRound:
            if bobTrove.getTargetConfig().serializeFlavors:
                toSerialize.append(bobTrove)
            else:
                batch.addTrove(bobTrove)

        # Immediately yield the set of buildable packages that do not
        # need to be serialized.
        if not batch.isEmpty():
            yield batch

        # Now go back to the serialized ones, splitting them by flavor
        # into individual packages.
        batches = []
        for bobTrove in toSerialize:
            for idx, flavor in enumerate(sorted(bobTrove.getFlavors())):
                newTrove = copy.deepcopy(bobTrove)
                newTrove.setFlavors([flavor])
                newTrove.getTargetConfig().flavor = [str(flavor)]
                if len(batches) == idx:
                    batches.append(Batch(helper))
                batches[idx].addTrove(newTrove)
        for batch in batches:
            yield batch
