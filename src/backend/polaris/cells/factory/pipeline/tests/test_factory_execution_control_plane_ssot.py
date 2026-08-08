"""Execution Control Plane single-source-of-truth regressions for Factory."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal import (
    factory_run_completion as completion_module,
    factory_stage_executor as executor_module,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
)
from polaris.cells.factory.pipeline.internal.factory_run_completion import RunCompletionWaiter
from polaris.cells.factory.pipeline.internal.factory_run_models import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
)
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryRunService
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.internal.factory_stage_helpers import (
    evaluate_canonical_factory_authority,
)
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.cells.runtime.task_runtime.public.contracts import ObservableTaskRowsProjectionV1
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_logical_path


@pytest.fixture(autouse=True)
def _bootstrap_real_fact_stream_workspace(request: pytest.FixtureRequest) -> None:
    """Provision FactStream only for tests that exercise a real temp workspace."""

    if "tmp_path" not in request.fixturenames:
        return
    workspace = Path(request.getfixturevalue("tmp_path")).resolve()
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_execution_control_plane_ssot_test_bootstrap",
        )
    )


def _canonical_projection(
    *,
    task_ok: bool = True,
    qa_ok: bool = True,
    evidence_ok: bool = True,
) -> dict[str, Any]:
    task_status = "completed_verified" if task_ok else "incomplete_materialization"
    return {
        "source": "run_ledger",
        "task_runtime_projection": {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
            "row_count": 1,
            "rows": [
                {
                    "task_id": "TASK-1",
                    "status": "completed",
                    "execution_state": "completed",
                    "fact_event_seq": 1,
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                }
            ],
            "readiness": {"ready": True, "blocking_reasons": []},
        },
        "integrity_ok": evidence_ok,
        "outcome_ok": task_ok and qa_ok and evidence_ok,
        "gate_count": 2,
        "gates": [
            {"name": "workspace_validation", "ok": evidence_ok},
            {
                "name": "qa_verdict",
                "ok": qa_ok,
                "append_id": "qa-append-1",
                "content_id": "qa-content-1",
            },
        ],
        "task_boundary": {
            "ok": task_ok,
            "verdict_count": 1,
            "latest_by_task": {
                "TASK-1": {
                    "task_id": "TASK-1",
                    "status": task_status,
                    "ok": task_ok,
                    "failure_class": "" if task_ok else "INCOMPLETE_MATERIALIZATION",
                    "responsible_layer": "task_boundary",
                }
            },
            "failed": [] if task_ok else [{"task_id": "TASK-1", "status": task_status}],
        },
        "evidence_policy": {
            "integrity_ok": evidence_ok,
            "outcome_ok": evidence_ok,
            "missing_required_modalities": [] if evidence_ok else ["command"],
            "failed_required_modalities": [],
        },
    }


class _CompletedCommandService:
    async def query_run_status(self, run_id: str) -> CommandResult:
        return CommandResult(run_id=run_id, status="completed", message="session completed")


class _FailedCommandService:
    async def query_run_status(self, run_id: str) -> CommandResult:
        return CommandResult(
            run_id=run_id,
            status="failed",
            message="director_no_materialized_changes",
        )


class _QaCommandService:
    async def execute_qa_run(self, **_kwargs: Any) -> CommandResult:
        return CommandResult(run_id="qa-run", status="running", message="qa started")


def _factory_run(run_id: str = "factory-ssot") -> FactoryRun:
    return FactoryRun(
        id=run_id,
        config=FactoryConfig(name="ssot"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-07-13T00:00:00+00:00",
    )


def test_canonical_authority_rejects_report_or_session_substitutes() -> None:
    missing_projection = evaluate_canonical_factory_authority(
        {},
        sequence_barrier_satisfied=True,
    )
    failed_boundary = evaluate_canonical_factory_authority(
        _canonical_projection(task_ok=False),
        sequence_barrier_satisfied=True,
    )

    assert missing_projection.director_stage_authorized is False
    assert missing_projection.quality_stage_authorized is False
    assert missing_projection.reason_code == "run_ledger_projection_unavailable"
    assert failed_boundary.director_stage_authorized is False
    assert failed_boundary.failure_class == "INCOMPLETE_MATERIALIZATION"


def test_canonical_authority_requires_sequence_qa_and_evidence() -> None:
    projection = _canonical_projection()

    no_sequence = evaluate_canonical_factory_authority(
        projection,
        sequence_barrier_satisfied=False,
    )
    complete = evaluate_canonical_factory_authority(
        projection,
        sequence_barrier_satisfied=True,
    )

    assert no_sequence.director_stage_authorized is True
    assert no_sequence.quality_stage_authorized is False
    assert no_sequence.reason_code == "canonical_sequence_barrier_unsatisfied"
    assert complete.quality_stage_authorized is True


def test_canonical_authority_rejects_partial_task_runtime_convergence() -> None:
    projection = _canonical_projection()
    task_runtime = projection["task_runtime_projection"]
    task_runtime["row_count"] = 2
    task_runtime["rows"].append(
        {
            "task_id": "TASK-2",
            "status": "pending",
            "execution_state": "pending",
            "fact_event_seq": 2,
            "source": "task_runtime.execution_fact",
            "status_source": "task_runtime.execution_fact",
        }
    )

    authority = evaluate_canonical_factory_authority(
        projection,
        sequence_barrier_satisfied=True,
    )

    assert authority.director_stage_authorized is False
    assert authority.task_runtime_converged is False
    assert authority.reason_code == "task_runtime_not_converged"
    assert authority.incomplete_runtime_task_ids == ("TASK-2",)
    assert authority.missing_task_boundary_ids == ("TASK-2",)


def test_completed_verified_boundary_does_not_override_failed_runtime() -> None:
    """TaskBoundary delivery evidence cannot rewrite TaskRuntime lifecycle."""

    projection = _canonical_projection()
    task_runtime = projection["task_runtime_projection"]
    task_runtime["row_count"] = 2
    task_runtime["rows"] = [
        {
            "task_id": "1",
            "status": "completed",
            "execution_state": "completed",
            "fact_event_seq": 1,
            "source": "task_runtime.execution_fact",
            "status_source": "task_runtime.execution_fact",
        },
        {
            "task_id": "3",
            "status": "failed",
            "execution_state": "failed",
            "fact_event_seq": 7,
            "source": "task_runtime.execution_fact",
            "status_source": "task_runtime.execution_fact",
        },
    ]
    projection["task_boundary"] = {
        "ok": True,
        "verdict_count": 2,
        "latest_by_task": {
            "1": {
                "task_id": "1",
                "status": "completed_verified",
                "ok": True,
                "failure_class": "PASSED",
                "responsible_layer": "execution_control_plane",
            },
            "3": {
                "task_id": "3",
                "status": "completed_verified",
                "ok": True,
                "failure_class": "PASSED",
                "responsible_layer": "execution_control_plane",
            },
        },
        "failed": [],
    }

    authority = evaluate_canonical_factory_authority(
        projection,
        sequence_barrier_satisfied=True,
    )

    assert authority.task_runtime_converged is False
    assert authority.incomplete_runtime_task_ids == ("3",)
    assert authority.director_stage_authorized is False
    assert authority.reason_code == "task_runtime_not_converged"


def test_r181_failed_runtime_without_boundary_still_incomplete() -> None:
    """Pending/failed without completed_verified boundary stays fail-closed."""

    projection = _canonical_projection()
    task_runtime = projection["task_runtime_projection"]
    task_runtime["rows"] = [
        {
            "task_id": "3",
            "status": "failed",
            "execution_state": "failed",
            "fact_event_seq": 1,
            "source": "task_runtime.execution_fact",
            "status_source": "task_runtime.execution_fact",
        }
    ]
    task_runtime["row_count"] = 1
    projection["task_boundary"] = {"ok": False, "verdict_count": 0, "latest_by_task": {}, "failed": []}

    authority = evaluate_canonical_factory_authority(
        projection,
        sequence_barrier_satisfied=True,
    )

    assert authority.director_stage_authorized is False
    assert "3" in authority.incomplete_runtime_task_ids or "3" in authority.incomplete_task_ids


def test_completed_verified_boundary_does_not_override_active_runtime() -> None:
    """A green boundary cannot convert pending/in-progress execution to completed."""

    projection = _canonical_projection()
    task_runtime = projection["task_runtime_projection"]
    task_runtime["row_count"] = 2
    task_runtime["rows"] = [
        {
            "task_id": "1",
            "status": "completed",
            "execution_state": "completed",
            "fact_event_seq": 1,
            "source": "task_runtime.execution_fact",
            "status_source": "task_runtime.execution_fact",
        },
        {
            "task_id": "2",
            "status": "pending",
            "execution_state": "in_progress",
            "fact_event_seq": 4,
            "source": "task_runtime.execution_fact",
            "status_source": "task_runtime.execution_fact",
        },
    ]
    projection["task_boundary"] = {
        "ok": True,
        "verdict_count": 2,
        "latest_by_task": {
            "1": {"task_id": "1", "status": "completed_verified", "ok": True},
            "2": {"task_id": "2", "status": "completed_verified", "ok": True},
        },
        "failed": [],
    }
    authority = evaluate_canonical_factory_authority(projection, sequence_barrier_satisfied=True)
    assert authority.task_runtime_converged is False
    assert authority.incomplete_runtime_task_ids == ("2",)
    assert authority.director_stage_authorized is False
    assert authority.reason_code == "task_runtime_not_converged"


def test_canonical_projection_filters_task_rows_to_current_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_projection = ObservableTaskRowsProjectionV1(
        workspace=str(tmp_path),
        source="task_runtime.execution_fact",
        authoritative=True,
        degraded=False,
        rows=(
            {
                "task_id": "TASK-CURRENT",
                "workflow_run_id": "factory-current",
                "factory_run_id": "factory-current",
                "status": "completed",
                "execution_state": "completed",
                "fact_event_seq": 11,
                "metadata": {
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                },
            },
            {
                "task_id": "TASK-OTHER",
                "workflow_run_id": "factory-other",
                "factory_run_id": "factory-other",
                "status": "pending",
                "execution_state": "pending",
                "fact_event_seq": 12,
                "metadata": {
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                },
            },
            {
                "task_id": "TASK-UNBOUND",
                "status": "completed",
                "execution_state": "completed",
                "fact_event_seq": 13,
                "metadata": {
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                },
            },
        ),
        readiness={"ready": True, "blocking_reasons": []},
    )

    class _TaskRuntimeService:
        def __init__(self, _workspace: str) -> None:
            pass

        def query_observable_task_rows_projection(self) -> ObservableTaskRowsProjectionV1:
            return runtime_projection

    monkeypatch.setattr(executor_module, "TaskRuntimeService", _TaskRuntimeService)
    monkeypatch.setattr(
        executor_module,
        "load_run_ledger_projection",
        lambda *_args, **_kwargs: {"source": "run_ledger"},
    )
    executor = OrchestrationStageExecutor(tmp_path)

    observable_rows = executor._read_observable_task_rows(factory_run_id="factory-current")
    projection = executor._canonical_factory_projection(
        _factory_run("factory-current"),
        {},
    )

    assert [row["task_id"] for row in observable_rows] == ["TASK-CURRENT"]
    authority = projection["task_runtime_projection"]
    assert authority["requested_factory_run_id"] == "factory-current"
    assert authority["total_row_count"] == 3
    assert authority["row_count"] == 1
    assert authority["rows"] == [
        {
            "task_id": "TASK-CURRENT",
            "workflow_run_id": "factory-current",
            "factory_run_id": "factory-current",
            "status": "completed",
            "execution_state": "completed",
            "fact_event_seq": 11,
            "source": "task_runtime.execution_fact",
            "status_source": "task_runtime.execution_fact",
        }
    ]


def test_pm_materialization_binds_preexisting_runtime_row_to_factory_run(tmp_path: Path) -> None:
    runtime = TaskRuntimeService(str(tmp_path))
    runtime.ensure_task_row(external_task_id="TASK-1", subject="PM-created task")
    executor = OrchestrationStageExecutor(tmp_path)

    summary = executor._materialize_pm_plan_taskboard(
        [{"id": "TASK-1", "goal": "Implement one bounded task"}],
        run_id="factory-current",
        source_stage="pm_planning",
    )
    projection = runtime.query_observable_task_rows_projection()

    assert summary["created_count"] == 0
    assert summary["bound_count"] == 1
    assert summary["binding_failures"] == []
    bound_rows = projection.rows_for_factory_run("factory-current")
    assert [executor._task_projection_external_id(row) for row in bound_rows] == ["TASK-1"]


def test_pm_materialization_reports_factory_run_binding_conflict(tmp_path: Path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    task = {"id": "TASK-1", "goal": "Implement one bounded task"}
    first = executor._materialize_pm_plan_taskboard(
        [task],
        run_id="factory-first",
        source_stage="pm_planning",
    )
    second = executor._materialize_pm_plan_taskboard(
        [task],
        run_id="factory-second",
        source_stage="pm_planning",
    )

    assert first["binding_failures"] == []
    assert second["bound_count"] == 0
    assert len(second["binding_failures"]) == 1
    assert second["binding_failures"][0]["task_id"] == "TASK-1"
    assert second["binding_failures"][0]["code"] == "factory_run_binding_conflict"
    assert second["binding_failures"][0]["existing_factory_run_id"] == "factory-first"


@pytest.mark.asyncio
async def test_waiter_rejects_completed_session_without_canonical_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Lifecycle:
        _active_runs: dict[str, asyncio.Task[Any]] = {}

    async def get_lifecycle() -> _Lifecycle:
        return _Lifecycle()

    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
        get_lifecycle,
    )
    monkeypatch.setattr(completion_module, "_CANONICAL_OUTCOME_SETTLEMENT_SECONDS", 0.01)
    waiter = RunCompletionWaiter(tmp_path)
    monkeypatch.setattr(waiter, "canonical_terminal_result", lambda **_kwargs: None)

    result = await waiter.wait(
        _CompletedCommandService(),
        CommandResult(run_id="run-session-only", status="completed", message="session complete"),
        timeout_seconds=1,
    )

    assert result.status == "failed"
    assert result.reason_code == "canonical_terminal_projection_missing"
    assert result.metadata is not None
    assert result.metadata["canonical_authoritative"] is False


@pytest.mark.asyncio
async def test_waiter_extends_settlement_for_active_execution_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Lifecycle:
        _active_runs: dict[str, asyncio.Task[Any]] = {}

    async def get_lifecycle() -> _Lifecycle:
        return _Lifecycle()

    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
        get_lifecycle,
    )
    monkeypatch.setattr(
        completion_module,
        "_CANONICAL_OUTCOME_SETTLEMENT_SECONDS",
        0.2,
    )

    waiter = RunCompletionWaiter(tmp_path)
    canonical_calls = 0
    progress_calls = 0

    def canonical_result(**_kwargs: Any) -> CommandResult | None:
        nonlocal canonical_calls
        canonical_calls += 1
        # succeed only after several poll cycles, beyond the base 0.2s settlement.
        if canonical_calls < 5:
            return None
        return CommandResult(
            run_id="run-progress",
            status="completed",
            message="TaskRuntime canonical projection reached completed",
            metadata={
                "canonical_authoritative": True,
                "terminal_source": "task_runtime.execution_fact",
                "fact_event_seq": 99,
            },
        )

    def progress_marker(**_kwargs: Any) -> tuple[tuple[str, str, str, str], ...]:
        nonlocal progress_calls
        progress_calls += 1
        if progress_calls < 2:
            return (("task-1", "1", "hb-1", "in_progress"),)
        return (("task-1", "2", "hb-2", "in_progress"),)

    monkeypatch.setattr(waiter, "canonical_terminal_result", canonical_result)
    monkeypatch.setattr(waiter, "active_execution_progress_marker", progress_marker)

    result = await waiter.wait(
        _CompletedCommandService(),
        CommandResult(run_id="run-progress", status="running", message="submitted"),
        timeout_seconds=1,
    )

    assert result.status == "completed"
    assert result.metadata is not None
    assert result.metadata["canonical_authoritative"] is True
    assert result.metadata["fact_event_seq"] == 99
    assert canonical_calls >= 5


@pytest.mark.asyncio
async def test_waiter_preserves_execution_lease_when_lifecycle_fails_before_active_task_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """R94: a lifecycle failure cannot outrank the active execution owner."""

    class _Lifecycle:
        _active_runs: dict[str, asyncio.Task[Any]] = {}

    async def get_lifecycle() -> _Lifecycle:
        return _Lifecycle()

    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
        get_lifecycle,
    )
    waiter = RunCompletionWaiter(tmp_path)
    state = {"committed": False}

    def task_runtime_projection() -> ObservableTaskRowsProjectionV1:
        now = datetime.now(timezone.utc)
        status = "completed" if state["committed"] else "in_progress"
        return ObservableTaskRowsProjectionV1(
            workspace=str(tmp_path),
            source="task_runtime.execution_fact",
            authoritative=True,
            degraded=False,
            rows=(
                {
                    "id": "TASK-2",
                    "status": status,
                    "execution_state": status,
                    "running": not state["committed"],
                    "workflow_run_id": "director-r94",
                    "fact_event_seq": 93 if state["committed"] else 92,
                    "last_heartbeat_at": (now - timedelta(seconds=1)).isoformat(),
                    "lease_expires_at": (now + timedelta(seconds=30)).isoformat(),
                    "metadata": {
                        "source": "task_runtime.execution_fact",
                        "status_source": "task_runtime.execution_fact",
                        "task_runtime_execution_fact": {"run_id": "director-r94"},
                    },
                },
            ),
            readiness={"ready": True, "blocking_reasons": []},
        )

    monkeypatch.setattr(
        waiter,
        "_observable_task_rows_projection",
        task_runtime_projection,
    )

    def canonical_result(**_kwargs: Any) -> CommandResult | None:
        if not state["committed"]:
            return None
        return CommandResult(
            run_id="director-r94",
            status="completed",
            message="TaskRuntime canonical projection reached completed",
            metadata={
                "canonical_authoritative": True,
                "terminal_source": "task_runtime.execution_fact",
                "fact_event_seq": 94,
            },
        )

    monkeypatch.setattr(waiter, "canonical_terminal_result", canonical_result)

    async def commit_task_runtime_fact() -> None:
        await asyncio.sleep(0.02)
        state["committed"] = True

    commit_task = asyncio.create_task(commit_task_runtime_fact())
    result = await waiter.wait(
        _FailedCommandService(),
        CommandResult(
            run_id="director-r94",
            status="failed",
            message="director_no_materialized_changes",
        ),
        timeout_seconds=1,
    )
    await commit_task

    assert result.status == "completed"
    assert result.metadata is not None
    assert result.metadata["canonical_authoritative"] is True
    assert result.metadata["terminal_source"] == "task_runtime.execution_fact"
    assert result.metadata["fact_event_seq"] == 94


@pytest.mark.asyncio
async def test_waiter_explicit_cancel_keeps_fixed_window_after_lifecycle_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Explicit cancel outranks lease waiting without mutating the active child."""

    class _Lifecycle:
        _active_runs: dict[str, asyncio.Task[Any]] = {}

        def __init__(self) -> None:
            self.cancelled: list[str] = []

        async def cancel_run(self, run_id: str, force: bool = False) -> None:
            del force
            self.cancelled.append(run_id)

    lifecycle = _Lifecycle()

    async def get_lifecycle() -> _Lifecycle:
        return lifecycle

    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
        get_lifecycle,
    )
    monkeypatch.setattr(completion_module, "_CANONICAL_OUTCOME_SETTLEMENT_SECONDS", 0.05)
    monkeypatch.setattr(completion_module, "_CANONICAL_POLL_SECONDS", 0.005)
    waiter = RunCompletionWaiter(tmp_path)
    projection_calls = 0

    def task_runtime_projection() -> ObservableTaskRowsProjectionV1:
        nonlocal projection_calls
        projection_calls += 1
        now = datetime.now(timezone.utc)
        return ObservableTaskRowsProjectionV1(
            workspace=str(tmp_path),
            source="task_runtime.execution_fact",
            authoritative=True,
            degraded=False,
            rows=(
                {
                    "id": "TASK-2",
                    "status": "in_progress",
                    "execution_state": "in_progress",
                    "running": True,
                    "workflow_run_id": "director-r94-cancel",
                    "fact_event_seq": projection_calls,
                    "last_heartbeat_at": (now - timedelta(seconds=1)).isoformat(),
                    "lease_expires_at": (now + timedelta(seconds=30)).isoformat(),
                    "metadata": {
                        "source": "task_runtime.execution_fact",
                        "status_source": "task_runtime.execution_fact",
                        "task_runtime_execution_fact": {"run_id": "director-r94-cancel"},
                    },
                },
            ),
            readiness={"ready": True, "blocking_reasons": []},
        )

    monkeypatch.setattr(waiter, "_observable_task_rows_projection", task_runtime_projection)
    monkeypatch.setattr(waiter, "canonical_terminal_result", lambda **_kwargs: None)
    cancel_event = asyncio.Event()
    cancel_event.set()
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    result = await waiter.wait(
        _FailedCommandService(),
        CommandResult(
            run_id="director-r94-cancel",
            status="failed",
            message="director_no_materialized_changes",
        ),
        timeout_seconds=1,
        cancel_event=cancel_event,
    )

    assert loop.time() - started_at < 0.25
    assert result.status == "cancelled"
    assert result.metadata is not None
    assert result.metadata["terminal_source"] == "task_runtime_active_execution_barrier"
    assert result.metadata["cancel_reason"] == "factory_cancelled"
    assert result.metadata["barrier_cancel_deferred"] is True
    assert result.metadata["deferred_cancel_reason"] == "factory_cancelled"
    assert lifecycle.cancelled == []


