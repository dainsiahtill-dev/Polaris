"""Owner-only model-ceiling qualification tests."""

from __future__ import annotations

import copy
import pickle
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.workflow_runtime.internal import model_ceiling_authority
from polaris.cells.orchestration.workflow_runtime.public.model_ceiling import (
    ModelCeilingAttemptObservationV1,
    ModelCeilingCandidateV1,
    ModelCeilingOwnerObservationError,
    ModelCeilingOwnerObservationV1,
    ModelCeilingQualificationV1,
    ModelCeilingTerminalResultV1,
    model_ceiling_attempt_request_binding_hash,
    qualify_model_ceiling,
    revalidate_model_ceiling_result,
)

WORKSPACE = str(Path("/tmp/model-ceiling-authority").resolve())
CONTRACT_HASH = "a" * 64
SNAPSHOT_REF = "b" * 24


def _candidate(**overrides: Any) -> ModelCeilingCandidateV1:
    values: dict[str, Any] = {
        "workspace": WORKSPACE,
        "project_id": "project-1",
        "run_id": "run-1",
        "factory_run_id": "factory-run-1",
        "completion_contract_hash": CONTRACT_HASH,
        "diagnostic_id": "diag.typescript.same_failure",
        "provider_call_id": "call-director-repair",
        "final_request_snapshot_ref": SNAPSHOT_REF,
    }
    values.update(overrides)
    return ModelCeilingCandidateV1(**values)


def _hash(seed: int) -> str:
    return f"{seed:064x}"


def _attempt(number: int, **overrides: Any) -> ModelCeilingAttemptObservationV1:
    call_id = "call-director-repair" if number == 3 else f"call-director-repair-{number}"
    values: dict[str, Any] = {
        "lifecycle_event_hash": _hash(number * 10 + 1),
        "material_effect_receipt_hash": _hash(number * 10 + 2),
        "verifier_receipt_hash": _hash(number * 10 + 3),
        "repair_coverage_ref": _hash(number * 10 + 4),
        "workspace": WORKSPACE,
        "factory_run_id": "factory-run-1",
        "run_id": "run-1",
        "project_id": "project-1",
        "completion_contract_hash": CONTRACT_HASH,
        "diagnostic_id": "diag.typescript.same_failure",
        "owner_task_id": "TASK-1",
        "call_id": call_id,
        "context_snapshot_ref": SNAPSHOT_REF if number == 3 else f"{number:024x}",
        "request_hash": _hash(900) if number == 3 else _hash(900 + number),
        "request_freeze_id": f"freeze-{number}",
        "semantic_request_hash": _hash(1_000 + number),
        "physical_wire_hash": _hash(1_050 + number),
        "composite_request_hash": _hash(1_100 + number),
        "role_id": "director",
        "provider_id": "provider-1",
        "model": "model-1",
        "provider_request_id": f"provider-request-{number}",
        "authority_attempt_ordinal": 1,
        "attempt_budget": 1,
        "terminal_status": "completed",
        "round_number": number,
        "max_rounds": 3,
        "before_artifact_hash": _hash(1_200 + number - 1),
        "after_artifact_hash": _hash(1_200 + number),
        "verifier_obligation_id": "build",
        "verifier_argv": ("npm", "run", "build"),
        "verifier_cwd": ".",
        "verifier_exit_code": 2,
        "verifier_timed_out": False,
        "verifier_output_hash": _hash(number * 10 + 7),
        "verifier_proof_satisfied": False,
        "failure_semantic_class": "typescript_compile_error",
        "failure_origin": "artifact_semantic",
        "provider_blocker_observed": False,
        "control_plane_blocker_observed": False,
        "environment_blocker_observed": False,
        "sandbox_blocker_observed": False,
        "executable_repair_available": False,
    }
    values.update(overrides)
    values.setdefault(
        "owner_request_binding_hash",
        model_ceiling_attempt_request_binding_hash(
            call_id=values["call_id"],
            context_snapshot_ref=values["context_snapshot_ref"],
            request_hash=values["request_hash"],
            request_freeze_id=values["request_freeze_id"],
            semantic_request_hash=values["semantic_request_hash"],
            physical_wire_hash=values["physical_wire_hash"],
            composite_request_hash=values["composite_request_hash"],
            provider_request_id=values["provider_request_id"],
            authority_attempt_ordinal=values["authority_attempt_ordinal"],
            attempt_budget=values["attempt_budget"],
        ),
    )
    return ModelCeilingAttemptObservationV1(**values)


