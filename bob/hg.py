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
                log.debug('Selected for %s revision %s (from tips)',
                    uri, _tip)
                return _tip[:12]
    except IOError:
        log.warning('Tips file does not exist while fetching repository %s',
            uri)

        hg_ui = ui.ui()
        repo = hg.repository(hg_ui, uri)
        tip = short(repo.heads()[0])
        log.debug('Selected for %s revision %s (from repo)', uri, tip)
        return tip
    else:
        raise RuntimeError('tips file exists, but does not contain '
            'repository %s' % uri)
