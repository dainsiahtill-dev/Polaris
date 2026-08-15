"""A009B2b-1 canonical Factory role-evidence source resolver tests."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
from polaris.cells.control_plane.run_ledger.public import RunLedger
from polaris.cells.events.fact_stream.public import FactStreamQueryResultV1, QueryFactEventsV1
from polaris.cells.factory.pipeline.internal.factory_event_chain import (
    FactoryRunAdmissionV1,
    build_factory_run_admitted_event,
    build_next_factory_event_record,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityError,
    FactoryRoleEvidenceStageAuthorityV1,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_source_resolver import (
    CanonicalFactoryRoleEvidenceSourceAuthority,
)
from polaris.cells.factory.pipeline.internal.factory_run_models import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
)
from polaris.cells.factory.pipeline.internal.factory_stage_artifact_bindings import (
    build_chief_engineer_stage_artifact_bindings,
    build_pm_stage_artifact_bindings,
)
from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore
from polaris.cells.factory.pipeline.tests.test_factory_role_evidence_provenance import (
    _blueprint,
    _pm_document,
    _pm_task,
    _review_document,
    _review_row,
    _write_json,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
    FactoryRoleEvidenceCutoffRequestV1,
)
from polaris.kernelone.events.sourcing.models import EventEnvelope


def _run(run_id: str = "factory-run-1") -> FactoryRun:
    return FactoryRun(
        id=run_id,
        config=FactoryConfig(name="Canonical source audit", description="Frozen PM intent"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-07-19T00:00:00+00:00",
    )


def _admission_chain(run: FactoryRun) -> tuple[dict[str, object], ...]:
    event = build_factory_run_admitted_event(
        FactoryRunAdmissionV1(
            factory_run_id=run.id,
            created_at=run.created_at,
            name=run.config.name,
            description=run.config.description,
        )
    )
    event.update(
        {
            "run_id": run.id,
            "event_id": "evt-admission",
            "timestamp": run.created_at,
        }
    )
    return (build_next_factory_event_record((), run_id=run.id, event=event),)


def _request(
    *,
    run_id: str = "controlled-child-run",
    role: str = "pm",
) -> FactoryRoleEvidenceCutoffRequestV1:
    return FactoryRoleEvidenceCutoffRequestV1(
        schema_version=FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
        run_id=run_id,
        role=role,
        turn_id="turn-pm-1",
        call_id="call-pm-1",
        request_freeze_id="freeze-pm-1",
        semantic_candidate_hash=hashlib.sha256(b"candidate").hexdigest(),
        attempt_budget=2,
        execution_authority_hash=hashlib.sha256(b"authority").hexdigest(),
        candidate_refs=(),
    )


def _authority(run_id: str = "factory-run-1") -> FactoryRoleEvidenceStageAuthorityV1:
    return FactoryRoleEvidenceStageAuthorityV1(
        factory_run_id=run_id,
        stage="pm_planning",
        workspace_fencing_token=7,
        stage_claim_attempt=1,
        stage_claim_nonce="claim-pm-1",
    )


def _resolver(tmp_path: Path, run: FactoryRun) -> CanonicalFactoryRoleEvidenceSourceAuthority:
    events = _admission_chain(run)

    def load_factory_events(factory_run_id: str) -> tuple[dict[str, object], ...]:
        assert factory_run_id == run.id
        return events

    def dynamic_query_forbidden(_query: object) -> object:
        raise AssertionError("PM policy has no dynamic source slots")

    return CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=tmp_path,
        factory_store=FactoryStore(tmp_path / "runtime"),
        factory_event_loader=load_factory_events,
        fact_query=dynamic_query_forbidden,
    )


def test_pm_cut_uses_strict_admission_and_frozen_factory_run_not_child_run(tmp_path: Path) -> None:
    run = _run()
    resolver = _resolver(tmp_path, run)

    first = resolver.resolve_source_cut(
        request=_request(run_id="child-a"),
        authority=_authority(run.id),
        factory_run=run,
    )
    second = resolver.resolve_source_cut(
        request=_request(run_id="malicious-other-factory-run"),
        authority=_authority(run.id),
        factory_run=run,
    )

    assert first == second
    assert first.role == "pm"
    assert tuple(slot.ref_kind for slot in first.slots) == ("pm_raw_intent",)
    slot = first.slots[0]
    assert slot.state == "present"
    assert slot.source_head.source_head_sequence == 1
    assert slot.items[0].source_fact_id == "evt-admission"
    assert slot.items[0].canonical_hash == _admission_chain(run)[0]["canonical_sha256"]
    run_digest = hashlib.sha256(run.id.encode("utf-8")).hexdigest()
    assert slot.source_head.canonical_source_ref == (f"factory.role_evidence.source.{run_digest}.pm_raw_intent.v1")


def test_resolver_rejects_authority_factory_run_drift(tmp_path: Path) -> None:
    run = _run()
    resolver = _resolver(tmp_path, run)

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        resolver.resolve_source_cut(
            request=_request(),
            authority=_authority("factory-run-2"),
            factory_run=run,
        )

    assert exc_info.value.code == "factory_role_evidence_source_factory_run_mismatch"


def test_resolver_rejects_mutable_run_identity_drift(tmp_path: Path) -> None:
    admitted = _run()
    drifted = _run()
    drifted.config.name = "Mutable run.json overwrite"
    resolver = _resolver(tmp_path, admitted)

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        resolver.resolve_source_cut(
            request=_request(),
            authority=_authority(drifted.id),
            factory_run=drifted,
        )

    assert exc_info.value.code == "factory_role_evidence_source_admission_drift"


def _control_plane_record(
    tmp_path: Path,
    *,
    seq: int,
    factory_run_id: str,
    stage: str,
    ok: bool,
    physical_evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    child_run_id = "child-run-1"
    nested = RunLedger(tmp_path, run_id=child_run_id).prepare_idempotent_event(
        {
            "event_type": "gate_evaluated",
            "run_id": child_run_id,
            "stage": stage,
            "job_token": {
                "run_id": child_run_id,
                "factory_run_id": factory_run_id,
                "stage": stage,
            },
            "gate": {"ok": ok},
            "physical_evidence": dict(physical_evidence or {}),
        }
    )
    envelope = EventEnvelope(
        event_id=f"fact-{seq}",
        stream="execution.control_plane",
        event_type="gate_evaluated",
        event_version=1,
        seq=seq,
        occurred_at=f"2026-07-19T00:{seq // 60:02d}:{seq % 60:02d}+00:00",
        source="control_plane.run_ledger",
        payload={
            "schema_version": "execution.control_plane.fact.v1",
            "run_id": child_run_id,
            "event": nested,
        },
        metadata={},
    )
    return envelope.to_record(include_integrity_digest=True)


def _control_plane_non_gate_record(
    tmp_path: Path,
    *,
    seq: int,
    event_type: str,
    stage: str,
) -> dict[str, object]:
    child_run_id = "child-run-1"
    nested = RunLedger(tmp_path, run_id=child_run_id).prepare_idempotent_event(
        {
            "event_type": event_type,
            "run_id": child_run_id,
            "stage": stage,
        }
    )
    envelope = EventEnvelope(
        event_id=f"fact-{seq}",
        stream="execution.control_plane",
        event_type=event_type,
        event_version=1,
        seq=seq,
        occurred_at=f"2026-07-19T00:{seq // 60:02d}:{seq % 60:02d}+00:00",
        source="control_plane.run_ledger",
        payload={
            "schema_version": "execution.control_plane.fact.v1",
            "run_id": child_run_id,
            "event": nested,
        },
        metadata={},
    )
    return envelope.to_record(include_integrity_digest=True)


def test_dynamic_views_use_one_exact_unfiltered_query_and_share_physical_head(tmp_path: Path) -> None:
    run = _run()
    records = (
        _control_plane_record(
            tmp_path,
            seq=1,
            factory_run_id=run.id,
            stage="workspace_validation",
            ok=False,
            physical_evidence={"command_count": 1},
        ),
        _control_plane_record(
            tmp_path,
            seq=2,
            factory_run_id=run.id,
            stage="qa",
            ok=True,
            physical_evidence={"command_receipts": [{"receipt_id": "receipt-1"}]},
        ),
    )
    queries: list[QueryFactEventsV1] = []

    def query(query_value: QueryFactEventsV1) -> FactStreamQueryResultV1:
        queries.append(query_value)
        return FactStreamQueryResultV1(
            workspace=str(tmp_path.resolve()),
            stream="execution.control_plane",
            events=records,
            total=2,
            next_offset=0,
        )

    resolver = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=tmp_path,
        factory_store=FactoryStore(tmp_path / "runtime"),
        factory_event_loader=lambda _run_id: _admission_chain(run),
        fact_query=query,
    )

    slots = resolver._capture_dynamic_slots(factory_run_id=run.id)

    assert len(queries) == 1
    assert queries[0] == QueryFactEventsV1(
        workspace=str(tmp_path.resolve()),
        stream="execution.control_plane",
        offset=0,
        limit=4096,
        event_type=None,
        run_id=None,
        task_id=None,
        strict_integrity=True,
    )
    assert set(slots) == {"failure_feedback", "workspace_quality", "verifier_receipts"}
    heads = {
        (slot.source_head.source_head_fact_id, slot.source_head.source_head_sequence, slot.source_head.source_head_hash)
        for slot in slots.values()
    }
    assert len(heads) == 1
    assert next(iter(heads))[:2] == ("fact-2", 2)
    assert slots["failure_feedback"].state == "present"
    assert slots["workspace_quality"].state == "present"
    assert slots["verifier_receipts"].state == "present"
    assert len({slot.source_head.canonical_source_ref for slot in slots.values()}) == 3


def test_dynamic_failure_feedback_keeps_latest_32_items(tmp_path: Path) -> None:
    """Live L2-11: 33+ failed gates made Slot.__post_init__ raise ValueError."""

    run = _run()
    records = tuple(
        _control_plane_record(
            tmp_path,
            seq=index,
            factory_run_id=run.id,
            stage="qa",
            ok=False,
        )
        for index in range(1, 34)
    )

    def query(query_value: QueryFactEventsV1) -> FactStreamQueryResultV1:
        del query_value
        return FactStreamQueryResultV1(
            workspace=str(tmp_path.resolve()),
            stream="execution.control_plane",
            events=records,
            total=len(records),
            next_offset=0,
        )

    resolver = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=tmp_path,
        factory_store=FactoryStore(tmp_path / "runtime"),
        factory_event_loader=lambda _run_id: _admission_chain(run),
        fact_query=query,
    )

    slots = resolver._capture_dynamic_slots(factory_run_id=run.id)
    assert slots["failure_feedback"].state == "present"
    assert len(slots["failure_feedback"].items) == 32
    assert slots["failure_feedback"].items[0].source_fact_sequence == 2
    assert slots["failure_feedback"].items[-1].source_fact_sequence == 33


def test_dynamic_views_validate_mixed_control_plane_stream_and_project_only_gate_events(tmp_path: Path) -> None:
    run = _run()
    records = (
        _control_plane_non_gate_record(
            tmp_path,
            seq=1,
            event_type="tool_call_lifecycle",
            stage="tool_batch",
        ),
        _control_plane_non_gate_record(
            tmp_path,
            seq=2,
            event_type="task_boundary_verdict",
            stage="task_boundary",
        ),
        _control_plane_record(
            tmp_path,
            seq=3,
            factory_run_id=run.id,
            stage="workspace_validation",
            ok=False,
            physical_evidence={},
        ),
    )

    slots = _dynamic_resolver(tmp_path, run, records)._capture_dynamic_slots(factory_run_id=run.id)

    assert {
        (slot.source_head.source_head_fact_id, slot.source_head.source_head_sequence) for slot in slots.values()
    } == {("fact-3", 3)}
    assert slots["failure_feedback"].state == "present"
    assert slots["workspace_quality"].state == "present"
    assert tuple(item.source_fact_sequence for item in slots["failure_feedback"].items) == (3,)
    assert slots["verifier_receipts"].state == "absent_at_request_time"


def test_dynamic_non_gate_outer_nested_event_type_drift_fails_closed(tmp_path: Path) -> None:
    run = _run()
    record = deepcopy(
        _control_plane_non_gate_record(
            tmp_path,
            seq=1,
            event_type="tool_call_lifecycle",
            stage="tool_batch",
        )
    )
    record["event_type"] = "task_boundary_verdict"
    record["integrity_digest"] = EventEnvelope.integrity_digest_for_record(record)

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        _dynamic_resolver(tmp_path, run, (record,))._capture_dynamic_slots(factory_run_id=run.id)

    assert exc_info.value.code == "factory_role_evidence_dynamic_envelope_invalid"


@pytest.mark.parametrize(
    ("total", "next_offset", "code"),
    [
        (2, 0, "factory_role_evidence_dynamic_total_mismatch"),
        (1, 1, "factory_role_evidence_dynamic_pagination_invalid"),
    ],
)
def test_dynamic_views_fail_closed_on_truncation(
    tmp_path: Path,
    total: int,
    next_offset: int,
    code: str,
) -> None:
    run = _run()
    record = _control_plane_record(
        tmp_path,
        seq=1,
        factory_run_id=run.id,
        stage="qa",
        ok=True,
        physical_evidence={},
    )
    resolver = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=tmp_path,
        factory_store=FactoryStore(tmp_path / "runtime"),
        factory_event_loader=lambda _run_id: _admission_chain(run),
        fact_query=lambda _query: FactStreamQueryResultV1(
            workspace=str(tmp_path.resolve()),
            stream="execution.control_plane",
            events=(record,),
            total=total,
            next_offset=next_offset,
        ),
    )

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        resolver._capture_dynamic_slots(factory_run_id=run.id)

    assert exc_info.value.code == code


def test_dynamic_empty_selected_views_bind_real_nonempty_physical_head(tmp_path: Path) -> None:
    run = _run()
    record = _control_plane_record(
        tmp_path,
        seq=1,
        factory_run_id=run.id,
        stage="qa",
        ok=True,
        physical_evidence={},
    )
    resolver = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=tmp_path,
        factory_store=FactoryStore(tmp_path / "runtime"),
        factory_event_loader=lambda _run_id: _admission_chain(run),
        fact_query=lambda _query: FactStreamQueryResultV1(
            workspace=str(tmp_path.resolve()),
            stream="execution.control_plane",
            events=(record,),
            total=1,
            next_offset=0,
        ),
    )

    slots = resolver._capture_dynamic_slots(factory_run_id=run.id)

    assert all(slot.state == "absent_at_request_time" for slot in slots.values())
    assert all(slot.items == () for slot in slots.values())
    assert {
        (slot.source_head.source_head_fact_id, slot.source_head.source_head_sequence) for slot in slots.values()
    } == {("fact-1", 1)}


def test_dynamic_truly_empty_stream_uses_zero_head_absence_proof(tmp_path: Path) -> None:
    run = _run()
    resolver = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=tmp_path,
        factory_store=FactoryStore(tmp_path / "runtime"),
        factory_event_loader=lambda _run_id: _admission_chain(run),
        fact_query=lambda _query: FactStreamQueryResultV1(
            workspace=str(tmp_path.resolve()),
            stream="execution.control_plane",
            events=(),
            total=0,
            next_offset=0,
        ),
    )

    slots = resolver._capture_dynamic_slots(factory_run_id=run.id)

    assert all(slot.state == "absent_at_request_time" for slot in slots.values())
    assert {
        (
            slot.source_head.source_head_fact_id,
            slot.source_head.source_head_sequence,
            slot.source_head.source_head_hash,
        )
        for slot in slots.values()
    } == {("", 0, "0" * 64)}


def _complete_static_chain(
    tmp_path: Path,
    run: FactoryRun,
) -> tuple[FactoryStore, tuple[dict[str, object], ...]]:
    runtime_root = tmp_path / "runtime"
    store = FactoryStore(runtime_root)
    task = _pm_task("TASK-1", ["src/main.py"])
    _write_json(runtime_root, "tasks/plan.json", _pm_document([task]))
    pm_binding = build_pm_stage_artifact_bindings(
        factory_store=store,
        source_root=runtime_root,
        factory_run_id=run.id,
    )
    prefix = list(_admission_chain(run))
    pm_event = build_next_factory_event_record(
        prefix,
        run_id=run.id,
        event={
            "type": "stage_completed",
            "stage": "pm_planning",
            "run_id": run.id,
            "event_id": "evt-pm-complete",
            "timestamp": "2026-07-19T00:00:01+00:00",
            "result": {"stage": "pm_planning", "status": "success"},
            "stage_artifact_bindings": pm_binding.to_record(),
        },
    )
    prefix.append(pm_event)
    row = _review_row(task)
    _write_json(
        runtime_root,
        f"runtime/state/blueprints/{run.id}.review.json",
        _review_document(run.id, [row]),
    )
    _write_json(runtime_root, "runtime/blueprints/bp-TASK-1.json", _blueprint(run.id, task))
    ce_binding = build_chief_engineer_stage_artifact_bindings(
        factory_store=store,
        source_root=runtime_root,
        factory_run_id=run.id,
        pm_stage_event=pm_event,
    )
    prefix.append(
        build_next_factory_event_record(
            prefix,
            run_id=run.id,
            event={
                "type": "stage_completed",
                "stage": "chief_engineer_review",
                "run_id": run.id,
                "event_id": "evt-ce-complete",
                "timestamp": "2026-07-19T00:00:02+00:00",
                "result": {"stage": "chief_engineer_review", "status": "success"},
                "stage_artifact_bindings": ce_binding.to_record(),
            },
        )
    )
    return store, tuple(prefix)


@pytest.mark.parametrize("role", ["pm", "architect", "chief_engineer", "director", "qa"])
def test_all_factory_roles_resolve_policy_ordered_static_and_dynamic_cut(
    tmp_path: Path,
    role: str,
) -> None:
    run = _run()
    store, chain = _complete_static_chain(tmp_path, run)
    dynamic_records = (
        _control_plane_record(
            tmp_path,
            seq=1,
            factory_run_id=run.id,
            stage="workspace_validation",
            ok=False,
            physical_evidence={"command_count": 1},
        ),
        _control_plane_record(
            tmp_path,
            seq=2,
            factory_run_id=run.id,
            stage="qa",
            ok=True,
            physical_evidence={"command_receipts": [{"receipt_id": "receipt-1"}]},
        ),
    )
    query_calls: list[QueryFactEventsV1] = []

    def query(query_value: QueryFactEventsV1) -> FactStreamQueryResultV1:
        query_calls.append(query_value)
        return FactStreamQueryResultV1(
            workspace=str(tmp_path.resolve()),
            stream="execution.control_plane",
            events=dynamic_records,
            total=2,
            next_offset=0,
        )

    resolver = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=tmp_path,
        factory_store=store,
        factory_event_loader=lambda _run_id: chain,
        fact_query=query,
    )

    cut = resolver.resolve_source_cut(
        request=_request(role=role),
        authority=_authority(run.id),
        factory_run=run,
    )

    from polaris.kernelone.events.final_request_evidence import role_final_request_policy

    policy = role_final_request_policy(role)
    assert tuple(slot.ref_kind for slot in cut.slots) == policy.slot_order
    assert len({slot.source_head.canonical_source_ref for slot in cut.slots}) == len(cut.slots)
    static_slots = [
        slot for slot in cut.slots if slot.ref_kind in {"pm_raw_intent", "pm_contract", "target_files", "ce_blueprint"}
    ]
    assert {
        (slot.source_head.source_head_fact_id, slot.source_head.source_head_sequence, slot.source_head.source_head_hash)
        for slot in static_slots
    } == {("evt-ce-complete", 3, chain[-1]["chain_event_hash"])}
    if role in {"pm", "architect"}:
        assert query_calls == []
    else:
        assert len(query_calls) == 1
    if role in {"director", "qa"}:
        ce_slot = next(slot for slot in cut.slots if slot.ref_kind == "ce_blueprint")
        assert len(ce_slot.items) == 1


def _dynamic_resolver(
    tmp_path: Path,
    run: FactoryRun,
    records: tuple[dict[str, object], ...],
) -> CanonicalFactoryRoleEvidenceSourceAuthority:
    return CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=tmp_path,
        factory_store=FactoryStore(tmp_path / "runtime"),
        factory_event_loader=lambda _run_id: _admission_chain(run),
        fact_query=lambda _query: FactStreamQueryResultV1(
            workspace=str(tmp_path.resolve()),
            stream="execution.control_plane",
            events=records,
            total=len(records),
            next_offset=0,
        ),
    )


def test_dynamic_sequence_gap_fails_closed(tmp_path: Path) -> None:
    run = _run()
    record = _control_plane_record(
        tmp_path,
        seq=2,
        factory_run_id=run.id,
        stage="qa",
        ok=True,
    )

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        _dynamic_resolver(tmp_path, run, (record,))._capture_dynamic_slots(factory_run_id=run.id)

    assert exc_info.value.code == "factory_role_evidence_dynamic_sequence_invalid"


def test_dynamic_outer_digest_drift_fails_closed(tmp_path: Path) -> None:
    run = _run()
    record = _control_plane_record(
        tmp_path,
        seq=1,
        factory_run_id=run.id,
        stage="qa",
        ok=True,
    )
    record["integrity_digest"] = "f" * 64

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        _dynamic_resolver(tmp_path, run, (record,))._capture_dynamic_slots(factory_run_id=run.id)

    assert exc_info.value.code == "factory_role_evidence_dynamic_record_invalid"


def test_dynamic_nested_content_identity_drift_fails_closed(tmp_path: Path) -> None:
    run = _run()
    record = deepcopy(
        _control_plane_record(
            tmp_path,
            seq=1,
            factory_run_id=run.id,
            stage="qa",
            ok=True,
        )
    )
    record["payload"]["event"]["content_id"] = "f" * 64
    record["integrity_digest"] = EventEnvelope.integrity_digest_for_record(record)

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        _dynamic_resolver(tmp_path, run, (record,))._capture_dynamic_slots(factory_run_id=run.id)

    assert exc_info.value.code == "factory_role_evidence_dynamic_content_identity_invalid"


def test_dynamic_cross_factory_gate_is_validated_but_not_selected(tmp_path: Path) -> None:
    run = _run()
    foreign_record = deepcopy(
        _control_plane_record(
            tmp_path,
            seq=1,
            factory_run_id=run.id,
            stage="workspace_validation",
            ok=False,
            physical_evidence={"command_count": 1},
        )
    )
    event = dict(foreign_record["payload"]["event"])
    event["job_token"] = {**event["job_token"], "factory_run_id": "factory-run-2"}
    event.pop("content_id", None)
    event.pop("append_id", None)
    foreign_record["payload"]["event"] = RunLedger(tmp_path, run_id="child-run-1").prepare_idempotent_event(event)
    foreign_record["integrity_digest"] = EventEnvelope.integrity_digest_for_record(foreign_record)
    current_record = _control_plane_record(
        tmp_path,
        seq=2,
        factory_run_id=run.id,
        stage="qa",
        ok=True,
        physical_evidence={"command_count": 1},
    )

    slots = _dynamic_resolver(tmp_path, run, (foreign_record, current_record))._capture_dynamic_slots(
        factory_run_id=run.id
    )

    assert slots["failure_feedback"].state == "absent_at_request_time"
    assert slots["workspace_quality"].state == "absent_at_request_time"
    assert tuple(item.source_fact_sequence for item in slots["verifier_receipts"].items) == (2,)
    assert {
        (slot.source_head.source_head_fact_id, slot.source_head.source_head_sequence) for slot in slots.values()
    } == {("fact-2", 2)}


def test_dynamic_foreign_gate_with_invalid_binding_still_fails_closed(tmp_path: Path) -> None:
    run = _run()
    record = deepcopy(
        _control_plane_record(
            tmp_path,
            seq=1,
            factory_run_id="factory-run-2",
            stage="qa",
            ok=True,
        )
    )
    event = dict(record["payload"]["event"])
    event["job_token"] = {**event["job_token"], "stage": "workspace_validation"}
    event.pop("content_id", None)
    event.pop("append_id", None)
    record["payload"]["event"] = RunLedger(tmp_path, run_id="child-run-1").prepare_idempotent_event(event)
    record["integrity_digest"] = EventEnvelope.integrity_digest_for_record(record)

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        _dynamic_resolver(tmp_path, run, (record,))._capture_dynamic_slots(factory_run_id=run.id)

    assert exc_info.value.code == "factory_role_evidence_dynamic_binding_invalid"


def test_dynamic_non_string_stage_binding_fails_closed(tmp_path: Path) -> None:
    run = _run()
    record = deepcopy(
        _control_plane_record(
            tmp_path,
            seq=1,
            factory_run_id=run.id,
            stage="qa",
            ok=True,
        )
    )
    event = dict(record["payload"]["event"])
    event["stage"] = True
    event["job_token"] = {**event["job_token"], "stage": True}
    event.pop("content_id", None)
    event.pop("append_id", None)
    record["payload"]["event"] = RunLedger(tmp_path, run_id="child-run-1").prepare_idempotent_event(event)
    record["integrity_digest"] = EventEnvelope.integrity_digest_for_record(record)

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        _dynamic_resolver(tmp_path, run, (record,))._capture_dynamic_slots(factory_run_id=run.id)

    assert exc_info.value.code == "factory_role_evidence_dynamic_binding_invalid"


def test_dynamic_exact_canonical_record_bytes_over_8mib_fail_closed(tmp_path: Path) -> None:
    run = _run()
    record = _control_plane_record(
        tmp_path,
        seq=1,
        factory_run_id=run.id,
        stage="qa",
        ok=True,
        physical_evidence={"commands": "x" * (8 * 1024 * 1024)},
    )

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        _dynamic_resolver(tmp_path, run, (record,))._capture_dynamic_slots(factory_run_id=run.id)

    assert exc_info.value.code == "factory_role_evidence_dynamic_byte_limit_exceeded"


def test_dynamic_verifier_selector_rejects_scalar_truthiness(tmp_path: Path) -> None:
    run = _run()
    record = _control_plane_record(
        tmp_path,
        seq=1,
        factory_run_id=run.id,
        stage="qa",
        ok=True,
        physical_evidence={
            "requirements": 1,
            "entrypoint": True,
            "commands": 1.5,
            "modalities": 1,
            "command_count": True,
            "command_receipts": [],
        },
    )

    slots = _dynamic_resolver(tmp_path, run, (record,))._capture_dynamic_slots(factory_run_id=run.id)

    assert slots["verifier_receipts"].state == "absent_at_request_time"
    assert slots["verifier_receipts"].items == ()


@pytest.mark.parametrize("artifact_kind", ["pm", "review", "blueprint"])
def test_static_immutable_snapshot_drift_fails_closed(tmp_path: Path, artifact_kind: str) -> None:
    run = _run()
    store, chain = _complete_static_chain(tmp_path, run)
    pm_items = chain[1]["stage_artifact_bindings"]["items"]
    ce_items = chain[2]["stage_artifact_bindings"]["items"]
    if artifact_kind == "pm":
        logical_ref = pm_items[0]["immutable_snapshot_ref"]
    elif artifact_kind == "review":
        logical_ref = ce_items[1]["immutable_snapshot_ref"]
    else:
        logical_ref = ce_items[2]["immutable_snapshot_ref"]
    snapshot_path = (tmp_path / "runtime") / str(logical_ref).removeprefix("runtime/")
    snapshot_path.write_bytes(b'{"tampered":true}\n')
    resolver = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=tmp_path,
        factory_store=store,
        factory_event_loader=lambda _run_id: chain,
        fact_query=lambda _query: FactStreamQueryResultV1(
            workspace=str(tmp_path.resolve()),
            stream="execution.control_plane",
            events=(),
            total=0,
            next_offset=0,
        ),
    )

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        resolver.resolve_source_cut(
            request=_request(role="director"),
            authority=_authority(run.id),
            factory_run=run,
        )

    expected_code = (
        "factory_role_evidence_source_pm_binding_invalid"
        if artifact_kind == "pm"
        else "factory_role_evidence_source_ce_binding_invalid"
    )
    assert exc_info.value.code == expected_code


def test_latest_successful_stage_selection_ignores_later_failed_stage_but_heads_full_chain(tmp_path: Path) -> None:
    run = _run()
    store, initial_chain = _complete_static_chain(tmp_path, run)
    chain = (
        *initial_chain,
        build_next_factory_event_record(
            initial_chain,
            run_id=run.id,
            event={
                "type": "stage_completed",
                "stage": "pm_planning",
                "run_id": run.id,
                "event_id": "evt-pm-failed-later",
                "timestamp": "2026-07-19T00:00:03+00:00",
                "result": {"stage": "pm_planning", "status": "failed"},
            },
        ),
    )
    resolver = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=tmp_path,
        factory_store=store,
        factory_event_loader=lambda _run_id: chain,
        fact_query=lambda _query: FactStreamQueryResultV1(
            workspace=str(tmp_path.resolve()),
            stream="execution.control_plane",
            events=(),
            total=0,
            next_offset=0,
        ),
    )

    cut = resolver.resolve_source_cut(
        request=_request(role="director"),
        authority=_authority(run.id),
        factory_run=run,
    )

    assert {
        (slot.source_head.source_head_fact_id, slot.source_head.source_head_sequence)
        for slot in cut.slots
        if slot.ref_kind in {"pm_contract", "target_files", "ce_blueprint"}
    } == {("evt-pm-failed-later", 4)}
    assert next(slot for slot in cut.slots if slot.ref_kind == "pm_contract").items[0].source_fact_id == (
        "evt-pm-complete"
    )


def test_newer_successful_pm_contract_invalidates_older_ce_binding(tmp_path: Path) -> None:
    run = _run()
    store, initial_chain = _complete_static_chain(tmp_path, run)
    newer_pm = build_next_factory_event_record(
        initial_chain,
        run_id=run.id,
        event={
            "type": "stage_completed",
            "stage": "pm_planning",
            "run_id": run.id,
            "event_id": "evt-pm-newer",
            "timestamp": "2026-07-19T00:00:03+00:00",
            "result": {"stage": "pm_planning", "status": "success"},
            "stage_artifact_bindings": initial_chain[1]["stage_artifact_bindings"],
        },
    )
    chain = (*initial_chain, newer_pm)
    resolver = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=tmp_path,
        factory_store=store,
        factory_event_loader=lambda _run_id: chain,
        fact_query=lambda _query: FactStreamQueryResultV1(
            workspace=str(tmp_path.resolve()),
            stream="execution.control_plane",
            events=(),
            total=0,
            next_offset=0,
        ),
    )

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        resolver.resolve_source_cut(
            request=_request(role="director"),
            authority=_authority(run.id),
            factory_run=run,
        )

    assert exc_info.value.code == "factory_role_evidence_source_ce_binding_invalid"


def test_factory_chain_hash_drift_fails_before_any_source_cut(tmp_path: Path) -> None:
    run = _run()
    chain = list(_admission_chain(run))
    chain[0] = {**chain[0], "chain_event_hash": "f" * 64}
    resolver = CanonicalFactoryRoleEvidenceSourceAuthority(
        workspace=tmp_path,
        factory_store=FactoryStore(tmp_path / "runtime"),
        factory_event_loader=lambda _run_id: tuple(chain),
        fact_query=lambda _query: FactStreamQueryResultV1(
            workspace=str(tmp_path.resolve()),
            stream="execution.control_plane",
            events=(),
            total=0,
            next_offset=0,
        ),
    )

    with pytest.raises(FactoryRoleEvidenceAuthorityError) as exc_info:
        resolver.resolve_source_cut(
            request=_request(),
            authority=_authority(run.id),
            factory_run=run,
        )

    assert exc_info.value.code == "factory_role_evidence_source_factory_chain_invalid"
