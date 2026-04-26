#!/usr/bin/env python3
"""Cell Internal Import Checker - Pre-commit hook for ACGA 2.0 compliance.

This script checks for forbidden internal imports between Cells modules.
According to ACGA 2.0, Cells should communicate via public contracts,
not by importing from other Cells' internal modules.

Usage:
    python scripts/check_cell_imports.py [file1.py file2.py ...]

    If no files specified, checks all staged files from git.

Exit codes:
    0 - No violations found
    1 - Violations found (blocked)
    2 - Invalid usage

Architecture:
    cells/roles/*/internal/*  -> Should NOT import from other cells/*/internal/*
    cells/roles/*/internal/*  -> CAN import from cells/*/public/* (contracts)
    cells/roles/*/internal/*  -> CAN import from cells/*/public/contracts/*
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# Patterns for forbidden internal imports
# These patterns match imports from other Cells' internal modules
FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Match "from polaris.cells.<role1>.internal" or "from polaris.cells.<role1>.internal."
    # where the imported module is different from the current module's role
    (
        "cell_internal_import",
        re.compile(r"from\s+polaris\.cells\.\w+\.internal\."),
    ),
    (
        "cell_internal_relative_import",
        re.compile(r"from\s+\.\.\.internal\."),
    ),
]

# Allowed patterns that are not violations
ALLOWED_PATTERNS: list[re.Pattern[str]] = [
    # Allow imports from own internal modules
    re.compile(r"from\s+polaris\.cells\.(\w+)\.internal\s+import\s+.*"),
    # Allow public contracts
    re.compile(r"from\s+polaris\.cells\.\w+\.public\."),
    re.compile(r"from\s+polaris\.cells\.\w+\.public\.contracts\."),
    # Allow kernelone ports
    re.compile(r"from\s+polaris\.kernelone\.ports\."),
    # Allow polaris.cells.adapters
    re.compile(r"from\s+polaris\.cells\.adapters\."),
]


class Violation(NamedTuple):
    """Represents an import violation."""

    file_path: str
    line_number: int
    line_content: str
    violation_type: str
    message: str


def check_file(file_path: Path) -> list[Violation]:
    """Check a single file for internal import violations.

    Args:
        file_path: Path to the Python file to check.

    Returns:
        List of Violation objects found in the file.
    """
    violations: list[Violation] = []

    # Skip if file doesn't exist or isn't a Python file
    if not file_path.exists() or file_path.suffix != ".py":
        return violations

    try:
        content = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return violations

    lines = content.split("\n")

    for line_num, line in enumerate(lines, start=1):
        # Skip comments and empty lines
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Check each forbidden pattern
        for pattern_name, pattern in FORBIDDEN_PATTERNS:
            if pattern.search(line):
                # Check if this is an allowed pattern
                is_allowed = any(allowed.match(line) for allowed in ALLOWED_PATTERNS)
                if not is_allowed:
                    violations.append(
                        Violation(
                            file_path=str(file_path),
                            line_number=line_num,
                            line_content=line.strip(),
                            violation_type=pattern_name,
                            message=(
                                f"ACGA 2.0 Violation: Cells should not import from "
                                f"other Cells' internal modules. Use public contracts instead. "
                                f"Found: {line.strip()}"
                            ),
                        )
                    )

    return violations


def get_staged_files() -> list[Path]:
    """Get list of staged Python files from git.

    Returns:
        List of Path objects for staged .py files.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True,
        )
        files = [Path(f) for f in result.stdout.strip().split("\n") if f.endswith(".py") and Path(f).exists()]
        return files
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def main() -> int:
    """Main entry point for the checker.

    Returns:
        0 if no violations, 1 if violations found, 2 for invalid usage.
    """
    # Get files to check
    if len(sys.argv) > 1:
        # Check specific files from command line
        files = [Path(f) for f in sys.argv[1:] if f.endswith(".py")]
        if not files:
            print("Error: No Python files specified", file=sys.stderr)
            return 2
    else:
        # Check staged files
        files = get_staged_files()
        if not files:
            print("No staged Python files to check.")
            return 0

    # Check all files
    all_violations: list[Violation] = []
    for file_path in files:
        violations = check_file(file_path)
        all_violations.extend(violations)

    # Report results
    if all_violations:
        print("=" * 80)
        print("ACGA 2.0 Cell Internal Import Violations Detected!")
        print("=" * 80)
        print()

        # Group violations by file
        by_file: dict[str, list[Violation]] = {}
        for v in all_violations:
            by_file.setdefault(v.file_path, []).append(v)

        for file_path, violations in by_file.items():  # type: ignore[assignment]
            print(f"File: {file_path}")
            print("-" * 80)
            for v in violations:
                print(f"  Line {v.line_number}: {v.violation_type}")
                print(f"    {v.line_content}")
                print("    Hint: Use public contracts instead of internal imports")
                print()
            print()

        print("=" * 80)
        print(f"Total violations: {len(all_violations)}")
        print()
        print("Fix: Replace internal imports with public contract imports.")
        print("Example:")
        print("  BEFORE: from polaris.cells.roles.kernel.internal import SomeClass")
        print("  AFTER:  from polaris.cells.roles.kernel.public.contracts import SomeClass")
        print("=" * 80)

        return 1

    print("No ACGA 2.0 internal import violations detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
