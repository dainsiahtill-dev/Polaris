"""Top-level characterization tests for OrchestrationStageExecutor helper clusters (part 1)."""

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
    run_ledger as run_ledger_module,
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
    _authoritative_task_projection,
    _bootstrap_fact_stream_workspace,
    _executor,
    _factory_stage_context,
    _factory_workspace_run_lease,
    _with_task_runtime_authority,
)


def test_canonical_qa_commit_identity_uses_final_owned_task_child_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    run = SimpleNamespace(id="factory-run")
    monkeypatch.setattr(
        executor,
        "_load_pm_plan_tasks",
        lambda _path: [{"id": "TASK-1"}, {"id": "TASK-2"}, {"id": "TASK-3"}],
    )
    monkeypatch.setattr(
        executor,
        "_canonical_factory_projection",
        lambda _run, _context: {
            "task_boundary": {
                "latest_by_task": {
                    "TASK-1": {"run_id": "director-1", "ok": True, "status": "completed_verified"},
                    "TASK-2": {"run_id": "director-2", "ok": True, "status": "completed_verified"},
                    "TASK-3": {"run_id": "director-3", "ok": True, "status": "completed_verified"},
                }
            }
        },
    )

    assert executor._canonical_qa_commit_identity(run=run, context={}) == ("TASK-3", "director-3")


def test_executor_constructor_does_not_bootstrap_fact_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pure executor construction must never provision or maintain FactStream."""

    def _fail_bootstrap(_workspace: Path) -> None:
        pytest.fail("executor construction must not bootstrap FactStream")

    monkeypatch.setitem(globals(), "_bootstrap_fact_stream_workspace", _fail_bootstrap)

    executor = _executor(Path("."))

    assert isinstance(executor, OrchestrationStageExecutor)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    ("pm_planning", "chief_engineer_review", "director_dispatch", "quality_gate"),
)
async def test_direct_stage_missing_cutoff_port_fails_before_service_or_role_call(
    stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every role stage must reject missing live authority before dispatch."""

    executor = _executor(tmp_path)
    run = FactoryRun(
        id=f"missing-authority-{stage}",
        config=FactoryConfig(name="missing-authority", stages=[stage]),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-07-19T00:00:00+00:00",
    )
    service_or_role_calls: list[str] = []

    def unexpected_service(_context: dict[str, Any]) -> object:
        service_or_role_calls.append("service")
        raise AssertionError("service dispatch must not run without cutoff authority")

    class _UnexpectedRoleRuntimeService:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            service_or_role_calls.append("role")
            raise AssertionError("role dispatch must not run without cutoff authority")

    monkeypatch.setattr(executor, "_build_orchestration_service", unexpected_service)
    monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _UnexpectedRoleRuntimeService)

    with pytest.raises(RuntimeError, match=r"^factory_role_evidence_live_cutoff_port_required$"):
        await executor.execute(stage, run, {})

    assert service_or_role_calls == []


def test_materialize_pm_task_projects_current_factory_workspace_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_fact_stream_workspace(workspace)
    executor = _executor(workspace)
    run_id = "factory-run-lease-current"
    current_lease = _factory_workspace_run_lease(
        workspace,
        run_id=run_id,
        fencing_token=41,
    )
    expected_lease = json.loads(json.dumps(current_lease, ensure_ascii=False))
    forged_task_lease = _factory_workspace_run_lease(
        workspace,
        run_id="factory-run-forged",
        fencing_token=999,
    )
    monkeypatch.setattr(
        TaskRuntimeService,
        "_publish_factory_execution_event",
        lambda _service, _payload: True,
    )
    tasks = [
        {
            "id": "PM-LEASE-1",
            "objective": "Preserve Factory workspace authority provenance",
            "metadata": {
                "factory_workspace_run_lease": forged_task_lease,
            },
        }
    ]

    summary = executor._materialize_pm_plan_taskboard(
        tasks,
        run_id=run_id,
        source_stage="pm_planning",
        run_metadata={"factory_workspace_run_lease": current_lease},
    )
    current_lease["fencing_token"] = 88
    current_lease["stage_execution_claim"]["nonce"] = "mutated-after-materialization"

    row = TaskRuntimeService(str(workspace)).get_task("PM-LEASE-1")

    assert summary["ensured_count"] == 1
    assert summary["bound_count"] == 1
    assert row is not None
    assert row["metadata"]["factory_run_id"] == run_id
    assert row["metadata"]["factory_workspace_run_lease"] == expected_lease

    refreshed_lease = _factory_workspace_run_lease(
        workspace,
        run_id=run_id,
        fencing_token=41,
    )
    refreshed_lease["version"] = 4
    refreshed_lease["updated_at"] = "2026-07-13T00:03:00+00:00"
    expected_refreshed_lease = json.loads(json.dumps(refreshed_lease, ensure_ascii=False))
    refresh_summary = executor._materialize_pm_plan_taskboard(
        tasks,
        run_id=run_id,
        source_stage="director_dispatch",
        run_metadata={"factory_workspace_run_lease": refreshed_lease},
    )
    refreshed_lease["stage_execution_claim"]["nonce"] = "mutated-after-refresh"
    row = TaskRuntimeService(str(workspace)).get_task("PM-LEASE-1")

    assert refresh_summary["binding_failures"] == []
    assert row is not None
    assert row["status"] == "pending"
    assert row["metadata"]["factory_workspace_run_lease"] == expected_refreshed_lease
    metadata_refresh_events = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="updated",
        )
    ).events
    assert len(metadata_refresh_events) == 1
    metadata_refresh_payload = metadata_refresh_events[0]["payload"]
    assert metadata_refresh_payload["status"] == "pending"
    assert metadata_refresh_payload["details"]["status"] == ""
    assert metadata_refresh_payload["details"]["metadata_updated"] is True

    task_runtime = TaskRuntimeService(str(workspace))
    claimed = task_runtime.claim_execution(
        row["id"],
        worker_id="director",
        role_id="director",
        run_id="director-child-run",
        selection_source="task_id_lookup",
    )
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claimed["execution_attempt"])
    completed = task_runtime.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary="fenced terminal fact committed",
        )
    )
    assert completed["success"] is True
    terminal_events = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="completed",
        )
    ).events
    assert len(terminal_events) == 1

    terminal_payload = terminal_events[0]["payload"]
    assert terminal_payload["factory_run_id"] == run_id
    assert _fencing_token(terminal_payload) == 41
    assert terminal_payload["factory_workspace_run_lease"] == expected_refreshed_lease


