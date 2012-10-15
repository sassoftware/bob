#
# Copyright (c) rPath, Inc.
#

'''
Utility functions
'''

import logging
import subprocess
import signal
import time

from conary import conaryclient
from rmake.cmdline import helper
from rmake.cmdline import monitor
from conary.lib.digestlib import md5

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
                buildConfig=self.cfg)
        return self._rmakeHelper

    def getrMakeClient(self):
        '''Get a rMakeClient'''
        return self.getrMakeHelper().client

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


class SCMRepository(Container):
    """
    Pointer to a SCM repository (e.g. Hg) by host and path.
    """
    __slots__ = ['kind', 'host', 'path', 'revision', 'uri']

    @classmethod
    def fromString(cls, val):
        if '::' not in val:
            raise ValueError("Invalid repository %r" % (val,))
        kind, location = val.split('::', 1)
        host, path = location.strip().split(':', 1)
        return cls(kind=kind.strip(), host=host, path=path)

    def asString(self):
        if None in (self.kind, self.host, self.path):
            raise ValueError("Cannot stringify incomplete repository handle")
        return '%s::%s:%s' % (self.kind, self.host, self.path)
