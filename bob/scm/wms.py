#
# Copyright (c) SAS Institute Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


import logging
import os
import subprocess
import urllib
import urllib2
from conary.lib.util import copyfileobj

from bob import scm

log = logging.getLogger('bob.scm')


class WmsRepository(scm.ScmRepository):

    def __init__(self, base, path, branch=None):
        self.base = base
        self.path = path
        self.branch = branch

    @property
    def repos(self):
        silo, subpath = self.path.split('/', 1)
        pathq = self._quote(silo) + '/' + self._quote(subpath)
        return self.base + '/api/repos/' + pathq

    @staticmethod
    def _quote(foo):
        return urllib.quote(foo).replace('/', ':')

    def _getTip(self):
        branch = self.branch or 'HEAD'
        f = urllib2.urlopen(self.repos + '/poll/' + self._quote(branch))
        result = f.readlines()
        f.close()
        assert len(result) == 1
        path, branch, tip = result[0].split()
        assert len(tip) == 40
        return branch, tip

    def getTip(self):
        return self._getTip()[1]

    def setFromTip(self):
        branch, tip = self._getTip()
        self.branch = branch
        self.revision = tip
        self.revIsExact = True

    def updateCache(self):
        pass

    def _archive(self, compress=''):
        return urllib.quote(os.path.basename(self.path)
                + '-' + self.getShortRev()
                + '.tar' + compress)

    def checkout(self, workDir, subtree=None):
        archive = self._archive()
        data = urllib.urlencode([('subtree', subtree)]) if subtree else None
        f = urllib2.urlopen(self.repos + '/archive/'
                    + self.revision + '/' + archive, data=data)
        tar = subprocess.Popen(['tar', '-x'], stdin=subprocess.PIPE,
                cwd=workDir)
        while True:
            d = f.read(10000)
            if not d:
                break
            tar.stdin.write(d)
        tar.stdin.close()
        tar.wait()
        if tar.returncode:
            raise RuntimeError("tar exited with status %s" % tar.returncode)
        prefix = archive.rsplit('.', 1)[0]
        return prefix

    def getAction(self, extra=''):
        f = urllib2.urlopen(self.repos + '/show_url')
        url = f.readline().strip()
        f.close()
        return 'addGitSnapshot(%r, branch=%r, tag=%r%s)' % (
                url, self.branch, self.getShortRev(), extra)

    def fetchArchive(self, conarySource, snapPath):
        if os.path.exists(snapPath):
            return
        archive = urllib.quote(os.path.basename(snapPath))
        url = (self.repos + '/archive/'
                + urllib.quote(self.revision) + '/' + archive)
        log.info("Downloading snapshot: %s", url)
        f_in = urllib2.urlopen(url)
        with open(snapPath, 'w') as f_out:
            copyfileobj(f_in, f_out)
        f_in.close()

    def setRevision(self, rev):
        super(WmsRepository, self).setRevision(rev)
        if 'branch' in rev:
            self.branch = rev['branch']
        if 'path' in rev:
            self.path = rev['path']
