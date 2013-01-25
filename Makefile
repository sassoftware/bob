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
