#!/usr/bin/env python3
"""Check CELL_KERNELONE_06: State owners are explicitly declared.

Rule: CELL_KERNELONE_06
Severity: high
Description: >
    Budget infrastructure must use kernelone.context.budget_gate as canonical source.
    TokenBudget in roles.kernel must delegate to ContextBudgetGate.

Evidence:
    - docs/blueprints/CELLS_KERNELONE_INTEGRATION_BLUEPRINT_20260403.md
    - polaris/kernelone/context/budget_gate.py
    - polaris/cells/roles/kernel/internal/token_budget.py
    - polaris/cells/roles/kernel/internal/policy/budget_policy.py

Compliance:
    - ContextBudgetGate must be used for token budget decisions
    - No independent TokenBudget implementation in cells/

Violations:
    - Local TokenBudget implementations that bypass kernelone.context.budget_gate
    - Independent budget decision logic outside kernelone

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


class CellKernelone06Checker(FitnessRuleChecker):
    """Checker for CELL_KERNELONE_06: state owners explicit declaration."""

    # Patterns that indicate local budget gate implementations
    BUDGET_GATE_PATTERNS = [
        re.compile(r"class ContextBudgetGate\b"),
        re.compile(r"class TokenBudget\b"),
        re.compile(r"def _check_budget\s*\("),
        re.compile(r"def _enforce_budget\s*\("),
        re.compile(r"def should_compact\s*\("),
        re.compile(r"def _compute_token_budget\s*\("),
    ]

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__(workspace)
        self.cells_dir = self.workspace / "polaris" / "cells"
        self.kernelone_dir = self.workspace / "polaris" / "kernelone"
        self.canonical_budget_gate = self.kernelone_dir / "context" / "budget_gate.py"

    def _find_local_budget_implementations(self, dir_path: Path) -> list[dict[str, Any]]:
        """Find local budget gate implementations in cells directory."""
        violations = []

        for py_file in dir_path.rglob("*.py"):
            # Skip canonical sources
            if py_file == self.canonical_budget_gate:
                continue

            # Skip test files
            if "test" in py_file.parts or "_fixture" in py_file.name:
                continue

            try:
                with open(py_file, encoding="utf-8") as f:
                    content = f.read()

                # Check for local budget gate implementations
                for pattern in self.BUDGET_GATE_PATTERNS:
                    for match in pattern.finditer(content):
                        line_num = content[: match.start()].count("\n") + 1
                        violations.append(
                            {
                                "file": str(py_file.relative_to(self.workspace)),
                                "line": line_num,
                                "pattern": match.group().strip(),
                            }
                        )

            except OSError:
                continue

        return violations

    def _verify_kernelone_has_budget_gate(self) -> bool:
        """Verify that the canonical kernelone budget gate exists."""
        if not self.canonical_budget_gate.exists():
            return False

        try:
            with open(self.canonical_budget_gate, encoding="utf-8") as f:
                content = f.read()
            return "ContextBudgetGate" in content
        except OSError:
            return False

    def _check_state_owners_declared(self) -> tuple[bool, list[str]]:
        """Check if all cells properly declare state_owners in cells.yaml.

        Returns (all_declared, undeclared_cells)
        """
        import yaml

        cells_yaml_path = self.workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        undeclared_cells = []

        if not cells_yaml_path.exists():
            return False, ["cells.yaml not found"]

        try:
            with open(cells_yaml_path, encoding="utf-8") as f:
                catalog_data = yaml.safe_load(f)

            cells = catalog_data.get("cells", [])
            for cell in cells:
                cell_id = cell.get("id", "unknown")
                # Stateful cells must have state_owners
                if cell.get("stateful", False):
                    state_owners = cell.get("state_owners", [])
                    if not state_owners and "state_owners" not in cell:
                        undeclared_cells.append(cell_id)

        except (OSError, yaml.YAMLError) as e:
            return False, [f"Error parsing cells.yaml: {e}"]

        return len(undeclared_cells) == 0, undeclared_cells

    def check(self) -> FitnessCheckResult:
        """Check that state owners are explicitly declared.

        The rule enforces:
        1. The canonical budget gate exists in kernelone.context.budget_gate
        2. No cells/ contain independent budget gate implementations
        3. Stateful cells properly declare state_owners in cells.yaml
        """
        result = FitnessCheckResult(
            rule_id="CELL_KERNELONE_06",
            passed=True,
        )

        # Step 1: Verify canonical source exists
        if not self._verify_kernelone_has_budget_gate():
            result.passed = False
            result.violations.append(
                f"Canonical budget gate not found: {self.canonical_budget_gate.relative_to(self.workspace)}"
            )
            return result

        result.evidence.append(
            f"Canonical budget gate verified: {self.canonical_budget_gate.relative_to(self.workspace)}"
        )

        # Step 2: Find local budget implementations
        violations = self._find_local_budget_implementations(self.cells_dir)

        if violations:
            result.passed = False
            for v in violations:
                result.violations.append(f"Local budget implementation at {v['file']}:{v['line']}: {v['pattern']}")
        else:
            result.evidence.append("No local budget gate implementations found in cells/")

        # Step 3: Check state_owners declarations
        all_declared, undeclared = self._check_state_owners_declared()

        if not all_declared:
            result.warnings.extend(
                f"Stateful cell '{cell_id}' missing state_owners declaration" for cell_id in undeclared
            )

        return result


def main() -> int:
    """Main entry point for running the check."""
    checker = CellKernelone06Checker()
    result = checker.check()
    print(result.format())

    # JSON output for CI integration
    import os

    if os.environ.get("CHECK_KERNELONE06_JSON_OUTPUT"):
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
