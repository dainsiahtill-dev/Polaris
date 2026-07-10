#!/usr/bin/env python3
"""Validate skeptical architecture review reports against schema and proof rules."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

try:
    from docs.governance.ci.scripts.fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
    from docs.governance.ci.scripts.skeptical_architecture_review_policy import (
        evaluate_skeptical_architecture_review,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(SCRIPT_DIR))
    from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
    from skeptical_architecture_review_policy import evaluate_skeptical_architecture_review


class SkepticalArchitectureReviewChecker(FitnessRuleChecker):
    """Checker for skeptical architecture review report evidence."""

    def check_skeptical_architecture_review(self, report_paths: Sequence[str] = ()) -> FitnessCheckResult:
        """Validate skeptical architecture review reports."""
        self.start_time = time.time()
        policy_result = evaluate_skeptical_architecture_review(self.workspace, report_paths=report_paths)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
            duration_ms=self._elapsed_ms(),
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the skeptical architecture review check."""
    parser = argparse.ArgumentParser(description="Validate skeptical architecture review reports")
    parser.add_argument("--workspace", type=Path, default=None, help="Workspace root path")
    parser.add_argument(
        "--report",
        action="append",
        default=[],
        help="Report YAML path to validate. May be repeated. Defaults to validating the template.",
    )
    parser.add_argument("--json", action="store_true", help="Output a single JSON document")
    args = parser.parse_args(argv)

    checker = SkepticalArchitectureReviewChecker(args.workspace)
    result = checker.check_skeptical_architecture_review(tuple(str(item) for item in args.report))

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
                },
                ensure_ascii=False,
            )
        )
    else:
        print(result.format())

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
