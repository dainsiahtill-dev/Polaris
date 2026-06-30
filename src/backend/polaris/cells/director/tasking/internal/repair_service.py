"""Repair Service - Automatic repair loop for failed QA.

Manages repair iterations when independent audit fails.
Coordinates with TaskService and EvidenceCollector for retry logic.

Migrated from: scripts/director/iteration/verification.py (run_repair_loop)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeAlias

from polaris.cells.qa.audit_verdict.public.contracts import (
    QaFailureClassificationV1,
    build_qa_failure_classification_v1,
)

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from collections.abc import Callable

    from polaris.domain.verification import ProgressDelta, SoftCheckResult
    from polaris.domain.verification.evidence_collector import EvidenceCollector, EvidencePackage

QAFailureClassification: TypeAlias = QaFailureClassificationV1


def _build_repair_qa_failure_classification(**kwargs: Any) -> QaFailureClassificationV1:
    return build_qa_failure_classification_v1(**kwargs)


@dataclass
class RepairResult:
    """Result of a repair attempt."""

    success: bool
    iteration: int
    changes_made: list[str] = field(default_factory=list)
    error_message: str = ""
    evidence_package: EvidencePackage | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "iteration": self.iteration,
            "changes_made": self.changes_made,
            "error_message": self.error_message,
            "has_evidence": self.evidence_package is not None,
        }


@dataclass
class RepairContext:
    """Context for repair operations."""

    task_id: str
    build_round: int = 0
    # F25 (2026-06-16): raised 4 -> 8 for cross-file completeness (#54). Complex
    # multi-file projects (L4/L5) need more repair rounds to create+wire every
    # referenced file; the INDEPENDENT stall-detector (should_attempt_repair:
    # is_stalled after stall_threshold no-progress rounds) bounds no-progress
    # loops, so raising the cap only extends repairs that keep resolving files.
    # Simple projects converge in 1-2 rounds and never reach the cap (no L2
    # regression). Integrity-preserving: forces REAL file creation, not stubs.
    max_build_rounds: int = 8
    stall_rounds: int = 0
    stall_threshold: int = 2
    previous_missing_targets: list[str] = field(default_factory=list)
    previous_unresolved_imports: list[str] = field(default_factory=list)
    original_plan: str = ""
    target_files: list[str] = field(default_factory=list)


class RepairService:
    """Service for managing automatic repair loops.

    When independent audit fails, this service coordinates repair attempts
    with proper tracking to prevent infinite loops.
    """

    def __init__(
        self,
        repair_executor: Callable[[str, list[str]], tuple[list[str], str | None]] | None = None,
    ) -> None:
        """Initialize repair service.

        Args:
            repair_executor: Function(brief, target_files) -> (changed_files, error)
                             If None, repairs will fail immediately.
        """
        self._repair_executor = repair_executor
        self._repair_history: list[RepairResult] = []

    def classify_qa_failure(
        self,
        *,
        audit_accepted: bool = False,
        soft_check: SoftCheckResult | None = None,
        progress: ProgressDelta | None = None,
        context: RepairContext | None = None,
        qa_feedback: str = "",
        evidence_refs: list[str] | tuple[str, ...] | None = None,
    ) -> QAFailureClassification:
        """Classify QA failure routing before starting Director repair."""

        refs = tuple(str(ref) for ref in (evidence_refs or ()) if str(ref).strip())
        if audit_accepted:
            return _build_repair_qa_failure_classification(
                failure_class="passed",
                route="no_action",
                reason="Audit passed, no repair needed",
                repairable_by_director=False,
                severity="info",
                evidence_refs=refs,
            )

        if context is not None and context.build_round >= context.max_build_rounds:
            return _build_repair_qa_failure_classification(
                failure_class="resource_budget_exhausted",
                route="ce_replan_required",
                reason=f"Build budget exhausted ({context.build_round}/{context.max_build_rounds})",
                repairable_by_director=False,
                severity="high",
                requires_ce_replan=True,
                evidence_refs=refs,
            )

        is_stalled = bool(getattr(progress, "is_stalled", False))
        if (
            context is not None
            and is_stalled
            and context.build_round >= 2
            and context.stall_rounds >= context.stall_threshold
        ):
            return _build_repair_qa_failure_classification(
                failure_class="progress_stalled",
                route="ce_replan_required",
                reason=f"Progress stalled for {context.stall_rounds} rounds",
                repairable_by_director=False,
                severity="high",
                requires_ce_replan=True,
                evidence_refs=refs,
            )

        missing_targets = list(getattr(soft_check, "missing_targets", []) or [])
        if missing_targets:
            return _build_repair_qa_failure_classification(
                failure_class="incomplete_materialization",
                route="execution_boundary_retry",
                reason=(
                    "Required target files were not materialized; route through "
                    f"TaskBoundary/Director execution control instead of local repair: {missing_targets}"
                ),
                repairable_by_director=False,
                evidence_refs=refs,
            )

        unresolved_imports = list(getattr(soft_check, "unresolved_imports", []) or [])
        if unresolved_imports:
            return _build_repair_qa_failure_classification(
                failure_class="implementation_defect",
                route="director_repair",
                reason=f"Unresolved imports to fix: {unresolved_imports}",
                repairable_by_director=True,
                evidence_refs=refs,
            )

        feedback = qa_feedback.lower()
        if any(term in feedback for term in ("security policy", "policy violation", "path traversal", "unauthorized")):
            return _build_repair_qa_failure_classification(
                failure_class="security_policy_violation",
                route="hard_stop",
                reason="QA failure indicates a security or authorization policy violation",
                repairable_by_director=False,
                severity="critical",
                evidence_refs=refs,
            )
        if any(
            term in feedback
            for term in ("scope mismatch", "outside scope", "target file not declared", "scope expansion")
        ):
            return _build_repair_qa_failure_classification(
                failure_class="scope_mismatch",
                route="ce_replan_required",
                reason="QA failure requires CE scope or blueprint replanning",
                repairable_by_director=False,
                severity="high",
                requires_ce_replan=True,
                evidence_refs=refs,
            )
        if any(
            term in feedback
            for term in ("contract ambiguous", "ambiguous requirement", "missing acceptance", "clarification")
        ):
            return _build_repair_qa_failure_classification(
                failure_class="contract_ambiguous",
                route="pm_revision_required",
                reason="QA failure requires PM contract clarification",
                repairable_by_director=False,
                severity="high",
                requires_pm_revision=True,
                evidence_refs=refs,
            )
        if any(term in feedback for term in ("acceptance invalid", "invalid acceptance", "undeclared acceptance")):
            return _build_repair_qa_failure_classification(
                failure_class="acceptance_invalid",
                route="pm_revision_required",
                reason="QA failure indicates invalid or undeclared acceptance criteria",
                repairable_by_director=False,
                severity="high",
                requires_pm_revision=True,
                evidence_refs=refs,
            )
        if any(term in feedback for term in ("tool_dispatch_dropped", "tool dispatch dropped")):
            return _build_repair_qa_failure_classification(
                failure_class="tool_dispatch_dropped",
                route="hard_stop",
                reason="Execution control plane dropped provider tool calls before dispatch",
                repairable_by_director=False,
                severity="critical",
                evidence_refs=refs,
            )
        if any(
            term in feedback
            for term in ("missing_entrypoint_target", "missing entrypoint", "manifest references local entrypoint")
        ):
            return _build_repair_qa_failure_classification(
                failure_class="missing_entrypoint_target",
                route="ce_replan_required",
                reason="Manifest entrypoint contract requires CE replanning or downstream artifact declaration",
                repairable_by_director=False,
                severity="high",
                requires_ce_replan=True,
                evidence_refs=refs,
            )
        if any(
            term in feedback
            for term in ("test environment", "environment failure", "network timeout", "dependency outage")
        ):
            return _build_repair_qa_failure_classification(
                failure_class="test_environment_failure",
                route="infra_retry",
                reason="QA failure appears to be caused by test infrastructure or environment",
                repairable_by_director=False,
                severity="medium",
                evidence_refs=refs,
            )

        return _build_repair_qa_failure_classification(
            failure_class="implementation_defect",
            route="director_repair",
            reason="QA failed with an implementation defect, attempting Director repair",
            repairable_by_director=True,
            evidence_refs=refs,
        )

    def should_attempt_repair(
        self,
        audit_accepted: bool,
        soft_check: SoftCheckResult,
        progress: ProgressDelta,
        context: RepairContext,
    ) -> tuple[bool, str]:
        """Determine if repair should be attempted.

        Args:
            audit_accepted: Whether audit passed
            soft_check: Current soft check result
            progress: Progress delta from previous iteration
            context: Repair context

        Returns:
            Tuple of (should_repair, reason)
        """
        classification = self.classify_qa_failure(
            audit_accepted=audit_accepted,
            soft_check=soft_check,
            progress=progress,
            context=context,
        )
        return classification.repairable_by_director, classification.reason

    async def run_repair(
        self,
        qa_feedback: str,
        context: RepairContext,
        iteration: int = 1,
        evidence_collector: EvidenceCollector | None = None,
    ) -> RepairResult:
        """Execute a single repair iteration.

        Args:
            qa_feedback: QA feedback explaining issues
            context: Repair context
            iteration: Current repair iteration number
            evidence_collector: Optional collector for evidence

        Returns:
            RepairResult with outcome
        """
        if not self._repair_executor:
            return RepairResult(
                success=False,
                iteration=iteration,
                error_message="No repair executor configured",
            )

        # Extract missing files from QA feedback
        missing_files = self._extract_missing_files(qa_feedback)

        # Compute repair scope
        repair_scope = self._compute_repair_scope(
            context.target_files,
            missing_files,
        )

        if not repair_scope:
            return RepairResult(
                success=False,
                iteration=iteration,
                error_message="No repair scope determined",
            )

        # Build repair brief
        repair_brief = self._build_repair_brief(
            context.original_plan,
            qa_feedback,
            repair_scope,
        )

        # Execute repair
        try:
            changed_files, error = self._repair_executor(repair_brief, repair_scope)

            if error:
                result = RepairResult(
                    success=False,
                    iteration=iteration,
                    error_message=error,
                )
            else:
                result = RepairResult(
                    success=True,
                    iteration=iteration,
                    changes_made=changed_files,
                )

                # Record evidence if collector provided
                if evidence_collector:
                    for file_path in changed_files:
                        evidence_collector.record_file_change(
                            path=file_path,
                            change_type="repaired",
                        )
                    result.evidence_package = evidence_collector.get_package()

            self._repair_history.append(result)
            return result

        except Exception as e:
            logger.error("Repair iteration %d failed: %s", iteration, e, exc_info=True)
            result = RepairResult(
                success=False,
                iteration=iteration,
                error_message=str(e),
            )
            self._repair_history.append(result)
            return result

    async def run_repair_loop(
        self,
        qa_feedback: str,
        context: RepairContext,
        max_repair_rounds: int = 2,
        evidence_collector: EvidenceCollector | None = None,
    ) -> tuple[bool, list[RepairResult], str]:
        """Run repair loop until success or exhaustion.

        Args:
            qa_feedback: Initial QA feedback
            context: Repair context
            max_repair_rounds: Maximum repair attempts
            evidence_collector: Optional evidence collector

        Returns:
            Tuple of (final_success, all_results, final_message)
        """
        results: list[RepairResult] = []
        classification = self.classify_qa_failure(
            qa_feedback=qa_feedback,
            context=context,
        )
        if not classification.repairable_by_director:
            return False, results, classification.reason

        for round_num in range(1, max_repair_rounds + 1):
            result = await self.run_repair(
                qa_feedback=qa_feedback,
                context=context,
                iteration=round_num,
                evidence_collector=evidence_collector,
            )
            results.append(result)

            if result.success:
                return True, results, f"Repair succeeded after {round_num} attempts"

            # Update feedback for next round based on error
            qa_feedback = f"{qa_feedback}\n\nRepair attempt {round_num} failed: {result.error_message}"

        return False, results, f"Repair failed after {max_repair_rounds} attempts"

    def _extract_missing_files(self, qa_output: str) -> list[str]:
        """Extract missing file references from QA output."""
        if not qa_output:
            return []

        # Look for file paths in the output
        pattern = r"[`'\"]?([A-Za-z0-9_\-./\\]+?\.[A-Za-z0-9_]+)[`'\"]?"
        matches = re.findall(pattern, qa_output)

        # Filter to likely source files
        source_extensions = (
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".go",
            ".rs",
            ".java",
            ".vue",
            ".svelte",
            ".php",
            ".rb",
            ".cs",
        )

        candidates = []
        for raw in matches:
            normalized = raw.replace("\\", "/").strip()
            if normalized and normalized.endswith(source_extensions) and normalized not in candidates:
                candidates.append(normalized)

        return candidates

    def _compute_repair_scope(
        self,
        target_files: list[str],
        missing_files: list[str],
    ) -> list[str]:
        """Compute the scope for repair."""
        scope = set()

        # Add original targets
        for f in target_files:
            scope.add(f)

        # Add missing files from QA
        for f in missing_files:
            scope.add(f)

        return sorted(scope)

    def _build_repair_brief(
        self,
        original_plan: str,
        qa_feedback: str,
        repair_scope: list[str],
    ) -> str:
        """Build repair brief for executor."""
        scope_text = "\n".join(f"- {path}" for path in repair_scope)

        return f"""{original_plan}

=== QA 反馈需修复 ===
{qa_feedback}

=== 修复范围 ===
{scope_text}

请修复上述问题，确保代码可编译、无语法错误、满足验收标准。
"""

    def get_repair_history(self) -> list[RepairResult]:
        """Get history of all repair attempts."""
        return self._repair_history.copy()

    def get_stats(self) -> dict[str, Any]:
        """Get repair statistics."""
        if not self._repair_history:
            return {"total": 0, "successful": 0, "failed": 0, "success_rate": 0.0}

        total = len(self._repair_history)
        successful = sum(1 for r in self._repair_history if r.success)

        return {
            "total": total,
            "successful": successful,
            "failed": total - successful,
            "success_rate": successful / total if total > 0 else 0.0,
        }
