#
# Copyright (c) rPath, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#


MAJOR=4.0
VERSION=4.0.1


PYVER=$(shell python -c 'import sys; print(sys.version[0:3])')
PYTHON = /usr/bin/python${PYVER}

DESTDIR=/
prefix = /usr
lib = $(shell uname -m | sed -r '/x86_64|ppc64|s390x|sparc64/{s/.*/lib64/;q};s/.*/lib/')
libdir = $(prefix)/$(lib)
bindir = $(prefix)/bin
sitepkg = $(libdir)/python$(PYVER)/site-packages
eggdir = $(sitepkg)/bob-$(VERSION)-py$(PYVER).egg

generated_files = bob/version.py bin/setup.py bin/bob bin/bob-deps

.PHONY: bob/version.py all install clean

all: $(generated_files)
	$(PYTHON) bin/setup.py egg_info

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

bin/bob bin/bob-deps: %: %.in
	echo "#!$(PYTHON)" >$@
	tail -n +2 $< | sed -e 's/@VERSION@/$(VERSION)/g' >>$@
	chmod a+rx $@

bin/setup.py: bin/setup.py.in bob/version.py
	sed -e 's/@VERSION@/$(VERSION)/g' $< >$@
