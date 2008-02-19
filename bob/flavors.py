#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

'''
Defines default flavor sets and provides a mechanism for reading a config
target and producing a list of build flavors for that trove.
'''

PLAIN = '!xen,!domU,!dom0,!vmware,'
DOMU = 'xen,domU,!dom0,!vmware,'
DOM0 = 'xen,!domU,dom0,!vmware,'
VMWARE = '!xen,!domU,!dom0,vmware,'

RPL1 = '~X,~!alternatives,!bootstrap,~builddocs,~buildtests,!cross,' \
    '~desktop,~emacs,~gcj,~gnome,~grub.static,~gtk,~ipv6,~kde,~krb,~ldap,' \
    '~nptl,pam,~pcre,~perl,~!pie,~python,~qt,~readline,~!sasl,~!selinux,' \
    'ssl,~tcl,tcpwrappers,~tk,~!xfce'
RPL1_X86 = '~dietlibc,' + RPL1 + \
    ' is: x86(~cmov,~i486,~i586,~i686,~mmx,~nx, ~sse, ~sse2)'
RPL1_X86_64 = '~!dietlibc,' + RPL1 + ' is: x86_64(~3dnow, ~3dnowext, ~nx)'

SETS = {
'plain': [ PLAIN + RPL1_X86, PLAIN + RPL1_X86_64 ],
'dom0': [ DOM0 + RPL1_X86, PLAIN + RPL1_X86_64 ],
'domU': [ DOMU + RPL1_X86, PLAIN + RPL1_X86_64 ],
'appliance': [ PLAIN  + RPL1_X86, PLAIN  + RPL1_X86_64,
               DOMU   + RPL1_X86, DOMU   + RPL1_X86_64,
               VMWARE + RPL1_X86, VMWARE + RPL1_X86_64 ],
}

def expand_targets(cfg):
    '''Accept a target config section and return a list of build flavors'''
    if cfg.flavor_set and cfg.flavor:
        raise RuntimeError('flavor_set and flavor cannot be used together')

    if cfg.flavor_set:
        try:
            return SETS[cfg.flavor_set]
        except IndexError:
            raise RuntimeError('flavor set "%s" is not defined'
                % cfg.flavor_set)
    else:
        return [str(f) for f in cfg.flavor]
