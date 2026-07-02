#!/usr/bin/env python3
"""Check migration_no_conflicting_full_coverage rule.

This script verifies that no two active migration units claim full coverage
of the same legacy path, and that target paths do not overlap unless
explicitly marked as partial with justification.

Rule ID: migration_no_conflicting_full_coverage
Severity: blocker
Evidence: docs/migration/ledger.yaml

Usage:
    python docs/governance/ci/scripts/check_no_conflicting_coverage.py
    python docs/governance/ci/scripts/check_no_conflicting_coverage.py --json
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    from docs.governance.ci.scripts.no_conflicting_coverage_policy import evaluate_no_conflicting_coverage
except ModuleNotFoundError:
    from no_conflicting_coverage_policy import evaluate_no_conflicting_coverage

# ─────────────────────────────────────────────────────────────────────────────
# Path Setup
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


@dataclass
class FitnessCheckResult:
    """Result of a fitness rule check."""

    rule_id: str
    passed: bool
    evidence: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timestamp: str = ""
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def format(self) -> str:
        """Format result for console output."""
        status = f"{GREEN}PASS{RESET}" if self.passed else f"{RED}FAIL{RESET}"
        lines = [
            f"[{self.rule_id}] {status}",
            f"  Duration: {self.duration_ms:.2f}ms",
        ]
        if self.evidence:
            lines.append("  Evidence:")
            for e in self.evidence[:5]:  # Limit output
                lines.append(f"    - {e}")
        if self.violations:
            lines.append("  Violations:")
            for v in self.violations:
                lines.append(f"    - {v}")
        if self.warnings:
            lines.append("  Warnings:")
            for w in self.warnings:
                lines.append(f"    - {w}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main Checker
# ─────────────────────────────────────────────────────────────────────────────


class NoConflictChecker:
    """Checker for migration_no_conflicting_full_coverage rule."""

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace or REPO_ROOT
        self.start_time = time.time()

    def _elapsed_ms(self) -> float:
        """Get elapsed time in milliseconds."""
        return (time.time() - self.start_time) * 1000

    def check_no_conflicting_coverage(self) -> FitnessCheckResult:
        """Check that migration units don't claim conflicting full coverage.

        This rule verifies:
        1. No two active units claim full coverage of the same source path
        2. No two active units have overlapping target paths
        3. Any overlapping paths are explicitly justified as partial
        """
        policy_result = evaluate_no_conflicting_coverage(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
            duration_ms=self._elapsed_ms(),
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    """Main entry point for the checker."""
    parser = argparse.ArgumentParser(description="Check migration_no_conflicting_full_coverage rule")
    parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        help="Workspace root path (default: repo root from script location)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace) if args.workspace else None
    checker = NoConflictChecker(workspace)
    result = checker.check_no_conflicting_coverage()

    if args.json:
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
    else:
        print(result.format())

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
