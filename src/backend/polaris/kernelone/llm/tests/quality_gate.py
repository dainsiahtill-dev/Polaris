"""LLM module quality gate.

Run all quality checks for the LLM module:
- Type checking with mypy
- Complexity analysis with radon
- Test coverage with pytest-cov
- All unit tests

Usage:
    python quality_gate.py
    python quality_gate.py --skip-coverage  # Skip coverage for faster runs
    python quality_gate.py --module polaris.kernelone.llm.reasoning  # Target specific module
"""

from __future__ import annotations

import subprocess
import sys

# Module paths to check
DEFAULT_TARGETS = [
    "polaris/kernelone/llm",
]

# Minimum coverage threshold (percentage)
MIN_COVERAGE = 80


def run_command(cmd: list[str], description: str) -> bool:
    """Run a command and report results.

    Args:
        cmd: Command and arguments to run
        description: Human-readable description of the check

    Returns:
        True if the command succeeded, False otherwise
    """
    print(f"\n{'=' * 60}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print(f"{'=' * 60}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        print(f"FAILED: {description}")
        return False

    print(f"PASSED: {description}")
    return True


def check_mypy(targets: list[str]) -> bool:
    """Run mypy type checking.

    Args:
        targets: List of module paths to check

    Returns:
        True if all checks pass
    """
    cmd = [
        sys.executable,
        "-m",
        "mypy",
        *targets,
        "--strict",
        "--ignore-missing-imports",
    ]
    return run_command(cmd, "Type Check (mypy --strict)")


def check_radon(targets: list[str]) -> bool:
    """Run radon complexity analysis.

    Args:
        targets: List of module paths to check

    Returns:
        True if all checks pass
    """
    cmd = [
        sys.executable,
        "-m",
        "radon",
        "cc",
        *targets,
        "-a",
        "-nc",
    ]
    return run_command(cmd, "Complexity Check (radon)")


def check_tests(targets: list[str], skip_coverage: bool = False) -> bool:
    """Run all tests with optional coverage.

    Args:
        targets: List of module paths to test
        skip_coverage: If True, skip coverage reporting

    Returns:
        True if all tests pass
    """
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "-v",
    ]

    if not skip_coverage:
        cmd.extend(
            [
                "--cov=polaris.kernelone.llm",
                f"--cov-fail-under={MIN_COVERAGE}",
                "--cov-report=term-missing",
            ]
        )

    return run_command(cmd, "Unit Tests (pytest)")


def check_ruff() -> bool:
    """Run ruff linting.

    Returns:
        True if linting passes
    """
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "polaris/kernelone/llm",
    ]
    return run_command(cmd, "Linting (ruff)")


def check_pyright(targets: list[str]) -> bool:
    """Run pyright type checking (if available).

    Args:
        targets: List of module paths to check

    Returns:
        True if check passes or pyright is not installed
    """
    cmd = [
        "npx",
        "pyright",
        *targets,
    ]
    try:
        return run_command(cmd, "Type Check (pyright)")
    except FileNotFoundError:
        print("pyright not installed, skipping...")
        return True


def main() -> int:
    """Run all quality gate checks.

    Returns:
        0 if all checks pass, 1 otherwise
    """
    import argparse

    parser = argparse.ArgumentParser(description="LLM Module Quality Gate")
    parser.add_argument(
        "--skip-coverage",
        action="store_true",
        help="Skip coverage reporting for faster runs",
    )
    parser.add_argument(
        "--skip-mypy",
        action="store_true",
        help="Skip mypy type checking",
    )
    parser.add_argument(
        "--skip-radon",
        action="store_true",
        help="Skip complexity analysis",
    )
    parser.add_argument(
        "--skip-ruff",
        action="store_true",
        help="Skip ruff linting",
    )
    parser.add_argument(
        "--module",
        action="append",
        dest="modules",
        help="Specific module to check (can be repeated)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all checks including optional ones",
    )

    args = parser.parse_args()

    # Determine targets
    targets = args.modules or DEFAULT_TARGETS

    print("=" * 60)
    print("LLM Module Quality Gate")
    print("=" * 60)
    print(f"Targets: {targets}")
    print(f"Skip coverage: {args.skip_coverage}")
    print(f"Minimum coverage: {MIN_COVERAGE}%")

    results: list[tuple[str, bool]] = []

    # Run checks
    checks = [
        ("ruff", lambda: check_ruff() if not args.skip_ruff else True),
        ("mypy", lambda: check_mypy(targets) if not args.skip_mypy else True),
        ("radon", lambda: check_radon(targets) if not args.skip_radon else True),
        ("tests", lambda: check_tests(targets, args.skip_coverage)),
    ]

    if args.all:
        checks.append(("pyright", lambda: check_pyright(targets)))

    for name, check_fn in checks:
        try:
            passed = check_fn()
            results.append((name, passed))
        except (RuntimeError, ValueError) as e:
            print(f"Error running {name}: {e}")
            results.append((name, False))

    # Summary
    print("\n" + "=" * 60)
    print("QUALITY GATE SUMMARY")
    print("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "PASSED" if passed else "FAILED"
        symbol = "[OK]" if passed else "[FAIL]"
        print(f"{symbol} {name}: {status}")
        if not passed:
            all_passed = False

    print("=" * 60)

    if all_passed:
        print("All quality gate checks PASSED!")
        return 0
    else:
        print("Some quality gate checks FAILED!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
