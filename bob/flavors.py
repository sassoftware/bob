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
Defines default flavor sets and provides a mechanism for reading a config
target and producing a list of build flavors for that trove.
'''

import re

from conary.deps import arch
from conary.deps import deps
from conary.deps.deps import parseFlavor as F


_DISTROS = {
    'rPL 1': { # rPath Linux 1 flavor defaults
        'base': F('~X,~!alternatives,!bootstrap,~builddocs,~buildtests,'
            '!cross,~desktop,~emacs,~gcj,~gnome,~grub.static,~gtk,~ipv6,'
            '~kde,~krb,~ldap,~nptl,pam,~pcre,~perl,~!pie,~python,~qt,'
            '~readline,~!sasl,~!selinux,ssl,~tcl,tcpwrappers,~tk,~!xfce'),
        'arches': {
            'x86': [
                F('~dietlibc is: x86(~cmov, ~i486, ~i586, ~i686, ~mmx, '
                    '~nx, ~sse, ~sse2)'),
            ],
            'x86_64': [
                F('~!dietlibc is: x86(~cmov, ~i486, ~i586, ~i686, ~mmx, '
                    '~nx, ~sse, ~sse2) x86_64(~3dnow, ~3dnowext, ~nx)'),
            ],
            'x86_64_pure': [
                F('~!dietlibc is: x86_64(~3dnow, ~3dnowext, ~nx)'),
            ],
        },
    },

    'rPL 2': {
        'base': F(''),
        'arches': {
            'x86': [
                F('is: x86(i486,i586,i686,sse,sse2)'),
            ],
            'x86_64': [
                F('is: x86(i486,i586,i686,sse,sse2) x86_64'),
            ],
            'x86_64_pure': [
                F('is: x86_64'),
            ],
        },
    }
}


def _make_set(prefix, distro='rPL 1', arches=None):
    '''Make a set of flavors from a flavor prefix and the given distro set'''

    prefix = deps.parseFlavor(prefix)
    distro = _DISTROS[distro]
    ret = []
    if not arches:
        arches = distro['arches'].keys()

    for one_arch in arches:
        flav = distro['base'].copy()
        flav.union(distro['arches'][one_arch][0])
        flav.union(prefix)
        ret.append(flav)

    return ret


FLAVOR_TEMPLATE_RE = re.compile('%([^%:]+):([^%:]+)%')
def expand_targets(cfg):
    '''
    Accept a target config section and return a list of build flavors.
    '''

    # If no configuration is available, build is: x86
    if not cfg or (not cfg.flavor_set and not cfg.flavor):
        return SETS['rPL 1']['x86']

    # Ensure flavor_set and flavor aren't both set
    # This might be supported later, by recombining flavors from each
    if cfg.flavor_set and cfg.flavor:
        raise ValueError('flavor_set and flavor cannot be used together')

    if cfg.flavor_set:
        if ':' in cfg.flavor_set:
            distro, set_name = cfg.flavor_set.split(':', 1)
        else:
            distro, set_name = 'rPL 1', cfg.flavor_set

        try:
            return SETS[distro][set_name]
        except KeyError:
            raise RuntimeError('flavor set "%s" is not defined for '
                'distro "%s"' % (distro, set_name))
    else:
        ret = []
        for flavor in cfg.flavor:
            if '%' in flavor:
                # Handle "templates" in flavors, e.g.
                # flavor %rPL 1:x86% xen,dom0,!domU,!vmware
                match = FLAVOR_TEMPLATE_RE.search(flavor)
                if not match:
                    raise RuntimeError('Malformed template in flavor')

                stripped_flavor = FLAVOR_TEMPLATE_RE.sub('', flavor)
                if '%' in stripped_flavor:
                    raise RuntimeError('Cannot have multiple templates '
                        'in flavor')

                distro_name, arch_name = match.groups()
                distro = _DISTROS[distro_name]
                base = distro['base'].copy()
                base.union(distro['arches'][arch_name][0])

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
    ret = []
    for suffix in arch_set:
        flav = distro['base'].copy()
        flav.union(suffix)
        ret.append(flav)

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


def mask_flavor(baseFlavor, maskFlavor):
    '''
    Remove flags from I{baseFlavor} not present in I{maskFlavor}. Sense of
    flags in I{maskFlavor} is ignored.
    '''

    new_flavor = deps.Flavor()
    for cls, mask_dep in maskFlavor.iterDeps():
        base_class = baseFlavor.members.get(cls.tag, None)
        if base_class is None:
            continue
        base_dep = base_class.members.get(mask_dep.name, None)
        if base_dep is None:
            continue

        new_flags = {}
        for flag, _ in mask_dep.flags.iteritems():
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
_PLAIN = '!xen,!domU,!dom0,!vmware'
_DOMU = 'xen,domU,!dom0,!vmware'
_DOMZ = 'xen,!domU,dom0,!vmware'
_VMWARE = '!xen,!domU,!dom0,vmware'


# Lists of build flavors that can be used to easily build packages and groups
# in multiple useful flavors.
SETS = {}
def _populate_sets():
    '''
    Fill out C{SETS} on module load.
    '''
    arches = ['x86', 'x86_64']
    for distro in _DISTROS:
        SETS[distro] = {
            'x86': _make_set(_PLAIN, distro, arches=['x86']),
            'x86_64': _make_set(_PLAIN, distro, arches=['x86_64']),
            'plain': _make_set(_PLAIN, distro, arches=arches),
            'dom0': _make_set(_DOMZ, distro, arches=arches),
            'domU': _make_set(_DOMU, distro, arches=arches),
            'appliance': _make_set(_PLAIN, distro, arches=arches) +
                         _make_set(_DOMU, distro, arches=arches)  +
                         _make_set(_VMWARE, distro, arches=arches),
            }
_populate_sets()
