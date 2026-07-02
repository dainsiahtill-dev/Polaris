#!/usr/bin/env python3
"""Check verified_evidence rule.

Ensures all migration units with status "verified" or "retired" have
evidence of verification (test results, review records, etc.).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

# Import from fitness_rule_checker (sibling module in same package)
sys.path.insert(0, str(SCRIPT_DIR))
from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker  # noqa: E402
from verified_evidence_policy import evaluate_verified_evidence  # noqa: E402


class VerifiedEvidenceChecker(FitnessRuleChecker):
    """Checker for verified/retired migration units having evidence."""

    def check_verified_evidence(self) -> FitnessCheckResult:
        """Check that verified/retired units have verification evidence."""
        self.start_time = time.time()
        policy_result = evaluate_verified_evidence(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
            duration_ms=self._elapsed_ms(),
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the verified evidence check."""
    parser = argparse.ArgumentParser(description="Check verified/retired migration unit evidence")
    parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        help="Workspace root path (default: repo root from script location)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output a single JSON document",
    )
    args = parser.parse_args(argv)

    workspace = Path(args.workspace) if args.workspace else None
    checker = VerifiedEvidenceChecker(workspace)
    result = checker.check_verified_evidence()

    if args.json:
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
    else:
        print(result.format())

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
