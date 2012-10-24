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
Internal representation of a build trove
'''

from conary.deps.deps import Flavor

from bob.config import BobTargetSection


class BobPackage(object):
    '''
    A C{BobPackage} is an object representing one source to be built
    in any number of flavors, and all attributes needed to build the
    source. These attributes include the original source version, the
    modified shadow version, trove-specific configuration, child
    packages for group sources, and cached trove objects.
    '''

    def __init__(self, name, targetConfig, recipeFiles):
        assert name.endswith(':source')

        if targetConfig is None:
            targetConfig = BobTargetSection(None)

        self.name = name
        self.targetConfig = targetConfig
        self.recipeFiles = recipeFiles

        self.children = set()
        self.downstreamTrove = None
        self.downstreamVersion = None
        self.flavors = set()
        self.mangleData = None
        self.trove = None

        # The 'after' target option acts as a list of additional children to
        # block on when splitting batches.
        for after in targetConfig.after:
            if ':' not in after:
                after += ':source'
            self.children.add(after)

    def __hash__(self):
        return hash(self.name)

    def __repr__(self):
        return 'BobPackage(%r)' % (self.name,)

    # Name
    def getName(self):
        '''
        Get the source name of the package; e.g. foobar:source
        '''
        return self.name

    def getPackageName(self):
        '''
        Get the package's base name; e.g. foobar
        '''
        return self.name.split(':')[0]

    def getRecipeName(self):
        '''
        Get the name of the package's recipe; e.g. foobar.recipe
        '''
        return self.getPackageName() + '.recipe'

    # Upstream contents
    def getRecipe(self):
        try:
            return self.recipeFiles[self.getRecipeName()]
        except KeyError:
            raise RuntimeError("Trove %s recipe is missing!" % (self.name,))

    # Downstream version
    def hasDownstreamVersion(self):
        '''
        Return C{True} if a downstream (shadowed or cloned) version
        has been set.
        '''
        return self.downstreamVersion is not None

    def setDownstreamVersion(self, version):
        '''
        Set the downstream (shadowed or cloned) version of this
        package. The version must not already be set or an
        assertion will be raised.

        @param version: The downstream version
        @type  version: L{Version<conary.versions.Version>}
        '''
        assert not self.downstreamVersion
        self.downstreamVersion = version

    def getDownstreamVersion(self):
        '''
        Get the downstream (shadowed or cloned) version of this
        package. The downstream version must have been previously set,
        or C{ValueError} will be raised.
        '''
        if not self.downstreamVersion:
            raise ValueError('Downstream version not yet allocated')
        return self.downstreamVersion

    def getDownstreamNameVersion(self):
        '''
        Return a tuple similar to
        C{x.getName(), x.getDownstreamNameVersion()}.
        '''
        if not self.downstreamVersion:
            raise ValueError('Downstream version not yet allocated')
        return self.name, self.downstreamVersion

    def getDownstreamNameVersionFlavor(self):
        '''
        Return a tuple similar to
        C{x.getName(), x.getDownstreamVersion(), deps.Flavor()}.

        Note that the flavor is empty as this is referring only to the
        source trove, not to any built object.
        '''
        if not self.downstreamVersion:
            raise ValueError('Downstream version not yet allocated')
        return self.name, self.downstreamVersion, Flavor()

    # Flavors
    def getFlavors(self):
        '''
        Get the set of flavors that this package will be built with.

        @rtype: C{set}
        '''
        return self.flavors

    def addFlavors(self, flavors):
        '''
        Add a set of flavors for this package to be built with.

        @param flavors: A set of new flavors to add to the build list
        @type  flavors: iterable
        '''
        self.flavors.update(flavors)

    def setFlavors(self, flavors):
        '''
        Replace the set of flavors for this package to be built with.

        @param flavors: A set of new flavors to replace the build list
        @type  flavors: iterable
        '''
        self.flavors = set(flavors)

    # Target configuration
    def getTargetConfig(self):
        '''
        Get the configuration section specific to this package, or
        an empty configuration section if none was provided.
        '''
        return self.targetConfig

    def getBaseVersion(self):
        '''
        Get the "upstream version" template that will be substituted
        into the mangled recipe, or the original version if no
        substitution is to be done.
        '''
        return self.targetConfig.version

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
        return self.children

    def addChild(self, child):
        '''
        Add a new child source name to this package.

        @param child: The name of a source which will be built and
                      added to the current package.
        @type  child: str
        '''
        self.children.add(child)

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
        if not self.trove:
            self.downstreamTrove = helper.getRepos().getTrove(
                *self.getDownstreamNameVersionFlavor())
        return self.downstreamTrove

    def deleteDownstreamSourceTrove(self):
        '''
        Delete a previously cached downstream source trove from
        C{getDownstreamSourceTrove}.
        '''
        self.downstreamTrove = None

    # Mangling
    def setMangleData(self, data):
        '''
        Set the mangling data used to mangle this package's recipe.

        @param data: A dictionary of data used by the
                     L{mangle<bob.mangle>} module to alter the package's
                     recipe.
        @type  data: dict
        '''
        self.mangleData = data

    def getMangleData(self):
        '''
        Get the mangling data previously set by C{setMangleData}.
        Raises C{ValueError} if none was set.
        '''
        if not self.mangleData:
            raise ValueError('Mangle data not set')
        return self.mangleData
