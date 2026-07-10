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

try:
    from docs.governance.ci.scripts.catalog_presence_policy import (
        evaluate_catalog_presence,
    )
    from docs.governance.ci.scripts.closed_ledger_intake_policy import (
        RULE_ID as CLOSED_LEDGER_INTAKE_RULE_ID,
        evaluate_closed_ledger_intake,
    )
    from docs.governance.ci.scripts.context_pack_freshness_policy import (
        evaluate_context_pack_freshness,
    )
    from docs.governance.ci.scripts.contract_change_review_policy import (
        evaluate_contract_change_review,
    )
    from docs.governance.ci.scripts.dangerous_pattern_source_policy import (
        evaluate_dangerous_pattern_source,
    )
    from docs.governance.ci.scripts.event_usage_policy import (
        evaluate_event_usage,
    )
    from docs.governance.ci.scripts.execution_control_reconstruction_card_policy import (
        evaluate_execution_control_reconstruction_card,
    )
    from docs.governance.ci.scripts.legacy_coverage_policy import (
        evaluate_legacy_coverage,
    )
    from docs.governance.ci.scripts.llm_import_policy import (
        evaluate_llm_import,
    )
    from docs.governance.ci.scripts.no_conflicting_coverage_policy import (
        evaluate_no_conflicting_coverage,
    )
    from docs.governance.ci.scripts.role_call_hierarchy_policy import (
        evaluate_role_call_hierarchy,
    )
    from docs.governance.ci.scripts.semantic_retrieval_boundary_policy import (
        evaluate_semantic_retrieval_boundary,
    )
    from docs.governance.ci.scripts.shim_markers_policy import (
        evaluate_shim_markers,
    )
    from docs.governance.ci.scripts.skeptical_architecture_review_policy import (
        evaluate_skeptical_architecture_review,
    )
    from docs.governance.ci.scripts.task_broker_policy import (
        evaluate_task_broker,
    )
    from docs.governance.ci.scripts.tool_compression_policy import (
        evaluate_tool_compression,
    )
    from docs.governance.ci.scripts.verified_evidence_policy import (
        evaluate_verified_evidence,
    )
