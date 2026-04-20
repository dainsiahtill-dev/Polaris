"""Repair Service - Automatic repair loop for failed QA.

Manages repair iterations when independent audit fails.
Coordinates with TaskService and EvidenceCollector for retry logic.

Migrated from: scripts/director/iteration/verification.py (run_repair_loop)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from collections.abc import Callable

    from polaris.domain.verification import ProgressDelta, SoftCheckResult
    from polaris.domain.verification.evidence_collector import EvidenceCollector, EvidencePackage


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
    max_build_rounds: int = 4
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
        if audit_accepted:
            return False, "Audit passed, no repair needed"

        # Check build budget
        if context.build_round >= context.max_build_rounds:
            return False, f"Build budget exhausted ({context.build_round}/{context.max_build_rounds})"

        # Check stall threshold
        if progress.is_stalled and context.build_round >= 2 and context.stall_rounds >= context.stall_threshold:
            return False, f"Progress stalled for {context.stall_rounds} rounds"

        # Check for resolvable issues
        if soft_check.missing_targets:
            return True, f"Missing targets to create: {soft_check.missing_targets}"

        if soft_check.unresolved_imports:
            return True, f"Unresolved imports to fix: {soft_check.unresolved_imports}"

        # Generic repair for QA failure
        return True, "QA failed, attempting repair"

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
