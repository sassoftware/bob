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

from bob import main as bob_main
from bob.scm import wms


def main(args=sys.argv[1:]):
    parser = optparse.OptionParser()
    parser.add_option('--base-uri')
    parser.add_option('--repo')
    parser.add_option('--plan')
    options, args = parser.parse_args(args)
    if not options.base_uri or not options.repo or not options.plan:
        parser.error("missing option")

    tips = {}
    for line in open('revision.txt'):
        repo, branch, tip = line.strip().split(' ', 2)
        if repo == options.repo:
            break
    else:
        sys.exit("repo %s not in revision.txt" % options.repo)
    repo = wms.WmsRepository(base=options.base_uri, path=options.repo)
    repo.revision = tip
    repo.revIsExact = True
    repo.branch = branch

    planDir = tempfile.mkdtemp(dir='.')
    try:
        prefix = repo.checkout(planDir)
        plan = os.path.join(planDir, prefix, options.plan)
        return bob_main.main([plan])
    finally:
        util.rmtree(planDir)


if __name__ == '__main__':
    main(sys.argv[1:])
