#!/usr/bin/env python3
"""Polaris Backend Development Tools.

Cross-platform development utility script for running linting,
type checking, and tests.

Usage:
    python scripts/dev-tools.py <command>

Commands:
    lint         Run Ruff linter (check only)
    lint-fix     Run Ruff linter and fix issues
    format       Run Ruff formatter (check only)
    format-fix   Run Ruff formatter and fix issues
    typecheck    Run Mypy type checker
    test         Run Pytest unit tests
    test-cov     Run tests with coverage
    verify       Run all checks (lint + typecheck + test)
    fix          Fix all auto-fixable issues
    clean        Clean cache files
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Configuration
KERNELONE_DIRS = [
    "polaris/bootstrap",
    "polaris/kernelone",
    "polaris/domain",
    "polaris/application",
    "polaris/delivery",
    "polaris/infrastructure",
    "polaris/cells",
]
TEST_DIRS = [
    "polaris/tests",
    "polaris/kernelone/tests",
    "polaris/bootstrap/tests",
]


def run_command(cmd: list[str], description: str, check: bool = True) -> int:
    """Run a command and print status."""
    print(f"\n{'=' * 60}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print("=" * 60)

    try:
        result = subprocess.run(cmd, check=check)
        return result.returncode
    except FileNotFoundError as e:
        print(f"Error: Command not found - {e}")
        return 1
    except subprocess.CalledProcessError as e:
        print(f"Error: Command failed with return code {e.returncode}")
        return e.returncode


def check_tool(tool: str) -> bool:
    """Check if a tool is installed."""
    return shutil.which(tool) is not None


def cmd_lint(args: argparse.Namespace) -> int:
    """Run Ruff linter."""
    if not check_tool("ruff"):
        print("Error: ruff not found. Install with: pip install ruff")
        return 1

    cmd = ["ruff", "check"] + KERNELONE_DIRS + ["--output-format=concise"]
    return run_command(cmd, "Ruff linter check")


def cmd_lint_fix(args: argparse.Namespace) -> int:
    """Run Ruff linter with auto-fix."""
    if not check_tool("ruff"):
        print("Error: ruff not found. Install with: pip install ruff")
        return 1

    cmd = ["ruff", "check"] + KERNELONE_DIRS + ["--fix"]
    return run_command(cmd, "Ruff linter with auto-fix")


def cmd_format(args: argparse.Namespace) -> int:
    """Run Ruff format check."""
    if not check_tool("ruff"):
        print("Error: ruff not found. Install with: pip install ruff")
        return 1

    cmd = ["ruff", "format", "--check"] + KERNELONE_DIRS
    return run_command(cmd, "Ruff format check")


def cmd_format_fix(args: argparse.Namespace) -> int:
    """Run Ruff formatter."""
    if not check_tool("ruff"):
        print("Error: ruff not found. Install with: pip install ruff")
        return 1

    cmd = ["ruff", "format"] + KERNELONE_DIRS
    return run_command(cmd, "Ruff formatter")


def cmd_typecheck(args: argparse.Namespace) -> int:
    """Run Mypy type checker."""
    if not check_tool("mypy"):
        print("Error: mypy not found. Install with: pip install mypy")
        return 1

    dirs = ["polaris/bootstrap", "polaris/kernelone"]
    for dir_path in dirs:
        cmd = ["mypy", dir_path, "--ignore-missing-imports", "--show-error-codes"]
        run_command(cmd, f"Mypy type check for {dir_path}", check=False)

    return 0


def cmd_test(args: argparse.Namespace) -> int:
    """Run Pytest unit tests."""
    if not check_tool("pytest"):
        print("Error: pytest not found. Install with: pip install pytest")
        return 1

    cmd = ["pytest"] + TEST_DIRS + ["-v", "--tb=short"]
    return run_command(cmd, "Pytest unit tests")


def cmd_test_cov(args: argparse.Namespace) -> int:
    """Run Pytest with coverage."""
    if not check_tool("pytest"):
        print("Error: pytest not found. Install with: pip install pytest pytest-cov")
        return 1

    cmd = (
        ["pytest"] + TEST_DIRS + ["-v", "--tb=short", "--cov=polaris", "--cov-report=term-missing", "--cov-report=html"]
    )
    return run_command(cmd, "Pytest with coverage")


def cmd_verify(args: argparse.Namespace) -> int:
    """Run all verification steps."""
    print("\n" + "=" * 60)
    print("Running full verification pipeline")
    print("=" * 60)

    results = []

    # Lint
    results.append(("lint", cmd_lint(args)))

    # Format check
    results.append(("format", cmd_format(args)))

    # Type check
    results.append(("typecheck", cmd_typecheck(args)))

    # Test
    results.append(("test", cmd_test(args)))

    # Summary
    print("\n" + "=" * 60)
    print("Verification Summary")
    print("=" * 60)
    all_passed = True
    for name, code in results:
        status = "PASS" if code == 0 else "FAIL"
        print(f"  {name}: {status}")
        if code != 0:
            all_passed = False

    if all_passed:
        print("\nAll verification steps passed!")
        return 0
    else:
        print("\nSome verification steps failed.")
        return 1


def cmd_fix(args: argparse.Namespace) -> int:
    """Fix all auto-fixable issues."""
    print("\n" + "=" * 60)
    print("Running auto-fix for all issues")
    print("=" * 60)

    # Lint fix
    cmd_lint_fix(args)

    # Format fix
    cmd_format_fix(args)

    print("\nAll auto-fixes applied!")
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    """Clean cache files."""
    print("\n" + "=" * 60)
    print("Cleaning cache files")
    print("=" * 60)

    patterns_to_clean = [
        "**/__pycache__",
        "**/*.pyc",
        "**/.pytest_cache",
        "**/.mypy_cache",
        "**/.ruff_cache",
        "htmlcov",
        ".coverage",
    ]

    backend_dir = Path(__file__).parent.parent

    for pattern in patterns_to_clean:
        if pattern.endswith("__pycache__") or pattern.startswith("."):
            # Directory patterns
            for path in backend_dir.rglob(pattern):
                if path.is_dir():
                    print(f"  Removing directory: {path.relative_to(backend_dir)}")
                    try:
                        import shutil

                        shutil.rmtree(path)
                    except OSError as e:
                        print(f"    Error removing {path}: {e}")
        else:
            # File patterns
            for path in backend_dir.rglob(pattern):
                if path.is_file():
                    print(f"  Removing file: {path.relative_to(backend_dir)}")
                    try:
                        path.unlink()
                    except OSError as e:
                        print(f"    Error removing {path}: {e}")

    print("\nCache files cleaned!")
    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Polaris Backend Development Tools",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/dev-tools.py lint
    python scripts/dev-tools.py verify
    python scripts/dev-tools.py fix
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # lint
    subparsers.add_parser("lint", help="Run Ruff linter (check only)")

    # lint-fix
    subparsers.add_parser("lint-fix", help="Run Ruff linter and fix issues")

    # format
    subparsers.add_parser("format", help="Run Ruff formatter (check only)")

    # format-fix
    subparsers.add_parser("format-fix", help="Run Ruff formatter and fix issues")

    # typecheck
    subparsers.add_parser("typecheck", help="Run Mypy type checker")

    # test
    subparsers.add_parser("test", help="Run Pytest unit tests")

    # test-cov
    subparsers.add_parser("test-cov", help="Run tests with coverage")

    # verify
    subparsers.add_parser("verify", help="Run all checks (lint + typecheck + test)")

    # fix
    subparsers.add_parser("fix", help="Fix all auto-fixable issues")

    # clean
    subparsers.add_parser("clean", help="Clean cache files")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Change to backend directory
    backend_dir = Path(__file__).parent.parent
    import os

    os.chdir(backend_dir)

    # Dispatch to command handler
    commands = {
        "lint": cmd_lint,
        "lint-fix": cmd_lint_fix,
        "format": cmd_format,
        "format-fix": cmd_format_fix,
        "typecheck": cmd_typecheck,
        "test": cmd_test,
        "test-cov": cmd_test_cov,
        "verify": cmd_verify,
        "fix": cmd_fix,
        "clean": cmd_clean,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args)
    else:
        print(f"Unknown command: {args.command}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
