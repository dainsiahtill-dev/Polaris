#!/usr/bin/env python3
"""Check no_direct_role_call governance rule.

This standalone entrypoint adapts the canonical
``role_call_hierarchy_policy`` result for CI and local script usage. The policy
module owns all role-call detection logic so this script and the aggregate
fitness runner cannot drift.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from docs.governance.ci.scripts.fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
    from docs.governance.ci.scripts.role_call_hierarchy_policy import (
        evaluate_role_call_hierarchy,
    )
except ModuleNotFoundError:
    from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
    from role_call_hierarchy_policy import (
        evaluate_role_call_hierarchy,
    )


class NoDirectRoleCallChecker(FitnessRuleChecker):
    """Checker for the no_direct_role_call rule."""

    def check(self) -> FitnessCheckResult:
        """Check that role collaboration does not bypass task-market boundaries."""
        policy_result = evaluate_role_call_hierarchy(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
            duration_ms=self._elapsed_ms(),
        )


def main() -> int:
    """Run the standalone no_direct_role_call governance check."""
    checker = NoDirectRoleCallChecker()
    result = checker.check()
    print(result.format())

    if os.environ.get("CHECK_NO_DIRECT_ROLE_CALL_JSON_OUTPUT"):
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
