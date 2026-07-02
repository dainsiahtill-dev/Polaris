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

import sys
from pathlib import Path

try:
    from docs.governance.ci.scripts.dangerous_pattern_source_policy import evaluate_dangerous_pattern_source
    from docs.governance.ci.scripts.fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
except ModuleNotFoundError:
    from dangerous_pattern_source_policy import evaluate_dangerous_pattern_source
    from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker


class CellKernelone03Checker(FitnessRuleChecker):
    """Checker for CELL_KERNELONE_03: dangerous patterns canonical source."""

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__(workspace)

    def check(self) -> FitnessCheckResult:
        """Check that dangerous patterns have a single canonical source.

        The rule enforces:
            1. The canonical dangerous patterns source exists in kernelone.security
            2. No cells/ directories contain duplicate pattern definitions
        """
        policy_result = evaluate_dangerous_pattern_source(self.workspace, rule_id="CELL_KERNELONE_03")
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )


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
