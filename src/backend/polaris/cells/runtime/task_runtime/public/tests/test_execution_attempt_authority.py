"""Concurrency and fail-closed tests for TaskRuntime public attempt authority."""

from __future__ import annotations

import pickle
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from multiprocessing import get_context
from multiprocessing.synchronize import Event as ProcessEvent
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.events.fact_stream.public.service import QueryFactEventsV1, query_fact_events
from polaris.cells.runtime.task_runtime.public import (
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    OpenTaskRuntimeExecutionAttemptAuthorityCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1,
    TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementVerdictV1,
    TaskRuntimeService,
    create_task_runtime_execution_attempt_authority,
    open_task_runtime_execution_attempt_authority,
)


def _identity(*, lease_expires_at: str = "2026-07-14T00:05:00+00:00") -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/tmp/task-runtime-authority",
        task_id=41,
        external_task_id="TASK-41",
        session_id="session-41",
        attempt=2,
        role_id="director",
        worker_id="director-worker",
        run_id="run-41",
        lease_expires_at=lease_expires_at,
    )


def _bootstrap_task_runtime_fact_stream(workspace: Path) -> None:
    """Establish the FactStream authority required by TaskRuntime event I/O."""

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="task-runtime-execution-attempt-authority-test",
            streams=fact_stream_bootstrap_streams(),
        )
    )


def _renewed_verdict(
    command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
    renewed = replace(command.identity, lease_expires_at="2026-07-14T00:10:00+00:00")
    return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
        success=True,
        code="heartbeat_renewed",
        workspace=command.workspace,
        identity=command.identity,
        renewed_identity=renewed,
    )


def _settled_verdict(
    command: SettleTaskRuntimeExecutionAttemptCommandV1,
) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
    return TaskRuntimeExecutionAttemptSettlementVerdictV1(
        success=True,
        code="settled",
        workspace=command.workspace,
        identity=command.identity,
        outcome=command.outcome,
    )


def test_heartbeat_and_settle_have_one_linearized_order_and_share_renewed_identity() -> None:
    """Settlement cannot observe identity until the in-flight heartbeat publishes it."""

    heartbeat_entered = Event()
    release_heartbeat = Event()
    settle_called = Event()
    calls: list[str] = []
    identities: list[TaskRuntimeExecutionAttemptIdentityV1] = []

    def heartbeat(
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        calls.append("heartbeat")
        heartbeat_entered.set()
        assert release_heartbeat.wait(timeout=1.0)
        return _renewed_verdict(command)

    def settle(
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
        calls.append("settle")
        identities.append(command.identity)
        settle_called.set()
        return _settled_verdict(command)

    authority = create_task_runtime_execution_attempt_authority(
        _identity(),
        heartbeat=heartbeat,
        settle=settle,
    )
    results: dict[
        str,
        TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1
        | TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1,
    ] = {}
    heartbeat_thread = Thread(
        target=lambda: results.setdefault(
            "heartbeat",
            authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=1.0),
        ),
    )
    settlement_thread = Thread(
        target=lambda: results.setdefault(
            "settle",
            authority.settle(outcome="completed", summary="done", lock_timeout_seconds=1.0),
        ),
    )

    heartbeat_thread.start()
    assert heartbeat_entered.wait(timeout=1.0)
    settlement_thread.start()
    assert not settle_called.wait(timeout=0.05)
    release_heartbeat.set()
    heartbeat_thread.join(timeout=1.0)
    settlement_thread.join(timeout=1.0)

    heartbeat_result = results["heartbeat"]
    settlement_result = results["settle"]
    assert isinstance(heartbeat_result, TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1)
    assert isinstance(settlement_result, TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1)
    assert heartbeat_result.success is True
    assert settlement_result.success is True
    assert calls == ["heartbeat", "settle"]
    assert identities == [heartbeat_result.identity]


def test_successful_heartbeat_without_renewed_identity_fails_closed() -> None:
    """Malformed successful callback output cannot advance the authority identity."""

    identity = _identity()
    malformed = object.__new__(TaskRuntimeExecutionAttemptHeartbeatVerdictV1)
    object.__setattr__(malformed, "success", True)
    object.__setattr__(malformed, "code", "heartbeat_renewed")
    object.__setattr__(malformed, "workspace", identity.workspace)
    object.__setattr__(malformed, "identity", identity)
    object.__setattr__(malformed, "renewed_identity", None)
    object.__setattr__(malformed, "evidence_anchor", {})
    authority = create_task_runtime_execution_attempt_authority(
        identity,
        heartbeat=lambda _command: malformed,
        settle=_settled_verdict,
    )

    result = authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=0.1)

    assert result.success is False
    assert result.code == "heartbeat_missing_renewed_identity"
    assert authority.snapshot().identity == identity


