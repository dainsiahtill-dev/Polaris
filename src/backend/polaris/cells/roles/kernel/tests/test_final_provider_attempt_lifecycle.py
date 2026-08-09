from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendSegmentedFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    append_segmented_fact_event,
    bootstrap_fact_stream_workspace,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle import (
    StrictProviderAttemptLifecycleStore,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA,
    FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
    FactoryPhysicalAttemptLeaseV1,
    FactoryPhysicalAttemptStartPermitV1,
    ProviderAttemptStartReceiptV1,
    ProviderAttemptTerminalReceiptV1,
)
from polaris.kernelone.events.sourcing.segmented_file_store import SegmentedJsonlEventStore
from polaris.kernelone.llm.engine.contracts import FrozenFinalProviderAttemptV1


def _bootstrap(workspace: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=("task_runtime.execution",),
            maintenance_reason="final_provider_attempt_lifecycle_test",
        )
    )


def _attempt(
    *,
    verification_scope: str = "factory",
    scope_id: str = "factory-run-1",
) -> FrozenFinalProviderAttemptV1:
    return FrozenFinalProviderAttemptV1(
        provider_request_id="provider-request-1",
        request_freeze_id="freeze-1",
        factory_run_id=scope_id if verification_scope == "factory" else "",
        scope_id=scope_id,
        run_id="run-1",
        turn_id="turn-1",
        call_id="call-1",
        role="director",
        provider="openai",
        model="model-1",
        attempt_number=1,
        verification_scope=verification_scope,
        execution_authority_hash="f" * 64 if verification_scope == "factory" else "",
        attempt_budget=32 if verification_scope == "factory" else 0,
        authority_attempt_ordinal=1 if verification_scope == "factory" else 0,
        semantic_candidate_hash="d" * 64 if verification_scope == "factory" else "",
        semantic_request_hash="a" * 64,
        physical_wire_hash="b" * 64,
        composite_request_hash="c" * 64,
        dispatch_view={},
        durable_view={},
    )


def _start_permit(attempt: FrozenFinalProviderAttemptV1) -> FactoryPhysicalAttemptStartPermitV1:
    return FactoryPhysicalAttemptStartPermitV1(
        schema_version=FACTORY_PHYSICAL_ATTEMPT_START_PERMIT_SCHEMA,
        verification_scope="factory",
        factory_run_id=attempt.factory_run_id,
        run_id=attempt.run_id,
        role=attempt.role,
        turn_id=attempt.turn_id,
        call_id=attempt.call_id,
        request_freeze_id=attempt.request_freeze_id,
        execution_authority_hash="f" * 64,
        attempt_budget=32,
        provider=attempt.provider,
        model=attempt.model,
        semantic_request_hash=attempt.semantic_request_hash,
        physical_wire_hash=attempt.physical_wire_hash,
        composite_request_hash=attempt.composite_request_hash,
        reservation_id="reservation-1",
        provider_request_id=attempt.provider_request_id,
        authority_attempt_ordinal=attempt.attempt_number,
        start_permit_id="start-permit-1",
    )


def _lease(
    permit: FactoryPhysicalAttemptStartPermitV1,
    receipt: ProviderAttemptStartReceiptV1,
) -> FactoryPhysicalAttemptLeaseV1:
    return FactoryPhysicalAttemptLeaseV1(
        schema_version=FACTORY_PHYSICAL_ATTEMPT_LEASE_SCHEMA,
        verification_scope=permit.verification_scope,
        factory_run_id=permit.factory_run_id,
        run_id=permit.run_id,
        role=permit.role,
        turn_id=permit.turn_id,
        call_id=permit.call_id,
        request_freeze_id=permit.request_freeze_id,
        execution_authority_hash=permit.execution_authority_hash,
        attempt_budget=permit.attempt_budget,
        provider=permit.provider,
        model=permit.model,
        semantic_request_hash=permit.semantic_request_hash,
        physical_wire_hash=permit.physical_wire_hash,
        composite_request_hash=permit.composite_request_hash,
        reservation_id=permit.reservation_id,
        provider_request_id=permit.provider_request_id,
        authority_attempt_ordinal=permit.authority_attempt_ordinal,
        start_permit_id=permit.start_permit_id,
        lease_id="lease-1",
        start_receipt=receipt,
    )


