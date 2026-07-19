from __future__ import annotations

import inspect
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from typing import TYPE_CHECKING, Callable, get_args, get_type_hints

import pytest
from polaris.cells.events.fact_stream.public import service as fact_stream_service_module
from polaris.cells.events.fact_stream.public.contracts import (
    AppendFactEventCommandV1,
    AppendIfGuardedSnapshotCommandV1,
    AppendSegmentedFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    EnrollFactStreamStreamsCommandV1,
    EnsureSegmentedFactLedgerCommandV1,
    FactStreamMaintenanceReceiptV1,
    ProvisionFactStreamLockAuthorityCommandV1,
    QuerySegmentedFactEventsV1,
    QuerySegmentedFactLedgerHeadV1,
)
from polaris.cells.events.fact_stream.public.service import (
    FactStreamError,
    FactStreamProvenanceV1,
    QueryFactEventsV1,
    QueryFactStreamHeadV1,
    append_fact_event,
    append_if_guarded_snapshot,
    append_segmented_fact_event,
    enroll_fact_stream_streams,
    ensure_segmented_fact_ledger,
    provision_fact_stream_lock_authority,
    query_fact_events,
    query_fact_stream_head,
    query_segmented_fact_events,
    query_segmented_fact_ledger_head,
)
from polaris.cells.events.fact_stream.public.workspace_bootstrap import bootstrap_fact_stream_workspace
from polaris.kernelone.events.sourcing import (
    EventEnvelope,
    ExpectedSequenceDriftError,
    IdempotencyConflictError,
    JsonlEventStore,
)

if TYPE_CHECKING:
    from pathlib import Path


def _annotation_references_maintenance_receipt(annotation: object) -> bool:
    """Return whether an input annotation admits a maintenance receipt DTO."""

    return annotation is FactStreamMaintenanceReceiptV1 or any(
        _annotation_references_maintenance_receipt(argument) for argument in get_args(annotation)
    )


def _bootstrap_workspace(workspace: Path, *streams: str) -> None:
    """Explicitly prepare the authority required by ordinary FactStream I/O."""

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=streams,
            maintenance_reason="test_owned_fact_stream_bootstrap",
        )
    )


@pytest.mark.parametrize(
    "reserved",
    [
        "roles.kernel.provider_attempts.factory.",
        "roles.kernel.provider_attempts.session.",
        "factory.role_evidence_authority.",
        "roles.kernel.provider_attempts.factory.run-one",
        "roles.kernel.provider_attempts.session.session-one",
        "factory.role_evidence_authority.run-one",
        "custom.segmented.audit",
    ],
)
@pytest.mark.parametrize("operation", ["provision", "enroll"])
@pytest.mark.parametrize("stream_shape", ["reserved_only", "mixed"])
def test_ordinary_maintenance_rejects_reserved_namespace_before_store_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reserved: str,
    operation: str,
    stream_shape: str,
) -> None:
    store_calls: list[tuple[object, ...]] = []

    def forbidden_store_access(*args: object, **kwargs: object) -> object:
        store_calls.append((*args, kwargs))
        raise AssertionError("ordinary segmented namespace reached maintenance storage")

    monkeypatch.setattr(fact_stream_service_module, "_maintenance_store", forbidden_store_access)
    streams = (reserved,) if stream_shape == "reserved_only" else ("task_runtime.execution", reserved)

    with pytest.raises(FactStreamError) as rejected:
        if operation == "provision":
            provision_fact_stream_lock_authority(
                ProvisionFactStreamLockAuthorityCommandV1(
                    workspace=str(tmp_path),
                    streams=streams,
                    maintenance_reason="reserved_namespace_preflight_test",
                )
            )
        else:
            enroll_fact_stream_streams(
                EnrollFactStreamStreamsCommandV1(
                    workspace=str(tmp_path),
                    streams=streams,
                    maintenance_reason="reserved_namespace_preflight_test",
                )
            )

    assert rejected.value.code == "segmented_stream_api_required"
    assert rejected.value.details == {"stream": reserved}
    assert store_calls == []


