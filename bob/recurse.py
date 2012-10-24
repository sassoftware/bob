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
Tools for recursing through a set of targets, and for preparing batches
to build.
'''

import copy
import logging

from bob.cook import Batch
from bob.errors import DependencyLoopError


log = logging.getLogger('bob.recurse')


def getBatchFromPackages(helper, packageList):
    '''
    Given a set of I{BobPackage}s, yield a sequence of I{Batch} objects
    to build and commit.

    Packages are split along group inclusion lines, that is, we do not
    care about build requirements (which rMake will handle), but we do
    care about packages that are included in groups which we will also
    build. The packages get built first (usually all in one batch),
    then any groups that can be built with just those packages, then
    any groups containing those groups just built, etc.

    Additionally, if any packages are marked as serialized, those
    packages will be split into one batch per flavor, and those "extra"
    batches will be yielded after any non-serialized packages in the
    current batch. If multiple packages are to be serialized, one
    batch will contain a single flavor from each of the serialized
    packages, rather than running one batch per flavor per package.

    @param helper: ClientHelper object
    @param packageList: List of I{BobPackage}s to build
    '''

    built = set() # names
    notBuilt = set(packageList) # BobPackages

    while notBuilt:
        # Determine which troves can be built
        thisRound = set()
        unmetThisRound = dict()
        for bobPackage in notBuilt:
            unmetRequires = bobPackage.getChildren() - built
            if unmetRequires:
                # Not all requirements have been built; skip
                unmetThisRound[bobPackage.getName()] = unmetRequires
                continue
            thisRound.add(bobPackage)

        if not thisRound:
            # Nothing can be built!
            log.error('Unmet dependencies:')
            for name, unmet in unmetThisRound.iteritems():
                log.error('  %s: %s', name, ' '.join(unmet))
            raise DependencyLoopError()

        notBuilt -= thisRound
        built |= set(x.getName() for x in thisRound)

        # Create a batch and add all non-serialized packages, saving
        # the serialized ones for later.
        toSerialize = []
        batch = Batch(helper)
        for bobTrove in thisRound:
            if bobTrove.getTargetConfig().serializeFlavors:
                toSerialize.append(bobTrove)
            else:
                batch.addTrove(bobTrove)

        # Immediately yield the set of buildable packages that do not
        # need to be serialized.
        if not batch.isEmpty():
            yield batch

        # Now go back to the serialized ones, splitting them by flavor
        # into individual packages.
        batches = []
        for bobTrove in toSerialize:
            for idx, flavor in enumerate(sorted(bobTrove.getFlavors())):
                newTrove = copy.deepcopy(bobTrove)
                newTrove.setFlavors([flavor])
                newTrove.getTargetConfig().flavor = [str(flavor)]
                if len(batches) == idx:
                    batches.append(Batch(helper))
                batches[idx].addTrove(newTrove)
        for batch in batches:
            yield batch
