from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    AppendIfGuardedSnapshotCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamError,
    GuardedFactAppendedV1,
    GuardedFactEventV1,
    GuardedFactSnapshotV1,
    QueryFactEventsV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
    query_fact_events,
)
from polaris.cells.runtime.task_runtime.internal import directed_effect_operation as deo_internal
from polaris.cells.runtime.task_runtime.public import (
    DIRECTED_EFFECT_OPERATION_SCHEMA_V1,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V2,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentCommandV1,
    DirectedEffectParentBindingV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    GetDirectedEffectOperationQueryV1,
    GetDirectedEffectParentRegistryQueryV1,
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ParentCorrelationV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    admit_directed_effect_operation,
    admit_directed_effect_parent,
    enroll_directed_effect_operation_stream,
    enroll_directed_effect_parent_registry_stream,
    get_directed_effect_operation,
    get_directed_effect_parent_registry,
    heartbeat_task_runtime_execution_attempt,
)
from polaris.kernelone.storage import resolve_storage_roots

_PARENT_CLOSED_EVENT_TYPE = "task_runtime.deo_parent_registry.v1.closed"


def _attempt(workspace: Path) -> TaskRuntimeExecutionAttemptIdentityV1:
    workspace_abs = str(workspace.resolve())
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=workspace_abs,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="directed-effect-operation-test",
        )
    )
    service = TaskRuntimeService(workspace_abs)
    task_id = int(service.create_task_row(subject="directed effect operation")["id"])
    claimed = service.claim_execution(
        task_id,
        worker_id="deo-test-worker",
        role_id="director",
        run_id="deo-test-run",
        external_task_id="DEO-1B",
        selection_source="test",
    )
    return TaskRuntimeExecutionAttemptIdentityV1.from_record(claimed["execution_attempt"])


def _parent_command(identity: TaskRuntimeExecutionAttemptIdentityV1) -> AdmitDirectedEffectParentCommandV1:
    return AdmitDirectedEffectParentCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        correlation=ParentCorrelationV1(turn_id="turn-1", batch_id="batch-1"),
        admission_idempotency_key="parent-1",
        expected_version=0,
        expected_seq=1,
        actor="test-parent",
    )


def _operation_command(
    identity: TaskRuntimeExecutionAttemptIdentityV1,
    binding: DirectedEffectParentBindingV1,
    *,
    tool_call_id: str = "tool-1",
    effect_id: str = "effect-1",
    fingerprint: str = "fingerprint-1",
    expected_seq: int = 1,
) -> AdmitDirectedEffectOperationCommandV1:
    return AdmitDirectedEffectOperationCommandV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=tool_call_id,
        effect_id=effect_id,
        expected_version=0,
        expected_seq=expected_seq,
        actor="test-child",
        intended_effect_fingerprint=fingerprint,
        policy_verdict_hash="policy-1",
        expected_receipt_binding_hash="receipt-1",
    )


def _enroll_parent(identity: TaskRuntimeExecutionAttemptIdentityV1) -> None:
    result = enroll_directed_effect_parent_registry_stream(
        EnrollDirectedEffectParentRegistryStreamCommandV1(execution_attempt=identity)
    )
    assert result.code == "parent_registry_stream_enrolled"
    assert result.receipt is not None
    assert result.evidence["receipt_authoritative"] is False


def _admit_parent(identity: TaskRuntimeExecutionAttemptIdentityV1) -> DirectedEffectParentBindingV1:
    result = admit_directed_effect_parent(_parent_command(identity))
    assert result.code == "parent_admitted"
    assert result.parent_binding is not None
    return result.parent_binding


def _enroll_operation(identity: TaskRuntimeExecutionAttemptIdentityV1, binding: DirectedEffectParentBindingV1) -> None:
    result = enroll_directed_effect_operation_stream(
        EnrollDirectedEffectOperationStreamCommandV1(
            execution_attempt=identity,
            parent_binding=binding,
        )
    )
    assert result.code == "operation_stream_enrolled"
    assert result.parent_binding == binding
    assert result.receipt is not None
    assert result.evidence["receipt_authoritative"] is False


