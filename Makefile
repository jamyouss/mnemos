VENV := venv
PYTHON := python3
BIN := $(VENV)/bin

.PHONY: help venv install install-cli clean reinstall activate

help:
	@echo "Targets:"
	@echo "  make venv         - Create Python venv at ./$(VENV)"
	@echo "  make install      - Create venv and install mnemos CLI"
	@echo "  make install-cli  - Install mnemos CLI into existing venv"
	@echo "  make reinstall    - Wipe venv and reinstall"
	@echo "  make clean        - Remove venv"
	@echo "  make activate     - Print command to activate venv"

venv:
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@$(BIN)/pip install --upgrade pip >/dev/null

install: venv install-cli
	@echo ""
	@echo "Mnemos CLI installed. Activate with:"
	@echo "  source $(VENV)/bin/activate"
	@echo "Then run: mnemos --help"

install-cli:
	@$(BIN)/pip install -e ./cli

reinstall: clean install

clean:
	rm -rf $(VENV)

activate:
	@echo "source $(VENV)/bin/activate"
