#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

from conary.build import cook
from conary.conaryclient import callbacks
from conary.deps import deps
from conary.trove import Trove
from rmake import compat

def commit(parent_bob, job):
    branch_map = {} # source_branch -> target_branch
    nbf_map = {} # name, target_branch, flavor -> trove, source_version

    for trove in job.iterTroves():
        source_name, source_version, _ = trove.getNameVersionFlavor()
        assert source_version.getHost() == parent_bob.buildcfg.reposName

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
    callback = callbacks.CloneCallback(parent_bob.buildcfg,
        parent_bob.cfg.commitMessage)
    okay, cs = parent_bob.cc.createTargetedCloneChangeSet(
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
            for old_trove in parent_bob.nc.getTroves(old_troves):
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
                assert child_version.getHost() \
                    != parent_bob.buildcfg.reposName, \
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
