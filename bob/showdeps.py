#
# Copyright (c) rPath, Inc.
#


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
            if '/' in item:
                # Pinned. Probably not a inter-bob require anyway.
                continue
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
    root = os.path.abspath(args[0])
    provides = {}
    requires = {}
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

    edges = {}
    for item, providers in provides.iteritems():
        requirers = requires.get(item, set())
        if not requirers:
            continue
        for provider in providers:
            edges[provider] = set(requirers)

    edges_trimmed = {}
    for provider, requirers in edges.iteritems():
        requirers = dedupe(requirers, edges)
        edges_trimmed[provider] = requirers
    import pprint
    pprint.pprint(edges_trimmed)
