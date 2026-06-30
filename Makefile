# Makefile for claude-bug-squash.
# The engine is stdlib-only (zero runtime deps); dev work needs just pytest.

PYTHON ?= python3

.PHONY: install dev test lint clean help

help:
	@echo "Targets:"
	@echo "  make install   install the package ($(PYTHON) -m pip install .)"
	@echo "  make dev       editable install with dev extras (pytest)"
	@echo "  make test      run the test suite (pytest -q)"
	@echo "  make lint      run ruff if available, else skip"
	@echo "  make clean     remove build artifacts and caches"

install:
	$(PYTHON) -m pip install .

dev:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -q

lint:
	@if command -v ruff >/dev/null 2>&1; then \
		echo "→ ruff check"; ruff check .; \
	else \
		echo "ruff not installed — skipping lint (pip install ruff to enable)"; \
	fi

clean:
	rm -rf build dist *.egg-info src/*.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