def _close_parent(binding: DirectedEffectParentBindingV1) -> None:
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=binding.workspace,
            stream=binding.registry_stream_token,
            event_type=_PARENT_CLOSED_EVENT_TYPE,
            payload={
                "schema_version": DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
                "stable_registry_identity": binding.registry_identity.to_record(),
                "previous_version": binding.registry_version,
                "version": binding.registry_version + 1,
                "parent_sequence": binding.parent_sequence,
                "binding_id": binding.binding_id,
                "close_evidence_ref": "fact://test/close",
                "close_evidence_hash": "a" * 64,
                "actor": "test-close",
                "recorded_at": "2026-07-15T00:00:00+00:00",
            },
            source="test",
            idempotency_key=f"close-{binding.binding_id}",
            expected_seq=binding.registry_version + 1,
            durability="fsync",
            strict_integrity=True,
        )
    )


def test_explicit_enrollment_order_fails_closed_without_implicit_maintenance(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    registry = get_directed_effect_parent_registry(
        GetDirectedEffectParentRegistryQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
        )
    )
    assert registry.code == "stream_lock_missing"
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    rejected = admit_directed_effect_operation(_operation_command(identity, binding))
    assert rejected.code == "stream_lock_missing"
    _enroll_operation(identity, binding)
    assert admit_directed_effect_operation(_operation_command(identity, binding)).code == "admitted"


_BINDING_MISMATCH_CASES = (
    ("schema_version", "parent_binding_conflict"),
    ("registry_identity.schema_version", "execution_attempt_mismatch"),
    ("registry_identity.workspace", "workspace_mismatch"),
    ("registry_identity.task_id", "task_mismatch"),
    ("registry_identity.external_task_id", "execution_attempt_mismatch"),
    ("registry_identity.session_id", "execution_attempt_mismatch"),
    ("registry_identity.attempt", "execution_attempt_mismatch"),
    ("registry_identity.role_id", "execution_attempt_mismatch"),
    ("registry_identity.worker_id", "execution_attempt_mismatch"),
    ("registry_identity.run_id", "execution_attempt_mismatch"),
    ("registry_stream_token", "parent_binding_conflict"),
    ("registry_version", "parent_binding_version_conflict"),
    ("parent_sequence", "parent_binding_version_conflict"),
    ("binding_id", "parent_binding_not_found"),
    ("operation_stream_token", "parent_binding_conflict"),
    ("binding_hash", "parent_binding_hash_mismatch"),
    ("admission_idempotency_key", "parent_admission_idempotency_conflict"),
    ("correlation.schema_version", "parent_binding_conflict"),
    ("correlation.turn_id", "turn_mismatch"),
    ("correlation.batch_id", "batch_mismatch"),
    ("actor", "parent_binding_conflict"),
    ("source_event_id", "parent_binding_event_conflict"),
    ("source_event_seq", "parent_binding_version_conflict"),
)


def _forge_binding_field(
    binding: DirectedEffectParentBindingV1,
    field_path: str,
) -> DirectedEffectParentBindingV1:
    forged = replace(binding)
    owner: object = forged
    field_name = field_path
    if field_path.startswith("registry_identity."):
        owner = replace(binding.registry_identity)
        field_name = field_path.removeprefix("registry_identity.")
        object.__setattr__(forged, "registry_identity", owner)
    elif field_path.startswith("correlation."):
        owner = replace(binding.correlation)
        field_name = field_path.removeprefix("correlation.")
        object.__setattr__(forged, "correlation", owner)
    current = getattr(owner, field_name)
    forged_value = current + 1 if isinstance(current, int) else f"{current}-forged"
    object.__setattr__(owner, field_name, forged_value)
    return forged


