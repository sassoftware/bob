prefix = /usr
lib = $(shell arch | sed -r '/x86_64|ppc64|s390x|sparc64/{s/.*/lib64/;q};s/.*/lib/')
libdir = $(prefix)/$(lib)
bindir = $(prefix)/bin

export DESTDIR=
PYTHON=/usr/bin/python2.4
PYCFLAGS=-c 'import compileall, sys;[compileall.compile_dir(x, ddir=x.replace("$(DESTDIR)", ""), quiet=1) for x in sys.argv[1:]]'
VERSION=3.1

generated_files = bob/version.py

.PHONY: $(generated_files)

all: $(generated_files)

install: $(generated_files)
	mkdir -p "$(DESTDIR)$(libdir)/python2.4/site-packages/bob"
	cp -a bob/*.py "$(DESTDIR)$(libdir)/python2.4/site-packages/bob/"
	$(PYTHON) $(PYCFLAGS) "$(DESTDIR)$(libdir)/python2.4/site-packages/bob"
	$(PYTHON) -O $(PYCFLAGS) "$(DESTDIR)$(libdir)/python2.4/site-packages/bob"
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
