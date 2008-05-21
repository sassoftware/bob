#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Build, commit, and process troves in "batches"
'''

import logging
import time

from conary.build.macros import Macros
from rmake.cmdline import monitor

from bob import commit
from bob import flavors
from bob import test
from bob.errors import JobFailedError, TestFailureError
from bob.util import ContextCache, StatusOnlyDisplay


log = logging.getLogger('bob.cook')



class Batch(object):
    '''
    A batch of troves to be built at once; e.g. packages or groups. A
    batch may contain either troves or groups but not both, and if it
    includes groups, none of the groups may add another group also in
    the same batch.
    '''

    def __init__(self, clientHelper):
        self._helper = clientHelper

        # setup
        self._contextCache = ContextCache(self._helper.cfg)
        self._troves = set()

        # results
        self._testSuite = None
        self._coverageData = None

    def addTrove(self, bobTrove):
        '''
        Add a I{BobPackage} to the batch.

        @type bobTrove: L{bob.trove.BobPackage}
        '''

        _macros = Macros(self._helper.plan.macros)
        macros = {}
        config = bobTrove.getTargetConfig()
        if config:
            for key, value in config.macros.iteritems():
                if key not in self._helper.plan.skipMacros:
                    macros[key] = value % _macros

        # Reduce the set of flavors to build
        newFlavors = flavors.reduce_flavors(bobTrove.getPackageName(),
            bobTrove.getTargetConfig(), bobTrove.getFlavors())

        if len(newFlavors) != len(bobTrove.getFlavors()):
            log.debug('Package %s would be built in %d flavors; '
                'now built in %d flavors', bobTrove.getPackageName(),
                len(bobTrove.getFlavors()), len(newFlavors))
        else:
            log.debug('Package %s will be built in %d flavors',
                bobTrove.getPackageName(), len(newFlavors))

        for buildFlavor in newFlavors:
            # Calculate build parameters
            searchFlavors = flavors.guess_search_flavors(buildFlavor)

            # Get a context in which to build the trove
            context = self._contextCache.get(buildFlavor, searchFlavors,
                macros)

            # Add the tuple to the build list
            self._troves.add((bobTrove.getName(),
                bobTrove.getDownstreamVersion(), buildFlavor, context))

    def run(self, main):
        '''
        Create and run a rMake build job from the set of added troves,
        process the resulting artifacts, and commit if no errors or
        failed tests were encountered.
        '''

        troveNames = sorted(set(x[0].split(':')[0] for x in self._troves))
        log.info('Creating build job: %s', ' '.join(troveNames))

        # Create rMake job
        job = self._helper.getrMakeHelper().createBuildJob(list(self._troves),
            buildConfig=self._helper.cfg)
        jobId = self._helper.getrMakeClient().buildJob(job)
        log.info('Job %d started', jobId)

        # Watch build (to stdout)
        self._helper.callClientHook('client_preCommand', main,
            None, (self._helper.cfg, self._helper.cfg),
            None, None)
        self._helper.callClientHook('client_preCommand2', main,
            self._helper.getrMakeHelper(), None)
        monitor.monitorJob(self._helper.getrMakeClient(), jobId,
            exitOnFinish=True, displayClass=StatusOnlyDisplay)

        # Check for error condition
        job = self._helper.getrMakeClient().getJob(jobId)
        if job.isFailed():
            log.error('Job %d failed', jobId)
            raise JobFailedError(jobId=jobId, why='Job failed')
        elif not job.isFinished():
            log.error('Job %d is not done, yet watch returned early!', jobId)
            raise JobFailedError(jobId=jobId, why='Job not done')
        elif not list(job.iterBuiltTroves()):
            log.error('Job %d has no built troves', jobId)
            raise JobFailedError(jobId=jobId, why='Job built no troves')

        # Fetch test/coverage output and report results
        self._testSuite, self._coverageData = test.processTests(self._helper,
            job)
        print 'Batch results:', self._testSuite.describe()

        # Bail out without committing if tests failed
        if not self._testSuite.isSuccessful():
            log.error('Some tests failed, aborting')
            raise TestFailureError()

        # Commit to target repository
        if job.isCommitting():
            log.error('Job %d is already committing ' \
                '(probably to the wrong place)', jobId)
            raise JobFailedError(jobId=jobId, why='Job already committing')

        startTime = time.time()
        log.info('Starting commit of job %d', jobId)
        self._helper.getrMakeClient().startCommit([jobId])

        try:
            mapping = commit.commit(self._helper, job)
        except Exception, e_value:
            self._helper.getrMakeClient().commitFailed([jobId], str(e_value))
            raise
        else:
            self._helper.getrMakeClient().commitSucceeded(mapping)
            log.info('Commit of job %d completed in %.02f seconds',
                jobId, time.time() - startTime)

    def getTestSuite(self):
        '''
        Retrieve testsuite data compiled after a batch is run.

        @rtype: L{TestSuite<bob.test.TestSuite>}
        '''
        return self._testSuite

    def getCoverageData(self):
        '''
        Retrieve coverage data compiled after a batch is run.

        @rtype: C{dict([(filename,
                (set([statements]), set([missing])))])}
        '''
        return self._coverageData
