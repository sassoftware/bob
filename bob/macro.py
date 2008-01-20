#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import logging

def expand(raw, parent, trove=None):
    m = {}

    # Basic info
    for x in 'tag', 'version':
        m[x] = getattr(parent.cfg, x)

    # Additional info available in trove contexts
    if trove:
        if parent.targets.has_key(trove):
            hg = parent.targets[trove.hg]
            if hg and parent.hg.has_key(hg):
                m['hg'] = parent.hg[parent.targets[trove].hg]
            elif hg:
                logging.warning('Trove %s references undefined Hg '
                    'repository %s', trove, hg)

    return raw % m
