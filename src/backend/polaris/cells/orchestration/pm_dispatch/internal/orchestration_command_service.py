"""Orchestration Command Service - Single write path for PM/Director/Factory execution.

This module provides a unified command execution layer that consolidates all
orchestration operations (PM, Director, Factory) into a single entry point.

Architecture:
- Single entry point for all orchestration commands
- Unified run ID generation
- Consistent error handling and logging
- Integration with UnifiedOrchestrationService

Phase 4 Implementation: Single Execution Write Path
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.orchestration.workflow_runtime.public.service import (
    OrchestrationMode,
    OrchestrationRunRequest,
    RoleEntrySpec,
    get_orchestration_service,
)

# Import and register role adapters to ensure they're available for factory flows
# (API routes already do this, but factory flow needs it too)
from polaris.cells.roles.adapters.public.service import register_all_adapters
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM, DEFAULT_MAX_WORKERS

# Re-export for backwards compatibility - import from polaris.kernelone.constants
_DEFAULT_MAX_WORKERS = DEFAULT_MAX_WORKERS

logger = logging.getLogger(__name__)


def _coerce_metadata_overrides(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    overrides: dict[str, Any] = {}
    for key, item in value.items():
        token = str(key or "").strip()
        if not token:
            continue
        overrides[token] = item
    return overrides


@dataclass
class CommandResult:
    """Unified command execution result.

    Attributes:
        run_id: Unique run identifier
        status: Run status ("pending", "running", "completed", "failed", "not_implemented")
        message: Optional status message
        reason_code: Optional error/reason code for programmatic handling
        stage_results: Optional dict of stage execution results
        started_at: ISO format timestamp when run started
        completed_at: ISO format timestamp when run completed
        artifacts: Optional list of artifact dictionaries
        metadata: Optional dict of additional metadata
    """

    run_id: str
    status: str
    message: str | None = None
    reason_code: str | None = None
    stage_results: dict | None = None
    started_at: str | None = None
    completed_at: str | None = None
    artifacts: list[dict] | None = None
    metadata: dict | None = None


@dataclass
class PMRunOptions:
    """Options for PM run execution.

    Attributes:
        run_type: Type of PM run ("full", "architect", "pm")
        directive: Optional directive/requirement text
        run_director: Whether to auto-run Director after PM
        director_iterations: Number of Director iterations
    """

    run_type: str = "full"
    directive: str = ""
    run_director: bool = False
    director_iterations: int = 2


@dataclass
class DirectorRunOptions:
    """Options for Director run execution.

    Attributes:
        task_filter: Optional filter for task selection
        max_workers: Maximum parallel workers
        execution_mode: Execution mode ("serial", "parallel")
    """

    task_filter: str | None = None
    max_workers: int = field(default_factory=lambda: _DEFAULT_MAX_WORKERS)
    execution_mode: str = "parallel"


@dataclass
class FactoryRunOptions:
    """Options for Factory run execution.

    Attributes:
        config: Factory configuration dictionary
        auto_start: Whether to auto-start the run
    """

    config: dict[str, Any] = field(default_factory=dict)
    auto_start: bool = True


class OrchestrationCommandService:
    """Single entry point for all orchestration commands.

    This service provides unified execution paths for:
    - PM (Project Manager) runs
    - Director runs
    - Factory runs

    All execution goes through UnifiedOrchestrationService for consistency.
    """

    def __init__(self, settings: Any) -> None:
        """Initialize the command service.

        Args:
            settings: Application settings object
        """
        self.settings = settings
        self._active_runs: dict[str, dict] = {}

    async def execute_pm_run(
        self,
        workspace: str,
        run_type: str = "full",
        options: dict[str, Any] | None = None,
    ) -> CommandResult:
        """Execute PM run - unified entry point for PM orchestration.

        This is the ONLY entry point for PM execution. All PM runs must go
        through this method to ensure consistent run ID generation,
        logging, and error handling.

        Args:
            workspace: Workspace path
            run_type: Type of run ("full", "architect", "pm")
            options: Optional execution options dictionary

        Returns:
            CommandResult with run details and status

        Example:
            result = await service.execute_pm_run(
                workspace=".",
                run_type="architect",
                options={"directive": "Implement login feature"}
            )
        """
        run_id = self._generate_run_id("pm")
        started_at = datetime.now(timezone.utc).isoformat()

        opts = options or {}
        pm_options = PMRunOptions(
            run_type=run_type,
            directive=opts.get("directive", ""),
            run_director=opts.get("run_director", False),
            director_iterations=opts.get("director_iterations", 2),
        )
        metadata_overrides = _coerce_metadata_overrides(opts.get("metadata"))

        try:
            # Get unified orchestration service and ensure adapters are registered
            service = await get_orchestration_service()
            register_all_adapters(service)

            # Determine role based on run_type
            role_id = "architect" if run_type == "architect" else "pm"

            # Build role entries
            # NOTE: scope_paths must include workspace for adapter path resolution
            # (PM adapter uses scope_paths[0] as workspace for resolve_runtime_path)
            role_entries = [
                RoleEntrySpec(
                    role_id=role_id,
                    input=pm_options.directive or f"Execute {run_type} phase",
                    scope_paths=[workspace],
                    metadata=metadata_overrides,
                )
            ]

            # Build orchestration request
            orch_request = OrchestrationRunRequest(
                run_id=run_id,
                workspace=Path(workspace),
                mode=OrchestrationMode.WORKFLOW,
                role_entries=role_entries,
                metadata={
                    "run_type": run_type,
                    "run_director": pm_options.run_director,
                    "director_iterations": pm_options.director_iterations,
                    "command_source": "orchestration_command_service",
                    **metadata_overrides,
                },
            )

            # Validate request
            errors = orch_request.validate()
            if errors:
                return CommandResult(
                    run_id=run_id,
                    status="failed",
                    message=f"Validation failed: {errors}",
                    reason_code="VALIDATION_FAILED",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )

            # Submit run
            snapshot = await service.submit_run(orch_request)

            # Track active run
            self._active_runs[run_id] = {
                "workspace": workspace,
                "role": role_id,
                "started_at": started_at,
            }

            return CommandResult(
                run_id=run_id,
                status=snapshot.status.value,
                message=f"PM {run_type} run started",
                started_at=started_at,
                artifacts=[],
            )

        except (RuntimeError, ValueError) as e:
            return CommandResult(
                run_id=run_id,
                status="failed",
                message=str(e),
                reason_code="PM_RUN_FAILED",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    async def execute_director_run(
        self,
        workspace: str,
        tasks: list[str] | None = None,
        options: dict[str, Any] | None = None,
    ) -> CommandResult:
        """Execute Director run - unified entry point for Director orchestration.

        This is the ONLY entry point for Director execution. All Director runs
        must go through this method to ensure consistent execution semantics.

        Args:
            workspace: Workspace path
            tasks: Optional list of specific task IDs to execute
            options: Optional execution options dictionary

        Returns:
            CommandResult with run details and status

        Example:
            result = await service.execute_director_run(
                workspace=".",
                tasks=["task-1", "task-2"],
                options={"max_workers": 3}
            )
        """
        run_id = self._generate_run_id("director")
        started_at = datetime.now(timezone.utc).isoformat()

        opts = options or {}
        director_options = DirectorRunOptions(
            task_filter=opts.get("task_filter"),
            max_workers=opts.get("max_workers", DEFAULT_DIRECTOR_MAX_PARALLELISM),
            execution_mode=opts.get("execution_mode", "parallel"),
        )
        metadata_overrides = _coerce_metadata_overrides(opts.get("metadata"))

        try:
            # Get unified orchestration service and ensure adapters are registered
            service = await get_orchestration_service()
            register_all_adapters(service)

            # Build role entries
            input_text = director_options.task_filter or "Execute ready tasks"
            if tasks:
                input_text = f"Execute tasks: {', '.join(tasks)}"

            role_entries = [
                RoleEntrySpec(
                    role_id="director",
                    input=input_text,
                    scope_paths=[workspace],
                    metadata=metadata_overrides,
                )
            ]

            # Build orchestration request
            orch_request = OrchestrationRunRequest(
                run_id=run_id,
                workspace=Path(workspace),
                mode=OrchestrationMode.WORKFLOW,
                role_entries=role_entries,
                metadata={
                    "tasks": tasks or [],
                    "max_workers": director_options.max_workers,
                    "execution_mode": director_options.execution_mode,
                    "command_source": "orchestration_command_service",
                    **metadata_overrides,
                },
            )

            # Validate request
            errors = orch_request.validate()
            if errors:
                return CommandResult(
                    run_id=run_id,
                    status="failed",
                    message=f"Validation failed: {errors}",
                    reason_code="VALIDATION_FAILED",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )

            # Submit run
            snapshot = await service.submit_run(orch_request)

            # Track active run
            self._active_runs[run_id] = {
                "workspace": workspace,
                "role": "director",
                "started_at": started_at,
            }

            return CommandResult(
                run_id=run_id,
                status=snapshot.status.value,
                message=f"Director started in {director_options.execution_mode} mode",
                started_at=started_at,
                artifacts=[],
            )

        except (RuntimeError, ValueError) as e:
            return CommandResult(
                run_id=run_id,
                status="failed",
                message=str(e),
                reason_code="DIRECTOR_RUN_FAILED",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    async def execute_qa_run(
        self,
        workspace: str,
        target: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> CommandResult:
        """Execute QA run - unified entry point for QA orchestration."""
        run_id = self._generate_run_id("qa")
        started_at = datetime.now(timezone.utc).isoformat()

        opts = options or {}
        input_text = opts.get("input") or target or "Run quality gate checks on completed tasks"

        try:
            service = await get_orchestration_service()

            role_entries = [
                RoleEntrySpec(
                    role_id="qa",
                    input=input_text,
                    scope_paths=[workspace],
                )
            ]

            orch_request = OrchestrationRunRequest(
                run_id=run_id,
                workspace=Path(workspace),
                mode=OrchestrationMode.WORKFLOW,
                role_entries=role_entries,
                metadata={
                    "command_source": "orchestration_command_service",
                    "qa_target": target or "",
                },
            )

            errors = orch_request.validate()
            if errors:
                return CommandResult(
                    run_id=run_id,
                    status="failed",
                    message=f"Validation failed: {errors}",
                    reason_code="VALIDATION_FAILED",
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )

            snapshot = await service.submit_run(orch_request)

            self._active_runs[run_id] = {
                "workspace": workspace,
                "role": "qa",
                "started_at": started_at,
            }

            return CommandResult(
                run_id=run_id,
                status=snapshot.status.value,
                message="QA run started",
                started_at=started_at,
                artifacts=[],
            )

        except (RuntimeError, ValueError) as e:
            return CommandResult(
                run_id=run_id,
                status="failed",
                message=str(e),
                reason_code="QA_RUN_FAILED",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    async def execute_factory_run(
        self,
        workspace: str,
        config: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> CommandResult:
        """Execute Factory run - unified entry point for Factory orchestration.

        This is the entry point for Factory execution. It delegates to
        FactoryRunService for actual execution.

        Args:
            workspace: Workspace path
            config: Optional Factory configuration
            options: Optional execution options dictionary

        Returns:
            CommandResult with run details and status

        Example:
            result = await service.execute_factory_run(
                workspace=".",
                config={"stages": ["docs", "pm", "director"]}
            )
        """
        run_id = self._generate_run_id("factory")
        started_at = datetime.now(timezone.utc).isoformat()

        opts = options or {}
        factory_options = FactoryRunOptions(
            config=config or {},
            auto_start=opts.get("auto_start", True),
        )

        # Use FactoryRunService for actual execution
        try:
            from .factory_run_service import FactoryConfig, FactoryRunService

            factory_service = FactoryRunService(workspace=Path(opts.get("workspace", ".")))

            config = FactoryConfig(
                name=f"orch_factory_{run_id}",
                description="Factory run from orchestration command",
                stages=opts.get("stages", ["docs_generation", "pm_planning", "director_dispatch"]),
                auto_dispatch=factory_options.auto_start,
            )

            run = await factory_service.create_run(config)

            if factory_options.auto_start:
                await factory_service.start_run(run.id)

            return CommandResult(
                run_id=run.id,
                status=run.status,
                message=f"Factory run created: {run.status}",
                started_at=run.started_at or started_at,
                artifacts=[],
            )
        except (RuntimeError, ValueError) as e:
            logger.error(f"Factory run creation failed: {e}")
            return CommandResult(
                run_id=run_id,
                status="failed",
                message=f"Factory run failed: {e}",
                reason_code="FACTORY_RUN_CREATION_FAILED",
                started_at=started_at,
                artifacts=[],
            )

    def _generate_run_id(self, prefix: str = "run") -> str:
        """Generate unique run ID.

        Args:
            prefix: ID prefix ("pm", "director", "factory")

        Returns:
            Unique run identifier string
        """
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def get_run_status(self, run_id: str) -> CommandResult | None:
        """Get status of an active run.

        Args:
            run_id: Run identifier

        Returns:
            CommandResult if run exists, None otherwise
        """
        if run_id in self._active_runs:
            run_info = self._active_runs[run_id]
            return CommandResult(
                run_id=run_id,
                status=run_info.get("status", "unknown"),
                message=run_info.get("message"),
                started_at=run_info.get("started_at"),
            )
        return None

    @staticmethod
    def _trim_error_text(value: str | None, limit: int = 240) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return f"{text[: limit - 1]}…"

    @classmethod
    def _build_failed_task_summaries(cls, snapshot: Any) -> list[dict[str, Any]]:
        failed_statuses = {"failed", "blocked", "cancelled", "timeout"}
        rows: list[dict[str, Any]] = []
        tasks = getattr(snapshot, "tasks", {})
        if not isinstance(tasks, dict):
            return rows

        for task in tasks.values():
            status_obj = getattr(task, "status", None)
            status = str(getattr(status_obj, "value", status_obj) or "").strip().lower()
            if status not in failed_statuses:
                continue
            row = {
                "task_id": str(getattr(task, "task_id", "") or "").strip(),
                "role_id": str(getattr(task, "role_id", "") or "").strip(),
                "status": status,
                "error_category": str(getattr(task, "error_category", "") or "").strip() or None,
                "error_message": cls._trim_error_text(getattr(task, "error_message", None)),
                "updated_at": (
                    updated_at.isoformat() if (updated_at := getattr(task, "updated_at", None)) is not None else None
                ),
            }
            rows.append(row)

        rows.sort(
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        return rows

    @staticmethod
    def _build_task_status_counts(snapshot: Any) -> dict[str, int]:
        counts: dict[str, int] = {}
        tasks = getattr(snapshot, "tasks", {})
        if not isinstance(tasks, dict):
            return counts
        for task in tasks.values():
            status_obj = getattr(task, "status", None)
            status = str(getattr(status_obj, "value", status_obj) or "").strip().lower()
            if not status:
                continue
            counts[status] = counts.get(status, 0) + 1
        return counts

    async def query_run_status(self, run_id: str) -> CommandResult:
        """Query orchestration snapshot for authoritative run status."""
        service = await get_orchestration_service()
        snapshot = await service.query_run(run_id)
        if not snapshot:
            return CommandResult(
                run_id=run_id,
                status="failed",
                message=f"Run {run_id} not found",
                reason_code="RUN_NOT_FOUND",
            )

        run_status = str(snapshot.status.value or "").strip().lower()
        failed_tasks = self._build_failed_task_summaries(snapshot)
        message = f"Run status: {snapshot.status.value}"
        if run_status in {"failed", "blocked", "cancelled", "timeout"} and failed_tasks:
            primary = failed_tasks[0]
            task_ref = str(primary.get("task_id") or "unknown_task").strip()
            role_ref = str(primary.get("role_id") or "unknown_role").strip()
            error_ref = str(primary.get("error_message") or primary.get("error_category") or "unknown_error").strip()
            message = f"Run status: {snapshot.status.value} | failed_task={task_ref} ({role_ref}) | error={error_ref}"

        return CommandResult(
            run_id=run_id,
            status=snapshot.status.value,
            message=message,
            started_at=snapshot.created_at.isoformat() if snapshot.created_at else None,
            completed_at=snapshot.completed_at.isoformat() if snapshot.completed_at else None,
            metadata={
                "current_phase": snapshot.current_phase.value,
                "overall_progress": snapshot.overall_progress,
                "task_count": len(snapshot.tasks),
                "task_status_counts": self._build_task_status_counts(snapshot),
                "failed_task_count": len(failed_tasks),
                "failed_tasks": failed_tasks[:20],
            },
        )

    def list_active_runs(self, workspace: str | None = None) -> list[dict[str, Any]]:
        """List active runs, optionally filtered by workspace.

        Args:
            workspace: Optional workspace filter

        Returns:
            List of active run information dictionaries
        """
        runs = []
        for run_id, info in self._active_runs.items():
            if workspace is None or info.get("workspace") == workspace:
                runs.append({"run_id": run_id, **info})
        return runs

    def clear_completed_runs(self) -> int:
        """Clear completed/failed runs from tracking.

        Returns:
            Number of runs cleared
        """
        to_clear = [
            run_id
            for run_id, info in self._active_runs.items()
            if info.get("status") in ("completed", "failed", "cancelled")
        ]
        for run_id in to_clear:
            del self._active_runs[run_id]
        return len(to_clear)


# ============================================================================
# Convenience Functions
# ============================================================================


async def execute_pm_command(
    workspace: str,
    run_type: str = "full",
    options: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> CommandResult:
    """Convenience function to execute PM command without instantiating service.

    Args:
        workspace: Workspace path
        run_type: Type of PM run
        options: Optional execution options
        settings: Optional settings object

    Returns:
        CommandResult with execution status
    """
    service = OrchestrationCommandService(settings)
    return await service.execute_pm_run(workspace, run_type, options)


async def execute_director_command(
    workspace: str,
    tasks: list[str] | None = None,
    options: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> CommandResult:
    """Convenience function to execute Director command without instantiating service.

    Args:
        workspace: Workspace path
        tasks: Optional list of task IDs
        options: Optional execution options
        settings: Optional settings object

    Returns:
        CommandResult with execution status
    """
    service = OrchestrationCommandService(settings)
    return await service.execute_director_run(workspace, tasks, options)


async def execute_factory_command(
    workspace: str,
    config: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    settings: Any | None = None,
) -> CommandResult:
    """Convenience function to execute Factory command without instantiating service.

    Args:
        workspace: Workspace path
        config: Optional Factory configuration
        options: Optional execution options
        settings: Optional settings object

    Returns:
        CommandResult with execution status
    """
    service = OrchestrationCommandService(settings)
    return await service.execute_factory_run(workspace, config, options)


__all__ = [
    "CommandResult",
    "DirectorRunOptions",
    "FactoryRunOptions",
    "OrchestrationCommandService",
    "PMRunOptions",
    "execute_director_command",
    "execute_factory_command",
    "execute_pm_command",
]