@pytest.mark.parametrize(
    "namespace_root",
    [
        "roles.kernel.provider_attempts.factory.",
        "roles.kernel.provider_attempts.session.",
        "factory.role_evidence_authority.",
    ],
)
def test_dedicated_segmented_api_rejects_exact_namespace_root(
    tmp_path: Path,
    namespace_root: str,
) -> None:
    with pytest.raises(FactStreamError) as rejected:
        ensure_segmented_fact_ledger(
            EnsureSegmentedFactLedgerCommandV1(
                workspace=str(tmp_path),
                logical_stream=namespace_root,
                maintenance_reason="dedicated_namespace_root_rejection_test",
            )
        )

    assert rejected.value.code == "segmented_stream_namespace_required"
    assert rejected.value.details == {"stream": namespace_root}


def test_rejected_mixed_provision_leaves_fresh_workspace_without_authority(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = "roles.kernel.provider_attempts.factory.run-one"

    with pytest.raises(FactStreamError) as rejected:
        provision_fact_stream_lock_authority(
            ProvisionFactStreamLockAuthorityCommandV1(
                workspace=str(workspace),
                streams=("task_runtime.execution", protected),
                maintenance_reason="mixed_provision_zero_effect_test",
            )
        )
    assert rejected.value.code == "segmented_stream_api_required"
    assert rejected.value.details == {"stream": protected}

    with pytest.raises(FactStreamError) as missing_authority:
        enroll_fact_stream_streams(
            EnrollFactStreamStreamsCommandV1(
                workspace=str(workspace),
                streams=("task_runtime.execution",),
                maintenance_reason="prove_rejected_provision_wrote_nothing",
            )
        )
    assert missing_authority.value.code == "lock_authority_missing"


def test_rejected_mixed_enrollment_has_zero_partial_stream_effect(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provision_fact_stream_lock_authority(
        ProvisionFactStreamLockAuthorityCommandV1(
            workspace=str(workspace),
            streams=(),
            maintenance_reason="mixed_enrollment_zero_effect_test",
        )
    )
    protected = "factory.role_evidence_authority.run-one"

    with pytest.raises(FactStreamError) as rejected:
        enroll_fact_stream_streams(
            EnrollFactStreamStreamsCommandV1(
                workspace=str(workspace),
                streams=("task_runtime.execution", protected),
                maintenance_reason="mixed_enrollment_zero_effect_test",
            )
        )
    assert rejected.value.code == "segmented_stream_api_required"
    assert rejected.value.details == {"stream": protected}

    with pytest.raises(FactStreamError) as missing_stream:
        query_fact_events(
            QueryFactEventsV1(
                workspace=str(workspace),
                stream="task_runtime.execution",
                strict_integrity=True,
            )
        )
    assert missing_stream.value.code == "stream_lock_missing"


def test_protected_segmented_namespace_rejects_ordinary_api(tmp_path: Path) -> None:
    protected = "roles.kernel.provider_attempts.factory.run-one"

    with pytest.raises(FactStreamError) as append_error:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(tmp_path),
                stream=protected,
                event_type="provider_attempt.started",
                source="test",
                payload={"provider_request_id": "req-1"},
            )
        )
    assert append_error.value.code == "segmented_stream_api_required"
    assert append_error.value.details == {"stream": protected}

    with pytest.raises(FactStreamError) as query_error:
        query_fact_events(QueryFactEventsV1(workspace=str(tmp_path), stream=protected))
    assert query_error.value.code == "segmented_stream_api_required"
    assert query_error.value.details == {"stream": protected}

    with pytest.raises(FactStreamError) as head_error:
        query_fact_stream_head(QueryFactStreamHeadV1(workspace=str(tmp_path), stream=protected))
    assert head_error.value.code == "segmented_stream_api_required"
    assert head_error.value.details == {"stream": protected}