def _observation(**overrides: Any) -> ModelCeilingOwnerObservationV1:
    values: dict[str, Any] = {
        "workspace": WORKSPACE,
        "project_id": "project-1",
        "run_id": "run-1",
        "factory_run_id": "factory-run-1",
        "completion_contract_hash": CONTRACT_HASH,
        "diagnostic_id": "diag.typescript.same_failure",
        "provider_call_id": "call-director-repair",
        "final_request_snapshot_ref": SNAPSHOT_REF,
        "final_request_status": "available",
        "request_hash": _hash(900),
        "role_id": "director",
        "provider_id": "provider-1",
        "model": "model-1",
        "role_identity_ok": True,
        "coverage_pass": True,
        "required_refs": ("completion_contract", "diagnostic_feedback"),
        "included_refs": ("completion_contract", "diagnostic_feedback"),
        "missing_required_refs": (),
        "required_tools": ("edit_file", "execute_command"),
        "available_tools": ("edit_file", "execute_command"),
        "actual_tools": ("edit_file", "execute_command"),
        "missing_required_tools": (),
        "attempts": (_attempt(1), _attempt(2), _attempt(3)),
    }
    values.update(overrides)
    return ModelCeilingOwnerObservationV1(**values)


class _OwnerPort:
    def __init__(self, observation: ModelCeilingOwnerObservationV1) -> None:
        self.observation = observation

    def observe_model_ceiling(self, candidate: ModelCeilingCandidateV1) -> ModelCeilingOwnerObservationV1:
        del candidate
        return self.observation


