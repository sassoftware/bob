#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Internal representation of a build trove
'''

from conary.deps.deps import Flavor

from bob.errors import TroveNotShadowError


class BobPackage(object):
    '''
    Object representing a set of flavors of a particular
    trove to be built. This will be further exploded into individual
    (name, version, flavor, context) tuples.

    Contains:
    * source name
    * source version (upstream version)
    * source version (shadow on target label)
    * build flavors
    * target configuration
    * child package names

    Yields:
    * name, version, flavor, context tuples via Batch.addTrove()
    '''

    def __init__(self, name, upstreamVersion, targetConfig=None):
        assert name.endswith(':source')

        self._name = name
        self._targetConfig = targetConfig
        self._upstreamVersion = upstreamVersion

        self._children = set()
        self._downstreamVersion = None
        self._flavors = set()
        self._mangleData = None
        self._trove = None

        if self.isSiblingClone() \
          and not self._upstreamVersion.hasParentVersion():
            raise TroveNotShadowError(name=self.getName(),
                version=self.getUpstreamVersion())

    def __hash__(self):
        return hash((self._name, self._upstreamVersion))

    def __repr__(self):
        return 'BobPackage(%s=%s)' % (self._name, self._upstreamVersion)

    # Name
    def getName(self):
        return self._name

    def getPackageName(self):
        return self._name.split(':')[0]

    def getRecipeName(self):
        return self.getPackageName() + '.recipe'

    # Upstream version
    def getUpstreamVersion(self):
        return self._upstreamVersion

    def getUpstreamNameVersion(self):
        return self._name, self._upstreamVersion

    def getUpstreamNameVersionFlavor(self):
        return self._name, self._upstreamVersion, Flavor()

    # Downstream version
    def hasDownstreamVersion(self):
        return self._downstreamVersion is not None

    def setDownstreamVersion(self, version):
        assert not self._downstreamVersion
        self._downstreamVersion = version

    def getDownstreamVersion(self):
        if not self._downstreamVersion:
            raise ValueError('Downstream version not yet allocated')
        return self._downstreamVersion

    def getDownstreamNameVersion(self):
        if not self._downstreamVersion:
            raise ValueError('Downstream version not yet allocated')
        return self._name, self._downstreamVersion

    def getDownstreamNameVersionFlavor(self):
        if not self._downstreamVersion:
            raise ValueError('Downstream version not yet allocated')
        return self._name, self._downstreamVersion, Flavor()

    # Flavors
    def getFlavors(self):
        return self._flavors

    def addFlavors(self, flavors):
        self._flavors.update(flavors)

    # Target configuration
    def getTargetConfig(self):
        return self._targetConfig

    def isSiblingClone(self):
        return self._targetConfig and self._targetConfig.siblingClone

    # Children
    def getChildren(self):
        return self._children

    def addChild(self, child):
        self._children.add(child)

    # Repository
    def getDownstreamSourceTrove(self, helper):
        if not self._trove:
            self._downstreamTrove = helper.getRepos().getTrove(
                *self.getDownstreamNameVersionFlavor())
        return self._downstreamTrove

    def deleteDownstreamSourceTrove(self):
        self._downstreamTrove = None

    # Mangling
    def setMangleData(self, data):
        self._mangleData = data

    def getMangleData(self):
        if not self._mangleData:
            raise ValueError('Mangle data not set')
        return self._mangleData
