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


'''
Helper functions for dealing with mercurial (hg) repositories.
'''

import logging
import os
import subprocess

from bob import scm

log = logging.getLogger('bob.hg')


class GitRepository(scm.ScmRepository):

    def __init__(self, cacheDir, uri, branch):
        self.uri = uri
        self.branch = branch

        dirPath = self.uri.split('//', 1)[-1]
        dirPath = dirPath.replace('/', '_')
        self.repoDir = os.path.join(cacheDir, dirPath, 'git')

    def getTip(self):
        self.updateCache()
        p = subprocess.Popen(['git', 'rev-parse', self.branch],
                stdout=subprocess.PIPE, cwd=self.repoDir)
        stdout, _ = p.communicate()
        if p.returncode:
            raise RuntimeError("git exited with status %s" % p.returncode)
        rev = stdout.split()[0]
        assert len(rev) == 40
        return rev[:12]

    def updateCache(self):
        # Create the cache repo if needed.
        if not os.path.isdir(self.repoDir):
            os.makedirs(self.repoDir)
        if not (os.path.isdir(self.repoDir + '/refs')
                or os.path.isdir(self.repoDir + '/.git/refs')):
            subprocess.check_call(['git', 'init', '--bare'], cwd=self.repoDir)
        subprocess.check_call(['git', 'fetch', '-q',
            self.uri, '+%s:%s' % (self.branch, self.branch)], cwd=self.repoDir)

    def checkout(self, workDir):
        p1 = subprocess.Popen(['git', 'archive', '--format=tar',
            self.revision], stdout=subprocess.PIPE, cwd=self.repoDir)
        p2 = subprocess.Popen(['tar', '-x'], stdin=p1.stdout, cwd=workDir)
        p1.stdout.close()  # remove ourselves from between git and tar
        p1.wait()
        p2.wait()
        if p1.returncode:
            raise RuntimeError("git exited with status %s" % p1.returncode)
        if p2.returncode:
            raise RuntimeError("tar exited with status %s" % p1.returncode)

    def getAction(self):
        return 'addGitSnapshot(%r, branch=%r, tag=%r)' % (
                self.uri, self.branch, self.revision)
