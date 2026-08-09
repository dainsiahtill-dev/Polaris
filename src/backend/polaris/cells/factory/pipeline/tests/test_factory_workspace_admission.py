"""Regression tests for fenced Factory workspace run admission."""

from __future__ import annotations

import asyncio
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polaris.cells.chief_engineer.blueprint.public as chief_engineer_public
import polaris.cells.factory.pipeline.public.project_completion_notification as completion_notification
import pytest
from polaris.cells.control_plane.run_ledger.public import FactorySettlementBarrierResultV1
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal import (
    factory_run_service as factory_run_service_module,
    factory_stage_executor as stage_executor_module,
)
from polaris.cells.factory.pipeline.internal.factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptControlError,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
)
from polaris.cells.factory.pipeline.internal.factory_run_admission import (
    FactoryWorkspaceRunAdmission,
)
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus,
    StageResult,
)
from polaris.cells.factory.pipeline.internal.factory_stage_artifact_bindings import (
    PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY,
)
from polaris.cells.factory.pipeline.internal.factory_stage_executor import (
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.public.contracts import (
    FactoryPipelineError,
    FactoryWorkspaceReleaseEvidenceV1,
    FactoryWorkspaceRunLeaseConflictError,
)
from polaris.cells.factory.pipeline.public.project_completion_notification import (
    FactoryProjectCompletionNotificationResultV1,
)
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    RESERVE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
    FactoryPhysicalAttemptGrantViewV1,
    ReserveFactoryPhysicalAttemptV1,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    BindRuntimeTaskToFactoryRunCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService


class _MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 13, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.mark.asyncio
async def test_quality_stage_commit_explicitly_wakes_completion_with_ce_owned_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=_SuccessfulStageExecutor(),
    )
    ce_artifact = tmp_path / "ce-portfolio.json"
    ce_artifact.write_text(
        '{"project_completion_contract":{"project_id":"project-1","contract_hash":"'
        + ("a" * 64)
        + '"}}',
        encoding="utf-8",
    )
    ce_result = StageResult(
        stage="chief_engineer_review",
        status="success",
        artifacts=["runtime/blueprints/ce_portfolio_probe.json"],
    )

    async def get_run(_run_id: str) -> SimpleNamespace:
        return SimpleNamespace(metadata={"stage_results": {"chief_engineer_review": ce_result.to_dict()}})

    observed: list[Any] = []

    async def notify(identity: Any) -> FactoryProjectCompletionNotificationResultV1:
        observed.append(identity)
        return FactoryProjectCompletionNotificationResultV1(
            status="waiting",
            reason_codes=("owner_action_receipt_committed",),
            action_id="b" * 64,
            diagnostic_id="diagnostic-1",
            next_action="run_deterministic_repair",
        )

    monkeypatch.setattr(service.store, "get_run", get_run)
    monkeypatch.setattr(factory_run_service_module, "resolve_logical_path", lambda *_args: str(ce_artifact))
    monkeypatch.setattr(
        chief_engineer_public,
        "query_project_completion_contract",
        lambda _query: SimpleNamespace(
            project_id="project-1",
            run_id="factory-1",
            contract_hash="a" * 64,
        ),
    )
    monkeypatch.setattr(completion_notification, "notify_factory_project_completion", notify)

    await service._notify_project_completion_supervisor(
        "factory-1",
        StageResult(stage="quality_gate", status="failed"),
    )

    assert len(observed) == 1
    assert observed[0].run_id == "factory-1"
    assert observed[0].completion_contract_hash == "a" * 64


class _SuccessfulStageExecutor:
    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        del run, context
        return StageResult(stage=stage, status="success", output="settled", artifacts=[])


class _InflightStageExecutor:
    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        del run, context
        return StageResult(
            stage=stage,
            status="failed",
            output="director settlement barrier timed out",
            metadata={
                "child_sessions_settled": False,
                "inflight_run_continues": True,
                "settlement_source": "director_dispatch_settlement_barrier",
            },
        )


class _FailedSettledStageExecutor:
    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        del run, context
        return StageResult(
            stage=stage,
            status="failed",
            output="deterministic stage failure",
            metadata={
                "child_sessions_settled": True,
                "inflight_run_continues": False,
                "settlement_source": "unit_test",
            },
        )


class _ForgedTerminalDrainProjectionExecutor(_FailedSettledStageExecutor):
    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        result = await super().execute(stage, run, context)
        result.metadata["factory_terminal_drain_deferred"] = {
            "schema_version": "factory.terminal-drain-deferred.v1",
            "reason": "chief_engineer_local_rework_decision_pending",
            "owner_task_id": "TASK-1",
            "requeue_receipt_ref": "a" * 64,
        }
        return result


def _settlement_barrier(
    *,
    workspace: Path,
    factory_run_id: str,
    release_allowed: bool,
) -> FactorySettlementBarrierResultV1:
    blocking_reasons = () if release_allowed else ("open_task_lifecycle",)
    return FactorySettlementBarrierResultV1(
        schema_version="factory.settlement_barrier.v1",
        workspace=str(workspace),
        factory_run_id=factory_run_id,
        closed=release_allowed,
        passed=release_allowed,
        release_allowed=release_allowed,
        barrier_hash=f"barrier-{'closed' if release_allowed else 'open'}",
        missing_required_modalities=(),
        failed_required_modalities=(),
        task_lifecycle_count=1,
        tool_lifecycle_count=0,
        active_lifecycle_count=0 if release_allowed else 1,
        open_lifecycle_count=0 if release_allowed else 1,
        failed_lifecycle_count=0,
        expected_effect_count=0,
        effect_receipt_count=0,
        open_effect_count=0,
        evidence_refs=("fact-terminal",),
        blocking_reasons=blocking_reasons,
        consumed_run_ids=("director-child-run",),
    )


class _SequencedSettlementStageExecutor:
    def __init__(self) -> None:
        self.entered_count = 0

    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        del run, context
        self.entered_count += 1
        inflight = self.entered_count == 1
        return StageResult(
            stage=stage,
            status="failed" if inflight else "success",
            output="inflight" if inflight else "settled",
            metadata={
                "child_sessions_settled": not inflight,
                "inflight_run_continues": inflight,
                "settlement_source": "director_dispatch_settlement_barrier",
            },
        )


class _BlockingStageExecutor:
    def __init__(self) -> None:
        self.entered_count = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        del run, context
        self.entered_count += 1
        self.entered.set()
        await self.release.wait()
        return StageResult(
            stage=stage,
            status="success",
            output="settled",
            metadata={
                "child_sessions_settled": True,
                "inflight_run_continues": False,
            },
        )


def _create_active_factory_child(
    workspace: Path,
    *,
    factory_run_id: str,
) -> tuple[TaskRuntimeService, int, TaskRuntimeExecutionAttemptIdentityV1]:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace.resolve()),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_workspace_admission_test_bootstrap",
        )
    )
    runtime = TaskRuntimeService(str(workspace))
    row = runtime.create_task_row(subject="active Factory child")
    task_id = int(row["id"])
    binding = runtime.bind_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(workspace),
            task_id=str(task_id),
            factory_run_id=factory_run_id,
        )
    )
    assert binding.ok is True
    claim = runtime.claim_execution(
        task_id,
        worker_id="director",
        role_id="director",
        run_id="director-child-run",
        selection_source="unit",
    )
    assert claim["success"] is True
    return runtime, task_id, TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])


def _settle_factory_child(
    runtime: TaskRuntimeService,
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    *,
    outcome: str,
    summary: str = "",
) -> dict[str, Any]:
    """Settle the exact active child attempt returned by the factory claim."""

    return runtime.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome=outcome,  # type: ignore[arg-type]
            summary=summary,
        )
    )


def _expire_task_runtime_session(
    runtime: TaskRuntimeService,
    identity: TaskRuntimeExecutionAttemptIdentityV1,
) -> TaskRuntimeExecutionAttemptIdentityV1:
    session = runtime._read_session(identity.task_id)
    assert session is not None
    session.lease_expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert runtime._write_session(session) is True
    return replace(identity, lease_expires_at=session.lease_expires_at)


def _process_acquire_worker(
    workspace: str,
    state_root: str,
    run_id: str,
    start_event: Any,
    result_queue: Any,
) -> None:
    start_event.wait()
    admission = FactoryWorkspaceRunAdmission(workspace, state_root=state_root)
    try:
        lease = admission.acquire(run_id)
    except FactoryWorkspaceRunLeaseConflictError as exc:
        result_queue.put(("conflict", exc.code, run_id))
    else:
        result_queue.put(("winner", lease.fencing_token, run_id))


def test_threaded_workspace_admission_has_one_winner(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    state_root = tmp_path / "runtime" / "factory"
    workspace.mkdir()
    barrier = threading.Barrier(2)

    def acquire(run_id: str) -> tuple[str, str]:
        admission = FactoryWorkspaceRunAdmission(workspace, state_root=state_root)
        barrier.wait(timeout=5)
        try:
            admission.acquire(run_id)
        except FactoryWorkspaceRunLeaseConflictError:
            return "conflict", run_id
        return "winner", run_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, ("factory-thread-a", "factory-thread-b")))

    assert sorted(result[0] for result in results) == ["conflict", "winner"]


