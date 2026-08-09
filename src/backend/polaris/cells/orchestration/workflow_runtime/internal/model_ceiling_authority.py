"""Owner-bound model-ceiling terminal authority."""

from __future__ import annotations

import hashlib
import json
import weakref
from itertools import pairwise
from threading import Lock
from typing import cast

from polaris.cells.orchestration.workflow_runtime.public.model_ceiling import (
    ModelCeilingCandidateV1,
    ModelCeilingOwnerObservationError,
    ModelCeilingOwnerObservationPortV1,
    ModelCeilingOwnerObservationV1,
    ModelCeilingQualificationV1,
    ModelCeilingTerminalResultV1,
    model_ceiling_attempt_request_binding_hash,
)

_model_ceiling_owner_observation_port: ModelCeilingOwnerObservationPortV1 | None = None
_model_ceiling_owner_observation_port_lock = Lock()
_sealed_results: dict[int, tuple[weakref.ReferenceType[ModelCeilingTerminalResultV1], str]] = {}
_sealed_results_lock = Lock()


def bind_model_ceiling_owner_observation_port(port: ModelCeilingOwnerObservationPortV1) -> None:
    """Bind the process-composition adapter once."""

    if not isinstance(port, ModelCeilingOwnerObservationPortV1):
        raise TypeError("port must implement ModelCeilingOwnerObservationPortV1")
    global _model_ceiling_owner_observation_port
    with _model_ceiling_owner_observation_port_lock:
        bound = _model_ceiling_owner_observation_port
        if bound is None:
            _model_ceiling_owner_observation_port = port
            return
        if bound is not port:
            raise RuntimeError("model_ceiling_owner_observation_port_conflicting_rebind")


def clear_model_ceiling_owner_observation_port(
    port: ModelCeilingOwnerObservationPortV1,
) -> None:
    """Release the exact composition binding during lifespan shutdown/tests."""

    global _model_ceiling_owner_observation_port
    with _model_ceiling_owner_observation_port_lock:
        if _model_ceiling_owner_observation_port is port:
            _model_ceiling_owner_observation_port = None


def _result(
    candidate: ModelCeilingCandidateV1,
    *,
    status: str,
    routing_disposition: str,
    reasons: tuple[str, ...],
    qualification: ModelCeilingQualificationV1 | None = None,
) -> ModelCeilingTerminalResultV1:
    stable_reasons = tuple(dict.fromkeys(reasons))
    result = ModelCeilingTerminalResultV1(
        workspace=candidate.workspace,
        project_id=candidate.project_id,
        run_id=candidate.run_id,
        factory_run_id=candidate.factory_run_id,
        completion_contract_hash=candidate.completion_contract_hash,
        diagnostic_id=candidate.diagnostic_id,
        status=status,
        routing_disposition=routing_disposition,
        reason_codes=stable_reasons,
        qualification=qualification,
    )
    if status == "MODEL_CEILING_QUALIFIED" and type(qualification) is ModelCeilingQualificationV1:
        _seal_terminal_result(result)
    return result


def _qualification_payload(qualification: ModelCeilingQualificationV1 | None) -> object:
    if type(qualification) is not ModelCeilingQualificationV1:
        return None
    qualification = cast(ModelCeilingQualificationV1, qualification)
    return {
        "workspace": qualification.workspace,
        "project_id": qualification.project_id,
        "run_id": qualification.run_id,
        "factory_run_id": qualification.factory_run_id,
        "completion_contract_hash": qualification.completion_contract_hash,
        "diagnostic_id": qualification.diagnostic_id,
        "semantic_class": qualification.semantic_class,
        "role_id": qualification.role_id,
        "provider_id": qualification.provider_id,
        "model": qualification.model,
        "provider_call_id": qualification.provider_call_id,
        "request_hash": qualification.request_hash,
        "final_request_snapshot_ref": qualification.final_request_snapshot_ref,
        "round_count": qualification.round_count,
        "max_rounds": qualification.max_rounds,
        "round_request_binding_hashes": list(qualification.round_request_binding_hashes),
        "evidence_refs": list(qualification.evidence_refs),
    }


