"""The TaskRuntime owner authenticates claim provenance and current durable state."""

from __future__ import annotations

import importlib
import json
import threading
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
)
from polaris.kernelone.storage import resolve_runtime_path


@pytest.fixture(autouse=True)
def _private_kernelone_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "kernelone-owner"))


def _api() -> Any:
    return importlib.import_module("polaris.cells.runtime.task_runtime.public.execution_claim_evidence")


def _claim(workspace: Path) -> tuple[TaskRuntimeService, TaskRuntimeExecutionAttemptIdentityV1]:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="private-claim-evidence-owner-regression",
        )
    )
    service = TaskRuntimeService(str(workspace))
    row = service.create_task_row(
        subject="Private claim evidence",
        metadata={"external_task_id": "CLAIM-OWNER", "factory_run_id": "factory-owner", "original": {"value": 1}},
    )
    claimed = service.claim_execution(
        row["id"], worker_id="director", role_id="director", run_id="director-owner", external_task_id="CLAIM-OWNER"
    )
    assert claimed["success"], claimed
    return service, TaskRuntimeExecutionAttemptIdentityV1.from_record(claimed["execution_attempt"])


def _query(identity: TaskRuntimeExecutionAttemptIdentityV1, purpose: str = "active_completion") -> Any:
    api = _api()
    return api.query_execution_claim_evidence(
        api.QueryExecutionClaimEvidenceV1(workspace=identity.workspace, identity=identity, purpose=purpose)
    )


def _runtime_bytes(workspace: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(workspace)): path.read_bytes()
        for path in (workspace / ".polaris/runtime").rglob("*")
        if path.is_file()
    }


def _tree(workspace: Path) -> dict[str, bytes | None]:
    """Keep directory creation and lock resources visible too."""
    return {
        str(path.relative_to(workspace)): path.read_bytes() if path.is_file() else None for path in workspace.rglob("*")
    }


def _save_tree_evidence(workspace: Path, before: dict[str, bytes | None], after: dict[str, bytes | None]) -> None:
    # Private fixture bytes only, retained with every directory and lock file.
    (workspace / "query-tree-evidence.json").write_text(
        json.dumps(
            {
                "before": {key: value.hex() if value is not None else None for key, value in before.items()},
                "after": {key: value.hex() if value is not None else None for key, value in after.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_cold_query_proves_original_claim_without_writing_or_leaking_mutability(tmp_path: Path) -> None:
    _, identity = _claim(tmp_path)
    before = _runtime_bytes(tmp_path)
    proof = _query(identity)
    assert proof.purpose == "active_completion" and proof.status == "active"
    assert proof.acceptance_basis == "active_unexpired_attempt"
    assert proof.identity == identity and proof.claim_event_id and proof.claim_event_seq > 0
    assert proof.current_event_id and proof.current_event_seq >= proof.claim_event_seq
    assert proof.session_content_hash and proof.task_content_hash
    assert proof.claim_metadata["original"] == {"value": 1}
    copy = proof.claim_metadata
    copy["original"]["value"] = 99
    assert proof.claim_metadata["original"] == {"value": 1}
    with pytest.raises(FrozenInstanceError):
        proof.status = "completed"
    assert _query(identity) == proof
    assert _runtime_bytes(tmp_path) == before


@pytest.mark.parametrize("purpose", ["active_completion", "receipt_revalidation"])
@pytest.mark.parametrize("status", ["active", "completed", "failed", "suspended"])
@pytest.mark.parametrize("expired", [False, True])
def test_purpose_status_and_expiry_are_decided_by_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    purpose: str,
    status: Literal["active", "completed", "failed", "suspended"],
    expired: bool,
) -> None:
    service, identity = _claim(tmp_path)
    if status != "active":
        result = service.settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(str(tmp_path), identity, status, "private settled attempt")
        )
        assert result["success"], result
    if expired:
        from polaris.cells.runtime.task_runtime.internal.service import _mixin_dependency_session

        monkeypatch.setattr(_mixin_dependency_session, "utc_now", lambda: datetime(2200, 1, 1, tzinfo=UTC))
    accepted = (status == "active" and not expired) or (status == "completed" and purpose == "receipt_revalidation")
    if accepted:
        proof = _query(identity, purpose)
        assert proof.status == status and proof.purpose == purpose
        assert proof.acceptance_basis == (
            "completed_receipt_source" if status == "completed" else "active_unexpired_attempt"
        )
    else:
        with pytest.raises(ValueError, match=r"session_not_active|session_lease_expired"):
            _query(identity, purpose)


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", "foreign-session"),
        ("attempt", 99),
        ("worker_id", "foreign-worker"),
        ("run_id", "foreign-child"),
        ("external_task_id", "FOREIGN"),
    ],
)
def test_current_row_cannot_authenticate_a_foreign_attempt(tmp_path: Path, field: str, value: Any) -> None:
    _, identity = _claim(tmp_path)
    with pytest.raises(ValueError, match="mismatch"):
        _query(replace(identity, **{field: value}))