@pytest.mark.parametrize(
    "run_metadata",
    [
        None,
        {},
        {"factory_workspace_run_lease": "not-a-lease-mapping"},
    ],
)
def test_materialize_pm_task_never_trusts_task_supplied_workspace_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_metadata: dict[str, Any] | None,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_fact_stream_workspace(workspace)
    monkeypatch.setattr(
        TaskRuntimeService,
        "_publish_factory_execution_event",
        lambda _service, _payload: True,
    )

    _executor(workspace)._materialize_pm_plan_taskboard(
        [
            {
                "id": "PM-LEASE-UNTRUSTED",
                "objective": "Reject task-supplied Factory authority",
                "metadata": {
                    "factory_workspace_run_lease": {
                        "run_id": "forged-run",
                        "fencing_token": 777,
                    }
                },
            }
        ],
        run_id="factory-run-without-lease-projection",
        source_stage="pm_planning",
        run_metadata=run_metadata,
    )

    row = TaskRuntimeService(str(workspace)).get_task("PM-LEASE-UNTRUSTED")

    assert row is not None
    assert "factory_workspace_run_lease" not in row["metadata"]


@pytest.mark.parametrize(
    ("sequence_ready", "boundary_status", "qa_ok", "policy_ok", "expected", "reason_code"),
    [
        (False, "completed_verified", True, True, False, "canonical_sequence_barrier_unsatisfied"),
        (True, "incomplete_materialization", True, True, False, "task_boundary_not_completed_verified"),
        (True, "completed_verified", False, True, False, "qa_verdict_failed"),
        (True, "completed_verified", True, False, False, "evidence_policy_failed"),
        (True, "completed_verified", True, True, True, "canonical_projection_authorized"),
    ],
)
def test_canonical_factory_authority_conflict_matrix(
    sequence_ready: bool,
    boundary_status: str,
    qa_ok: bool,
    policy_ok: bool,
    expected: bool,
    reason_code: str,
) -> None:
    boundary_ok = boundary_status == "completed_verified"
    projection = _with_task_runtime_authority(
        {
            "source": "run_ledger",
            "integrity_ok": policy_ok,
            "outcome_ok": policy_ok and boundary_ok and qa_ok,
            "task_boundary": {
                "latest_by_task": {
                    "TASK-1": {
                        "task_id": "TASK-1",
                        "status": boundary_status,
                        "ok": boundary_ok,
                        "failure_class": "PASSED" if boundary_ok else "INCOMPLETE_MATERIALIZATION",
                        "responsible_layer": "execution_control_plane",
                    }
                }
            },
            "gates": [
                {
                    "name": "qa_verdict",
                    "ok": qa_ok,
                    "append_id": "qa-append-1",
                    "content_id": "qa-content-1",
                }
            ],
            "evidence_policy": {
                "integrity_ok": policy_ok,
                "outcome_ok": policy_ok,
                "missing_required_modalities": [] if policy_ok else ["command"],
                "failed_required_modalities": [],
            },
        }
    )

    authority = evaluate_canonical_factory_authority(
        projection,
        sequence_barrier_satisfied=sequence_ready,
    )

    assert authority.quality_stage_authorized is expected
    assert authority.reason_code == reason_code


def test_run_completion_blocks_degraded_task_runtime_projection(tmp_path: Path) -> None:
    waiter = RunCompletionWaiter(tmp_path)
    degraded = ObservableTaskRowsProjectionV1(
        workspace=str(tmp_path),
        source="task_runtime.transitional_file_fallback",
        authoritative=False,
        degraded=True,
        rows=(
            {
                "id": "TASK-1",
                "workflow_run_id": "run-1",
                "execution_state": "completed",
                "fact_event_seq": 9,
            },
        ),
        readiness={"ready": False, "blocking_reasons": ["task_row_file_fallback_required"]},
    )
    waiter._observable_task_rows_projection = lambda: degraded  # type: ignore[method-assign]

    result = waiter.canonical_terminal_result(run_id="run-1", process_terminal=True)

    assert result is not None
    assert result.status == "blocked"
    assert result.reason_code == "task_runtime_fact_projection_not_ready"
    assert result.metadata["canonical_authoritative"] is False
    assert result.metadata["degraded"] is True


def test_run_completion_conflict_matrix_prefers_failure(tmp_path: Path) -> None:
    waiter = RunCompletionWaiter(tmp_path)
    projection = _authoritative_task_projection(tmp_path, ())
    waiter._observable_task_rows_projection = lambda: projection  # type: ignore[method-assign]
    waiter._task_runtime_terminal_result = lambda **_kwargs: CommandResult(  # type: ignore[method-assign]
        run_id="run-1",
        status="completed",
        message="task runtime complete",
        metadata={"canonical_authoritative": True, "fact_event_seq": 11},
    )
    waiter._committed_turn_outcome_result = lambda **_kwargs: CommandResult(  # type: ignore[method-assign]
        run_id="run-1",
        status="failed",
        message="turn outcome failed",
        metadata={"canonical_authoritative": True, "fact_event_seq": 7},
    )

    result = waiter.canonical_terminal_result(run_id="run-1", process_terminal=True)

    assert result is not None
    assert result.status == "failed"
    assert result.metadata["terminal_source"] == "canonical_conflict_matrix"
    assert result.metadata["canonical_conflict"] is True


