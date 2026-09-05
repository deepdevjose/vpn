.PHONY: test lint security-check

test:
	python -m unittest discover

lint:
	python -m py_compile connectvpn src/connectvpn/app.py
	bash -n install.sh scripts/install.sh scripts/uninstall.sh scripts/check_no_sensitive_files.sh

security-check:
	./scripts/check_no_sensitive_files.sh
