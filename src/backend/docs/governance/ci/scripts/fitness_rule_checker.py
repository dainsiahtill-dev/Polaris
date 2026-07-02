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
import json
import re
import subprocess
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
    from docs.governance.ci.scripts.dangerous_pattern_source_policy import (
        evaluate_dangerous_pattern_source,
    )
    from docs.governance.ci.scripts.event_usage_policy import (
        evaluate_event_usage,
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
    from docs.governance.ci.scripts.shim_markers_policy import (
        evaluate_shim_markers,
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
    from dangerous_pattern_source_policy import (
        evaluate_dangerous_pattern_source,
    )
    from event_usage_policy import (
        evaluate_event_usage,
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
    from shim_markers_policy import (
        evaluate_shim_markers,
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
        """Check that each Cell has a context pack with fresh timestamp."""
        result = FitnessCheckResult(rule_id="context_pack_is_primary_ai_entry", passed=True)
        cells_yaml_path = self.workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        if not cells_yaml_path.exists():
            result.passed = False
            result.violations.append(f"cells.yaml not found at {cells_yaml_path}")
            return result
        try:
            import yaml

            with open(cells_yaml_path, encoding="utf-8") as f:
                catalog_data = yaml.safe_load(f)
        except (OSError, ImportError) as e:
            result.passed = False
            result.violations.append(f"Failed to parse cells.yaml: {e}")
            return result
        cells = catalog_data.get("cells", [])
        if not cells:
            result.warnings.append("No cells found in cells.yaml")
            return result
        total_cells = len(cells)
        cells_with_pack = 0
        fresh_packs = 0
        missing_packs: list[str] = []
        stale_packs: list[str] = []
        invalid_packs: list[str] = []
        current_time = time.time()
        freshness_cutoff = current_time - 7 * 24 * 60 * 60
        for cell in cells:
            cell_id = cell.get("id")
            if not cell_id:
                continue
            cell_path = self.workspace / "polaris" / "cells" / cell_id.replace(".", "/")
            pack_path = cell_path / "generated" / "context.pack.json"
            if not pack_path.exists():
                pack_path = cell_path / "context.pack.json"
            if not pack_path.exists():
                missing_packs.append(cell_id)
                continue
            cells_with_pack += 1
            try:
                with open(pack_path, encoding="utf-8") as f:
                    pack_data = json.load(f)
                if "cell_id" not in pack_data and "id" not in pack_data:
                    invalid_packs.append(f"{cell_id}: Missing 'cell_id' or 'id' field")
                    continue
            except json.JSONDecodeError as e:
                invalid_packs.append(f"{cell_id}: Invalid JSON: {e}")
                continue
            except OSError as e:
                invalid_packs.append(f"{cell_id}: Cannot read: {e}")
                continue
            try:
                mtime = pack_path.stat().st_mtime
            except OSError:
                invalid_packs.append(f"{cell_id}: cannot read modification time")
                continue
            if mtime >= freshness_cutoff:
                fresh_packs += 1
            else:
                stale_packs.append(f"{cell_id}: context.pack.json is stale")
        result.evidence.append(
            f"Summary: {fresh_packs}/{cells_with_pack} packs fresh, "
            f"{len(stale_packs)} stale, {len(missing_packs)} missing out of {total_cells} cells"
        )
        if missing_packs:
            result.violations.extend(f"Missing context.pack.json: {cell_id}" for cell_id in missing_packs)
            result.passed = False
        if stale_packs:
            result.violations.extend(stale_packs)
            result.passed = False
        if invalid_packs:
            result.violations.extend(invalid_packs)
            result.passed = False
        return result

    def check_semantic_retrieval_boundary(self) -> FitnessCheckResult:
        """Check that semantic retrieval respects graph boundaries."""
        result = FitnessCheckResult(rule_id="graph_constrained_semantic_retrieval", passed=True)
        graph_constrained_entrypoints = {
            "polaris/cells/context/engine/internal/search_gateway.py",
            "polaris/cells/context/catalog/service.py",
        }
        known_unconstrained = {
            "polaris/kernelone/akashic/semantic_memory.py",
            "polaris/kernelone/akashic/hybrid_memory.py",
            "polaris/kernelone/akashic/memory_manager.py",
            "polaris/kernelone/memory/memory_store.py",
            "polaris/infrastructure/db/repositories/lancedb_code_search.py",
        }
        patterns = [
            "polaris/cells/**/search*.py",
            "polaris/cells/**/*semantic*.py",
            "polaris/cells/**/*descriptor*.py",
            "polaris/kernelone/**/search*.py",
            "polaris/kernelone/**/semantic*.py",
            "polaris/kernelone/**/*memory*.py",
            "polaris/infrastructure/**/search*.py",
        ]
        constrained_patterns = [
            "ContextCatalogService",
            "SearchService",
            "cells.yaml",
            "_load_from_catalog",
            "_filter_by_cell",
            "graph_constrained",
        ]
        unconstrained_patterns = [
            "AkashicSemanticMemory",
            "LanceDB",
            "vector_search",
            "embedding_search",
            "workspace_search",
        ]
        non_compliant: list[str] = []
        compliant: list[str] = []
        for pattern in patterns:
            for file_path in self.workspace.glob(pattern):
                if "test" in file_path.parts or file_path.name.startswith("test_"):
                    continue
                if file_path.suffix != ".py":
                    continue
                rel = str(file_path.relative_to(self.workspace)).replace("\\", "/")
                if rel in graph_constrained_entrypoints:
                    compliant.append(rel)
                    continue
                if rel in known_unconstrained:
                    compliant.append(f"{rel} (workspace-level acceptable)")
                    continue
                try:
                    content = file_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                has_constraint = any(p in content for p in constrained_patterns)
                has_unconstraint = any(p in content for p in unconstrained_patterns)
                if has_constraint:
                    compliant.append(rel)
                elif has_unconstraint:
                    non_compliant.append(rel)
                else:
                    compliant.append(f"{rel} (undetermined)")
        result.evidence.append(f"Compliant sites: {len(compliant)}")
        if non_compliant:
            result.passed = False
            for site in non_compliant:
                result.violations.append(f"Semantic search bypasses graph boundaries: {site}")
        return result

    def check_contract_change_review(self) -> FitnessCheckResult:
        """Check that public contract changes trigger review."""
        result = FitnessCheckResult(rule_id="contract_change_requires_review", passed=True)
        contract_patterns = ["public/contracts.py", "public/contract.py", "contracts.py", "contract.py"]
        adr_pattern = re.compile(r"\badr-\d+[-\w]*\b", re.IGNORECASE)
        vc_pattern = re.compile(r"\bvc-\d{8}[-\w]*\b", re.IGNORECASE)
        review_patterns = [
            re.compile(r"\breview(?:\s|[:\-])", re.IGNORECASE),
            re.compile(r"\bapproved?\b", re.IGNORECASE),
            re.compile(r"\bchecked?\b", re.IGNORECASE),
            re.compile(r"\bverified?\b", re.IGNORECASE),
            re.compile(r"\b LGTM \b", re.IGNORECASE),
            re.compile(r"\blooks?\s+good\b", re.IGNORECASE),
            re.compile(r"\bgovernance\b", re.IGNORECASE),
        ]
        cells_dir = self.workspace / "polaris" / "cells"
        contract_files: list[Path] = []
        if cells_dir.exists():
            for cell_dir in cells_dir.iterdir():
                if not cell_dir.is_dir():
                    continue
                for pattern in contract_patterns:
                    for contract_path in cell_dir.rglob(pattern):
                        if "/internal/" in str(contract_path) or "/test" in str(contract_path):
                            continue
                        contract_files.append(contract_path)
        result.evidence.append(f"Found {len(contract_files)} public contract file(s)")
        if not contract_files:
            result.warnings.append("No public contract files found")
            return result
        files_with_changes = 0
        files_without_review: list[str] = []
        for cf in contract_files:
            command = [
                "git",
                "log",
                "--format=%H|%ad|%s",
                "--date=iso",
                "--since=30.days",
                "--",
                str(cf),
            ]
            try:
                proc = subprocess.run(
                    command,
                    cwd=str(self.workspace),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            stdout = proc.stdout.strip()
            if not stdout:
                continue
            files_with_changes += 1
            lines = stdout.split("\n")
            if lines and lines[0]:
                parts = lines[0].split("|", 2)
                if len(parts) >= 3:
                    commit_message = parts[2]
                    has_review = bool(adr_pattern.search(commit_message) or vc_pattern.search(commit_message))
                    if not has_review:
                        for pat in review_patterns:
                            if pat.search(commit_message.lower()):
                                has_review = True
                                break
                    if not has_review:
                        files_without_review.append(f"{cf.relative_to(self.workspace)}: {commit_message[:60]}...")
        result.evidence.append(f"Checked {files_with_changes} file(s) with recent changes (last 30 days)")
        if files_with_changes == 0:
            result.evidence.append("No contract changes in the last 30 days - rule not applicable")
        elif not files_without_review:
            result.evidence.append("All contract changes have review evidence")
        else:
            result.passed = False
            result.violations.append(f"{len(files_without_review)} contract change(s) lack review evidence")
            for v in files_without_review:
                result.violations.append(v)
        return result

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