def test_run_completion_does_not_promote_turn_outcome_over_active_task_runtime(
    tmp_path: Path,
) -> None:
    waiter = RunCompletionWaiter(tmp_path)
    projection = _authoritative_task_projection(
        tmp_path,
        (
            {
                "id": "TASK-1",
                "workflow_run_id": "run-1",
                "execution_state": "in_progress",
                "fact_event_seq": 12,
                "metadata": {
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                },
            },
        ),
    )
    waiter._observable_task_rows_projection = lambda: projection  # type: ignore[method-assign]
    waiter._committed_turn_outcome_result = lambda **_kwargs: CommandResult(  # type: ignore[method-assign]
        run_id="run-1",
        status="completed",
        message="one role turn completed",
        metadata={"canonical_authoritative": True, "fact_event_seq": 13},
    )

    result = waiter.canonical_terminal_result(run_id="run-1", process_terminal=True)

    assert result is None


def test_waiter_reuses_task_runtime_projection_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = {"n": 0}
    fake = SimpleNamespace(
        authoritative=True,
        degraded=False,
        source="task_runtime.execution_fact",
        rows=(),
        readiness={"ready": True},
    )

    class _Svc:
        def __init__(self, _workspace: str) -> None:
            calls["n"] += 1

        def query_observable_task_rows_projection(self) -> Any:
            return fake

    monkeypatch.setattr(
        "polaris.cells.runtime.task_runtime.public.service.TaskRuntimeService",
        _Svc,
    )
    waiter = RunCompletionWaiter(tmp_path)
    first = waiter._observable_task_rows_projection()
    second = waiter._observable_task_rows_projection()
    assert first is fake
    assert second is fake
    assert calls["n"] == 1


def test_canonical_terminal_skips_turn_outcome_query_until_task_runtime_terminal(
    tmp_path: Path,
) -> None:
    waiter = RunCompletionWaiter(tmp_path)
    projection = _authoritative_task_projection(
        tmp_path,
        (
            {
                "id": "TASK-1",
                "workflow_run_id": "run-1",
                "execution_state": "in_progress",
                "fact_event_seq": 12,
                "metadata": {
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                },
            },
        ),
    )
    waiter._observable_task_rows_projection = lambda: projection  # type: ignore[method-assign]
    turn_outcome_calls = {"n": 0}

    def _turn_outcome(**_kwargs: Any) -> CommandResult | None:
        turn_outcome_calls["n"] += 1
        raise AssertionError("turn outcome must not be queried while TaskRuntime is non-terminal")

    waiter._committed_turn_outcome_result = _turn_outcome  # type: ignore[method-assign]

    result = waiter.canonical_terminal_result(run_id="run-1", process_terminal=True)

    assert result is None
    assert turn_outcome_calls["n"] == 0


def test_canonical_terminal_queries_turn_outcome_only_after_task_runtime_terminal(
    tmp_path: Path,
) -> None:
    waiter = RunCompletionWaiter(tmp_path)
    projection = _authoritative_task_projection(tmp_path, ())
    waiter._observable_task_rows_projection = lambda: projection  # type: ignore[method-assign]
    waiter._task_runtime_terminal_result = lambda **_kwargs: CommandResult(  # type: ignore[method-assign]
        run_id="run-1",
        status="completed",
        message="task runtime complete",
        metadata={"canonical_authoritative": True, "fact_event_seq": 11},
    )
    turn_outcome_calls = {"n": 0}

    def _turn_outcome(**_kwargs: Any) -> CommandResult | None:
        turn_outcome_calls["n"] += 1
        return None

    waiter._committed_turn_outcome_result = _turn_outcome  # type: ignore[method-assign]

    result = waiter.canonical_terminal_result(run_id="run-1", process_terminal=True)

    assert result is not None
    assert result.status == "completed"
    assert turn_outcome_calls["n"] == 1


@pytest.mark.asyncio
async def test_run_completion_cancel_during_dispatch_waits_for_canonical_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _FakeOrchestrationService:
        def __init__(self) -> None:
            self.active_task = asyncio.create_task(asyncio.sleep(60))
            self._active_runs = {"run-1": self.active_task}
            self.cancelled: list[str] = []

        async def cancel_run(self, run_id: str, force: bool = False) -> None:
            del force
            self.cancelled.append(run_id)

    class _FakeCommandService:
        async def query_run_status(self, run_id: str) -> CommandResult:
            return CommandResult(run_id=run_id, status="running", message="dispatching")

    fake_orchestration = _FakeOrchestrationService()

    async def _get_orchestration_service() -> _FakeOrchestrationService:
        return fake_orchestration

    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
        _get_orchestration_service,
    )
    reads = 0

    def _projection() -> ObservableTaskRowsProjectionV1:
        nonlocal reads
        reads += 1
        status = "in_execution" if reads < 4 else "completed"
        now = datetime.now(timezone.utc)
        return _authoritative_task_projection(
            tmp_path,
            (
                {
                    "id": "TASK-1",
                    "task_id": "TASK-1",
                    "workflow_run_id": "run-1",
                    "execution_state": status,
                    "running": status == "in_execution",
                    "fact_event_seq": reads,
                    "last_heartbeat_at": (now - timedelta(seconds=1)).isoformat(),
                    "lease_expires_at": (now + timedelta(seconds=30)).isoformat(),
                    "metadata": {
                        "source": "task_runtime.execution_fact",
                        "status_source": "task_runtime.execution_fact",
                    },
                },
            ),
        )

    waiter = RunCompletionWaiter(tmp_path)
    waiter._observable_task_rows_projection = _projection  # type: ignore[method-assign]
    cancel_event = asyncio.Event()
    cancel_event.set()

    result = await waiter.wait(
        _FakeCommandService(),
        CommandResult(run_id="run-1", status="running", message="submitted"),
        timeout_seconds=1,
        cancel_event=cancel_event,
    )

    assert result.status == "completed"
    assert result.metadata["canonical_authoritative"] is True
    assert result.metadata["terminal_source"] == "task_runtime.execution_fact"
    assert fake_orchestration.cancelled == []
    fake_orchestration.active_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await fake_orchestration.active_task


