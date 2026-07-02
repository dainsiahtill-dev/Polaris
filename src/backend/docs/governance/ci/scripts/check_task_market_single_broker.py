#!/usr/bin/env python3
"""Check task_market_is_single_business_broker governance rule.

The canonical detection logic lives in ``task_broker_policy``. This script is
only a standalone CI/local adapter so it cannot drift from the aggregate
fitness runner.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from docs.governance.ci.scripts.fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
    from docs.governance.ci.scripts.task_broker_policy import evaluate_task_broker
except ModuleNotFoundError:
    from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
    from task_broker_policy import evaluate_task_broker


class TaskMarketSingleBrokerChecker(FitnessRuleChecker):
    """Checker for task_market_is_single_business_broker."""

    def check(self) -> FitnessCheckResult:
        """Check that runtime.task_market is the single business broker."""
        policy_result = evaluate_task_broker(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
            duration_ms=self._elapsed_ms(),
        )


def main() -> int:
    """Run the standalone task-market single-broker governance check."""
    checker = TaskMarketSingleBrokerChecker()
    result = checker.check()
    print(result.format())

    if os.environ.get("CHECK_TASK_MARKET_BROKER_JSON_OUTPUT"):
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
