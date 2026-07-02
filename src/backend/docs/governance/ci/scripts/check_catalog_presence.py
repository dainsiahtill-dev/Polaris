#!/usr/bin/env python3
"""Check catalog_presence rule.

Rule: catalog_missing_units_cannot_advance
Enforces that migration units targeting cells with catalog_status=missing
cannot advance to verified/retired states until they are added to the catalog.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from docs.governance.ci.scripts.catalog_presence_policy import evaluate_catalog_presence
    from docs.governance.ci.scripts.fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
except ModuleNotFoundError:
    from catalog_presence_policy import evaluate_catalog_presence
    from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker


class CatalogPresenceChecker(FitnessRuleChecker):
    """Checker for catalog_missing_units_cannot_advance rule."""

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__(workspace)

    def check_catalog_presence(self) -> FitnessCheckResult:
        """Check that all migration targets are present in catalog.

        Migration units with catalog_status=missing should not be able to
        advance to verified/retired states until their target cell is
        declared in cells.yaml.
        """
        policy_result = evaluate_catalog_presence(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )


if __name__ == "__main__":
    checker = CatalogPresenceChecker()
    result = checker.check_catalog_presence()
    print(result.format())
    sys.exit(0 if result.passed else 1)
