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

import base64
import json
import logging
import os
import subprocess
import urllib
from conary.lib.http import http_error
from conary.lib.http import opener
from conary.lib.http.request import URL
from conary.lib.util import copyfileobj

from bob import scm

log = logging.getLogger('bob.scm')


class WmsRepository(scm.ScmRepository):

    def __init__(self, cfg, path, branch=None):
        self.wms = WmsClient(cfg)
        self.path = path
        self.branch = branch
        self._open = opener.URLOpener(followRedirects=True).open

    def _getTip(self):
        for path, branch, tip in self.wms.poll(self.path,
                self.branch or 'HEAD'):
            if path == self.path:
                assert len(tip) == 40
                return branch, tip
        else:
            assert False

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
        name = self._archive()
        f = self.wms.archive(self.path, self.revision, name, subtree)
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
        prefix = name.rsplit('.', 1)[0]
        return prefix

    def getAction(self, extra=''):
        url = self.wms.show_url(self.path)
        return 'addGitSnapshot(%r, branch=%r, tag=%r%s)' % (
                url, self.branch, self.getShortRev(), extra)

    def fetchArchive(self, conarySource, snapPath):
        if os.path.exists(snapPath):
            return
        archive = urllib.quote(os.path.basename(snapPath))
        log.info("Downloading %s", archive)
        f_in = self.wms.archive(self.path, self.revision, archive)
        with open(snapPath, 'w') as f_out:
            copyfileobj(f_in, f_out)
        f_in.close()

    def setRevision(self, rev):
        super(WmsRepository, self).setRevision(rev)
        if 'branch' in rev:
            self.branch = rev['branch']
        if 'path' in rev:
            self.path = rev['path']


class WmsClient(object):

    def __init__(self, cfg):
        self.cfg = cfg
        base = URL(cfg.wmsBase)
        self.wmsUser = base.userpass
        self.base = base._replace(userpass=None)
        self.opener = opener.URLOpener(followRedirects=True)

    # API

    def poll(self, repos, branch):
        data = self._open_repos(repos, ['poll', self._quote(branch)])
        return [x.split() for x in data]

    def show_url(self, repos):
        return self._open_repos(repos, ['show_url']).readline().strip()

    def archive(self, repos, ref, name, subtree=None):
        subtrees = [subtree] if subtree else None
        return self._open_repos(repos, ['archive', ref,
            name + self._q_subtrees(subtrees)])

    def create_token(self):
        if not self.wmsUser:
            return None
        try:
            # Check if the credentials we're using are already a token
            self._open_json(['token'], None)
            return None
        except http_error.ResponseError as err:
            # 404 means it's not a token. 403 means credentials are wrong.
            if err.errcode != 404:
                raise
        response = self._open_json(['token'], {})
        token = response['token']
        return (self.wmsUser[0], token)

    def destroy_token(self, token):
        token = token[1]
        try:
            self._open(['token', token], method='DELETE')
        except http_error.ResponseError as err:
            if err.errcode not in (204, 404):
                raise

    # Support methods

    @staticmethod
    def _quote(foo):
        return urllib.quote(foo).replace('/', ':')

    def _repos_url(self, repos, elems):
        silo, subpath = repos.split('/', 1)
        return self.base.join('api/repos/%s/%s/%s' % (silo,
            self._quote(subpath), '/'.join(elems)))

    @staticmethod
    def _q_subtrees(subtrees):
        if subtrees:
            data = urllib.urlencode([('subtree', x) for x in subtrees])
            return '?' + data
        else:
            return ''

    def _add_auth(self, kwargs):
        headers = kwargs.get('headers', [])
        if hasattr(headers, 'items'):
            headers = headers.items()
        if self.wmsUser:
            headers.append(('Authorization',
                'basic ' + base64.b64encode('%s:%s' % self.wmsUser)))
        kwargs['headers'] = headers

    def _open(self, elems, **kwargs):
        self._add_auth(kwargs)
        return self.opener.open(self.base.join('api/' + '/'.join(elems)),
                **kwargs)

    def _open_repos(self, repos, elems, **kwargs):
        self._add_auth(kwargs)
        return self.opener.open(self._repos_url(repos, elems), **kwargs)

    def _open_json(self, elems, json_data, **kwargs):
        self._add_auth(kwargs)
        kwargs['headers'].append(('Content-Type', 'application/json'))
        if json_data is not None:
            kwargs['data'] = json.dumps(json_data)
        fobj = self.opener.open(self.base.join('api/' + '/'.join(elems)),
                **kwargs)
        return json.load(fobj)
