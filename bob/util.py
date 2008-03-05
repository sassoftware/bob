#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import os
import tempfile

from conary.deps import deps
from conary.lib import util as cny_util


class hashabledict(dict):
    def __hash__(self):
        return hash(tuple(sorted(self.items())))


def fetch_recipe(nc, name, version):
    '''
    Fetch a recipe from a source component and return the path to the recipe.
    '''

    assert name.endswith(':source')
    package = name.split(':')[0]
    recipe_name = package + '.recipe'

    trove = nc.getTrove(name, version, deps.Flavor())

    (fd, recipe_file) = tempfile.mkstemp('.recipe', 'temp-%s-' % package)
    out_f = os.fdopen(fd, 'w')

    for path_id, path, file_id, file_ver in trove.iterFileList():
        if path == recipe_name:
            in_f = nc.getFileContents([(file_id, file_ver)])[0].get()
            break

    if not in_f:
        raise RuntimeError('%s=%s did not contain recipe' % (name, version))

    cny_util.copyfileobj(in_f, out_f)

    del in_f
    out_f.close()
    del out_f

    return recipe_file
