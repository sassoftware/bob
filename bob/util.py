#
# Copyright (c) SAS Institute Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


'''
Utility functions
'''

import errno
import fcntl
import logging
import os
import subprocess
import signal
import tempfile
import time

from conary import conaryclient
from rmake.cmdline import helper
from rmake.cmdline import monitor
from conary.lib import util
from conary.lib.digestlib import md5
from conary.lib.util import statFile


log = logging.getLogger('bob.util')


def checkBZ2(path):
    """
    Validate that C{path} is a valid bzip2 file.
    """
    devnull = open('/dev/null', 'w+')
    proc = subprocess.Popen(['/usr/bin/bzip2', '-t', path], shell=False,
            stdin=devnull, stdout=devnull, stderr=devnull)
    proc.communicate()
    return proc.returncode == 0


class ClientHelper(object):
    '''
    Agent containing the current build configuration which can
    return conary client, repository client, rmake client,
    and rmake helper on request.
    '''

    def __init__(self, cfg, plan, pluginMgr):
        self.cfg = cfg
        self.plan = plan
        self.pluginMgr = pluginMgr

        self._conaryClient = None
        self._rmakeClient = None
        self._rmakeHelper = None
        self.ephemeralDir = None

    def configChanged(self):
        '''
        Mark all the generated stuff as invalid after a configuration
        has changed.
        '''
        log.debug('Helper flushed')

        self._conaryClient = None
        self._rmakeClient = None
        self._rmakeHelper = None

    def getClient(self):
        '''Get a ConaryClient'''
        if not self._conaryClient:
            self._conaryClient = conaryclient.ConaryClient(self.cfg)
        return self._conaryClient

    def getRepos(self):
        '''Get a NetworkRepositoryClient'''
        return self.getClient().getRepos()

    def getrMakeHelper(self):
        '''Get a rMakeHelper'''
        if not self._rmakeHelper:
            self._rmakeHelper = helper.rMakeHelper(
                buildConfig=self.cfg, promptPassword=True)
        return self._rmakeHelper

    def getrMakeClient(self):
        '''Get a rMakeClient'''
        return self.getrMakeHelper().client

    def makeEphemeralDir(self):
        if not self.ephemeralDir:
            self.ephemeralDir = tempfile.mkdtemp(
                    dir=self.plan.ephemeralSourceDir)
            os.chmod(self.ephemeralDir, 0755)
            ssd = '/sources/' + os.path.basename(self.ephemeralDir)
            self.cfg.sourceSearchDir = ssd
            for section in self.cfg._sections.values():
                section.sourceSearchDir = ssd
        return self.ephemeralDir

    def cleanupEphemeralDir(self):
        if not self.ephemeralDir:
            return
        if os.path.isdir(self.ephemeralDir):
            util.rmtree(self.ephemeralDir)
        self.ephemeralDir = None

    # Passthroughs
    def callClientHook(self, *args):
        '''Call plugin hooks'''
        self.pluginMgr.callClientHook(*args)

    def createChangeSet(self, items):
        '''Create a changeset with files but no contents'''
        return self.getRepos().createChangeSet(items,
            withFileContents=False, recurse=False)


class Container(object):
    '''
    A superclass to plain old container objects. Either subclass this
    and define your desired attributes in C{__slots__}, or use
    C{makeContainer} to generate one automatically.
    '''
    __slots__ = []

    def __init__(self, **kwargs):
        for name in self.__class__.__slots__:
            setattr(self, name, kwargs.pop(name, None))
        assert not kwargs


class ContextCache(object):
    '''
    Cache of made-up contexts for use in a rMake build. Call I{get} to
    add a new context to the build config to get the name of a context
    with those parameters.
    '''

    # R0903 - Too few public methods
    #pylint: disable-msg=R0903

    def __init__(self, config):
        self.contexts = set()
        self.config = config

    def get(self, build_flavor, search_flavors, macros):
        '''
        Create a context in the local buildcfg out of the specified build
        and search flavors, and macros.
        '''

        # Calculate a unique context name based on the specified settings
        ctx = md5()
        ctx.update(build_flavor.freeze())
        for search_flavor in search_flavors:
            ctx.update(search_flavor.freeze())
        for key in sorted(macros.keys()):
            ctx.update(key + macros[key])
        name = ctx.hexdigest()[:12]

        # Add a context if necessary and return the context name.
        if name not in self.contexts:
            context = self.config.setSection(name)
            context['buildFlavor'] = build_flavor
            context['flavor'] = search_flavors
            context['macros'] = macros
            self.contexts.add(name)

        return name


class HashableDict(dict):
    '''
    Dict with a hash method so it can used as a dictionary key.
    '''

    def __hash__(self):
        return hash(tuple(sorted(self.items())))


class StatusOnlyDisplay(monitor.JobLogDisplay):
    '''
    Display only job and trove status. No log output.
    '''

    # R0901 - Too many ancestors
    #pylint: disable-msg=R0901

    def _troveLogUpdated(self, (jobId, troveTuple), state, status):
        '''Don't care about trove logs'''
        pass

    def _trovePreparingChroot(self, (jobId, troveTuple), host, path):
        '''Don't care about resolving/installing chroot'''
        pass