def test_current_session_cannot_replace_missing_original_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, identity = _claim(tmp_path)
    implementation = importlib.import_module("polaris.cells.runtime.task_runtime.internal.execution_claim_evidence")
    original = implementation._read_owner_events
    monkeypatch.setattr(
        implementation,
        "_read_owner_events",
        lambda workspace: tuple(e for e in original(workspace) if e["event_type"] != "claimed"),
    )
    with pytest.raises(ValueError, match="original_claim_missing"):
        _query(identity)


def test_unrelated_task_update_preserves_proof_but_same_task_metadata_changes_version(tmp_path: Path) -> None:
    service, identity = _claim(tmp_path)
    first = _query(identity)
    service.create_task_row(subject="Unrelated task")
    assert _query(identity) == first
    service.update_task_row(identity.task_id, metadata={"after_claim": "changed"})
    assert _query(identity) != first
    assert _query(identity).claim_metadata == first.claim_metadata


@pytest.mark.parametrize("field,value", [("session_id", "foreign-projected-session"), ("attempt", True)])
def test_stale_current_row_identity_is_not_replaced_by_valid_session(tmp_path: Path, field: str, value: Any) -> None:
    service, identity = _claim(tmp_path)
    row = service.get_task(identity.task_id)
    assert row is not None
    runtime = {**row["metadata"]["runtime_execution"], field: value}
    service.update_task_row(identity.task_id, metadata={"runtime_execution": runtime})
    with pytest.raises(ValueError, match="current_task_identity_mismatch"):
        _query(identity)


def test_concurrent_same_task_change_during_capture_is_explicit_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, identity = _claim(tmp_path)
    implementation = importlib.import_module("polaris.cells.runtime.task_runtime.internal.execution_claim_evidence")
    original = implementation._read_owner_events
    calls = 0

    def changing_capture(workspace: str) -> Any:
        nonlocal calls
        result = original(workspace)
        calls += 1
        if calls == 1:
            service.update_task_row(identity.task_id, metadata={"concurrent_change": True})
        return result

    monkeypatch.setattr(implementation, "_read_owner_events", changing_capture)
    with pytest.raises(ValueError, match="claim_evidence_drift"):
        _query(identity)


@pytest.mark.parametrize("branch", ["missing_root", "missing_session", "bad_session", "active", "completed"])
def test_all_query_branches_preserve_complete_runtime_tree_and_release_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, branch: str
) -> None:
    service, identity = _claim(tmp_path)
    implementation = importlib.import_module("polaris.cells.runtime.task_runtime.internal.execution_claim_evidence")
    original_constructor = implementation.TaskRuntimeService
    query_services: list[TaskRuntimeService] = []

    def record_constructor(workspace: str, *, read_only: bool = False) -> TaskRuntimeService:
        observed = original_constructor(workspace, read_only=read_only)
        query_services.append(observed)
        return observed

    monkeypatch.setattr(implementation, "TaskRuntimeService", record_constructor)
    if branch == "missing_root":
        identity = replace(identity, workspace=str(tmp_path / "missing-owner"))
    session_path = Path(resolve_runtime_path(str(tmp_path), f"runtime/tasks/task_{identity.task_id}.session.json"))
    if branch == "missing_session":
        session_path.unlink()
    elif branch == "bad_session":
        session_path.write_text("{private-invalid-json", encoding="utf-8")
    elif branch == "completed":
        assert service.settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(str(tmp_path), identity, "completed", "private completion")
        )["success"]
    before = _tree(tmp_path)
    if branch in {"active", "completed"}:
        _query(identity, "receipt_revalidation")
    else:
        with pytest.raises(ValueError):
            _query(identity)
    after = _tree(tmp_path)
    assert after == before
    _save_tree_evidence(tmp_path, before, after)
    # Query rejection and success both release the existing cooperative lock.
    with service._board._file_lock(service._session_file_lock_path(identity.task_id), timeout_seconds=0):
        pass
    for observed in query_services:
        if observed._read_only:
            continue
        acquired: list[bool] = []

        def probe_local_lock(service: TaskRuntimeService, results: list[bool]) -> None:
            lock = service._get_session_lock(identity.task_id)
            success = lock.acquire(timeout=0)
            results.append(success)
            if success:
                lock.release()

        probe = threading.Thread(target=probe_local_lock, args=(observed, acquired))
        probe.start()
        probe.join(timeout=5)
        assert not probe.is_alive() and acquired == [True]