def _result_seal_digest(result: ModelCeilingTerminalResultV1) -> str:
    payload = {
        "workspace": result.workspace,
        "project_id": result.project_id,
        "run_id": result.run_id,
        "factory_run_id": result.factory_run_id,
        "completion_contract_hash": result.completion_contract_hash,
        "diagnostic_id": result.diagnostic_id,
        "status": result.status,
        "routing_disposition": result.routing_disposition,
        "reason_codes": list(result.reason_codes),
        "qualification": _qualification_payload(result.qualification),
        "terminal": result.terminal,
        "is_model_ceiling": result.is_model_ceiling,
        "parked": result.parked,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _seal_terminal_result(result: ModelCeilingTerminalResultV1) -> None:
    """Activate one process-local owner result without exporting a capability."""

    object.__setattr__(result, "terminal", True)
    object.__setattr__(result, "is_model_ceiling", True)
    object.__setattr__(result, "parked", False)
    object_id = id(result)

    def _discard(reference: weakref.ReferenceType[ModelCeilingTerminalResultV1]) -> None:
        with _sealed_results_lock:
            current = _sealed_results.get(object_id)
            if current is not None and current[0] is reference:
                _sealed_results.pop(object_id, None)

    reference = weakref.ref(result, _discard)
    with _sealed_results_lock:
        _sealed_results[object_id] = (reference, _result_seal_digest(result))


def _is_sealed_terminal_result(result: ModelCeilingTerminalResultV1) -> bool:
    sealed_result = cast(ModelCeilingTerminalResultV1, result)
    terminal_flags_valid = (
        sealed_result.terminal is True and sealed_result.is_model_ceiling is True and sealed_result.parked is False
    )
    with _sealed_results_lock:
        record = _sealed_results.get(id(sealed_result))
    if record is None or record[0]() is not sealed_result:
        return False
    return bool(record[1] == _result_seal_digest(sealed_result) and terminal_flags_valid)


def _park(candidate: ModelCeilingCandidateV1, *reasons: str) -> ModelCeilingTerminalResultV1:
    """Park incomplete owner evidence without spending attempts or escalating."""

    return _result(
        candidate,
        status="CONTROL_PLANE_BLOCKED",
        routing_disposition="park",
        reasons=tuple(reasons or ("model_ceiling_owner_evidence_incomplete",)),
    )


def _owner_port() -> ModelCeilingOwnerObservationPortV1 | None:
    with _model_ceiling_owner_observation_port_lock:
        return _model_ceiling_owner_observation_port


def qualify_model_ceiling_authoritatively(candidate: ModelCeilingCandidateV1) -> ModelCeilingTerminalResultV1:
    """Recheck owner facts and derive the sole terminal model-ceiling stop."""

    if type(candidate) is not ModelCeilingCandidateV1:
        raise TypeError("candidate must be an exact ModelCeilingCandidateV1")
    port = _owner_port()
    if port is None:
        return _park(candidate, "owner_observation_port_unbound")
    try:
        observation = port.observe_model_ceiling(candidate)
    except ModelCeilingOwnerObservationError as exc:
        return _park(candidate, exc.code)
    except Exception:  # noqa: BLE001 -- every owner-query or join failure parks fail-closed
        return _park(candidate, "owner_observation_failed")
    if type(observation) is not ModelCeilingOwnerObservationV1:
        return _park(candidate, "owner_observation_wrong_type")

    reasons: list[str] = []
    expected_identity = (
        candidate.workspace,
        candidate.project_id,
        candidate.run_id,
        candidate.factory_run_id,
        candidate.completion_contract_hash,
        candidate.diagnostic_id,
        candidate.provider_call_id,
    )
    observed_identity = (
        observation.workspace,
        observation.project_id,
        observation.run_id,
        observation.factory_run_id,
        observation.completion_contract_hash,
        observation.diagnostic_id,
        observation.provider_call_id,
    )
    if observed_identity != expected_identity:
        reasons.append("owner_identity_mismatch")
    if (
        observation.final_request_status != "available"
        or observation.final_request_snapshot_ref != candidate.final_request_snapshot_ref
    ):
        reasons.append("final_request_snapshot_unavailable_or_mismatched")
    if not observation.coverage_pass or not observation.role_identity_ok:
        reasons.append("final_request_coverage_incomplete")
    if observation.missing_required_refs or not set(observation.required_refs).issubset(observation.included_refs):
        reasons.append("final_request_refs_incomplete")
    if (
        observation.missing_required_tools
        or not set(observation.required_tools).issubset(observation.available_tools)
        or not set(observation.required_tools).issubset(observation.actual_tools)
    ):
        reasons.append("final_request_tools_incomplete")

    attempts = observation.attempts
    attempt_identity_mismatch = any(
        (
            attempt.workspace,
            attempt.project_id,
            attempt.factory_run_id,
            attempt.run_id,
            attempt.completion_contract_hash,
            attempt.diagnostic_id,
        )
        != (
            candidate.workspace,
            candidate.project_id,
            candidate.factory_run_id,
            candidate.run_id,
            candidate.completion_contract_hash,
            candidate.diagnostic_id,
        )
        for attempt in attempts
    )
    if attempt_identity_mismatch:
        reasons.append("attempt_identity_mismatch")
    if len({attempt.owner_task_id for attempt in attempts}) != 1:
        reasons.append("owner_task_identity_shift")
    if attempts[-1].call_id != candidate.provider_call_id:
        reasons.append("final_provider_call_not_bound_to_last_round")
    if attempts[-1].context_snapshot_ref != candidate.final_request_snapshot_ref:
        reasons.append("final_snapshot_not_bound_to_last_attempt")
    if observation.request_hash != attempts[-1].request_hash:
        reasons.append("final_request_hash_not_bound_to_last_attempt")
    if len({attempt.call_id for attempt in attempts}) != len(attempts):
        reasons.append("repair_round_call_identity_reused")
    if len({attempt.provider_request_id for attempt in attempts}) != len(attempts):
        reasons.append("repair_round_provider_request_reused")
    if len({attempt.context_snapshot_ref for attempt in attempts}) != len(attempts):
        reasons.append("repair_round_context_snapshot_reused")
    provider_models_roles = {(attempt.provider_id, attempt.model, attempt.role_id) for attempt in attempts}
    if provider_models_roles != {(observation.provider_id, observation.model, observation.role_id)}:
        reasons.append("attempt_provider_model_role_mismatch")
    if any(attempt.terminal_status != "completed" for attempt in attempts):
        reasons.append("provider_attempt_not_completed")
    if any(attempt.authority_attempt_ordinal != attempt.attempt_budget for attempt in attempts):
        reasons.append("provider_attempt_budget_not_exhausted")
    request_binding_hashes = tuple(
        model_ceiling_attempt_request_binding_hash(
            call_id=attempt.call_id,
            context_snapshot_ref=attempt.context_snapshot_ref,
            request_hash=attempt.request_hash,
            request_freeze_id=attempt.request_freeze_id,
            semantic_request_hash=attempt.semantic_request_hash,
            physical_wire_hash=attempt.physical_wire_hash,
            composite_request_hash=attempt.composite_request_hash,
            provider_request_id=attempt.provider_request_id,
            authority_attempt_ordinal=attempt.authority_attempt_ordinal,
            attempt_budget=attempt.attempt_budget,
        )
        for attempt in attempts
    )
    if any(
        attempt.owner_request_binding_hash != request_binding_hash
        for attempt, request_binding_hash in zip(attempts, request_binding_hashes, strict=True)
    ):
        reasons.append("owner_request_binding_mismatch")
    if any(attempt.provider_blocker_observed for attempt in attempts):
        reasons.append("provider_blocker_observed")
    if any(attempt.control_plane_blocker_observed for attempt in attempts):
        reasons.append("control_plane_blocker_observed")
    if any(attempt.environment_blocker_observed or attempt.verifier_timed_out for attempt in attempts):
        reasons.append("environment_blocker_observed")
    if any(attempt.sandbox_blocker_observed for attempt in attempts):
        reasons.append("sandbox_blocker_observed")

    expected_rounds = tuple(range(1, len(attempts) + 1))
    actual_rounds = tuple(attempt.round_number for attempt in attempts)
    max_rounds = {attempt.max_rounds for attempt in attempts}
    if len(max_rounds) != 1 or len(attempts) != next(iter(max_rounds), 0) or actual_rounds != expected_rounds:
        reasons.append("repair_round_budget_not_exhausted")
    if any(not attempt.material_effect_observed for attempt in attempts):
        reasons.append("repair_round_without_material_effect")
    if any(current.before_artifact_hash != previous.after_artifact_hash for previous, current in pairwise(attempts)):
        reasons.append("repair_round_artifact_chain_discontinuous")
    if any(not attempt.verifier_failed for attempt in attempts):
        reasons.append("repair_round_without_exact_verifier_failure")

    verifier_identities = {
        (attempt.verifier_obligation_id, attempt.verifier_argv, attempt.verifier_cwd) for attempt in attempts
    }
    if len(verifier_identities) != 1:
        reasons.append("verifier_identity_shift")
    semantic_classes = {attempt.failure_semantic_class for attempt in attempts}
    if len(semantic_classes) != 1:
        reasons.append("semantic_class_not_stable")
    repair_available = any(attempt.executable_repair_available for attempt in attempts)
    if repair_available:
        reasons.append("executable_repair_still_available")

    if reasons:
        if reasons == ["executable_repair_still_available"]:
            return _result(
                candidate,
                status="LOCAL_REPAIR_REQUIRED",
                routing_disposition="local_repair",
                reasons=tuple(reasons),
            )
        return _park(candidate, *reasons)

    attempt_max_rounds = attempts[0].max_rounds
    semantic_class = attempts[0].failure_semantic_class
    evidence_refs = tuple(
        dict.fromkeys(
            (
                candidate.final_request_snapshot_ref,
                *(
                    evidence_ref
                    for attempt in attempts
                    for evidence_ref in (
                        attempt.lifecycle_event_hash,
                        attempt.material_effect_receipt_hash,
                        attempt.verifier_receipt_hash,
                        attempt.repair_coverage_ref,
                    )
                ),
            )
        )
    )
    qualification = ModelCeilingQualificationV1(
        workspace=candidate.workspace,
        project_id=candidate.project_id,
        run_id=candidate.run_id,
        factory_run_id=candidate.factory_run_id,
        completion_contract_hash=candidate.completion_contract_hash,
        diagnostic_id=candidate.diagnostic_id,
        semantic_class=semantic_class,
        role_id=observation.role_id,
        provider_id=observation.provider_id,
        model=observation.model,
        provider_call_id=candidate.provider_call_id,
        request_hash=observation.request_hash,
        final_request_snapshot_ref=candidate.final_request_snapshot_ref,
        round_count=len(attempts),
        max_rounds=attempt_max_rounds,
        round_request_binding_hashes=request_binding_hashes,
        evidence_refs=evidence_refs,
    )
    return _result(
        candidate,
        status="MODEL_CEILING_QUALIFIED",
        routing_disposition="stop",
        reasons=(),
        qualification=qualification,
    )


def revalidate_model_ceiling_result_authoritatively(
    result: ModelCeilingTerminalResultV1,
) -> ModelCeilingTerminalResultV1:
    """Reject copies/forgeries, then re-query owners before consumption."""

    if type(result) is not ModelCeilingTerminalResultV1:
        raise TypeError("result must be an exact ModelCeilingTerminalResultV1")
    if not _is_sealed_terminal_result(result):
        raise ModelCeilingOwnerObservationError(
            "model_ceiling_result_unsealed",
            "Model ceiling result was not issued by the live workflow_runtime owner",
        )
    qualification = result.qualification
    if type(qualification) is not ModelCeilingQualificationV1:
        raise ModelCeilingOwnerObservationError(
            "model_ceiling_qualification_missing",
            "Model ceiling result has no exact qualification",
        )
    candidate = ModelCeilingCandidateV1(
        workspace=result.workspace,
        project_id=result.project_id,
        run_id=result.run_id,
        factory_run_id=result.factory_run_id,
        completion_contract_hash=result.completion_contract_hash,
        diagnostic_id=result.diagnostic_id,
        provider_call_id=qualification.provider_call_id,
        final_request_snapshot_ref=qualification.final_request_snapshot_ref,
    )
    fresh = qualify_model_ceiling_authoritatively(candidate)
    if not _is_sealed_terminal_result(fresh):
        return fresh
    if _result_seal_digest(fresh) != _result_seal_digest(result):
        return _park(candidate, "model_ceiling_owner_revalidation_drift")
    return fresh


__all__ = [
    "bind_model_ceiling_owner_observation_port",
    "clear_model_ceiling_owner_observation_port",
    "qualify_model_ceiling_authoritatively",
    "revalidate_model_ceiling_result_authoritatively",
]
