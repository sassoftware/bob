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


import optparse
import os
import sys
import tempfile
from conary.lib import util

from bob import config
from bob import main as bob_main
from bob.rev_file import RevisionFile
from bob.scm import wms


def main(args=sys.argv[1:]):
    cfg = config.openPlan(None, systemOnly=True)
    parser = optparse.OptionParser()
    parser.add_option('--base-uri')
    parser.add_option('--repo')
    parser.add_option('--plan')
    parser.add_option('--checkout')
    options, args = parser.parse_args(args)
    if not cfg.wmsBase:
        if options.base_uri:
            cfg.wmsBase = options.base_uri
        else:
            parser.error("Please set wmsBase option in /etc/bobrc or ~/.bobrc")
    if not options.repo:
        parser.error("--repo option must be set")
    if not options.plan and not options.checkout:
        parser.error("Must set one of --checkout or --plan")

    rf = RevisionFile()
    tip = rf.revs.get(options.repo)
    path = options.repo
    if not tip:
        for path, tip in rf.revs.items():
            if os.path.basename(path) == options.repo:
                break
        else:
            sys.exit("repo %s not in revision.txt" % options.repo)
    repo = wms.WmsRepository(cfg, path=path)
    repo.revision = tip['id']
    repo.branch = tip['branch']
    repo.revIsExact = True

    if options.checkout:
        checkoutDir = os.path.abspath(options.checkout)
        if os.path.exists(checkoutDir):
            util.rmtree(checkoutDir)
        parent = os.path.dirname(checkoutDir)
        prefix = repo.checkout(parent)
        os.rename(os.path.join(parent, prefix), checkoutDir)
    else:
        planDir = tempfile.mkdtemp(dir='.')
        try:
            prefix = repo.checkout(planDir)
            plan = os.path.join(planDir, prefix, options.plan)
            return bob_main.main([plan])
        finally:
            util.rmtree(planDir)


if __name__ == '__main__':
    main(sys.argv[1:])
