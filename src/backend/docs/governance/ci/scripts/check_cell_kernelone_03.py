#!/usr/bin/env python3
"""Check CELL_KERNELONE_03: Cell boundaries respect KERNELONE kernel contracts.

Rule: CELL_KERNELONE_03
Severity: high
Description: >
    Dangerous command pattern detection must have a single canonical source
    in polaris.kernelone.security.dangerous_patterns.

Evidence:
    - docs/blueprints/CELLS_KERNELONE_INTEGRATION_BLUEPRINT_20260403.md
    - polaris/kernelone/security/dangerous_patterns.py
    - polaris/cells/roles/kernel/internal/policy/layer/budget.py
    - polaris/cells/roles/kernel/internal/policy/sandbox_policy.py

Compliance:
    - _DANGEROUS_PATTERNS must only exist in kernelone/security/
    - No duplicate pattern definitions in cells/

Violations:
    - Local _DANGEROUS_PATTERNS definitions in polaris/cells/
    - Independent dangerous pattern implementations outside kernelone

Exit codes:
    0 - All checks passed
    1 - Rule violation detected
    2 - Script error (e.g., missing dependencies)
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()  # docs/governance/ci/scripts
GOVERNANCE_DIR = SCRIPT_DIR.parent  # docs/governance
CI_DIR = GOVERNANCE_DIR.parent  # docs
DOCS_DIR = CI_DIR.parent  # docs
BACKEND_ROOT = DOCS_DIR.parent  # src/backend

sys.path.insert(0, str(SCRIPT_DIR))
from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker  # noqa: E402


class CellKernelone03Checker(FitnessRuleChecker):
    """Checker for CELL_KERNELONE_03: dangerous patterns canonical source."""

    # Patterns that indicate local dangerous pattern definitions
    DANGEROUS_PATTERN_PATTERNS = [
        re.compile(r"_DANGEROUS_PATTERNS\s*=\s*\["),
        re.compile(r"DANGEROUS_PATTERNS\s*=\s*\["),
        re.compile(r"DANGEROUS_PATTERNS\s*:\s*list"),
        re.compile(r"_DANGEROUS_PATTERNS\s*:\s*list"),
    ]

    # Regex patterns for detecting class attribute definitions
    CLASS_ATTR_PATTERN = re.compile(r"(?:dangerous_patterns|DANGEROUS_PATTERNS)\s*=\s*\[")
    MODULE_VAR_PATTERN = re.compile(r"(?:^|[^a-zA-Z_])_(?:DANGEROUS_PATTERNS|dangerous_patterns)\s*=\s*\[")

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__(workspace)
        self.cells_dir = self.workspace / "polaris" / "cells"
        self.kernelone_dir = self.workspace / "polaris" / "kernelone"
        self.canonical_patterns_path = self.kernelone_dir / "security" / "dangerous_patterns.py"

    def _parse_python_file(self, file_path: Path) -> ast.Module | None:
        """Parse a Python file into an AST."""
        try:
            with open(file_path, encoding="utf-8") as f:
                return ast.parse(f.read(), filename=str(file_path))
        except (OSError, SyntaxError):
            return None

    def _find_local_pattern_definitions(self, dir_path: Path) -> list[dict[str, Any]]:
        """Find local dangerous pattern definitions in a directory tree."""
        violations = []

        for py_file in dir_path.rglob("*.py"):
            # Skip the canonical source
            if py_file == self.canonical_patterns_path:
                continue

            # Skip test files (they may define test fixtures)
            if "test" in py_file.parts or "_fixture" in py_file.name:
                continue

            try:
                with open(py_file, encoding="utf-8") as f:
                    content = f.read()

                # Check for local pattern definitions
                for pattern in self.DANGEROUS_PATTERN_PATTERNS:
                    for match in pattern.finditer(content):
                        # Get line number
                        line_num = content[: match.start()].count("\n") + 1
                        violations.append(
                            {
                                "file": str(py_file.relative_to(self.workspace)),
                                "line": line_num,
                                "pattern": match.group(),
                            }
                        )

            except OSError:
                continue

        return violations

    def _verify_kernelone_has_patterns(self) -> bool:
        """Verify that the canonical kernelone patterns file exists and has content."""
        if not self.canonical_patterns_path.exists():
            return False

        try:
            with open(self.canonical_patterns_path, encoding="utf-8") as f:
                content = f.read()

            # Check for actual pattern definitions
            return "_DANGEROUS_PATTERNS" in content or "DANGEROUS_PATTERNS" in content
        except OSError:
            return False

    def check(self) -> FitnessCheckResult:
        """Check that dangerous patterns have a single canonical source.

        The rule enforces:
        1. The canonical dangerous patterns source exists in kernelone.security
        2. No cells/ directories contain duplicate pattern definitions
        """
        result = FitnessCheckResult(
            rule_id="CELL_KERNELONE_03",
            passed=True,
        )

        # Step 1: Verify canonical source exists
        if not self._verify_kernelone_has_patterns():
            result.passed = False
            result.violations.append(
                f"Canonical source not found: {self.canonical_patterns_path.relative_to(self.workspace)}"
            )
            return result

        result.evidence.append(f"Canonical source verified: {self.canonical_patterns_path.relative_to(self.workspace)}")

        # Step 2: Find local pattern definitions in cells/
        violations = self._find_local_pattern_definitions(self.cells_dir)

        if violations:
            result.passed = False
            for v in violations:
                result.violations.append(f"Local pattern definition at {v['file']}:{v['line']}: {v['pattern'][:50]}...")
        else:
            result.evidence.append("No local dangerous pattern definitions found in cells/")

        return result


def main() -> int:
    """Main entry point for running the check."""
    checker = CellKernelone03Checker()
    result = checker.check()
    print(result.format())

    # JSON output for CI integration
    import os

    if os.environ.get("CHECK_KERNELONE03_JSON_OUTPUT"):
        import json

        print(
            json.dumps(
                {
                    "rule_id": result.rule_id,
                    "passed": result.passed,
                    "evidence": result.evidence,
                    "violations": result.violations,
                    "warnings": result.warnings,
                    "timestamp": result.timestamp,
                    "duration_ms": result.duration_ms,
                },
                indent=2,
            )
        )

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
