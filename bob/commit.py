#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Mechanism for committing bobs to a configured target repository.
'''

from conary.build import cook
from conary.conaryclient import callbacks
from conary.deps import deps
from conary.trove import Trove
from rmake import compat

def commit(parent, job):
    '''
    Commit a job to the target repository.

    @param job: rMake job
    '''

    okay, changeset, nbf_map = clone_job(parent, job)

    # Sanity check the resulting changeset and produce a mapping of
    # committed versions
    mapping = {job.jobId: {}}
    if okay:
        for trove in iter_new_troves(changeset, parent.nc):
            # Make sure there are no references to the internal repos.
            for _, child_version, _ in trove.iterTroveList(
              strongRefs=True, weakRefs=True):
                assert child_version.getHost() \
                    != parent.buildcfg.reposName, \
                    "Trove %s references repository" % trove

            trove_name, trove_version, trove_flavor = \
                trove.getNameVersionFlavor()
            trove_branch = trove_version.branch()
            trove, _ = nbf_map[(trove_name, trove_branch, trove_flavor)]
            trove_nvfc = trove.getNameVersionFlavor(withContext=True)
            # map jobId -> trove -> binaries
            mapping[trove.jobId].setdefault(trove_nvfc, []).append(
                (trove_name, trove_version, trove_flavor))
    else:
        raise RuntimeError('failed to clone finished build')

    if compat.ConaryVersion().signAfterPromote():
        changeset = cook.signAbsoluteChangeset(changeset)
    parent.nc.commitChangeSet(changeset)
    return mapping

def clone_job(parent, job):
    '''
    Create a changeset that will clone all built troves into the target
    label.
    '''

    branch_map = {} # source_branch -> target_branch
    nbf_map = {} # name, target_branch, flavor -> trove, source_version

    for trove in job.iterTroves():
        source_name, source_version, _ = trove.getNameVersionFlavor()
        assert source_version.getHost() == parent.buildcfg.reposName

        # Determine which branch this will be committed to
        source_branch = source_version.branch()
        origin_branch = source_branch.parentBranch()
        target_branch = origin_branch.createShadow(parent.cfg.targetLabel)

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
    callback = callbacks.CloneCallback(parent.buildcfg,
        parent.cfg.commitMessage)
    okay, changeset = parent.cc.createTargetedCloneChangeSet(
        branch_map, troves_to_clone,
        updateBuildInfo=update_build_info,
        cloneSources=False,
        trackClone=False,
        callback=callback,
        fullRecurse=False)
    return okay, changeset, nbf_map

def iter_new_troves(changeset, nc):
    '''
    Take a changeset and yield trove objects corresponding to the new
    versions of all troves in that changeset. This involves fetching old
    troves and applying the changeset to them to produce the new troves.
    '''

    # First get a list of all relative trove changesets
    old_troves = []
    for trove_cs in changeset.iterNewTroveList():
        if trove_cs.getOldVersion():
            old_troves.append(trove_cs.getOldNameVersionFlavor())

    # Now fetch trove objects corresponding to old versions
    old_dict = {}
    if old_troves:
        for old_trove in nc.getTroves(old_troves):
            old_dict.setdefault(old_trove.getNameVersionFlavor(),
                                []).append(old_trove)

    # Iterate over changeset again, yielding new trove objects
    for trove_cs in changeset.iterNewTroveList():
        if trove_cs.getOldVersion():
            trv = old_dict[trove_cs.getOldNameVersionFlavor()].pop()
            trv.applyChangeSet(trove_cs)
            yield trv
        else:
            yield Trove(trove_cs)