def test_segmented_fact_ledger_roundtrip_uses_dynamic_enrolled_authority(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_workspace(workspace, "task_runtime.execution")
    logical_stream = "roles.kernel.provider_attempts.factory.run-one"

    ready = ensure_segmented_fact_ledger(
        EnsureSegmentedFactLedgerCommandV1(
            workspace=str(workspace),
            logical_stream=logical_stream,
            maintenance_reason="provider_attempt_run_start",
        )
    )
    assert ready.logical_stream == logical_stream
    assert ready.retention == "pinned_audit_no_delete"

    appended = append_segmented_fact_event(
        AppendSegmentedFactEventCommandV1(
            workspace=str(workspace),
            logical_stream=logical_stream,
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": "request-1"},
            idempotency_key="request-1:start",
            expected_global_seq=1,
        )
    )
    assert appended.global_seq == 1
    assert appended.event_hash

    replay = append_segmented_fact_event(
        AppendSegmentedFactEventCommandV1(
            workspace=str(workspace),
            logical_stream=logical_stream,
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": "request-1"},
            idempotency_key="request-1:start",
            require_idempotency_replay=True,
        )
    )
    assert replay.event_id == appended.event_id

    with pytest.raises(FactStreamError) as missing_replay:
        append_segmented_fact_event(
            AppendSegmentedFactEventCommandV1(
                workspace=str(workspace),
                logical_stream=logical_stream,
                event_type="provider_attempt.started",
                source="roles.kernel",
                payload={"provider_request_id": "request-missing"},
                idempotency_key="request-missing:start",
                require_idempotency_replay=True,
            )
        )
    assert missing_replay.value.code == "idempotency_replay_missing"

    head = query_segmented_fact_ledger_head(
        QuerySegmentedFactLedgerHeadV1(workspace=str(workspace), logical_stream=logical_stream)
    )
    assert head.global_seq == 1
    assert head.total_count == 1
    assert head.next_expected_global_seq == 2

    result = query_segmented_fact_events(
        QuerySegmentedFactEventsV1(workspace=str(workspace), logical_stream=logical_stream)
    )
    assert result.captured_head == head
    assert len(result.events) == 1
    assert result.events[0]["payload"]["provider_request_id"] == "request-1"


def test_segmented_fact_ledger_ensure_without_base_authority_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(FactStreamError) as missing_authority:
        ensure_segmented_fact_ledger(
            EnsureSegmentedFactLedgerCommandV1(
                workspace=str(workspace),
                logical_stream="roles.kernel.provider_attempts.factory.run-one",
                maintenance_reason="prove_dedicated_ensure_requires_base_authority",
            )
        )

    assert missing_authority.value.code == "lock_authority_missing"


def test_maintenance_receipts_are_non_authoritative_and_services_revalidate_physical_state(
    tmp_path: Path,
) -> None:
    """Receipt DTOs are outputs only; every effect boundary accepts commands."""

    command_types = (
        ProvisionFactStreamLockAuthorityCommandV1,
        EnrollFactStreamStreamsCommandV1,
        BootstrapFactStreamWorkspaceCommandV1,
        AppendFactEventCommandV1,
        AppendIfGuardedSnapshotCommandV1,
    )
    service_functions: tuple[Callable[..., object], ...] = (
        provision_fact_stream_lock_authority,
        enroll_fact_stream_streams,
        bootstrap_fact_stream_workspace,
        append_fact_event,
        append_if_guarded_snapshot,
    )

    for command_type in command_types:
        command_hints = get_type_hints(command_type)
        assert all(not _annotation_references_maintenance_receipt(annotation) for annotation in command_hints.values())
        assert all(field_info.name != "maintenance_receipt" for field_info in fields(command_type))

    for service_function in service_functions:
        parameter_hints = get_type_hints(service_function)
        assert tuple(inspect.signature(service_function).parameters) == ("command",)
        assert all(
            not _annotation_references_maintenance_receipt(annotation)
            for name, annotation in parameter_hints.items()
            if name != "return"
        )

    workspace = tmp_path / "workspace"
    authority_root = tmp_path / "authority"
    workspace.mkdir()
    receipt = provision_fact_stream_lock_authority(
        ProvisionFactStreamLockAuthorityCommandV1(
            workspace=str(workspace),
            maintenance_reason="receipt_non_authority_regression",
            platform_lock_root=str(authority_root),
        )
    )
    assert isinstance(receipt, FactStreamMaintenanceReceiptV1)

    shutil.rmtree(authority_root)
    with pytest.raises(FactStreamError):
        enroll_fact_stream_streams(
            EnrollFactStreamStreamsCommandV1(
                workspace=str(workspace),
                streams=("task_runtime.execution",),
                maintenance_reason="receipt_non_authority_regression",
                platform_lock_root=str(authority_root),
            )
        )


def test_append_fact_event_and_query_roundtrip(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace, "task_runtime.execution")

    appended = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            payload={"task_id": "task-1", "run_id": "run-1"},
            source="runtime.task_runtime",
            task_id="task-1",
            run_id="run-1",
        )
    )
    assert appended.workspace == str(workspace)
    assert appended.stream == "task_runtime.execution"
    assert appended.storage_path == "runtime/events/task_runtime.execution.jsonl"
    assert str(appended.event_id).strip()

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            limit=50,
            offset=0,
            task_id="task-1",
        )
    )
    assert queried.total == 1
    assert len(queried.events) == 1
    assert queried.events[0]["event_type"] == "claimed"
    assert queried.events[0]["task_id"] == "task-1"


