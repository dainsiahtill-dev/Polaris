#!/usr/bin/env python3
"""
Fitness Rule Checker Framework.

Provides unified interface for checking governance fitness rules.
Each rule implements a check_* method returning FitnessCheckResult.

用法:
    python docs/governance/ci/scripts/fitness_rule_checker.py --rule context_pack_is_primary_ai_entry
    python docs/governance/ci/scripts/fitness_rule_checker.py --all
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent


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


class FitnessRuleChecker:
    """Base class for fitness rule checkers."""

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace or REPO_ROOT
        self.start_time = time.time()

    def _elapsed_ms(self) -> float:
        return (time.time() - self.start_time) * 1000

    def check_context_pack_freshness(self) -> FitnessCheckResult:
        """Check that each Cell has a context pack with fresh timestamp."""
        raise NotImplementedError

    def check_semantic_retrieval_boundary(self) -> FitnessCheckResult:
        """Check that semantic retrieval respects graph boundaries."""
        raise NotImplementedError

    def check_contract_change_review(self) -> FitnessCheckResult:
        """Check that public contract changes trigger review."""
        raise NotImplementedError

    def check_no_conflicting_coverage(self) -> FitnessCheckResult:
        """Check that migration units don't claim conflicting full coverage."""
        raise NotImplementedError

    def check_catalog_presence(self) -> FitnessCheckResult:
        """Check that target Cells are present in catalog."""
        raise NotImplementedError

    def check_shim_markers(self) -> FitnessCheckResult:
        """Check that shim_only files have migration markers."""
        raise NotImplementedError

    def check_legacy_coverage(self) -> FitnessCheckResult:
        """Check that legacy path coverage is audited at file granularity."""
        raise NotImplementedError

    def check_verified_evidence(self) -> FitnessCheckResult:
        """Check that verified/retired units have evidence."""
        raise NotImplementedError

    def check_command_pattern_source(self) -> FitnessCheckResult:
        """Check that dangerous command patterns have single source."""
        raise NotImplementedError

    def check_event_usage(self) -> FitnessCheckResult:
        """Check that events use kernelone.events."""
        raise NotImplementedError

    def check_tool_compression(self) -> FitnessCheckResult:
        """Check that tool compression uses kernelone.tool."""
        raise NotImplementedError

    def check_llm_import(self) -> FitnessCheckResult:
        """Check that LLM calls use kernelone.llm."""
        raise NotImplementedError

    def check_role_call_hierarchy(self) -> FitnessCheckResult:
        """Check that roles don't directly call同级 peers."""
        raise NotImplementedError

    def check_task_broker(self) -> FitnessCheckResult:
        """Check that task_market is the only business broker."""
        raise NotImplementedError


def get_checker() -> FitnessRuleChecker:
    """Get the default fitness rule checker instance."""
    return FitnessRuleChecker(REPO_ROOT)


def run_rule(rule_id: str) -> FitnessCheckResult:
    """Run a specific rule and return result."""
    checker = get_checker()
    method_name = f"check_{rule_id}"
    if not hasattr(checker, method_name):
        return FitnessCheckResult(
            rule_id=rule_id,
            passed=False,
            violations=[f"Unknown rule: {rule_id}"],
            duration_ms=checker._elapsed_ms(),
        )
    method = getattr(checker, method_name)
    result = method()
    result.duration_ms = checker._elapsed_ms()
    return result


def run_all() -> list[FitnessCheckResult]:
    """Run all rules and return results."""
    checker = get_checker()
    rules = [
        "context_pack_freshness",
        "semantic_retrieval_boundary",
        "contract_change_review",
        "no_conflicting_coverage",
        "catalog_presence",
        "shim_markers",
        "legacy_coverage",
        "verified_evidence",
        "command_pattern_source",
        "event_usage",
        "tool_compression",
        "llm_import",
        "role_call_hierarchy",
        "task_broker",
    ]
    results = []
    for rule in rules:
        result = run_rule(rule)
        results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Fitness Rule Checker")
    parser.add_argument("--rule", help="Specific rule ID to check")
    parser.add_argument("--all", action="store_true", help="Run all rules")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if args.rule:
        result = run_rule(args.rule)
        if args.json:
            import json
            print(json.dumps({
                "rule_id": result.rule_id,
                "passed": result.passed,
                "evidence": result.evidence,
                "violations": result.violations,
                "warnings": result.warnings,
                "timestamp": result.timestamp,
                "duration_ms": result.duration_ms,
            }))
        else:
            print(result.format())
        return 0 if result.passed else 1

    if args.all:
        results = run_all()
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        print(f"\n{'='*60}")
        print(f"Fitness Rule Check: {passed}/{total} passed")
        print(f"{'='*60}\n")
        for result in results:
            print(result.format())
            print()
        return 0 if passed == total else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
