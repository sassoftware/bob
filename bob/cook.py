#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Build, commit, and process troves in "batches"
'''

import logging
import os
import sys
import time

from conary.build.macros import Macros
from rmake.cmdline import monitor

from bob import commit
from bob import flavors
from bob import test
from bob.errors import JobFailedError, TestFailureError
from bob.util import ContextCache, StatusOnlyDisplay
from bob.util import partial, pushStopHandler, popStopHandler


log = logging.getLogger('bob.cook')


def stopJob(batch, signum, _):
    '''
    Signal handler used during a cook job that will stop the job
    and exit.
    '''

    log.error('Caught signal %d during build; stopping job', signum)
    batch.stop()
    sys.exit('Signalled stop')


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
        self._commit = None

        # job state
        self._jobId = None

        # results
        self._testSuite = None
        self._coverageData = None

    def isEmpty(self):
        """
        Returns C{True} if there are no troves in this job.
        """
        return not self._troves

    def addTrove(self, bobTrove):
        '''
        Add a I{BobPackage} to the batch.

        @type bobTrove: L{bob.trove.BobPackage}
        '''

        _macros = Macros(self._helper.plan.macros)
        macros = {}
        config = bobTrove.getTargetConfig()

        doCommit = not config.noCommit
        if self._commit is None:
            self._commit = doCommit
        elif self._commit != doCommit:
            senseA = doCommit and "wants" or "does not want"
            senseB = doCommit and "do not" or "do"
            log.error("Package %s %s to commit, but existing packages "
                "in batch %s", bobTrove.getPackageName(), senseA, senseB)
            log.error("Either all packages in a batch must commit, or none can")
            raise RuntimeError("Can't commit part of a batch")

        if config:
            for key, value in config.macros.iteritems():
                if key not in self._helper.plan.skipMacros:
                    macros[key] = value % _macros

        # Reduce the set of flavors to build
        oldFlavors = bobTrove.getFlavors()
        newFlavors = flavors.reduce_flavors(bobTrove.getPackageName(),
            config, oldFlavors)

        if len(newFlavors) != len(oldFlavors):
            log.debug('Package %s would be built in %d flavors; '
                'now built in %d flavors', bobTrove.getPackageName(),
                len(oldFlavors), len(newFlavors))
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
            buildConfig=self._helper.cfg, rebuild=True)
        jobId = self._helper.getrMakeClient().buildJob(job)
        log.info('Job %d started', jobId)

        # Set a signal handler so we can stop the job if we get
        # interrupted
        self._jobId = jobId
        pushStopHandler(partial(stopJob, self))

        # Watch build (to stdout)
        self._helper.callClientHook('client_preCommand', main,
            None, (self._helper.cfg, self._helper.cfg),
            None, None)
        self._helper.callClientHook('client_preCommand2', main,
            self._helper.getrMakeHelper(), None)
        monitor.monitorJob(self._helper.getrMakeClient(), jobId,
            exitOnFinish=True, displayClass=StatusOnlyDisplay,
            showBuildLogs=self._helper.plan.showBuildLogs)

        # Remove the signal handler now that the job is done
        self._jobId = None
        popStopHandler()

        # Pull out logs
        job = self._helper.getrMakeClient().getJob(jobId)
        self.writeLogs(job)

        # Check for error condition
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

        # Bail out without committing if tests failed ...
        if not self._testSuite.isSuccessful():
            log.error('Some tests failed, aborting')
            raise TestFailureError()

        # ... or if all packages are set not to commit
        if self._commit is False:
            return {}

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
        return mapping

    def stop(self):
        '''
        Stop the currently running build.
        '''
        if self._jobId:
            self._helper.getrMakeHelper().stopJob(self._jobId)

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

    def writeLogs(self, job):
        """
        Write build logs for job C{job} to the output directory.
        """
        jobDir = os.path.join('output', 'logs', str(job.jobId))
        client = self._helper.getrMakeClient()
        for trv in job.iterTroves():
            troveDir = os.path.join(jobDir, '%s{%s}'
                % (trv.getName(), trv.getContext()))
            if not os.path.isdir(troveDir):
                os.makedirs(troveDir)

            troveLog = open(os.path.join(troveDir, 'trove.log'), 'w')
            mark = 0
            while True:
                logs = client.getTroveLogs(job.jobId,
                    trv.getNameVersionFlavor(True), mark)
                if not logs:
                    break
                mark += len(logs)

                for timeStamp, message, _ in logs:
                    troveLog.write('[%s] %s\n' % (timeStamp, message))
            troveLog.close()

            buildLog = open(os.path.join(troveDir, 'build.log'), 'w')
            mark = 0
            while True:
                _, logs, mark = client.getTroveBuildLog(job.jobId,
                    trv.getNameVersionFlavor(True), mark)
                if not logs:
                    break
                mark += len(logs)
                buildLog.write(logs)
            buildLog.close()
