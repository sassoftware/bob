#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Defines default flavor sets and provides a mechanism for reading a config
target and producing a list of build flavors for that trove.
'''

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

    for arch in arches:
        arch_set = distro['arches'][arch]
        ret.append(deps.parseFlavor(prefix + arch_set['prefix'] +
            distro['base'] + arch_set['suffix']))

    return ret


def expand_targets(cfg):
    '''
    Accept a target config section and return a list of build flavors.
    '''
    if cfg.flavor_set and cfg.flavor:
        raise RuntimeError('flavor_set and flavor cannot be used together')

    if cfg.flavor_set:
        try:
            return SETS[cfg.flavor_set]
        except IndexError:
            raise RuntimeError('flavor set "%s" is not defined'
                % cfg.flavor_set)
    else:
        return cfg.flavor


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
