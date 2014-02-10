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
import multiprocessing
import optparse
import pprint
import os
import sys
import tempfile
from bob import config
from bob import main as bobmain
from conary.build import groupsetrecipe
from conary.lib import log as cny_log
from conary.lib import util

log = logging.getLogger('showdeps')


def analyze_plan(root, pluginMgr, recipeDir, relpath):
    cfg = config.BobConfig()
    path = os.path.join(root, relpath)
    cfg.read(path)
    cfg.recipeDir = recipeDir

    # Provide each source that this plan would build
    label = cfg.getTargetLabel()
    provides = {}
    for target in cfg.target:
        provide = '%s=%s' % (target, label)
        provides.setdefault(provide, set()).add(relpath)

    # Require anything mentioned in a resolveTrove
    requires = {}
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
            if hasattr(recipeObj, 'g'):
                group_req, group_prov = analyze_groupset(recipeObj)
            else:
                group_req, group_prov = analyze_group(recipeObj)
            for require in group_req:
                requires.setdefault(require, set()).add(relpath)
            for name in group_prov:
                provide = '%s=%s' % (name, label)
                provides.setdefault(provide, set()).add(relpath)

        elif hasattr(recipeObj, 'packages'):
            for name in recipeObj.packages:
                provide = '%s=%s' % (name, label)
                provides.setdefault(provide, set()).add(relpath)

    return requires, provides


def analyze_group(recipeObj):
    if not hasattr(recipeObj, 'getAdditionalSearchPath'):
        log.warning("Recipe for %s does not have a "
                "getAdditionalSearchPath method; cannot analyze "
                "requirements.", recipeObj.name)
        return [], []
    path = recipeObj.getAdditionalSearchPath()
    if not path:
        log.warning("Recipe for %s does not have a "
                "getAdditionalSearchPath method; cannot analyze "
                "requirements.", recipeObj.name)
        return [], []
    requires = []
    for item in itertools.chain(*path):
        item = item.split('[')[0]
        requires.append(item)
    provides = []
    for name in recipeObj.groups:
        provides.append(name)
    return requires, provides


def analyze_groupset(recipeObj):
    g = recipeObj.g
    requires = []
    for source in g.getRoots():
        if isinstance(source, groupsetrecipe.GroupSearchSourceTroveSet):
            label = source.searchSource.installLabelPath[0]
            for child in g.getChildren(source):
                if isinstance(child, groupsetrecipe.GroupSearchPathTroveSet):
                    log.warning("Bare label %s is used as a search path. "
                            "Troves found there will not be marked as "
                            "requirements.", label)
                    continue
                if not hasattr(child, 'action'):
                    log.warning("Don't know how to handle node of type '%s'",
                            type(child).__name__)
                    continue
                if isinstance(child.action, groupsetrecipe.GroupFindAction):
                    names = child.action.troveSpecs
                else:
                    log.warning("Don't know how to handle action of type '%s'",
                            type(child.action).__name__)
                    continue
                for name in names:
                    name = name.split('[')[0]
                    if '=' not in name:
                        name = '%s=%s' % (name, label)
                    requires.append(name)
        else:
            log.warning("Don't know how to handle source of type '%s'",
                    type(source).__name__)
    provides = []
    for node in g.iterNodes():
        if isinstance(getattr(node, 'action', None),
                groupsetrecipe.CreateGroupAction):
            provides.append(node.action.name)
    return requires, provides


def dump_recipes((root, pluginMgr, recipeDir, relpath)):
    try:
        log.info("Dumping recipes for %s", relpath)
        cfg = config.BobConfig()
        path = os.path.join(root, relpath)
        cfg.read(path)
        cfg.dumpRecipes = True
        cfg.recipeDir = recipeDir
        bob = bobmain.BobMain(pluginMgr)
        bob.setPlan(cfg)
        bob.runDeps()
        return True
    except:
        log.exception("Error parsing file %s:", relpath)
        return False


def _analyze_plan((root, pluginMgr, recipeDir, relpath)):
    try:
        return analyze_plan(root, pluginMgr, recipeDir, relpath)
    except:
        log.exception("Error parsing file %s:", relpath)
        return None


