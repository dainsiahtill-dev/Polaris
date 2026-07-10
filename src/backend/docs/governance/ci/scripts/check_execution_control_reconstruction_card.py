"""Validate execution-control-plane reconstruction cards."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

try:
    from docs.governance.ci.scripts.execution_control_reconstruction_card_policy import (
        evaluate_execution_control_reconstruction_card,
    )
    from docs.governance.ci.scripts.fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
except ModuleNotFoundError:
    sys.path.insert(0, str(SCRIPT_DIR))
    from execution_control_reconstruction_card_policy import evaluate_execution_control_reconstruction_card
    from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker


class ExecutionControlReconstructionCardChecker(FitnessRuleChecker):
    """Checker for execution-control-plane reconstruction cards."""

    def check_execution_control_reconstruction_card(self, card_paths: Sequence[str] = ()) -> FitnessCheckResult:
        """Validate execution-control-plane reconstruction cards."""
        self.start_time = time.time()
        policy_result = evaluate_execution_control_reconstruction_card(self.workspace, card_paths=card_paths)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
            duration_ms=self._elapsed_ms(),
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the execution-control-plane reconstruction card check."""
    parser = argparse.ArgumentParser(description="Validate execution-control-plane reconstruction cards")
    parser.add_argument("--workspace", type=Path, default=None, help="Workspace root path")
    parser.add_argument(
        "--card",
        action="append",
        default=[],
        help="Card YAML path to validate. May be repeated. Defaults to validating the template.",
    )
    parser.add_argument("--json", action="store_true", help="Output a single JSON document")
    args = parser.parse_args(argv)

    checker = ExecutionControlReconstructionCardChecker(args.workspace)
    result = checker.check_execution_control_reconstruction_card(tuple(str(item) for item in args.card))

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
