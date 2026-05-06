# SHIM: mig-application-batch1 — migration shim pending full Cell migration (2026-03-20)
"""Application-layer orchestrator for the QA (Quality Assurance) domain.

This module provides a high-level facade that encapsulates the QA audit
lifecycle: plan audit, execute review, and compile verdict.  Delivery layers
(CLI, HTTP, TUI) use this orchestrator instead of importing Cell internals
directly.

Call chain::

    delivery -> QaOrchestrator -> cells.qa.audit_verdict.public
                                -> cells.audit.verdict.public

Usage example::

    >>> from polaris.application.orchestration import (
    ...     QaOrchestrator,
    ...     QaAuditConfig,
    ... )
    >>> config = QaAuditConfig(
    ...     workspace="/path/to/project",
    ...     run_id="qa-run-001",
    ...     auto_audit=True,
    ...     min_coverage=0.8,
    ... )
    >>> orch = QaOrchestrator(config)
    >>> # Run full lifecycle
    >>> result = await orch.run_audit_lifecycle(
    ...     task_id="TASK-123",
    ...     task_subject="User login feature",
    ...     changed_files=["src/auth.py", "tests/test_auth.py"],
    ... )
    >>> print(result.verdict.verdict)

Architecture constraints (AGENTS.md):
    - Imports ONLY from Cell ``public/`` boundaries and ``kernelone`` contracts.
    - NEVER imports from ``internal/`` at module level.
    - All text I/O uses explicit UTF-8.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "QaAuditConfig",
    "QaAuditLifecycleResult",
    "QaOrchestrator",
    "QaOrchestratorError",
    "QaReviewResult",
    "QaVerdictResult",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class QaOrchestratorError(RuntimeError):
    """Application-layer error for QA orchestration operations.

    Wraps lower-level Cell or KernelOne errors so delivery never catches
    infrastructure-specific exception types.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "qa_orchestrator_error",
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cause = cause


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QaReviewResult:
    """Immutable snapshot of a single QA review outcome."""

    review_id: str
    target: str
    status: str  # "completed" | "failed" | "skipped"
    issue_count: int = 0
    findings: tuple[str, ...] = ()
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QaVerdictResult:
    """Immutable snapshot of a compiled QA verdict."""

    verdict: str  # "PASS" | "FAIL" | "NEEDS_REVIEW"
    verdict_id: str
    summary: str
    findings: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QaAuditLifecycleResult:
    """Immutable snapshot of a full QA audit lifecycle outcome."""

    success: bool
    task_id: str
    workspace: str
    review: QaReviewResult | None = None
    verdict: QaVerdictResult | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class QaAuditConfig:
    """Configuration for QA audit execution."""

    workspace: str
    run_id: str = ""
    criteria: dict[str, Any] = field(default_factory=dict)
    evidence_paths: tuple[str, ...] = ()
    auto_audit: bool = True
    min_coverage: float = 0.7


# ---------------------------------------------------------------------------
# QaOrchestrator
# ---------------------------------------------------------------------------


