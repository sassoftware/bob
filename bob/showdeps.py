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
import traceback
from bob import config


def analyze_plan(provides, requires, root, relpath):
    cfg = config.BobConfig()
    path = os.path.join(root, relpath)
    cfg.read(path)

    # Provide each source that this plan would build
    label = cfg.getTargetLabel()
    for target in cfg.target:
        provide = '%s=%s' % (target, label)
        provides.setdefault(provide, set()).add(relpath)

    # Require anything mentioned in a resolveTrove
    for bucket in cfg.resolveTroves:
        for item in bucket:
            item %= cfg.getMacros()
            requires.setdefault(item, set()).add(relpath)


def dedupe(requirers, edges):
    """Trim requirers that are reachable via other requirers"""
    stack = [(x, '') for x in requirers]
    requirers = set(requirers)
    seen = set()
    while stack:
        parent, path = stack.pop(0)
        children = edges.get(parent, set())
        children.discard(seen)
        seen.update(children)
        for nuke in children & requirers:
            requirers.discard(nuke)
        stack.extend((x, path + '::' + parent) for x in children)
    return requirers


def main(args):
    parser = optparse.OptionParser(usage='%prog {--graph,--required-hosts} root')
    parser.add_option('--graph', action='store_true')
    parser.add_option('--required-hosts', action='store_true')
    options, args = parser.parse_args(args)
    if not args or not (options.graph or options.required_hosts):
        parser.error('wrong arguments')
    provides = {}
    requires = {}
    for root in args:
        root = os.path.abspath(root)
        for dirpath, dirnames, filenames in os.walk(root):
            reldir = dirpath[len(root)+1:]
            for filename in filenames:
                if filename.endswith('.bob'):
                    relpath = os.path.join(reldir, filename)
                    try:
                        analyze_plan(provides, requires, root, relpath)
                    except:
                        print 'Error parsing file %s:' % relpath
                        traceback.print_exc()
                        sys.exit(1)

    if options.graph:
        edges = {}
        for item, providers in provides.iteritems():
            requirers = requires.get(item, set())
            for provider in providers:
                edges[provider] = set(requirers)

        edges_trimmed = {}
        for provider, requirers in edges.iteritems():
            requirers = dedupe(requirers, edges)
            edges_trimmed[provider] = requirers
        import pprint
        print '# map of providers to the set of requirers'
        print 'dep_graph = ',
        pprint.pprint(edges_trimmed)

    if options.required_hosts:
        mapping = {}
        for item, requirers in requires.iteritems():
            if item.count('=') != 1:
                print "Doesn't look like a trovespec:", item
                continue
            name, version = item.split('=')
            if version.count('@') != 1:
                print "Doesn't look like a trovespec:", item
                continue
            host = version.split('@')[0]
            if host.count('/') == 1 and host[0] == '/':
                host = host[1:]
            mapping.setdefault(host, {})[item] = requirers
        for host, items in sorted(mapping.items()):
            print host
            for item, requirers in sorted(items.items()):
                print ' ', item, '\t', sorted(requirers)[0]
