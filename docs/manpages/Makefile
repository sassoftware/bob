
VERSION=4.2
SUBCOMMANDS=jenkins deps

.PHONY: all clean rm_phony_commands phony_commands troff 

all: troff rm_phony_commands

clean: rm_phony_commands
	rm *.1

rm_phony_commands:
	rm ./bob
	for subcommand in $(SUBCOMMANDS); do \
	    rm ./bob-$$subcommand; \
	done

phony_commands:
	printf "%s\n\n%s\n" "#/bin/sh" 'PYTHONPATH=../.. python ../../bin/bob "$$@"' \
	    > ./bob
	chmod 700 ./bob
	for subcommand in $(SUBCOMMANDS); do \
	    printf "%s\n\n%s\n" "#/bin/sh" \
	    	"PYTHONPATH=../.. python ../../bin/bob-$$subcommand \"\$$@\"" \
		> ./bob-$$subcommand; \
	    chmod 700 ./bob-$$subcommand; \
	done

troff: phony_commands
	help2man --no-info --include=bob.help2man --include=bob-common.help2man --name="Bob the Constructinator version $(VERSION)" \
	    ./bob > bob.1
	for subcommand in $(SUBCOMMANDS); do \
	     help2man --no-info --include=bob-common.help2man \
	     	--opt-include=bob-$$subcommand.help2man ./bob-$$subcommand \
		> bob-$$subcommand.1; \
	done

html:
	mkdir ./html
	groff -t -mandoc -Thtml bob.1 > ./html/bob-$(VERSION).html
	groff -t -mandoc -Thtml bob-plans.1 > ./html/bob-plans-$(VERSION).html
	for subcommand in $(SUBCOMMANDS); do \
		groff -t -mandoc -Thtml bob-$$subcommand.1 > ./html/bob-$$subcommand-$(VERSION).html; \
	done

rm_html:
	rm -r ./html

