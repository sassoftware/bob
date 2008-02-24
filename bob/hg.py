#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Helper functions for dealing with mercurial (hg) repositories.
'''

import logging
import os

from mercurial import hg, ui
from mercurial.node import short

log = logging.getLogger('bob.hg')


def get_tip(uri):
    '''
    Fetch the tip of a given repository URI, using the tips file if present.
    '''

    try:
        for line in open('tips'):
            _uri, _tip = line.split(' ', 1)
            if _uri == uri:
                return _tip[:12]
    except IOError:
        log.warning('Tips file does not exist while fetching repository %s',
            uri)

        hg_ui = ui.ui()
        repo = hg.repository(hg_ui, uri)
        return short(repo.heads()[0])
    else:
        raise RuntimeError('tips file exists, but does not contain '
            'repository %s' % uri)