@pytest.mark.asyncio
async def test_waiter_allows_terminal_projection_to_catch_up_after_active_row_disappears(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """R94: active-to-terminal projection lag gets the fixed settlement window."""

    class _Lifecycle:
        _active_runs: dict[str, asyncio.Task[Any]] = {}

    async def get_lifecycle() -> _Lifecycle:
        return _Lifecycle()

    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
        get_lifecycle,
    )
    monkeypatch.setattr(completion_module, "_CANONICAL_OUTCOME_SETTLEMENT_SECONDS", 0.1)
    monkeypatch.setattr(completion_module, "_CANONICAL_POLL_SECONDS", 0.005)
    waiter = RunCompletionWaiter(tmp_path)
    projection_calls = 0
    canonical_calls = 0

    def task_runtime_projection() -> ObservableTaskRowsProjectionV1:
        nonlocal projection_calls
        projection_calls += 1
        now = datetime.now(timezone.utc)
        rows: tuple[dict[str, Any], ...] = ()
        if projection_calls <= 2:
            rows = (
                {
                    "id": "TASK-2",
                    "status": "in_progress",
                    "execution_state": "in_progress",
                    "running": True,
                    "workflow_run_id": "director-r94-gap",
                    "fact_event_seq": 95,
                    "last_heartbeat_at": (now - timedelta(seconds=1)).isoformat(),
                    "lease_expires_at": (now + timedelta(seconds=30)).isoformat(),
                    "metadata": {
                        "source": "task_runtime.execution_fact",
                        "status_source": "task_runtime.execution_fact",
                        "task_runtime_execution_fact": {"run_id": "director-r94-gap"},
                    },
                },
            )
        return ObservableTaskRowsProjectionV1(
            workspace=str(tmp_path),
            source="task_runtime.execution_fact",
            authoritative=True,
            degraded=False,
            rows=rows,
            readiness={"ready": True, "blocking_reasons": []},
        )

    monkeypatch.setattr(waiter, "_observable_task_rows_projection", task_runtime_projection)

    def canonical_result(**_kwargs: Any) -> CommandResult | None:
        nonlocal canonical_calls
        canonical_calls += 1
        if canonical_calls < 3:
            return None
        return CommandResult(
            run_id="director-r94-gap",
            status="completed",
            message="terminal fact visible after active-row projection",
            metadata={
                "canonical_authoritative": True,
                "terminal_source": "task_runtime.execution_fact",
                "fact_event_seq": 96,
            },
        )

    monkeypatch.setattr(waiter, "canonical_terminal_result", canonical_result)

    result = await waiter.wait(
        _FailedCommandService(),
        CommandResult(
            run_id="director-r94-gap",
            status="failed",
            message="director_no_materialized_changes",
        ),
        timeout_seconds=1,
    )

    assert result.status == "completed"
    assert result.metadata is not None
    assert result.metadata["fact_event_seq"] == 96
    assert projection_calls >= 3
    assert canonical_calls >= 3


