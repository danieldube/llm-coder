.PHONY: install uninstall check

install:
	./install.sh

uninstall:
	./uninstall.sh

check:
	./tests/static-checks.sh
