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

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

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
        2. Role-session execution – run each task through
           ``RoleRuntimeService`` (the canonical tool-loop facade).
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
        self._runtime: Any | None = None

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

    def _get_runtime(self) -> Any:
        """Lazily resolve ``RoleRuntimeService`` from the ``roles.runtime`` Cell."""
        if self._runtime is not None:
            return self._runtime
        try:
            from polaris.cells.roles.runtime.public.service import RoleRuntimeService

            self._runtime = RoleRuntimeService()
            return self._runtime
        except (ImportError, RuntimeError, ValueError) as exc:
            raise DirectorOrchestratorError(
                f"Failed to resolve RoleRuntimeService: {exc}",
                code="runtime_resolution_error",
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
        """Execute a single task via the role-runtime facade.

        This method builds a canonical ``ExecuteRoleSessionCommandV1``,
        invokes ``RoleRuntimeService.execute_role_session``, and maps the
        response back to a ``DirectorTaskResult``.

        Args:
            task: Task dict with at least ``id`` and ``subject`` keys.

        Returns:
            ``DirectorTaskResult`` snapshot.

        Raises:
            DirectorOrchestratorError: if the runtime invocation fails in
                an unexpected way.
        """
        task_id = str(task.get("id", "unknown"))
        subject = str(task.get("subject", "unknown"))
        description = str(task.get("description", ""))

        board = self._get_task_board()
        runtime = self._get_runtime()

        # Update status to in_progress
        try:
            normalized_id = self._normalize_task_id(task_id)
            board.update(normalized_id, status="in_progress")
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Failed to update task %s to in_progress: %s", task_id, exc)

        message = self._build_director_message(subject, description)

        try:
            from polaris.cells.roles.runtime.public.contracts import (
                ExecuteRoleSessionCommandV1,
            )

            command = ExecuteRoleSessionCommandV1(
                role="director",
                session_id=f"director-task-{task_id}",
                workspace=self._workspace,
                user_message=message,
                history=(),
                stream=False,
            )
            payload = await runtime.execute_role_session(command)
            response = self._extract_response_text(payload)

            # Mark completed
            try:
                board.update(
                    normalized_id,
                    status="completed",
                    metadata={
                        "adapter_result": {
                            "response_length": len(response),
                            "tool_calls_executed_by_kernel": True,
                        }
                    },
                )
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning("Failed to update task %s to completed: %s", task_id, exc)

            return DirectorTaskResult(
                task_id=task_id,
                subject=subject,
                success=True,
                status="completed",
                response_length=len(response),
                metadata={"response_length": len(response)},
            )

        except (RuntimeError, ValueError) as exc:
            logger.exception("Director task execution failed: id=%s", task_id)
            try:
                board.update(
                    normalized_id,
                    status="failed",
                    metadata={"adapter_error": str(exc)},
                )
            except (AttributeError, RuntimeError, ValueError):
                logger.exception("Task state update failed after execution error: id=%s", task_id)

            return DirectorTaskResult(
                task_id=task_id,
                subject=subject,
                success=False,
                status="failed",
                error=str(exc),
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
    def _normalize_task_id(task_id: Any) -> int:
        """Normalize a task identifier to an integer.

        Args:
            task_id: Raw task identifier (usually string or int).

        Returns:
            Integer task id.

        Raises:
            ValueError: if the identifier cannot be coerced to an int.
        """
        token = str(task_id or "").strip()
        if not token.isdigit():
            raise ValueError(f"Invalid TaskBoard task id: {task_id}")
        return int(token)

    @staticmethod
    def _extract_response_text(payload: Any) -> str:
        """Extract plain-text response from a role-runtime payload.

        Args:
            payload: Raw payload returned by ``execute_role_session``.

        Returns:
            Normalized response string.
        """
        if isinstance(payload, dict):
            return str(payload.get("response") or payload.get("text") or "").strip()
        return str(payload or "").strip()

    @staticmethod
    def _build_director_message(subject: str, description: str) -> str:
        """Build the canonical Director role message for a task.

        Args:
            subject: Task subject.
            description: Optional task description.

        Returns:
            Formatted message string.
        """
        lines = [f"任务: {subject}", ""]
        if description:
            lines.extend(["描述:", description, ""])
        lines.extend(
            [
                "请执行此任务。",
                "",
                "运行时说明:",
                "",
                "- 工具调用由运行时以原生 structured tool calls 处理。",
                "- 不要输出任何 [READ_FILE] / [WRITE_FILE] / [TOOL_CALL] 之类的文本 wrapper。",
                "- 如果不需要工具，直接给出回答；如果需要工具，正常表达你的意图，运行时会处理工具 schema。",
            ]
        )
        return "\n".join(lines)