except ModuleNotFoundError:
    from catalog_presence_policy import (
        evaluate_catalog_presence,
    )
    from closed_ledger_intake_policy import (
        RULE_ID as CLOSED_LEDGER_INTAKE_RULE_ID,
        evaluate_closed_ledger_intake,
    )
    from context_pack_freshness_policy import (
        evaluate_context_pack_freshness,
    )
    from contract_change_review_policy import (
        evaluate_contract_change_review,
    )
    from dangerous_pattern_source_policy import (
        evaluate_dangerous_pattern_source,
    )
    from event_usage_policy import (
        evaluate_event_usage,
    )
    from execution_control_reconstruction_card_policy import (
        evaluate_execution_control_reconstruction_card,
    )
    from legacy_coverage_policy import (
        evaluate_legacy_coverage,
    )
    from llm_import_policy import (
        evaluate_llm_import,
    )
    from no_conflicting_coverage_policy import (
        evaluate_no_conflicting_coverage,
    )
    from role_call_hierarchy_policy import (
        evaluate_role_call_hierarchy,
    )
    from semantic_retrieval_boundary_policy import (
        evaluate_semantic_retrieval_boundary,
    )
    from shim_markers_policy import (
        evaluate_shim_markers,
    )
    from skeptical_architecture_review_policy import (
        evaluate_skeptical_architecture_review,
    )
    from task_broker_policy import (
        evaluate_task_broker,
    )
    from tool_compression_policy import (
        evaluate_tool_compression,
    )
    from verified_evidence_policy import (
        evaluate_verified_evidence,
    )

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent
DEFAULT_RULE_IDS: tuple[str, ...] = (
    "context_pack_freshness",
    "semantic_retrieval_boundary",
    "contract_change_review",
    "no_conflicting_coverage",
    "catalog_presence",
    "shim_markers",
    "legacy_coverage",
    "closed_governance_ledgers_intake_only",
    "verified_evidence",
    "command_pattern_source",
    "event_usage",
    "tool_compression",
    "llm_import",
    "role_call_hierarchy",
    "task_broker",
    "skeptical_architecture_review",
    "execution_control_reconstruction_card",
)


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
            for e in self.evidence[:5]:
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
        """Check Context Pack freshness through the canonical policy module."""
        policy_result = evaluate_context_pack_freshness(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_semantic_retrieval_boundary(self) -> FitnessCheckResult:
        """Check semantic retrieval boundaries through the canonical policy."""
        policy_result = evaluate_semantic_retrieval_boundary(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_contract_change_review(self) -> FitnessCheckResult:
        """Check public contract changes through the canonical policy."""
        policy_result = evaluate_contract_change_review(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_no_conflicting_coverage(self) -> FitnessCheckResult:
        """Check migration coverage conflicts through the canonical policy module."""
        policy_result = evaluate_no_conflicting_coverage(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_catalog_presence(self) -> FitnessCheckResult:
        """Check target Cell catalog presence through the canonical policy module."""
        policy_result = evaluate_catalog_presence(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_shim_markers(self) -> FitnessCheckResult:
        """Check shim marker policy through the canonical policy module."""
        policy_result = evaluate_shim_markers(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_legacy_coverage(self) -> FitnessCheckResult:
        """Check legacy coverage granularity through the canonical policy module."""
        policy_result = evaluate_legacy_coverage(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_verified_evidence(self) -> FitnessCheckResult:
        """Check verified/retired units through the canonical policy module."""
        policy_result = evaluate_verified_evidence(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_command_pattern_source(self) -> FitnessCheckResult:
        """Check dangerous command patterns through the canonical policy module."""
        policy_result = evaluate_dangerous_pattern_source(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_event_usage(self) -> FitnessCheckResult:
        """Check event usage through the canonical policy module."""
        policy_result = evaluate_event_usage(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_tool_compression(self) -> FitnessCheckResult:
        """Check tool compression through the canonical policy module."""
        policy_result = evaluate_tool_compression(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_llm_import(self) -> FitnessCheckResult:
        """Check LLM invocation through the canonical policy module."""
        policy_result = evaluate_llm_import(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_role_call_hierarchy(self) -> FitnessCheckResult:
        """Check role hierarchy through the canonical policy module."""
        policy_result = evaluate_role_call_hierarchy(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_task_broker(self) -> FitnessCheckResult:
        """Check task broker ownership through the canonical policy module."""
        policy_result = evaluate_task_broker(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_skeptical_architecture_review(self) -> FitnessCheckResult:
        """Check skeptical architecture review report schema and proof rules."""
        policy_result = evaluate_skeptical_architecture_review(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_execution_control_reconstruction_card(self) -> FitnessCheckResult:
        """Check execution-control-plane reconstruction card schema and proof rules."""
        policy_result = evaluate_execution_control_reconstruction_card(self.workspace)
        return FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )

    def check_closed_governance_ledgers_intake_only(self) -> FitnessCheckResult:
        """Check that closed convergence ledgers remain intake-only."""
        report = evaluate_closed_ledger_intake(self.workspace)
        return FitnessCheckResult(
            rule_id=CLOSED_LEDGER_INTAKE_RULE_ID,
            passed=report.passed,
            evidence=list(report.evidence),
            violations=list(report.violations),
            warnings=list(report.warnings),
        )


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
    results = []
    for rule in DEFAULT_RULE_IDS:
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

    if args.all:
        results = run_all()
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        print(f"\n{'=' * 60}")
        print(f"Fitness Rule Check: {passed}/{total} passed")
        print(f"{'=' * 60}\n")
        for result in results:
            print(result.format())
            print()
        return 0 if passed == total else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