def _bind(monkeypatch: pytest.MonkeyPatch, observation: ModelCeilingOwnerObservationV1) -> None:
    monkeypatch.setattr(model_ceiling_authority, "_model_ceiling_owner_observation_port", _OwnerPort(observation))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("workspace", None),
        ("project_id", 7),
        ("run_id", None),
        ("factory_run_id", None),
        ("completion_contract_hash", 1),
        ("provider_call_id", None),
    ),
)
def test_candidate_rejects_none_and_integer_identity_values(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _candidate(**{field: value})


def test_mapping_cannot_forge_terminal_model_ceiling() -> None:
    with pytest.raises(TypeError, match="exact ModelCeilingCandidateV1"):
        qualify_model_ceiling({"terminal": True, "is_model_ceiling": True})  # type: ignore[arg-type]


def test_exact_owner_rounds_qualify_and_result_is_sealed(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind(monkeypatch, _observation())

    result = qualify_model_ceiling(_candidate())

    assert result.terminal is True
    assert result.is_model_ceiling is True
    assert result.status == "MODEL_CEILING_QUALIFIED"
    assert result.routing_disposition == "stop"
    assert type(result.qualification) is ModelCeilingQualificationV1
    assert result.qualification.round_count == 3
    replaced = replace(result, status="MODEL_CEILING_QUALIFIED")
    assert replaced.terminal is False
    assert copy.copy(result).terminal is False
    assert copy.deepcopy(result).terminal is False
    assert pickle.loads(pickle.dumps(result)).terminal is False
    assert revalidate_model_ceiling_result(result).terminal is True


def test_public_exact_construction_cannot_forge_terminal() -> None:
    qualification = ModelCeilingQualificationV1(
        workspace=WORKSPACE,
        project_id="project-1",
        run_id="run-1",
        factory_run_id="factory-run-1",
        completion_contract_hash=CONTRACT_HASH,
        diagnostic_id="diag.typescript.same_failure",
        semantic_class="typescript_compile_error",
        role_id="director",
        provider_id="provider-1",
        model="model-1",
        provider_call_id="call-director-repair",
        request_hash=_hash(900),
        final_request_snapshot_ref=SNAPSHOT_REF,
        round_count=3,
        max_rounds=3,
        round_request_binding_hashes=(_hash(101), _hash(102), _hash(103)),
        evidence_refs=(_hash(1),),
    )
    forged = ModelCeilingTerminalResultV1(
        workspace=WORKSPACE,
        project_id="project-1",
        run_id="run-1",
        factory_run_id="factory-run-1",
        completion_contract_hash=CONTRACT_HASH,
        diagnostic_id="diag.typescript.same_failure",
        status="MODEL_CEILING_QUALIFIED",
        routing_disposition="stop",
        reason_codes=(),
        qualification=qualification,
    )

    assert forged.terminal is False
    assert forged.is_model_ceiling is False
    with pytest.raises(ModelCeilingOwnerObservationError, match="model_ceiling_result_unsealed"):
        revalidate_model_ceiling_result(forged)


def test_no_edit_repeated_failure_never_qualifies(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = (
        _attempt(1),
        _attempt(2, after_artifact_hash=_attempt(2).before_artifact_hash),
        _attempt(3),
    )
    _bind(monkeypatch, _observation(attempts=attempts))

    result = qualify_model_ceiling(_candidate())

    assert result.terminal is False
    assert result.parked is True
    assert "repair_round_without_material_effect" in result.reason_codes


def test_cross_call_rounds_are_allowed_but_artifact_chain_must_be_continuous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bind(monkeypatch, _observation())
    assert qualify_model_ceiling(_candidate()).terminal is True

    attempts = (
        _attempt(1),
        _attempt(2, before_artifact_hash=_hash(9999)),
        _attempt(3),
    )
    _bind(monkeypatch, _observation(attempts=attempts))
    result = qualify_model_ceiling(_candidate())
    assert result.terminal is False
    assert "repair_round_artifact_chain_discontinuous" in result.reason_codes


def test_cross_call_rounds_must_keep_the_same_owner_task(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = (_attempt(1), replace(_attempt(2), owner_task_id="task-other"), _attempt(3))
    _bind(monkeypatch, _observation(attempts=attempts))

    result = qualify_model_ceiling(_candidate())

    assert result.terminal is False
    assert "owner_task_identity_shift" in result.reason_codes


def test_reused_call_or_request_identity_never_qualifies(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = (
        _attempt(1),
        _attempt(2, call_id=_attempt(1).call_id),
        _attempt(3),
    )
    _bind(monkeypatch, _observation(attempts=attempts))
    result = qualify_model_ceiling(_candidate())
    assert "repair_round_call_identity_reused" in result.reason_codes


@pytest.mark.parametrize(
    ("overrides", "reason"),
    (
        ({"provider_blocker_observed": True}, "provider_blocker_observed"),
        ({"control_plane_blocker_observed": True}, "control_plane_blocker_observed"),
        ({"environment_blocker_observed": True}, "environment_blocker_observed"),
        ({"sandbox_blocker_observed": True}, "sandbox_blocker_observed"),
        ({"verifier_timed_out": True}, "environment_blocker_observed"),
        ({"failure_origin": "control_plane"}, "repair_round_without_exact_verifier_failure"),
    ),
)
def test_non_artifact_failure_origins_park(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    reason: str,
) -> None:
    attempts = (_attempt(1), _attempt(2), _attempt(3, **overrides))
    _bind(monkeypatch, _observation(attempts=attempts))
    result = qualify_model_ceiling(_candidate())
    assert result.status == "CONTROL_PLANE_BLOCKED"
    assert reason in result.reason_codes


def test_final_request_hash_drift_parks(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind(monkeypatch, _observation(request_hash=_hash(7777)))
    result = qualify_model_ceiling(_candidate())
    assert "final_request_hash_not_bound_to_last_attempt" in result.reason_codes


def test_attempt_ordinal_must_fit_authority_budget() -> None:
    with pytest.raises(ValueError, match="must not exceed"):
        _attempt(1, authority_attempt_ordinal=2, attempt_budget=1)


def test_each_provider_attempt_must_exhaust_its_owner_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = (
        _attempt(1),
        _attempt(2, authority_attempt_ordinal=1, attempt_budget=3),
        _attempt(3),
    )
    _bind(monkeypatch, _observation(attempts=attempts))

    result = qualify_model_ceiling(_candidate())

    assert result.terminal is False
    assert result.status == "CONTROL_PLANE_BLOCKED"
    assert "provider_attempt_budget_not_exhausted" in result.reason_codes


@pytest.mark.parametrize(
    "field", ("request_hash", "semantic_request_hash", "physical_wire_hash", "composite_request_hash")
)
def test_owner_request_binding_rejects_arbitrary_identity_replacement(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    original = _attempt(2)
    tampered = replace(original, **{field: _hash(8_000)})
    attempts = (_attempt(1), tampered, _attempt(3))
    _bind(monkeypatch, _observation(attempts=attempts))

    result = qualify_model_ceiling(_candidate())

    assert result.terminal is False
    assert "owner_request_binding_mismatch" in result.reason_codes


def test_verifier_success_in_any_round_never_qualifies(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = (_attempt(1), _attempt(2, verifier_exit_code=0, verifier_proof_satisfied=True), _attempt(3))
    _bind(monkeypatch, _observation(attempts=attempts))

    result = qualify_model_ceiling(_candidate())

    assert result.terminal is False
    assert "repair_round_without_exact_verifier_failure" in result.reason_codes


def test_semantic_class_shift_across_rounds_parks(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = (_attempt(1), _attempt(2, failure_semantic_class="different_failure"), _attempt(3))
    _bind(monkeypatch, _observation(attempts=attempts))

    result = qualify_model_ceiling(_candidate())

    assert result.status == "CONTROL_PLANE_BLOCKED"
    assert result.routing_disposition == "park"
    assert "semantic_class_not_stable" in result.reason_codes


def test_round_budget_must_be_exactly_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind(monkeypatch, _observation(attempts=(_attempt(1), _attempt(2))))

    result = qualify_model_ceiling(_candidate())

    assert result.terminal is False
    assert "repair_round_budget_not_exhausted" in result.reason_codes


def test_executable_repair_routes_local_without_terminal_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = (_attempt(1), _attempt(2), _attempt(3, executable_repair_available=True))
    _bind(monkeypatch, _observation(attempts=attempts))

    result = qualify_model_ceiling(_candidate())

    assert result.status == "LOCAL_REPAIR_REQUIRED"
    assert result.routing_disposition == "local_repair"
    assert result.terminal is False
    assert result.parked is False


def test_context_coverage_failure_parks_without_escalation(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind(monkeypatch, _observation(coverage_pass=False, missing_required_tools=("edit_file",)))

    result = qualify_model_ceiling(_candidate())

    assert result.status == "CONTROL_PLANE_BLOCKED"
    assert result.routing_disposition == "park"
    assert "final_request_coverage_incomplete" in result.reason_codes


def test_owner_query_failure_parks_not_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FailingPort:
        def observe_model_ceiling(self, candidate: ModelCeilingCandidateV1) -> ModelCeilingOwnerObservationV1:
            del candidate
            raise RuntimeError("owner round join unavailable")

    monkeypatch.setattr(model_ceiling_authority, "_model_ceiling_owner_observation_port", _FailingPort())

    result = qualify_model_ceiling(_candidate())

    assert type(result) is ModelCeilingTerminalResultV1
    assert result.terminal is False
    assert result.status == "CONTROL_PLANE_BLOCKED"
    assert result.routing_disposition == "park"
    assert "owner_observation_failed" in result.reason_codes


def test_typed_owner_gap_code_is_preserved_in_parked_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class _GapPort:
        def observe_model_ceiling(self, candidate: ModelCeilingCandidateV1) -> ModelCeilingOwnerObservationV1:
            del candidate
            raise ModelCeilingOwnerObservationError(
                "model_ceiling_round_owner_query_unavailable",
                "owner round query is not yet exposed",
            )

    monkeypatch.setattr(model_ceiling_authority, "_model_ceiling_owner_observation_port", _GapPort())

    result = qualify_model_ceiling(_candidate())

    assert result.status == "CONTROL_PLANE_BLOCKED"
    assert result.reason_codes == ("model_ceiling_round_owner_query_unavailable",)
