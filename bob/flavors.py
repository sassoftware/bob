#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Defines default flavor sets and provides a mechanism for reading a config
target and producing a list of build flavors for that trove.
'''

import re

from conary.deps import arch
from conary.deps import deps


_DISTROS = {
    'rPL 1': { # rPath Linux 1 flavor defaults
        'base': '~X,~!alternatives,!bootstrap,~builddocs,~buildtests,'
            '!cross,~desktop,~emacs,~gcj,~gnome,~grub.static,~gtk,~ipv6,'
            '~kde,~krb,~ldap,~nptl,pam,~pcre,~perl,~!pie,~python,~qt,'
            '~readline,~!sasl,~!selinux,ssl,~tcl,tcpwrappers,~tk,~!xfce',
        'arches': {
            'x86': {
                'prefix': '~dietlibc,',
                'suffix': ' is: x86(~cmov, ~i486, ~i586, ~i686, ~mmx, '
                    '~nx, ~sse, ~sse2)',
            },
            'x86_64': {
                'prefix': '~!dietlibc,',
                'suffix': ' is: x86_64(~3dnow, ~3dnowext, ~nx)',
                'dual': [' is: x86(~cmov, ~i486, ~i586, ~i686, ~mmx, '
                    '~nx, ~sse, ~sse2) x86_64(~3dnow, ~3dnowext, ~nx)'],
            },
        },
    }
}


def _make_set(prefix, distro='rPL 1', arches=None):
    '''Make a set of flavors from a flavor prefix and the given distro set'''

    distro = _DISTROS[distro]
    ret = []
    if not arches:
        arches = distro['arches'].keys()

    for one_arch in arches:
        arch_set = distro['arches'][one_arch]
        ret.append(deps.parseFlavor(prefix + arch_set['prefix'] +
            distro['base'] + arch_set['suffix']))

    return ret


flavor_template_re = re.compile('%([^%:]+):([^%:]+)%')
def expand_targets(cfg):
    '''
    Accept a target config section and return a list of build flavors.
    '''

    # If no configuration is available, build is: x86
    if not cfg:
        return SETS['x86']

    # Ensure flavor_set and flavor aren't both set
    # This might be supported later, by recombining flavors from each
    if cfg.flavor_set and cfg.flavor:
        raise ValueError('flavor_set and flavor cannot be used together')

    if cfg.flavor_set:
        try:
            return SETS[cfg.flavor_set]
        except IndexError:
            raise RuntimeError('flavor set "%s" is not defined'
                % cfg.flavor_set)
    else:
        ret = []
        for flavor in cfg.flavor:
            if '%' in flavor:
                # Handle "templates" in flavors, e.g.
                # flavor %rPL 1:x86% xen,dom0,!domU,!vmware
                match = flavor_template_re.search(flavor)
                if not match:
                    raise RuntimeError('Malformed template in flavor')

                stripped_flavor = flavor_template_re.sub('', flavor)
                if '%' in stripped_flavor:
                    raise RuntimeError('Cannot have multiple templates '
                        'in flavor')

                distro, arch = match.groups()
                distro = _DISTROS[distro]
                arch_set = distro['arches'][arch]
                base = deps.parseFlavor(arch_set['prefix'] + distro['base']
                    + arch_set['suffix'])
                suffix = deps.parseFlavor(stripped_flavor)
                ret.append(deps.overrideFlavor(base, suffix))
            else:
                ret.append(deps.parseFlavor(flavor))

        return ret


def guess_search_flavors(flavor, distro='rPL 1'):
    '''
    Given a build flavor, decide a reasonable search flavor list, possibly
    using a particular distro set.
    '''

    # Determine the major architecture of the given build flavor
    maj_arch = None
    for dep_group in flavor.getDepClasses().itervalues():
        if isinstance(dep_group, deps.InstructionSetDependency):
            maj_arch = arch.getMajorArch(dep_group.getDeps()).name
    if not maj_arch:
        maj_arch = 'x86'

    distro = _DISTROS[distro]
    arch_set = distro['arches'][maj_arch]

    # Start the search flavor with the stock build flavor
    ret = [deps.parseFlavor(arch_set['prefix'] +
        distro['base'] + arch_set['suffix'])]

    # Now add dual-arch flavors
    for dual_suffix in arch_set.get('dual', []):
        ret.append(deps.parseFlavor(arch_set['prefix'] +
            distro['base'] + dual_suffix))

    return ret


def reduce_flavors(package, target_cfg, flavors_in):
    '''
    Reduce the set of flavors to be built for a given trove to specify
    only:
     * The instruction set
     * Package use flags (for this package only)
     * Flavors specified in a "use" config item for the package
    '''

    flavors_out = set()

    if target_cfg and (target_cfg.flavor or target_cfg.flavor_set):
        # An explicit set of flavors was provided. They should be used instead
        # of whatever we were given.
        flavors_out = expand_targets(target_cfg)
    elif target_cfg and target_cfg.flavor_mask != deps.Flavor():
        # A flavor mask was provided. Use this to select which flavors are
        # unique, and build only variations on them.
        flavors_out = set()
        for flavor in flavors_in:
            flavors_out.add(mask_flavor(flavor, target_cfg.flavor_mask))
    elif package.startswith('group-'):
        # With groups it's impossible to guess which flavors are useful; and
        # usually we want them all anyway since groups are where the set of
        # flavors to be built originate.
        flavors_out = flavors_in
    else:
        # No rules were specified. Keep instruction set and package flags
        # for this package only.
        flavors_out = set()
        for flavor in flavors_in:
            flavors_out.add(fragment_flavor(package, flavor))

    return flavors_out


def mask_flavor(base_flavor, mask_flavor):
    '''
    Remove flags from I{base_flavor} not present in I{mask_flavor}. Sense of
    flags in I{mask_flavor} is ignored.
    '''

    new_flavor = deps.Flavor()
    for cls, mask_dep in mask_flavor.iterDeps():
        base_class = base_flavor.members.get(cls.tag, None)
        if base_class is None:
            continue
        base_dep = base_class.members.get(mask_dep.name, None)
        if base_dep is None:
            continue

        new_flags = {}
        for flag, sense in mask_dep.flags.iteritems():
            if flag in base_dep.flags:
                new_flags[flag] = base_dep.flags[flag]

        new_flavor.addDep(cls, deps.Dependency(mask_dep.name, new_flags))

    return new_flavor


def fragment_flavor(package, flavor):
    '''
    Select instruction set and package flags from a flavor and return just
    those parts.
    '''

    new_flavor = deps.Flavor()
    for dep in flavor.iterDepsByClass(deps.InstructionSetDependency):
        # Instruction set (e.g. arch)
        new_flavor.addDep(deps.InstructionSetDependency, dep)
    for dep in flavor.iterDepsByClass(deps.UseDependency):
        new_flags = {}
        for flag, sense in dep.flags.iteritems():
            if flag.startswith(package + '.'):
                new_flags[flag] = sense
        new_flavor.addDep(deps.UseDependency,
            deps.Dependency('use', new_flags))

    return new_flavor


# Flavor fragments used below in SETS
_PLAIN = '!xen,!domU,!dom0,!vmware,'
_DOMU = 'xen,domU,!dom0,!vmware,'
_DOM0 = 'xen,!domU,dom0,!vmware,'
_VMWARE = '!xen,!domU,!dom0,vmware,'


# Lists of build flavors that can be used to easily build packages and groups
# in multiple useful flavors.
SETS = {
'x86': _make_set(_PLAIN, arches=['x86']),
'x86_64': _make_set(_PLAIN, arches=['x86_64']),
'plain': _make_set(_PLAIN),
'dom0': _make_set(_DOM0),
'domU': _make_set(_DOMU),
'appliance': _make_set(_PLAIN) +
             _make_set(_DOMU)  +
             _make_set(_VMWARE),
}
