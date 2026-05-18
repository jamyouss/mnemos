# Mnemos — developer Makefile
# Run `make help` for the cheat-sheet.

VENV       := venv
PYTHON     := python3
BIN        := $(VENV)/bin
COMPOSE    := docker compose
COMPOSE_DEV := docker compose -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: help \
        venv install install-cli install-dev reinstall clean activate \
        test test-unit test-fast lint \
        up up-dev up-llm down restart logs build-dev rebuild-dev \
        status doctor demo

help: ## Show this help
	@echo "Mnemos — common tasks"
	@echo ""
	@echo "Setup:"
	@grep -E '^[a-z-]+:.*## .* \[setup\]' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/; s/ \[setup\]//'
	@echo ""
	@echo "Run:"
	@grep -E '^[a-z-]+:.*## .* \[run\]' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/; s/ \[run\]//'
	@echo ""
	@echo "Develop:"
	@grep -E '^[a-z-]+:.*## .* \[dev\]' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/; s/ \[dev\]//'
	@echo ""
	@echo "Test:"
	@grep -E '^[a-z-]+:.*## .* \[test\]' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/; s/ \[test\]//'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

venv: ## Create the project venv [setup]
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	@$(BIN)/pip install --upgrade pip >/dev/null

install: venv install-cli ## Create venv + install ALL packages in editable mode [setup]
	@$(BIN)/pip install -e ./packages/core -e ./packages/eval >/dev/null
	@echo ""
	@echo "Mnemos installed. Activate with:"
	@echo "  source $(VENV)/bin/activate"
	@echo "Then:    mnemos status   (after \`make up\`)"

install-cli: venv ## Install only the CLI package [setup]
	@$(BIN)/pip install -e ./cli >/dev/null

install-dev: install ## Install + pytest + test tooling [setup]
	@$(BIN)/pip install pytest pytest-asyncio fastapi pydantic-settings watchdog >/dev/null
	@echo "Dev tools installed."

reinstall: clean install ## Wipe venv + reinstall everything [setup]

clean: ## Remove the venv [setup]
	rm -rf $(VENV)

activate: ## Print the activation command [setup]
	@echo "source $(VENV)/bin/activate"

# ---------------------------------------------------------------------------
# Run (Docker)
# ---------------------------------------------------------------------------

up: ## Start core stack from published images [run]
	$(COMPOSE) up -d

up-dev: ## Start stack with locally-built images [run]
	$(COMPOSE_DEV) up -d --build

up-llm: ## Start stack + the bundled Ollama container [run]
	$(COMPOSE) --profile llm up -d

down: ## Stop the stack [run]
	$(COMPOSE) down

restart: ## Restart the stack [run]
	$(COMPOSE) restart rag-server

logs: ## Tail rag-server logs [run]
	$(COMPOSE) logs -f rag-server

build-dev: ## Rebuild local dev images [dev]
	$(COMPOSE_DEV) build

rebuild-dev: build-dev ## Rebuild + restart with local images [dev]
	$(COMPOSE_DEV) up -d

# ---------------------------------------------------------------------------
# Health / demo
# ---------------------------------------------------------------------------

status: install-cli ## Show health + collection counts via the CLI [run]
	@$(BIN)/mnemos status

doctor: install-cli ## Run end-to-end health checks (server, LLM, mounts, search) [run]
	@$(BIN)/mnemos doctor

demo: install-cli ## Index a tiny bundled example repo, run a sample search [run]
	@bash scripts/run-demo.sh

# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

test: install-dev ## Run all unit tests [test]
	@$(BIN)/python -m pytest tests/ -q \
	  --ignore=tests/test_api.py \
	  --ignore=tests/test_cli.py \
	  --ignore=tests/test_server.py \
	  --ignore=tests/test_mcp_integration.py \
	  --ignore=tests/test_mcp_tools.py \
	  --ignore=tests/test_extract_api.py

test-unit: test ## Alias for `make test` [test]

test-fast: install-dev ## Quick subset (no transformers / no qdrant) [test]
	@$(BIN)/python -m pytest tests/test_projects_tags.py tests/test_collections.py \
	  tests/test_sparse.py tests/test_should_skip.py -q

lint: install-dev ## Compile-check every .py file (catches obvious syntax breakage) [test]
	@$(BIN)/python -m compileall -q packages/ server/ watcher/ cli/ tests/
