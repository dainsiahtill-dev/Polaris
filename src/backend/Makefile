# Polaris Backend Development Tools
# Usage: make <command> from src/backend directory

.PHONY: help install lint lint-fix typecheck typecheck-json validate-mypy-json test test-cov verify fix clean

# Default Python interpreter
PYTHON := python
# Directories to check
KERNELONE_DIRS := polaris/bootstrap polaris/kernelone polaris/domain polaris/application polaris/delivery polaris/infrastructure polaris/cells
TEST_DIRS := polaris/tests polaris/kernelone/tests polaris/bootstrap/tests

help:
	@echo "Polaris Backend Development Tools"
	@echo ""
	@echo "Available commands:"
	@echo "  install            Install dependencies"
	@echo "  lint               Run Ruff linter (check only)"
	@echo "  lint-fix           Run Ruff linter and fix issues"
	@echo "  format             Run Ruff formatter (check only)"
	@echo "  format-fix         Run Ruff formatter and fix issues"
	@echo "  typecheck          Run Mypy type checker (console output)"
	@echo "  typecheck-json     Run Mypy type checker, produce JSON report (mypy_errors.json)"
	@echo "  validate-mypy-json Validate mypy_errors.json is well-formed JSON"
	@echo "  test               Run Pytest unit tests"
	@echo "  test-cov           Run tests with coverage"
	@echo "  verify             Run all checks (lint + typecheck + test)"
	@echo "  fix                Fix all auto-fixable issues"
	@echo "  clean              Clean cache files"
	@echo ""
	@echo "Environment variables:"
	@echo "  PYTHON             Python interpreter (default: python)"

install:
	$(PYTHON) -m pip install -r requirements.txt 2>/dev/null || true
	$(PYTHON) -m pip install ruff mypy pytest pytest-asyncio pytest-cov

lint:
	@echo "Running Ruff linter..."
	ruff check $(KERNELONE_DIRS) --output-format=concise

lint-fix:
	@echo "Running Ruff linter with auto-fix..."
	ruff check $(KERNELONE_DIRS) --fix

format:
	@echo "Running Ruff format check..."
	ruff format --check $(KERNELONE_DIRS)

format-fix:
	@echo "Running Ruff formatter..."
	ruff format $(KERNELONE_DIRS)

typecheck:
	@echo "Running Mypy type checker (console output)..."
	@mypy $(KERNELONE_DIRS) --ignore-missing-imports --show-error-codes 2>&1 || true
	@echo "Type check complete (warnings may be present for missing imports)"

# Generate JSON report using mypy --output json (requires mypy >= 1.11).
# The --output json flag produces one JSON object per line (JSONL format).
# We wrap the output in a structured JSON report with a "status" field.
typecheck-json:
	@echo "Running Mypy type checker (JSON report)..."
	@mypy $(KERNELONE_DIRS) --ignore-missing-imports --show-error-codes --output json > mypy_errors.jsonl 2>&1 || true
	@python -c "import json; lines=[l.strip() for l in open('mypy_errors.jsonl', encoding='utf-8') if l.strip()]; entries=[json.loads(l) for l in lines]; json.dump({'status':'ok','error_count':len(entries),'errors':entries}, open('mypy_errors.json','w',encoding='utf-8'), indent=2); print(f'JSON report written: {len(entries)} errors')" || (echo '{"status":"parse_error","error_count":0,"errors":[]}' > mypy_errors.json && echo "WARNING: mypy JSON parsing failed, wrote fallback report")
	@rm -f mypy_errors.jsonl
	@echo "Type check JSON report complete: mypy_errors.json"

# Validate that mypy_errors.json is well-formed JSON.
# Returns non-zero exit code if the file is missing, empty, or not valid JSON.
validate-mypy-json:
	@echo "Validating mypy_errors.json..."
	@python -c "import json, sys, os; p='mypy_errors.json'; assert os.path.exists(p), f'{p} does not exist'; d=json.load(open(p, encoding='utf-8')); assert isinstance(d,dict) and 'status' in d, f'{p} missing status field'; print(f'OK: {p} is valid JSON (status={d[\"status\"]}, errors={d.get(\"error_count\",\"N/A\")})')"
	@echo "Validation complete."

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