def test_lifecycle_ledger_validation_is_cached_by_physical_runtime_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime"
    workspace.mkdir()
    runtime_root.mkdir()
    ensure_calls: list[str] = []

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle.resolve_storage_roots",
        lambda _workspace: SimpleNamespace(
            workspace_abs=str(workspace),
            runtime_root=str(runtime_root),
        ),
    )
    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle.ensure_segmented_fact_ledger",
        lambda command: ensure_calls.append(command.logical_stream),
    )

    StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(workspace),
        factory_run_id="factory-run-1",
    )
    StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(workspace),
        factory_run_id="factory-run-1",
    )
    assert len(ensure_calls) == 1

    StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(workspace),
        factory_run_id="factory-run-2",
    )
    assert len(ensure_calls) == 2

    shutil.rmtree(runtime_root)
    (tmp_path / "consume-released-inode").mkdir()
    runtime_root.mkdir()
    StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(workspace),
        factory_run_id="factory-run-1",
    )
    assert len(ensure_calls) == 3


def test_lifecycle_ledger_validation_failure_is_not_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime"
    workspace.mkdir()
    runtime_root.mkdir()
    ensure_calls = 0

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle.resolve_storage_roots",
        lambda _workspace: SimpleNamespace(
            workspace_abs=str(workspace),
            runtime_root=str(runtime_root),
        ),
    )

    def _ensure(_command: object) -> None:
        nonlocal ensure_calls
        ensure_calls += 1
        if ensure_calls == 1:
            raise RuntimeError("integrity check failed")

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle.ensure_segmented_fact_ledger",
        _ensure,
    )

    with pytest.raises(RuntimeError, match="integrity check failed"):
        StrictProviderAttemptLifecycleStore.for_factory_run(
            workspace=str(workspace),
            factory_run_id="factory-run-failure",
        )
    StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(workspace),
        factory_run_id="factory-run-failure",
    )
    assert ensure_calls == 2


def test_factory_lifecycle_returns_exact_durable_receipts(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    attempt = _attempt()
    permit = _start_permit(attempt)

    start_receipt = lifecycle.append_start(
        attempt,
        start_permit=permit,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
    )
    assert type(start_receipt) is ProviderAttemptStartReceiptV1
    lease = _lease(permit, start_receipt)
    terminal_receipt = lifecycle.append_terminal(
        attempt,
        lease=lease,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
        status="completed",
    )
    assert type(terminal_receipt) is ProviderAttemptTerminalReceiptV1
    assert terminal_receipt.logical_sequence > start_receipt.logical_sequence
    assert terminal_receipt.lease_id == lease.lease_id


def test_factory_lifecycle_proves_exact_start_absence_at_captured_head(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    permit = _start_permit(_attempt())

    proof = lifecycle.prove_start_not_persisted(permit)

    assert proof is not None
    assert proof.start_permit == permit
    assert proof.lifecycle_head_sequence == 0
    assert len(proof.lifecycle_head_hash) == 64
    assert proof.durability_acked is True


def test_factory_lifecycle_never_projects_persisted_or_conflicting_start_as_absent(tmp_path: Path) -> None:
    persisted_workspace = tmp_path / "persisted"
    conflict_workspace = tmp_path / "conflict"
    _bootstrap(persisted_workspace)
    _bootstrap(conflict_workspace)
    attempt = _attempt()
    permit = _start_permit(attempt)
    persisted = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(persisted_workspace),
        factory_run_id="factory-run-1",
    )
    persisted.append_start(
        attempt,
        start_permit=permit,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
    )
    assert persisted.prove_start_not_persisted(permit) is None

    conflict = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(conflict_workspace),
        factory_run_id="factory-run-1",
    )
    append_segmented_fact_event(
        AppendSegmentedFactEventCommandV1(
            workspace=str(conflict_workspace),
            logical_stream=conflict.logical_stream,
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": permit.provider_request_id},
            idempotency_key=f"{permit.provider_request_id}:conflicting-start",
        )
    )
    with pytest.raises(RuntimeError, match="start identity conflict"):
        conflict.prove_start_not_persisted(permit)


def test_terminal_without_authoritative_start_fails_closed(tmp_path: Path) -> None:
    source_workspace = tmp_path / "receipt-source"
    target_workspace = tmp_path / "missing-start-target"
    _bootstrap(source_workspace)
    _bootstrap(target_workspace)
    attempt = _attempt()
    permit = _start_permit(attempt)
    source_lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(source_workspace),
        factory_run_id="factory-run-1",
    )
    start_receipt = source_lifecycle.append_start(
        attempt,
        start_permit=permit,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
    )
    assert type(start_receipt) is ProviderAttemptStartReceiptV1
    lease = _lease(permit, start_receipt)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(target_workspace),
        factory_run_id="factory-run-1",
    )
    with pytest.raises(RuntimeError, match="start is missing or ambiguous"):
        lifecycle.append_terminal(
            attempt,
            lease=lease,
            context_snapshot_ref="d" * 24,
            pin_hash="e" * 64,
            status="failed",
        )
    assert lifecycle.query_strict() == ()


