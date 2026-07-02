#!/usr/bin/env python3
"""Check context_pack_is_primary_ai_entry governance rule.

The canonical freshness and structure logic lives in
``context_pack_freshness_policy``. This script is a standalone CI/local adapter
so it cannot drift from the aggregate fitness runner.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

try:
    from docs.governance.ci.scripts.context_pack_freshness_policy import (
        evaluate_context_pack_freshness,
    )
    from docs.governance.ci.scripts.fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
except ModuleNotFoundError:
    from context_pack_freshness_policy import evaluate_context_pack_freshness
    from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker


class ContextPackFreshnessChecker(FitnessRuleChecker):
    """Checker for context_pack_is_primary_ai_entry."""

    def check_context_pack_freshness(self) -> FitnessCheckResult:
        """Check that each catalog Cell has a valid, fresh Context Pack."""
        policy_result = evaluate_context_pack_freshness(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
            duration_ms=self._elapsed_ms(),
        )


def main() -> int:
    """Run the standalone Context Pack freshness governance check."""
    checker = ContextPackFreshnessChecker()
    result = checker.check_context_pack_freshness()
    print(result.format())

    if os.environ.get("CHECK_CONTEXT_PACK_JSON_OUTPUT"):
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
                }
            )
        )

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