def test_active_execution_barrier_rejects_degraded_transitional_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    waiter = RunCompletionWaiter(tmp_path)
    now = datetime.now(timezone.utc)
    projection = ObservableTaskRowsProjectionV1(
        workspace=str(tmp_path),
        source="task_runtime.transitional_file_fallback",
        authoritative=False,
        degraded=True,
        rows=(
            {
                "id": "TASK-2",
                "status": "in_progress",
                "workflow_run_id": "director-r94",
                "fact_event_seq": 97,
                "last_heartbeat_at": (now - timedelta(seconds=1)).isoformat(),
                "lease_expires_at": (now + timedelta(seconds=30)).isoformat(),
                "metadata": {
                    "source": "task_runtime.transitional_file_fallback",
                    "status_source": "task_runtime.transitional_file_fallback",
                },
            },
        ),
        readiness={"ready": False, "blocking_reasons": ["fact_cutover_not_ready"]},
    )
    monkeypatch.setattr(waiter, "_observable_task_rows_projection", lambda: projection)

    assert (
        waiter.active_execution_barrier_result(
            run_id="director-r94",
            reason="orchestration_lifecycle_failure",
        )
        is None
    )


@pytest.mark.parametrize(
    ("row_overrides", "reason"),
    [
        (
            {
                "workflow_run_id": "director-other",
                "factory_run_id": "director-r94",
                "metadata": {
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                    "task_runtime_execution_fact": {"run_id": "director-third"},
                },
            },
            "ambiguous child run identities",
        ),
        ({"fact_event_seq": 0}, "missing positive fact sequence"),
        ({"lease_expires_at": "2000-01-01T00:00:00+00:00"}, "expired execution lease"),
        ({"last_heartbeat_at": ""}, "missing heartbeat"),
    ],
)
def test_active_execution_barrier_rejects_noncanonical_or_stale_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    row_overrides: dict[str, Any],
    reason: str,
) -> None:
    del reason
    waiter = RunCompletionWaiter(tmp_path)
    now = datetime.now(timezone.utc)
    row: dict[str, Any] = {
        "id": "TASK-2",
        "status": "in_progress",
        "execution_state": "in_progress",
        "running": True,
        "workflow_run_id": "director-r94",
        "fact_event_seq": 98,
        "last_heartbeat_at": (now - timedelta(seconds=1)).isoformat(),
        "lease_expires_at": (now + timedelta(seconds=30)).isoformat(),
        "metadata": {
            "source": "task_runtime.execution_fact",
            "status_source": "task_runtime.execution_fact",
            "task_runtime_execution_fact": {"run_id": "director-r94"},
        },
    }
    row.update(row_overrides)
    projection = ObservableTaskRowsProjectionV1(
        workspace=str(tmp_path),
        source="task_runtime.execution_fact",
        authoritative=True,
        degraded=False,
        rows=(row,),
        readiness={"ready": True, "blocking_reasons": []},
    )
    monkeypatch.setattr(waiter, "_observable_task_rows_projection", lambda: projection)

    assert (
        waiter.active_execution_barrier_result(
            run_id="director-r94",
            reason="orchestration_lifecycle_failure",
        )
        is None
    )


