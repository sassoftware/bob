#
# Copyright (c) 2010 rPath, Inc.
#
# This program is distributed under the terms of the Common Public License,
# version 1.0. A copy of this license should have been distributed with this
# source file in a file called LICENSE. If it is not present, the license
# is always available at http://www.rpath.com/permanent/licenses/CPL-1.0.
#
# This program is distributed in the hope that it will be useful, but
# without any warranty; without even the implied warranty of merchantability
# or fitness for a particular purpose. See the Common Public License for
# full details.
#
#
prefix = /usr
export lib = $(shell uname -m | sed -r '/x86_64|ppc64|s390x|sparc64/{s/.*/lib64/;q};s/.*/lib/')
libdir = $(prefix)/$(lib)
bindir = $(prefix)/bin

export DESTDIR=
PYVER=$(shell python -c 'import sys; print(sys.version[0:3])')
PYTHON = /usr/bin/python${PYVER}
PYCFLAGS=-c 'import compileall, sys;[compileall.compile_dir(x, ddir=x.replace("$(DESTDIR)", ""), quiet=1) for x in sys.argv[1:]]'
VERSION=4.0

sitepkg = $(libdir)/python$(PYVER)/site-packages
bobdir = $(sitepkg)/bob

generated_files = bob/version.py

.PHONY: $(generated_files)

all: $(generated_files)

install: $(generated_files)
	mkdir -p "$(DESTDIR)$(bobdir)"
	cp -a bob/*.py "$(DESTDIR)$(bobdir)/"
	$(PYTHON) $(PYCFLAGS) "$(DESTDIR)$(bobdir)"
	install -D -m755 bin/bob $(DESTDIR)$(bindir)/bob

clean:
	rm -f $(generated_files) bob/*.py[co]


bob/version.py:
	echo "version = '$(VERSION)'" >bob/version.py
	@if [[ -x /usr/bin/hg && -d .hg ]] ; then \
		rev=`hg id -i`; \
	elif [ -f .hg_archival.txt ]; then \
		rev=`grep node .hg_archival.txt |cut -d' ' -f 2 |head -c 12`; \
	else \
		rev= ; \
	fi ; \
	echo "revision = '$$rev'" >>bob/version.py