def test_query_fact_stream_head_projects_next_expected_sequence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace, "task_runtime.execution")
    query = QueryFactStreamHeadV1(
        workspace=str(workspace),
        stream="task_runtime.execution",
    )

    empty_head = query_fact_stream_head(query)
    assert empty_head.current_seq == 0
    assert empty_head.next_expected_seq == 1
    assert empty_head.storage_path == "runtime/events/task_runtime.execution.jsonl"

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="created",
            payload={"task_id": "task-head"},
            source="runtime.task_runtime",
            task_id="task-head",
            expected_seq=empty_head.next_expected_seq,
        )
    )

    advanced_head = query_fact_stream_head(query)
    assert advanced_head.current_seq == 1
    assert advanced_head.next_expected_seq == 2


def test_stream_enrollment_projects_verified_lock_key_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provision_fact_stream_lock_authority(
        ProvisionFactStreamLockAuthorityCommandV1(
            workspace=str(workspace),
            streams=(),
            maintenance_reason="test_verified_key_identity",
        )
    )

    receipt = enroll_fact_stream_streams(
        EnrollFactStreamStreamsCommandV1(
            workspace=str(workspace),
            streams=("task_runtime.execution",),
            maintenance_reason="test_verified_key_identity",
        )
    )

    proof = receipt.proofs[0]
    key = proof.lock_keys[0]
    assert proof.final_validation is True
    assert key.identity.device >= 0
    assert key.identity.inode >= 1


def test_append_fact_event_is_idempotent_by_key(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace, "task_runtime.execution")

    command = AppendFactEventCommandV1(
        workspace=str(workspace),
        stream="task_runtime.execution",
        event_type="claimed",
        payload={"task_id": "task-idem", "run_id": "run-idem"},
        source="runtime.task_runtime",
        task_id="task-idem",
        run_id="run-idem",
        idempotency_key="outbox-idem-1",
    )

    first = append_fact_event(command)
    second = append_fact_event(command)

    assert second.event_id == first.event_id

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            limit=50,
            offset=0,
            task_id="task-idem",
        )
    )
    assert queried.total == 1


