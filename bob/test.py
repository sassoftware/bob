#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Module containing test-processing code.
'''

import cPickle
import logging
import re
import sys
import xml.dom.minidom

from bob.util import hashabledict

log = logging.getLogger('bob.test')


# Match paths from :testinfo components
re_config_output = re.compile('^/usr/share/testinfo/[^/]+/configuration.txt$')
re_test_output = re.compile('^/usr/share/testinfo/[^/]+/tests/.*$')
re_cover_output = re.compile('^/usr/share/testinfo/[^/]+/coverage/.*$')


# Possible test statuses, in order of increasing severity
TEST_NONE   = -1
TEST_OK     = 0
TEST_FAIL   = 1
TEST_ERROR  = 2


class TestCase(object):
    def __init__(self, name):
        self.name = name
        self.status = TEST_NONE
        self.runs = {}

    def add_run(self, status, duration, configuration, message):
        '''Add a run to the test case.'''
        run = dict(status=status, duration=duration, message=message)
        if configuration in self.runs:
            log.warning('Test %s already has an entry for conf %r; '
                'overwriting', self.name, configuration)
        self.runs[configuration] = run
        self.status = max(self.status, status)

    def get_failing_runs(self):
        '''Return failing runs from this test case.'''
        return dict((cfg, run) for (cfg, run) in self.runs.iteritems() \
            if run['status'] > TEST_OK)

    def max_runtime(self):
        '''Return the maximum test duration across all runs.'''
        return max(x['duration'] for x in self.runs.values())

    def failing_configurations(self):
        '''Find common factors in failed runs.'''

        # Check the obvious case - all runs failed
        if len(self.runs) == len(self.get_failing_runs()):
            return 'Failed in all configurations'

        # For each factor, accumulate passing and failing values
        factors = {}
        for configuration, run in self.runs.iteritems():
            passed = run['status'] <= TEST_OK
            for key, value in configuration.iteritems():
                factor = factors.setdefault(key, (set(), set()))
                if passed:
                    factor[0].add(value)
                else:
                    factor[1].add(value)

        # Go through each factor and determine which values produce consistent
        # failures
        failing_factors = []
        for factor, (passing, failing) in factors.iteritems():
            # If any run with value X passed, value X is not at fault
            failing -= passing

            for value in failing:
                failing_factors.append((factor, value))

        if failing_factors:
            return 'Failed in these configurations: ' + \
                ', '.join('%s=%s' % (key, value) for (key, value) \
                    in failing_factors)
        else:
            return 'Failed in various configurations'

    def exception_report(self):
        '''
        Interpret exception reports output by the conary testsuite's junit
        formatter and produce a single report summarizing all failures in a
        particular test case.
        '''

        failed = self.get_failing_runs()
        last_lines = failed.values()[0]['message'].splitlines()

        failing_configurations = self.failing_configurations()

        output = '\n'.join(last_lines[-3:]) + '\n'
        output += self.failing_configurations() + '\n'

        for configuration, run in failed.iteritems():
            output += '\n'
            output += '+++ ' + ', '.join('%s=%s' % (key, value) \
                for (key, value) in configuration.iteritems()) + '\n'
            output += run['message']

        return output

    def write_junit(self, fileobj):
        '''Write an individual test in JUnit-style XML format.'''

        classname, name = self.name.rsplit('.', 1)
        duration = self.max_runtime()

        if self.status == TEST_OK:
            print >>fileobj, \
                '<testcase classname="%s" name="%s" time="%0.03f" />' \
                % (classname, name, duration)
        elif self.status in (TEST_FAIL, TEST_ERROR):
            tag_names = {TEST_FAIL: 'failure', TEST_ERROR: 'error'}
            message = self.exception_report()
            print >>fileobj, \
                '<testcase classname="%s" name="%s" time="%0.03f">' \
                % (classname, name, duration)
            print >>fileobj, \
                '<%s type="Exception" message="">' % tag_names[self.status]
            print >>fileobj, '<![CDATA[' + message + ']]>'
            print >>fileobj, '</%s>' % tag_names[self.status]
            print >>fileobj, '</testcase>'

class TestSuite(object):
    def __init__(self):
        self.tests = {}
        self.status = TEST_NONE

    def add_test(self, name, status, duration, configuration, message):
        '''
        Add a record of a particular test case.
        '''
        if name in self.tests:
            test = self.tests[name]
        else:
            test = self.tests[name] = TestCase(name)
        test.add_run(status, duration, configuration, message)
        self.status = max(self.status, status)

    def load_junit(self, fileobj, configuration):
        '''
        Load test data from a JUnit-style XML file.
        '''
        document = xml.dom.minidom.parse(fileobj)
        for test in document.childNodes[0].childNodes:
            if not isinstance(test, xml.dom.minidom.Element):
                continue
            assert test.nodeName == 'testcase'

            attrs = test.attributes
            name = attrs['classname'].value + '.' + attrs['name'].value
            duration = float(attrs['time'].value)

            # Get the actual result status
            message = None
            if test.getElementsByTagName('error'):
                status = TEST_ERROR
                message = test.childNodes[1].childNodes[1].data
            elif test.getElementsByTagName('failure'):
                status = TEST_FAILURE
                message = test.childNodes[1].childNodes[1].data
            else:
                status = TEST_OK

            self.add_test(name, status, duration, configuration, message)

    def write_junit(self, fileobj):
        '''
        Write test data in JUnit-style XML format.
        '''

        print >>fileobj, '<testsuite>'
        for name in sorted(self.tests.keys()):
            self.tests[name].write_junit(fileobj)
        print >>fileobj, '</testsuite>'

    def isSuccessful(self):
        return self.status <= TEST_OK


def processTests(parent_bob, job):
    '''
    For each built trove configured to extract tests, process those tests
    into JUnit output and return a pass/fail status.

    @returns: True if all tests pass, False otherwise.
    '''

    test_suite = TestSuite()
    cover_data = {}

    for build_trove in job.iterTroves():
        for name, version, flavor in build_trove.iterBuiltTroves():
            if not name.endswith(':testinfo'):
                continue

            configuration = None
            test_fobjs = []
            cover_fobjs = []

            cs_job = [(name, (None, None), (version, flavor), True)]
            changeset = parent_bob.cc.createChangeSet(cs_job, withFiles=True,
                withFileContents=True)

            def getFile(pathId, fileId):
                cont_item = changeset.getFileContents(pathId, fileId)[1]
                cont_file = cont_item.get()
                changeset.reset()
                return cont_file

            for trove_cs in changeset.iterNewTroveList():
                for pathId, path, fileId, fileVer in trove_cs.getNewFileList():
                    if re_config_output.search(path):
                        configuration = getFile(pathId, fileId).read()
                    elif re_test_output.search(path):
                        test_fobjs.append(getFile(pathId, fileId))
                    elif re_cover_output.search(path):
                        cover_fobjs.append(getFile(pathId, fileId))
            processTroveTests(test_suite, cover_data, name, version, flavor,
                configuration, test_fobjs, cover_fobjs)

    test_suite.write_junit(open('test_results.xml', 'w'))

    return test_suite.isSuccessful(), cover_data


def processTroveTests(test_suite, cover_data, name, version, flavor,
  configuration, test_fobjs, cover_fobjs):
    '''
    Process tests for a single built trove.
    '''

    log.debug('Processing tests from %s=%s[%s]', name, version, flavor)

    # XXX need a better parser (or format)
    configuration = hashabledict(eval(configuration))

    # Tests
    for test_fobj in test_fobjs:
        test_suite.load_junit(test_fobj, configuration)

    # Coverage
    for cover_fobj in cover_fobjs:
        add_coverage(cover_data, cover_fobj)

def add_coverage(cover_data, cover_fobj):
    '''
    Add the coverage data from one coverage blob to a "grand total"
    dictionary.
    '''

    this_coverage = cPickle.load(cover_fobj)
    for morf, (statements, missing) in this_coverage.iteritems():
        if not cover_data.has_key(morf):
            cover_data[morf] = [statements, set(missing)]
        else:
            cover_data[morf][1] &= set(missing)


def coverage_report(cover_data, fileobj):
    '''
    Print a coverage report from the given coverage dictionary to the
    given file object.
    '''

    # Print out a report
    max_name = max([5,] + map(len, cover_data.keys()))
    fmt_name = "%%- %ds  " % max_name
    fmt_err = fmt_name + "%s: %s"
    header = fmt_name % "Name" + " Stmts   Exec    Cover"
    fmt_coverage = fmt_name + "% 6d % 6d % 7s%%"

    total_statements = total_executed = 0

    print >>fileobj, header
    for morf in sorted(cover_data.keys()):
        statements, missing = cover_data[morf]

        num_statements = len(statements)
        num_missing = len(missing)
        num_executed = num_statements - num_missing
        if num_statements > 0:
            percent = 100.0 * num_executed / num_statements
        else:
            percent = 100.0
        str_percent = '%-4.2f' % percent

        print >>fileobj, fmt_coverage % (morf, num_statements,
            num_executed, str_percent)

        total_statements += num_statements
        total_executed += num_executed

    if total_statements > 0:
        total_percent = 100.0 * total_executed / total_statements

        print '-' * len(header)
        print fmt_coverage % ('TOTAL', total_statements, total_executed,
                              '%-4.2f' % total_percent)
