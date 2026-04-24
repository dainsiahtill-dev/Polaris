#!/usr/bin/env python3
"""Check verified_evidence rule.

Ensures all migration units with status "verified" or "retired" have
evidence of verification (test results, review records, etc.).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent
LEDGER_PATH = REPO_ROOT / "docs" / "migration" / "ledger.yaml"

# Import from fitness_rule_checker (sibling module in same package)
sys.path.insert(0, str(SCRIPT_DIR))
from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker  # noqa: E402


class VerifiedEvidenceChecker(FitnessRuleChecker):
    """Checker for verified/retired migration units having evidence."""

    def check_verified_evidence(self) -> FitnessCheckResult:
        """Check that verified/retired units have verification evidence."""
        self.start_time = time.time()
        result = FitnessCheckResult(
            rule_id="verified_or_retired_units_require_evidence",
            passed=True,
        )

        if not LEDGER_PATH.exists():
            result.passed = False
            result.violations.append(f"Ledger not found: {LEDGER_PATH}")
            result.duration_ms = self._elapsed_ms()
            return result

        try:
            with open(LEDGER_PATH, encoding="utf-8") as f:
                ledger = yaml.safe_load(f)
        except yaml.YAMLError as e:
            result.passed = False
            result.violations.append(f"Failed to parse ledger.yaml: {e}")
            result.duration_ms = self._elapsed_ms()
            return result

        units = ledger.get("units", [])
        if not units:
            result.warnings.append("No migration units found in ledger")
            result.duration_ms = self._elapsed_ms()
            return result

        # Find units with verified or retired status
        checked_units: list[str] = []
        units_without_evidence: list[str] = []

        for unit in units:
            status = unit.get("status", "")
            unit_id = unit.get("id", "unknown")

            if status not in ("verified", "retired"):
                continue

            checked_units.append(unit_id)
            has_evidence = self._has_verification_evidence(unit)

            if has_evidence:
                result.evidence.append(f"{unit_id}: has verification evidence")
            else:
                units_without_evidence.append(unit_id)
                result.violations.append(f"{unit_id}: status={status} but missing verification evidence")

        # Summary
        if checked_units:
            result.evidence.append(f"Checked {len(checked_units)} verified/retired units")
        else:
            result.warnings.append("No verified/retired units found to check")

        # Determine pass/fail
        result.passed = len(units_without_evidence) == 0

        if not result.passed:
            result.warnings.append(f"{len(units_without_evidence)} units lack verification evidence")

        result.duration_ms = self._elapsed_ms()
        return result

    def _has_verification_evidence(self, unit: dict[str, Any]) -> bool:
        """Check if a unit has verification evidence.

        Evidence includes:
        - verification.checks (non-empty list of test commands)
        - verification.required_tests (non-empty list)
        - verification.docs_updates (non-empty list)
        - verification.graph_updates (non-empty list)
        """
        verification = unit.get("verification", {})

        # Check for any non-empty evidence lists
        evidence_fields = [
            verification.get("checks", []),
            verification.get("required_tests", []),
            verification.get("docs_updates", []),
            verification.get("graph_updates", []),
        ]

        for field in evidence_fields:
            if isinstance(field, list) and len(field) > 0:
                return True

        # Also accept if unit has explicit evidence_notes or similar
        return bool(unit.get("evidence_notes"))


def main() -> int:
    """Run the verified evidence check."""
    checker = VerifiedEvidenceChecker()
    result = checker.check_verified_evidence()

    print(result.format())

    # Also support --json flag
    if "--json" in sys.argv:
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