@pytest.mark.asyncio
async def test_waiter_keeps_lifecycle_failure_when_task_runtime_is_not_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The R94 liveness exception must not hide a genuine lifecycle failure."""

    class _Lifecycle:
        _active_runs: dict[str, asyncio.Task[Any]] = {}

    async def get_lifecycle() -> _Lifecycle:
        return _Lifecycle()

    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
        get_lifecycle,
    )
    waiter = RunCompletionWaiter(tmp_path)
    monkeypatch.setattr(waiter, "canonical_terminal_result", lambda **_kwargs: None)
    monkeypatch.setattr(waiter, "_active_execution_rows", lambda **_kwargs: [])

    result = await waiter.wait(
        _CompletedCommandService(),
        CommandResult(
            run_id="director-real-failure",
            status="failed",
            message="director_no_materialized_changes",
        ),
        timeout_seconds=1,
    )

    assert result.status == "failed"
    assert result.message == "director_no_materialized_changes"
    assert result.metadata is not None
    assert result.metadata["canonical_authoritative"] is False
    assert result.metadata["terminal_source"] == "orchestration_lifecycle_failure"


@pytest.mark.asyncio
async def test_waiter_does_not_treat_transient_projection_readiness_as_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A non-authoritative readiness diagnostic must not close stage authority.

    Regression for R30: the Director TaskRuntime child was still active while
    the fact-only projection was briefly not ready. Returning that diagnostic
    as terminal let Factory close the stage-bound role-evidence authority before
    the child reached Provider transport.
    """

    class _Lifecycle:
        _active_runs: dict[str, asyncio.Task[Any]] = {}

    async def get_lifecycle() -> _Lifecycle:
        return _Lifecycle()

    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
        get_lifecycle,
    )
    waiter = RunCompletionWaiter(tmp_path)
    calls = 0

    def canonical_result(**_kwargs: Any) -> CommandResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            return CommandResult(
                run_id="director-r30",
                status="blocked",
                message="TaskRuntime fact-only observable projection is not ready",
                reason_code="task_runtime_fact_projection_not_ready",
                metadata={
                    "canonical_authoritative": False,
                    "degraded": True,
                    "terminal_source": "task_runtime_cutover_readiness",
                },
            )
        return CommandResult(
            run_id="director-r30",
            status="completed",
            message="TaskRuntime canonical projection reached completed",
            metadata={
                "canonical_authoritative": True,
                "terminal_source": "task_runtime.execution_fact",
                "fact_event_seq": 17,
            },
        )

    monkeypatch.setattr(waiter, "canonical_terminal_result", canonical_result)

    result = await waiter.wait(
        _CompletedCommandService(),
        CommandResult(run_id="director-r30", status="running", message="submitted"),
        timeout_seconds=1,
    )

    assert calls >= 2
    assert result.status == "completed"
    assert result.metadata is not None
    assert result.metadata["canonical_authoritative"] is True
    assert result.metadata["fact_event_seq"] == 17


