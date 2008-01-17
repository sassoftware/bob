#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

import os.path

from conary import conarycfg, conaryclient

from bob import config
from bob import mercurial

class CookBob(object):
    def __init__(self):
        self.cfg = config.BobConfig()
        self.macros = {}
        self.targets = {}
        self.tests = {}
        
        # repositories
        self.conary = {}
        self.hg = {}

        # conary client
        self.conarycfg = conarycfg.ConaryConfiguration(True)
        self.cc = conaryclient.ConaryClient()
        self.nc = self.cc.getRepos()

    def readPlan(self, plan):
        if plan.startsWith('http://') or plan.startswith('https://'):
            self.cfg.readUrl(plan)
        else:
            self.cfg.read(plan)

        for name, section in self.cfg._sections:
            sectype, name = name.split(':', 1)
            if sectype == 'target':
                self.targets[name] = CookTarget(name, section)
            elif sectype == 'test':
                self.tests[name] = CookTest(name, section)
            else:
                assert False

    def loadConary(self):
        '''Pick versions of troves to shadow and build against'''

    def loadMercurial(self):
        '''Pick versions of mercurial repositories to build against'''
        for name, repos in self.cfg.hg.iteritems():
            node = mercurial.getNode(repos)
            self.hg[name] = node

    def run(self):
        self.loadConary()
        self.loadMercurial()
