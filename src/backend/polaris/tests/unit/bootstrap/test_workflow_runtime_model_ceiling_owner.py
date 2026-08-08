"""Bootstrap adapter proofs for owner-only model-ceiling evidence."""

from __future__ import annotations

import inspect
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.bootstrap import workflow_runtime_model_ceiling_owner as owner_module
from polaris.bootstrap.workflow_runtime_model_ceiling_owner import WorkflowRuntimeModelCeilingOwnerAdapter
from polaris.cells.context.engine.public.contracts import FinalProviderRequestAuditResultV1
from polaris.cells.events.fact_stream.public import SegmentedFactLedgerHeadV1
from polaris.cells.orchestration.workflow_runtime.public.model_ceiling import (
    ModelCeilingCandidateV1,
    ModelCeilingOwnerObservationError,
)
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_FACT_SCHEMA,
    FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SNAPSHOT_SCHEMA,
    FactoryProviderAttemptLifecycleReplayFactV1,
    FactoryProviderAttemptLifecycleReplaySnapshotV1,
    factory_provider_attempt_lifecycle_stream,
)

WORKSPACE = str(Path("/tmp/model-ceiling-owner").resolve())
CONTRACT_HASH = "a" * 64
SNAPSHOT_REF = "b" * 24


def _candidate() -> ModelCeilingCandidateV1:
    return ModelCeilingCandidateV1(
        workspace=WORKSPACE,
        project_id="project-1",
        run_id="run-1",
        factory_run_id="factory-run-1",
        completion_contract_hash=CONTRACT_HASH,
        diagnostic_id="diag.typescript.same_failure",
        provider_call_id="call-1",
        final_request_snapshot_ref=SNAPSHOT_REF,
    )


def _context_result(
    *, workspace: str = WORKSPACE, snapshot_ref: str = SNAPSHOT_REF
) -> FinalProviderRequestAuditResultV1:
    return FinalProviderRequestAuditResultV1(
        ok=True,
        status="available",
        workspace=workspace,
        context_snapshot_ref=snapshot_ref,
        payload={
            "schema_version": "context.final_provider_request_audit.v1",
            "context_hash": snapshot_ref,
            "call_id": "call-1",
            "role": "director",
            "provider_id": "provider-1",
            "model": "model-1",
            "tools": [
                {"type": "function", "function": {"name": "edit_file"}},
                {"type": "function", "function": {"name": "execute_command"}},
            ],
            "final_request_context_audit": {
                "schema_version": "llm.final_request_context_audit.v1",
                "final_request_evidence_coverage": {
                    "schema_version": "polaris.final_request_evidence_coverage.v1",
                    "request_hash": "8" * 64,
                    "role_id": "director",
                    "expected_role_id": "director",
                    "role_identity_ok": True,
                    "required_refs": ["completion_contract", "diagnostic_feedback"],
                    "included_refs": ["completion_contract", "diagnostic_feedback"],
                    "missing_required_refs": [],
                    "required_tools": ["edit_file", "execute_command"],
                    "available_tools": ["edit_file", "execute_command"],
                    "missing_required_tools": [],
                    "coverage_ratio": 1.0,
                    "pass": True,
                },
            },
        },
    )


def _lifecycle_fact(*, phase: str, sequence: int) -> FactoryProviderAttemptLifecycleReplayFactV1:
    return FactoryProviderAttemptLifecycleReplayFactV1(
        schema_version=FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_FACT_SCHEMA,
        phase=phase,  # type: ignore[arg-type]
        lifecycle_event_id=f"lifecycle-{phase}-{sequence}",
        logical_sequence=sequence,
        event_hash=f"{sequence:064x}",
        factory_run_id="factory-run-1",
        scope_id="factory-run-1",
        run_id="run-1",
        role="director",
        turn_id="turn-1",
        call_id="call-1",
        request_freeze_id="freeze-1",
        execution_authority_hash="1" * 64,
        attempt_budget=1,
        provider="provider-1",
        model="model-1",
        semantic_candidate_hash="2" * 64,
        semantic_request_hash="3" * 64,
        physical_wire_hash="4" * 64,
        composite_request_hash="5" * 64,
        reservation_id="reservation-1",
        provider_request_id="provider-request-1",
        authority_attempt_ordinal=1,
        start_permit_id="permit-1",
        context_snapshot_ref=SNAPSHOT_REF,
        pin_hash="6" * 64,
        lease_id="lease-1" if phase == "terminal" else "",
        terminal_status="completed" if phase == "terminal" else "",
        error="",
    )


def _replay(*, facts: tuple[FactoryProviderAttemptLifecycleReplayFactV1, ...] | None = None):
    stable_facts = facts or (_lifecycle_fact(phase="start", sequence=1), _lifecycle_fact(phase="terminal", sequence=2))
    stream = factory_provider_attempt_lifecycle_stream("factory-run-1")
    head = SegmentedFactLedgerHeadV1(
        workspace=WORKSPACE,
        logical_stream=stream,
        storage_prefix="segmented/provider-attempt-replay",
        total_count=len(stable_facts),
        segment_count=1,
        global_seq=len(stable_facts),
        next_expected_global_seq=len(stable_facts) + 1,
        tail_segment_index=0,
        tail_local_seq=len(stable_facts),
        head_hash="7" * 64,
        storage_bytes=1,
    )
    return FactoryProviderAttemptLifecycleReplaySnapshotV1(
        schema_version=FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SNAPSHOT_SCHEMA,
        workspace=WORKSPACE,
        factory_run_id="factory-run-1",
        logical_stream=stream,
        captured_head=head,
        facts=stable_facts,
    )