def test_append_fact_event_records_typed_transition_provenance(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_workspace(workspace, "roles.kernel.turn_outcomes")
    provenance = FactStreamProvenanceV1(
        workspace=str(workspace.resolve()),
        run_id="run-1",
        task_id="task-1",
        turn_id="turn-1",
        transition_id="transition-1",
    )

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="roles.kernel.turn_outcomes",
            event_type="turn_outcome_committed",
            payload={"provenance": provenance.to_record(), "outcome": {"status": "completed"}},
            source="roles.kernel.transaction",
            run_id="run-1",
            task_id="task-1",
            provenance=provenance,
            idempotency_key="turn-outcome:transition-1",
        )
    )

    event = query_fact_events(
        QueryFactEventsV1(workspace=str(workspace), stream="roles.kernel.turn_outcomes", limit=10)
    ).events[0]
    assert event["metadata"]["provenance"] == provenance.to_record()
    assert event["metadata"]["storage_identity"]["workspace_abs"] == str(workspace.resolve())


def test_append_fact_event_rejects_provenance_from_another_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    other_workspace = tmp_path / "other-workspace"
    workspace.mkdir()
    other_workspace.mkdir()
    _bootstrap_workspace(workspace, "roles.kernel.turn_outcomes")
    provenance = FactStreamProvenanceV1(
        workspace=str(other_workspace.resolve()),
        run_id="run-1",
        task_id="task-1",
        turn_id="turn-1",
        transition_id="transition-1",
    )

    with pytest.raises(FactStreamError, match="provenance workspace"):
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="roles.kernel.turn_outcomes",
                event_type="turn_outcome_committed",
                payload={"provenance": provenance.to_record()},
                source="roles.kernel.transaction",
                provenance=provenance,
            )
        )


@pytest.mark.parametrize(
    ("command_run_id", "command_task_id", "mismatch_field"),
    [
        ("run-stale", "task-1", "run_id"),
        ("run-1", "task-stale", "task_id"),
    ],
)
def test_append_fact_event_rejects_contradictory_provenance_identity(
    tmp_path: Path,
    command_run_id: str,
    command_task_id: str,
    mismatch_field: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_workspace(workspace, "roles.kernel.turn_outcomes")
    provenance = FactStreamProvenanceV1(
        workspace=str(workspace.resolve()),
        run_id="run-1",
        task_id="task-1",
        turn_id="turn-1",
        transition_id="transition-1",
    )

    with pytest.raises(FactStreamError) as exc_info:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="roles.kernel.turn_outcomes",
                event_type="turn_outcome_committed",
                payload={"provenance": provenance.to_record()},
                source="roles.kernel.transaction",
                run_id=command_run_id,
                task_id=command_task_id,
                provenance=provenance,
            )
        )

    assert exc_info.value.code == "provenance_mismatch"
    assert exc_info.value.details["fields"] == (mismatch_field,)
    assert (
        query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="roles.kernel.turn_outcomes")).total == 0
    )


def test_append_fact_event_promotes_provenance_when_optional_command_identity_is_empty(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_workspace(workspace, "roles.kernel.turn_outcomes")
    provenance = FactStreamProvenanceV1(
        workspace=str(workspace.resolve()),
        run_id="run-from-provenance",
        task_id="task-from-provenance",
        turn_id="turn-1",
        transition_id="transition-1",
    )

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="roles.kernel.turn_outcomes",
            event_type="turn_outcome_committed",
            payload={"provenance": provenance.to_record()},
            source="roles.kernel.transaction",
            run_id=" ",
            task_id=None,
            provenance=provenance,
        )
    )

    event = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="roles.kernel.turn_outcomes")).events[
        0
    ]
    assert event["metadata"]["run_id"] == provenance.run_id
    assert event["metadata"]["task_id"] == provenance.task_id
    assert event["aggregate_id"] == provenance.task_id