@pytest.mark.parametrize(("field_path", "expected_code"), _BINDING_MISMATCH_CASES)
def test_operation_enrollment_rejects_complete_binding_mismatch_before_maintenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_path: str,
    expected_code: str,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    maintenance_calls: list[object] = []

    def observe_operation_enrollment(command: object) -> None:
        maintenance_calls.append(command)
        raise AssertionError("binding mismatch reached the operation enrollment port")

    monkeypatch.setattr(
        deo_internal,
        "enroll_fact_stream_streams",
        observe_operation_enrollment,
    )
    forged = _forge_binding_field(binding, field_path)
    rejected = enroll_directed_effect_operation_stream(
        EnrollDirectedEffectOperationStreamCommandV1(execution_attempt=identity, parent_binding=forged)
    )
    assert rejected.ok is False
    assert rejected.code == expected_code
    assert rejected.receipt is None
    assert maintenance_calls == []


def test_v2_writer_and_historical_v1_exact_replay_are_schema_neutral(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    command = _operation_command(identity, binding)
    written = admit_directed_effect_operation(command)
    assert written.code == "admitted"
    assert written.evidence["authoritative_append"] is False
    assert written.evidence["authoritative_effect_receipt"] is True
    assert written.evidence["append_disposition"] == "committed_or_exact_replay"
    assert written.snapshot is not None
    assert written.snapshot.state == "INTENT_COMMITTED"
    assert written.snapshot.version == 1
    payload = query_fact_events(
        QueryFactEventsV1(workspace=identity.workspace, stream=binding.operation_stream_token, strict_integrity=True)
    ).events[0]["payload"]
    assert payload["schema_version"] == DIRECTED_EFFECT_OPERATION_SCHEMA_V2
    assert "recorded_at" not in payload
    assert (
        admit_directed_effect_operation(replace(command, expected_version=99, expected_seq=99)).code
        == "idempotent_replay"
    )

    second = _operation_command(
        identity,
        binding,
        tool_call_id="tool-v1",
        effect_id="effect-v1",
        expected_seq=2,
    )
    operation = deo_internal.DirectedEffectOperationRepository._derive_operation(second, binding)
    descriptor = deo_internal.DirectedEffectOperationRepository._operation_descriptor(second, kind="admit")
    historical = deo_internal.DirectedEffectOperationRepository._operation_event_canonical(
        operation=operation,
        state="INTENT_COMMITTED",
        previous_version=0,
        descriptor=descriptor,
    )
    historical["schema_version"] = DIRECTED_EFFECT_OPERATION_SCHEMA_V1
    historical["recorded_at"] = "2026-07-15T00:00:00+00:00"
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            event_type="task_runtime.directed_effect_operation.v1.intent_committed",
            payload=historical,
            source="test",
            idempotency_key="historical-v1",
            expected_seq=2,
            durability="fsync",
            strict_integrity=True,
        )
    )
    replay = admit_directed_effect_operation(second)
    assert replay.code == "idempotent_replay"
    assert replay.evidence["committed_seq"] == 2
    assert replay.evidence["authoritative_effect_receipt"] is False


def test_semantic_conflict_and_replay_after_parent_close(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    command = _operation_command(identity, binding)
    assert admit_directed_effect_operation(command).code == "admitted"
    assert (
        admit_directed_effect_operation(replace(command, intended_effect_fingerprint="changed")).code
        == "idempotency_semantic_conflict"
    )
    _close_parent(binding)
    assert admit_directed_effect_operation(command).code == "idempotent_replay"
    assert (
        admit_directed_effect_operation(
            _operation_command(identity, binding, tool_call_id="new-tool", effect_id="new-effect")
        ).code
        == "parent_closed"
    )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("unknown_schema", "strict_stream_unknown_schema"),
        ("missing_field", "strict_stream_corruption"),
        ("extra_field", "strict_stream_corruption"),
        ("v1_naive_timestamp", "strict_stream_corruption"),
    ],
)
def test_operation_parser_fails_closed_for_exact_v1_v2_shapes(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    workspace = tmp_path / mutation
    workspace.mkdir()
    identity = _attempt(workspace)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    command = _operation_command(identity, binding)
    operation = deo_internal.DirectedEffectOperationRepository._derive_operation(command, binding)
    descriptor = deo_internal.DirectedEffectOperationRepository._operation_descriptor(command, kind="admit")
    payload = deo_internal.DirectedEffectOperationRepository._operation_event_canonical(
        operation=operation,
        state="INTENT_COMMITTED",
        previous_version=0,
        descriptor=descriptor,
    )
    if mutation == "unknown_schema":
        payload["schema_version"] = "task-runtime.directed-effect-operation/99"
    elif mutation == "missing_field":
        del payload["state"]
    elif mutation == "extra_field":
        payload["recorded_at"] = "2026-07-15T00:00:00+00:00"
    else:
        payload["schema_version"] = DIRECTED_EFFECT_OPERATION_SCHEMA_V1
        payload["recorded_at"] = "2026-07-15T00:00:00"
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            event_type="task_runtime.directed_effect_operation.v1.intent_committed",
            payload=payload,
            source="test",
            idempotency_key=f"invalid-{mutation}",
            expected_seq=1,
            durability="fsync",
            strict_integrity=True,
        )
    )
    result = get_directed_effect_operation(
        GetDirectedEffectOperationQueryV1(
            workspace=identity.workspace,
            task_id=identity.task_id,
            execution_attempt=identity,
            parent_binding=binding,
            tool_call_id=command.tool_call_id,
            effect_id=command.effect_id,
        )
    )
    assert result.code == expected_code