class QaOrchestrator:
    """High-level facade for the QA audit lifecycle.

    Responsibilities:
        1. Plan audit – gather evidence and criteria for the audit scope.
        2. Execute review – run the QA review via ``qa.audit_verdict`` Cell.
        3. Compile verdict – aggregate review findings into a final verdict
           via ``audit.verdict`` Cell.

    The orchestrator is stateless and cheap to construct.  All mutable
    state (QA service handles, audit state) is obtained lazily inside each
    public method so that import-time side effects are avoided.
    """

    def __init__(self, config: QaAuditConfig) -> None:
        self._config = config
        self._workspace = str(config.workspace)
        self._run_id = str(config.run_id or "")
        self._qa_service: Any | None = None
        self._audit_service: Any | None = None

    # -- lazy service resolution --------------------------------------------

    def _get_qa_service(self) -> Any:
        """Lazily resolve ``QAService`` from the ``qa.audit_verdict`` Cell."""
        if self._qa_service is not None:
            return self._qa_service
        try:
            from polaris.cells.qa.audit_verdict.public import QAConfig, QAService

            cfg = QAConfig(
                workspace=self._workspace,
                enable_auto_audit=self._config.auto_audit,
                min_test_coverage=self._config.min_coverage,
            )
            self._qa_service = QAService(config=cfg)
            return self._qa_service
        except (ImportError, RuntimeError, ValueError) as exc:
            raise QaOrchestratorError(
                f"Failed to resolve QAService: {exc}",
                code="qa_service_resolution_error",
                cause=exc,
            ) from exc

    def _get_audit_service(self) -> Any:
        """Lazily resolve ``IndependentAuditService`` from ``audit.verdict`` Cell."""
        if self._audit_service is not None:
            return self._audit_service
        try:
            from polaris.cells.audit.verdict.public import IndependentAuditService

            self._audit_service = IndependentAuditService()
            return self._audit_service
        except (ImportError, RuntimeError, ValueError) as exc:
            raise QaOrchestratorError(
                f"Failed to resolve IndependentAuditService: {exc}",
                code="audit_service_resolution_error",
                cause=exc,
            ) from exc

    # -- step 1: plan audit -------------------------------------------------

    def plan_audit(
        self,
        *,
        task_id: str,
        criteria: Mapping[str, Any] | None = None,
        evidence_paths: str | tuple[str, ...] | list[str] | None = None,
    ) -> dict[str, Any]:
        """Plan an audit by gathering criteria and evidence paths.

        This step validates inputs, normalizes criteria, and returns an
        audit-plan dict that can be passed to ``execute_review``.

        Args:
            task_id: The task identifier to audit.
            criteria: Optional audit criteria (overrides config defaults).
            evidence_paths: Optional evidence file paths (overrides config).

        Returns:
            Audit plan dict with keys:
            ``task_id``, ``workspace``, ``run_id``, ``criteria``,
            ``evidence_paths``.

        Raises:
            QaOrchestratorError: if planning fails due to invalid input.
        """
        if not task_id or not str(task_id).strip():
            raise QaOrchestratorError(
                "task_id is required for audit planning",
                code="missing_task_id",
            )

        merged_criteria = dict(self._config.criteria)
        if criteria is not None:
            merged_criteria.update(criteria)

        # Normalize evidence_paths: handle string, None, or iterable inputs
        # to avoid string-iteration bug (a string "path" would iterate over characters)
        if evidence_paths is None:
            merged_evidence = tuple(self._config.evidence_paths)
        elif isinstance(evidence_paths, str):
            merged_evidence = (evidence_paths,) if evidence_paths.strip() else ()
        else:
            merged_evidence = tuple(str(p) for p in evidence_paths if str(p).strip())

        return {
            "task_id": str(task_id).strip(),
            "workspace": self._workspace,
            "run_id": self._run_id,
            "criteria": merged_criteria,
            "evidence_paths": merged_evidence,
        }

    # -- step 2: execute review ---------------------------------------------

    async def execute_review(
        self,
        *,
        task_id: str,
        task_subject: str = "",
        changed_files: list[str] | None = None,
        criteria: Mapping[str, Any] | None = None,
    ) -> QaReviewResult:
        """Execute a QA review for the given task.

        This delegates to ``QAService.audit_task`` and maps the raw
        ``AuditResult`` into a stable ``QaReviewResult``.

        Args:
            task_id: The task identifier.
            task_subject: Human-readable task subject.
            changed_files: List of changed file paths to review.
            criteria: Optional audit criteria.

        Returns:
            ``QaReviewResult`` snapshot.

        Raises:
            QaOrchestratorError: if the review Cell raises an unexpected
                exception.
        """
        service = self._get_qa_service()
        files = list(changed_files or [])

        try:
            raw = await service.audit_task(
                task_id=str(task_id),
                task_subject=str(task_subject or task_id),
                changed_files=files,
            )

            issues = raw.issues if raw.issues is not None else []
            findings = tuple(str(i.get("message", "")) for i in issues if isinstance(i, dict))
            status = "completed" if raw.verdict in ("PASS", "FAIL") else "skipped"

            return QaReviewResult(
                review_id=str(raw.audit_id),
                target=str(raw.target),
                status=status,
                issue_count=len(issues),
                findings=findings,
                metadata={
                    "verdict": str(raw.verdict),
                    "metrics": dict(raw.metrics) if isinstance(raw.metrics, dict) else {},
                    "timestamp": raw.timestamp.isoformat()
                    if hasattr(raw.timestamp, "isoformat")
                    else str(raw.timestamp),
                },
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise QaOrchestratorError(
                f"QA review execution failed: {exc}",
                code="qa_review_failed",
                cause=exc,
            ) from exc

    # -- step 3: compile verdict --------------------------------------------

    async def compile_verdict(
        self,
        *,
        review_result: QaReviewResult,
        task_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> QaVerdictResult:
        """Compile a final verdict from one or more review results.

        This delegates to ``audit.verdict`` Cell to produce a canonical
        verdict that can be consumed by downstream gates.

        Args:
            review_result: The review result to compile into a verdict.
            task_id: The task identifier.
            metadata: Optional metadata to attach to the verdict.

        Returns:
            ``QaVerdictResult`` snapshot.

        Raises:
            QaOrchestratorError: if verdict compilation fails.
        """
        try:
            from polaris.cells.audit.verdict.public import (
                RunAuditVerdictCommandV1,
            )

            command = RunAuditVerdictCommandV1(
                workspace=self._workspace,
                run_id=self._run_id or f"qa-{task_id}",
                task_id=str(task_id),
                metadata=dict(metadata or {}),
            )

            service = self._get_audit_service()
            raw = await service.run_verdict(command)

            verdict_str = str(raw.verdict or "NEEDS_REVIEW").strip().upper()
            if verdict_str not in {"PASS", "FAIL", "NEEDS_REVIEW"}:
                verdict_str = "NEEDS_REVIEW"

            suggestions_raw = raw.details.get("suggestions") if isinstance(raw.details, dict) else None
            suggestions = tuple(str(s) for s in suggestions_raw) if isinstance(suggestions_raw, (list, tuple)) else ()

            return QaVerdictResult(
                verdict=verdict_str,
                verdict_id=f"verdict-{review_result.review_id}",
                summary=str(raw.details.get("summary", "")) if isinstance(raw.details, dict) else "",
                findings=review_result.findings,
                suggestions=suggestions,
                score=float(raw.details.get("score", 0.0)) if isinstance(raw.details, dict) else 0.0,
                metadata=dict(raw.details) if isinstance(raw.details, dict) else {},
            )
        except (ImportError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise QaOrchestratorError(
                f"QA verdict compilation failed: {exc}",
                code="qa_verdict_failed",
                cause=exc,
            ) from exc

    # -- convenience: full lifecycle ----------------------------------------

    async def run_audit_lifecycle(
        self,
        *,
        task_id: str,
        task_subject: str = "",
        changed_files: list[str] | None = None,
        criteria: Mapping[str, Any] | None = None,
    ) -> QaAuditLifecycleResult:
        """Run the complete QA audit lifecycle.

        This is the **primary high-level entry point** for delivery layers.
        It orchestrates plan -> review -> verdict while keeping all
        Cell-internal details hidden.

        Args:
            task_id: The task identifier to audit.
            task_subject: Human-readable task subject.
            changed_files: List of changed file paths to review.
            criteria: Optional audit criteria.

        Returns:
            ``QaAuditLifecycleResult`` snapshot.
        """
        logger.info(
            "qa audit lifecycle start: task_id=%s workspace=%s",
            task_id,
            self._workspace,
        )

        # 1. Plan
        plan = self.plan_audit(
            task_id=task_id,
            criteria=criteria,
            evidence_paths=tuple(changed_files or ()),
        )

        # 2. Review
        review = await self.execute_review(
            task_id=task_id,
            task_subject=task_subject,
            changed_files=changed_files,
            criteria=plan.get("criteria"),
        )

        # 3. Verdict
        verdict = await self.compile_verdict(
            review_result=review,
            task_id=task_id,
        )

        success = review.status == "completed" and verdict.verdict == "PASS"

        logger.info(
            "qa audit lifecycle complete: task_id=%s success=%s verdict=%s",
            task_id,
            success,
            verdict.verdict,
        )

        return QaAuditLifecycleResult(
            success=success,
            task_id=task_id,
            workspace=self._workspace,
            review=review,
            verdict=verdict,
            notes=f"Audit lifecycle completed with verdict={verdict.verdict}",
        )

    # -- query helpers ------------------------------------------------------

    async def query_verdict(
        self,
        *,
        task_id: str | None = None,
        include_artifacts: bool = True,
    ) -> dict[str, Any]:
        """Query the current verdict state for a task or run.

        Args:
            task_id: Optional task identifier filter.
            include_artifacts: Whether to include artifact details.

        Returns:
            Verdict state dict.

        Raises:
            QaOrchestratorError: if the query fails.
        """
        try:
            from polaris.cells.audit.verdict.public import QueryAuditVerdictV1

            query = QueryAuditVerdictV1(
                workspace=self._workspace,
                run_id=self._run_id or None,
                task_id=str(task_id) if task_id else None,
                include_artifacts=include_artifacts,
            )

            service = self._get_audit_service()
            raw = await service.query_verdict(query)

            return {
                "ok": bool(raw.ok),
                "status": str(raw.status),
                "verdict": str(raw.verdict) if raw.verdict else None,
                "details": dict(raw.details) if isinstance(raw.details, dict) else {},
                "error_code": str(raw.error_code) if raw.error_code else None,
                "error_message": str(raw.error_message) if raw.error_message else None,
            }
        except (ImportError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise QaOrchestratorError(
                f"QA verdict query failed: {exc}",
                code="qa_verdict_query_failed",
                cause=exc,
            ) from exc