def test_query_fact_events_pagination(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace, "taskboard.terminal.events")

    for idx in range(3):
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="taskboard.terminal.events",
                event_type="completed",
                payload={"task_id": f"task-{idx}"},
                source="runtime.task_runtime.task_board",
                task_id=f"task-{idx}",
            )
        )

    first_page = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="taskboard.terminal.events",
            limit=2,
            offset=0,
        )
    )
    assert first_page.total == 3
    assert len(first_page.events) == 2
    assert first_page.next_offset == 2

    second_page = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="taskboard.terminal.events",
            limit=2,
            offset=first_page.next_offset,
        )
    )
    assert second_page.total == 3
    assert len(second_page.events) == 1
    assert second_page.next_offset == 0


def test_append_fact_event_default_expected_seq_is_none_and_assigns_seq_one(
    tmp_path: Path,
) -> None:
    """Default append behaviour is unchanged: no expected_seq → next free seq."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace, "task_runtime.execution")

    appended = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            payload={"task_id": "task-default", "run_id": "run-default"},
            source="runtime.task_runtime",
            task_id="task-default",
            run_id="run-default",
        )
    )

    # appended_seq is filled in by the service for callers that care; old
    # callers that don't read it must still work.
    assert appended.appended_seq == 1
    assert appended.event_id

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            limit=10,
            offset=0,
        )
    )
    assert queried.total == 1


def test_append_fact_event_expected_seq_match_succeeds(tmp_path: Path) -> None:
    """CAS path: caller supplies expected_seq matching next free → append lands."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace, "ledger.expected_seq")

    first = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="ledger.expected_seq",
            event_type="claimed",
            payload={"task_id": "task-cas", "run_id": "run-cas"},
            source="runtime.task_runtime",
            task_id="task-cas",
            run_id="run-cas",
        )
    )
    assert first.appended_seq == 1

    second = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="ledger.expected_seq",
            event_type="completed",
            payload={"task_id": "task-cas", "run_id": "run-cas"},
            source="runtime.task_runtime",
            task_id="task-cas",
            run_id="run-cas",
            expected_seq=2,
        )
    )
    assert second.appended_seq == 2

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="ledger.expected_seq",
            limit=10,
            offset=0,
        )
    )
    assert queried.total == 2
    assert [evt["seq"] for evt in queried.events] == [1, 2]


def test_append_fact_event_expected_seq_drift_fails_closed_and_does_not_append(
    tmp_path: Path,
) -> None:
    """CAS drift must raise FactStreamError and not produce any new event."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace, "ledger.expected_seq.drift")

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="ledger.expected_seq.drift",
            event_type="claimed",
            payload={"task_id": "task-drift", "run_id": "run-drift"},
            source="runtime.task_runtime",
            task_id="task-drift",
            run_id="run-drift",
        )
    )

    # Stream already holds seq=1, so expected_seq=99 must fail-closed.
    with pytest.raises(FactStreamError) as exc_info:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="ledger.expected_seq.drift",
                event_type="completed",
                payload={"task_id": "task-drift", "run_id": "run-drift"},
                source="runtime.task_runtime",
                task_id="task-drift",
                run_id="run-drift",
                expected_seq=99,
            )
        )

    assert exc_info.value.code == "expected_seq_drift"
    assert exc_info.value.details.get("expected_seq") == 99
    assert isinstance(exc_info.value.__cause__, ExpectedSequenceDriftError)
    assert exc_info.value.__cause__.code == "expected_seq_drift"

    # Crucially: no second event was written.
    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="ledger.expected_seq.drift",
            limit=10,
            offset=0,
        )
    )
    assert queried.total == 1
    assert queried.events[0]["event_type"] == "claimed"


def test_append_fact_event_idempotent_hit_with_mismatched_expected_seq_fails(
    tmp_path: Path,
) -> None:
    """Idempotent hit + CAS drift must fail-closed instead of silently
    returning the original event.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace, "ledger.expected_seq.idem")

    command = AppendFactEventCommandV1(
        workspace=str(workspace),
        stream="ledger.expected_seq.idem",
        event_type="claimed",
        payload={"task_id": "task-idem-cas", "run_id": "run-idem-cas"},
        source="runtime.task_runtime",
        task_id="task-idem-cas",
        run_id="run-idem-cas",
        idempotency_key="idem-key-cas-1",
    )

    first = append_fact_event(command)
    assert first.appended_seq == 1

    # Replay with mismatched expected_seq must fail-closed.
    with pytest.raises(FactStreamError) as exc_info:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="ledger.expected_seq.idem",
                event_type="claimed",
                payload={"task_id": "task-idem-cas", "run_id": "run-idem-cas"},
                source="runtime.task_runtime",
                task_id="task-idem-cas",
                run_id="run-idem-cas",
                idempotency_key="idem-key-cas-1",
                expected_seq=42,
            )
        )

    assert exc_info.value.code == "expected_seq_drift"
    assert exc_info.value.details.get("existing_seq") == 1
    assert exc_info.value.details.get("expected_seq") == 42
    assert isinstance(exc_info.value.__cause__, ExpectedSequenceDriftError)
    assert exc_info.value.__cause__.code == "expected_seq_drift"

    # Confirm we did NOT write a duplicate event.
    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="ledger.expected_seq.idem",
            limit=10,
            offset=0,
        )
    )
    assert queried.total == 1


