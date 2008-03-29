export VERSION=3.01

libdir=/usr/lib
bindir=/usr/bin

generated_files = bob/version.py

all: $(generated_files)

install: $(generated_files)
	mkdir -p "$(DESTDIR)$(libdir)/python2.4/site-packages/bob"
	cp -a bob/*.py "$(DESTDIR)$(libdir)/python2.4/site-packages/bob/"
	python2.4 $(libdir)/python2.4/compileall.py "$(DESTDIR)$(libdir)/python2.4/site-packages/bob"
	python2.4 -O $(libdir)/python2.4/compileall.py "$(DESTDIR)$(libdir)/python2.4/site-packages/bob"
	install -D -m755 bin/bob $(DESTDIR)$(bindir)/bob

clean:
	rm -f $(generated_files)


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
