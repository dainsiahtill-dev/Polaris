# SHIM: mig-application-batch1 — migration shim pending full Cell migration (2026-03-20)
"""Application-layer orchestrator for the Director domain.

This module provides a high-level facade that encapsulates the Director task
execution workflow: task discovery, role-session execution, result aggregation,
and status updates.  Delivery layers (CLI, HTTP, TUI) use this orchestrator
instead of importing Cell internals directly.

Call chain::

    delivery -> DirectorOrchestrator -> cells.director.execution.public
                                      -> cells.roles.runtime.public
                                      -> cells.runtime.task_runtime.public
                                      -> kernelone.*

Architecture constraints (AGENTS.md):
    - Imports ONLY from Cell ``public/`` boundaries and ``kernelone`` contracts.
    - NEVER imports from ``internal/`` at module level.
    - All text I/O uses explicit UTF-8.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _record_director_decision_safe(workspace: str, payload: Mapping[str, Any]) -> None:
    """Append a Resident decision for a Director task execution. Never raises.

    This feeds the ``resident.autonomy`` decision trace (the fuel for its
    meta-cognition / skill / counterfactual loops) from the canonical
    application-layer execution path, which previously recorded nothing.

    Skipped when running inside a workflow context (``KERNELONE_WORKFLOW_ID``
    set), because the ``workflow_runtime`` engine records its own Resident
    decision for the same task and we must not double-count.  Decision capture
    is observability, not a task-execution dependency, so any failure is
    swallowed.
    """
    if str(os.environ.get("KERNELONE_WORKFLOW_ID", "")).strip():
        return
    try:
        from polaris.cells.resident.autonomy.public.service import record_resident_decision

        record_resident_decision(workspace, payload)
    except Exception:  # noqa: BLE001 - resident capture must never break director execution
        logger.debug(
            "resident decision capture skipped for workspace=%s",
            workspace,
            exc_info=True,
        )


__all__ = [
    "DirectorExecutionConfig",
    "DirectorIterationResult",
    "DirectorOrchestrator",
    "DirectorOrchestratorError",
    "DirectorTaskResult",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DirectorOrchestratorError(RuntimeError):
    """Application-layer error for Director orchestration operations.

    Wraps lower-level Cell or KernelOne errors so delivery never catches
    infrastructure-specific exception types.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "director_orchestrator_error",
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cause = cause


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DirectorTaskResult:
    """Immutable snapshot of a single Director task execution outcome."""

    task_id: str
    subject: str
    success: bool
    status: str  # "completed" | "failed" | "skipped"
    response_length: int = 0
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DirectorIterationResult:
    """Immutable snapshot of a Director iteration outcome."""

    success: bool
    iteration: int
    tasks_processed: int
    tasks_succeeded: int
    tasks_failed: int
    results: tuple[DirectorTaskResult, ...]
    notes: str = ""


@dataclass(frozen=True, slots=True)
class DirectorExecutionConfig:
    """Configuration for Director execution."""

    workspace: str
    model: str = ""
    max_workers: int = 3
    execution_mode: str = "parallel"  # "parallel" | "serial"
    timeout_seconds: int = 3600

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "execution_mode",
            "serial" if str(self.execution_mode or "").strip().lower() == "serial" else "parallel",
        )
        object.__setattr__(
            self,
            "max_workers",
            max(1, int(self.max_workers)),
        )


# ---------------------------------------------------------------------------
# DirectorOrchestrator
# ---------------------------------------------------------------------------


