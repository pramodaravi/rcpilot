# rcpilot — convenience targets.
#
# `make help` lists everything. Defaults assume Python 3.10+ on PATH.

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show this help.
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install:  ## Install rcpilot in editable mode (no extras).
	$(PIP) install -e .

.PHONY: install-dev
install-dev:  ## Install with dev + cockpit extras (pygame, pytest, ruff).
	$(PIP) install -e .[dev,cockpit]

.PHONY: test
test:  ## Run the full pytest suite.
	$(PYTHON) -m pytest

.PHONY: test-fast
test-fast:  ## Run tests except the network-loop integration test.
	$(PYTHON) -m pytest --ignore=tests/test_loop.py

.PHONY: lint
lint:  ## Lint with ruff (read-only).
	$(PYTHON) -m ruff check src tests

.PHONY: lint-fix
lint-fix:  ## Lint and auto-fix safe issues.
	$(PYTHON) -m ruff check --fix src tests

.PHONY: format
format:  ## Format with ruff.
	$(PYTHON) -m ruff format src tests

.PHONY: clean
clean:  ## Remove build/cache artifacts.
	rm -rf build/ dist/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true

# ---- Dev workflows -------------------------------------------------------

.PHONY: run-echo
run-echo:  ## Run the echo server with verbose logging.
	$(PYTHON) -m rcpilot.jetson.echo_server -v

.PHONY: run-sender
run-sender:  ## Run the control sender with verbose logging.
	$(PYTHON) -m rcpilot.cockpit.control_sender -v

.PHONY: identify-joystick
identify-joystick:  ## Print live joystick axis values to ID the wheel layout.
	$(PYTHON) -m rcpilot.cockpit.joystick

.PHONY: run-local-loop
run-local-loop:  ## Run the localhost echo + fake sender for ~5 seconds.
	bash scripts/dev/run_local_loop.sh
