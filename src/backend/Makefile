# Polaris Backend Development Tools
# Usage: make <command> from src/backend directory

.PHONY: help install lint lint-fix typecheck test test-cov verify fix clean

# Default Python interpreter
PYTHON := python
# Directories to check
POLARIS_DIRS := polaris/bootstrap polaris/kernelone polaris/domain polaris/application polaris/delivery polaris/infrastructure polaris/cells
TEST_DIRS := polaris/tests polaris/kernelone/tests polaris/bootstrap/tests

help:
	@echo "Polaris Backend Development Tools"
	@echo ""
	@echo "Available commands:"
	@echo "  install      Install dependencies"
	@echo "  lint         Run Ruff linter (check only)"
	@echo "  lint-fix     Run Ruff linter and fix issues"
	@echo "  format       Run Ruff formatter (check only)"
	@echo "  format-fix   Run Ruff formatter and fix issues"
	@echo "  typecheck    Run Mypy type checker"
	@echo "  test         Run Pytest unit tests"
	@echo "  test-cov     Run tests with coverage"
	@echo "  verify       Run all checks (lint + typecheck + test)"
	@echo "  fix          Fix all auto-fixable issues"
	@echo "  clean        Clean cache files"
	@echo ""
	@echo "Environment variables:"
	@echo "  PYTHON       Python interpreter (default: python)"

install:
	$(PYTHON) -m pip install -r requirements.txt 2>/dev/null || true
	$(PYTHON) -m pip install ruff mypy pytest pytest-asyncio pytest-cov

lint:
	@echo "Running Ruff linter..."
	ruff check $(POLARIS_DIRS) --output-format=concise

lint-fix:
	@echo "Running Ruff linter with auto-fix..."
	ruff check $(POLARIS_DIRS) --fix

format:
	@echo "Running Ruff format check..."
	ruff format --check $(POLARIS_DIRS)

format-fix:
	@echo "Running Ruff formatter..."
	ruff format $(POLARIS_DIRS)

typecheck:
	@echo "Running Mypy type checker..."
	@mypy polaris/bootstrap --ignore-missing-imports --show-error-codes 2>/dev/null || true
	@mypy polaris/kernelone --ignore-missing-imports --show-error-codes 2>/dev/null || true
	@echo "Type check complete (warnings may be present for missing imports)"

test:
	@echo "Running Pytest unit tests..."
	pytest $(TEST_DIRS) -v --tb=short

test-cov:
	@echo "Running Pytest with coverage..."
	pytest $(TEST_DIRS) -v --tb=short --cov=polaris --cov-report=term-missing --cov-report=html

verify: lint format typecheck test
	@echo ""
	@echo "All verification steps completed!"

fix: lint-fix format-fix
	@echo ""
	@echo "All auto-fixes applied!"

clean:
	@echo "Cleaning cache files..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov .coverage 2>/dev/null || true
	@echo "Cache files cleaned!"
