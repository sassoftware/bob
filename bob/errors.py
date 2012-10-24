#
# Copyright (c) rPath, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
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