@pytest.mark.asyncio
async def test_cancel_during_dispatch_waits_for_committed_terminal_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    active_task = asyncio.create_task(asyncio.sleep(60))

    class _Lifecycle:
        _active_runs = {"run-dispatch": active_task}

        def __init__(self) -> None:
            self.cancelled = False

        async def cancel_run(self, run_id: str, force: bool = False) -> None:
            del run_id, force
            self.cancelled = True

    lifecycle = _Lifecycle()

    async def get_lifecycle() -> _Lifecycle:
        return lifecycle

    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
        get_lifecycle,
    )
    waiter = RunCompletionWaiter(tmp_path)
    state = {"committed": False}
    monkeypatch.setattr(
        waiter,
        "_active_execution_rows",
        lambda **_kwargs: [] if state["committed"] else [{"id": "TASK-1"}],
    )

    def canonical_result(**_kwargs: Any) -> CommandResult | None:
        if not state["committed"]:
            return None
        return CommandResult(
            run_id="run-dispatch",
            status="completed",
            message="effect receipt committed",
            metadata={
                "canonical_authoritative": True,
                "fact_event_seq": 17,
                "terminal_source": "task_runtime.execution_fact",
            },
        )

    monkeypatch.setattr(waiter, "canonical_terminal_result", canonical_result)

    async def commit_after_dispatch() -> None:
        await asyncio.sleep(0.02)
        state["committed"] = True

    commit_task = asyncio.create_task(commit_after_dispatch())
    cancel_event = asyncio.Event()
    cancel_event.set()
    result = await waiter.wait(
        _CompletedCommandService(),
        CommandResult(run_id="run-dispatch", status="running", message="dispatching"),
        timeout_seconds=1,
        cancel_event=cancel_event,
    )
    await commit_task
    active_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await active_task

    assert result.status == "completed"
    assert result.metadata is not None
    assert result.metadata["fact_event_seq"] == 17
    assert lifecycle.cancelled is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("report_passed", "canonical_qa_passed", "expected_status"),
    [
        (True, False, "failed"),
        (False, True, "success"),
    ],
)
async def test_quality_gate_conflict_matrix_uses_canonical_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    report_passed: bool,
    canonical_qa_passed: bool,
    expected_status: str,
) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    run = _factory_run()
    authority_port = object.__new__(FactoryRoleEvidenceAuthorityPort)

    async def call_with_test_authority(
        _authority_port: object,
        _role: str,
        operation: Any,
    ) -> Any:
        return await operation()

    async def workspace_checks(_run: FactoryRun, _context: dict[str, Any]) -> tuple[bool, str]:
        return True, ""

    async def terminal_result(*_args: Any, **_kwargs: Any) -> CommandResult:
        report_path = Path(resolve_logical_path(tmp_path, "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"passed": report_passed, "score": 100 if report_passed else 0}),
            encoding="utf-8",
        )
        return CommandResult(
            run_id="qa-run",
            status="completed",
            message="qa terminal",
            metadata={"canonical_authoritative": True, "fact_event_seq": 23},
        )

    monkeypatch.setattr(executor, "_run_workspace_quality_checks", workspace_checks)
    monkeypatch.setattr(executor, "_call_with_factory_role_evidence_authority", call_with_test_authority)
    monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: _QaCommandService())
    monkeypatch.setattr(executor, "_wait_run_completion", terminal_result)
    monkeypatch.setattr(
        executor,
        "_canonical_factory_projection",
        lambda _run, _context: _canonical_projection(qa_ok=canonical_qa_passed),
    )

    result = await executor._execute_quality_gate(
        run,
        {
            "qa_target": "Quality gate",
            "canonical_projection_settlement_timeout_seconds": 0.1,
            executor_module.FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY: authority_port,
        },
    )

    assert result.status == expected_status
    assert f"report_consistent={report_passed == canonical_qa_passed}" in result.output
    assert f"qa_verdict_passed={canonical_qa_passed}" in result.output


@pytest.mark.asyncio
async def test_complete_run_records_operational_not_verified_status(tmp_path: Path) -> None:
    service = FactoryRunService(tmp_path)
    run = await service.create_run(FactoryConfig(name="session-only"))
    await service.start_run(run.id)

    completed = await service.complete_run(run.id, success=True)
    events = await service.get_run_events(run.id)
    completion = next(event for event in events if event.get("type") == "completed")

    assert completed.status == FactoryRunStatus.COMPLETED
    assert completed.metadata["verified"] is False
    assert completed.metadata["verification_authority"] == "execution_ledger_projection"
    assert completion["authoritative"] is False
    assert completion["verified"] is False