@pytest.mark.asyncio
async def test_quality_gate_authority_ignores_report_and_workspace_display_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    run = FactoryRun(
        id="factory-authority",
        config=FactoryConfig(name="authority"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-07-13T00:00:00+00:00",
    )
    executor._write_json_artifact(
        "runtime/qa/report.json",
        {
            "passed": False,
            "score": 0,
            "critical_issue_count": 9,
            "warnings": ["display_only"],
        },
    )

    async def _workspace_checks(
        _run: FactoryRun,
        _context: dict[str, Any],
    ) -> tuple[bool, str]:
        return True, ""

    class _Service:
        async def execute_qa_run(self, **_kwargs: Any) -> CommandResult:
            return CommandResult(run_id="qa-run", status="running", message="submitted")

    async def _wait(*_args: Any, **_kwargs: Any) -> CommandResult:
        return CommandResult(
            run_id="qa-run",
            status="completed",
            message="committed",
            metadata={
                "canonical_authoritative": True,
                "terminal_source": "task_runtime.execution_fact",
                "fact_event_seq": 19,
            },
        )

    projection = _with_task_runtime_authority(
        {
            "source": "run_ledger",
            "integrity_ok": True,
            "outcome_ok": True,
            "task_boundary": {
                "latest_by_task": {
                    "TASK-1": {
                        "task_id": "TASK-1",
                        "status": "completed_verified",
                        "ok": True,
                        "failure_class": "PASSED",
                        "responsible_layer": "execution_control_plane",
                    }
                }
            },
            "gates": [
                {
                    "name": "qa_verdict",
                    "ok": True,
                    "append_id": "qa-append-2",
                    "content_id": "qa-content-2",
                }
            ],
            "evidence_policy": {
                "integrity_ok": True,
                "outcome_ok": True,
                "missing_required_modalities": [],
                "failed_required_modalities": [],
            },
        }
    )
    monkeypatch.setattr(executor, "_run_workspace_quality_checks", _workspace_checks)
    monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: _Service())
    monkeypatch.setattr(executor, "_wait_run_completion", _wait)
    monkeypatch.setattr(executor, "_canonical_factory_projection", lambda _run, _context: projection)

    result = await executor._execute_quality_gate(
        run,
        _factory_stage_context({"qa_target": "Quality gate"}),
    )

    assert result.status == "success"
    assert "canonical_authorized=True" in str(result.output)
    assert "report_consistent=False" in str(result.output)


def test_read_claimable_director_task_ids_uses_observable_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        TaskRuntimeService,
        "query_observable_task_rows_projection",
        lambda runtime: _authoritative_task_projection(
            Path(runtime.workspace),
            (
                {"id": 1, "status": "pending", "metadata": {"pm_task_id": "TASK-1"}},
                {"id": 2, "status": "ready", "metadata": {"external_task_id": "TASK-2"}},
                {"id": 3, "status": "pending", "blocked_by": [1]},
                {"id": 4, "status": "completed", "metadata": {"pm_task_id": "TASK-4"}},
            ),
        ),
    )

    claimable = _executor(tmp_path)._read_claimable_director_task_ids(limit=10)

    assert claimable == ["TASK-1", "TASK-2"]


