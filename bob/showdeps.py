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

import itertools
import logging
import optparse
import os
import sys
import tempfile
from bob import config
from bob import main as bobmain
from conary.lib import log as cny_log
from conary.lib import util

log = logging.getLogger('showdeps')


def analyze_plan(provides, requires, root, relpath, pluginMgr, recipeDir):
    cfg = config.BobConfig()
    path = os.path.join(root, relpath)
    cfg.read(path)
    cfg.recipeDir = recipeDir

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

    # Analyze recipe for provides, and in the case of groups, requires
    log.info("Loading recipes for plan %s", relpath)
    bob = bobmain.BobMain(pluginMgr)
    bob.setPlan(cfg)
    targets, batch = bob.runDeps()
    for package, (_, recipeObj) in zip(batch.packages, batch.recipes):
        if package.name.startswith('group-'):
            if not hasattr(recipeObj, 'getAdditionalSearchPath'):
                log.warning("Recipe for %s does not have a "
                        "getAdditionalSearchPath method; cannot analyze "
                        "requirements.", package.name)
                continue
            path = recipeObj.getAdditionalSearchPath()
            if not path:
                log.warning("Recipe for %s does not have a "
                        "getAdditionalSearchPath method; cannot analyze "
                        "requirements.", package.name)
                continue
            for item in itertools.chain(*path):
                item = item.split('[')[0]
                requires.setdefault(item, set()).add(relpath)
        elif hasattr(recipeObj, 'packages'):
            for name in recipeObj.packages:
                provide = '%s=%s' % (name, label)
                provides.setdefault(provide, set()).add(relpath)


def dump_recipes(root, relpath, pluginMgr, recipeDir):
    log.info("Dumping recipes for %s", relpath)
    cfg = config.BobConfig()
    path = os.path.join(root, relpath)
    cfg.read(path)
    cfg.dumpRecipes = True
    cfg.recipeDir = recipeDir
    bob = bobmain.BobMain(pluginMgr)
    bob.setPlan(cfg)
    bob.runDeps()


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
    cny_log.setupLogging(consoleLevel=logging.INFO)
    parser = optparse.OptionParser(usage='%prog {--graph,--required-hosts} root')
    parser.add_option('--graph', action='store_true')
    parser.add_option('--required-hosts', action='store_true')
    options, args = parser.parse_args(args)
    if not args or not (options.graph or options.required_hosts):
        parser.error('wrong arguments')

    failed = False
    bobfiles = set()
    pluginMgr = bobmain.getPluginManager()
    recipeDir = tempfile.mkdtemp(prefix='bob-recipes-')
    try:
        # Collect a list of bob plans
        for root in args:
            root = os.path.abspath(root)
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames.sort()
                filenames.sort()
                reldir = dirpath[len(root)+1:]
                for filename in filenames:
                    if filename.endswith('.bob'):
                        relpath = os.path.join(reldir, filename)
                        bobfiles.add(relpath)

        # First pass: mangle and dump all the recipes so that loadSuperClass()
        # can work without actually committing anything.
        for relpath in bobfiles:
            try:
                dump_recipes(root, relpath, pluginMgr, recipeDir)
            except KeyboardInterrupt:
                raise
            except:
                log.exception("Error parsing file %s:", relpath)
                failed = True
        if failed:
            sys.exit(1)

        # Second pass: make provides and requires out of the bob plan, recipe
        # PackageSpecs, and group recipe inputs.
        provides = {}
        requires = {}
        for relpath in sorted(bobfiles):
            try:
                analyze_plan(provides, requires, root, relpath, pluginMgr,
                        recipeDir)
            except KeyboardInterrupt:
                raise
            except:
                log.exception("Error parsing file %s:", relpath)
                failed = True
        if failed:
            sys.exit(1)
    finally:
        util.rmtree(recipeDir)

    if options.graph:
        # Make edges out of any provided thing. Requires that don't match any
        # provider are discarded, since they are outside the analyzed set.
        edges = {}
        for item, providers in provides.iteritems():
            requirers = requires.get(item, set())
            for provider in providers:
                edges[provider] = set(requirers)

        # Remove edges that are made entirely redundant by a longer path.
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
