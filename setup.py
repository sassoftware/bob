#!/usr/bin/python
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


import os
from setuptools import setup, find_packages

VERSION = '4.2'

with open('bob/version.py', 'w') as f:
    print >> f, "version = %r" % VERSION
    print >> f, "changeset = %r" % os.popen('./scripts/hg-version.sh').read().strip()

setup(name='bob',
      version=VERSION,
      platforms='any',
      packages=find_packages(),
      entry_points="""\
      [console_scripts]
      bob = bob.main:main
      bob-deps = bob.showdeps:main
      bob-jenkins = bob.jenkins:main
      """,
)