#
# Copyright (c) rPath, Inc.
#

'''
Errors specific to bob
'''

class BobError(RuntimeError):
    'An unknown error has occured'
    _params = []

    def __init__(self, **kwargs):
        RuntimeError.__init__(self)

        self._kwargs = kwargs
        self._template = self.__class__.__doc__

        # Copy kwargs to attributes
        for key in self._params:
            setattr(self, key, kwargs[key])

    def __str__(self):
        return self._template % self.__dict__

    def __repr__(self):
        params = ', '.join('%s=%r' % x for x in self._kwargs.iteritems())
        return '%s(%s)' % (self.__class__, params)


class CommitFailedError(BobError):
    'rMake job %(jobId)d failed to commit: %(why)s'
    _params = ['jobId', 'why']

class DependencyLoopError(BobError):
    'A dependency loop could not be closed.'

class JobFailedError(BobError):
    'rMake job %(jobId)s failed: %(why)s'
    _params = ['jobId', 'why']

class TestFailureError(BobError):
    'A testsuite has failed'