def test_append_fact_event_idempotent_hit_with_matching_expected_seq_succeeds(
    tmp_path: Path,
) -> None:
    """Idempotent hit + matching expected_seq must return the original
    event and not produce a duplicate write.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace, "ledger.expected_seq.idem.match")

    first = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="ledger.expected_seq.idem.match",
            event_type="claimed",
            payload={"task_id": "task-idem-match", "run_id": "run-idem-match"},
            source="runtime.task_runtime",
            task_id="task-idem-match",
            run_id="run-idem-match",
            idempotency_key="idem-key-match-1",
            expected_seq=1,
        )
    )
    assert first.appended_seq == 1

    # Same idempotency key + matching expected_seq → idempotent return.
    second = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="ledger.expected_seq.idem.match",
            event_type="claimed",
            payload={"task_id": "task-idem-match", "run_id": "run-idem-match"},
            source="runtime.task_runtime",
            task_id="task-idem-match",
            run_id="run-idem-match",
            idempotency_key="idem-key-match-1",
            expected_seq=1,
        )
    )
    assert second.event_id == first.event_id
    assert second.appended_seq == 1

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="ledger.expected_seq.idem.match",
            limit=10,
            offset=0,
        )
    )
    assert queried.total == 1


def test_append_fact_event_concurrent_idempotency_is_atomic(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace, "roles.kernel.turn_outcomes")
    command = AppendFactEventCommandV1(
        workspace=str(workspace),
        stream="roles.kernel.turn_outcomes",
        event_type="turn_outcome_committed",
        payload={"run_id": "run-atomic", "turn_id": "turn-atomic"},
        source="roles.kernel",
        run_id="run-atomic",
        task_id="task-atomic",
        idempotency_key="run-atomic:task-atomic:turn-atomic",
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: append_fact_event(command), range(16)))

    assert len({result.event_id for result in results}) == 1
    assert {result.appended_seq for result in results} == {1}
    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="roles.kernel.turn_outcomes",
            limit=20,
        )
    )
    assert queried.total == 1


def test_append_fact_event_rejects_idempotency_key_payload_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace, "roles.kernel.turn_outcomes")
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="roles.kernel.turn_outcomes",
            event_type="turn_outcome_committed",
            source="roles.kernel",
            run_id="run-conflict",
            task_id="task-conflict",
            idempotency_key="run-conflict:task-conflict:turn-conflict",
            payload={"run_id": "run-conflict", "outcome_status": "completed"},
        )
    )

    with pytest.raises(FactStreamError) as exc_info:
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="roles.kernel.turn_outcomes",
                event_type="turn_outcome_committed",
                source="roles.kernel",
                run_id="run-conflict",
                task_id="task-conflict",
                idempotency_key="run-conflict:task-conflict:turn-conflict",
                payload={"run_id": "run-conflict", "outcome_status": "failed"},
            )
        )

    assert exc_info.value.code == "idempotency_conflict"
    assert isinstance(exc_info.value.__cause__, IdempotencyConflictError)
    assert exc_info.value.__cause__.code == "idempotency_conflict"


def test_strict_fact_stream_public_append_and_query_preserve_utf8(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_workspace(workspace, "strict.public")

    appended = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="strict.public",
            event_type="recorded",
            source="test",
            payload={"message": "中文"},
            expected_seq=1,
            durability="fsync",
            strict_integrity=True,
        )
    )

    queried = query_fact_events(
        QueryFactEventsV1(workspace=str(workspace), stream="strict.public", strict_integrity=True)
    )
    head = query_fact_stream_head(
        QueryFactStreamHeadV1(workspace=str(workspace), stream="strict.public", strict_integrity=True)
    )

    assert appended.appended_seq == 1
    assert queried.events[0]["payload"]["message"] == "中文"
    assert head.next_expected_seq == 2


def test_strict_query_returns_exact_envelope_and_preserves_stored_digest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_workspace(workspace, "strict.exact.public")

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="strict.exact.public",
            event_type="recorded",
            source="test",
            run_id="run-exact",
            task_id="task-exact",
            payload={"run_id": "run-exact", "message": "canonical"},
            durability="fsync",
            strict_integrity=True,
        )
    )

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="strict.exact.public",
            strict_integrity=True,
        )
    )
    record = queried.events[0]
    store = JsonlEventStore(str(workspace))
    path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.exact.public"))
    stored = json.loads(path.read_text(encoding="utf-8"))

    assert "run_id" not in record
    assert "task_id" not in record
    assert record["metadata"]["run_id"] == "run-exact"
    assert record["metadata"]["task_id"] == "task-exact"
    assert record == stored
    assert record["integrity_digest"]
    assert EventEnvelope.integrity_digest_for_record(record) == record["integrity_digest"]


def test_non_strict_query_keeps_compatibility_run_and_task_fields(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_workspace(workspace, "compat.public")

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="compat.public",
            event_type="recorded",
            source="test",
            run_id="run-compat",
            task_id="task-compat",
            payload={"message": "compatibility"},
        )
    )

    queried = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="compat.public"))

    assert queried.events[0]["run_id"] == "run-compat"
    assert queried.events[0]["task_id"] == "task-compat"


def test_strict_query_rejects_non_strict_record_without_digest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_workspace(workspace, "strict.missing-digest.public")
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="strict.missing-digest.public",
            event_type="recorded",
            source="test",
            payload={"message": "legacy"},
        )
    )

    with pytest.raises(FactStreamError) as caught:
        query_fact_events(
            QueryFactEventsV1(
                workspace=str(workspace),
                stream="strict.missing-digest.public",
                strict_integrity=True,
            )
        )

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_failure_code"] == "missing_integrity_digest"


def test_strict_fact_stream_public_query_exposes_torn_tail_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_workspace(workspace, "strict.torn.public")
    store = JsonlEventStore(str(workspace))
    path = store._kernel_fs.resolve_path(store.stream_logical_path("strict.torn.public"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"schema_version":1', encoding="utf-8")

    with pytest.raises(FactStreamError) as caught:
        query_fact_events(
            QueryFactEventsV1(
                workspace=str(workspace),
                stream="strict.torn.public",
                strict_integrity=True,
            )
        )

    assert caught.value.code == "strict_stream_corruption"
    assert caught.value.details["strict_failure_code"] == "torn_tail"
    assert caught.value.details["recovery_required"] is True