class DirectorOrchestrator:
    """High-level facade for Director task execution lifecycle.

    Responsibilities:
        1. Task discovery – query the task board for ready tasks.
        2. Adapter execution – run each task through the canonical
           ``roles.adapters`` Director adapter, which owns write receipts and
           materialization quality gates.
        3. Result aggregation – collect per-task results into an iteration
           snapshot.
        4. Status bookkeeping – update task-board state without exposing
           internal ORM models.

    The orchestrator is stateless and cheap to construct.  All mutable
    state (task board, runtime service) is obtained lazily inside each
    public method so that import-time side effects are avoided.
    """

    def __init__(self, config: DirectorExecutionConfig) -> None:
        self._config = config
        self._workspace = str(config.workspace)
        self._task_board: Any | None = None

    # -- lazy service resolution --------------------------------------------

    def _get_task_board(self) -> Any:
        """Lazily resolve the TaskBoard from the ``runtime.task_runtime`` Cell."""
        if self._task_board is not None:
            return self._task_board
        try:
            from polaris.cells.runtime.task_runtime.public.task_board_contract import (
                TaskBoard,
            )

            self._task_board = TaskBoard(workspace=self._workspace)
            return self._task_board
        except (ImportError, RuntimeError, ValueError) as exc:
            raise DirectorOrchestratorError(
                f"Failed to resolve TaskBoard: {exc}",
                code="task_board_resolution_error",
                cause=exc,
            ) from exc

    # -- task discovery -----------------------------------------------------

    def get_ready_tasks(self) -> list[dict[str, Any]]:
        """Return ready tasks from the task board.

        Returns:
            List of task dicts (each guaranteed to have at least
            ``id`` and ``subject`` keys).

        Raises:
            DirectorOrchestratorError: if the task board query fails.
        """
        board = self._get_task_board()
        try:
            raw_tasks = board.get_ready_tasks()
            return [task.to_dict() for task in raw_tasks]
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise DirectorOrchestratorError(
                f"Task board query failed: {exc}",
                code="task_board_query_error",
                cause=exc,
            ) from exc

    # -- single task execution ----------------------------------------------

    async def execute_task(self, task: Mapping[str, Any]) -> DirectorTaskResult:
        """Execute one task through the canonical Director role adapter.

        This method is kept as an application-layer facade for older delivery
        callers, but it no longer performs role-runtime-only execution. A task
        is completed only when the adapter returns a successful materialization
        result with its own write-receipt and quality metadata.

        Args:
            task: Task dict with at least ``id`` and ``subject`` keys.

        Returns:
            ``DirectorTaskResult`` snapshot.

        Raises:
            DirectorOrchestratorError: if the adapter cannot be resolved.
        """
        task_id = str(task.get("id", "unknown"))
        subject = str(task.get("subject") or task.get("title") or "unknown")
        board = self._get_task_board()
        adapter_result: dict[str, Any]
        try:
            from polaris.cells.roles.adapters.public.service import create_role_adapter

            adapter = create_role_adapter("director", self._workspace)
            adapter_result = dict(
                await adapter.execute(
                    task_id,
                    self._build_adapter_input(task),
                    {
                        "workspace": self._workspace,
                        "metadata": {
                            "source": "application.director_orchestrator",
                            "delivery_compat": True,
                        },
                    },
                )
            )
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            raise DirectorOrchestratorError(
                f"Director adapter execution failed: {exc}",
                code="director_adapter_execution_failed",
                cause=exc,
            ) from exc

        success = bool(adapter_result.get("success"))
        status = "completed" if success else "failed"
        error = "" if success else str(adapter_result.get("error") or adapter_result.get("error_code") or "").strip()
        changed_files = self._normalize_string_list(adapter_result.get("changed_files"))
        if not changed_files:
            changed_files = sorted(
                {
                    *self._normalize_string_list(adapter_result.get("new_files")),
                    *self._normalize_string_list(adapter_result.get("modified_files")),
                }
            )
        metadata = {
            "adapter": "roles.adapters.director",
            "adapter_result": adapter_result,
            "changed_files": changed_files,
            "qa_required_for_final_verdict": bool(adapter_result.get("qa_required_for_final_verdict", True)),
        }

        try:
            normalized_id = self._normalize_task_id(task_id)
            board.update(normalized_id, status=status, metadata=metadata)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Failed to update Director task %s after adapter execution: %s", task_id, exc)

        _record_director_decision_safe(
            self._workspace,
            {
                "run_id": task_id,
                "actor": "director",
                "stage": "task_execution",
                "summary": f"Director executed task {task_id}: {subject}",
                "strategy_tags": [
                    "orchestrator_direct",
                    f"{self._config.execution_mode}_dispatch",
                ],
                "expected_outcome": {"status": "completed", "success": True},
                "actual_outcome": {
                    "status": status,
                    "success": success,
                    "changed_files": changed_files,
                    "qa_required_for_final_verdict": metadata["qa_required_for_final_verdict"],
                },
                "verdict": "success" if success else "failure",
                "evidence_refs": changed_files,
                "context_refs": [task_id],
                "confidence": 0.7,
            },
        )

        return DirectorTaskResult(
            task_id=task_id,
            subject=subject,
            success=success,
            status=status,
            error=error,
            metadata=metadata,
        )

    # -- iteration orchestration --------------------------------------------

    async def run_iteration(self, iteration: int = 1) -> DirectorIterationResult:
        """Run a full Director iteration.

        Discovers ready tasks, executes them (serial or parallel according
        to ``self._config.execution_mode``), and returns an aggregated
        result snapshot.

        Args:
            iteration: Current iteration number (for telemetry).

        Returns:
            ``DirectorIterationResult`` snapshot.
        """
        logger.info(
            "director iteration start: iteration=%s workspace=%s mode=%s",
            iteration,
            self._workspace,
            self._config.execution_mode,
        )

        ready_tasks = self.get_ready_tasks()
        logger.info("director ready tasks: count=%s", len(ready_tasks))

        if not ready_tasks:
            return DirectorIterationResult(
                success=True,
                iteration=iteration,
                tasks_processed=0,
                tasks_succeeded=0,
                tasks_failed=0,
                results=(),
                notes="No ready tasks",
            )

        batch_size = self._config.max_workers if self._config.execution_mode == "parallel" else 1
        batch = ready_tasks[:batch_size]

        results: list[DirectorTaskResult] = []
        if self._config.execution_mode == "parallel" and batch_size > 1:
            # Parallel execution: run all tasks concurrently
            logger.info(
                "director parallel dispatch: batch_size=%s task_ids=%s",
                len(batch),
                [str(t.get("id", "unknown")) for t in batch],
            )
            raw_results = list(
                await asyncio.gather(
                    *[self.execute_task(task) for task in batch],
                    return_exceptions=True,
                )
            )
            # Convert exceptions to failed DirectorTaskResult and log firewall events
            for i, result in enumerate(raw_results):
                if isinstance(result, BaseException):
                    task_id = str(batch[i].get("id", "unknown"))
                    subject = str(batch[i].get("subject") or batch[i].get("title") or "unknown")
                    logger.warning(
                        "director task exception in parallel batch: task_id=%s error_type=%s error=%s",
                        task_id,
                        type(result).__name__,
                        str(result),
                    )
                    results.append(
                        DirectorTaskResult(
                            task_id=task_id,
                            subject=subject,
                            success=False,
                            status="failed",
                            error=str(result),
                            metadata={"error_type": type(result).__name__},
                        )
                    )
                else:
                    results.append(result)
        else:
            # Serial execution: run tasks one by one
            logger.info(
                "director serial dispatch: batch_size=%s task_ids=%s",
                len(batch),
                [str(t.get("id", "unknown")) for t in batch],
            )
            for task in batch:
                result = await self.execute_task(task)
                results.append(result)

        success_count = sum(1 for r in results if r.success)

        return DirectorIterationResult(
            success=True,
            iteration=iteration,
            tasks_processed=len(batch),
            tasks_succeeded=success_count,
            tasks_failed=len(batch) - success_count,
            results=tuple(results),
        )

    # -- task submission (v2 director.execution cell) -----------------------

    async def submit_task(
        self,
        *,
        subject: str,
        description: str = "",
        priority: str = "medium",
    ) -> dict[str, Any]:
        """Submit a new task via the ``director.execution`` Cell.

        This is a thin wrapper around ``DirectorService.submit_task`` so
        that delivery layers do not import the Cell service directly.

        Args:
            subject: Task subject / title.
            description: Optional task description.
            priority: Task priority (``low``, ``medium``, ``high``).

        Returns:
            Task dict with at least an ``id`` key.

        Raises:
            DirectorOrchestratorError: if submission fails.
        """
        try:
            from polaris.cells.director.execution.public import (
                DirectorConfig,
                DirectorService,
            )
            from polaris.domain.entities import TaskPriority

            config = DirectorConfig(workspace=self._workspace)
            service = DirectorService(config=config)
            task_priority = TaskPriority(priority.lower())
            task = await service.submit_task(
                subject=subject,
                description=description,
                priority=task_priority,
            )
            return {"id": str(task.id), "subject": subject, "status": "submitted"}
        except (ImportError, RuntimeError, ValueError) as exc:
            raise DirectorOrchestratorError(
                f"Task submission failed: {exc}",
                code="task_submission_failed",
                cause=exc,
            ) from exc

    # -- workflow orchestration (console / server modes) --------------------

    async def submit_workflow(
        self,
        *,
        run_id: str,
        tasks: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit a PM workflow via the ``workflow_runtime`` Cell.

        This is used by the Director CLI in console/server mode to hand
        tasks to the workflow engine for asynchronous execution.

        Args:
            run_id: Workflow run identifier.
            tasks: List of task dicts to dispatch.
            metadata: Optional metadata for the workflow input.

        Returns:
            Submission result dict with keys:
            ``submitted``, ``workflow_id``, ``workflow_run_id``,
            ``status``, ``error``.

        Raises:
            DirectorOrchestratorError: if submission fails.
        """
        try:
            from polaris.cells.orchestration.workflow_runtime.public import (
                PMWorkflowInput,
                WorkflowConfig,
                submit_pm_workflow_sync,
            )

            config = WorkflowConfig.from_env(force_enable=True)  # type: ignore[attr-defined]
            workflow_input = PMWorkflowInput(
                workspace=self._workspace,
                run_id=run_id,
                precomputed_payload={"tasks": tasks},
                metadata=dict(metadata or {}),
            )
            submission = submit_pm_workflow_sync(workflow_input, config)
            return {
                "submitted": bool(submission.submitted),
                "status": str(submission.status or "").strip(),
                "workflow_id": str(submission.workflow_id or "").strip(),
                "workflow_run_id": str(submission.workflow_run_id or "").strip(),
                "error": str(submission.error or "").strip(),
            }
        except (ImportError, RuntimeError, ValueError) as exc:
            raise DirectorOrchestratorError(
                f"Workflow submission failed: {exc}",
                code="workflow_submission_failed",
                cause=exc,
            ) from exc

    @staticmethod
    async def wait_for_workflow(
        workflow_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Wait for a workflow to reach terminal status.

        Args:
            workflow_id: The workflow identifier returned by ``submit_workflow``.
            timeout_seconds: Maximum time to wait (``None`` = no timeout).

        Returns:
            Wait result dict with keys: ``status``, ``error``.

        Raises:
            DirectorOrchestratorError: if the wait call fails.
        """
        try:
            from polaris.cells.orchestration.workflow_runtime.public import (
                WorkflowConfig,
                wait_for_workflow_completion_sync,
            )

            config = WorkflowConfig.from_env(force_enable=True)  # type: ignore[attr-defined]
            payload = wait_for_workflow_completion_sync(
                workflow_id,
                timeout_seconds=timeout_seconds,
                config=config,
            )
            return {
                "status": str(payload.get("status") or "").strip(),
                "error": str(payload.get("error") or "").strip(),
            }
        except (ImportError, RuntimeError, ValueError) as exc:
            raise DirectorOrchestratorError(
                f"Workflow wait failed: {exc}",
                code="workflow_wait_failed",
                cause=exc,
            ) from exc

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _build_adapter_input(task: Mapping[str, Any]) -> dict[str, Any]:
        """Build a DirectorAdapter input payload from a task-board row."""

        task_id = str(task.get("id") or task.get("task_id") or "").strip()
        subject = str(task.get("subject") or task.get("title") or "").strip()
        description = str(task.get("description") or task.get("goal") or subject).strip()
        metadata_raw = task.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        # D-08/D-14: Normalize pm_task_id to canonical int format when possible.
        raw_pm_task_id = str(metadata.get("pm_task_id") or task_id).strip()
        try:
            normalized_pm_id = str(DirectorOrchestrator._normalize_task_id(raw_pm_task_id))
        except ValueError:
            normalized_pm_id = raw_pm_task_id
        metadata.update(
            {
                "task_id": task_id,
                "pm_task_id": normalized_pm_id,
                "subject": subject,
                "goal": description,
                "source": "application.director_orchestrator",
            }
        )
        return {
            "task_id": task_id,
            "pm_task_id": metadata["pm_task_id"],
            "id": task_id,
            "subject": subject,
            "title": subject,
            "goal": description,
            "description": description,
            "input": description,
            "task": dict(task),
            "metadata": metadata,
        }

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _normalize_task_id(task_id: Any) -> int:
        """Normalize a task identifier to an integer.

        Supports both numeric IDs (``"1"``, ``42``) and PM-format prefixed
        IDs (``"TASK-1"``, ``"task_42"``).  The ``TASK-`` / ``task_`` prefix
        is stripped before the numeric check, matching the normalization
        pattern in :func:`polaris.cells.chief_engineer.blueprint.public.service._normalize_task_token`.

        Args:
            task_id: Raw task identifier (usually string or int).

        Returns:
            Integer task id.

        Raises:
            ValueError: if the identifier cannot be coerced to an int.
        """
        import re

        token = str(task_id or "").strip()
        # Strip TASK-N / task_N / task-N prefix (D-03: PM→Director ID bridging)
        token = re.sub(r"^(task[-_])+", "", token, flags=re.IGNORECASE)
        if not token.isdigit():
            raise ValueError(f"Invalid TaskBoard task id: {task_id}")
        return int(token)