def test_heartbeat_identity_drift_preserves_current_identity() -> None:
    """A renewal for another attempt binding is rejected before publication."""

    identity = _identity()

    def drifting_heartbeat(
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
            success=True,
            code="heartbeat_renewed",
            workspace=command.workspace,
            identity=command.identity,
            renewed_identity=replace(command.identity, worker_id="other-worker"),
        )

    authority = create_task_runtime_execution_attempt_authority(
        identity,
        heartbeat=drifting_heartbeat,
        settle=_settled_verdict,
    )

    result = authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=0.1)

    assert result.success is False
    assert result.code == "heartbeat_identity_drift"
    assert authority.snapshot().identity == identity


def test_bounded_lock_timeout_never_reads_or_mutates_identity() -> None:
    """A concurrent callback retains the handle lock until its bounded operation exits."""

    entered = Event()
    release = Event()
    identity = _identity()

    def blocking_heartbeat(
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        entered.set()
        assert release.wait(timeout=1.0)
        return _renewed_verdict(command)

    authority = create_task_runtime_execution_attempt_authority(
        identity,
        heartbeat=blocking_heartbeat,
        settle=_settled_verdict,
    )
    thread = Thread(
        target=lambda: authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=1.0),
    )

    thread.start()
    assert entered.wait(timeout=1.0)
    snapshot = authority.snapshot(lock_timeout_seconds=0.01)
    rejected = authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=0.01)
    release.set()
    thread.join(timeout=1.0)

    assert snapshot.success is False
    assert snapshot.code == "authority_lock_timeout"
    assert snapshot.identity is None
    assert rejected.success is False
    assert rejected.code == "authority_lock_timeout"
    assert rejected.identity is None
    assert authority.snapshot().identity != identity