def dedupe(requirers, edges):
    """Trim requirers that are reachable via other requirers"""
    stack = [(x, '') for x in requirers]
    requirers = set(requirers)
    seen = set()
    while stack:
        parent, path = stack.pop(0)
        children = set(edges.get(parent, ()))
        children -= seen
        seen |= children
        requirers -= children
        stack.extend((x, path + '::' + parent) for x in children)
    return requirers


def main(args):
    cny_log.setupLogging(consoleLevel=logging.INFO)
    parser = optparse.OptionParser(usage='%prog {--graph,--required-hosts,--scm} root')
    parser.add_option('--graph', action='store_true')
    parser.add_option('--required-hosts', action='store_true')
    parser.add_option('--scm', action='store_true')
    options, args = parser.parse_args(args)
    if len(args) != 1 or not (options.graph or options.required_hosts or options.scm):
        parser.error('wrong arguments')
    root = os.path.abspath(args[0])

    # Collect a list of bob plans
    bobfiles = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        reldir = dirpath[len(root)+1:]
        for filename in filenames:
            if filename.endswith('.bob'):
                relpath = os.path.join(reldir, filename)
                bobfiles.add(relpath)

    if options.scm:
        watchMap = {}
        for plan in bobfiles:
            cfg = config.openPlan(os.path.join(root, plan))
            aliases = {}
            watchPaths = {}
            for name, value in cfg.scm.items():
                name %= cfg.macros
                value %= cfg.macros
                kind, url = value.split()[:2]
                if kind != 'wms':
                    continue
                aliases[name] = url
            for target in cfg.target:
                sec = cfg.getSection('target:' + target)
                if sec.sourceTree:
                    name, path = sec.sourceTree.split(None, 1)
                    name %= cfg.macros
                    path %= cfg.macros
                    watchPaths.setdefault(name, set()).add(path)
                if sec.scm:
                    name = sec.scm % cfg.macros
                    watchPaths.setdefault(name, set()).add('')
            for alias, paths in watchPaths.items():
                if alias not in aliases:
                    continue
                url = aliases[alias]
                if '' in paths:
                    paths = set([''])
                watchList = watchMap.setdefault(plan, set())
                for path in paths:
                    watchList.add((url, path))
        print "# Map of plan files to SCM paths they consume"
        print "scm_deps = ",
        pprint.pprint(watchMap)
        sys.exit(0)


    recipeDir = tempfile.mkdtemp(prefix='bob-recipes-')
    pluginMgr = bobmain.getPluginManager()
    pool = multiprocessing.Pool(processes=4)
    try:
        # First pass: mangle and dump all the recipes so that loadSuperClass()
        # can work without actually committing anything.
        ok = pool.map(dump_recipes,
                [(root, pluginMgr, recipeDir, x) for x in bobfiles])
        if False in ok:
            sys.exit("Failed to load recipes")

        # Second pass: make provides and requires out of the bob plan, recipe
        # PackageSpecs, and group recipe inputs.
        provides = {}
        requires = {}
        results = pool.map(_analyze_plan,
                [(root, pluginMgr, recipeDir, x) for x in bobfiles])
        if None in results:
            sys.exit("Failed to analyze recipes")
        for plan_requires, plan_provides in results:
            for key, value in plan_requires.iteritems():
                requires.setdefault(key, set()).update(value)
            for key, value in plan_provides.iteritems():
                provides.setdefault(key, set()).update(value)
        pool.close()
    finally:
        util.rmtree(recipeDir)

    if options.graph:
        # Make edges out of any provided thing. Requires that don't match any
        # provider are discarded, since they are outside the analyzed set.
        edges = {}
        for item, providers in provides.iteritems():
            requirers = requires.get(item, set())
            for provider in providers:
                edges.setdefault(provider, set()).update(requirers)

        # Remove edges that are made entirely redundant by a longer path.
        edges_trimmed = {}
        for provider, requirers in edges.iteritems():
            requirers = dedupe(requirers, edges)
            edges_trimmed[provider] = requirers
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