def test_read_claimable_director_task_ids_confines_parallel_claims_to_admitted_wave(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Parallel capacity must not bypass the PM dependency wave."""

    monkeypatch.setattr(
        TaskRuntimeService,
        "query_observable_task_rows_projection",
        lambda runtime: _authoritative_task_projection(
            Path(runtime.workspace),
            rows=[
                {"id": 1, "status": "pending", "metadata": {"external_task_id": "TASK-1"}},
                {
                    "id": 2,
                    "status": "pending",
                    "metadata": {
                        "external_task_id": "TASK-2",
                        "depends_on": ["TASK-1"],
                    },
                },
                {
                    "id": 3,
                    "status": "ready",
                    "metadata": {
                        "external_task_id": "TASK-3",
                        "depends_on": ["TASK-2"],
                    },
                },
            ],
        ),
    )

    executor = _executor(tmp_path)
    first_wave = executor._read_claimable_director_task_ids(
        limit=3,
        allowed_task_ids=("TASK-1",),
    )
    second_wave = executor._read_claimable_director_task_ids(
        limit=3,
        allowed_task_ids=("TASK-2",),
    )

    assert first_wave == ["TASK-1"]
    assert second_wave == ["TASK-2"]


def test_read_claimable_director_task_ids_excludes_trusted_internal_ce_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory_run_id = "factory-run-mixed-task-domains"

    def _metadata(external_task_id: str) -> dict[str, str]:
        return {
            "factory_run_id": factory_run_id,
            "factory_stage": "chief_engineer_review",
            "role": "chief_engineer",
            "external_task_id": external_task_id,
            "source_task_id": external_task_id,
            "materialized_by": "runtime.task_runtime",
        }

    monkeypatch.setattr(
        TaskRuntimeService,
        "query_observable_task_rows_projection",
        lambda runtime: _authoritative_task_projection(
            Path(runtime.workspace),
            (
                {
                    "id": 1,
                    "status": "ready",
                    "metadata": {
                        "factory_run_id": factory_run_id,
                        "pm_task_id": "TASK-2",
                        "external_task_id": "TASK-2",
                    },
                },
                {
                    "id": 2,
                    "status": "pending",
                    "metadata": _metadata(f"CE-PORTFOLIO-{factory_run_id}"),
                },
                {
                    "id": 3,
                    "status": "ready",
                    "metadata": _metadata(f"CE-PORTFOLIO-{factory_run_id}-SCHEMA-REPAIR"),
                },
            ),
        ),
    )

    claimable = _executor(tmp_path)._read_claimable_director_task_ids(
        limit=10,
        factory_run_id=factory_run_id,
    )

    assert claimable == ["TASK-2"]


def test_unresolved_task_ids_use_same_external_identity_as_claims() -> None:
    rows = [
        {"id": 1, "status": "pending", "metadata": {"external_task_id": "TASK-1"}},
        {"id": 2, "status": "ready", "metadata": {"pm_task_id": "TASK-2"}},
        {"id": 3, "status": "in_progress", "metadata": {"source_task_id": "TASK-3"}},
        {"id": 4, "status": "completed", "metadata": {"external_task_id": "TASK-4"}},
    ]

    unresolved = OrchestrationStageExecutor._unresolved_task_ids_from_rows(rows)

    assert unresolved == ("TASK-1", "TASK-2", "TASK-3")


def test_director_dependency_schedule_excludes_trusted_internal_ce_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    factory_run_id = "factory-run-schema-repair"
    rows = [
        {"id": 1, "status": "pending", "metadata": {"external_task_id": "TASK-1"}},
        {"id": 2, "status": "ready", "metadata": {"external_task_id": "TASK-2"}},
        {
            "id": 3,
            "status": "pending",
            "metadata": {
                "factory_run_id": factory_run_id,
                "factory_stage": "chief_engineer_review",
                "role": "chief_engineer",
                "external_task_id": f"CE-PORTFOLIO-{factory_run_id}",
                "source_task_id": f"CE-PORTFOLIO-{factory_run_id}",
                "materialized_by": "runtime.task_runtime",
            },
        },
    ]
    monkeypatch.setattr(executor, "_read_observable_task_rows", lambda **_kwargs: rows)

    schedule = executor._director_dependency_schedule(
        [
            {"id": "TASK-1"},
            {"id": "TASK-2", "depends_on": ["TASK-1"]},
        ],
        factory_run_id=factory_run_id,
    )

    assert schedule.valid is True
    assert schedule.active_task_ids == ("TASK-1", "TASK-2")
    assert schedule.blockers == ()


def test_director_dependency_schedule_keeps_untrusted_unknown_task_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    rows = [
        {"id": 1, "status": "pending", "metadata": {"external_task_id": "TASK-1"}},
        {
            "id": 2,
            "status": "pending",
            "metadata": {
                "factory_run_id": "factory-run",
                "factory_stage": "chief_engineer_review",
                "role": "chief_engineer",
                "external_task_id": "UNTRUSTED-INTERNAL-LOOKALIKE",
                "source_task_id": "UNTRUSTED-INTERNAL-LOOKALIKE",
                # Deliberately lacks the TaskRuntime materialization provenance.
            },
        },
    ]
    monkeypatch.setattr(executor, "_read_observable_task_rows", lambda **_kwargs: rows)

    schedule = executor._director_dependency_schedule(
        [{"id": "TASK-1"}],
        factory_run_id="factory-run",
    )

    assert schedule.valid is False
    assert schedule.blockers == ("unknown_active_task_ids:UNTRUSTED-INTERNAL-LOOKALIKE",)


def test_read_claimable_director_task_ids_skips_execution_owned_states(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        TaskRuntimeService,
        "query_observable_task_rows_projection",
        lambda runtime: _authoritative_task_projection(
            Path(runtime.workspace),
            (
                {"id": 1, "status": "pending", "metadata": {"external_task_id": "TASK-PENDING"}},
                {"id": 2, "status": "in_progress", "metadata": {"external_task_id": "TASK-IN-PROGRESS"}},
                {"id": 3, "status": "running", "metadata": {"external_task_id": "TASK-RUNNING"}},
                {"id": 4, "status": "claimed", "metadata": {"external_task_id": "TASK-CLAIMED"}},
            ),
        ),
    )

    claimable = _executor(tmp_path)._read_claimable_director_task_ids(limit=10)

    assert claimable == ["TASK-PENDING"]


def test_taskboard_stats_read_observable_owner_projection_when_stats_diverge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = {"observable_stats": 0, "raw_stats": 0}

    class _DivergedTaskRuntime:
        def __init__(self, workspace: str) -> None:
            assert workspace == str(tmp_path)

        def get_observable_task_row_stats(self) -> dict[str, int]:
            calls["observable_stats"] += 1
            return {"total": 2, "pending": 0, "ready": 0, "completed": 1, "failed": 1}

        def get_task_row_stats(self) -> dict[str, int]:
            calls["raw_stats"] += 1
            return {"total": 2, "pending": 2, "ready": 2, "completed": 0, "failed": 0}

    monkeypatch.setattr(stage_executor_module, "TaskRuntimeService", _DivergedTaskRuntime)

    stats = _executor(tmp_path)._read_taskboard_stats()

    assert calls["observable_stats"] == 1
    assert calls["raw_stats"] == 0
    assert stats["total"] == 2
    assert stats["pending"] == 0
    assert stats["ready"] == 0
    assert stats["completed"] == 1
    assert stats["failed"] == 1
    assert OrchestrationStageExecutor._is_taskboard_converged(stats) is True


# ---------------------------------------------------------------------------
# WS2 observable stats source regression guards (AST-level)
# ---------------------------------------------------------------------------
# These tests statically inspect the *source code* of _read_taskboard_stats
# to prove it delegates to get_observable_task_row_stats() and never falls
# back to get_task_row_stats() or list_observable_task_rows().  If a future
# refactor accidentally swaps the method name, these tests fail at collection
# time even before any monkeypatching exercises the runtime path.
# ---------------------------------------------------------------------------


def test_read_taskboard_stats_ast_calls_observable_not_legacy() -> None:
    """_read_taskboard_stats() must call get_observable_task_row_stats().

    It must never call the legacy get_task_row_stats() compatibility wrapper,
    which would produce identical numbers today but bypasses the
    fact-overlay contract that observable stats enforce.
    """
    src = textwrap.dedent(inspect.getsource(OrchestrationStageExecutor._read_taskboard_stats))
    tree = ast.parse(src)

    calls_on_instance: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Call)
        ):
            calls_on_instance.add(node.func.attr)

    assert "get_observable_task_row_stats" in calls_on_instance, (
        "_read_taskboard_stats() must call get_observable_task_row_stats() on "
        "TaskRuntimeService; the observable projection is the WS2 contract"
    )
    assert "get_task_row_stats" not in calls_on_instance, (
        "_read_taskboard_stats() must not call the legacy get_task_row_stats() "
        "wrapper — use get_observable_task_row_stats() directly"
    )


def test_read_taskboard_stats_ast_does_not_list_rows() -> None:
    """_read_taskboard_stats() must not call list_observable_task_rows().

    Stats aggregation belongs in the task-runtime service layer via
    get_observable_task_row_stats().  Factory must not reimplement
    row-level counting; _read_observable_task_rows() remains available for
    claimable-row inspection.
    """
    src = textwrap.dedent(inspect.getsource(OrchestrationStageExecutor._read_taskboard_stats))
    tree = ast.parse(src)

    calls_on_instance: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Call)
        ):
            calls_on_instance.add(node.func.attr)

    assert "list_observable_task_rows" not in calls_on_instance, (
        "_read_taskboard_stats() must not call list_observable_task_rows() — "
        "that method is for claimable-row inspection, not stats aggregation; "
        "use get_observable_task_row_stats() instead"
    )


def test_materialization_quality_target_filter_prefers_ts_source_over_compiled_outputs(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "dist").mkdir(parents=True)
    (tmp_path / "tests" / "behavior.test.ts").write_text("export const source = 1;\n", encoding="utf-8")
    (tmp_path / "tests" / "behavior.test.js").write_text("export const source = 1;\n", encoding="utf-8")
    (tmp_path / "dist" / "main.js").write_text("export const compiled = 1;\n", encoding="utf-8")
    errors = [
        "\n".join(
            [
                "TypeScript project typecheck failed:",
                "tests/behavior.test.ts(10,1): error TS1003: Identifier expected.",
                "tests/behavior.test.js(10,1): error TS1003: Identifier expected.",
                "dist/main.js(1,1): error TS1003: Identifier expected.",
            ]
        )
    ]

    targets = resolve_director_semantic_quality_repair_target_files(
        artifact_quality_errors=errors,
        changed_files=["tests/behavior.test.ts", "tests/behavior.test.js", "dist/main.js"],
        workspace_full=str(tmp_path),
    )

    assert targets == ["tests/behavior.test.ts"]


def test_workspace_quality_diagnostic_targets_include_language_neutral_manifests(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    executor = _executor(tmp_path)

    targets = executor._workspace_quality_repair_diagnostic_target_files(
        [
            "go.mod: malformed module path",
            "pyproject.toml: invalid project scripts table",
            "CMakeLists.txt: CMake configure failed",
        ]
    )

    assert targets == ["go.mod", "pyproject.toml", "CMakeLists.txt"]


def test_workspace_quality_plan_probe_reads_relevant_base_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export const value = 1;\n", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "main.js").write_text("compiled\n", encoding="utf-8")
    executor = _executor(tmp_path)
    captured: dict[str, Any] = {}

    def fake_query(query: Any) -> SimpleNamespace:
        captured["artifact_quality_errors"] = query.artifact_quality_errors
        captured["artifact_quality_issues"] = tuple(query.artifact_quality_issues)
        captured["base_files"] = dict(query.base_files)
        captured["metadata"] = dict(query.metadata)
        return SimpleNamespace(
            to_dict=lambda: {
                "schema_version": "director.repair_plan_probe_result.v1",
                "status": "coverage_matched_but_unplannable",
                "coverage_is_not_planning": True,
            }
        )

    monkeypatch.setattr(
        "polaris.cells.director.runtime.public.query_director_repair_plan_probe",
        fake_query,
    )

    result = executor._workspace_quality_repair_plan_probe_report(
        ["src/main.ts(1,1): error TS2322: Type 'string' is not assignable to type 'number'."]
    )

    assert result["status"] == "coverage_matched_but_unplannable"
    assert captured["base_files"] == {"src/main.ts": "export const value = 1;\n"}
    assert captured["metadata"]["coverage_is_not_planning"] is True
    assert captured["artifact_quality_errors"] == (
        "src/main.ts(1,1): error TS2322: Type 'string' is not assignable to type 'number'.",
    )
    assert captured["artifact_quality_issues"]
    typed_issue = captured["artifact_quality_issues"][0]
    assert typed_issue["code"]
    assert typed_issue["path"] == "src/main.ts"
    assert "TS2322" in typed_issue["message"]


def test_workspace_quality_repair_transports_nested_command_diagnostics_without_wrapper_gaps(
    tmp_path: Path,
) -> None:
    """Real verifier output must reach M10 as repairable diagnostics, not gate wrappers."""

    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (tmp_path / "package.json").write_text(
        '{"type":"module","scripts":{"build":"tsc","test":"node --test tests/verify.test.ts"},'
        '"devDependencies":{"typescript":"5.5.4"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"module":"ESNext","moduleResolution":"Bundler"}}\n',
        encoding="utf-8",
    )
    (src / "web.ts").write_text(
        "interface DrawingSurface { width: number; height: number }\n"
        "declare const ctx: CanvasRenderingContext2D;\n"
        "declare function render(surface: DrawingSurface): void;\n"
        "render(ctx);\n",
        encoding="utf-8",
    )
    (src / "verify.ts").write_text("export const verify = (): boolean => true;\n", encoding="utf-8")
    (tests / "verify.test.ts").write_text(
        'import { verify } from "../src/verify.js";\nvoid verify;\n',
        encoding="utf-8",
    )
    executor = _executor(tmp_path)
    results = [
        {
            "command": ["npm", "run", "build"],
            "phase": "check",
            "exit_code": 2,
            "passed": False,
            "stdout_tail": (
                "src/web.ts(4,8): error TS2345: Argument of type 'CanvasRenderingContext2D' "
                "is not assignable to parameter of type 'DrawingSurface'.\n"
                "  Type 'CanvasRenderingContext2D' is missing the following properties "
                "from type 'DrawingSurface': width, height"
            ),
            "stderr_tail": "",
        },
        {
            "command": ["npm", "test"],
            "phase": "check",
            "exit_code": 1,
            "passed": False,
            "stdout_tail": (
                "Error [ERR_MODULE_NOT_FOUND]: Cannot find module "
                f"'{src / 'verify.js'}' imported from {tests / 'verify.test.ts'}"
            ),
            "stderr_tail": "",
        },
    ]

    repair_errors = executor._workspace_quality_repair_errors(results)
    coverage = executor._workspace_quality_repair_coverage_report(repair_errors)
    probe = executor._workspace_quality_repair_plan_probe_report(repair_errors)

    assert len(repair_errors) == 2
    assert all("workspace validation command failed" not in error for error in repair_errors)
    assert any("TS2345" in error for error in repair_errors)
    assert any("ERR_MODULE_NOT_FOUND" in error for error in repair_errors)
    assert coverage["uncovered_diagnostic_count"] == 0
    assert probe["status"] != "coverage_gap_uncovered_diagnostics"
    matched_tools = {source_tool for item in coverage["items"] for source_tool in item["matched_source_tools"]}
    assert "deterministic_typescript_argument_shape_adapter_repair" in matched_tools
    assert "deterministic_typescript_local_js_import_repair" in matched_tools


def test_workspace_quality_repair_uses_one_tap_failure_island_without_duplicate_stream_rows(
    tmp_path: Path,
) -> None:
    """Marker-aware excerpt is authoritative repair input, not excerpt plus duplicated tails."""

    executor = _executor(tmp_path)
    failure = """not ok 2 - extracts dream keywords
  ---
  location: '/workspace/tests/product.test.js:46:1'
  error: |-
    assert.ok(keywords.includes('火焰'))
  expected: true
  actual: false
  operator: '=='
  ...
ok 3 - handles empty content
1..23
# pass 22
# fail 1"""
    results = [
        {
            "command": ["npm", "test"],
            "phase": "check",
            "exit_code": 1,
            "passed": False,
            "diagnostic_excerpt": failure,
            "stdout_tail": failure,
            "stderr_tail": failure,
        }
    ]

    repair_errors = executor._workspace_quality_repair_errors(results)

    assert len(repair_errors) == 1
    assert repair_errors[0].count("not ok 2") == 1
    assert "tests/product.test.js:46:1" in repair_errors[0]
    assert "火焰" in repair_errors[0]
    assert "# pass 22" not in repair_errors[0]


def test_workspace_quality_repair_maps_absolute_test_failure_to_imported_source_owner(
    tmp_path: Path,
) -> None:
    """Verifier location becomes safe relative scope and expands through test imports."""

    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "dream.js").write_text("export const extractDreamKeywords = () => [];\n", encoding="utf-8")
    test_path = tests / "product.test.js"
    test_path.write_text(
        "import { extractDreamKeywords } from '../src/dream.js';\nvoid extractDreamKeywords;\n",
        encoding="utf-8",
    )
    executor = _executor(tmp_path)
    diagnostic = f"""not ok 2 - extracts dream keywords
  ---
  location: '{test_path}:46:1'
  expected: true
  actual: false
  ..."""

    targets = executor._workspace_quality_repair_diagnostic_target_files([diagnostic])

    assert targets == ["tests/product.test.js", "src/dream.js"]


