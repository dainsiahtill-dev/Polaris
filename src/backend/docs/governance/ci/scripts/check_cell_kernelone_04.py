#!/usr/bin/env python3
"""Check CELL_KERNELONE_04: No direct internal imports across cells.

Rule: CELL_KERNELONE_04
Severity: high
Description: >
    Storage path resolution must have a single canonical source
    in polaris.kernelone.storage.paths.

Evidence:
    - docs/blueprints/CELLS_KERNELONE_INTEGRATION_BLUEPRINT_20260403.md
    - polaris/kernelone/storage/paths.py
    - polaris/kernelone/storage/io_paths.py
    - polaris/cells/roles/adapters/internal/base.py
    - polaris/cells/roles/session/internal/storage_paths.py

Compliance:
    - resolve_signal_path/resolve_artifact_path must only exist in kernelone/storage/
    - No local _resolve_artifact_path definitions in cells/

Violations:
    - Local _resolve_artifact_path definitions in polaris/cells/
    - Independent path resolution implementations outside kernelone

Exit codes:
    0 - All checks passed
    1 - Rule violation detected
    2 - Script error (e.g., missing dependencies)
"""

from __future__ import annotations

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


class CellKernelone04Checker(FitnessRuleChecker):
    """Checker for CELL_KERNELONE_04: path resolution canonical source."""

    # Patterns that indicate local path resolution definitions
    PATH_RESOLUTION_PATTERNS = [
        re.compile(r"def _resolve_artifact_path\s*\("),
        re.compile(r"def _resolve_signal_path\s*\("),
        re.compile(r"def resolve_artifact_path\s*\("),
        re.compile(r"def resolve_signal_path\s*\("),
        re.compile(r"def _resolve_preferred_logical_prefix\s*\("),
    ]

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__(workspace)
        self.cells_dir = self.workspace / "polaris" / "cells"
        self.kernelone_dir = self.workspace / "polaris" / "kernelone"
        self.canonical_paths = [
            self.kernelone_dir / "storage" / "paths.py",
            self.kernelone_dir / "storage" / "io_paths.py",
        ]

    def _find_local_path_definitions(self, dir_path: Path) -> list[dict[str, Any]]:
        """Find local path resolution definitions in a directory tree."""
        violations = []

        for py_file in dir_path.rglob("*.py"):
            # Skip canonical sources
            if py_file in self.canonical_paths:
                continue

            # Skip test files (they may define test fixtures)
            if "test" in py_file.parts or "_fixture" in py_file.name:
                continue

            try:
                with open(py_file, encoding="utf-8") as f:
                    content = f.read()

                # Check for local path resolution definitions
                for pattern in self.PATH_RESOLUTION_PATTERNS:
                    for match in pattern.finditer(content):
                        # Get line number
                        line_num = content[: match.start()].count("\n") + 1
                        violations.append(
                            {
                                "file": str(py_file.relative_to(self.workspace)),
                                "line": line_num,
                                "function": match.group().strip(),
                            }
                        )

            except OSError:
                continue

        return violations

    def _verify_kernelone_has_paths(self) -> bool:
        """Verify that the canonical kernelone path resolution exists."""
        for path in self.canonical_paths:
            if path.exists():
                try:
                    with open(path, encoding="utf-8") as f:
                        content = f.read()
                    if "resolve_artifact_path" in content or "resolve_signal_path" in content:
                        return True
                except OSError:
                    continue
        return False

    def check(self) -> FitnessCheckResult:
        """Check that path resolution has a single canonical source.

        The rule enforces:
        1. The canonical path resolution exists in kernelone.storage
        2. No cells/ directories contain duplicate path resolution definitions
        """
        result = FitnessCheckResult(
            rule_id="CELL_KERNELONE_04",
            passed=True,
        )

        # Step 1: Verify canonical source exists
        if not self._verify_kernelone_has_paths():
            result.passed = False
            result.violations.append("Canonical path resolution not found in kernelone/storage/")
            return result

        result.evidence.append("Canonical path resolution verified in kernelone/storage/")

        # Step 2: Find local path resolution definitions in cells/
        violations = self._find_local_path_definitions(self.cells_dir)

        if violations:
            result.passed = False
            for v in violations:
                result.violations.append(f"Local path resolution at {v['file']}:{v['line']}: {v['function']}")
        else:
            result.evidence.append("No local path resolution definitions found in cells/")

        return result


def main() -> int:
    """Main entry point for running the check."""
    checker = CellKernelone04Checker()
    result = checker.check()
    print(result.format())

    # JSON output for CI integration
    import os

    if os.environ.get("CHECK_KERNELONE04_JSON_OUTPUT"):
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
