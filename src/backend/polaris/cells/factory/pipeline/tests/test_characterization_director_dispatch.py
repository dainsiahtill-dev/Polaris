"""Characterization tests for the Director dispatch loop (part 1)."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import inspect
import json
import logging
import os
import shutil
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BlueprintPersistence,
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
    derive_project_kind_authority_from_catalog_snapshot,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    TaskBlueprintResultV1,
    _issue_chief_engineer_portfolio_authority_carrier,
)
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.events.fact_stream.public.service import (
    QueryFactEventsV1,
    query_fact_events,
)
from polaris.cells.factory.pipeline.internal import (
    factory_stage_executor as stage_executor_module,
    factory_workspace_quality as workspace_quality_module,
)
from polaris.cells.factory.pipeline.internal.factory_deadline_policy import (
    FactoryDeadlineBudgetPolicyV1,
    FactoryDeadlineDispositionV1,
    build_task_dependency_schedule,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
)
from polaris.cells.factory.pipeline.internal.factory_run_completion import RunCompletionWaiter
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.internal.factory_settlement_consumer import _fencing_token
from polaris.cells.factory.pipeline.internal.factory_stage_helpers import (
    evaluate_canonical_factory_authority,
)
from polaris.cells.factory.pipeline.internal.run_ledger import load_run_ledger_projection
from polaris.cells.roles.adapters.public import (
    build_director_materialization_quality_repair_message,
    extract_workspace_quality_summary,
    resolve_director_semantic_quality_repair_target_files,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    ObservableTaskRowsProjectionV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_logical_path


from polaris.cells.factory.pipeline.tests._characterization_helpers import (  # noqa: F401
    _characterization_authority_port,
    _executor,
    _factory_stage_context,
    _with_task_runtime_authority,
    _write_handoff_ready_review_for_tasks,
)


class _PartialFailureProgressExecutor(OrchestrationStageExecutor):
    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace)
        self.results = [
            CommandResult(
                run_id="director-round-1",
                status="failed",
                message="Director binding fanout: 2 bindings, 1 succeeded, 1 failed",
                metadata={
                    "binding_fanout": True,
                    "per_binding": [
                        {"provider_id": "p1", "model": "m1", "run_id": "r1", "status": "completed"},
                        {"provider_id": "p2", "model": "m2", "run_id": "r2", "status": "timeout"},
                    ],
                },
            ),
            CommandResult(
                run_id="director-round-2",
                status="completed",
                message="Run status: completed",
                metadata={
                    "binding_fanout": True,
                    "per_binding": [
                        {"provider_id": "p1", "model": "m1", "run_id": "r3", "status": "completed"},
                        {"provider_id": "p2", "model": "m2", "run_id": "r4", "status": "completed"},
                    ],
                },
            ),
        ]
        self.stats = [
            {"total": 2, "pending": 2, "ready": 2, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
            {"total": 2, "pending": 2, "ready": 2, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
            {"total": 2, "pending": 1, "ready": 1, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 0},
            {"total": 2, "pending": 1, "ready": 1, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 0},
            {"total": 2, "pending": 0, "ready": 0, "in_progress": 0, "completed": 2, "failed": 0, "blocked": 0},
            {"total": 2, "pending": 0, "ready": 0, "in_progress": 0, "completed": 2, "failed": 0, "blocked": 0},
        ]

    def _build_orchestration_service(self, context: dict) -> object:
        del context
        return object()

    def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        del context
        return [
            {"provider_id": "p1", "model": "m1"},
            {"provider_id": "p2", "model": "m2"},
        ]

    def _read_taskboard_stats(self) -> dict[str, int]:
        if len(self.stats) > 1:
            return dict(self.stats.pop(0))
        return dict(self.stats[0])

    def _canonical_factory_projection(
        self,
        _run: FactoryRun,
        _context: dict[str, Any],
    ) -> dict[str, Any]:
        completed = not self.results
        return _with_task_runtime_authority(
            {
                "source": "run_ledger",
                "task_boundary": {
                    "latest_by_task": {
                        task_id: {
                            "task_id": task_id,
                            "status": "completed_verified" if completed else "in_execution",
                            "ok": completed,
                        }
                        for task_id in ("TASK-1", "TASK-2")
                    }
                },
            },
            task_ids=("TASK-1", "TASK-2"),
        )

    async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
        del kwargs
        return self.results.pop(0)

    def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
        del additional_events
        return True, []


class TestDirectorDispatchLoop:
    @pytest.fixture(autouse=True)
    def _use_short_fake_dispatch_budget(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Keep fake dispatch tests fast without weakening production policy."""

        def _policy(context: dict[str, Any]) -> FactoryDeadlineBudgetPolicyV1:
            settlement_seconds = min(
                5,
                max(
                    0,
                    int(
                        context.get(
                            "director_dispatch_timeout_settle_grace_seconds",
                            0,
                        )
                    ),
                ),
            )
            return FactoryDeadlineBudgetPolicyV1(
                chief_engineer_min_start_seconds=1,
                director_first_task_min_seconds=1,
                director_followup_task_min_seconds=1,
                quality_gate_reserved_seconds=0,
                quality_gate_min_start_reserved_seconds=0,
                safety_seconds=0,
                director_settlement_barrier_seconds=settlement_seconds,
            )

        monkeypatch.setattr(
            OrchestrationStageExecutor,
            "_factory_deadline_budget_policy",
            staticmethod(_policy),
        )

    @pytest.mark.asyncio
    async def test_dependency_settle_barrier_exposes_new_claimable_task(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        claim_reads = iter(([], ["TASK-2"]))
        stats = {
            "total": 2,
            "pending": 1,
            "ready": 1,
            "in_progress": 0,
            "completed": 1,
            "failed": 0,
            "blocked": 0,
        }
        monkeypatch.setattr(
            executor,
            "_read_claimable_director_task_ids",
            lambda *, limit, factory_run_id="": list(next(claim_reads)),
        )
        monkeypatch.setattr(executor, "_read_taskboard_stats", lambda: dict(stats))

        task_ids, observed_stats = await executor._wait_for_claimable_director_tasks(
            limit=1,
            grace_seconds=0.2,
        )

        assert task_ids == ["TASK-2"]
        assert observed_stats == stats

    @pytest.mark.asyncio
    async def test_run_completion_waiter_cancel_event_propagates_to_active_orchestration_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(60))
                self._active_runs = {"run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        class _FakeCommandService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await RunCompletionWaiter(tmp_path).wait(
            _FakeCommandService(),
            CommandResult(run_id="run-1", status="running", message="submitted"),
            timeout_seconds=30,
            cancel_event=cancel_event,
        )

        assert result.status == "cancelled"
        assert result.message == "Run cancelled: factory_cancelled"
        assert fake_orchestration.cancelled == [("run-1", True)]
        await asyncio.sleep(0)
        assert fake_orchestration.active_task.cancelled()

    @pytest.mark.asyncio
    async def test_run_completion_waiter_cancel_event_preserves_active_director_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(60))
                self._active_runs = {"run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        class _FakeCommandService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.create_task_row(subject="cancelled active director task")
        claim = task_runtime.claim_execution(
            task["id"],
            worker_id="director",
            role_id="director",
            run_id="run-1",
            selection_source="unit",
        )
        assert claim["success"] is True
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await RunCompletionWaiter(tmp_path).wait(
            _FakeCommandService(),
            CommandResult(run_id="run-1", status="running", message="submitted"),
            timeout_seconds=30,
            cancel_event=cancel_event,
        )

        assert result.status == "cancelled"
        assert result.metadata["cancel_signal_sent"] is False
        assert result.metadata["cancel_reason"] == "factory_cancelled"
        assert result.metadata["inflight_run_continues"] is True
        assert result.metadata["terminal_source"] == "task_runtime_active_execution_barrier"
        assert result.metadata["active_task_count"] == 1
        assert result.metadata["active_task_ids"] == [str(task["id"])]
        assert result.metadata["barrier_cancel_deferred"] is True
        assert fake_orchestration.cancelled == []
        assert fake_orchestration.active_task.cancelled() is False
        guarded_heartbeat = task_runtime.heartbeat_execution(
            task["id"],
            session_id=str(claim["session"]["session_id"]),
        )
        assert guarded_heartbeat["success"] is True

        fake_orchestration.active_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_orchestration.active_task

    @pytest.mark.asyncio
    async def test_cancel_active_run_preserves_active_director_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(60))
                self._active_runs = {"run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.create_task_row(subject="direct cancellation active director task")
        claim = task_runtime.claim_execution(
            task["id"],
            worker_id="director",
            role_id="director",
            run_id="run-1",
            selection_source="unit",
        )
        assert claim["success"] is True

        result = await RunCompletionWaiter(tmp_path).cancel_active_run("run-1", reason="factory_stage_timeout")

        assert result is not None
        assert result.status == "timeout"
        assert result.metadata == {
            "cancel_signal_sent": False,
            "cancel_reason": "factory_stage_timeout",
            "inflight_run_continues": True,
            "terminal_source": "task_runtime_active_execution_barrier",
            "active_task_count": 1,
            "active_task_ids": [str(task["id"])],
        }
        assert fake_orchestration.cancelled == []
        assert fake_orchestration.active_task.cancelled() is False
        guarded_heartbeat = task_runtime.heartbeat_execution(
            task["id"],
            session_id=str(claim["session"]["session_id"]),
        )
        assert guarded_heartbeat["success"] is True

        fake_orchestration.active_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_orchestration.active_task

    @pytest.mark.asyncio
    async def test_cancel_active_run_treats_inactive_orchestration_run_as_already_gone(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """L2-12 task 259: expired Director run must not fail-close factory."""

        class _InactiveOrchestrationError(Exception):
            pass

        _InactiveOrchestrationError.__name__ = "InvalidStateError"

        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.cancelled: list[str] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append(run_id)
                raise _InactiveOrchestrationError(f"Run {run_id} is not active")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )

        result = await RunCompletionWaiter(tmp_path).cancel_active_run(
            "director-aa724e5f4540",
            reason="factory_stage_timeout",
        )

        assert result is None
        assert fake_orchestration.cancelled == ["director-aa724e5f4540"]

    @pytest.mark.asyncio
    async def test_run_completion_waiter_timeout_preserves_active_director_session_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(60))
                self._active_runs = {"run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        class _FakeCommandService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.create_task_row(subject="late director task")
        claim = task_runtime.claim_execution(
            task["id"],
            worker_id="director",
            role_id="director",
            run_id="run-1",
            selection_source="unit",
        )
        assert claim["success"] is True

        result = await RunCompletionWaiter(tmp_path).wait(
            _FakeCommandService(),
            CommandResult(run_id="run-1", status="running", message="submitted"),
            timeout_seconds=0,
        )

        assert result.status == "timeout"
        assert result.metadata == {
            "cancel_signal_sent": False,
            "cancel_reason": "factory_stage_timeout",
            "inflight_run_continues": True,
            "terminal_source": "task_runtime_active_execution_barrier",
            "active_task_count": 1,
            "active_task_ids": [str(task["id"])],
        }
        assert fake_orchestration.cancelled == []
        await asyncio.sleep(0)
        assert fake_orchestration.active_task.cancelled() is False
        guarded_heartbeat = task_runtime.heartbeat_execution(
            task["id"],
            session_id=str(claim["session"]["session_id"]),
        )
        assert guarded_heartbeat["success"] is True

        fake_orchestration.active_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_orchestration.active_task

    @pytest.mark.asyncio
    async def test_run_completion_waiter_timeout_matches_active_director_by_factory_run_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(60))
                self._active_runs = {"factory-run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        class _FakeCommandService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.create_task_row(
            subject="active director task owned by a factory run",
            metadata={"factory_run_id": "factory-run-1"},
        )
        claim = task_runtime.claim_execution(
            task["id"],
            worker_id="director",
            role_id="director",
            run_id="director-run-1",
            selection_source="unit",
        )
        assert claim["success"] is True

        result = await RunCompletionWaiter(tmp_path).wait(
            _FakeCommandService(),
            CommandResult(run_id="factory-run-1", status="running", message="submitted"),
            timeout_seconds=0,
        )

        assert result.status == "timeout"
        assert result.metadata == {
            "cancel_signal_sent": False,
            "cancel_reason": "factory_stage_timeout",
            "inflight_run_continues": True,
            "terminal_source": "task_runtime_active_execution_barrier",
            "active_task_count": 1,
            "active_task_ids": [str(task["id"])],
        }
        assert fake_orchestration.cancelled == []
        assert fake_orchestration.active_task.cancelled() is False
        guarded_heartbeat = task_runtime.heartbeat_execution(
            task["id"],
            session_id=str(claim["session"]["session_id"]),
        )
        assert guarded_heartbeat["success"] is True

        fake_orchestration.active_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_orchestration.active_task

    @pytest.mark.asyncio
    async def test_run_completion_waiter_soft_timeout_preserves_active_director_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(60))
                self._active_runs = {"run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        class _FakeCommandService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.create_task_row(subject="inflight director task")
        claim = task_runtime.claim_execution(
            task["id"],
            worker_id="director",
            role_id="director",
            run_id="run-1",
            selection_source="unit",
        )
        assert claim["success"] is True

        result = await RunCompletionWaiter(tmp_path).wait(
            _FakeCommandService(),
            CommandResult(run_id="run-1", status="running", message="submitted"),
            timeout_seconds=0,
            cancel_on_timeout=False,
        )

        assert result.status == "timeout"
        assert result.metadata["cancel_signal_sent"] is False
        assert result.metadata["cancel_reason"] == "factory_stage_timeout"
        assert result.metadata["inflight_run_continues"] is True
        assert result.metadata["canonical_authoritative"] is False
        assert fake_orchestration.cancelled == []
        assert fake_orchestration.active_task.cancelled() is False
        guarded_heartbeat = task_runtime.heartbeat_execution(
            task["id"],
            session_id=str(claim["session"]["session_id"]),
        )
        assert guarded_heartbeat["success"] is True

        fake_orchestration.active_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_orchestration.active_task

    @pytest.mark.asyncio
    async def test_run_completion_waiter_run_not_found_abort_preserves_child_director_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(0))
                self._active_runs = {"run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        class _FakeCommandService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="completed", message="done")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        async def _run_not_found_abort_checker() -> str:
            return "run_not_found"

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.create_task_row(subject="director child run task")
        claim = task_runtime.claim_execution(
            task["id"],
            worker_id="director",
            role_id="director",
            run_id="run-1",
            selection_source="unit",
        )
        assert claim["success"] is True

        result = await RunCompletionWaiter(tmp_path).wait(
            _FakeCommandService(),
            CommandResult(run_id="run-1", status="running", message="submitted"),
            timeout_seconds=30,
            abort_checker=_run_not_found_abort_checker,
        )

        assert result.status == "failed"
        assert result.reason_code == "canonical_terminal_projection_missing"
        assert fake_orchestration.cancelled == []
        guarded_heartbeat = task_runtime.heartbeat_execution(
            task["id"],
            session_id=str(claim["session"]["session_id"]),
        )
        assert guarded_heartbeat["success"] is True

    @pytest.mark.asyncio
    async def test_director_timeout_settle_cancel_event_without_active_task_still_cancels_run(
        self,
        tmp_path: Path,
    ) -> None:
        class _FakeRunCompletionWaiter:
            def __init__(self) -> None:
                self.cancelled: list[tuple[str, str]] = []

            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                del run_id, process_terminal
                return None

            async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
                self.cancelled.append((run_id, reason))

        fake_waiter = _FakeRunCompletionWaiter()
        executor = _executor(tmp_path)
        executor._run_completion_waiter = fake_waiter  # type: ignore[assignment]
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await executor._settle_inflight_director_run_after_timeout(
            service=object(),  # type: ignore[arg-type]
            run_id="run-2",
            grace_seconds=30,
            cancel_event=cancel_event,
        )

        assert result is not None
        assert result.status == "cancelled"
        assert result.message == "Run cancelled: factory_cancelled"
        assert fake_waiter.cancelled == [("run-2", "factory_cancelled")]

    @pytest.mark.asyncio
    async def test_director_timeout_settle_cancel_event_prefers_canonical_terminal_outcome(
        self,
        tmp_path: Path,
    ) -> None:
        class _FakeRunCompletionWaiter:
            def __init__(self) -> None:
                self.cancelled: list[tuple[str, str]] = []

            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                del process_terminal
                return CommandResult(
                    run_id=run_id,
                    status="completed",
                    message="canonical outcome committed",
                    metadata={
                        "canonical_authoritative": True,
                        "fact_event_seq": 31,
                    },
                )

            async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
                self.cancelled.append((run_id, reason))

        class _FakeService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="completed", message="done")

        class _ActiveExecutor(OrchestrationStageExecutor):
            def _read_taskboard_stats(self) -> dict[str, int]:
                return {
                    "total": 1,
                    "pending": 0,
                    "ready": 0,
                    "in_progress": 1,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }

        fake_waiter = _FakeRunCompletionWaiter()
        executor = _ActiveExecutor(tmp_path)
        executor._run_completion_waiter = fake_waiter  # type: ignore[assignment]
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await executor._settle_inflight_director_run_after_timeout(
            service=_FakeService(),  # type: ignore[arg-type]
            run_id="run-2",
            grace_seconds=30,
            cancel_event=cancel_event,
        )

        assert result is not None
        assert result.status == "completed"
        assert result.message == "canonical outcome committed"
        assert fake_waiter.cancelled == []

    @pytest.mark.asyncio
    async def test_director_timeout_settle_cancel_event_preserves_active_task_runtime_barrier(
        self,
        tmp_path: Path,
    ) -> None:
        class _FakeRunCompletionWaiter:
            def __init__(self) -> None:
                self.cancelled: list[tuple[str, str]] = []

            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                if not process_terminal:
                    return None
                return CommandResult(
                    run_id=run_id,
                    status="completed",
                    message="canonical outcome committed",
                    metadata={
                        "canonical_authoritative": True,
                        "fact_event_seq": 32,
                    },
                )

            def active_execution_barrier_result(self, *, run_id: str, reason: str) -> CommandResult:
                return CommandResult(
                    run_id=run_id,
                    status="cancelled" if reason == "factory_cancelled" else "timeout",
                    message=f"Director run left active for execution-control-plane barrier: {reason}",
                    metadata={
                        "cancel_signal_sent": False,
                        "cancel_reason": reason,
                        "inflight_run_continues": True,
                        "terminal_source": "task_runtime_active_execution_barrier",
                        "active_task_count": 1,
                        "active_task_ids": ["TASK-1"],
                    },
                )

            async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
                self.cancelled.append((run_id, reason))

        class _SettlingService:
            def __init__(self) -> None:
                self.calls = 0

            async def query_run_status(self, run_id: str) -> CommandResult:
                self.calls += 1
                if self.calls == 1:
                    return CommandResult(run_id=run_id, status="running", message="settling")
                return CommandResult(run_id=run_id, status="completed", message="done")

        fake_waiter = _FakeRunCompletionWaiter()
        executor = OrchestrationStageExecutor(tmp_path)
        executor._run_completion_waiter = fake_waiter  # type: ignore[assignment]
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await executor._settle_inflight_director_run_after_timeout(
            service=_SettlingService(),  # type: ignore[arg-type]
            run_id="run-2",
            grace_seconds=30,
            cancel_event=cancel_event,
        )

        assert result is not None
        assert result.status == "completed"
        assert result.metadata["canonical_authoritative"] is True
        assert result.metadata["fact_event_seq"] == 32
        assert result.metadata["cancel_signal_sent"] is False
        assert result.metadata["barrier_cancel_deferred"] is True
        assert result.metadata["deferred_cancel_reason"] == "factory_cancelled"
        assert fake_waiter.cancelled == []

    @pytest.mark.asyncio
    async def test_director_timeout_settle_records_progress_without_extending_hard_deadline(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _ProgressWaiter:
            def __init__(self) -> None:
                self.marker_index = 0
                self.cancelled: list[tuple[str, str]] = []

            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                if not process_terminal:
                    return None
                return CommandResult(
                    run_id=run_id,
                    status="completed",
                    message="canonical outcome committed",
                    metadata={
                        "canonical_authoritative": True,
                        "fact_event_seq": 33,
                    },
                )

            def active_execution_progress_marker(
                self,
                *,
                run_id: str,
            ) -> tuple[tuple[str, str, str, str], ...]:
                del run_id
                self.marker_index += 1
                return (("TASK-1", str(self.marker_index), f"heartbeat-{self.marker_index}", "in_progress"),)

            async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
                self.cancelled.append((run_id, reason))

        class _ProgressService:
            def __init__(self) -> None:
                self.calls = 0

            async def query_run_status(self, run_id: str) -> CommandResult:
                self.calls += 1
                if self.calls < 3:
                    return CommandResult(run_id=run_id, status="running", message="settling")
                return CommandResult(run_id=run_id, status="completed", message="done")

        async def _yield_without_waiting(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", _yield_without_waiting)
        fake_waiter = _ProgressWaiter()
        executor = OrchestrationStageExecutor(tmp_path)
        executor._run_completion_waiter = fake_waiter  # type: ignore[assignment]

        result = await executor._settle_inflight_director_run_after_timeout(
            service=_ProgressService(),  # type: ignore[arg-type]
            run_id="run-progress",
            grace_seconds=1,
        )

        assert result is not None
        assert result.status == "completed"
        assert result.metadata["barrier_progress_extensions"] == 2
        assert result.metadata["barrier_progress_source"] == "task_runtime_execution_fact"
        assert result.metadata["barrier_max_total_seconds"] == 1.0
        assert fake_waiter.cancelled == []

    @pytest.mark.asyncio
    async def test_director_timeout_settle_grace_expiry_preserves_active_task_runtime_barrier(
        self,
        tmp_path: Path,
    ) -> None:
        class _FakeRunCompletionWaiter:
            def __init__(self) -> None:
                self.cancelled: list[tuple[str, str]] = []

            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                del run_id, process_terminal
                return None

            def active_execution_barrier_result(self, *, run_id: str, reason: str) -> CommandResult:
                return CommandResult(
                    run_id=run_id,
                    status="timeout",
                    message=f"Director run left active for execution-control-plane barrier: {reason}",
                    metadata={
                        "cancel_signal_sent": False,
                        "cancel_reason": reason,
                        "inflight_run_continues": True,
                        "terminal_source": "task_runtime_active_execution_barrier",
                        "active_task_count": 1,
                        "active_task_ids": ["TASK-1"],
                    },
                )

            async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
                self.cancelled.append((run_id, reason))

        class _FakeService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        fake_waiter = _FakeRunCompletionWaiter()
        executor = OrchestrationStageExecutor(tmp_path)
        executor._run_completion_waiter = fake_waiter  # type: ignore[assignment]

        result = await executor._settle_inflight_director_run_after_timeout(
            service=_FakeService(),  # type: ignore[arg-type]
            run_id="run-3",
            grace_seconds=1,
        )

        assert result is not None
        assert result.status == "timeout"
        expected_metadata = {
            "cancel_signal_sent": False,
            "cancel_reason": "factory_stage_timeout",
            "inflight_run_continues": True,
            "timeout_settle_grace_seconds": 1,
            "terminal_source": "task_runtime_active_execution_barrier",
            "active_task_count": 1,
            "active_task_ids": ["TASK-1"],
            "barrier_state": "timeout",
            "barrier_timeout": True,
            "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
            "responsible_layer": "execution_control_plane",
        }
        assert {key: result.metadata[key] for key in expected_metadata} == expected_metadata
        assert result.metadata["barrier_max_total_seconds"] == 1.0
        assert 0.0 <= result.metadata["barrier_elapsed_seconds"] <= 1.0
        assert fake_waiter.cancelled == []

    @pytest.mark.asyncio
    async def test_director_timeout_status_query_cannot_outlive_settlement_lease(
        self,
        tmp_path: Path,
    ) -> None:
        class _BoundedWaiter:
            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                del run_id, process_terminal
                return None

            def active_execution_progress_marker(
                self,
                *,
                run_id: str,
            ) -> tuple[tuple[str, str, str, str], ...]:
                del run_id
                return (("TASK-1", "lease-1", "active", "in_progress"),)

            def active_execution_barrier_result(
                self,
                *,
                run_id: str,
                reason: str,
            ) -> CommandResult:
                return CommandResult(
                    run_id=run_id,
                    status="timeout",
                    message=reason,
                    metadata={
                        "cancel_signal_sent": False,
                        "inflight_run_continues": True,
                    },
                )

            async def cancel_active_run(
                self,
                run_id: str,
                *,
                reason: str,
            ) -> CommandResult | None:
                del run_id, reason
                raise AssertionError("active TaskRuntime barrier must defer cancellation")

        class _BlockingStatusService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                await asyncio.sleep(60)
                return CommandResult(run_id=run_id, status="running", message="late")

        executor = OrchestrationStageExecutor(tmp_path)
        executor._run_completion_waiter = _BoundedWaiter()  # type: ignore[assignment]
        loop = asyncio.get_running_loop()
        started_at = loop.time()

        result = await executor._settle_inflight_director_run_after_timeout(
            service=_BlockingStatusService(),  # type: ignore[arg-type]
            run_id="run-bounded-query",
            grace_seconds=1,
        )

        assert loop.time() - started_at < 1.25
        assert result is not None
        assert result.status == "timeout"
        assert result.metadata["barrier_max_total_seconds"] == 1.0
        assert result.metadata["inflight_run_continues"] is True

    @pytest.mark.asyncio
    async def test_director_binding_fanout_waits_submitted_runs_concurrently(self, tmp_path: Path) -> None:
        class _FanoutService:
            def __init__(self) -> None:
                self.next_id = 0

            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                self.next_id += 1
                return CommandResult(run_id=f"run-{self.next_id}", status="running", message="submitted")

        class _ConcurrentWaitExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.started_waits: list[str] = []
                self.all_waits_started = asyncio.Event()

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                self.started_waits.append(initial_result.run_id)
                if len(self.started_waits) >= 2:
                    self.all_waits_started.set()
                await self.all_waits_started.wait()
                return CommandResult(run_id=initial_result.run_id, status="completed", message="done")

        executor = _ConcurrentWaitExecutor(tmp_path)
        result = await asyncio.wait_for(
            executor._execute_director_binding_fanout(
                service=_FanoutService(),
                workspace=str(tmp_path),
                tasks=["TASK-1", "TASK-2"],
                base_options={"execution_mode": "parallel", "max_workers": 2},
                bindings=[
                    {"provider_id": "p1", "model": "m1", "binding_id": "b1"},
                    {"provider_id": "p2", "model": "m2", "binding_id": "b2"},
                ],
                timeout_seconds=10,
                authority_port=_characterization_authority_port(),
            ),
            timeout=0.5,
        )

        assert result.status == "completed"
        assert sorted(executor.started_waits) == ["run-1", "run-2"]
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert [item["status"] for item in per_binding] == ["completed", "completed"]

    @pytest.mark.asyncio
    async def test_director_binding_fanout_cancel_event_preserves_active_task_runtime_barrier(
        self,
        tmp_path: Path,
    ) -> None:
        class _FanoutService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-active", status="running", message="submitted")

            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        class _ActiveExecutor(OrchestrationStageExecutor):
            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, abort_checker, cancel_on_timeout
                assert cancel_event is not None and cancel_event.is_set()
                return self._run_completion_waiter.active_execution_barrier_result(
                    run_id=initial_result.run_id,
                    reason="factory_cancelled",
                )

        class _RunScopedBarrierWaiter:
            def active_execution_barrier_result(self, *, run_id: str, reason: str) -> CommandResult:
                return CommandResult(
                    run_id=run_id,
                    status="cancelled",
                    message=f"Director run left active for execution-control-plane barrier: {reason}",
                    metadata={
                        "cancel_signal_sent": False,
                        "cancel_reason": reason,
                        "inflight_run_continues": True,
                        "terminal_source": "task_runtime_active_execution_barrier",
                        "active_task_count": 1,
                        "active_task_ids": ["TASK-1"],
                    },
                )

        executor = _ActiveExecutor(tmp_path)
        executor._run_completion_waiter = _RunScopedBarrierWaiter()  # type: ignore[assignment]
        executor._binding_status_probe_seconds = 0.01
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await asyncio.wait_for(
            executor._execute_director_binding_fanout(
                service=_FanoutService(),
                workspace=str(tmp_path),
                tasks=["TASK-1"],
                base_options={"execution_mode": "parallel", "max_workers": 1},
                bindings=[{"provider_id": "p1", "model": "m1", "binding_id": "b1"}],
                timeout_seconds=10,
                cancel_event=cancel_event,
                authority_port=_characterization_authority_port(),
            ),
            timeout=0.5,
        )

        assert result.status == "failed"
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert per_binding == [
            {
                "provider_id": "p1",
                "model": "m1",
                "binding_id": "b1",
                "run_id": "run-active",
                "status": "cancelled",
                "message": "Director run left active for execution-control-plane barrier: factory_cancelled",
                "assigned_tasks": ["TASK-1"],
                "assigned_task_count": 1,
                "cancel_signal_sent": False,
                "cancel_reason": "factory_cancelled",
                "inflight_run_continues": True,
                "terminal_source": "task_runtime_active_execution_barrier",
                "active_task_count": 1,
                "active_task_ids": ["TASK-1"],
            }
        ]

    @pytest.mark.asyncio
    async def test_director_binding_fanout_cancel_event_prefers_canonical_terminal_outcome(
        self,
        tmp_path: Path,
    ) -> None:
        class _FanoutService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-done", status="running", message="submitted")

            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="completed", message="done")

        class _ActiveExecutor(OrchestrationStageExecutor):
            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, initial_result, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                return CommandResult(
                    run_id="run-done",
                    status="completed",
                    message="canonical outcome committed",
                    metadata={
                        "canonical_authoritative": True,
                        "fact_event_seq": 34,
                    },
                )

            def _read_taskboard_stats(self) -> dict[str, int]:
                return {
                    "total": 1,
                    "pending": 0,
                    "ready": 0,
                    "in_progress": 1,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }

        executor = _ActiveExecutor(tmp_path)
        executor._binding_status_probe_seconds = 0.01
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await asyncio.wait_for(
            executor._execute_director_binding_fanout(
                service=_FanoutService(),
                workspace=str(tmp_path),
                tasks=["TASK-1"],
                base_options={"execution_mode": "parallel", "max_workers": 1},
                bindings=[{"provider_id": "p1", "model": "m1", "binding_id": "b1"}],
                timeout_seconds=10,
                cancel_event=cancel_event,
                authority_port=_characterization_authority_port(),
            ),
            timeout=0.5,
        )

        assert result.status == "completed"
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert per_binding[0]["status"] == "completed"
        assert per_binding[0]["message"] == "canonical outcome committed"
        assert "inflight_run_continues" not in per_binding[0]

    @pytest.mark.asyncio
    async def test_director_binding_fanout_ignores_command_result_task_status_counts(
        self,
        tmp_path: Path,
    ) -> None:
        class _FanoutService:
            def __init__(self) -> None:
                self.queries = 0

            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-stuck", status="running", message="submitted")

            async def query_run_status(self, run_id: str) -> CommandResult:
                self.queries += 1
                return CommandResult(
                    run_id=run_id,
                    status="running",
                    message="Run status: running",
                    metadata={
                        "task_status_counts": {
                            "completed": 1,
                            "failed": 1,
                            "pending": 0,
                            "ready": 0,
                            "in_progress": 0,
                            "blocked": 0,
                        }
                    },
                )

        class _TerminalProbeExecutor(OrchestrationStageExecutor):
            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, initial_result, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                await asyncio.sleep(0.05)
                return CommandResult(run_id="run-stuck", status="completed", message="actual run completed")

        service = _FanoutService()
        executor = _TerminalProbeExecutor(tmp_path)
        executor._binding_status_probe_seconds = 0.01

        result = await asyncio.wait_for(
            executor._execute_director_binding_fanout(
                service=service,
                workspace=str(tmp_path),
                tasks=["TASK-1", "TASK-2"],
                base_options={"execution_mode": "parallel", "max_workers": 2},
                bindings=[{"provider_id": "p1", "model": "m1", "binding_id": "b1"}],
                timeout_seconds=60,
                authority_port=_characterization_authority_port(),
            ),
            timeout=1.0,
        )

        assert service.queries == 0
        assert result.status == "completed"
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert per_binding[0]["status"] == "completed"
        assert per_binding[0]["message"] == "actual run completed"
        assert "task_status_counts" not in per_binding[0]

    @pytest.mark.asyncio
    async def test_director_binding_fanout_ignores_workspace_taskboard_counts_for_terminal_state(
        self,
        tmp_path: Path,
    ) -> None:
        class _FanoutService:
            def __init__(self) -> None:
                self.queries = 0

            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-stuck", status="running", message="submitted")

            async def query_run_status(self, run_id: str) -> CommandResult:
                self.queries += 1
                return CommandResult(
                    run_id=run_id,
                    status="running",
                    message="Run status: running",
                    metadata={
                        "task_status_counts": {
                            "completed": 0,
                            "failed": 0,
                            "pending": 1,
                            "ready": 0,
                            "in_progress": 0,
                            "blocked": 0,
                        }
                    },
                )

        class _TaskboardProbeExecutor(OrchestrationStageExecutor):
            def _read_taskboard_stats(self) -> dict[str, int]:
                return {
                    "total": 3,
                    "pending": 0,
                    "ready": 0,
                    "in_progress": 0,
                    "completed": 1,
                    "failed": 2,
                    "blocked": 0,
                }

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, initial_result, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                await asyncio.sleep(0.05)
                return CommandResult(run_id="run-stuck", status="completed", message="actual run completed")

        service = _FanoutService()
        executor = _TaskboardProbeExecutor(tmp_path)
        executor._binding_status_probe_seconds = 0.01

        result = await asyncio.wait_for(
            executor._execute_director_binding_fanout(
                service=service,
                workspace=str(tmp_path),
                tasks=["TASK-1", "TASK-2", "TASK-3"],
                base_options={"execution_mode": "parallel", "max_workers": 2},
                bindings=[{"provider_id": "p1", "model": "m1", "binding_id": "b1"}],
                timeout_seconds=60,
                authority_port=_characterization_authority_port(),
            ),
            timeout=1.0,
        )

        assert service.queries == 0
        assert result.status == "completed"
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert per_binding[0]["status"] == "completed"
        assert per_binding[0]["message"] == "actual run completed"
        assert "task_status_counts" not in per_binding[0]

    @pytest.mark.asyncio
    async def test_director_binding_fanout_counts_newly_quarantined_timeouts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("KERNELONE_FACTORY_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT", "2")

        class _FanoutService:
            counter = 0

            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                self.counter += 1
                return CommandResult(run_id=f"run-timeout-{self.counter}", status="running", message="submitted")

        class _TimeoutExecutor(OrchestrationStageExecutor):
            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                return CommandResult(run_id=initial_result.run_id, status="timeout", message="timed out")

        service = _FanoutService()
        executor = _TimeoutExecutor(tmp_path)
        binding = {"provider_id": "p1", "model": "m1", "binding_id": "b1"}

        await executor._execute_director_binding_fanout(
            service=service,
            workspace=str(tmp_path),
            tasks=["TASK-1"],
            base_options={"execution_mode": "parallel", "max_workers": 1},
            bindings=[binding],
            timeout_seconds=10,
            authority_port=_characterization_authority_port(),
        )
        result = await executor._execute_director_binding_fanout(
            service=service,
            workspace=str(tmp_path),
            tasks=["TASK-1"],
            base_options={"execution_mode": "parallel", "max_workers": 1},
            bindings=[binding],
            timeout_seconds=10,
            authority_port=_characterization_authority_port(),
        )

        assert result.status == "failed"
        assert "1 quarantined" in result.message
        assert (result.metadata or {})["quarantined_binding_count"] == 1
        assert (result.metadata or {})["quarantined_skipped_count"] == 0
        per_binding = (result.metadata or {})["per_binding"]
        assert per_binding[0]["status"] == "timeout"
        assert per_binding[0]["quarantined"] is True
        assert per_binding[0]["timeout_count"] == 2

    @pytest.mark.asyncio
    async def test_director_binding_fanout_soft_timeout_preserves_submitted_run(
        self,
        tmp_path: Path,
    ) -> None:
        class _FanoutService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-soft-timeout", status="running", message="submitted")

        class _SoftTimeoutExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.cancel_on_timeout_values: list[bool] = []

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker
                self.cancel_on_timeout_values.append(cancel_on_timeout)
                return CommandResult(
                    run_id=initial_result.run_id,
                    status="timeout",
                    message="soft timed out",
                    metadata={
                        "cancel_signal_sent": bool(cancel_on_timeout),
                        "cancel_reason": "factory_stage_timeout",
                        "inflight_run_continues": not cancel_on_timeout,
                    },
                )

        executor = _SoftTimeoutExecutor(tmp_path)
        result = await executor._execute_director_binding_fanout(
            service=_FanoutService(),
            workspace=str(tmp_path),
            tasks=["TASK-1"],
            base_options={"execution_mode": "parallel", "max_workers": 1},
            bindings=[{"provider_id": "p1", "model": "m1", "binding_id": "b1"}],
            timeout_seconds=10,
            authority_port=_characterization_authority_port(),
        )

        assert executor.cancel_on_timeout_values == [False]
        assert result.status == "failed"
        per_binding = (result.metadata or {})["per_binding"]
        assert per_binding[0]["status"] == "timeout"
        assert per_binding[0]["cancel_signal_sent"] is False
        assert per_binding[0]["inflight_run_continues"] is True

    @pytest.mark.asyncio
    async def test_dispatch_passes_pm_plan_task_ids_to_director_fanout(self, tmp_path: Path) -> None:
        class _CaptureTasksExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.captured_tasks: list[str] | None = None
                self.stats = [
                    {
                        "total": 2,
                        "pending": 2,
                        "ready": 2,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 2,
                        "pending": 2,
                        "ready": 2,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 2,
                        "failed": 0,
                        "blocked": 0,
                    },
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [
                    {"provider_id": "p1", "model": "m1"},
                    {"provider_id": "p2", "model": "m2"},
                ]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            def _canonical_factory_projection(
                self,
                _run: FactoryRun,
                _context: dict[str, Any],
            ) -> dict[str, Any]:
                return _with_task_runtime_authority(
                    {
                        "source": "run_ledger",
                        "task_boundary": {
                            "latest_by_task": {
                                task_id: {
                                    "task_id": task_id,
                                    "status": "completed_verified",
                                    "ok": True,
                                }
                                for task_id in ("TASK-1", "TASK-2")
                            }
                        },
                    },
                    task_ids=("TASK-1", "TASK-2"),
                )

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                tasks = kwargs.get("tasks")
                self.captured_tasks = list(tasks) if isinstance(tasks, list) else None
                return CommandResult(
                    run_id="director-capture",
                    status="completed",
                    message="Run status: completed",
                    metadata={"task_status_counts": {"completed": 2}},
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _CaptureTasksExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["package.json"]},
            {"id": "TASK-2", "target_files": ["src/index.ts"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-capture-tasks",
            config=FactoryConfig(name="capture-tasks"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 120, "execution_mode": "parallel", "max_workers": 2}
            ),
        )

        assert result.status == "success"
        assert executor.captured_tasks == ["TASK-1", "TASK-2"]

    @pytest.mark.asyncio
    async def test_continues_after_partial_fanout_failure_when_taskboard_progresses(self, tmp_path: Path) -> None:
        executor = _PartialFailureProgressExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["package.json"]},
            {"id": "TASK-2", "target_files": ["src/index.ts"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-progress",
            config=FactoryConfig(name="progress"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 3, "timeout": 120, "execution_mode": "parallel", "max_workers": 2}
            ),
        )

        assert result.status == "success"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert len(payload["attempts"]) == 2
        assert payload["taskboard"]["converged"] is True
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.partial_failure_progress_continued" in codes
        assert "director.run_status_non_success" not in codes

    @pytest.mark.asyncio
    async def test_fails_when_all_director_bindings_fail_even_if_taskboard_converges(self, tmp_path: Path) -> None:
        class _AllBindingsFailedExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 2,
                        "pending": 2,
                        "ready": 2,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 2,
                        "pending": 2,
                        "ready": 2,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 2,
                        "blocked": 0,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 2,
                        "blocked": 0,
                    },
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [
                    {"provider_id": "p1", "model": "m1"},
                    {"provider_id": "p2", "model": "m2"},
                ]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(
                    run_id="director-all-failed",
                    status="failed",
                    message="Director binding fanout: 2 bindings, 0 succeeded, 2 failed",
                    metadata={
                        "binding_fanout": True,
                        "active_binding_count": 2,
                        "per_binding": [
                            {"provider_id": "p1", "model": "m1", "run_id": "r1", "status": "failed"},
                            {"provider_id": "p2", "model": "m2", "run_id": "r2", "status": "failed"},
                        ],
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _AllBindingsFailedExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["package.json"]},
            {"id": "TASK-2", "target_files": ["src/index.ts"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-all-bindings-failed",
            config=FactoryConfig(name="all-bindings-failed"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 120, "execution_mode": "parallel", "max_workers": 2}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["error_code"] == "director.canonical_task_boundary_missing"
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.canonical_task_boundary_missing" in codes
        assert "director.dispatch_converged_after_partial_failure" in codes

    @pytest.mark.asyncio
    async def test_materialization_quality_failure_with_artifacts_stays_failed(self, tmp_path: Path) -> None:
        class _MaterializationQualityHandoffExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 3,
                        "pending": 3,
                        "ready": 3,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 3,
                        "pending": 3,
                        "ready": 3,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 3,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 1,
                        "failed": 2,
                        "blocked": 0,
                    },
                    {
                        "total": 3,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 1,
                        "failed": 2,
                        "blocked": 0,
                    },
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [{"provider_id": "p-live", "model": "m-live", "binding_id": "b-live"}]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(
                    run_id="director-quality-failed",
                    status="failed",
                    message=(
                        "Director binding fanout: 3 bindings, 0 succeeded, 1 failed, 0 quarantined, 2 readiness-skipped"
                    ),
                    metadata={
                        "binding_fanout": True,
                        "active_binding_count": 1,
                        "readiness_skipped_count": 2,
                        "per_binding": [
                            {
                                "provider_id": "p-live",
                                "model": "m-live",
                                "binding_id": "b-live",
                                "run_id": "director-quality-failed",
                                "status": "failed",
                                "message": (
                                    "Run status: failed | failed_task=task-2-director "
                                    "| error=director_materialization_quality_failed"
                                ),
                                "task_status_counts": {"completed": 1, "failed": 2},
                            },
                            {
                                "provider_id": "p-dead",
                                "model": "m-dead",
                                "binding_id": "b-dead",
                                "run_id": "",
                                "status": "skipped",
                                "skipped": True,
                                "skip_reason": "provider_connectivity_unavailable",
                            },
                        ],
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        (tmp_path / "src").mkdir()
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc"},"devDependencies":{"typescript":"latest"}}',
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")

        executor = _MaterializationQualityHandoffExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["package.json", "src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-quality-handoff",
            config=FactoryConfig(name="quality-handoff"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-22T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 120, "execution_mode": "parallel", "max_workers": 1}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["quality_gate_handoff"] is False
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["error_code"] == "director.canonical_task_boundary_missing"
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff" not in codes
        assert "director.canonical_task_boundary_missing" in codes
