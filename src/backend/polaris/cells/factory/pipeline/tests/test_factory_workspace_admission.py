"""Regression tests for fenced Factory workspace run admission."""

from __future__ import annotations

import asyncio
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import FactorySettlementBarrierResultV1
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal import (
    factory_stage_executor as stage_executor_module,
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
from polaris.cells.factory.pipeline.internal.factory_stage_executor import (
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.public.contracts import (
    FactoryPipelineError,
    FactoryWorkspaceReleaseEvidenceV1,
    FactoryWorkspaceRunLeaseConflictError,
)
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
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
async def test_two_services_cannot_execute_same_run_stage_concurrently(tmp_path: Path) -> None:
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

    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict:
        await contender.execute_stage(
            run.id,
            "director_dispatch",
            {"heartbeat_interval_seconds": 0},
        )

    assert conflict.value.code == "factory_stage_execution_conflict"
    assert executor.entered_count == 1
    executor.release.set()
    result = await asyncio.wait_for(first_execution, timeout=5)
    assert result.status == "success"
    assert owner._admission.current().stage_execution_claim is None


@pytest.mark.asyncio
async def test_failed_settled_stage_retains_exact_claim_until_explicit_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed attempt stays fenced until canonical settlement is reconciled."""

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

    canonical_settlement: dict[str, object] = {
        "schema_version": "task-runtime.factory-run-settlement/1",
        "factory_run_id": run.id,
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

    def query_canonical_settlement(factory_run_id: str) -> dict[str, object]:
        assert factory_run_id == run.id
        return dict(canonical_settlement)

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
    retained = service._admission.current()
    assert retained is not None
    assert retained.state.value == "active"
    assert retained.release_evidence is None
    retained_claim = retained.stage_execution_claim
    assert retained_claim is not None
    assert retained_claim.run_id == run.id
    assert retained_claim.stage == "director_dispatch"
    assert retained_claim.attempt == 1
    assert retained_claim.nonce
    assert barrier_queries == []

    reconciled = await service.reconcile_stage_execution_for_reentry(
        run.id,
        operation="factory_failure_terminalization",
    )

    assert reconciled.status == FactoryRunStatus.FAILED
    after_reconciliation = service._admission.current()
    assert after_reconciliation is not None
    assert after_reconciliation.state.value == "active"
    assert after_reconciliation.stage_execution_claim is None
    assert after_reconciliation.release_evidence is None
    assert barrier_queries == []

    settled = await service.settle_terminal_run(run.id)

    released = service._admission.current()
    assert released is not None
    assert released.state.value == "released"
    assert released.stage_execution_claim is None
    assert released.release_evidence is not None
    assert released.release_evidence.source == "factory_terminal_drain"
    assert settled.metadata["factory_run_ledger_settlement_barrier"]["barrier_hash"] == "barrier-closed"
    assert barrier_queries == [(str(workspace.resolve()), run.id)]


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

    await service.retry_run_from_stage(run.id, target_stage="director_dispatch")
    assert service._admission.current().stage_execution_claim is None
    second = await service.execute_stage(
        run.id,
        "director_dispatch",
        {"heartbeat_interval_seconds": 0},
    )
    assert second.status == "success"
    assert executor.entered_count == 2


@pytest.mark.asyncio
async def test_terminal_drain_reacts_to_child_terminal_fact_and_queries_remain_pure(
    tmp_path: Path,
) -> None:
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
    task_path = runtime._board.tasks_dir / f"task_{task_id}.json"
    session_path = runtime._board.tasks_dir / f"task_{task_id}.session.json"

    result = await service.execute_stage(
        run.id,
        "director_dispatch",
        {"heartbeat_interval_seconds": 0},
    )
    assert result.metadata["child_sessions_settled"] is False
    assert result.metadata["inflight_run_continues"] is True
    draining = await service.complete_run(run.id, success=False)

    assert draining.metadata["factory_child_sessions_settled"] is False
    assert draining.metadata["factory_workspace_run_lease"]["state"] == "draining"
    assert draining.metadata["factory_workspace_run_drain_conflict"]["code"] == (
        "factory_workspace_run_child_session_inflight"
    )
    assert task_path.is_file()
    assert session_path.is_file()

    completed = _settle_factory_child(runtime, identity, outcome="completed", summary="child settled")
    assert completed["success"] is True
    await service.settle_terminal_run(run.id)
    before_read_lease = service._admission.current()
    before_read_events = await service.get_run_events(run.id)
    observed = await service.get_run(run.id)
    await service.list_runs()

    assert observed is not None
    assert observed.metadata["factory_child_sessions_settled"] is True
    assert service._admission.current() == before_read_lease
    assert await service.get_run_events(run.id) == before_read_events
    assert not task_path.exists()
    assert not session_path.exists()

    released = await service.settle_terminal_run(run.id)

    assert released is not None
    assert released.metadata["factory_child_sessions_settled"] is True
    assert released.metadata["factory_workspace_run_lease"]["state"] == "released"
    assert "factory_workspace_run_drain_conflict" not in released.metadata


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
    run = FactoryRun(
        id="factory-authority",
        config=FactoryConfig(name="authority", stages=["pm_planning"]),
        status=FactoryRunStatus.RUNNING,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    monkeypatch.setattr(stage_executor_module, "TaskRuntimeService", CapturingTaskRuntime)
    monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: CompletedPmService())

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

    result = await executor._execute_pm_planning(run, {"directive": "Plan implementation tasks"})

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