def test_duplicate_terminal_is_idempotent_only_for_the_same_terminal_fact(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    attempt = _attempt()
    permit = _start_permit(attempt)
    start_receipt = lifecycle.append_start(
        attempt,
        start_permit=permit,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
    )
    assert type(start_receipt) is ProviderAttemptStartReceiptV1
    lease = _lease(permit, start_receipt)
    lifecycle.append_terminal(
        attempt,
        lease=lease,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
        status="completed",
    )
    lifecycle.append_terminal(
        attempt,
        lease=lease,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
        status="completed",
    )
    facts = lifecycle.query_strict()
    assert [item["event_type"] for item in facts] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]

    with pytest.raises((RuntimeError, ValueError)):
        lifecycle.append_terminal(
            attempt,
            lease=lease,
            context_snapshot_ref="d" * 24,
            pin_hash="e" * 64,
            status="failed",
            error="semantic conflict",
        )


def test_terminal_healthy_path_recovers_start_by_locator_without_full_ledger_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    attempt = _attempt()

    def _scan_forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("healthy lifecycle append must not run strict head/full scan")

    monkeypatch.setattr(lifecycle, "query_strict", _scan_forbidden)
    monkeypatch.setattr(SegmentedJsonlEventStore, "head", _scan_forbidden)
    monkeypatch.setattr(SegmentedJsonlEventStore, "_full_scan_and_rebuild_locked", _scan_forbidden)
    permit = _start_permit(attempt)
    start_receipt = lifecycle.append_start(
        attempt,
        start_permit=permit,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
    )
    assert type(start_receipt) is ProviderAttemptStartReceiptV1
    lease = _lease(permit, start_receipt)
    lifecycle.append_terminal(
        attempt,
        lease=lease,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
        status="completed",
    )
    monkeypatch.undo()
    replay = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    ).query_strict()
    assert [item["event_type"] for item in replay] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]


def test_role_session_ledger_is_separate_and_cannot_satisfy_factory_scope(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    factory = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    role_session = StrictProviderAttemptLifecycleStore.for_role_session(
        workspace=str(tmp_path),
        role_session_id="role-session-1",
    )
    factory_attempt = _attempt()
    role_attempt = _attempt(verification_scope="role_session", scope_id="role-session-1")

    with pytest.raises(RuntimeError, match="scope"):
        role_session.append_start(factory_attempt, context_snapshot_ref="d" * 24, pin_hash="e" * 64)
    with pytest.raises(RuntimeError, match="scope"):
        factory.append_start(role_attempt, context_snapshot_ref="d" * 24, pin_hash="e" * 64)

    role_session.append_start(role_attempt, context_snapshot_ref="d" * 24, pin_hash="e" * 64)
    role_session.append_terminal(
        role_attempt,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
        status="completed",
    )
    assert factory.query_strict() == ()
    assert [item["event_type"] for item in role_session.query_strict()] == [
        "provider_attempt.started",
        "provider_attempt.terminal",
    ]


def test_lifecycle_rejects_malformed_refs_pin_hash_and_terminal_status_before_append(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
        workspace=str(tmp_path),
        factory_run_id="factory-run-1",
    )
    attempt = _attempt()

    with pytest.raises(ValueError, match="context_snapshot_ref"):
        lifecycle.append_start(attempt, context_snapshot_ref="D" * 24, pin_hash="e" * 64)
    with pytest.raises(ValueError, match="pin_hash"):
        lifecycle.append_start(attempt, context_snapshot_ref="d" * 24, pin_hash="e" * 63)
    assert lifecycle.query_strict() == ()

    permit = _start_permit(attempt)
    start_receipt = lifecycle.append_start(
        attempt,
        start_permit=permit,
        context_snapshot_ref="d" * 24,
        pin_hash="e" * 64,
    )
    assert type(start_receipt) is ProviderAttemptStartReceiptV1
    lease = _lease(permit, start_receipt)
    with pytest.raises(ValueError, match="terminal status"):
        lifecycle.append_terminal(
            attempt,
            lease=lease,
            context_snapshot_ref="d" * 24,
            pin_hash="e" * 64,
            status="success",
        )
    assert [item["event_type"] for item in lifecycle.query_strict()] == ["provider_attempt.started"]