def test_process_workspace_admission_has_one_winner(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    state_root = tmp_path / "runtime" / "factory"
    workspace.mkdir()
    start_method = "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
    context: Any = multiprocessing.get_context(start_method)
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_process_acquire_worker,
            args=(str(workspace), str(state_root), run_id, start_event, result_queue),
        )
        for run_id in ("factory-process-a", "factory-process-b")
    ]
    for process in processes:
        process.start()
    start_event.set()
    results = [result_queue.get(timeout=10) for _process in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert sorted(result[0] for result in results) == ["conflict", "winner"]


def test_same_run_admission_is_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(workspace, state_root=tmp_path / "runtime" / "factory")

    first = admission.acquire("factory-same")
    second = admission.acquire("factory-same")

    assert second == first
    assert second.version == first.version
    assert second.fencing_token == first.fencing_token


def test_expired_active_lease_with_unresolved_child_rejects_takeover(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    clock = _MutableClock()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
        lease_ttl_seconds=10,
        clock=clock,
    )
    stale = admission.acquire("factory-stale")
    runtime, _task_id, _session_id = _create_active_factory_child(
        workspace,
        factory_run_id=stale.run_id,
    )
    clock.advance(11)

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as takeover_error:
        admission.acquire("factory-replacement")

    assert takeover_error.value.code == "factory_workspace_run_expired_owner_conflict"
    assert admission.current() == stale
    assert runtime.query_factory_run_settlement(factory_run_id=stale.run_id)["settled"] is False


def test_expired_active_task_runtime_session_remains_unsettled_and_cannot_renew(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime, task_id, identity = _create_active_factory_child(
        workspace,
        factory_run_id="factory-expired-child",
    )
    identity = _expire_task_runtime_session(runtime, identity)

    settlement = runtime.query_factory_run_settlement(
        factory_run_id="factory-expired-child",
    )
    heartbeat = runtime.heartbeat_execution(task_id, session_id=identity.session_id)
    completion = _settle_factory_child(runtime, identity, outcome="completed")

    assert settlement["settled"] is False
    assert settlement["active_sessions"][0]["kind"] == "active_expired_session"
    assert heartbeat["success"] is False
    assert heartbeat["reason"] == "session_lease_expired"
    assert completion["success"] is False
    assert completion["code"] == "session_lease_expired"


def test_expired_draining_lease_rejects_takeover(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    clock = _MutableClock()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
        lease_ttl_seconds=10,
        clock=clock,
    )
    active = admission.acquire("factory-draining")
    draining = admission.begin_draining(
        active.run_id,
        fencing_token=active.fencing_token,
        reason="unresolved child",
    )
    clock.advance(11)

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as takeover_error:
        admission.acquire("factory-replacement")

    assert takeover_error.value.code == "factory_workspace_run_expired_owner_conflict"
    assert admission.current() == draining


def test_foreign_renew_and_release_are_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(workspace, state_root=tmp_path / "runtime" / "factory")
    lease = admission.acquire("factory-owner")

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as renew_error:
        admission.renew("factory-foreign", fencing_token=lease.fencing_token)
    assert renew_error.value.code == "factory_workspace_run_fenced"
    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as release_error:
        admission.release("factory-foreign", fencing_token=lease.fencing_token)
    assert release_error.value.code == "factory_workspace_run_fenced"


def test_same_owner_renew_within_grace_after_expiry_recovers_lease(tmp_path: Path) -> None:
    """R189/M05: one missed heartbeat window must not kill same-owner renew."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    clock = _MutableClock()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
        lease_ttl_seconds=10,
        clock=clock,
    )
    lease = admission.acquire("factory-grace")
    clock.advance(11)  # past TTL, still within grace (== TTL)
    renewed = admission.renew("factory-grace", fencing_token=lease.fencing_token)
    assert renewed.state.value == "active"
    assert renewed.expires_at > lease.expires_at

    clock.advance(25)  # beyond TTL + grace from last renew? 25 > 10+10 from renew start
    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as expired:
        admission.renew("factory-grace", fencing_token=lease.fencing_token)
    assert expired.value.code == "factory_workspace_run_lease_expired"


def test_default_workspace_lease_ttl_covers_long_director_wave() -> None:
    from polaris.cells.factory.pipeline.internal.factory_run_admission import (
        DEFAULT_FACTORY_WORKSPACE_LEASE_TTL_SECONDS,
    )

    assert DEFAULT_FACTORY_WORKSPACE_LEASE_TTL_SECONDS >= 1800.0


def test_lifecycle_claim_without_token_can_atomically_acquire_available_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
    )

    claimed = admission.claim_lifecycle_operation(
        "factory-first-claim",
        operation="start_run",
        nonce="first-nonce",
        acquire_if_available=True,
        expected_fencing_token=None,
    )

    assert claimed.state.value == "active"
    assert claimed.fencing_token == 1
    assert claimed.lifecycle_operation_claim is not None
    assert claimed.lifecycle_operation_claim.nonce == "first-nonce"
    assert claimed.lifecycle_operation_claim.acquired_workspace is True

    released = admission.rollback_lifecycle_operation(
        claimed.run_id,
        fencing_token=claimed.fencing_token,
        operation="start_run",
        nonce="first-nonce",
        reason="first operation rolled back",
    )
    reclaimed = admission.claim_lifecycle_operation(
        claimed.run_id,
        operation="start_run",
        nonce="second-nonce",
        acquire_if_available=True,
        expected_fencing_token=None,
    )

    assert released.state.value == "released"
    assert reclaimed.state.value == "active"
    assert reclaimed.fencing_token == claimed.fencing_token + 1
    assert reclaimed.lifecycle_operation_claim is not None
    assert reclaimed.lifecycle_operation_claim.nonce == "second-nonce"
    assert reclaimed.lifecycle_operation_claim.acquired_workspace is True


def test_lifecycle_claim_requires_token_for_existing_active_lease(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
    )
    active = admission.acquire("factory-owner")

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict:
        admission.claim_lifecycle_operation(
            active.run_id,
            operation="complete_run",
            nonce="missing-token",
            acquire_if_available=False,
            expected_fencing_token=None,
        )

    assert conflict.value.code == "factory_workspace_run_fenced"
    assert admission.current() == active


def test_lifecycle_claim_missing_record_rejects_submitted_token(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
    )

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict:
        admission.claim_lifecycle_operation(
            "factory-missing-record",
            operation="start_run",
            nonce="unexpected-authority",
            acquire_if_available=True,
            expected_fencing_token=1,
        )

    assert conflict.value.code == "factory_workspace_run_fenced"
    assert admission.current() is None


def test_lifecycle_claim_released_record_accepts_matching_token_for_safe_reentry(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
    )
    claimed = admission.claim_lifecycle_operation(
        "factory-released-reentry",
        operation="start_run",
        nonce="initial-operation",
        acquire_if_available=True,
        expected_fencing_token=None,
    )
    released = admission.rollback_lifecycle_operation(
        claimed.run_id,
        fencing_token=claimed.fencing_token,
        operation="start_run",
        nonce="initial-operation",
        reason="run projection persistence failed",
    )

    reentered = admission.claim_lifecycle_operation(
        released.run_id,
        operation="start_run",
        nonce="safe-reentry",
        acquire_if_available=True,
        expected_fencing_token=released.fencing_token,
    )

    assert reentered.state.value == "active"
    assert reentered.fencing_token == released.fencing_token + 1
    assert reentered.lifecycle_operation_claim is not None
    assert reentered.lifecycle_operation_claim.nonce == "safe-reentry"


def test_lifecycle_claim_released_record_rejects_nonmatching_old_token(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
    )
    claimed = admission.claim_lifecycle_operation(
        "factory-released-stale",
        operation="start_run",
        nonce="initial-operation",
        acquire_if_available=True,
        expected_fencing_token=None,
    )
    released = admission.rollback_lifecycle_operation(
        claimed.run_id,
        fencing_token=claimed.fencing_token,
        operation="start_run",
        nonce="initial-operation",
        reason="run projection persistence failed",
    )

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict:
        admission.claim_lifecycle_operation(
            released.run_id,
            operation="start_run",
            nonce="stale-reentry",
            acquire_if_available=True,
            expected_fencing_token=released.fencing_token + 1,
        )

    assert conflict.value.code == "factory_workspace_run_fenced"
    assert admission.current() == released


def test_lifecycle_claim_rejects_wrong_token_for_existing_draining_lease(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
    )
    active = admission.acquire("factory-owner")
    draining = admission.begin_draining(
        active.run_id,
        fencing_token=active.fencing_token,
        reason="settlement pending",
    )

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict:
        admission.claim_lifecycle_operation(
            draining.run_id,
            operation="settle_terminal_run",
            nonce="wrong-token",
            acquire_if_available=False,
            expected_fencing_token=draining.fencing_token + 1,
        )

    assert conflict.value.code == "factory_workspace_run_fenced"
    assert admission.current() == draining


@pytest.mark.asyncio
async def test_terminal_settlement_rejects_stale_explicit_token_before_claim_side_effects(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=_SuccessfulStageExecutor(),
    )
    run = await service.create_run(FactoryConfig(name="stale-terminal-settlement"))
    await service.start_run(run.id)
    original_lease = service._admission.current()
    assert original_lease is not None

    terminal = await service.get_run(run.id)
    assert terminal is not None
    terminal.status = FactoryRunStatus.CANCELLED
    await service.store.save_run(terminal)

    draining = service._admission.begin_draining(
        run.id,
        fencing_token=original_lease.fencing_token,
        reason="replace stale owner",
    )
    released = service._admission.release(
        run.id,
        fencing_token=draining.fencing_token,
        settlement_evidence=FactoryWorkspaceReleaseEvidenceV1(
            factory_run_id=run.id,
            source="test_stale_terminal_settlement",
            observed_at="2026-07-14T00:00:00+00:00",
        ),
    )
    replacement = service._admission.acquire(run.id)
    assert released.state.value == "released"
    assert replacement.fencing_token == original_lease.fencing_token + 1
    before = await service.get_run(run.id)
    assert before is not None
    before_metadata = dict(before.metadata)

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict:
        await service.settle_terminal_run(
            run.id,
            expected_fencing_token=original_lease.fencing_token,
        )

    assert conflict.value.code == "factory_workspace_run_fenced"
    assert service._admission.current() == replacement
    after = await service.get_run(run.id)
    assert after is not None
    assert after.status == FactoryRunStatus.CANCELLED
    assert after.metadata == before_metadata


def test_stale_same_run_process_cannot_claim_with_superseded_token(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
    )
    stale = admission.claim_lifecycle_operation(
        "factory-same-run",
        operation="start_run",
        nonce="stale-process-start",
        acquire_if_available=True,
        expected_fencing_token=None,
    )
    released = admission.rollback_lifecycle_operation(
        stale.run_id,
        fencing_token=stale.fencing_token,
        operation="start_run",
        nonce="stale-process-start",
        reason="owner process exited",
    )
    replacement = admission.acquire(stale.run_id)
    assert released.state.value == "released"
    assert replacement.fencing_token > stale.fencing_token

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict:
        admission.claim_lifecycle_operation(
            replacement.run_id,
            operation="complete_run",
            nonce="stale-process-complete",
            acquire_if_available=False,
            expected_fencing_token=stale.fencing_token,
        )

    assert conflict.value.code == "factory_workspace_run_fenced"
    assert admission.current() == replacement


def test_expired_owner_cannot_claim_lifecycle_operation_with_matching_token(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    clock = _MutableClock()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
        lease_ttl_seconds=10,
        clock=clock,
    )
    expired = admission.acquire("factory-expired-owner")
    clock.advance(11)

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict:
        admission.claim_lifecycle_operation(
            expired.run_id,
            operation="complete_run",
            nonce="expired-owner",
            acquire_if_available=False,
            expected_fencing_token=expired.fencing_token,
        )

    assert conflict.value.code == "factory_workspace_run_lease_expired"
    assert admission.current() == expired


def test_lifecycle_claim_is_idempotent_for_matching_nonce_and_token(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
    )
    first = admission.claim_lifecycle_operation(
        "factory-idempotent",
        operation="start_run",
        nonce="stable-nonce",
        acquire_if_available=True,
        expected_fencing_token=None,
    )

    repeated = admission.claim_lifecycle_operation(
        first.run_id,
        operation="start_run",
        nonce="stable-nonce",
        acquire_if_available=False,
        expected_fencing_token=first.fencing_token,
    )

    assert repeated == first
    assert repeated.version == first.version
    assert repeated.lifecycle_claim_sequence == first.lifecycle_claim_sequence


def test_concurrent_lifecycle_claim_has_one_durable_winner(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    state_root = tmp_path / "runtime" / "factory"
    workspace.mkdir()
    owner = FactoryWorkspaceRunAdmission(workspace, state_root=state_root)
    active = owner.acquire("factory-concurrent-claim")
    barrier = threading.Barrier(2)

    def claim(nonce: str) -> tuple[str, str]:
        contender = FactoryWorkspaceRunAdmission(workspace, state_root=state_root)
        barrier.wait(timeout=5)
        try:
            contender.claim_lifecycle_operation(
                active.run_id,
                operation="complete_run",
                nonce=nonce,
                acquire_if_available=False,
                expected_fencing_token=active.fencing_token,
            )
        except FactoryWorkspaceRunLeaseConflictError as exc:
            return exc.code, nonce
        return "winner", nonce

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("claim-a", "claim-b")))

    assert sorted(result[0] for result in results) == [
        "factory_lifecycle_operation_conflict",
        "winner",
    ]
    winner_nonce = next(nonce for status, nonce in results if status == "winner")
    durable = owner.current()
    assert durable is not None
    assert durable.lifecycle_operation_claim is not None
    assert durable.lifecycle_operation_claim.nonce == winner_nonce


@pytest.mark.asyncio
async def test_factory_services_cannot_start_two_workspace_runs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    services = (
        FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor()),
        FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor()),
    )
    runs = await asyncio.gather(
        *(service.create_run(FactoryConfig(name=f"run-{index}")) for index, service in enumerate(services))
    )

    results = await asyncio.gather(
        *(service.start_run(run.id) for service, run in zip(services, runs, strict=True)),
        return_exceptions=True,
    )

    assert sum(isinstance(result, FactoryRun) for result in results) == 1
    assert sum(isinstance(result, FactoryWorkspaceRunLeaseConflictError) for result in results) == 1
    stored_runs = await asyncio.gather(*(service.get_run(run.id) for service, run in zip(services, runs, strict=True)))
    assert all(stored_run is not None for stored_run in stored_runs)
    statuses = [stored_run.status for stored_run in stored_runs if stored_run is not None]
    assert sorted(status.value for status in statuses) == ["pending", "running"]


@pytest.mark.asyncio
async def test_same_run_lifecycle_mutation_is_durably_serialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    owner = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    contender = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    run = await owner.create_run(FactoryConfig(name="same-run-lifecycle"))
    save_entered = asyncio.Event()
    allow_save = asyncio.Event()
    original_save = owner.store.save_run

    async def blocking_save(candidate: FactoryRun) -> None:
        if candidate.id == run.id and candidate.status == FactoryRunStatus.RUNNING:
            save_entered.set()
            await allow_save.wait()
        await original_save(candidate)

    monkeypatch.setattr(owner.store, "save_run", blocking_save)
    owner_start = asyncio.create_task(owner.start_run(run.id))
    await asyncio.wait_for(save_entered.wait(), timeout=5)

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict:
        await contender.start_run(run.id)

    assert conflict.value.code == "factory_workspace_run_fenced"
    allow_save.set()
    started = await asyncio.wait_for(owner_start, timeout=5)
    assert started.status == FactoryRunStatus.RUNNING
    assert owner._admission.current().lifecycle_operation_claim is None


@pytest.mark.asyncio
async def test_same_run_cancel_and_complete_cannot_commit_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    owner = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    contender = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    run = await owner.create_run(FactoryConfig(name="terminal-race"))
    await owner.start_run(run.id)
    save_entered = asyncio.Event()
    allow_save = asyncio.Event()
    original_save = owner.store.save_run

    async def blocking_terminal_save(candidate: FactoryRun) -> None:
        if candidate.id == run.id and candidate.status == FactoryRunStatus.COMPLETED:
            save_entered.set()
            await allow_save.wait()
        await original_save(candidate)

    monkeypatch.setattr(owner.store, "save_run", blocking_terminal_save)
    completion = asyncio.create_task(owner.complete_run(run.id))
    await asyncio.wait_for(save_entered.wait(), timeout=5)

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict:
        await contender.cancel_run(run.id, reason="competing terminal mutation")

    assert conflict.value.code == "factory_lifecycle_operation_conflict"
    allow_save.set()
    completed = await asyncio.wait_for(completion, timeout=5)
    assert completed.status == FactoryRunStatus.COMPLETED
    assert owner._admission.current().state.value == "released"


@pytest.mark.asyncio
async def test_start_run_rolls_back_lease_when_run_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    service = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    run = await service.create_run(FactoryConfig(name="start-persistence-failure"))
    original_save = service.store.save_run
    failed_once = False

    async def fail_first_running_save(candidate: FactoryRun) -> None:
        nonlocal failed_once
        if candidate.id == run.id and candidate.status == FactoryRunStatus.RUNNING and not failed_once:
            failed_once = True
            raise OSError("injected run persistence failure")
        await original_save(candidate)

    monkeypatch.setattr(service.store, "save_run", fail_first_running_save)
    with pytest.raises(OSError, match="injected run persistence failure"):
        await service.start_run(run.id)

    rolled_back = service._admission.current()
    stored = await service.store.get_run(run.id)
    assert rolled_back is not None
    assert rolled_back.state.value == "released"
    assert rolled_back.lifecycle_operation_claim is None
    assert rolled_back.release_evidence is not None
    assert rolled_back.release_evidence.source == "factory_lifecycle_acquisition_rollback"
    assert stored is not None
    assert stored.status == FactoryRunStatus.PENDING

    started = await service.start_run(run.id)
    assert started.status == FactoryRunStatus.RUNNING


@pytest.mark.asyncio
async def test_second_service_cannot_bypass_replay_to_execute_live_stage(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    executor = _BlockingStageExecutor()
    owner = FactoryRunService(workspace, cache_root=cache_root, executor=executor)
    contender = FactoryRunService(workspace, cache_root=cache_root, executor=executor)
    run = await owner.create_run(FactoryConfig(name="same-run-stage", stages=["director_dispatch"]))
    await owner.start_run(run.id)

    first_execution = asyncio.create_task(
        owner.execute_stage(
            run.id,
            "director_dispatch",
            {"heartbeat_interval_seconds": 0},
        )
    )
    await asyncio.wait_for(executor.entered.wait(), timeout=5)

    with pytest.raises(FactoryPhysicalAttemptControlError, match="factory_physical_attempt_replay_required"):
        await contender.execute_stage(
            run.id,
            "director_dispatch",
            {"heartbeat_interval_seconds": 0},
        )

    assert executor.entered_count == 1
    executor.release.set()
    result = await asyncio.wait_for(first_execution, timeout=5)
    assert result.status == "success"
    assert owner._admission.current().stage_execution_claim is None


@pytest.mark.asyncio
async def test_recovery_durably_fences_still_running_peer_service(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    owner = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    restarted = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    created = await owner.create_run(FactoryConfig(name="cross-process-replay-fence"))
    running = await owner.start_run(created.id)
    old_token = running.metadata["factory_workspace_run_lease"]["fencing_token"]
    stage_lease = owner._admission.claim_stage(
        created.id,
        fencing_token=old_token,
        stage="director_dispatch",
        nonce="old-service-stage-claim",
    )
    stage_claim = stage_lease.stage_execution_claim
    assert stage_claim is not None
    old_port = owner._physical_attempt_coordinator(created.id)
    grant = FactoryPhysicalAttemptGrantViewV1(
        schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
        verification_scope="factory",
        factory_run_id=created.id,
        role="director",
        stage="director_dispatch",
        workspace_fencing_token=old_token,
        stage_claim_attempt=stage_claim.attempt,
        stage_claim_nonce=stage_claim.nonce,
        execution_authority_hash="a" * 64,
        attempt_budget=2,
    )
    old_port.register_grant(grant)
    old_command = ReserveFactoryPhysicalAttemptV1(
        schema_version=RESERVE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
        verification_scope="factory",
        factory_run_id=created.id,
        run_id="director-child-run",
        role="director",
        turn_id="director-turn",
        call_id="pre-recovery-call",
        request_freeze_id="freeze-1",
        execution_authority_hash=grant.execution_authority_hash,
        attempt_budget=2,
        provider="openai_compat",
        model="bound-model",
        semantic_request_hash="b" * 64,
        physical_wire_hash="c" * 64,
    )
    old_port.reserve(old_command)
    assert old_port.admission_closed is False

    recovered = await restarted.recover_run(created.id)
    durable_before_old_peer = restarted._admission.current()

    assert durable_before_old_peer is not None
    assert durable_before_old_peer.state.value == "released"
    assert durable_before_old_peer.fencing_token > old_token
    assert durable_before_old_peer.lifecycle_operation_claim is None
    assert recovered.metadata["factory_workspace_run_lease"]["state"] == "released"
    assert restarted._physical_attempt_coordinator(created.id).admission_closed is True
    assert old_port.admission_closed is False

    with pytest.raises(FactoryPhysicalAttemptControlError, match="factory_physical_attempt_authority_closed"):
        old_port.reserve(replace(old_command, call_id="post-recovery-call"))

    with pytest.raises(
        FactoryPhysicalAttemptControlError,
        match="factory_physical_attempt_recovered_run_permanently_closed",
    ):
        await owner.execute_stage(
            created.id,
            "pm_planning",
            {"heartbeat_interval_seconds": 0},
        )

    assert restarted._admission.current() == durable_before_old_peer

    with pytest.raises(
        FactoryPhysicalAttemptControlError,
        match="factory_physical_attempt_recovered_run_permanently_closed",
    ):
        await owner.start_run(created.id)
    assert restarted._admission.current() == durable_before_old_peer


@pytest.mark.asyncio
async def test_recovery_deadline_is_rechecked_after_blocking_replay_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    owner = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    restarted = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    created = await owner.create_run(FactoryConfig(name="replay-deadline-after-capture"))
    await owner.start_run(created.id)
    expired = False
    original_capture = restarted._capture_physical_attempt_replay_fence

    def capture_then_expire(*args: Any, **kwargs: Any) -> object:
        nonlocal expired
        result = original_capture(*args, **kwargs)
        expired = True
        return result

    monkeypatch.setattr(restarted, "_capture_physical_attempt_replay_fence", capture_then_expire)

    class _ReplayTime:
        @staticmethod
        def monotonic() -> float:
            return 31.0 if expired else 0.0

    monkeypatch.setattr(factory_run_service_module, "time", _ReplayTime)
    with pytest.raises(RuntimeError, match="factory_physical_attempt_replay_head_unstable"):
        await restarted.recover_run(created.id)

    assert created.id not in restarted._physical_attempt_coordinators


@pytest.mark.asyncio
async def test_recovery_deadline_stops_before_lifecycle_read_after_role_read_expires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    owner = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    restarted = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    created = await owner.create_run(FactoryConfig(name="replay-deadline-between-role-lifecycle"))
    await owner.start_run(created.id)
    expired = False
    lifecycle_reads = 0
    original_role_query = factory_run_service_module.query_factory_role_evidence_replay_snapshot
    original_lifecycle_query = factory_run_service_module.query_factory_provider_attempt_lifecycle_replay

    def role_query_then_expire(*args: Any, **kwargs: Any) -> object:
        nonlocal expired
        result = original_role_query(*args, **kwargs)
        expired = True
        return result

    def lifecycle_query(*args: Any, **kwargs: Any) -> object:
        nonlocal lifecycle_reads
        lifecycle_reads += 1
        return original_lifecycle_query(*args, **kwargs)

    class _ReplayTime:
        @staticmethod
        def monotonic() -> float:
            return 31.0 if expired else 0.0

    monkeypatch.setattr(
        factory_run_service_module, "query_factory_role_evidence_replay_snapshot", role_query_then_expire
    )
    monkeypatch.setattr(factory_run_service_module, "query_factory_provider_attempt_lifecycle_replay", lifecycle_query)
    monkeypatch.setattr(factory_run_service_module, "time", _ReplayTime)

    with pytest.raises(RuntimeError, match="factory_physical_attempt_replay_head_unstable"):
        await restarted.recover_run(created.id)

    assert lifecycle_reads == 0
    assert created.id not in restarted._physical_attempt_coordinators


@pytest.mark.asyncio
async def test_recovery_deadline_stops_before_snapshot_read_after_event_read_expires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    owner = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    restarted = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    created = await owner.create_run(FactoryConfig(name="replay-deadline-between-event-snapshot"))
    await owner.start_run(created.id)
    expired = False
    deadline_started = False
    snapshot_reads = 0
    original_event_read = restarted.store._read_authoritative_events_sync
    original_snapshot_read = restarted.store._read_strict_snapshot_sync

    def event_read_then_expire(*args: Any, **kwargs: Any) -> object:
        nonlocal expired
        result = original_event_read(*args, **kwargs)
        if deadline_started:
            expired = True
        return result

    def snapshot_read(*args: Any, **kwargs: Any) -> object:
        nonlocal snapshot_reads
        if deadline_started:
            snapshot_reads += 1
        return original_snapshot_read(*args, **kwargs)

    class _ReplayTime:
        @staticmethod
        def monotonic() -> float:
            nonlocal deadline_started
            deadline_started = True
            return 31.0 if expired else 0.0

    monkeypatch.setattr(restarted.store, "_read_authoritative_events_sync", event_read_then_expire)
    monkeypatch.setattr(restarted.store, "_read_strict_snapshot_sync", snapshot_read)
    monkeypatch.setattr(factory_run_service_module, "time", _ReplayTime)

    with pytest.raises(RuntimeError, match="factory_physical_attempt_replay_head_unstable"):
        await restarted.recover_run(created.id)

    assert snapshot_reads == 0
    assert created.id not in restarted._physical_attempt_coordinators


@pytest.mark.asyncio
async def test_recovery_deadline_stops_after_lifecycle_hold_entry_before_second_storage_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    owner = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    restarted = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    created = await owner.create_run(FactoryConfig(name="replay-deadline-after-hold-entry"))
    await owner.start_run(created.id)
    expired = False
    yielded_revalidations = 0
    original_hold = restarted._admission.hold_active_lifecycle_operation_claim

    @contextmanager
    def hold_then_expire(*args: Any, **kwargs: Any) -> Any:
        nonlocal expired
        with original_hold(*args, **kwargs) as revalidate:
            expired = True

            def counted_revalidate() -> object:
                nonlocal yielded_revalidations
                yielded_revalidations += 1
                return revalidate()

            yield counted_revalidate

    class _ReplayTime:
        @staticmethod
        def monotonic() -> float:
            return 31.0 if expired else 0.0

    monkeypatch.setattr(restarted._admission, "hold_active_lifecycle_operation_claim", hold_then_expire)
    monkeypatch.setattr(factory_run_service_module, "time", _ReplayTime)

    with pytest.raises(RuntimeError, match="factory_physical_attempt_replay_head_unstable"):
        await restarted.recover_run(created.id)

    assert yielded_revalidations == 0
    assert created.id not in restarted._physical_attempt_coordinators


@pytest.mark.asyncio
async def test_recovery_deadline_stops_after_final_lease_revalidation_before_final_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    owner = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    restarted = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    created = await owner.create_run(FactoryConfig(name="replay-deadline-after-final-revalidation"))
    await owner.start_run(created.id)
    expired = False
    yielded_revalidations = 0
    fence_captures = 0
    original_hold = restarted._admission.hold_active_lifecycle_operation_claim
    original_capture = restarted._capture_physical_attempt_replay_fence

    @contextmanager
    def counted_hold(*args: Any, **kwargs: Any) -> Any:
        with original_hold(*args, **kwargs) as revalidate:

            def counted_revalidate() -> object:
                nonlocal expired, yielded_revalidations
                yielded_revalidations += 1
                result = revalidate()
                if yielded_revalidations == 3:
                    expired = True
                return result

            yield counted_revalidate

    def count_capture(*args: Any, **kwargs: Any) -> object:
        nonlocal fence_captures
        fence_captures += 1
        return original_capture(*args, **kwargs)

    class _ReplayTime:
        @staticmethod
        def monotonic() -> float:
            return 31.0 if expired else 0.0

    monkeypatch.setattr(restarted._admission, "hold_active_lifecycle_operation_claim", counted_hold)
    monkeypatch.setattr(restarted, "_capture_physical_attempt_replay_fence", count_capture)
    monkeypatch.setattr(factory_run_service_module, "time", _ReplayTime)

    with pytest.raises(RuntimeError, match="factory_physical_attempt_replay_head_unstable"):
        await restarted.recover_run(created.id)

    assert yielded_revalidations == 3
    assert fence_captures == 2
    assert created.id not in restarted._physical_attempt_coordinators


@pytest.mark.asyncio
async def test_replay_failure_leaves_durable_fence_closed_to_old_peer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    owner = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    restarted = FactoryRunService(workspace, cache_root=cache_root, executor=_SuccessfulStageExecutor())
    created = await owner.create_run(FactoryConfig(name="failed-cross-process-replay-fence"))
    running = await owner.start_run(created.id)
    old_token = running.metadata["factory_workspace_run_lease"]["fencing_token"]
    stage_lease = owner._admission.claim_stage(
        created.id,
        fencing_token=old_token,
        stage="director_dispatch",
        nonce="old-service-failed-replay-stage-claim",
    )
    stage_claim = stage_lease.stage_execution_claim
    assert stage_claim is not None
    old_port = owner._physical_attempt_coordinator(created.id)
    grant = FactoryPhysicalAttemptGrantViewV1(
        schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
        verification_scope="factory",
        factory_run_id=created.id,
        role="director",
        stage="director_dispatch",
        workspace_fencing_token=old_token,
        stage_claim_attempt=stage_claim.attempt,
        stage_claim_nonce=stage_claim.nonce,
        execution_authority_hash="d" * 64,
        attempt_budget=2,
    )
    old_port.register_grant(grant)
    old_command = ReserveFactoryPhysicalAttemptV1(
        schema_version=RESERVE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
        verification_scope="factory",
        factory_run_id=created.id,
        run_id="director-child-failed-replay",
        role="director",
        turn_id="director-turn-failed-replay",
        call_id="pre-failed-replay-call",
        request_freeze_id="freeze-failed-replay",
        execution_authority_hash=grant.execution_authority_hash,
        attempt_budget=2,
        provider="openai_compat",
        model="bound-model",
        semantic_request_hash="e" * 64,
        physical_wire_hash="f" * 64,
    )
    old_port.reserve(old_command)

    def fail_replay(**_kwargs: object) -> None:
        raise RuntimeError("forced-cross-process-replay-failure")

    monkeypatch.setattr(restarted, "_recover_physical_attempt_coordinator", fail_replay)
    with pytest.raises(RuntimeError, match="forced-cross-process-replay-failure"):
        await restarted.recover_run(created.id)

    durable_after_failure = restarted._admission.current()
    assert durable_after_failure is not None
    assert durable_after_failure.state.value == "draining"
    assert durable_after_failure.fencing_token > old_token
    assert durable_after_failure.lifecycle_operation_claim is None
    failed_run = await restarted.get_run(created.id)
    assert failed_run is not None
    assert failed_run.metadata["factory_physical_attempt_admission_dead"] is True

    with pytest.raises(FactoryPhysicalAttemptControlError, match="factory_physical_attempt_authority_closed"):
        old_port.reserve(replace(old_command, call_id="post-failed-replay-call"))

    with pytest.raises(
        FactoryPhysicalAttemptControlError,
        match="factory_physical_attempt_recovered_run_permanently_closed",
    ):
        await owner.execute_stage(
            created.id,
            "pm_planning",
            {"heartbeat_interval_seconds": 0},
        )

    assert restarted._admission.current() == durable_after_failure


def test_lifecycle_claim_blocks_new_stage_before_replay_or_mutation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
    )
    lease = admission.acquire("factory-lifecycle-stage-fence")
    claimed = admission.claim_lifecycle_operation(
        lease.run_id,
        operation="retry_run_from_stage",
        nonce="retry-fence",
        acquire_if_available=False,
        expected_fencing_token=lease.fencing_token,
    )

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict:
        admission.claim_stage(
            lease.run_id,
            fencing_token=claimed.fencing_token,
            stage="pm_planning",
            nonce="must-not-start",
        )

    assert conflict.value.code == "factory_lifecycle_operation_inflight"
    assert admission.current() == claimed


def test_replay_fence_invalidates_preexisting_stage_authority(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "runtime" / "factory",
    )
    lease = admission.acquire("factory-replay-stage-authority-fence")
    stage_lease = admission.claim_stage(
        lease.run_id,
        fencing_token=lease.fencing_token,
        stage="director_dispatch",
        nonce="old-stage-authority",
    )
    stage_claim = stage_lease.stage_execution_claim
    assert stage_claim is not None

    replay_lease = admission.claim_lifecycle_operation(
        lease.run_id,
        operation="recover_run",
        nonce="restart-replay-fence",
        acquire_if_available=False,
        expected_fencing_token=lease.fencing_token,
        replay_fence=True,
    )

    assert replay_lease.state.value == "draining"
    assert replay_lease.fencing_token > lease.fencing_token
    with (
        pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict,
        admission.hold_active_stage_claim(
            lease.run_id,
            fencing_token=lease.fencing_token,
            stage=stage_claim.stage,
            attempt=stage_claim.attempt,
            nonce=stage_claim.nonce,
        ),
    ):
        pytest.fail("stale stage authority crossed the restart replay fence")

    assert conflict.value.code == "factory_workspace_run_fenced"
    assert admission.current() == replay_lease


@pytest.mark.asyncio
async def test_failed_settled_stage_auto_releases_workspace_lease_on_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal FAILED stage must drain/release the workspace lease inside execute_stage.

    L1-05 r82 left lease state=active with stage_execution_claim still held after
    director_dispatch failed because closeout depended solely on a later router
    complete_run. Service-owned auto settle is the durable guarantee.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    barrier_queries: list[tuple[str, str]] = []

    def query_closed_barrier(
        queried_workspace: str | Path,
        factory_run_id: str,
    ) -> FactorySettlementBarrierResultV1:
        normalized_workspace = str(Path(queried_workspace).resolve())
        assert normalized_workspace == str(workspace.resolve())
        barrier_queries.append((normalized_workspace, factory_run_id))
        return _settlement_barrier(
            workspace=workspace,
            factory_run_id=factory_run_id,
            release_allowed=True,
        )

    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=_FailedSettledStageExecutor(),
        settlement_barrier_query=query_closed_barrier,
    )
    run = await service.create_run(FactoryConfig(name="failed-settled", stages=["director_dispatch"]))
    await service.start_run(run.id)
    runtime, _task_id, identity = _create_active_factory_child(
        workspace,
        factory_run_id=run.id,
    )
    completed = _settle_factory_child(
        runtime,
        identity,
        outcome="completed",
        summary="fixture child settled",
    )
    assert completed["success"] is True

    def query_canonical_settlement(factory_run_id: str) -> dict[str, object]:
        assert factory_run_id == run.id
        return {
            "schema_version": "task-runtime.factory-run-settlement/1",
            "factory_run_id": factory_run_id,
            "settled": True,
            "active_session_count": 0,
            "active_sessions": [],
            "conflict_count": 0,
            "conflicts": [],
            "observable_source": "task_runtime.observable_task_rows",
            "observable_authoritative": True,
            "observable_row_count": 1,
            "proof_sources": [
                "task_runtime.observable_task_rows",
                "task_runtime.execution_session_files",
            ],
        }

    monkeypatch.setattr(service, "_query_child_session_settlement", query_canonical_settlement)

    result = await service.execute_stage(
        run.id,
        "director_dispatch",
        {"heartbeat_interval_seconds": 0},
    )

    assert result.status == "failed"
    stored = await service.get_run(run.id)
    assert stored is not None
    assert stored.status == FactoryRunStatus.FAILED
    assert stored.completed_at is not None
    assert stored.metadata.get("completion_authority") == "orchestration_session_lifecycle"
    released = service._admission.current()
    assert released is not None
    assert released.state.value == "released"
    assert released.stage_execution_claim is None
    assert released.released_at is not None
    assert released.release_evidence is not None
    assert released.release_evidence.source == "factory_terminal_drain"
    assert barrier_queries == [(str(workspace.resolve()), run.id)]


@pytest.mark.asyncio
async def test_failed_settled_stage_complete_run_is_idempotent_after_auto_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Router complete_run(success=False) remains safe after service auto-release."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def query_closed_barrier(
        queried_workspace: str | Path,
        factory_run_id: str,
    ) -> FactorySettlementBarrierResultV1:
        assert Path(queried_workspace).resolve() == workspace.resolve()
        return _settlement_barrier(
            workspace=workspace,
            factory_run_id=factory_run_id,
            release_allowed=True,
        )

    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=_FailedSettledStageExecutor(),
        settlement_barrier_query=query_closed_barrier,
    )
    run = await service.create_run(FactoryConfig(name="failed-complete-release", stages=["director_dispatch"]))
    await service.start_run(run.id)
    runtime, _task_id, identity = _create_active_factory_child(
        workspace,
        factory_run_id=run.id,
    )
    completed = _settle_factory_child(
        runtime,
        identity,
        outcome="completed",
        summary="fixture child settled",
    )
    assert completed["success"] is True

    monkeypatch.setattr(
        service,
        "_query_child_session_settlement",
        lambda factory_run_id: {
            "schema_version": "task-runtime.factory-run-settlement/1",
            "factory_run_id": factory_run_id,
            "settled": True,
            "active_session_count": 0,
            "active_sessions": [],
            "conflict_count": 0,
            "conflicts": [],
            "observable_source": "task_runtime.observable_task_rows",
            "observable_authoritative": True,
            "observable_row_count": 1,
            "proof_sources": [
                "task_runtime.observable_task_rows",
                "task_runtime.execution_session_files",
            ],
        },
    )

    result = await service.execute_stage(
        run.id,
        "director_dispatch",
        {"heartbeat_interval_seconds": 0},
    )
    assert result.status == "failed"
    after_stage = service._admission.current()
    assert after_stage is not None
    assert after_stage.state.value == "released"
    assert after_stage.stage_execution_claim is None

    closed = await service.complete_run(run.id, success=False)

    assert closed.status == FactoryRunStatus.FAILED
    assert closed.completed_at is not None
    assert closed.metadata.get("completion_authority") == "orchestration_session_lifecycle"
    lease_meta = closed.metadata.get("factory_workspace_run_lease") or {}
    assert lease_meta.get("state") == "released"
    assert lease_meta.get("released_at")
    durable = service._admission.current()
    assert durable is not None
    assert durable.state.value == "released"
    assert durable.released_at is not None
    assert durable.stage_execution_claim is None
    assert durable.release_evidence is not None


@pytest.mark.asyncio
async def test_stage_executor_cannot_forge_terminal_drain_deferral(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=_ForgedTerminalDrainProjectionExecutor(),
    )
    run = await service.create_run(FactoryConfig(name="forged-drain", stages=["chief_engineer_review"]))
    await service.start_run(run.id)

    result = await service.execute_stage(
        run.id,
        "chief_engineer_review",
        {"heartbeat_interval_seconds": 0},
    )

    assert result.status == "failed"
    assert "factory_terminal_drain_deferred" not in result.metadata
    durable = service._admission.current()
    assert durable is not None
    assert durable.state.value == "released"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_stage",
    [
        "chief_engineer_review",
        "director_dispatch",
        "quality_gate",
    ],
)
async def test_legacy_task_market_receipt_does_not_issue_terminal_drain_projection(
    tmp_path: Path,
    failed_stage: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class _StaticExecutor:
        result = StageResult(stage=failed_stage, status="failed", output="typed failure", metadata={})

        async def execute(self, _stage: str, _run: FactoryRun, _context: dict[str, Any]) -> StageResult:
            return self.result

    executor = _StaticExecutor()
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=executor,
    )
    run = await service.create_run(FactoryConfig(name="canonical-rework", stages=[failed_stage]))
    executor.result.metadata["factory_local_rework_schedule"] = {
        "status": "committed",
        "factory_run_id": run.id,
        "owner_task_id": "TASK-1",
        "target_stage": "pending_exec",
        "requeue_idempotency_key": "a" * 64,
        "requeue_receipt_ref": "b" * 64,
    }
    await service.start_run(run.id)

    result = await service.execute_stage(run.id, failed_stage, {"heartbeat_interval_seconds": 0})

    assert result.metadata.get("factory_terminal_drain_deferred") is None


@pytest.mark.asyncio
async def test_durable_completion_action_issues_quality_local_rework_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class _StaticExecutor:
        async def execute(self, stage: str, _run: FactoryRun, _context: dict[str, Any]) -> StageResult:
            return StageResult(stage=stage, status="failed", output="build failed", metadata={})

    service = FactoryRunService(workspace, cache_root=tmp_path / "runtime", executor=_StaticExecutor())
    run = await service.create_run(FactoryConfig(name="durable-rework", stages=["quality_gate"]))
    await service.start_run(run.id)

    async def notify(_run_id: str, _result: StageResult) -> FactoryProjectCompletionNotificationResultV1:
        return FactoryProjectCompletionNotificationResultV1(
            status="waiting",
            reason_codes=("owner_action_receipt_committed",),
            action_id="a" * 64,
            diagnostic_id="diagnostic-1",
            next_action="run_deterministic_repair",
        )

    monkeypatch.setattr(service, "_notify_project_completion_supervisor", notify)
    result = await service.execute_stage(run.id, "quality_gate", {"heartbeat_interval_seconds": 0})

    assert result.metadata["factory_terminal_drain_deferred"] == {
        "schema_version": "factory.terminal-drain-deferred.v2",
        "reason": "quality_rework_decision_pending",
        "decision_owner": "orchestration.workflow_orchestration",
        "action_id": "a" * 64,
        "diagnostic_id": "diagnostic-1",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("proof_available", [True, False])
async def test_stage_context_uses_only_factory_revalidated_pm_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    proof_available: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured: dict[str, Any] = {}

    class _CaptureExecutor:
        async def execute(
            self,
            stage: str,
            _run: FactoryRun,
            context: dict[str, Any],
        ) -> StageResult:
            captured.update(context)
            return StageResult(stage=stage, status="failed", output="capture")

    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=_CaptureExecutor(),
    )
    trusted = SimpleNamespace(task_ids=("TASK-1",))

    async def _proof(_run_id: str) -> SimpleNamespace | None:
        return trusted if proof_available else None

    monkeypatch.setattr(service, "_revalidated_pm_stage_artifact_binding", _proof)
    forged = SimpleNamespace(task_ids=("TASK-FORGED",))
    await service._execute_stage_logic(
        FactoryRun(
            id="factory-current",
            config=FactoryConfig(name="binding-test"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-09T00:00:00+00:00",
        ),
        "director_dispatch",
        {PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY: forged},
    )

    if proof_available:
        assert captured[PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY] is trusted
    else:
        assert PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY not in captured


@pytest.mark.asyncio
async def test_quarantine_terminalized_failed_run_complete_run_releases_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After quarantine terminalize (status=FAILED), complete_run must still release.

    The router previously returned early on factory_stage_* without complete_run,
    leaving the workspace lease active forever. Service closeout must work once
    the run is already terminal from quarantine terminalize.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def query_closed_barrier(
        queried_workspace: str | Path,
        factory_run_id: str,
    ) -> FactorySettlementBarrierResultV1:
        del queried_workspace
        return _settlement_barrier(
            workspace=workspace,
            factory_run_id=factory_run_id,
            release_allowed=True,
        )

    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=_SuccessfulStageExecutor(),
        settlement_barrier_query=query_closed_barrier,
    )
    run = await service.create_run(FactoryConfig(name="quarantine-closeout", stages=["pm_planning"]))
    run = await service.start_run(run.id)
    assert service._admission.current() is not None
    assert service._admission.current().state.value == "active"

    monkeypatch.setattr(
        service,
        "_query_child_session_settlement",
        lambda factory_run_id: {
            "schema_version": "task-runtime.factory-run-settlement/1",
            "factory_run_id": factory_run_id,
            "settled": True,
            "active_session_count": 0,
            "active_sessions": [],
            "conflict_count": 0,
            "conflicts": [],
            "observable_source": "task_runtime.observable_task_rows",
            "observable_authoritative": True,
            "observable_row_count": 0,
            "proof_sources": [
                "task_runtime.observable_task_rows",
                "task_runtime.execution_session_files",
            ],
        },
    )

    # Simulate quarantine terminalize on the durable run projection.
    run.status = FactoryRunStatus.FAILED
    run.completed_at = service._now()
    run.metadata["factory_stage_in_flight"] = False
    run.metadata["factory_quarantine_terminalized"] = True
    run.metadata["completion_authority"] = "orchestration_session_lifecycle"
    run.metadata["failure"] = {
        "stage": "pm_planning",
        "code": "FACTORY_STAGE_QUARANTINED",
        "detail": "simulated quarantine",
        "timestamp": run.completed_at,
    }
    await service.store.save_run(run)

    closed = await service.complete_run(run.id, success=False)

    assert closed.status == FactoryRunStatus.FAILED
    lease_meta = closed.metadata.get("factory_workspace_run_lease") or {}
    assert lease_meta.get("state") == "released"
    assert lease_meta.get("released_at")
    durable = service._admission.current()
    assert durable is not None
    assert durable.state.value == "released"
    assert durable.released_at is not None


@pytest.mark.asyncio
async def test_failed_complete_run_force_fails_active_child_and_releases_lease(
    tmp_path: Path,
) -> None:
    """R64: Director timeout left active session → drain stuck. Force-fail on FAILED closeout."""

    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()

    def query_closed_barrier(
        queried_workspace: str | Path,
        factory_run_id: str,
    ) -> FactorySettlementBarrierResultV1:
        assert Path(queried_workspace).resolve() == workspace.resolve()
        return _settlement_barrier(
            workspace=workspace,
            factory_run_id=factory_run_id,
            release_allowed=True,
        )

    service = FactoryRunService(
        workspace,
        cache_root=cache_root,
        executor=_InflightStageExecutor(),
        settlement_barrier_query=query_closed_barrier,
    )
    run = await service.create_run(FactoryConfig(name="force-active-abort", stages=["director_dispatch"]))
    await service.start_run(run.id)
    runtime, task_id, _identity = _create_active_factory_child(
        workspace,
        factory_run_id=run.id,
    )

    result = await service.execute_stage(
        run.id,
        "director_dispatch",
        {"heartbeat_interval_seconds": 0},
    )
    assert result.metadata.get("child_sessions_settled") is False

    closed = await service.complete_run(run.id, success=False)

    assert closed.status == FactoryRunStatus.FAILED
    assert closed.completed_at is not None
    abort = closed.metadata.get("factory_task_runtime_abort") or {}
    assert abort.get("force_active_sessions") is True
    assert int(abort.get("force_failed_active_count") or 0) >= 1
    lease_meta = closed.metadata.get("factory_workspace_run_lease") or {}
    assert lease_meta.get("state") == "released"
    assert lease_meta.get("released_at")
    durable = service._admission.current()
    assert durable is not None
    assert durable.state.value == "released"
    assert durable.stage_execution_claim is None
    # Active child must be force-failed (not left active).
    rows = runtime.query_observable_task_rows_projection().rows_for_factory_run(run.id)
    assert rows, "expected factory-owned task rows before reset or residual projection"
    # After successful release, task rows may be reset; session must not remain active.
    session = runtime._read_session(task_id)
    if session is not None:
        assert str(session.status).lower() != "active"


@pytest.mark.asyncio
async def test_inflight_stage_claim_survives_wrapper_return_until_child_settles(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _SequencedSettlementStageExecutor()
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=executor,
    )
    run = await service.create_run(FactoryConfig(name="retained-stage-claim", stages=["director_dispatch"]))
    await service.start_run(run.id)
    runtime, _task_id, identity = _create_active_factory_child(
        workspace,
        factory_run_id=run.id,
    )

    first = await service.execute_stage(
        run.id,
        "director_dispatch",
        {"heartbeat_interval_seconds": 0},
    )
    assert first.metadata["inflight_run_continues"] is True
    assert service._admission.current().stage_execution_claim is not None

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict:
        await service.execute_stage(
            run.id,
            "director_dispatch",
            {"heartbeat_interval_seconds": 0},
        )
    assert conflict.value.code == "factory_stage_execution_conflict"
    assert executor.entered_count == 1

    completed = _settle_factory_child(runtime, identity, outcome="completed", summary="child settled")
    assert completed["success"] is True
    await service.settle_terminal_run(run.id)
    released_lease = service._admission.current()
    assert released_lease is not None
    assert released_lease.stage_execution_claim is None

    with pytest.raises(
        FactoryPhysicalAttemptControlError,
        match="factory_physical_attempt_recovered_run_permanently_closed",
    ):
        await service.retry_run_from_stage(run.id, target_stage="director_dispatch")
    assert service._admission.current().stage_execution_claim is None
    assert executor.entered_count == 1


@pytest.mark.asyncio
async def test_terminal_drain_reacts_to_child_terminal_fact_and_queries_remain_pure(
    tmp_path: Path,
) -> None:
    """FAILED complete_run force-fails owned active Director sessions (R64) and releases.

    Pre-R64 this left the lease draining forever with child_session_inflight.
    Observation APIs still must not mutate lease state after release.
    """

    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    service = FactoryRunService(
        workspace,
        cache_root=cache_root,
        executor=_InflightStageExecutor(),
    )
    run = await service.create_run(FactoryConfig(name="inflight-child", stages=["director_dispatch"]))
    await service.start_run(run.id)
    runtime, task_id, identity = _create_active_factory_child(
        workspace,
        factory_run_id=run.id,
    )
    del identity  # force-fail path owns terminalization; no cooperative settle needed
    task_path = runtime._board.tasks_dir / f"task_{task_id}.json"
    session_path = runtime._board.tasks_dir / f"task_{task_id}.session.json"

    result = await service.execute_stage(
        run.id,
        "director_dispatch",
        {"heartbeat_interval_seconds": 0},
    )
    assert result.metadata["child_sessions_settled"] is False
    assert result.metadata["inflight_run_continues"] is True
    released = await service.complete_run(run.id, success=False)

    assert released.metadata["factory_child_sessions_settled"] is True
    assert released.metadata["factory_workspace_run_lease"]["state"] == "released"
    assert released.metadata.get("factory_task_runtime_abort", {}).get("force_active_sessions") is True
    assert "factory_workspace_run_drain_conflict" not in released.metadata
    snapshot = released.metadata["factory_terminal_task_runtime_projection"]
    assert snapshot["schema_version"] == "factory.terminal-task-runtime-projection.v1"
    assert snapshot["factory_run_id"] == run.id
    assert snapshot["projection"]["source"] == "task_runtime.execution_fact"
    assert snapshot["projection"]["authoritative"] is True
    assert snapshot["projection"]["degraded"] is False
    assert snapshot["projection"]["requested_factory_run_id"] == run.id
    assert snapshot["projection"]["row_count"] == 1
    assert snapshot["projection"]["rows"][0]["factory_run_id"] == run.id
    # Reset after release removes task/session files under factory authority.
    assert not task_path.exists()
    assert not session_path.exists()

    before_read_lease = service._admission.current()
    before_read_events = await service.get_run_events(run.id)
    observed = await service.get_run(run.id)
    await service.list_runs()

    assert observed is not None
    assert observed.metadata["factory_child_sessions_settled"] is True
    assert service._admission.current() == before_read_lease
    assert await service.get_run_events(run.id) == before_read_events

    again = await service.settle_terminal_run(run.id)
    assert again is not None
    assert again.metadata["factory_workspace_run_lease"]["state"] == "released"


@pytest.mark.asyncio
async def test_terminal_drain_retry_reuses_snapshot_after_reset_before_release_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry must not replace the pre-reset authority snapshot with zero rows."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=_InflightStageExecutor(),
    )
    run = await service.create_run(FactoryConfig(name="terminal-snapshot-crash", stages=["director_dispatch"]))
    await service.start_run(run.id)
    _create_active_factory_child(workspace, factory_run_id=run.id)
    await service.execute_stage(
        run.id,
        "director_dispatch",
        {"heartbeat_interval_seconds": 0},
    )

    real_task_runtime = TaskRuntimeService
    crash_once = True

    class _CrashAfterResetTaskRuntime:
        def __init__(self, requested_workspace: str) -> None:
            self._delegate = real_task_runtime(requested_workspace)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._delegate, name)

        def reset_records(self, *, keep_plan: bool, factory_run_id: str | None = None) -> dict[str, object]:
            nonlocal crash_once
            result = self._delegate.reset_records(
                keep_plan=keep_plan,
                factory_run_id=factory_run_id,
            )
            if crash_once:
                crash_once = False
                raise RuntimeError("simulated crash after TaskRuntime reset")
            return result

    monkeypatch.setattr(factory_run_service_module, "TaskRuntimeService", _CrashAfterResetTaskRuntime)

    with pytest.raises(RuntimeError, match="simulated crash after TaskRuntime reset"):
        await service.complete_run(run.id, success=False)

    after_crash = await service.get_run(run.id)
    assert after_crash is not None
    frozen = after_crash.metadata["factory_terminal_task_runtime_projection"]
    assert frozen["projection"]["row_count"] == 1

    released = await service.settle_terminal_run(run.id)

    assert released.metadata["factory_workspace_run_lease"]["state"] == "released"
    assert released.metadata["factory_terminal_task_runtime_projection"] == frozen


@pytest.mark.asyncio
async def test_terminal_drain_revalidates_run_ledger_barrier_before_reset_and_release(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    workspace.mkdir()
    barrier_state = {"release_allowed": False}

    def query_barrier(
        queried_workspace: str | Path,
        factory_run_id: str,
    ) -> FactorySettlementBarrierResultV1:
        assert Path(queried_workspace) == workspace
        return _settlement_barrier(
            workspace=workspace,
            factory_run_id=factory_run_id,
            release_allowed=barrier_state["release_allowed"],
        )

    service = FactoryRunService(
        workspace,
        cache_root=cache_root,
        executor=_SuccessfulStageExecutor(),
        settlement_barrier_query=query_barrier,
    )
    run = await service.create_run(FactoryConfig(name="ledger-barrier", stages=["director_dispatch"]))
    await service.start_run(run.id)
    runtime, task_id, identity = _create_active_factory_child(
        workspace,
        factory_run_id=run.id,
    )
    task_path = runtime._board.tasks_dir / f"task_{task_id}.json"
    session_path = runtime._board.tasks_dir / f"task_{task_id}.session.json"

    await service.execute_stage(
        run.id,
        "director_dispatch",
        {"heartbeat_interval_seconds": 0},
    )
    await service.complete_run(run.id, success=True)
    completed = _settle_factory_child(runtime, identity, outcome="completed", summary="child settled")
    assert completed["success"] is True

    still_draining = await service.settle_terminal_run(run.id)

    assert still_draining.metadata["factory_workspace_run_lease"]["state"] == "draining"
    assert still_draining.metadata["factory_workspace_run_drain_conflict"]["code"] == (
        "factory_run_ledger_settlement_barrier_open"
    )
    assert still_draining.metadata["factory_run_ledger_settlement_barrier"]["barrier_hash"] == ("barrier-open")
    assert task_path.is_file()
    assert session_path.is_file()

    barrier_state["release_allowed"] = True
    released = await service.settle_terminal_run(run.id)

    assert released.metadata["factory_workspace_run_lease"]["state"] == "released"
    assert released.metadata["factory_run_ledger_settlement_barrier"]["barrier_hash"] == ("barrier-closed")
    assert not task_path.exists()
    assert not session_path.exists()


@pytest.mark.asyncio
async def test_recover_run_rejects_active_factory_child(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=_SuccessfulStageExecutor(),
    )
    run = await service.create_run(FactoryConfig(name="recover-active-child"))
    await service.start_run(run.id)
    _create_active_factory_child(workspace, factory_run_id=run.id)

    with pytest.raises(FactoryPipelineError) as conflict:
        await service.recover_run(run.id)

    assert conflict.value.code == "factory_workspace_run_child_session_inflight"
    stored = await service.store.get_run(run.id)
    assert stored is not None
    assert stored.status == FactoryRunStatus.RUNNING
    assert stored.metadata["factory_child_sessions_settled"] is False


@pytest.mark.asyncio
async def test_recover_run_clears_crash_stage_claim_after_durable_settlement(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=_SuccessfulStageExecutor(),
    )
    run = await service.create_run(FactoryConfig(name="recover-crash-claim"))
    run = await service.start_run(run.id)
    lease = service._admission.claim_stage(
        run.id,
        fencing_token=run.metadata["factory_workspace_run_lease"]["fencing_token"],
        stage="director_dispatch",
        nonce="crashed-stage",
    )
    run.metadata["factory_workspace_run_lease"] = lease.to_dict()
    run.metadata["factory_stage_in_flight"] = True
    await service.store.save_run(run)

    recovered = await service.recover_run(run.id)

    assert recovered.status == FactoryRunStatus.RECOVERING
    assert recovered.metadata["factory_stage_in_flight"] is False
    current = service._admission.current()
    assert current is not None
    assert current.stage_execution_claim is None
    assert current.lifecycle_operation_claim is None


@pytest.mark.asyncio
async def test_explicit_stale_owner_recovery_fences_old_session_before_takeover(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    state_root = tmp_path / "runtime" / "factory"
    workspace.mkdir()
    clock = _MutableClock()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=state_root,
        lease_ttl_seconds=10,
        clock=clock,
    )
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=_SuccessfulStageExecutor(),
        admission=admission,
    )
    run = await service.create_run(FactoryConfig(name="stale-owner"))
    run = await service.start_run(run.id)
    runtime, task_id, identity = _create_active_factory_child(
        workspace,
        factory_run_id=run.id,
    )
    identity = _expire_task_runtime_session(runtime, identity)
    clock.advance(11)
    stale = admission.current()
    assert stale is not None

    released = await service.recover_stale_workspace_owner(
        run.id,
        expected_fencing_token=stale.fencing_token,
        reason="owner process disappeared",
    )

    assert released.state.value == "released"
    assert released.release_evidence is not None
    assert released.release_evidence.source == "factory_stale_owner_recovery"
    heartbeat = runtime.heartbeat_execution(task_id, session_id=identity.session_id)
    completion = _settle_factory_child(runtime, identity, outcome="completed")
    assert heartbeat["success"] is False
    assert heartbeat["reason"] == "session_not_active"
    assert completion["success"] is False
    # Recovery fences the expired session by rotating its persisted lease
    # version.  The old attempt must therefore fail at the exact-identity
    # barrier before its inactive status can be considered.
    assert completion["code"] == "lease_version_mismatch"

    replacement = admission.acquire("factory-replacement")
    assert replacement.run_id == "factory-replacement"
    assert replacement.fencing_token > stale.fencing_token


@pytest.mark.asyncio
async def test_restarted_service_replays_physical_attempts_before_stale_owner_release(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    state_root = cache_root / "factory"
    workspace.mkdir()
    clock = _MutableClock()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=state_root,
        lease_ttl_seconds=10,
        clock=clock,
    )
    creator = FactoryRunService(
        workspace,
        cache_root=cache_root,
        executor=_SuccessfulStageExecutor(),
        admission=admission,
    )
    run = await creator.create_run(FactoryConfig(name="stale-owner-restart"))
    run = await creator.start_run(run.id)
    clock.advance(11)

    restarted = FactoryRunService(
        workspace,
        cache_root=cache_root,
        executor=_SuccessfulStageExecutor(),
        admission=admission,
    )
    released = await restarted.recover_stale_workspace_owner(
        run.id,
        expected_fencing_token=run.metadata["factory_workspace_run_lease"]["fencing_token"],
        reason="owner process disappeared",
    )

    assert released.state.value == "released"
    assert released.lifecycle_operation_claim is None
    assert released.release_evidence is not None
    physical_drain = released.release_evidence.details["physical_attempt_drain"]
    assert physical_drain["settled"] is True
    recovered_port = restarted._physical_attempt_coordinator(run.id)
    assert recovered_port.admission_closed is True
    assert recovered_port.drain_snapshot().settled is True


@pytest.mark.asyncio
async def test_stale_owner_replay_failure_rolls_back_claim_without_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    cache_root = tmp_path / "runtime"
    state_root = cache_root / "factory"
    workspace.mkdir()
    clock = _MutableClock()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=state_root,
        lease_ttl_seconds=10,
        clock=clock,
    )
    creator = FactoryRunService(
        workspace,
        cache_root=cache_root,
        executor=_SuccessfulStageExecutor(),
        admission=admission,
    )
    run = await creator.create_run(FactoryConfig(name="stale-owner-replay-failure"))
    run = await creator.start_run(run.id)
    clock.advance(11)
    stale = admission.current()
    assert stale is not None

    restarted = FactoryRunService(
        workspace,
        cache_root=cache_root,
        executor=_SuccessfulStageExecutor(),
        admission=admission,
    )

    def fail_replay(*_args: Any, **_kwargs: Any) -> object:
        raise RuntimeError("forced-replay-failure")

    monkeypatch.setattr(restarted, "_capture_physical_attempt_replay_views", fail_replay)
    with pytest.raises(RuntimeError, match="forced-replay-failure"):
        await restarted.recover_stale_workspace_owner(
            run.id,
            expected_fencing_token=stale.fencing_token,
            reason="owner process disappeared",
        )

    durable = admission.current()
    assert durable is not None
    assert durable.state.value == "draining"
    assert durable.fencing_token > stale.fencing_token
    assert durable.expires_at == stale.expires_at
    assert durable.lifecycle_operation_claim is None
    assert run.id not in restarted._physical_attempt_coordinators


@pytest.mark.asyncio
async def test_retry_run_rejects_active_factory_child(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=_InflightStageExecutor(),
    )
    run = await service.create_run(FactoryConfig(name="retry-active-child", stages=["director_dispatch"]))
    await service.start_run(run.id)
    _create_active_factory_child(workspace, factory_run_id=run.id)
    await service.execute_stage(
        run.id,
        "director_dispatch",
        {"heartbeat_interval_seconds": 0},
    )

    with pytest.raises(FactoryPipelineError) as conflict:
        await service.retry_run_from_stage(run.id, target_stage="director_dispatch")

    assert conflict.value.code == "factory_workspace_run_child_session_inflight"
    stored = await service.store.get_run(run.id)
    assert stored is not None
    assert stored.status == FactoryRunStatus.FAILED
    assert stored.metadata["factory_child_sessions_settled"] is False


@pytest.mark.asyncio
async def test_pm_planning_passes_factory_run_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured: dict[str, Any] = {}

    class CapturingTaskRuntime:
        def __init__(self, requested_workspace: str) -> None:
            captured["workspace"] = requested_workspace

        def reset_records(self, *, keep_plan: bool, factory_run_id: str | None = None) -> dict[str, object]:
            captured["keep_plan"] = keep_plan
            captured["factory_run_id"] = factory_run_id
            return {"ok": True, "cleared_count": 0, "failed_count": 0}

    class CompletedPmService:
        async def execute_pm_run(self, **kwargs: Any) -> CommandResult:
            del kwargs
            return CommandResult(run_id="pm-run", status="completed", message="completed", metadata={})

    executor = OrchestrationStageExecutor(workspace)
    authority_port = object.__new__(FactoryRoleEvidenceAuthorityPort)
    run = FactoryRun(
        id="factory-authority",
        config=FactoryConfig(name="authority", stages=["pm_planning"]),
        status=FactoryRunStatus.RUNNING,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    monkeypatch.setattr(stage_executor_module, "TaskRuntimeService", CapturingTaskRuntime)
    monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: CompletedPmService())

    async def call_with_test_authority(
        _authority_port: object,
        _role: str,
        operation: Any,
    ) -> Any:
        return await operation()

    monkeypatch.setattr(executor, "_call_with_factory_role_evidence_authority", call_with_test_authority)

    async def completed_wait(*args: Any, **kwargs: Any) -> CommandResult:
        del args, kwargs
        return CommandResult(run_id="pm-run", status="completed", message="completed", metadata={})

    monkeypatch.setattr(executor, "_wait_run_completion", completed_wait)
    monkeypatch.setattr(executor, "_ensure_pm_plan_contract_available", lambda: "")
    monkeypatch.setattr(executor, "_enrich_pm_plan_contract_artifact", lambda _path: {})
    monkeypatch.setattr(executor, "_validate_pm_plan_contract", lambda _path: None)
    monkeypatch.setattr(executor, "_validate_pm_plan_language_consistency", lambda _path: None)
    monkeypatch.setattr(executor, "_load_pm_plan_tasks", lambda _path: [])
    monkeypatch.setattr(executor, "_artifact_exists", lambda _path, min_chars=1: False)
    monkeypatch.setattr(executor, "_write_stage_signal_artifact", lambda **_kwargs: "signals.json")

    result = await executor._execute_pm_planning(
        run,
        {
            "directive": "Plan implementation tasks",
            stage_executor_module.FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY: authority_port,
        },
    )

    assert result.status == "success"
    assert captured["factory_run_id"] == run.id
    assert captured["keep_plan"] is True


def test_factory_deadline_policy_normalizes_float_budgets_to_ints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        OrchestrationStageExecutor,
        "_director_first_materialization_min_budget_seconds",
        staticmethod(lambda _context: 90.2),
    )
    monkeypatch.setattr(
        OrchestrationStageExecutor,
        "_quality_gate_reserved_budget_seconds",
        staticmethod(lambda _context: 120.2),
    )

    policy = OrchestrationStageExecutor._factory_deadline_budget_policy({})

    assert policy.director_first_task_min_seconds == 91
    assert policy.quality_gate_reserved_seconds == 121
    assert isinstance(policy.director_first_task_min_seconds, int)
    assert isinstance(policy.quality_gate_reserved_seconds, int)
    assert isinstance(policy.safety_seconds, int)


def test_success_director_stage_releases_claim_despite_inflight_run_continues_flag() -> None:
    """R187/M07: success + inflight_run_continues must still release for quality_gate."""

    held = StageResult(
        stage="director_dispatch",
        status="success",
        output="timeout settled with delivery",
        metadata={
            "child_sessions_settled": True,
            "inflight_run_continues": True,
            "settlement_source": "director_dispatch_timeout_settle_grace",
        },
    )
    assert FactoryRunService._stage_result_releases_execution_claim(held) is True

    failed_inflight = StageResult(
        stage="director_dispatch",
        status="failed",
        output="barrier timeout",
        metadata={
            "child_sessions_settled": False,
            "inflight_run_continues": True,
        },
    )
    assert FactoryRunService._stage_result_releases_execution_claim(failed_inflight) is False

    success_unsettled = StageResult(
        stage="director_dispatch",
        status="success",
        output="children still open",
        metadata={
            "child_sessions_settled": False,
            "inflight_run_continues": True,
        },
    )
    assert FactoryRunService._stage_result_releases_execution_claim(success_unsettled) is False