def test_snapshot_projection_is_in_memory_only(tmp_path: Path) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    command = _operation_command(identity, binding)
    admitted = admit_directed_effect_operation(command)
    assert admitted.snapshot is not None
    assert admitted.snapshot.state == "INTENT_COMMITTED"
    query = GetDirectedEffectOperationQueryV1(
        workspace=identity.workspace,
        task_id=identity.task_id,
        execution_attempt=identity,
        parent_binding=binding,
        tool_call_id=command.tool_call_id,
        effect_id=command.effect_id,
    )
    assert get_directed_effect_operation(query).snapshot is not None
    runtime_root = Path(resolve_storage_roots(identity.workspace).runtime_root)
    assert not (runtime_root / "task_runtime" / "directed_effect_operation_v1").exists()


def test_non_drift_error_after_real_commit_reconciles_strict_durable_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)

    def fail_after_real_commit(receipt: object) -> None:
        del receipt
        heartbeat = heartbeat_task_runtime_execution_attempt(
            HeartbeatTaskRuntimeExecutionAttemptCommandV1(
                workspace=identity.workspace,
                identity=identity,
                lease_ttl_seconds=120,
                context_summary="invalidate original identity after durable append",
                lock_timeout_seconds=5.0,
            )
        )
        assert heartbeat.success is True
        raise FactStreamError(
            "simulated acknowledgement loss after fsync",
            code="append_write_failed",
            details={"boundary": "after_fsync"},
        )

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_commit",
        staticmethod(fail_after_real_commit),
    )
    result = admit_directed_effect_operation(_operation_command(identity, binding))

    assert result.code == "admitted"
    assert result.evidence["reconciled_after_guarded_error"] is True
    assert result.evidence["fact_stream_code"] == "append_write_failed"
    assert result.evidence["authoritative_append"] is False
    assert result.evidence["authoritative_effect_receipt"] is True
    assert result.evidence["append_disposition"] == "committed_or_exact_replay"
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert len(events) == 1
    assert result.evidence["event_id"] == events[0]["event_id"]
    assert result.evidence["appended_seq"] == events[0]["seq"]


def test_non_drift_error_without_durable_event_returns_typed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)

    def fail_before_commit(snapshot: object) -> None:
        del snapshot
        raise FactStreamError(
            "simulated failure before guarded append",
            code="append_write_failed",
            details={"boundary": "before_append"},
        )

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_prepare",
        staticmethod(fail_before_commit),
    )
    result = admit_directed_effect_operation(_operation_command(identity, binding))

    assert result.code == "stream_append_failed"
    assert result.evidence["reconciled_after_guarded_error"] is True
    assert result.evidence["fact_stream_code"] == "append_write_failed"
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert events == ()


