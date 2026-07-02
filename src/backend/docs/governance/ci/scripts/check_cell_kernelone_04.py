#!/usr/bin/env python3
"""Check CELL_KERNELONE_04: path resolution delegates to KernelOne."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from cell_kernelone_04_policy import (  # noqa: E402
    CellKernelone04Policy,
    CellKernelone04PolicyResult,
    evaluate_cell_kernelone_04,
)
from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker  # noqa: E402


class CellKernelone04Checker(FitnessRuleChecker):
    """CLI adapter for CELL_KERNELONE_04 path resolution governance."""

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__(workspace)
        self.policy = CellKernelone04Policy(self.workspace)

    def _find_local_path_definitions(self, dir_path: Path) -> list[dict[str, Any]]:
        """Return non-delegating local resolver definitions for compatibility tests."""
        return [
            definition.to_dict()
            for definition in self.policy.find_path_definitions(dir_path)
            if not definition.delegated
        ]

    def _verify_kernelone_has_paths(self) -> bool:
        """Return whether KernelOne exposes canonical path resolution helpers."""
        return self.policy.verify_kernelone_has_paths()

    def check(self) -> FitnessCheckResult:
        """Check that path resolution has a single canonical implementation."""
        return self._to_fitness_result(evaluate_cell_kernelone_04(self.workspace))

    def _to_fitness_result(self, policy_result: CellKernelone04PolicyResult) -> FitnessCheckResult:
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
            duration_ms=self._elapsed_ms(),
        )


def main() -> int:
    """Run the standalone CELL_KERNELONE_04 check."""
    checker = CellKernelone04Checker()
    result = checker.check()
    print(result.format())

    if os.environ.get("CHECK_KERNELONE04_JSON_OUTPUT"):
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
