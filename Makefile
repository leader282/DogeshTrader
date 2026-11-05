.DEFAULT_GOAL := help
PY ?= .venv/bin/python
BIN := $(dir $(PY))

.PHONY: help install hooks lint format typecheck test cov check clean

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Create .venv and install the package with dev extras
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -e ".[dev]"

hooks: ## Install the git pre-commit hooks
	$(BIN)pre-commit install

lint: ## Ruff lint and format check (no changes)
	$(BIN)ruff check dogeshtrader tests
	$(BIN)ruff format --check dogeshtrader tests

format: ## Apply ruff fixes and formatting
	$(BIN)ruff check --fix dogeshtrader tests
	$(BIN)ruff format dogeshtrader tests

typecheck: ## mypy over the package
	$(BIN)mypy dogeshtrader

test: ## Fast test run
	$(PY) -m pytest -q

cov: ## Tests with the enforced coverage floor
	$(PY) -m pytest -q --cov --cov-report=term-missing

check: lint typecheck cov ## Everything CI runs

clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist *.egg-info
