#
# Copyright (c) 2008 rPath, Inc.
#
# All rights reserved.
#

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

sets = {
'plain': [ PLAIN + RPL1_X86, PLAIN + RPL1_X86_64 ],
'dom0': [ DOM0 + RPL1_X86, PLAIN + RPL1_X86_64 ],
'domU': [ DOMU + RPL1_X86, PLAIN + RPL1_X86_64 ],
'appliance': [ PLAIN  + RPL1_X86, PLAIN  + RPL1_X86_64,
               DOMU   + RPL1_X86, DOMU   + RPL1_X86_64,
               VMWARE + RPL1_X86, VMWARE + RPL1_X86_64 ],
}

def expandByTarget(cfg):
    if cfg.flavor_set and cfg.flavor:
        raise RuntimeError('flavor_set and flavor cannot be used together')

    if cfg.flavor_set:
        if sets.has_key(cfg.flavor_set):
            return sets[cfg.flavor_set]
        else:
            raise RuntimeError('flavor set "%s" is not defined'
                % cfg.flavor_set)
    else:
        return [str(f) for f in cfg.flavor]