def test_existing_live_owner_can_create_only_its_missing_synchronization_resource(tmp_path: Path) -> None:
    service, identity = _claim(tmp_path)
    lock = service._session_file_lock_path(identity.task_id)
    lock.unlink()
    before = _tree(tmp_path)
    _query(identity)
    after = _tree(tmp_path)
    assert set(after) - set(before) == {str(lock.relative_to(tmp_path))}
    assert after[str(lock.relative_to(tmp_path))] == b""
    assert {key: after[key] for key in before} == before
    _save_tree_evidence(tmp_path, before, after)
    with service._board._file_lock(lock, timeout_seconds=0):
        pass


def test_original_claim_binding_is_checked_and_same_task_successor_cannot_borrow_it(tmp_path: Path) -> None:
    service, identity = _claim(tmp_path)
    assert service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(str(tmp_path), identity, "failed", "private old attempt")
    )["success"]
    service.reopen_task_row(identity.task_id, reason="private-new-owner")
    successor = service.claim_execution(
        identity.task_id,
        worker_id="successor",
        role_id="director",
        run_id=identity.run_id,
        external_task_id=identity.external_task_id,
    )
    assert successor["success"]
    with pytest.raises(ValueError, match="session_mismatch"):
        _query(identity, "receipt_revalidation")


def test_cancelled_task_is_rejected_for_receipt_revalidation(tmp_path: Path) -> None:
    service, identity = _claim(tmp_path)
    assert service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(str(tmp_path), identity, "suspended", "private suspended attempt")
    )["success"]
    cancelled = service.cancel_task_row_for_factory_abort(identity.task_id, factory_run_id="factory-owner")
    assert cancelled is not None and cancelled["status"] == "cancelled"
    with pytest.raises(ValueError, match="session_not_active"):
        _query(identity, "receipt_revalidation")


def test_strict_fact_integrity_failure_is_preserved(tmp_path: Path) -> None:
    from polaris.cells.events.fact_stream.public import FactStreamError

    _, identity = _claim(tmp_path)
    path = Path(resolve_runtime_path(str(tmp_path), "runtime/events/task_runtime.execution.jsonl"))
    records = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(records[-1])
    record["payload"]["task_row_snapshot"]["metadata"]["original"]["value"] = 99
    records[-1] = json.dumps(record, ensure_ascii=False)
    path.write_text("\n".join(records) + "\n", encoding="utf-8")
    with pytest.raises(FactStreamError):
        _query(identity)


def test_real_heartbeat_between_history_and_state_cuts_never_runs_under_session_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, identity = _claim(tmp_path)
    implementation = importlib.import_module("polaris.cells.runtime.task_runtime.internal.execution_claim_evidence")
    original = implementation._read_owner_events
    captured = threading.Event()
    heartbeat_done = threading.Event()
    outcomes: list[Any] = []

    def heartbeat() -> None:
        if not captured.wait(timeout=5):
            return
        try:
            outcomes.append(
                service.heartbeat_execution(identity.task_id, session_id=identity.session_id, lease_ttl_seconds=120)
            )
        finally:
            heartbeat_done.set()

    calls = 0

    def capture_before_heartbeat(workspace: str) -> Any:
        nonlocal calls
        records = original(workspace)
        calls += 1
        if calls == 1:
            captured.set()
            assert heartbeat_done.wait(timeout=5), "history read held the writer's session lock"
        return records

    monkeypatch.setattr(implementation, "_read_owner_events", capture_before_heartbeat)
    worker = threading.Thread(target=heartbeat)
    worker.start()
    try:
        with pytest.raises(ValueError, match="claim_evidence_drift"):
            _query(identity)
    finally:
        worker.join(timeout=5)
    assert not worker.is_alive() and len(outcomes) == 1 and outcomes[0]["success"]
