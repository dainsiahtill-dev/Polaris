#!/usr/bin/env python3
"""Check CELL_KERNELONE_05: Public contracts follow descriptor patterns.

Rule: CELL_KERNELONE_05
Severity: high
Description: >
    Event publishing must use kernelone.events as canonical source.
    Multiple parallel event emitters must be consolidated.

Evidence:
    - docs/blueprints/CELLS_KERNELONE_INTEGRATION_BLUEPRINT_20260403.md
    - polaris/kernelone/events/fact_events.py
    - polaris/kernelone/events/session_events.py
    - polaris/cells/roles/kernel/internal/events.py
    - polaris/cells/roles/session/internal/session_persistence.py

Compliance:
    - emit_fact_event/emit_session_event must be primary interfaces
    - No duplicate _emit_event patterns in cells/

Violations:
    - Local _emit_event implementations in polaris/cells/ that bypass kernelone.events
    - Independent event emission systems outside kernelone

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


class CellKernelone05Checker(FitnessRuleChecker):
    """Checker for CELL_KERNELONE_05: event publishing canonical source."""

    # Patterns that indicate local event emission definitions
    EVENT_EMISSION_PATTERNS = [
        re.compile(r"def _emit_event\s*\("),
        re.compile(r"def emit_event\s*\("),
        re.compile(r"async def _emit_event\s*\("),
        re.compile(r"async def emit_event\s*\("),
    ]

    # Patterns for independent event class definitions
    EVENT_CLASS_PATTERNS = [
        re.compile(r"class (?!.*Event\b).*:"),  # Exclude classes ending in Event
        re.compile(r"class \w*Fact\w*:"),
        re.compile(r"class \w*Session\w*Event:"),
    ]

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__(workspace)
        self.cells_dir = self.workspace / "polaris" / "cells"
        self.kernelone_dir = self.workspace / "polaris" / "kernelone"
        self.canonical_events = [
            self.kernelone_dir / "events" / "fact_events.py",
            self.kernelone_dir / "events" / "session_events.py",
            self.kernelone_dir / "events" / "__init__.py",
        ]

    def _find_local_event_emitters(self, dir_path: Path) -> list[dict[str, Any]]:
        """Find local event emission definitions in cells directory."""
        violations = []

        for py_file in dir_path.rglob("*.py"):
            # Skip canonical sources
            if py_file in self.canonical_events:
                continue

            # Skip test files
            if "test" in py_file.parts or "_fixture" in py_file.name:
                continue

            try:
                with open(py_file, encoding="utf-8") as f:
                    content = f.read()

                # Check for local event emission definitions
                for pattern in self.EVENT_EMISSION_PATTERNS:
                    for match in pattern.finditer(content):
                        line_num = content[: match.start()].count("\n") + 1
                        violations.append(
                            {
                                "file": str(py_file.relative_to(self.workspace)),
                                "line": line_num,
                                "function": match.group().strip(),
                                "type": "local_emitter",
                            }
                        )

                # Check for independent event class definitions
                for pattern in self.EVENT_CLASS_PATTERNS:
                    for match in pattern.finditer(content):
                        line_num = content[: match.start()].count("\n") + 1
                        violations.append(
                            {
                                "file": str(py_file.relative_to(self.workspace)),
                                "line": line_num,
                                "class": match.group().strip(),
                                "type": "independent_event_class",
                            }
                        )

            except OSError:
                continue

        return violations

    def _verify_kernelone_has_events(self) -> bool:
        """Verify that the canonical kernelone events module exists."""
        for path in self.canonical_events:
            if path.exists():
                try:
                    with open(path, encoding="utf-8") as f:
                        content = f.read()
                    if "emit_fact_event" in content or "emit_session_event" in content:
                        return True
                except OSError:
                    continue
        return False

    def _check_cells_import_kernelone_events(self) -> tuple[bool, list[str]]:
        """Check if cells properly import from kernelone.events.

        Returns (uses_canonical, non_canonical_importers)
        """
        non_canonical_importers = []
        uses_canonical = False

        # Files that should use kernelone.events
        relevant_dirs = [
            self.cells_dir / "roles" / "kernel",
            self.cells_dir / "roles" / "session",
        ]

        for dir_path in relevant_dirs:
            if not dir_path.exists():
                continue

            for py_file in dir_path.rglob("*.py"):
                if "test" in py_file.parts:
                    continue

                try:
                    with open(py_file, encoding="utf-8") as f:
                        content = f.read()

                    # Check for kernelone.events imports
                    if "from polaris.kernelone.events import" in content:
                        uses_canonical = True

                    # Check for non-canonical event imports
                    if "from polaris.cells" in content and ".events import" in content and "kernelone" not in content:
                        non_canonical_importers.append(str(py_file.relative_to(self.workspace)))

                except OSError:
                    continue

        return uses_canonical, non_canonical_importers

    def check(self) -> FitnessCheckResult:
        """Check that event publishing uses kernelone.events as canonical source.

        The rule enforces:
        1. The canonical events module exists in kernelone.events
        2. No cells/ contain independent _emit_event implementations
        3. Cells properly delegate to kernelone.events
        """
        result = FitnessCheckResult(
            rule_id="CELL_KERNELONE_05",
            passed=True,
        )

        # Step 1: Verify canonical source exists
        if not self._verify_kernelone_has_events():
            result.passed = False
            result.violations.append("Canonical events not found in kernelone/events/")
            return result

        result.evidence.append("Canonical events verified in kernelone/events/")

        # Step 2: Check for local event emitters
        violations = self._find_local_event_emitters(self.cells_dir)

        if violations:
            result.passed = False
            for v in violations:
                if v["type"] == "local_emitter":
                    result.violations.append(f"Local event emitter at {v['file']}:{v['line']}: {v['function']}")
                elif v["type"] == "independent_event_class":
                    result.violations.append(f"Independent event class at {v['file']}:{v['line']}: {v['class']}")
        else:
            result.evidence.append("No local event emitter definitions found in cells/")

        # Step 3: Check if cells use canonical events
        uses_canonical, non_canonical = self._check_cells_import_kernelone_events()

        if non_canonical:
            result.warnings.extend(f"Non-canonical event import in {path}" for path in non_canonical)

        if uses_canonical:
            result.evidence.append("Cells properly import from kernelone.events")

        return result


def main() -> int:
    """Main entry point for running the check."""
    checker = CellKernelone05Checker()
    result = checker.check()
    print(result.format())

    # JSON output for CI integration
    import os

    if os.environ.get("CHECK_KERNELONE05_JSON_OUTPUT"):
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
