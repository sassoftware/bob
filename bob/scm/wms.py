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

from bob import scm

log = logging.getLogger('bob.scm')


class WmsRepository(scm.ScmRepository):

    def __init__(self, base, path, branch):
        self.base = base
        self.path = path
        self.branch = branch

        silo, subpath = path.split('/', 1)
        self.pathq = self._quote(silo) + '/' + self._quote(subpath)
        self.branchq = self._quote(branch)
        self.repos = self.base + '/api/repos/' + self.pathq

    @staticmethod
    def _quote(foo):
        return urllib.quote(foo).replace('/', ',')

    def getTip(self):
        f = urllib2.urlopen(self.repos + '/poll/' + self.branchq)
        result = f.readlines()
        f.close()
        assert len(result) == 1
        rev = result[0].split()[0]
        assert len(rev) == 40
        return rev

    def updateCache(self):
        pass

    def _archive(self, compress=''):
        return urllib.quote(os.path.basename(self.path)
                + '-' + self.getShortRev()
                + '.tar' + compress)

    def checkout(self, workDir, subtree):
        archive = self._archive()
        prefix = archive.rsplit('.', 1)[0]
        f = urllib2.urlopen(self.repos + '/archive/'
                    + self.revision + '/' + archive,
                data=urllib.urlencode([('subtree', subtree)]))
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
        return prefix

    def getAction(self, extra=''):
        f = urllib2.urlopen(self.repos + '/show_url')
        url = f.readline().strip()
        f.close()
        return 'addGitSnapshot(%r, branch=%r, tag=%r%s)' % (
                url, self.branch, self.getShortRev(), extra)
