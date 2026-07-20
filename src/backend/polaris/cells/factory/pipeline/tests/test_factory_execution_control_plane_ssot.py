"""Execution Control Plane single-source-of-truth regressions for Factory."""

from __future__ import annotations

import asyncio
import contextlib
import json
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
