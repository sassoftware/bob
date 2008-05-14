#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Mechanism for committing bobs to a configured target repository.
'''

import logging
import time

from conary.build import cook
from conary.conaryclient import callbacks
from conary.deps import deps
from conary.trove import Trove
from rmake import compat
from rmake.cmdline.commit import commitJobs

from bob.errors import CommitFailedError

log = logging.getLogger('bob.commit')


def commit(helper, job):
    ok, data = commitJobs(helper.getClient(), [job], helper.cfg.reposName,
        message=helper.plan.commitMessage)
    if ok:
        return data
    else:
        raise CommitFailedError(jobId=job.jobId, why=data)

def _old_commit(helper, job):
    '''
    Commit a job to the target repository.

    @param job: rMake job
    '''
    log.info('Starting commit')
    _start_time = time.time()

    okay, changeset, nbf_map = clone_job(helper, job)

    # Sanity check the resulting changeset and produce a mapping of
    # committed versions
    mapping = {job.jobId: {}}
    if okay:
        for trove in iter_new_troves(changeset, helper):
            # Make sure there are no references to the internal repos.
            for _, child_version, _ in trove.iterTroveList(
              strongRefs=True, weakRefs=True):
                assert child_version.getHost() \
                    != helper.cfg.reposName, \
                    "Trove %s references repository" % trove
                #assert not child_name.endswith(':testinfo'), \
                #    "Trove %s references :testinfo component" % trove

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
    helper.getRepos().commitChangeSet(changeset)

    _finish_time = time.time()
    log.info('Commit took %.03f seconds', _finish_time - _start_time)
    return mapping

def clone_job(helper, job):
    '''
    Create a changeset that will clone all built troves into the target
    label.
    '''

    branch_map = {} # source_branch -> target_branch

    # nbf_map := name, target_branch, flavor -> trove, source_version
    # This maps a given name and built flavor back to the BuildTrove that
    # created it, allowing us to find duplicate builds and build a clone
    # job.
    nbf_map = {}

    for trove in job.iterTroves():
        source_name, source_version, _ = trove.getNameVersionFlavor()
        #assert source_version.getHost() == helper.cfg.reposName

        # Determine which branch this will be committed to
        source_branch = origin_branch = source_version.branch()
        target_branch = origin_branch.createShadow(helper.plan.targetLabel)

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
            # Don't commit test info
            #if bin_name.endswith(':testinfo'):
            #    continue

            nbf = bin_name, target_branch, bin_flavor
            if nbf in nbf_map:
                # Eliminate duplicate commits of the same NBF by keeping
                # only the newer package
                other_version = nbf_map[nbf][0].getBinaryTroves()[0][1]
                if other_version < bin_version:
                    bad_trove, new_trove = nbf_map[nbf][0], trove
                    new_version = bin_version
                else:
                    new_trove, bad_trove = nbf_map[nbf][0], trove
                    new_version = other_version
                new_name = new_trove.getName().split(':')[0]

                # Delete commit maps for the entire rejected package
                for bad_name, bad_version, bad_flavor \
                  in bad_trove.iterBuiltTroves():
                    bad_nbf = (bad_name, target_branch, bad_flavor)
                    if not ':' in bad_name:
                        log.warning('Not committing %s=%s[%s] - overridden '
                            'by %s=%s', bad_name, bad_version, bad_flavor,
                            new_name, new_version)
                    if bad_nbf in nbf_map \
                      and bad_trove is nbf_map[bad_nbf][0]:
                        log.debug('Purging %s=%s[%s]' % (bad_name, bad_version,
                            bad_flavor))
                        del nbf_map[bad_nbf]

                # If this trove is the bad trove, stop processing it
                if trove is bad_trove:
                    break

            nbf_map[nbf] = trove, bin_version

    # Determine what to clone
    troves_to_clone = []

    for (trv_name, _, trv_flavor), (trove, trv_version) \
      in nbf_map.iteritems():
        troves_to_clone.append((trv_name, trv_version, trv_flavor))

    # Do the clone
    update_build_info = compat.ConaryVersion()\
        .acceptsPartialBuildReqCloning()
    callback = callbacks.CloneCallback(helper.cfg,
        helper.plan.commitMessage)
    okay, changeset = helper.getClient().createTargetedCloneChangeSet(
        branch_map, troves_to_clone,
        updateBuildInfo=update_build_info,
        cloneSources=False,
        trackClone=False,
        callback=callback,
        #cloneOnlyByDefaultTroves=True,
        fullRecurse=False)
    return okay, changeset, nbf_map

def iter_new_troves(changeset, helper):
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
        for old_trove in helper.getRepos().getTroves(old_troves):
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
