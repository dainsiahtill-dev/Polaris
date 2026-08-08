"""Owner-only model-ceiling qualification boundary.

The caller supplies identity locators only.  A terminal model ceiling can be
created only from bootstrap-bound owner observations.  Those observations must
bind every repair round to roles.kernel provider lifecycle facts, a material
edit/effect receipt, the exact execution_broker verifier failure, and a direct
director.runtime coverage result.  Missing owner evidence parks the project;
it never spends project budget or escalates to PM/Chief Engineer.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

_LOWER_HEX = frozenset("0123456789abcdef")


def _exact_text(name: str, value: object, *, max_length: int = 512) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise ValueError(f"{name} must be 1..{max_length} characters")
    if any(unicodedata.category(char).startswith("C") for char in normalized):
        raise ValueError(f"{name} must not contain control characters")
    return normalized


def _workspace(value: object) -> str:
    return str(Path(_exact_text("workspace", value, max_length=4096)).expanduser().resolve())


def _lower_hex(name: str, value: object, *, length: int) -> str:
    token = _exact_text(name, value, max_length=length)
    if len(token) != length or any(char not in _LOWER_HEX for char in token):
        raise ValueError(f"{name} must be a {length}-character lowercase hex value")
    return token


def _sha256(name: str, value: object) -> str:
    return _lower_hex(name, value, length=64)


def _snapshot_ref(value: object) -> str:
    return _lower_hex("context_snapshot_ref", value, length=24)


def _exact_tuple(name: str, value: object, *, allow_empty: bool = True) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be an exact tuple")
    normalized = tuple(_exact_text(f"{name}[{index}]", item) for index, item in enumerate(value))
    if not allow_empty and not normalized:
        raise ValueError(f"{name} must not be empty")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} must not contain duplicates")
    return normalized


def _sha256_tuple(name: str, value: object, *, allow_empty: bool = True) -> tuple[str, ...]:
    normalized = _exact_tuple(name, value, allow_empty=allow_empty)
    return tuple(_sha256(f"{name}[{index}]", item) for index, item in enumerate(normalized))


def model_ceiling_attempt_request_binding_hash(
    *,
    call_id: str,
    context_snapshot_ref: str,
    request_hash: str,
    request_freeze_id: str,
    semantic_request_hash: str,
    physical_wire_hash: str,
    composite_request_hash: str,
    provider_request_id: str,
    authority_attempt_ordinal: int,
    attempt_budget: int,
) -> str:
    """Canonical owner invariant joining ContextOS and lifecycle request identity."""

    payload = {
        "schema_version": "orchestration.workflow_runtime.model_ceiling_request_binding.v1",
        "call_id": _exact_text("call_id", call_id, max_length=512),
        "context_snapshot_ref": _snapshot_ref(context_snapshot_ref),
        "request_hash": _sha256("request_hash", request_hash),
        "request_freeze_id": _exact_text("request_freeze_id", request_freeze_id, max_length=512),
        "semantic_request_hash": _sha256("semantic_request_hash", semantic_request_hash),
        "physical_wire_hash": _sha256("physical_wire_hash", physical_wire_hash),
        "composite_request_hash": _sha256("composite_request_hash", composite_request_hash),
        "provider_request_id": _exact_text("provider_request_id", provider_request_id, max_length=512),
        "authority_attempt_ordinal": authority_attempt_ordinal,
        "attempt_budget": attempt_budget,
    }
    for name in ("authority_attempt_ordinal", "attempt_budget"):
        value = payload[name]
        if type(value) is not int or not 1 <= value <= 64:
            raise ValueError(f"{name} must be an exact integer in 1..64")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ModelCeilingCandidateV1:
    """Non-authoritative identity locators for a possible model ceiling."""

    workspace: str
    project_id: str
    run_id: str
    factory_run_id: str
    completion_contract_hash: str
    diagnostic_id: str
    provider_call_id: str
    final_request_snapshot_ref: str
    schema_version: str = field(default="orchestration.workflow_runtime.model_ceiling_candidate.v2", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _workspace(self.workspace))
        for name in ("project_id", "run_id", "factory_run_id", "provider_call_id"):
            object.__setattr__(self, name, _exact_text(name, getattr(self, name), max_length=256))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _sha256("completion_contract_hash", self.completion_contract_hash),
        )
        object.__setattr__(self, "diagnostic_id", _exact_text("diagnostic_id", self.diagnostic_id, max_length=128))
        object.__setattr__(self, "final_request_snapshot_ref", _snapshot_ref(self.final_request_snapshot_ref))


@dataclass(frozen=True, slots=True)
class ModelCeilingAttemptObservationV1:
    """One owner-joined repair round; no field is accepted from the caller."""

    lifecycle_event_hash: str
    material_effect_receipt_hash: str
    verifier_receipt_hash: str
    repair_coverage_ref: str
    workspace: str
    factory_run_id: str
    run_id: str
    project_id: str
    completion_contract_hash: str
    diagnostic_id: str
    owner_task_id: str
    call_id: str
    context_snapshot_ref: str
    request_hash: str
    request_freeze_id: str
    semantic_request_hash: str
    physical_wire_hash: str
    composite_request_hash: str
    owner_request_binding_hash: str
    role_id: str
    provider_id: str
    model: str
    provider_request_id: str
    authority_attempt_ordinal: int
    attempt_budget: int
    terminal_status: str
    round_number: int
    max_rounds: int
    before_artifact_hash: str
    after_artifact_hash: str
    verifier_obligation_id: str
    verifier_argv: tuple[str, ...]
    verifier_cwd: str
    verifier_exit_code: int | None
    verifier_timed_out: bool
    verifier_output_hash: str
    verifier_proof_satisfied: bool
    failure_semantic_class: str
    failure_origin: str
    provider_blocker_observed: bool
    control_plane_blocker_observed: bool
    environment_blocker_observed: bool
    sandbox_blocker_observed: bool
    executable_repair_available: bool

    def __post_init__(self) -> None:
        for name in (
            "lifecycle_event_hash",
            "material_effect_receipt_hash",
            "verifier_receipt_hash",
            "repair_coverage_ref",
            "before_artifact_hash",
            "after_artifact_hash",
            "verifier_output_hash",
            "completion_contract_hash",
            "request_hash",
            "semantic_request_hash",
            "physical_wire_hash",
            "composite_request_hash",
            "owner_request_binding_hash",
        ):
            object.__setattr__(self, name, _sha256(name, getattr(self, name)))
        object.__setattr__(self, "workspace", _workspace(self.workspace))
        for name in (
            "factory_run_id",
            "run_id",
            "project_id",
            "diagnostic_id",
            "owner_task_id",
            "call_id",
            "request_freeze_id",
            "role_id",
            "provider_id",
            "model",
            "provider_request_id",
            "terminal_status",
            "verifier_obligation_id",
            "verifier_cwd",
            "failure_semantic_class",
            "failure_origin",
        ):
            object.__setattr__(self, name, _exact_text(name, getattr(self, name), max_length=512))
        object.__setattr__(self, "context_snapshot_ref", _snapshot_ref(self.context_snapshot_ref))
        object.__setattr__(self, "verifier_argv", _exact_tuple("verifier_argv", self.verifier_argv, allow_empty=False))
        for name in ("authority_attempt_ordinal", "attempt_budget", "round_number", "max_rounds"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 64:
                raise ValueError(f"{name} must be an exact integer in 1..64")
        if self.verifier_exit_code is not None and type(self.verifier_exit_code) is not int:
            raise TypeError("verifier_exit_code must be an exact int or None")
        for name in (
            "verifier_timed_out",
            "verifier_proof_satisfied",
            "provider_blocker_observed",
            "control_plane_blocker_observed",
            "environment_blocker_observed",
            "sandbox_blocker_observed",
            "executable_repair_available",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be an exact bool")
        if self.authority_attempt_ordinal > self.attempt_budget:
            raise ValueError("authority_attempt_ordinal must not exceed attempt_budget")

    @property
    def material_effect_observed(self) -> bool:
        return self.before_artifact_hash != self.after_artifact_hash

    @property
    def verifier_failed(self) -> bool:
        return (
            self.failure_origin == "artifact_semantic"
            and not self.verifier_timed_out
            and self.verifier_exit_code is not None
            and self.verifier_exit_code != 0
            and not self.verifier_proof_satisfied
            and not self.provider_blocker_observed
            and not self.control_plane_blocker_observed
            and not self.environment_blocker_observed
            and not self.sandbox_blocker_observed
        )


@dataclass(frozen=True, slots=True)
class ModelCeilingOwnerObservationV1:
    """Owner-assembled facts.  Only process bootstrap may construct these."""

    workspace: str
    project_id: str
    run_id: str
    factory_run_id: str
    completion_contract_hash: str
    diagnostic_id: str
    provider_call_id: str
    final_request_snapshot_ref: str
    final_request_status: str
    request_hash: str
    role_id: str
    provider_id: str
    model: str
    role_identity_ok: bool
    coverage_pass: bool
    required_refs: tuple[str, ...]
    included_refs: tuple[str, ...]
    missing_required_refs: tuple[str, ...]
    required_tools: tuple[str, ...]
    available_tools: tuple[str, ...]
    actual_tools: tuple[str, ...]
    missing_required_tools: tuple[str, ...]
    attempts: tuple[ModelCeilingAttemptObservationV1, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _workspace(self.workspace))
        for name in ("project_id", "run_id", "factory_run_id", "provider_call_id"):
            object.__setattr__(self, name, _exact_text(name, getattr(self, name), max_length=256))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _sha256("completion_contract_hash", self.completion_contract_hash),
        )
        object.__setattr__(self, "diagnostic_id", _exact_text("diagnostic_id", self.diagnostic_id, max_length=128))
        object.__setattr__(self, "final_request_snapshot_ref", _snapshot_ref(self.final_request_snapshot_ref))
        object.__setattr__(self, "request_hash", _sha256("request_hash", self.request_hash))
        for name in ("final_request_status", "role_id", "provider_id", "model"):
            object.__setattr__(self, name, _exact_text(name, getattr(self, name), max_length=512))
        for name in ("role_identity_ok", "coverage_pass"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be an exact bool")
        for name in (
            "required_refs",
            "included_refs",
            "missing_required_refs",
            "required_tools",
            "available_tools",
            "actual_tools",
            "missing_required_tools",
        ):
            object.__setattr__(self, name, _exact_tuple(name, getattr(self, name)))
        if type(self.attempts) is not tuple or not self.attempts:
            raise TypeError("attempts must be a non-empty exact tuple")
        if any(type(attempt) is not ModelCeilingAttemptObservationV1 for attempt in self.attempts):
            raise TypeError("attempts must contain exact ModelCeilingAttemptObservationV1 values")


class ModelCeilingOwnerObservationError(RuntimeError):
    """Owner evidence was absent, malformed, or could not be joined."""

    def __init__(self, code: str, message: str) -> None:
        self.code = _exact_text("code", code, max_length=128)
        super().__init__(f"{self.code}: {_exact_text('message', message, max_length=2048)}")


@runtime_checkable
class ModelCeilingOwnerObservationPortV1(Protocol):
    def observe_model_ceiling(self, candidate: ModelCeilingCandidateV1) -> ModelCeilingOwnerObservationV1: ...


@dataclass(frozen=True, slots=True)
class ModelCeilingQualificationV1:
    """Evidence summary. Authority exists only on an internally sealed result."""

    workspace: str
    project_id: str
    run_id: str
    factory_run_id: str
    completion_contract_hash: str
    diagnostic_id: str
    semantic_class: str
    role_id: str
    provider_id: str
    model: str
    provider_call_id: str
    request_hash: str
    final_request_snapshot_ref: str
    round_count: int
    max_rounds: int
    round_request_binding_hashes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    schema_version: str = field(default="orchestration.workflow_runtime.model_ceiling_qualification.v4", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _workspace(self.workspace))
        for name in (
            "project_id",
            "run_id",
            "factory_run_id",
            "diagnostic_id",
            "semantic_class",
            "role_id",
            "provider_id",
            "model",
            "provider_call_id",
        ):
            object.__setattr__(self, name, _exact_text(name, getattr(self, name), max_length=512))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _sha256("completion_contract_hash", self.completion_contract_hash),
        )
        object.__setattr__(self, "request_hash", _sha256("request_hash", self.request_hash))
        object.__setattr__(self, "final_request_snapshot_ref", _snapshot_ref(self.final_request_snapshot_ref))
        object.__setattr__(
            self,
            "round_request_binding_hashes",
            _sha256_tuple("round_request_binding_hashes", self.round_request_binding_hashes, allow_empty=False),
        )
        object.__setattr__(self, "evidence_refs", _exact_tuple("evidence_refs", self.evidence_refs, allow_empty=False))
        for name in ("round_count", "max_rounds"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 64:
                raise ValueError(f"{name} must be an exact integer in 1..64")
        if self.round_count != self.max_rounds:
            raise ValueError("round_count must equal max_rounds for a model ceiling")
        if len(self.round_request_binding_hashes) != self.round_count:
            raise ValueError("round_request_binding_hashes must bind every repair round")


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ModelCeilingTerminalResultV1:
    """Sealed stop, local-repair, or parked control-plane decision."""

    workspace: str
    project_id: str
    run_id: str
    factory_run_id: str
    completion_contract_hash: str
    diagnostic_id: str
    status: str
    routing_disposition: str
    reason_codes: tuple[str, ...]
    qualification: ModelCeilingQualificationV1 | None
    schema_version: str = field(default="orchestration.workflow_runtime.model_ceiling_terminal_result.v4", init=False)
    terminal: bool = field(init=False)
    is_model_ceiling: bool = field(init=False)
    parked: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _workspace(self.workspace))
        for name in ("project_id", "run_id", "factory_run_id", "diagnostic_id", "status", "routing_disposition"):
            object.__setattr__(self, name, _exact_text(name, getattr(self, name), max_length=512))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _sha256("completion_contract_hash", self.completion_contract_hash),
        )
        object.__setattr__(self, "reason_codes", _exact_tuple("reason_codes", self.reason_codes))
        if self.qualification is not None and type(self.qualification) is not ModelCeilingQualificationV1:
            raise TypeError("qualification must be an exact ModelCeilingQualificationV1 or None")
        # Public construction, dataclasses.replace, copy and pickle always
        # produce an unsealed observation. Only workflow_runtime internal
        # authority may register and activate terminal state.
        object.__setattr__(self, "terminal", False)
        object.__setattr__(self, "is_model_ceiling", False)
        object.__setattr__(self, "parked", self.status == "CONTROL_PLANE_BLOCKED")

    def __copy__(self) -> ModelCeilingTerminalResultV1:
        return type(self)(
            workspace=self.workspace,
            project_id=self.project_id,
            run_id=self.run_id,
            factory_run_id=self.factory_run_id,
            completion_contract_hash=self.completion_contract_hash,
            diagnostic_id=self.diagnostic_id,
            status=self.status,
            routing_disposition=self.routing_disposition,
            reason_codes=self.reason_codes,
            qualification=self.qualification,
        )

    def __deepcopy__(self, memo: dict[int, object]) -> ModelCeilingTerminalResultV1:
        del memo
        return self.__copy__()

    def __reduce_ex__(self, protocol: object) -> tuple[object, tuple[object, ...]]:
        del protocol
        qualification = deepcopy(self.qualification)
        return (
            type(self),
            (
                self.workspace,
                self.project_id,
                self.run_id,
                self.factory_run_id,
                self.completion_contract_hash,
                self.diagnostic_id,
                self.status,
                self.routing_disposition,
                self.reason_codes,
                qualification,
            ),
        )


def qualify_model_ceiling(candidate: ModelCeilingCandidateV1) -> ModelCeilingTerminalResultV1:
    """Qualify one identity using bootstrap-bound owner queries only."""

    if type(candidate) is not ModelCeilingCandidateV1:
        raise TypeError("candidate must be an exact ModelCeilingCandidateV1")
    from polaris.cells.orchestration.workflow_runtime.internal.model_ceiling_authority import (
        qualify_model_ceiling_authoritatively,
    )

    return qualify_model_ceiling_authoritatively(candidate)


def revalidate_model_ceiling_result(result: ModelCeilingTerminalResultV1) -> ModelCeilingTerminalResultV1:
    """Re-query owner facts before any caller-supplied result is consumed."""

    if type(result) is not ModelCeilingTerminalResultV1:
        raise TypeError("result must be an exact ModelCeilingTerminalResultV1")
    from polaris.cells.orchestration.workflow_runtime.internal.model_ceiling_authority import (
        revalidate_model_ceiling_result_authoritatively,
    )

    return revalidate_model_ceiling_result_authoritatively(result)


__all__ = [
    "ModelCeilingAttemptObservationV1",
    "ModelCeilingCandidateV1",
    "ModelCeilingOwnerObservationError",
    "ModelCeilingOwnerObservationPortV1",
    "ModelCeilingOwnerObservationV1",
    "ModelCeilingQualificationV1",
    "ModelCeilingTerminalResultV1",
    "model_ceiling_attempt_request_binding_hash",
    "qualify_model_ceiling",
    "revalidate_model_ceiling_result",
]