def test_guarded_confirmation_fails_closed_on_receipt_or_semantic_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)
    command = _operation_command(identity, binding)
    captured: list[GuardedFactAppendedV1] = []

    def capture_receipt(receipt: GuardedFactAppendedV1) -> None:
        captured.append(receipt)

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_commit",
        staticmethod(capture_receipt),
    )
    assert admit_directed_effect_operation(command).code == "admitted"
    assert len(captured) == 1

    repository = deo_internal.DirectedEffectOperationRepository()
    operation = repository._derive_operation(command, binding)
    descriptor = repository._operation_descriptor(command, kind="admit")
    canonical = repository._operation_event_canonical(
        operation=operation,
        state="INTENT_COMMITTED",
        previous_version=0,
        descriptor=descriptor,
    )
    normalized = repository._normalized_transition(
        operation=operation,
        state="INTENT_COMMITTED",
        descriptor=descriptor,
    )
    prepared = repository._prepare_guarded_snapshot(command, binding.registry_identity)
    assert isinstance(prepared, GuardedFactSnapshotV1)
    idempotency_key = deo_internal._hash_token(normalized.to_record())
    guarded_command = AppendIfGuardedSnapshotCommandV1(
        snapshot_proof=prepared.proof,
        event=GuardedFactEventV1(
            event_type="task_runtime.directed_effect_operation.v1.intent_committed",
            source="runtime.task_runtime",
            payload=canonical,
            aggregate_id=str(command.task_id),
            correlation_id=idempotency_key,
        ),
        idempotency_key=idempotency_key,
    )
    receipt_mismatch = repository._confirm_guarded_append(
        command=command,
        operation=operation,
        kind="admit",
        target="INTENT_COMMITTED",
        canonical_event=canonical,
        normalized=normalized,
        expected_previous_version=0,
        guarded_attempt=1,
        receipt=replace(captured[0], event_id="forged-event-id"),
        guarded_command=guarded_command,
    )
    semantic_mismatch = repository._confirm_guarded_append(
        command=command,
        operation=operation,
        kind="admit",
        target="INTENT_COMMITTED",
        canonical_event={**canonical, "parent_binding_id": "forged-binding"},
        normalized=normalized,
        expected_previous_version=0,
        guarded_attempt=1,
        receipt=captured[0],
        guarded_command=guarded_command,
    )

    assert receipt_mismatch.code == "guarded_receipt_mismatch"
    assert receipt_mismatch.evidence["reason"] == "receipt_identity_mismatch"
    assert semantic_mismatch.code == "guarded_receipt_mismatch"
    assert semantic_mismatch.evidence["reason"] == "canonical_transition_not_unique"


@pytest.mark.parametrize(
    "tampered_field",
    (
        "event_id",
        "workspace",
        "stream",
        "storage_path",
        "appended_at",
        "appended_seq",
        "semantic_digest",
    ),
)
def test_public_guarded_confirmation_rejects_each_tampered_receipt_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampered_field: str,
) -> None:
    identity = _attempt(tmp_path)
    _enroll_parent(identity)
    binding = _admit_parent(identity)
    _enroll_operation(identity, binding)

    def tamper(receipt: GuardedFactAppendedV1) -> GuardedFactAppendedV1:
        replacements: dict[str, object] = {
            "event_id": "forged-event-id",
            "workspace": str((tmp_path / "forged-workspace").resolve()),
            "stream": "forged-stream",
            "storage_path": "/forged/storage/path.jsonl",
            "appended_at": "2099-01-01T00:00:00+00:00",
            "appended_seq": receipt.appended_seq + 1,
            "semantic_digest": "b" * 64,
        }
        return replace(receipt, **{tampered_field: replacements[tampered_field]})

    monkeypatch.setattr(
        deo_internal.DirectedEffectOperationRepository,
        "_after_guarded_commit",
        staticmethod(tamper),
    )
    result = admit_directed_effect_operation(_operation_command(identity, binding))

    assert result.code == "guarded_receipt_mismatch"
    assert result.evidence["reason"] == "receipt_identity_mismatch"
    assert result.evidence["receipt_drift_fields"] == (tampered_field,)
    assert "authoritative_effect_receipt" not in result.evidence
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=identity.workspace,
            stream=binding.operation_stream_token,
            strict_integrity=True,
        )
    ).events
    assert len(events) == 1
