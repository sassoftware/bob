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

MAJOR=3.1
VERSION=3.1.0


PYVER=$(shell python -c 'import sys; print(sys.version[0:3])')
PYTHON = /usr/bin/python${PYVER}

DESTDIR=/
prefix = /usr
lib = $(shell uname -m | sed -r '/x86_64|ppc64|s390x|sparc64/{s/.*/lib64/;q};s/.*/lib/')
libdir = $(prefix)/$(lib)
bindir = $(prefix)/bin
sitepkg = $(libdir)/python$(PYVER)/site-packages
eggdir = $(sitepkg)/bob-$(VERSION)-py$(PYVER).egg

generated_files = bob/version.py bin/setup.py bin/bob

.PHONY: bob/version.py all install clean

all: $(generated_files)

install: $(generated_files)
	# This should be roughly equivalent to bdist_egg && easy_install -m,
	# except that it uses DESTDIR
	rm -rf $(DESTDIR)$(eggdir)
	$(PYTHON) bin/setup.py install --force --quiet \
		--root "$(DESTDIR)" \
		--single-version-externally-managed \
		--install-purelib "$(eggdir)" \
		--install-platlib "$(eggdir)" \
		--install-data "$(eggdir)"
	mv $(DESTDIR)$(eggdir)/*.egg-info $(DESTDIR)$(eggdir)/EGG-INFO
	install -D -m0755 bin/bob $(DESTDIR)$(bindir)/bob-$(MAJOR)

clean:
	@rm -f $(generated_files) bob/*.py[co]
	@rm -rf build *.egg-info


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

bin/bob: bin/bob.in
	echo "#!$(PYTHON)" >$@
	sed -e 's/@VERSION@/$(VERSION)/g' $< >>$@
	chmod a+rx $@

bin/setup.py: bin/setup.py.in bob/version.py
	sed -e 's/@VERSION@/$(VERSION)/g' $< >$@
