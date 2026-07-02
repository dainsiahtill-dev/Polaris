#!/usr/bin/env python3
"""Check CELL_KERNELONE_05: event publishing uses KernelOne events.

Rule: CELL_KERNELONE_05
Severity: high

This script is the standalone CLI-compatible adapter for the canonical
``event_usage_policy`` module. It intentionally contains no local scanning
logic so the aggregate fitness runner and the standalone gate cannot drift.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from docs.governance.ci.scripts.event_usage_policy import evaluate_event_usage
    from docs.governance.ci.scripts.fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
except ModuleNotFoundError:
    from event_usage_policy import evaluate_event_usage
    from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker


class CellKernelone05Checker(FitnessRuleChecker):
    """Checker for CELL_KERNELONE_05: event publishing canonical source."""

    def check(self) -> FitnessCheckResult:
        """Check that event publishing uses kernelone.events as canonical source."""
        policy_result = evaluate_event_usage(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
            duration_ms=self._elapsed_ms(),
        )


def main() -> int:
    """Run the standalone event-usage governance check."""
    checker = CellKernelone05Checker()
    result = checker.check()
    print(result.format())

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