def _install_owner_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    context_result: FinalProviderRequestAuditResultV1 | None = None,
    replay: FactoryProviderAttemptLifecycleReplaySnapshotV1 | None = None,
) -> None:
    monkeypatch.setattr(
        owner_module,
        "query_final_provider_request_audit",
        lambda query: context_result or _context_result(),
    )
    monkeypatch.setattr(
        owner_module,
        "query_factory_provider_attempt_lifecycle_replay",
        lambda query: replay or _replay(),
    )


def test_adapter_reads_roles_kernel_then_fails_closed_on_missing_round_owner_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_owner_fakes(monkeypatch)

    with pytest.raises(ModelCeilingOwnerObservationError, match="model_ceiling_round_owner_query_unavailable"):
        WorkflowRuntimeModelCeilingOwnerAdapter().observe_model_ceiling(_candidate())

    assert owner_module.MODEL_CEILING_REQUIRED_OWNER_API_GAPS == (
        "runtime.execution_broker.query_verification_round_by_factory_call",
        "runtime.execution_broker.query_material_effect_round_by_factory_call",
        "director.runtime.query_repair_coverage_by_owner_verifier_diagnostic",
    )


def test_adapter_rejects_wrong_identity_context_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_owner_fakes(monkeypatch, context_result=_context_result(snapshot_ref="9" * 24))

    with pytest.raises(ModelCeilingOwnerObservationError, match="context_snapshot_identity_mismatch"):
        WorkflowRuntimeModelCeilingOwnerAdapter().observe_model_ceiling(_candidate())


def test_adapter_rejects_context_call_identity_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    context_result = _context_result()
    payload = deepcopy(context_result.payload)
    payload["call_id"] = "call-other"
    _install_owner_fakes(monkeypatch, context_result=replace(context_result, payload=payload))

    with pytest.raises(ModelCeilingOwnerObservationError, match="context_snapshot_call_identity_mismatch"):
        WorkflowRuntimeModelCeilingOwnerAdapter().observe_model_ceiling(_candidate())


@pytest.mark.parametrize("mutation", ["role", "tools", "refs"])
def test_adapter_rejects_incomplete_final_request_coverage(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    context_result = _context_result()
    payload = deepcopy(context_result.payload)
    coverage = payload["final_request_context_audit"]["final_request_evidence_coverage"]
    if mutation == "role":
        coverage["role_identity_ok"] = False
    elif mutation == "tools":
        payload["tools"] = []
    else:
        coverage["missing_required_refs"] = ["completion_contract"]
    _install_owner_fakes(monkeypatch, context_result=replace(context_result, payload=payload))

    expected = {
        "role": "final_request_role_or_coverage_invalid",
        "tools": "final_request_tools_incomplete",
        "refs": "final_request_refs_incomplete",
    }[mutation]
    with pytest.raises(ModelCeilingOwnerObservationError, match=expected):
        WorkflowRuntimeModelCeilingOwnerAdapter().observe_model_ceiling(_candidate())


def test_adapter_requires_terminal_roles_kernel_fact(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_owner_fakes(monkeypatch, replay=_replay(facts=(_lifecycle_fact(phase="start", sequence=1),)))

    with pytest.raises(ModelCeilingOwnerObservationError, match="provider_attempt_terminal_missing"):
        WorkflowRuntimeModelCeilingOwnerAdapter().observe_model_ceiling(_candidate())


def test_adapter_rejects_final_request_lifecycle_identity_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _replay()
    terminal = replay.facts[-1]
    drifted = replace(terminal, model="other-model")
    replay = replace(replay, facts=(replay.facts[0], drifted))
    _install_owner_fakes(monkeypatch, replay=replay)

    with pytest.raises(ModelCeilingOwnerObservationError, match="provider_attempt_final_request_identity_mismatch"):
        WorkflowRuntimeModelCeilingOwnerAdapter().observe_model_ceiling(_candidate())


def test_bootstrap_source_has_no_generic_receipt_authority() -> None:
    source = inspect.getsource(owner_module)

    assert "audit.evidence" not in source
    assert "read_managed_process_receipt" not in source
    assert "query_factory_provider_attempt_lifecycle_replay" in source


def test_configure_reuses_the_process_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    bound: list[object] = []
    monkeypatch.setattr(owner_module, "bind_model_ceiling_owner_observation_port", bound.append)

    owner_module.configure_workflow_runtime_model_ceiling_owner()
    owner_module.configure_workflow_runtime_model_ceiling_owner()

    assert bound == [owner_module._OWNER_ADAPTER, owner_module._OWNER_ADAPTER]


def test_http_app_factory_installs_the_owner_adapter() -> None:
    app_factory = Path(__file__).resolve().parents[3] / "delivery/http/app_factory.py"

    source = app_factory.read_text(encoding="utf-8")

    assert "configure_workflow_runtime_model_ceiling_owner" in source
    assert "configure_workflow_runtime_model_ceiling_owner()" in source
