"""Check closed governance ledger intake policy.

This gate prevents closed convergence ledgers from silently becoming active
backlogs again. New findings must be opened as new evidence-backed intake
items, not by reopening closed rows.

Rule ID: closed_governance_ledgers_intake_only
Severity: high

Usage:
    python docs/governance/ci/scripts/check_closed_ledger_intake.py
    python docs/governance/ci/scripts/check_closed_ledger_intake.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
BACKEND_ROOT = SCRIPT_DIR.parent.parent.parent.parent

sys.path.insert(0, str(SCRIPT_DIR))
from closed_ledger_intake_policy import (  # noqa: E402
    CLOSED_LEDGER_EXPECTATIONS,
    RULE_ID,
    ClosedLedgerExpectation,
    evaluate_closed_ledger_intake,
)
from fitness_rule_checker import FitnessCheckResult  # noqa: E402


class ClosedLedgerIntakeChecker:
    """Checker for closed governance ledger intake policy."""

    def __init__(
        self,
        workspace: Path | None = None,
        expectations: tuple[ClosedLedgerExpectation, ...] = CLOSED_LEDGER_EXPECTATIONS,
    ) -> None:
        self.workspace = workspace or BACKEND_ROOT
        self.expectations = expectations
        self.start_time = time.time()

    def _elapsed_ms(self) -> float:
        """Return elapsed checker time in milliseconds."""
        return (time.time() - self.start_time) * 1000

    def check_closed_ledgers(self) -> FitnessCheckResult:
        """Validate that closed governance ledgers remain intake-only."""
        report = evaluate_closed_ledger_intake(self.workspace, self.expectations)
        return FitnessCheckResult(
            rule_id=RULE_ID,
            passed=report.passed,
            evidence=list(report.evidence),
            violations=list(report.violations),
            warnings=list(report.warnings),
            duration_ms=self._elapsed_ms(),
        )


def main() -> int:
    """Run the closed ledger intake checker from the command line."""
    parser = argparse.ArgumentParser(description="Check closed governance ledger intake policy")
    parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        help="Backend workspace root (default: src/backend inferred from script path)",
    )
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args()

    workspace = Path(args.workspace) if args.workspace else None
    result = ClosedLedgerIntakeChecker(workspace).check_closed_ledgers()

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
                indent=2,
            )
        )
    else:
        print(result.format())

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