def timeIt(func):
    '''
    A decorator that times how long a function takes to execute, and
    logs the result as a debug message on completion.
    '''

    def wrapper(*args, **kwargs):
        '''inner function'''
        start = time.time()
        returnValue = func(*args, **kwargs)
        stop = time.time()

        log.debug('Call %s.%s : %.03f',
            func.__module__,
            func.__name__,
            stop - start)

        return returnValue

    wrapper.__module__ = func.__module__
    wrapper.__name__ = func.__name__ # stupid -- pylint: disable-msg=W0621
    wrapper.__wrapped_func__ = func
    return wrapper


def makeContainer(name, slots):
    '''
    Create a new subclass of C{Container} from a C{name} and set of
    C{slots}.
    '''
    return type(name, (Container,), {'__slots__': slots})


def partial(func, *args, **kwargs):
    '''
    Return a new function that when called will call C{func} with
    arguments C{args} and C{kwargs} pre-applied. Positional arguments
    given to the new function will be appended to C{args}, and keyword
    arguments will update and replace those in C{kwargs}.
    '''
    def wrapper(*realargs, **realkwargs):
        '''inner function'''
        newkwargs = kwargs.copy()
        newkwargs.update(realkwargs)
        return func(*(args + realargs), **newkwargs)
    wrapper.__wrapped_func__ = func
    wrapper.args = args
    wrapper.kwargs = kwargs
    return wrapper

_STOP_SIGNALS = [signal.SIGINT, signal.SIGQUIT, signal.SIGTERM]
_STOP_HANDLERS = [signal.SIG_DFL]
def pushStopHandler(handler):
    '''
    Push a signal handler onto the stack and set that handler for
    all "stop" signals.
    '''
    _STOP_HANDLERS.append(handler)
    for signum in _STOP_SIGNALS:
        signal.signal(signum, handler)
    return handler


def popStopHandler():
    '''
    Pop the current top-most signal handler off the stack and return
    it. Restore the next-top-most handler for all "stop" signals.
    '''
    oldHandler = _STOP_HANDLERS.pop()
    assert _STOP_HANDLERS
    for signum in _STOP_SIGNALS:
        signal.signal(signum, _STOP_HANDLERS[-1])
    return oldHandler


def reportCommitMap(commitMap):
    '''
    Print out a commit map in the form of a listing of sources and
    troves built.
    '''

    print 'Committed:'
    sourceNVMap = {}
    uniqueRevs = set()
    for jobId in sorted(commitMap):
        for sourceTup, builtTups in commitMap[jobId].iteritems():
            sourceNVMap.setdefault(sourceTup[0:2], []).extend(builtTups)
            name = sourceTup[0].split(':')[0]
            rev = builtTups[0][1].trailingRevision().asString()
            uniqueRevs.add((name, rev))

    for sourceNV in sorted(sourceNVMap):
        print '%s=%s' % sourceNV
        builtTups = sorted(sourceNVMap[sourceNV])
        for builtTup in builtTups:
            if ':' in builtTup[0]:
                continue
            print '  %s=%s[%s]' % builtTup

    print
    print 'Revisions built:', ' '.join(('%s=%s' % x for x in sorted(uniqueRevs)))


def insertResolveTroves(cfg, commitMap):
    """
    Insert newly committed packages at the front of the resolveTrove stack.
    """
    packages = set()
    for jobId, sources in commitMap.iteritems():
        for sourceTup, builtTups in sources.iteritems():
            for builtTup in builtTups:
                packages.add(builtTup)
    packages = sorted(packages)
    cfg.resolveTroves.insert(0, [(n, str(v), f) for (n, v, f) in packages])
    cfg.resolveTroveTups.insert(0, packages)


class LockFile(object):
    """
    Protect a code block with an exclusive file lock. Can be used as a context
    manager, or standalone.

    >>> with LockFile(path):
    ...     do_stuff()
    """

    def __init__(self, path, callback=None):
        self.path = path
        self.callback = callback
        self.fobj = None

    def _open(self, wait):
        flags = fcntl.LOCK_EX
        if not wait:
            flags |= fcntl.LOCK_NB
        fobj = open(self.path, 'w')
        try:
            fcntl.lockf(fobj, flags)
            return fobj
        except IOError as err:
            fobj.close()
            if err.args[0] != errno.EAGAIN:
                raise
            assert not wait
            return None
        except:
            fobj.close()
            raise

    def _acquire_once(self, wait):
        fobj = self._open(wait)
        if not fobj:
            return False
        if statFile(fobj, True, True) != statFile(self.path, True, True):
            # The file was unlinked and possibly replaced with a different one,
            # so this lock is useless.
            fobj.close()
            return False
        self.fobj = fobj
        return True

    def acquire(self, wait=True):
        """
        Try to acquire a lock. Returns True if it succeeded, or False if it did
        not.

        @param wait: If True, wait until it is possible to acquire the lock
            before returning. The method will not return False in this mode.
        """
        ok = self._acquire_once(False)
        if ok or not wait:
            return ok
        if self.callback:
            self.callback()
        while True:
            if self._acquire_once(True):
                break
        return True
    __enter__ = acquire

    def release(self, unlink=True, touch=False):
        """
        Release the lock. Does nothing if no lock was previously acquired.

        @param unlink: If True, unlink the lockfile before releasing it.
        @param touch: If True, update the mtime on the lockfile before
            releasing it.
        """
        fobj = self.fobj
        if not fobj:
            return
        if touch:
            fobj.write('\n')
        if unlink:
            os.unlink(self.path)
        self.fobj = None
        fobj.close()

    def __exit__(self, *args):
        self.release()
