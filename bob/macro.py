#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import logging

def expand(raw, parent, trove=None):
    m = {}

    # Basic info
    m.update(parent.cfg.macro)
    for x in 'tag',:
        m[x] = getattr(parent.cfg, x)

    # Additional info available in trove contexts
    if trove:
        if trove in parent.targets:
            hg = parent.targets[trove].hg
            if hg and parent.hg.has_key(hg):
                m['hg'] = parent.hg[hg][1]
            elif hg:
                logging.warning('Trove %s references undefined Hg '
                    'repository %s', trove, hg)

    return raw % m