def test_workspace_quality_repair_ignores_declared_downstream_test_discovery_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A planned downstream test path must not hijack current-task repair routing."""

    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node --test tests/"}}\n',
        encoding="utf-8",
    )
    executor = _executor(tmp_path)
    monkeypatch.setattr(
        executor,
        "_workspace_quality_repair_target_files",
        lambda: ["package.json", "src/index.js", "tests/product.test.js"],
    )

    repair_errors = executor._workspace_quality_repair_errors([])

    assert not any("references missing local entrypoint 'tests/'" in error for error in repair_errors)


def test_workspace_quality_repair_keeps_unowned_missing_test_discovery_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing test paths absent from the PM contract remain hard diagnostics."""

    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node --test tests/"}}\n',
        encoding="utf-8",
    )
    executor = _executor(tmp_path)
    monkeypatch.setattr(
        executor,
        "_workspace_quality_repair_target_files",
        lambda: ["package.json", "src/index.js"],
    )

    repair_errors = executor._workspace_quality_repair_errors([])

    assert any("references missing local entrypoint 'tests/'" in error for error in repair_errors)


def test_quality_gate_failure_stage_does_not_add_qa_llm_warning_for_deterministic_blocker(
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    run = FactoryRun(
        id="run-deterministic-failure",
        config=FactoryConfig(name="demo"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-06-30T00:00:00Z",
    )

    result = executor._build_quality_gate_failure_stage(
        run,
        reason_code="workspace_quality_gate_failed",
        detail="npm test failed",
        context={},
    )

    assert result.status == "failed"
    report = json.loads(executor._artifact_path("runtime/qa/report.json").read_text(encoding="utf-8"))
    assert report["warnings"] == ["workspace_quality_gate_failed"]
    assert "qa_llm_judgement_unavailable" not in report["warnings"]


def test_workspace_validation_artifact_writes_run_ledger_command_evidence(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    run = FactoryRun(
        id="run-workspace-validation",
        config=FactoryConfig(name="demo"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-06-30T00:00:00Z",
    )
    executor._write_json_artifact(
        "tasks/plan.json",
        {
            "tasks": [
                {
                    "id": "TASK-1",
                    "goal": "Build the product",
                    "target_files": ["src/index.js"],
                    "acceptance": ["npm test passes"],
                }
            ]
        },
    )
    executor._write_json_artifact(
        f"runtime/state/blueprints/{run.id}.review.json",
        {
            "factory_run_id": run.id,
            "blueprints": [
                {
                    "task_id": "TASK-1",
                    "target_files": ["src/index.js"],
                    "verification_commands": ["npm test"],
                }
            ],
        },
    )

    artifact = executor._write_workspace_validation_artifact(
        run,
        {"project_id": "L1-ledger", "target_files": ["src/index.js"]},
        {
            "schema_version": "factory.workspace_quality_checks.v1",
            "factory_run_id": run.id,
            "passed": True,
            "commands": [
                {
                    "command": ["npm", "test"],
                    "passed": True,
                    "exit_code": 0,
                }
            ],
            "repair": {
                "attempted": True,
                "success": False,
                "residual_error_count": 1,
                "plan_probe_preaudit": {"status": "covered", "items": ["x" * 1_200_000]},
                "rounds": [
                    {
                        "round": 1,
                        "repair_summary": {"stage": "runtime_plan", "nested": "y" * 1_200_000},
                    }
                ],
            },
        },
    )

    projection = load_run_ledger_projection(tmp_path, run_id=run.id)
    assert artifact == "runtime/qa/workspace-validation.json"
    assert projection["gate_count"] == 1
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == []
    assert projection["evidence_modalities"]["command"]["ok"] == 1
    assert projection["integrity_ok"] is True
    gate = projection["gates"][0]
    assert gate["capability_ok"] is True
    assert gate["capability_issues"] == []
    ledger_path = tmp_path / "runtime" / "control_plane" / "ledger" / f"{run.id}.ndjson"
    ledger_event = json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[-1])
    repair_result = ledger_event["physical_evidence"]["repair_result"]
    assert repair_result["full_evidence_ref"] == "runtime/qa/workspace-validation.json"
    assert repair_result["full_evidence_bytes"] > 2_000_000
    assert len(json.dumps(ledger_event, ensure_ascii=False).encode("utf-8")) < 64_000


def test_workspace_validation_ledger_preserves_canonical_project_identity_on_qa_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-run QA retry must not replace the project id with the Factory run id."""

    executor = _executor(tmp_path)
    run = FactoryRun(
        id="factory-retry-1",
        config=FactoryConfig(name="demo"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-08-25T00:00:00Z",
        metadata={
            "factory_start_request": {
                "metadata": {
                    "factory_bench_project_id": "L3-23",
                    "factory_bench_requested_project_id": "L3-23",
                    "factory_bench_canonical_project_id": "L3-23",
                }
            }
        },
    )
    captured: dict[str, Any] = {}

    def _capture_persist(
        _workspace: Path,
        record: dict[str, Any],
        _gate: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured["record"] = dict(record)
        captured["kwargs"] = dict(kwargs)
        return {}

    monkeypatch.setattr(run_ledger_module, "persist_real_run_gate_ledger", _capture_persist)

    executor._write_workspace_validation_artifact(
        run,
        {"target_files": ["src/lib.rs"]},
        {
            "schema_version": "factory.workspace_quality_checks.v1",
            "factory_run_id": run.id,
            "passed": True,
            "effective_commands": [
                {"command": ["cargo", "test", "--quiet"], "passed": True, "exit_code": 0},
            ],
        },
    )

    assert captured["record"]["project_id"] == "L3-23"
    assert captured["kwargs"]["project_id"] == "L3-23"
    assert executor._workspace_validation_project_id(run, {"project_id": "explicit-project"}) == "explicit-project"


def test_workspace_validation_ledger_uses_terminal_verifier_epoch(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    run = FactoryRun(
        id="run-workspace-repaired",
        config=FactoryConfig(name="demo"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-08-13T00:00:00Z",
    )

    executor._write_workspace_validation_artifact(
        run,
        {"project_id": "L1-repaired", "target_files": ["src/main.py"]},
        {
            "schema_version": "factory.workspace_quality_checks.v1",
            "factory_run_id": run.id,
            "passed": True,
            "commands": [
                {"command": ["python", "-m", "unittest"], "passed": False, "exit_code": 1},
                {"command": ["python", "-m", "unittest"], "passed": True, "exit_code": 0},
            ],
            "effective_commands": [
                {"command": ["python", "-m", "unittest"], "passed": True, "exit_code": 0},
            ],
            "repair": {"attempted": True, "success": True, "convergence_stop_reason": "verifier_passed"},
        },
    )

    projection = load_run_ledger_projection(tmp_path, run_id=run.id)
    gate = projection["effective_gates"][0]
    assert gate["ok"] is True
    assert gate["failed_required_evidence_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == []
    assert projection["outcome_ok"] is True

    artifact = json.loads(executor._artifact_path("runtime/qa/workspace-validation.json").read_text(encoding="utf-8"))
    assert len(artifact["commands"]) == 2
    assert artifact["commands"][0]["passed"] is False


def test_workspace_validation_failure_detail_uses_terminal_verifier_epoch(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    artifact_path = executor._artifact_path("runtime/qa/workspace-validation.json")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(
            {
                "passed": False,
                "commands": [
                    {
                        "command": ["go", "test", "./..."],
                        "phase": "check",
                        "passed": False,
                        "stderr_tail": "engine/engine.go: undefined: restBand",
                    },
                    {
                        "command": ["go", "test", "./..."],
                        "phase": "check_after_repair",
                        "passed": False,
                        "stderr_tail": "TestStepClampsOnFloor: velocity remains downward",
                    },
                ],
                "effective_commands": [
                    {
                        "command": ["go", "test", "./..."],
                        "phase": "check_after_repair",
                        "passed": False,
                        "stderr_tail": "TestStepClampsOnFloor: velocity remains downward",
                    }
                ],
                "repair": {
                    "residual_errors": ["TestStepClampsOnFloor: velocity remains downward"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    detail = executor._workspace_quality_failure_detail("runtime/qa/workspace-validation.json")

    assert "TestStepClampsOnFloor" in detail
    assert "undefined: restBand" not in detail


def test_pm_plan_validation_contract_hygiene_defers_test_acceptance_to_validation_task() -> None:
    payload = {
        "tasks": [
            {
                "id": "TASK-1",
                "goal": "Create implementation modules",
                "scope": "src",
                "target_files": ["package.json", "tsconfig.json", "src/index.ts"],
                "steps": ["Create implementation files"],
                "acceptance": ["`npm run build` and `npm run test` pass for the implementation."],
                "acceptance_criteria": ["`npm run build` and `npm run test` pass for the implementation."],
            },
            {
                "id": "TASK-2",
                "goal": "Create verification assets",
                "scope": "tests",
                "target_files": ["src/verify.ts", "tests/verify.test.ts", "README.md"],
                "steps": ["Create test coverage"],
                "acceptance": ["`npm run test` executes real verification and returns PASS."],
                "depends_on": ["TASK-1"],
            },
        ]
    }

    tasks = OrchestrationStageExecutor._pm_plan_tasks_from_payload(payload)

    first_acceptance = " ".join(tasks[0]["acceptance"]).lower()
    first_acceptance_criteria = " ".join(tasks[0]["acceptance_criteria"]).lower()
    assert "npm run test" not in first_acceptance
    assert "npm run test" not in first_acceptance_criteria
    assert "build/start checks" in first_acceptance
    assert "build/start checks" in first_acceptance_criteria
    assert tasks[0]["metadata"]["validation_contract_hygiene"]["downstream_validation_targets"] == [
        "src/verify.ts",
        "tests/verify.test.ts",
    ]
    assert "npm run test" in " ".join(tasks[1]["acceptance"]).lower()
