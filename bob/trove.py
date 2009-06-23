#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Internal representation of a build trove
'''

from conary.deps.deps import Flavor

from bob.config import BobTargetSection
from bob.errors import TroveNotShadowError


class BobPackage(object):
    '''
    A C{BobPackage} is an object representing one source to be built
    in any number of flavors, and all attributes needed to build the
    source. These attributes include the original source version, the
    modified shadow version, trove-specific configuration, child
    packages for group sources, and cached trove objects.
    '''

    def __init__(self, name, upstreamVersion, targetConfig=None):
        assert name.endswith(':source')

        if targetConfig is None:
            targetConfig = BobTargetSection(None)

        self._name = name
        self._targetConfig = targetConfig
        self._upstreamVersion = upstreamVersion

        self._children = set()
        self._downstreamTrove = None
        self._downstreamVersion = None
        self._flavors = set()
        self._mangleData = None
        self._trove = None

        if self.isSiblingClone() \
          and not self._upstreamVersion.hasParentVersion():
            raise TroveNotShadowError(name=self.getName(),
                version=self.getUpstreamVersion())

        self._children.update(targetConfig.after)

    def __hash__(self):
        return hash((self._name, self._upstreamVersion))

    def __repr__(self):
        return 'BobPackage(%s=%s)' % (self._name, self._upstreamVersion)

    # Name
    def getName(self):
        '''
        Get the source name of the package; e.g. foobar:source
        '''
        return self._name

    def getPackageName(self):
        '''
        Get the package's base name; e.g. foobar
        '''
        return self._name.split(':')[0]

    def getRecipeName(self):
        '''
        Get the name of the package's recipe; e.g. foobar.recipe
        '''
        return self.getPackageName() + '.recipe'

    # Upstream version
    def getUpstreamVersion(self):
        '''
        Get the upstream source version that is selected at the start
        of the build.
        '''
        return self._upstreamVersion

    def getUpstreamNameVersion(self):
        '''
        Return a tuple similar to
        C{x.getName(), x.getUpstreamVersion()}.
        '''
        return self._name, self._upstreamVersion

    def getUpstreamNameVersionFlavor(self):
        '''
        Return a tuple similar to
        C{x.getName(), x.getUpstreamVersion(), deps.Flavor()}.

        Note that the flavor is empty as this is referring only to the
        source trove, not to any built object.
        '''
        return self._name, self._upstreamVersion, Flavor()

    # Downstream version
    def hasDownstreamVersion(self):
        '''
        Return C{True} if a downstream (shadowed or cloned) version
        has been set.
        '''
        return self._downstreamVersion is not None

    def setDownstreamVersion(self, version):
        '''
        Set the downstream (shadowed or cloned) version of this
        package. The version must not already be set or an
        assertion will be raised.

        @param version: The downstream version
        @type  version: L{Version<conary.versions.Version>}
        '''
        assert not self._downstreamVersion
        self._downstreamVersion = version

    def getDownstreamVersion(self):
        '''
        Get the downstream (shadowed or cloned) version of this
        package. The downstream version must have been previously set,
        or C{ValueError} will be raised.
        '''
        if not self._downstreamVersion:
            raise ValueError('Downstream version not yet allocated')
        return self._downstreamVersion

    def getDownstreamNameVersion(self):
        '''
        Return a tuple similar to
        C{x.getName(), x.getDownstreamNameVersion()}.
        '''
        if not self._downstreamVersion:
            raise ValueError('Downstream version not yet allocated')
        return self._name, self._downstreamVersion

    def getDownstreamNameVersionFlavor(self):
        '''
        Return a tuple similar to
        C{x.getName(), x.getDownstreamVersion(), deps.Flavor()}.

        Note that the flavor is empty as this is referring only to the
        source trove, not to any built object.
        '''
        if not self._downstreamVersion:
            raise ValueError('Downstream version not yet allocated')
        return self._name, self._downstreamVersion, Flavor()

    # Flavors
    def getFlavors(self):
        '''
        Get the set of flavors that this package will be built with.

        @rtype: C{set}
        '''
        return self._flavors

    def addFlavors(self, flavors):
        '''
        Add a set of flavors for this package to be built with.

        @param flavors: A set of new flavors to add to the build list
        @type  flavors: iterable
        '''
        self._flavors.update(flavors)

    def setFlavors(self, flavors):
        '''
        Replace the set of flavors for this package to be built with.

        @param flavors: A set of new flavors to replace the build list
        @type  flavors: iterable
        '''
        self._flavors = set(flavors)

    # Target configuration
    def getTargetConfig(self):
        '''
        Get the configuration section specific to this package, or
        an empty configuration section if none was provided.
        '''
        return self._targetConfig

    def isSiblingClone(self):
        '''
        Return C{True} if this package is configured to be sibling
        cloned instead of shadowed, or C{False} if it will be shadowed
        (the default).

        @rtype: bool
        '''
        return self._targetConfig and self._targetConfig.siblingClone

    def getBaseVersion(self):
        '''
        Get the "upstream version" template that will be substituted
        into the mangled recipe, or the original version if no
        substitution is to be done.
        '''
        if self._targetConfig.version:
            return self._targetConfig.version
        else:
            return self._upstreamVersion.trailingRevision().getVersion()

    # Children
    def getChildren(self):
        '''
        Return the set of child source names for this package. This
        will only be non-empty if the package is a group and there are
        packages in the group that will also be built.

        Child packages are used mostly for dependency ordering; e.g.
        all packages will be built in one batch, then groups containing
        only those packages are built, then groups containing the first
        set of groups, etc.

        @return: source names that are children of this package
        @rtype : set
        '''
        return self._children

    def addChild(self, child):
        '''
        Add a new child source name to this package.

        @param child: The name of a source which will be built and
                      added to the current package.
        @type  child: str
        '''
        self._children.add(child)

    # Repository
    def getDownstreamSourceTrove(self, helper):
        '''
        Fetch a L{Trove<conary.trove.Trove>} of the downstream
        (shadowed or cloned) source, cache it, and return it. The
        cached object can be cleared with the
        C{deleteDownstreamSourceTrove} method.

        @param helper: The helper to use to fetch the trove if needed.
        @type  helper: L{ClientHelper<bob.util.ClientHelper>}
        @rtype: L{Trove<conary.trove.Trove>}
        '''
        if not self._trove:
            self._downstreamTrove = helper.getRepos().getTrove(
                *self.getDownstreamNameVersionFlavor())
        return self._downstreamTrove

    def deleteDownstreamSourceTrove(self):
        '''
        Delete a previously cached downstream source trove from
        C{getDownstreamSourceTrove}.
        '''
        self._downstreamTrove = None

    # Mangling
    def setMangleData(self, data):
        '''
        Set the mangling data used to mangle this package's recipe.

        @param data: A dictionary of data used by the
                     L{mangle<bob.mangle>} module to alter the package's
                     recipe.
        @type  data: dict
        '''
        self._mangleData = data

    def getMangleData(self):
        '''
        Get the mangling data previously set by C{setMangleData}.
        Raises C{ValueError} if none was set.
        '''
        if not self._mangleData:
            raise ValueError('Mangle data not set')
        return self._mangleData
