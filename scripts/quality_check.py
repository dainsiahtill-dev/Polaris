"""Polaris Quality Check Script."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_COVERAGE_THRESHOLD = 90

def run_command(cmd, cwd=None):
    try:
        result = subprocess.run(cmd, cwd=cwd or PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8")
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)

def check_ruff_lint(fix=False):
    print("Ruff Lint Check...")
    cmd = ["ruff", "check"]
    if fix: cmd.append("--fix")
    cmd.extend(["src/backend/polaris", "tests"])
    rc, out, err = run_command(cmd)
    if rc == 0: print("  OK")
    else: print(f"  FAIL: {out}")
    return rc

def check_ruff_format(check_only=True):
    print("Ruff Format Check...")
    cmd = ["ruff", "format"]
    if check_only: cmd.append("--check")
    cmd.extend(["src/backend/polaris", "tests", "scripts"])
    rc, out, err = run_command(cmd)
    if rc == 0: print("  OK")
    else: print("  FAIL")
    return rc

def check_mypy():
    print("MyPy Check...")
    rc, out, err = run_command(["mypy", "--strict", "src/backend/polaris"])
    if rc == 0: print("  OK")
    else: print(f"  FAIL: {out}")
    return rc

def check_pytest():
    print("Pytest...")
    rc, out, err = run_command(["pytest", "-v", "--tb=short"])
    if rc == 0: print("  OK")
    else: print(f"  FAIL")
    return rc

def main():
    parser = argparse.ArgumentParser(description="Quality Check")
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    errors = 0
    errors += check_ruff_lint(fix=args.fix)
    errors += check_ruff_format(check_only=not args.fix)
    errors += check_mypy()
    if not args.skip_tests:
        errors += check_pytest()
    if errors == 0:
        print("\nALL CHECKS PASSED")
    else:
        print(f"\n{errors} CHECK(S) FAILED")
    return 1 if errors > 0 else 0

if __name__ == "__main__":
    sys.exit(main())