def test_settlement_failure_remains_open_for_retry() -> None:
    """A rejected settlement never creates a cached terminal success."""

    attempts = 0

    def settle(
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return TaskRuntimeExecutionAttemptSettlementVerdictV1(
                success=False,
                code="session_not_active",
                workspace=command.workspace,
                identity=command.identity,
                outcome=command.outcome,
            )
        return _settled_verdict(command)

    authority = create_task_runtime_execution_attempt_authority(
        _identity(),
        heartbeat=_renewed_verdict,
        settle=settle,
    )

    rejected = authority.settle(outcome="completed", summary="first")
    accepted = authority.settle(outcome="completed", summary="retry")

    assert rejected.success is False
    assert rejected.code == "settlement_rejected"
    assert accepted.success is True
    assert accepted.code == "settled"
    assert attempts == 2


def test_terminal_settlement_rejects_heartbeat_and_replays_same_outcome() -> None:
    """A closed handle cannot renew and returns a safe typed terminal replay."""

    settlement_calls = 0

    def settle(
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
        nonlocal settlement_calls
        settlement_calls += 1
        return _settled_verdict(command)

    authority = create_task_runtime_execution_attempt_authority(
        _identity(),
        heartbeat=_renewed_verdict,
        settle=settle,
    )

    settled = authority.settle(outcome="completed", summary="done")
    heartbeat = authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=0.1)
    replay = authority.settle(outcome="completed", summary="replay")
    conflict = authority.settle(outcome="failed", summary="conflict")

    assert settled.code == "settled"
    assert heartbeat.code == "authority_closed"
    assert replay.code == "terminal_replay"
    assert replay.success is True
    assert conflict.code == "terminal_outcome_conflict"
    assert settlement_calls == 1


def test_callback_exceptions_are_encapsulated_and_leave_authority_usable() -> None:
    """External callback failures remain concrete typed evidence without false success."""

    def failing_heartbeat(
        _command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        raise RuntimeError("heartbeat transport failed")

    def failing_settle(
        _command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
        raise OSError("settlement transport failed")

    identity = _identity()
    heartbeat_authority = create_task_runtime_execution_attempt_authority(
        identity,
        heartbeat=failing_heartbeat,
        settle=_settled_verdict,
    )
    settle_authority = create_task_runtime_execution_attempt_authority(
        identity,
        heartbeat=_renewed_verdict,
        settle=failing_settle,
    )

    heartbeat_result = heartbeat_authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=0.1)
    settlement_result = settle_authority.settle(outcome="completed", summary="done")

    assert heartbeat_result.code == "heartbeat_callback_exception"
    assert heartbeat_result.callback_error_type == "RuntimeError"
    assert heartbeat_authority.snapshot().identity == identity
    assert settlement_result.code == "settlement_callback_exception"
    assert settlement_result.callback_error_type == "OSError"
    assert settle_authority.snapshot().closed is False


def test_heartbeat_callback_reentrant_settle_fails_closed_without_deadlock() -> None:
    """A heartbeat callback cannot terminalize its own authority operation."""

    reentrant_results: list[TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1] = []
    settle_calls = 0

    def heartbeat(
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        reentrant_results.append(authority.settle(outcome="completed", summary="reentrant"))
        return _renewed_verdict(command)

    def settle(
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
        nonlocal settle_calls
        settle_calls += 1
        return _settled_verdict(command)

    authority = create_task_runtime_execution_attempt_authority(_identity(), heartbeat=heartbeat, settle=settle)

    renewed = authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=0.1)
    settled = authority.settle(outcome="completed", summary="after heartbeat")

    assert renewed.code == "heartbeat_renewed"
    assert reentrant_results[0].code == "authority_operation_in_progress"
    assert settle_calls == 1
    assert settled.code == "settled"


def test_settlement_callback_reentrant_heartbeat_fails_closed_without_deadlock() -> None:
    """A settlement callback cannot renew its own authority operation."""

    reentrant_results: list[TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1] = []

    def heartbeat(
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        return _renewed_verdict(command)

    def settle(
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
        reentrant_results.append(authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=0.1))
        return _settled_verdict(command)

    authority = create_task_runtime_execution_attempt_authority(_identity(), heartbeat=heartbeat, settle=settle)

    settled = authority.settle(outcome="completed", summary="done")

    assert settled.code == "settled"
    assert reentrant_results[0].code == "authority_operation_in_progress"
    assert authority.snapshot().closed is True


def test_successful_settlement_verdict_drift_fails_closed_for_every_binding() -> None:
    """A successful callback verdict must bind exactly to the issued command."""

    def wrong_workspace(
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
        return TaskRuntimeExecutionAttemptSettlementVerdictV1(
            success=True,
            code="settled",
            workspace="/tmp/other-workspace",
            identity=command.identity,
            outcome=command.outcome,
        )

    def wrong_identity(
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
        return TaskRuntimeExecutionAttemptSettlementVerdictV1(
            success=True,
            code="settled",
            workspace=command.workspace,
            identity=replace(command.identity, worker_id="other-worker"),
            outcome=command.outcome,
        )

    def wrong_outcome(
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
        return TaskRuntimeExecutionAttemptSettlementVerdictV1(
            success=True,
            code="settled",
            workspace=command.workspace,
            identity=command.identity,
            outcome="failed",
        )

    for settle in (wrong_workspace, wrong_identity, wrong_outcome):
        authority = create_task_runtime_execution_attempt_authority(
            _identity(),
            heartbeat=_renewed_verdict,
            settle=settle,
        )

        result = authority.settle(outcome="completed", summary="done")

        assert result.success is False
        assert result.code == "settlement_verdict_drift"
        assert result.task_runtime_verdict is not None
        assert authority.snapshot().closed is False


def test_settlement_verdict_drift_leaves_authority_open_for_retry() -> None:
    """A rejected success-shaped verdict cannot cache a terminal outcome."""

    calls = 0

    def settle(
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
        nonlocal calls
        calls += 1
        if calls == 1:
            return TaskRuntimeExecutionAttemptSettlementVerdictV1(
                success=True,
                code="settled",
                workspace=command.workspace,
                identity=command.identity,
                outcome="failed",
            )
        return _settled_verdict(command)

    authority = create_task_runtime_execution_attempt_authority(
        _identity(),
        heartbeat=_renewed_verdict,
        settle=settle,
    )

    drifted = authority.settle(outcome="completed", summary="first")
    retried = authority.settle(outcome="completed", summary="retry")

    assert drifted.code == "settlement_verdict_drift"
    assert retried.code == "settled"
    assert calls == 2


def test_operation_guard_clears_after_callback_exception() -> None:
    """A callback exception cannot leave the shared mutation guard stuck."""

    calls = 0
    reentrant_results: list[TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1] = []

    def heartbeat(
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        nonlocal calls
        calls += 1
        if calls == 1:
            reentrant_results.append(authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=0.1))
            raise RuntimeError("transport failed")
        return _renewed_verdict(command)

    authority = create_task_runtime_execution_attempt_authority(
        _identity(),
        heartbeat=heartbeat,
        settle=_settled_verdict,
    )

    failed = authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=0.1)
    retried = authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=0.1)

    assert reentrant_results[0].code == "authority_operation_in_progress"
    assert failed.code == "heartbeat_callback_exception"
    assert retried.code == "heartbeat_renewed"
    assert calls == 2


def test_handle_has_no_session_only_or_durable_serialization_surface() -> None:
    """The handle exposes only full-identity projections and no persistence API."""

    authority = create_task_runtime_execution_attempt_authority(
        _identity(),
        heartbeat=_renewed_verdict,
        settle=_settled_verdict,
    )

    assert not hasattr(authority, "session_id")
    assert not hasattr(authority, "task_id")
    assert not hasattr(authority, "to_record")
    assert authority.snapshot().identity == _identity()
    assert not isinstance(SimpleNamespace(), TaskRuntimeExecutionAttemptIdentityV1)


def _claimed_attempt(tmp_path: Path) -> tuple[TaskRuntimeService, TaskRuntimeExecutionAttemptIdentityV1]:
    workspace = tmp_path / "task-runtime-authority-open"
    _bootstrap_task_runtime_fact_stream(workspace)
    service = TaskRuntimeService(str(workspace))
    task_id = int(service.create_task_row(subject="authority open")["id"])
    claim = service.claim_execution(
        task_id,
        worker_id="authority-worker",
        role_id="director",
        run_id="authority-run",
        external_task_id="authority-task",
        selection_source="authority-open-test",
    )
    assert claim["success"] is True
    return service, TaskRuntimeExecutionAttemptIdentityV1(**dict(claim["execution_attempt"]))


def _execution_fact_records(workspace: str) -> tuple[dict[str, object], ...]:
    result = query_fact_events(QueryFactEventsV1(workspace=workspace, stream="task_runtime.execution"))
    return tuple(dict(event) for event in result.events)


def _hold_cooperative_session_file_lock(
    workspace: str,
    task_id: int,
    acquired: ProcessEvent,
    release: ProcessEvent,
) -> None:
    """Hold the cooperative lock from an independent process for timeout coverage."""

    service = TaskRuntimeService(workspace)
    with service._board._file_lock(service._session_file_lock_path(task_id)):
        acquired.set()
        assert release.wait(timeout=2.0)


def test_open_authority_validates_active_attempt_without_persisted_side_effects(tmp_path: Path) -> None:
    service, identity = _claimed_attempt(tmp_path)
    session_before = service._read_session(identity.task_id)
    row_before = service.get_task(identity.task_id)
    facts_before = _execution_fact_records(identity.workspace)

    verdict = open_task_runtime_execution_attempt_authority(
        OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(workspace=identity.workspace, identity=identity),
    )

    assert verdict.success is True
    assert verdict.code == "valid"
    assert verdict.authority is not None
    assert verdict.authority.snapshot().identity == identity
    assert (
        OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(
            workspace=identity.workspace,
            identity=identity,
        ).to_record()["identity"]
        == identity.to_record()
    )
    assert session_before is not None
    current_session = service._read_session(identity.task_id)
    assert current_session is not None
    assert current_session.to_dict() == session_before.to_dict()
    assert service.get_task(identity.task_id) == row_before
    assert _execution_fact_records(identity.workspace) == facts_before
    record = verdict.to_record()
    assert "authority" not in record
    assert record["authority_opened"] is True
    observed = record["evidence"]["observed"]
    assert isinstance(observed, dict)
    observed["session_id"] = "mutated-record"
    assert verdict.evidence["observed"]["session_id"] == identity.session_id


@pytest.mark.parametrize(
    ("identity_change", "expected_code"),
    (
        ({"session_id": "fabricated"}, "session_mismatch"),
        ({"worker_id": "other-worker"}, "worker_mismatch"),
    ),
)
def test_open_authority_rejects_fabricated_mismatched_and_stale_identities(
    tmp_path: Path,
    identity_change: dict[str, str],
    expected_code: str,
) -> None:
    _service, identity = _claimed_attempt(tmp_path)
    if "session_id" in identity_change:
        changed_identity = replace(identity, session_id=identity_change["session_id"])
    else:
        changed_identity = replace(identity, worker_id=identity_change["worker_id"])
    verdict = open_task_runtime_execution_attempt_authority(
        OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(
            workspace=identity.workspace,
            identity=changed_identity,
        ),
    )

    assert verdict.success is False
    assert verdict.code == expected_code
    assert verdict.authority is None


def test_open_authority_accepts_same_owner_stale_lease_snapshot(tmp_path: Path) -> None:
    """R171: renewable lease_expires_at is not an open-authority fence.

    Concurrent same-owner heartbeats advance the stored lease while callers may
    still hold a pre-renewal snapshot. Open must not fail closed on lease-only
    drift (session/attempt/worker/role/run remain the fencing keys).
    """

    _service, identity = _claimed_attempt(tmp_path)
    stale = replace(identity, lease_expires_at="2026-01-01T00:00:00+00:00")
    verdict = open_task_runtime_execution_attempt_authority(
        OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(
            workspace=identity.workspace,
            identity=stale,
        ),
    )
    assert verdict.success is True
    assert verdict.code == "valid"
    assert verdict.authority is not None


def test_open_authority_rejects_terminal_and_expired_attempts(tmp_path: Path) -> None:
    service, identity = _claimed_attempt(tmp_path)
    settled = service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary="terminal",
        )
    )
    assert settled["success"] is True
    terminal_session = service._read_session(identity.task_id)
    assert terminal_session is not None
    terminal_identity = service._execution_attempt_identity_from_session(terminal_session)
    terminal = open_task_runtime_execution_attempt_authority(
        OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(
            workspace=identity.workspace,
            identity=terminal_identity,
        ),
    )
    assert terminal.success is False
    assert terminal.code == "session_not_active"
    assert terminal.authority is None

    expired_service, expired_identity = _claimed_attempt(tmp_path)
    expired_session = expired_service._read_session(expired_identity.task_id)
    assert expired_session is not None
    expired_session.lease_expires_at = "2020-01-01T00:00:00+00:00"
    assert expired_service._write_session(expired_session) is True
    expired_identity = expired_service._execution_attempt_identity_from_session(expired_session)
    expired = open_task_runtime_execution_attempt_authority(
        OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(
            workspace=expired_identity.workspace,
            identity=expired_identity,
        ),
    )
    assert expired.success is False
    assert expired.code == "session_lease_expired"
    assert expired.authority is None


def test_open_authority_lock_timeout_is_typed_and_side_effect_free(tmp_path: Path) -> None:
    service, identity = _claimed_attempt(tmp_path)
    lock_held = Event()
    release_lock = Event()
    session_lock = service._get_session_lock(identity.task_id)

    def hold_lock() -> None:
        with session_lock:
            lock_held.set()
            assert release_lock.wait(timeout=1.0)

    holder = Thread(target=hold_lock)
    holder.start()
    assert lock_held.wait(timeout=1.0)
    try:
        verdict = service.open_execution_attempt_authority(
            OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(
                workspace=identity.workspace,
                identity=identity,
                lock_timeout_seconds=0.01,
            )
        )
    finally:
        release_lock.set()
        holder.join(timeout=1.0)

    assert verdict.success is False
    assert verdict.code == "file_lock_timeout"
    assert verdict.authority is None
    assert verdict.evidence["lock_scope"] == "local_session"


def test_open_authority_cooperative_file_lock_timeout_is_typed_and_side_effect_free(tmp_path: Path) -> None:
    """An independent process holding the file lock cannot admit a second authority."""

    service, identity = _claimed_attempt(tmp_path)
    session_before = service._read_session(identity.task_id)
    row_before = service.get_task(identity.task_id)
    facts_before = _execution_fact_records(identity.workspace)
    context = get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_cooperative_session_file_lock,
        args=(identity.workspace, identity.task_id, acquired, release),
    )
    holder.start()
    assert acquired.wait(timeout=2.0)
    try:
        verdict = TaskRuntimeService(identity.workspace).open_execution_attempt_authority(
            OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(
                workspace=identity.workspace,
                identity=identity,
                lock_timeout_seconds=0.05,
            )
        )
    finally:
        release.set()
        holder.join(timeout=2.0)

    assert holder.exitcode == 0
    assert verdict.success is False
    assert verdict.code == "file_lock_timeout"
    assert verdict.authority is None
    assert verdict.evidence["lock_scope"] == "cooperative_session_file"
    assert session_before is not None
    current_session = service._read_session(identity.task_id)
    assert current_session is not None
    assert current_session.to_dict() == session_before.to_dict()
    assert service.get_task(identity.task_id) == row_before
    assert _execution_fact_records(identity.workspace) == facts_before


def test_open_authority_normalizes_cooperative_lock_setup_oserror_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lock setup failure is infrastructure evidence, not an identity mismatch."""

    service, identity = _claimed_attempt(tmp_path)
    session_before = service._read_session(identity.task_id)
    row_before = service.get_task(identity.task_id)
    facts_before = _execution_fact_records(identity.workspace)

    @contextmanager
    def fail_file_lock(*_args: object, **_kwargs: object) -> Iterator[object]:
        raise OSError("synthetic lock setup failure")
        yield

    monkeypatch.setattr(service._board, "_file_lock", fail_file_lock)
    verdict = service.open_execution_attempt_authority(
        OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(workspace=identity.workspace, identity=identity),
    )
    monkeypatch.undo()

    assert verdict.success is False
    assert verdict.code == "authority_open_internal_error"
    assert verdict.authority is None
    assert verdict.evidence == {
        "stage": "cooperative_session_file_lock",
        "error_type": "OSError",
        "error_message": "synthetic lock setup failure",
    }
    assert session_before is not None
    current_session = service._read_session(identity.task_id)
    assert current_session is not None
    assert current_session.to_dict() == session_before.to_dict()
    assert service.get_task(identity.task_id) == row_before
    assert _execution_fact_records(identity.workspace) == facts_before


def test_open_authority_normalizes_session_read_oserror_with_detached_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session read failure is fail-closed and does not become session_not_found."""

    service, identity = _claimed_attempt(tmp_path)
    row_before = service.get_task(identity.task_id)
    facts_before = _execution_fact_records(identity.workspace)

    def fail_session_read(*_args: object, **_kwargs: object) -> object:
        raise OSError("synthetic session read failure")

    monkeypatch.setattr(service, "_read_session_locked", fail_session_read)
    verdict = service.open_execution_attempt_authority(
        OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(workspace=identity.workspace, identity=identity),
    )
    monkeypatch.undo()

    assert verdict.success is False
    assert verdict.code == "authority_open_internal_error"
    assert verdict.authority is None
    assert verdict.evidence == {
        "stage": "session_read",
        "error_type": "OSError",
        "error_message": "synthetic session read failure",
    }
    record = verdict.to_record()
    record["evidence"]["error_message"] = "mutated record"
    assert verdict.evidence["error_message"] == "synthetic session read failure"
    assert service.get_task(identity.task_id) == row_before
    assert _execution_fact_records(identity.workspace) == facts_before


def test_terminal_and_open_race_is_linearized_and_cannot_revive_attempt(tmp_path: Path) -> None:
    service, identity = _claimed_attempt(tmp_path)
    start = Event()
    results: dict[str, object] = {}

    def open_authority() -> None:
        assert start.wait(timeout=1.0)
        results["open"] = service.open_execution_attempt_authority(
            OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(workspace=identity.workspace, identity=identity),
        )

    def settle_attempt() -> None:
        assert start.wait(timeout=1.0)
        results["settle"] = service.settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=identity.workspace,
                identity=identity,
                outcome="completed",
                summary="race terminal",
            )
        )

    opener = Thread(target=open_authority)
    settler = Thread(target=settle_attempt)
    opener.start()
    settler.start()
    start.set()
    opener.join(timeout=2.0)
    settler.join(timeout=2.0)

    opened = results["open"]
    settlement = results["settle"]
    assert isinstance(opened, TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1)
    assert isinstance(settlement, dict)
    assert settlement["success"] is True
    assert opened.code in {"valid", "session_not_active", "lease_version_mismatch"}
    current_session = service._read_session(identity.task_id)
    assert current_session is not None
    current = service._execution_attempt_identity_from_session(current_session)
    after_terminal = service.open_execution_attempt_authority(
        OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(workspace=identity.workspace, identity=current),
    )
    assert after_terminal.success is False
    assert after_terminal.code == "session_not_active"
    terminal_session = service._read_session(identity.task_id)
    assert terminal_session is not None
    assert terminal_session.status == "completed"


def test_repeated_open_creates_only_local_handles_and_internal_exceptions_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, identity = _claimed_attempt(tmp_path)
    command = OpenTaskRuntimeExecutionAttemptAuthorityCommandV1(workspace=identity.workspace, identity=identity)
    first = service.open_execution_attempt_authority(command)
    second = service.open_execution_attempt_authority(command)
    assert first.success is True
    assert second.success is True
    assert first.authority is not second.authority

    def fail_construction(_identity: TaskRuntimeExecutionAttemptIdentityV1) -> object:
        raise RuntimeError("synthetic authority construction failure")

    monkeypatch.setattr(service, "_create_execution_attempt_authority_locked", fail_construction)
    failed = service.open_execution_attempt_authority(command)
    assert failed.success is False
    assert failed.code == "authority_open_internal_error"
    assert failed.authority is None
    assert failed.evidence == {
        "stage": "authority_construction",
        "error_type": "RuntimeError",
        "error_message": "synthetic authority construction failure",
    }

    with pytest.raises(TypeError):
        open_task_runtime_execution_attempt_authority(object())  # type: ignore[arg-type]


def test_authority_handle_explicitly_rejects_pickle_serialization() -> None:
    """The process-local capability cannot be transferred into durable state."""

    authority = create_task_runtime_execution_attempt_authority(
        _identity(),
        heartbeat=_renewed_verdict,
        settle=_settled_verdict,
    )

    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(authority)
