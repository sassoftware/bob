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


PYVER=$(shell python -c 'import sys; print(sys.version[0:3])')
PYTHON = /usr/bin/python${PYVER}

DESTDIR=
VERSION=$(shell grep ^VERSION setup.py |cut -d\' -f2)
MANPAGES=$(notdir $(filter %.1,$(wildcard docs/manpages/*.1)))
prefix = /usr
lib = $(shell uname -m | sed -r '/x86_64|ppc64|s390x|sparc64/{s/.*/lib64/;q};s/.*/lib/')
libdir = $(prefix)/$(lib)
bindir = $(prefix)/bin
datadir = $(prefix)/share
mandir = $(datadir)/man
sitepkg = $(libdir)/python$(PYVER)/site-packages
eggname = bob-$(VERSION)-py$(PYVER).egg

.PHONY: all install clean dist/$(eggname)

all: dist/$(eggname)

install: all man
	mkdir -p $(DESTDIR)$(sitepkg)
	rm -rf $(DESTDIR)$(sitepkg)/$(eggname)
	$(PYTHON) -measy_install -m -d $(DESTDIR)$(sitepkg) -s $(DESTDIR)$(bindir) dist/$(eggname)
	$(PYTHON) -mcompileall -f -d $(sitepkg)/$(eggname) $(DESTDIR)$(sitepkg)/$(eggname)
	for x in $(DESTDIR)$(bindir)/*; do \
		mv $$x $$x-$(VERSION); \
		ln -sfn $$(basename $$x)-$(VERSION) $$x; \
	done

man:
	mkdir -p $(DESTDIR)$(mandir)/man1
	for M in $(MANPAGES); do \
		install -m 0644 docs/manpages/$$M $(DESTDIR)$(mandir)/man1/; \
		gzip $(DESTDIR)$(mandir)/man1/$$M; \
	done

clean:
	@find bob -name \*.pyc -delete
	@rm -rf build dist *.egg-info bob/version.py


dist/$(eggname):
	$(PYTHON) setup.py bdist_egg
