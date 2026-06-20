"""Factory Run Service - formal service for unattended development with persistence.

This module is the durable-lifecycle orchestrator (``FactoryRunService``) plus a
thin re-export shim. The data-contracts and shared cancel-registry foundation now
live in :mod:`factory_run_models`, and the production stage executor god-class
lives in :mod:`factory_stage_executor`. Both are re-exported here so the original
import path resolves identically for every existing caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os  # re-exported for lossless surface + test monkeypatch of ``os.name``
import re  # re-exported for lossless surface compatibility
import shutil  # re-exported for lossless surface + test monkeypatch of ``shutil.which``
import subprocess  # re-exported for lossless surface compatibility
import threading  # re-exported for lossless surface compatibility
import uuid
from dataclasses import asdict, dataclass, field  # re-exported for lossless surface
from datetime import datetime, timezone  # re-exported for lossless surface
from enum import Enum  # re-exported for lossless surface
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol  # Protocol re-exported for lossless surface

from polaris.cells.chief_engineer.blueprint.public import GenerateTaskBlueprintCommandV1, generate_task_blueprint
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.storage import resolve_logical_path, resolve_storage_roots
from polaris.kernelone.utils import utc_now_iso

from .factory_run_models import (
    _FACTORY_CANCEL_EVENTS,
    _FACTORY_CANCEL_EVENTS_GUARD,
    _FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS,
    _PM_ARCHITECT_DOC_MAX_CHARS,
    _PM_DIRECTIVE_MAX_CHARS,
    _PM_DIRECTIVE_META_LINE_PATTERN,
    _PM_ORIGINAL_DIRECTIVE_MAX_CHARS,
    _PM_PLAN_META_DIAGNOSTIC_MARKERS,
    _QA_LLM_JUDGEMENT_UNAVAILABLE_WARNING,
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    _WORKSPACE_VALIDATION_TIMEOUT_SECONDS,
    DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS,
    SUPPORTED_FACTORY_STAGES,
    TERMINAL_RUN_STATUSES,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    FactoryStageExecutor,
    StageResult,
    _factory_cancel_key,
    _register_factory_cancel_event,
    _signal_factory_cancel_event,
    _unregister_factory_cancel_event,
)
from .factory_stage_executor import OrchestrationStageExecutor

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


def _factory_jetstream_fanout_timeout_seconds() -> float:
    """Resolve the JetStream fanout timeout for ``_append_event``.

    Defined here (not imported from ``factory_run_models``) so it reads the
    module-level ``_FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS`` bound in THIS
    module. This preserves the original single-file behavior where the helper
    and constant were co-located, keeping the constant monkeypatch-able via
    ``factory_run_service._FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS``.
    """
    raw = os.getenv("POLARIS_FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS")
    if raw is None:
        return _FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS
    try:
        return max(float(raw), 0.05)
    except ValueError:
        return _FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS


# NOTE: ``__all__`` intentionally re-exports the symbols, stdlib modules, and
# private constants/helpers that the original single-file module bound at module
# scope. Keeping them here preserves the historical public+private import surface
# (callers / tests import these from ``factory_run_service``) and keeps the names
# from being stripped by ruff as "unused" — they are deliberate re-exports.
__all__ = [
    "DEFAULT_DIRECTOR_MAX_PARALLELISM",
    "DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS",
    "SUPPORTED_FACTORY_STAGES",
    "TERMINAL_RUN_STATUSES",
    "_FACTORY_CANCEL_EVENTS",
    "_FACTORY_CANCEL_EVENTS_GUARD",
    "_FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS",
    "_PM_ARCHITECT_DOC_MAX_CHARS",
    "_PM_DIRECTIVE_MAX_CHARS",
    "_PM_DIRECTIVE_META_LINE_PATTERN",
    "_PM_ORIGINAL_DIRECTIVE_MAX_CHARS",
    "_PM_PLAN_META_DIAGNOSTIC_MARKERS",
    "_QA_LLM_JUDGEMENT_UNAVAILABLE_WARNING",
    "_WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS",
    "_WORKSPACE_VALIDATION_TIMEOUT_SECONDS",
    "CommandResult",
    "Enum",
    "FactoryConfig",
    "FactoryRun",
    "FactoryRunService",
    "FactoryRunStatus",
    "FactoryStageExecutor",
    "GenerateTaskBlueprintCommandV1",
    "KernelFileSystem",
    "OrchestrationStageExecutor",
    "Protocol",
    "StageResult",
    "TaskRuntimeService",
    "_factory_cancel_key",
    "_factory_jetstream_fanout_timeout_seconds",
    "_register_factory_cancel_event",
    "_signal_factory_cancel_event",
    "_unregister_factory_cancel_event",
    "asdict",
    "dataclass",
    "datetime",
    "field",
    "generate_task_blueprint",
    "get_default_adapter",
    "os",
    "re",
    "resolve_logical_path",
    "resolve_storage_roots",
    "shutil",
    "subprocess",
    "threading",
    "timezone",
    "utc_now_iso",
]


class FactoryRunService:
    """Formal service for Factory runs with persistence and recovery."""

    # 细粒度锁桶数量 - 减少跨 run 的竞争
    _LOCK_BUCKETS = 64

    def __init__(
        self,
        workspace: Path,
        cache_root: Path | None = None,
        executor: FactoryStageExecutor | None = None,
    ) -> None:
        from .factory_store import FactoryStore

        self.workspace = Path(workspace)
        self.cache_root = (
            Path(cache_root)
            if cache_root is not None
            else Path(resolve_storage_roots(str(self.workspace)).runtime_root)
        )
        self.store = FactoryStore(self.cache_root / "factory")
        # 细粒度锁: 按 run_id 哈希分片，减少竞争
        self._run_locks: list[asyncio.Lock] = [asyncio.Lock() for _ in range(self._LOCK_BUCKETS)]
        self._executor: FactoryStageExecutor = executor or OrchestrationStageExecutor(self.workspace)

    def _get_run_lock(self, run_id: str) -> asyncio.Lock:
        """获取 run_id 对应的细粒度锁。

        使用哈希分片确保同一 run 的操作串行化，不同 run 可并行。
        """
        bucket = hash(run_id) % self._LOCK_BUCKETS
        return self._run_locks[bucket]

    async def create_run(self, config: FactoryConfig) -> FactoryRun:
        """Create a new factory run with directory structure."""
        run = FactoryRun(
            id=f"factory_{uuid.uuid4().hex[:12]}",
            config=config,
            status=FactoryRunStatus.PENDING,
            created_at=self._now(),
            metadata={
                "current_stage": None,
                "last_stage": None,
                "last_successful_stage": None,
                "last_failed_stage": None,
            },
        )

        run_dir = self.store.get_run_dir(run.id)
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (run_dir / "events").mkdir(parents=True, exist_ok=True)
        (run_dir / "checkpoints").mkdir(parents=True, exist_ok=True)

        await self.store.save_run(run)
        logger.info("Created factory run %s", run.id)
        return run

    async def execute_stage(
        self,
        run_id: str,
        stage: str,
        context: dict[str, Any] | None = None,
    ) -> StageResult:
        """Execute a single stage with durable lifecycle updates."""
        normalized_context = dict(context or {})
        normalized_context["_factory_abort_checker"] = self._build_abort_checker(run_id)
        cancel_event = _register_factory_cancel_event(self.workspace, run_id)
        normalized_context["_factory_cancel_event"] = cancel_event
        heartbeat_interval = self._resolve_heartbeat_interval_seconds(normalized_context)

        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            if run.status not in {FactoryRunStatus.RUNNING, FactoryRunStatus.RECOVERING}:
                raise ValueError(f"Run {run_id} is not executable in status {run.status.value}")
            started_at = self._now()
            await self._mark_stage_started(run, stage, started_at)

        heartbeat_task: asyncio.Task[None] | None = None
        if heartbeat_interval > 0:
            heartbeat_task = asyncio.create_task(
                self._run_stage_heartbeat(run_id, stage, heartbeat_interval),
                name=f"factory_stage_heartbeat:{run_id}:{stage}",
            )

        try:
            result = await self._execute_stage_logic(run, stage, normalized_context)
        except (
            AttributeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            asyncio.TimeoutError,
        ) as exc:
            result = StageResult(
                stage=stage,
                status="failed",
                output=f"{stage} failed: {exc}",
                artifacts=[],
                started_at=started_at,
                completed_at=self._now(),
            )
            async with run_lock:
                await self._mark_stage_finished(run, result, error=exc)
            logger.error("Stage %s failed for run %s: %s", stage, run_id, exc)
            raise
        finally:
            _unregister_factory_cancel_event(self.workspace, run_id, cancel_event)
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    try:
                        await heartbeat_task
                    except (
                        AttributeError,
                        OSError,
                        RuntimeError,
                        TypeError,
                        ValueError,
                        asyncio.TimeoutError,
                    ) as heartbeat_exc:
                        logger.warning(
                            "Factory heartbeat task failed for run %s stage %s: %s",
                            run_id,
                            stage,
                            heartbeat_exc,
                        )

        result.started_at = result.started_at or started_at
        result.completed_at = result.completed_at or self._now()
        async with run_lock:
            await self._mark_stage_finished(run, result)
        return result

    async def _run_stage_heartbeat(
        self,
        run_id: str,
        stage: str,
        interval_seconds: float,
    ) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            await self._emit_stage_heartbeat(run_id, stage)

    async def _emit_stage_heartbeat(self, run_id: str, stage: str) -> None:
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                return
            if run.status in TERMINAL_RUN_STATUSES:
                return
            current_stage = str(run.metadata.get("current_stage") or "").strip()
            if current_stage != stage:
                return

            timestamp = self._now()
            run.updated_at = timestamp
            run.metadata["last_stage_heartbeat_at"] = timestamp
            await self.store.save_run(run)
            await self._append_event(
                run_id,
                {
                    "type": "stage_heartbeat",
                    "stage": stage,
                    "message": f"Stage {stage} is still running",
                    "timestamp": timestamp,
                },
            )

    def _build_abort_checker(self, run_id: str) -> Callable[[], Awaitable[str | None]]:
        async def _checker() -> str | None:
            current_run = await self.store.get_run(run_id)
            if current_run is None:
                return "run_not_found"
            if current_run.status == FactoryRunStatus.CANCELLED:
                return str(current_run.metadata.get("cancel_reason") or "run_cancelled")
            return None

        return _checker

    @staticmethod
    def _resolve_heartbeat_interval_seconds(context: dict[str, Any]) -> float:
        raw_value = context.get("heartbeat_interval_seconds")
        if raw_value is None:
            return DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS
        if value <= 0:
            return 0.0
        return max(0.05, min(value, 300.0))

    async def recover_run(self, run_id: str) -> FactoryRun:
        """Recover a run from durable storage."""
        run = await self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found")

        if run.status in TERMINAL_RUN_STATUSES:
            return run

        last_successful_stage = (
            str(run.metadata.get("last_successful_stage") or "").strip()
            or str(run.recovery_point or "").strip()
            or str(await self._find_last_successful_stage(run_id) or "").strip()
            or None
        )
        run.recovery_point = last_successful_stage
        run.status = FactoryRunStatus.RECOVERING
        run.updated_at = self._now()
        run.metadata["current_stage"] = last_successful_stage
        run.metadata["last_stage"] = last_successful_stage
        await self.store.save_run(run)
        await self._append_event(
            run_id,
            {
                "type": "recovered",
                "stage": last_successful_stage,
                "message": f"Recovered run at {last_successful_stage or 'start'}",
                "timestamp": run.updated_at,
            },
        )
        logger.info("Run %s recovered at stage %s", run_id, last_successful_stage)
        return run

    async def retry_run_from_stage(
        self,
        run_id: str,
        target_stage: str | None = None,
        reason: str | None = None,
    ) -> FactoryRun:
        """Move a run into recovery from a checkpoint or configured stage."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status in {FactoryRunStatus.COMPLETED, FactoryRunStatus.CANCELLED}:
                return run
            if run.status != FactoryRunStatus.FAILED:
                raise ValueError(f"Run {run_id} cannot be retried in status {run.status.value}")

            configured_stages = [str(stage).strip() for stage in run.config.stages if str(stage).strip()]
            requested_stage = str(target_stage or "").strip()
            if requested_stage and requested_stage not in configured_stages:
                raise ValueError(f"Stage {requested_stage} is not configured for run {run_id}")

            retry_stage = (
                requested_stage
                or str(run.metadata.get("last_successful_stage") or "").strip()
                or str(run.recovery_point or "").strip()
                or str(await self._find_last_successful_stage(run_id) or "").strip()
                or None
            )
            retry_start_policy = "rerun_stage" if requested_stage else "after_checkpoint"
            retry_execution_stage = retry_stage
            if retry_stage and retry_stage in configured_stages:
                stage_index = configured_stages.index(retry_stage)
                rerun_start_index = stage_index if requested_stage else stage_index + 1
            else:
                rerun_start_index = 0
            stages_to_rerun = set(configured_stages[rerun_start_index:])
            if stages_to_rerun:
                run.stages_completed = [stage for stage in run.stages_completed if stage not in stages_to_rerun]
                run.stages_failed = [stage for stage in run.stages_failed if stage not in stages_to_rerun]
                retry_execution_stage = (
                    configured_stages[rerun_start_index] if rerun_start_index < len(configured_stages) else retry_stage
                )

            timestamp = self._now()
            previous_status = run.status.value
            previous_failure = run.metadata.get("failure")
            run.recovery_point = retry_stage
            run.status = FactoryRunStatus.RECOVERING
            run.completed_at = None
            run.updated_at = timestamp
            run.metadata["current_stage"] = retry_execution_stage
            run.metadata["last_stage"] = retry_stage
            run.metadata["retry_from_status"] = previous_status
            run.metadata["retry_start_policy"] = retry_start_policy
            run.metadata["retry_requested_stage"] = requested_stage or None
            run.metadata["retry_execution_stage"] = retry_execution_stage
            if previous_failure:
                run.metadata["retry_previous_failure"] = previous_failure
            run.metadata["failure"] = None
            run.metadata["last_failed_stage"] = None
            if reason:
                run.metadata["retry_reason"] = reason
            await self.store.save_run(run)
            await self._append_event(
                run_id,
                {
                    "type": "retry_requested",
                    "stage": retry_stage,
                    "message": f"Retry requested from {retry_stage or 'start'}",
                    "reason": reason,
                    "previous_status": previous_status,
                    "timestamp": timestamp,
                },
            )
            logger.info("Run %s retry requested from stage %s", run_id, retry_stage)
            return run

    async def execute_pause(self, run_id: str) -> FactoryRun:
        """Pause a running factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status == FactoryRunStatus.RUNNING:
                run.status = FactoryRunStatus.PAUSED
                run.updated_at = self._now()
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "paused",
                        "message": "Run paused",
                        "timestamp": run.updated_at,
                    },
                )
                logger.info("Run %s paused", run_id)
            return run

    async def execute_resume(self, run_id: str) -> FactoryRun:
        """Resume a paused factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status == FactoryRunStatus.PAUSED:
                run.status = FactoryRunStatus.RUNNING
                run.updated_at = self._now()
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "resumed",
                        "message": "Run resumed",
                        "timestamp": run.updated_at,
                    },
                )
                logger.info("Run %s resumed", run_id)
            return run

    async def update_run_metadata(self, run_id: str, metadata: dict[str, Any]) -> FactoryRun:
        """Persist metadata updates for an existing factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            run.metadata.update(dict(metadata))
            run.updated_at = self._now()
            await self.store.save_run(run)
            await self._append_event(
                run_id,
                {
                    "type": "metadata_updated",
                    "message": "Run metadata updated",
                    "metadata_keys": sorted(str(key) for key in metadata),
                    "timestamp": run.updated_at,
                },
            )
            logger.info("Run %s metadata updated: keys=%s", run_id, sorted(str(key) for key in metadata))
            return run

    async def start_run(self, run_id: str) -> FactoryRun:
        """Start a pending factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status == FactoryRunStatus.PENDING:
                started_at = self._now()
                run.status = FactoryRunStatus.RUNNING
                run.started_at = started_at
                run.updated_at = started_at
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "started",
                        "message": "Run started",
                        "timestamp": started_at,
                    },
                )
                logger.info("Run %s started", run_id)
            return run

    async def cancel_run(self, run_id: str, reason: str | None = None) -> FactoryRun:
        """Cancel a factory run and keep a distinct terminal status."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            if run.status in TERMINAL_RUN_STATUSES:
                return run

            timestamp = self._now()
            run.status = FactoryRunStatus.CANCELLED
            run.completed_at = timestamp
            run.updated_at = timestamp
            if reason:
                run.metadata["cancel_reason"] = reason
            await self.store.save_run(run)
            await self._append_event(
                run_id,
                {
                    "type": "cancelled",
                    "message": reason or "Run cancelled",
                    "reason": reason,
                    "timestamp": timestamp,
                },
            )
            logger.info("Run %s cancelled", run_id)
            _signal_factory_cancel_event(self.workspace, run_id)

            # Trigger history archiving (async, non-blocking)
            self._trigger_archive(run_id, "cancelled")

            return run

    async def complete_run(self, run_id: str, success: bool = True) -> FactoryRun:
        """Complete a factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status == FactoryRunStatus.CANCELLED:
                if run.completed_at is None:
                    run.completed_at = self._now()
                    run.updated_at = run.completed_at
                    await self.store.save_run(run)
                return run

            timestamp = self._now()
            run.status = FactoryRunStatus.COMPLETED if success else FactoryRunStatus.FAILED
            run.completed_at = timestamp
            run.updated_at = timestamp
            await self.store.save_run(run)
            await self._append_event(
                run_id,
                {
                    "type": "completed" if success else "failed",
                    "message": "Run completed" if success else "Run failed",
                    "timestamp": timestamp,
                    "success": success,
                },
            )
            logger.info("Run %s completed with success=%s", run_id, success)

            # Trigger history archiving (async, non-blocking)
            self._trigger_archive(run_id, "completed" if success else "failed")

            return run

    async def list_runs(self) -> list[dict[str, Any]]:
        """List all factory runs with basic info."""
        run_ids = self.store.list_runs()
        runs: list[dict[str, Any]] = []
        for run_id in run_ids:
            run = await self.store.get_run(run_id)
            if run is None:
                continue
            runs.append(
                {
                    "id": run.id,
                    "name": run.config.name,
                    "status": run.status.value,
                    "created_at": run.created_at,
                    "updated_at": run.updated_at,
                    "current_stage": run.metadata.get("current_stage"),
                    "last_successful_stage": run.metadata.get("last_successful_stage"),
                    "stages_completed": len(run.stages_completed),
                    "stages_failed": len(run.stages_failed),
                }
            )
        return runs

    async def get_run(self, run_id: str) -> FactoryRun | None:
        """Get a factory run by ID."""
        return await self.store.get_run(run_id)

    async def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        """Get all events for a run."""
        return await self.store.get_events(run_id)

    async def _execute_stage_logic(
        self,
        run: FactoryRun,
        stage: str,
        context: dict[str, Any],
    ) -> StageResult:
        if stage not in SUPPORTED_FACTORY_STAGES:
            return StageResult(stage=stage, status="skipped", output="No handler for this stage")
        return await self._executor.execute(stage, run, context)

    async def _find_last_successful_stage(self, run_id: str) -> str | None:
        """Find the last successful stage from events."""
        events = await self.store.get_events(run_id)
        for event in reversed(events):
            if event.get("type") != "stage_completed":
                continue
            result = event.get("result", {})
            if result.get("status") == "success":
                return result.get("stage")
        return None

    async def _mark_stage_started(self, run: FactoryRun, stage: str, started_at: str) -> None:
        run.metadata["current_stage"] = stage
        run.metadata["current_stage_started_at"] = started_at
        run.metadata["last_stage"] = stage
        run.updated_at = started_at
        await self.store.save_run(run)
        await self._append_event(
            run.id,
            {
                "type": "stage_started",
                "stage": stage,
                "message": f"Started stage {stage}",
                "timestamp": started_at,
            },
        )

    async def _mark_stage_finished(
        self,
        run: FactoryRun,
        result: StageResult,
        error: Exception | None = None,
    ) -> None:
        completed_at = result.completed_at or self._now()
        result.completed_at = completed_at
        latest_run = await self.store.get_run(run.id)
        target_run = latest_run or run

        target_run.metadata["last_stage"] = result.stage
        target_run.metadata["current_stage_completed_at"] = completed_at

        cancelled_externally = (
            target_run.status == FactoryRunStatus.CANCELLED or str(result.status or "").strip().lower() == "cancelled"
        )
        if cancelled_externally:
            result.status = "cancelled"
            if not str(result.output or "").strip():
                reason = str(target_run.metadata.get("cancel_reason") or "Run cancelled").strip()
                result.output = f"Stage {result.stage} cancelled: {reason}"
            target_run.status = FactoryRunStatus.CANCELLED
            target_run.metadata["last_cancelled_stage"] = result.stage
        elif result.status == "success":
            self._append_unique(target_run.stages_completed, result.stage)
            target_run.recovery_point = result.stage
            target_run.metadata["last_successful_stage"] = result.stage
        elif result.status == "failed":
            self._append_unique(target_run.stages_failed, result.stage)
            target_run.status = FactoryRunStatus.FAILED
            target_run.metadata["last_failed_stage"] = result.stage
            target_run.metadata["failure"] = {
                "stage": result.stage,
                "code": "FACTORY_STAGE_FAILED",
                "detail": result.output or str(error or "Stage failed"),
                "recoverable": True,
                "timestamp": completed_at,
            }

        target_run.updated_at = completed_at
        await self.store.save_run(target_run)
        await self._append_event(
            target_run.id,
            {
                "type": "stage_completed",
                "stage": result.stage,
                "message": result.output or f"Completed stage {result.stage}",
                "result": result.to_dict(),
                "timestamp": completed_at,
            },
        )
        await self.store.checkpoint(target_run)

    async def _append_event(self, run_id: str, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("run_id", run_id)
        payload.setdefault("event_id", f"evt_{uuid.uuid4().hex[:12]}")
        payload.setdefault("timestamp", self._now())
        await self.store.append_event(run_id, payload)
        # Best-effort NAT JetStream fanout so the unified WebSocket pipeline
        # (``event.factory:<run_id>`` channel) can stream these events to
        # subscribers. The factory run stays the source of truth (durable on
        # disk); JetStream is the best-effort realtime fanout.
        try:
            from polaris.delivery.http.routers.jetstream_utils import (
                publish_to_jetstream,
            )

            workspace_key = ""
            if self.workspace:
                try:
                    roots = resolve_storage_roots(str(self.workspace))
                    workspace_key = str(getattr(roots, "workspace_key", "") or "").strip()
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.debug("factory workspace key resolution failed for %s: %s", self.workspace, exc)
                    workspace_key = self.workspace.name
            if not workspace_key:
                return
            subject = f"hp.runtime.{workspace_key}.event.factory.{run_id}"
            channel = f"event.factory:{run_id}"
            envelope = {
                "schema_version": "runtime.v2",
                "event_id": payload.get("event_id"),
                "workspace_key": workspace_key,
                "run_id": run_id,
                "channel": channel,
                "kind": str(payload.get("type") or payload.get("kind") or "factory.event"),
                "ts": payload.get("timestamp"),
                "cursor": 0,
                "trace_id": None,
                "payload": payload,
                "meta": {"source": "factory_run_service"},
            }
            await asyncio.wait_for(
                publish_to_jetstream(
                    subject=subject,
                    payload=envelope,
                ),
                timeout=_factory_jetstream_fanout_timeout_seconds(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("factory JetStream fanout failed for run %s: %s", run_id, exc)

    def _trigger_archive(self, run_id: str, reason: str) -> None:
        """Trigger async archiving of factory run to history.

        This is non-blocking - archiving happens in background.
        """
        try:
            from polaris.cells.archive.factory_archive.public.service import trigger_factory_archive

            workspace = str(self.workspace) if hasattr(self, "workspace") else ""
            if workspace:
                trigger_factory_archive(
                    workspace=workspace,
                    factory_run_id=run_id,
                    reason=reason,
                )
                logger.debug("Triggered archive for factory run %s", run_id)
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            # Log error but don't block the main flow
            logger.warning("Failed to trigger archive for factory run %s: %s", run_id, exc)

    @staticmethod
    def _append_unique(target: list[str], value: str) -> None:
        if value and value not in target:
            target.append(value)

    @staticmethod
    def _now() -> str:
        return utc_now_iso()
