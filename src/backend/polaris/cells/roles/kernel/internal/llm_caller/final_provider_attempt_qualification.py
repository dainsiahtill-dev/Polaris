"""B3.5 final Provider Request qualification and non-physical rejection facts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    EnrollFactStreamStreamsCommandV1,
    FactStreamProvenanceV1,
    append_fact_event,
    enroll_fact_stream_streams,
)
from polaris.cells.roles.kernel.internal.structured_output_transport import (
    STRUCTURED_OUTPUT_TOOL_NAME,
    STRUCTURED_OUTPUT_TRANSPORT_SCHEMA,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleFrozenSemanticRequestV1,
)
from polaris.kernelone.events.final_request_evidence import (
    build_final_request_evidence_slots,
    final_request_evidence_ref_for_requirement,
    redact_provider_transport,
    role_final_request_policy,
)
from polaris.kernelone.fs.guarded_regular_file_snapshot import (
    GuardedRegularFileSnapshotError,
    read_guarded_regular_file_snapshot,
)
from polaris.kernelone.llm.engine.context_store_retention import ContextSnapshotAuditPinRepository
from polaris.kernelone.llm.engine.internal.context_hash import CONTEXT_HASH_PATTERN, validate_context_hash
from polaris.kernelone.storage import resolve_workspace_runtime_identity
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

from .factory_role_evidence_binding import FactoryRoleEvidenceBindingV1
from .final_request_metrics import canonical_message_chars, provider_native_request_metrics

FINAL_PROVIDER_ATTEMPT_QUALIFICATION_REJECTION_SCHEMA = "llm.final_provider_attempt_qualification_rejection.v1"
FINAL_PROVIDER_ATTEMPT_QUALIFICATION_REJECTION_EVENT_TYPE = "final_provider_attempt_qualification_rejected"
FINAL_PROVIDER_ATTEMPT_QUALIFICATION_REJECTION_SOURCE = "roles.kernel.final_provider_attempt_qualification"
_EVIDENCE_BEGIN = "polaris.final_request_evidence.v1:begin"
_EVIDENCE_END = "polaris.final_request_evidence.v1:end"
_MAX_CONTEXT_SNAPSHOT_BYTES = 32 * 1024 * 1024
_QUALIFICATION_PROOF_SEAL = object()
_FACTORY_AUTHORITY_SUPERSEDED_FINDING_CODES = frozenset(
    {
        "missing_context_coverage",
        "underutilized_with_missing_context",
        "missing_required_final_request_evidence",
        "missing_required_final_request_tools",
        "final_request_role_identity_mismatch",
    }
)


class FinalProviderAttemptQualificationError(RuntimeError):
    """Stable B3.5 rejection raised before reservation or transport."""

    def __init__(self, code: str) -> None:
        self.code = str(code or "").strip() or "final_provider_attempt_qualification_failed"
        super().__init__(self.code)


def _canonical_mapping_hash(value: Mapping[str, Any]) -> str:
    try:
        payload = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise FinalProviderAttemptQualificationError("physical_wire_not_canonical_json") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _FinalProviderAttemptQualificationProofV1:
    """Runtime-private capability minted only after exact B3.5 qualification."""

    schema_version: str
    workspace_abs: str
    factory_run_id: str
    run_id: str
    role: str
    turn_id: str
    call_id: str
    request_freeze_id: str
    semantic_candidate_hash: str
    final_semantic_request_hash: str
    gate_semantic_request_hash: str
    context_snapshot_ref: str
    final_request_context_audit_json: str
    physical_route_authority_json: str
    qualified_wire_hash: str
    provider_id: str
    provider_type: str
    model: str
    mode: str
    proof_hash: str
    _frozen: FactoryRoleFrozenSemanticRequestV1 = field(repr=False, compare=False)
    _binding: FactoryRoleEvidenceBindingV1 = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    @classmethod
    def _mint(
        cls,
        *,
        workspace: str,
        frozen: FactoryRoleFrozenSemanticRequestV1,
        binding: FactoryRoleEvidenceBindingV1,
        context_snapshot_ref: str,
        final_request_context_audit: Mapping[str, Any],
        wire_request: Mapping[str, Any],
        physical_route_authority: Mapping[str, Any],
    ) -> _FinalProviderAttemptQualificationProofV1:
        audit = qualify_final_provider_request(
            workspace=workspace,
            frozen=frozen,
            binding=binding,
            final_request_context_audit=final_request_context_audit,
            context_snapshot_ref=context_snapshot_ref,
        )
        validate_exact_wire_before_reservation(
            frozen=frozen,
            wire_request=wire_request,
            physical_route_authority=physical_route_authority,
        )
        _validate_final_physical_context_snapshot(
            workspace=workspace,
            context_snapshot_ref=context_snapshot_ref,
            wire_request=wire_request,
            physical_route_authority=physical_route_authority,
        )
        payload = _frozen_payload(frozen)
        identity = frozen.identity
        semantic_request = final_gate_semantic_request(frozen)
        workspace_abs = resolve_workspace_runtime_identity(workspace).workspace_abs
        audit_json = json.dumps(
            audit,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        route_json = json.dumps(
            dict(physical_route_authority),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        qualified_wire_hash = _canonical_mapping_hash(wire_request)
        provider_id = str(physical_route_authority.get("provider_id") or "")
        provider_type = str(physical_route_authority.get("provider_type") or "")
        model = str(physical_route_authority.get("model") or "")
        mode = str(physical_route_authority.get("mode") or "")
        gate_hash = hashlib.sha256(
            json.dumps(
                dict(semantic_request),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        proof_payload = {
            "schema_version": "llm.final_provider_attempt_qualification_proof.v1",
            "workspace_abs": workspace_abs,
            "factory_run_id": binding.factory_run_id,
            "run_id": identity.run_id,
            "role": str(payload["role"]),
            "turn_id": identity.turn_id,
            "call_id": identity.call_id,
            "request_freeze_id": identity.request_freeze_id,
            "semantic_candidate_hash": frozen.semantic_candidate_hash,
            "final_semantic_request_hash": frozen.final_semantic_request_hash,
            "gate_semantic_request_hash": gate_hash,
            "context_snapshot_ref": context_snapshot_ref,
            "final_request_context_audit_json": audit_json,
            "physical_route_authority_json": route_json,
            "qualified_wire_hash": qualified_wire_hash,
            "provider_id": provider_id,
            "provider_type": provider_type,
            "model": model,
            "mode": mode,
        }
        proof_hash = hashlib.sha256(
            json.dumps(proof_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(
            **proof_payload,
            proof_hash=proof_hash,
            _frozen=frozen,
            _binding=binding,
            _seal=_QUALIFICATION_PROOF_SEAL,
        )

    def __post_init__(self) -> None:
        if self._seal is not _QUALIFICATION_PROOF_SEAL:
            raise ValueError("final_provider_attempt_qualification_proof_not_minted")
        if self.schema_version != "llm.final_provider_attempt_qualification_proof.v1":
            raise ValueError("final_provider_attempt_qualification_proof_schema_mismatch")
        try:
            audit = json.loads(self.final_request_context_audit_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("final_provider_attempt_qualification_proof_audit_invalid") from exc
        if type(audit) is not dict:
            raise ValueError("final_provider_attempt_qualification_proof_audit_invalid")
        proof_payload = {
            "schema_version": self.schema_version,
            "workspace_abs": self.workspace_abs,
            "factory_run_id": self.factory_run_id,
            "run_id": self.run_id,
            "role": self.role,
            "turn_id": self.turn_id,
            "call_id": self.call_id,
            "request_freeze_id": self.request_freeze_id,
            "semantic_candidate_hash": self.semantic_candidate_hash,
            "final_semantic_request_hash": self.final_semantic_request_hash,
            "gate_semantic_request_hash": self.gate_semantic_request_hash,
            "context_snapshot_ref": self.context_snapshot_ref,
            "final_request_context_audit_json": self.final_request_context_audit_json,
            "physical_route_authority_json": self.physical_route_authority_json,
            "qualified_wire_hash": self.qualified_wire_hash,
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "model": self.model,
            "mode": self.mode,
        }
        expected_hash = hashlib.sha256(
            json.dumps(proof_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if self.proof_hash != expected_hash:
            raise ValueError("final_provider_attempt_qualification_proof_hash_mismatch")

    def audit(self) -> dict[str, Any]:
        self.__post_init__()
        payload = json.loads(self.final_request_context_audit_json)
        if type(payload) is not dict:
            raise ValueError("final_provider_attempt_qualification_proof_audit_invalid")
        return payload

    def validate_gate_binding(
        self,
        *,
        workspace: str,
        factory_run_id: str,
        run_id: str,
        role: str,
        turn_id: str,
        call_id: str,
        request_freeze_id: str,
        provider: str,
        model: str,
        semantic_request: Mapping[str, Any],
        wire_request: Mapping[str, Any] | None = None,
    ) -> None:
        self.__post_init__()
        workspace_abs = resolve_workspace_runtime_identity(workspace).workspace_abs
        gate_hash = hashlib.sha256(
            json.dumps(
                dict(semantic_request),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        if (
            workspace_abs,
            factory_run_id,
            run_id,
            role,
            turn_id,
            call_id,
            request_freeze_id,
            provider,
            model,
            gate_hash,
        ) != (
            self.workspace_abs,
            self.factory_run_id,
            self.run_id,
            self.role,
            self.turn_id,
            self.call_id,
            self.request_freeze_id,
            self.provider_id,
            self.model,
            self.gate_semantic_request_hash,
        ):
            raise ValueError("final_provider_attempt_qualification_proof_binding_mismatch")
        qualify_final_provider_request(
            workspace=workspace,
            frozen=self._frozen,
            binding=self._binding,
            final_request_context_audit=self.audit(),
            context_snapshot_ref=self.context_snapshot_ref,
        )
        if self.final_semantic_request_hash != self._frozen.final_semantic_request_hash:
            raise ValueError("final_provider_attempt_qualification_proof_frozen_hash_mismatch")
        if self.semantic_candidate_hash != self._frozen.semantic_candidate_hash:
            raise ValueError("final_provider_attempt_qualification_proof_candidate_hash_mismatch")
        if wire_request is not None:
            route = json.loads(self.physical_route_authority_json)
            if type(route) is not dict:
                raise ValueError("final_provider_attempt_qualification_proof_route_invalid")
            validate_exact_wire_before_reservation(
                frozen=self._frozen,
                wire_request=wire_request,
                physical_route_authority=route,
            )
            _validate_final_physical_context_snapshot(
                workspace=workspace,
                context_snapshot_ref=self.context_snapshot_ref,
                wire_request=wire_request,
                physical_route_authority=route,
            )
            if _canonical_mapping_hash(wire_request) != self.qualified_wire_hash:
                raise ValueError("final_provider_attempt_qualification_proof_wire_mismatch")


def _text(name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name}_missing")
    return value.strip()


@dataclass(frozen=True, slots=True)
class FinalProviderAttemptQualificationRejectionV1:
    """Non-authoritative failure fact; deliberately has no physical identity."""

    schema_version: str
    verification_scope: str
    scope_id: str
    factory_run_id: str
    run_id: str
    role: str
    turn_id: str
    call_id: str
    request_freeze_id: str
    rejection_code: str

    def __post_init__(self) -> None:
        if self.schema_version != FINAL_PROVIDER_ATTEMPT_QUALIFICATION_REJECTION_SCHEMA:
            raise ValueError("final_provider_attempt_qualification_rejection_schema_mismatch")
        if self.verification_scope != "factory":
            raise ValueError("final_provider_attempt_qualification_rejection_scope_mismatch")
        for name in (
            "scope_id",
            "factory_run_id",
            "run_id",
            "role",
            "turn_id",
            "call_id",
            "request_freeze_id",
            "rejection_code",
        ):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        if self.scope_id != self.factory_run_id:
            raise ValueError("final_provider_attempt_qualification_rejection_factory_scope_mismatch")

    def to_record(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "verification_scope": self.verification_scope,
            "scope_id": self.scope_id,
            "factory_run_id": self.factory_run_id,
            "run_id": self.run_id,
            "role": self.role,
            "turn_id": self.turn_id,
            "call_id": self.call_id,
            "request_freeze_id": self.request_freeze_id,
            "rejection_code": self.rejection_code,
        }


def qualification_rejection_stream(scope_id: str) -> str:
    scope_hash = hashlib.sha256(_text("scope_id", scope_id).encode("utf-8")).hexdigest()[:24]
    return f"roles.kernel.final_request_qualification_rejections.{scope_hash}"


def append_qualification_rejection(
    *,
    workspace: str,
    rejection: FinalProviderAttemptQualificationRejectionV1,
) -> None:
    rejection.__post_init__()
    storage_identity = resolve_workspace_runtime_identity(_text("workspace", workspace))
    stream = qualification_rejection_stream(rejection.scope_id)
    transition_id = hashlib.sha256(
        ":".join(
            (
                rejection.factory_run_id,
                rejection.run_id,
                rejection.role,
                rejection.turn_id,
                rejection.call_id,
                rejection.request_freeze_id,
                rejection.rejection_code,
            )
        ).encode("utf-8")
    ).hexdigest()
    provenance = FactStreamProvenanceV1(
        workspace=storage_identity.workspace_abs,
        run_id=rejection.run_id,
        task_id=rejection.call_id,
        turn_id=rejection.turn_id,
        transition_id=transition_id,
    )
    enroll_fact_stream_streams(
        EnrollFactStreamStreamsCommandV1(
            workspace=workspace,
            streams=(stream,),
            maintenance_reason="roles_kernel_final_request_qualification_rejection_stream",
        )
    )
    appended = append_fact_event(
        AppendFactEventCommandV1(
            workspace=workspace,
            stream=stream,
            event_type=FINAL_PROVIDER_ATTEMPT_QUALIFICATION_REJECTION_EVENT_TYPE,
            payload=rejection.to_record(),
            source=FINAL_PROVIDER_ATTEMPT_QUALIFICATION_REJECTION_SOURCE,
            run_id=rejection.run_id,
            task_id=rejection.call_id,
            correlation_id=rejection.request_freeze_id,
            provenance=provenance,
            idempotency_key=f"{stream}:{transition_id}",
            durability="fsync",
            strict_integrity=True,
        )
    )
    if appended.appended_seq is None:
        raise RuntimeError("final_provider_attempt_qualification_rejection_not_durable")


def _frozen_payload(frozen: FactoryRoleFrozenSemanticRequestV1) -> dict[str, Any]:
    if type(frozen) is not FactoryRoleFrozenSemanticRequestV1:
        raise FinalProviderAttemptQualificationError("frozen_semantic_request_type_invalid")
    try:
        FactoryRoleFrozenSemanticRequestV1.__post_init__(frozen)
        payload = json.loads(frozen.canonical_final_payload_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FinalProviderAttemptQualificationError("frozen_semantic_request_invalid") from exc
    if type(payload) is not dict:
        raise FinalProviderAttemptQualificationError("frozen_semantic_request_payload_invalid")
    return payload


def final_gate_semantic_request(frozen: FactoryRoleFrozenSemanticRequestV1) -> dict[str, Any]:
    payload = _frozen_payload(frozen)
    return {
        "messages": payload.get("messages"),
        "tools": payload.get("tools"),
        "tool_choice": payload.get("tool_choice"),
        "response_format": payload.get("response_format"),
        "semantic_options": {
            "temperature": payload.get("temperature"),
            "max_tokens": payload.get("max_tokens"),
        },
    }


def final_request_snapshot_evidence(frozen: FactoryRoleFrozenSemanticRequestV1) -> dict[str, Any]:
    """Exact frozen request record persisted inside the ContextOS snapshot."""

    payload = _frozen_payload(frozen)
    return {
        "schema_version": "llm.factory_final_request_snapshot_evidence.v1",
        "request_identity": {
            "run_id": frozen.identity.run_id,
            "turn_id": frozen.identity.turn_id,
            "call_id": frozen.identity.call_id,
            "request_freeze_id": frozen.identity.request_freeze_id,
        },
        "final_semantic_request_hash": frozen.final_semantic_request_hash,
        "canonical_final_payload": payload,
    }


def _factory_authority_coverage_sources(binding: FactoryRoleEvidenceBindingV1) -> list[dict[str, Any]]:
    """Project the cutoff-bound role slots into the generic coverage contract."""

    sources: list[dict[str, Any]] = []
    for slot in binding.policy_facts.slots:
        present = slot.state == "present"
        sources.append(
            {
                "ref_type": final_request_evidence_ref_for_requirement(slot.ref_kind) or slot.ref_kind,
                "present": present,
                "source": "factory_role_evidence_cutoff",
                "confidence": "cutoff_bound_source_fact" if present else "absent_at_request_time",
                "freshness": "factory_request_cutoff",
                "hash": slot.source_head_hash,
                "details": {
                    "canonical_source_ref": slot.canonical_source_ref,
                    "source_fact_schema": slot.source_fact_schema,
                    "source_fact_version": slot.source_fact_version,
                    "source_head_sequence": slot.source_head_sequence,
                    "cutoff_fact_id": slot.cutoff_fact_id,
                    "cutoff_fact_sequence": slot.cutoff_fact_sequence,
                },
            }
        )
    return sources


def _rebind_context_quality_to_factory_authority(
    *,
    bound: dict[str, Any],
    coverage: dict[str, Any],
) -> None:
    """Remove stale heuristic findings after the Factory cutoff becomes authoritative."""

    quality = bound.get("context_quality")
    if not isinstance(quality, dict):
        return
    raw_findings = quality.get("findings")
    if not isinstance(raw_findings, list):
        raise TypeError("final_request_context_quality_findings_invalid")
    findings = [
        item
        for item in raw_findings
        if not (isinstance(item, dict) and str(item.get("code") or "") in _FACTORY_AUTHORITY_SUPERSEDED_FINDING_CODES)
    ]
    missing_required_refs = list(coverage["missing_required_refs"])
    missing_required_tools = list(coverage["missing_required_tools"])
    request_hash = str(coverage.get("request_hash") or "")
    if missing_required_refs:
        findings.append(
            {
                "code": "missing_required_final_request_evidence",
                "severity": "warning",
                "missing_required_refs": missing_required_refs,
                "request_hash": request_hash,
            }
        )
    if missing_required_tools:
        findings.append(
            {
                "code": "missing_required_final_request_tools",
                "severity": "error",
                "missing_required_tools": missing_required_tools,
                "request_hash": request_hash,
            }
        )
    if coverage.get("role_identity_ok") is False:
        findings.append(
            {
                "code": "final_request_role_identity_mismatch",
                "severity": "error",
                "role_id": coverage.get("role_id", ""),
                "expected_role_id": coverage.get("expected_role_id", ""),
                "request_hash": request_hash,
            }
        )
    evidence_pass = bool(coverage["pass"])
    quality["missing_coverage"] = [] if evidence_pass else list(quality.get("missing_coverage") or [])
    quality["context_needs_review"] = bool(findings)
    quality["findings"] = findings
    quality["final_request_evidence_coverage_pass"] = evidence_pass
    quality["missing_required_refs"] = missing_required_refs
    quality["missing_required_tools"] = missing_required_tools


def bind_final_request_context_audit_to_frozen(
    *,
    audit: Mapping[str, Any],
    frozen: FactoryRoleFrozenSemanticRequestV1,
    binding: FactoryRoleEvidenceBindingV1 | None = None,
) -> dict[str, Any]:
    """Bind generated audit evidence to one immutable Factory request.

    Generic ContextOS coverage records every observed request signal.  Factory
    qualification is stricter: ``required_refs`` and ``included_refs`` are an
    authority projection of the cutoff-bound role slots, not a heuristic text
    inventory.  When the live typed binding is available, preserve the generic
    observation separately and replace the qualifying fields with that exact
    authority projection.
    """

    loaded = json.loads(
        json.dumps(dict(audit), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )
    if not isinstance(loaded, dict):
        raise TypeError("final_request_context_audit_object_required")
    bound: dict[str, Any] = loaded
    bound["final_semantic_request_hash"] = frozen.final_semantic_request_hash
    bound["request_identity"] = final_request_snapshot_evidence(frozen)["request_identity"]
    if binding is not None:
        if type(binding) is not FactoryRoleEvidenceBindingV1:
            raise TypeError("factory_role_evidence_binding_exact_type_required")
        payload = _frozen_payload(frozen)
        role = str(payload.get("role") or "")
        binding_error = binding.validation_error(expected_role=role)
        if binding_error:
            raise ValueError(f"factory_role_evidence_binding_malformed:{binding_error}")
        coverage = bound.get("final_request_evidence_coverage")
        if not isinstance(coverage, dict):
            raise TypeError("final_request_evidence_coverage_object_required")
        if coverage.get("included_refs_authority") == "factory_role_evidence_cutoff":
            return bound
        observed_included_refs = coverage.get("included_refs")
        if type(observed_included_refs) is not list or any(
            type(ref) is not str or not ref for ref in observed_included_refs
        ):
            raise TypeError("final_request_included_refs_invalid")
        missing_required_tools = coverage.get("missing_required_tools")
        if type(missing_required_tools) is not list:
            raise TypeError("final_request_missing_required_tools_invalid")
        required_tools = coverage.get("required_tools")
        if type(required_tools) is not list:
            raise TypeError("final_request_required_tools_invalid")
        policy = role_final_request_policy(role)
        required_refs = [
            final_request_evidence_ref_for_requirement(ref_kind) or ref_kind
            for ref_kind in policy.required_present_slots
        ]
        included_refs = [
            final_request_evidence_ref_for_requirement(slot.ref_kind) or slot.ref_kind
            for slot in binding.policy_facts.slots
            if slot.state == "present"
        ]
        missing_required_refs = [ref for ref in required_refs if ref not in included_refs]
        coverage_sources = _factory_authority_coverage_sources(binding)
        total_required = len(required_refs) + len(required_tools)
        total_missing = len(missing_required_refs) + len(missing_required_tools)
        coverage_ratio = (
            1.0
            if total_required == 0
            else max(
                0.0,
                (total_required - total_missing) / total_required,
            )
        )
        coverage["observed_included_refs"] = list(observed_included_refs)
        coverage["included_refs_authority"] = "factory_role_evidence_cutoff"
        coverage["required_refs"] = required_refs
        coverage["included_refs"] = included_refs
        coverage["missing_required_refs"] = missing_required_refs
        coverage["coverage_sources"] = coverage_sources
        coverage["evidence_slots"] = build_final_request_evidence_slots(
            coverage_sources=coverage_sources,
            required_refs=required_refs,
            included_refs=included_refs,
            missing_required_refs=missing_required_refs,
        )
        coverage["coverage_ratio"] = round(coverage_ratio, 4)
        coverage["pass"] = bool(
            coverage.get("role_identity_ok") is True and not missing_required_refs and not missing_required_tools
        )
        _rebind_context_quality_to_factory_authority(bound=bound, coverage=coverage)
    return bound


def _tool_schema_name(schema: object) -> str:
    if not isinstance(schema, Mapping):
        return ""
    function = schema.get("function")
    if not isinstance(function, Mapping):
        return ""
    return str(function.get("name") or "").strip()


def _enum_member_matches_json_type(value: object, json_type: object) -> bool:
    if json_type == "string":
        return type(value) is str
    if json_type == "integer":
        return type(value) is int
    if json_type == "number":
        return type(value) in (int, float)
    if json_type == "boolean":
        return type(value) is bool
    if json_type == "array":
        return type(value) is list
    if json_type == "object":
        return type(value) is dict
    return False


def _validate_registry_property_contract(
    *,
    tool_name: str,
    property_name: str,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> None:
    """Require exact registry schema, permitting only scoped enum narrowing."""

    expected_keys = set(expected)
    actual_keys = set(actual)
    added_keys = actual_keys - expected_keys
    if added_keys not in (set(), {"enum"}) or expected_keys - actual_keys:
        raise FinalProviderAttemptQualificationError("tool_registry_arg_contract_drift")
    if any(actual.get(key) != value for key, value in expected.items()):
        raise FinalProviderAttemptQualificationError("tool_registry_arg_contract_drift")
    if added_keys != {"enum"}:
        return
    if tool_name != "write_file" or (property_name != "file" and expected.get("description") != "(alias for file)"):
        raise FinalProviderAttemptQualificationError("tool_registry_scoped_enum_unauthorized")
    enum_values = actual.get("enum")
    json_type = expected.get("type")
    if type(enum_values) is not list or not enum_values:
        raise FinalProviderAttemptQualificationError("tool_registry_scoped_enum_invalid")
    if any(not _enum_member_matches_json_type(value, json_type) for value in enum_values):
        raise FinalProviderAttemptQualificationError("tool_registry_scoped_enum_invalid")
    canonical_members = {
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for value in enum_values
    }
    if len(canonical_members) != len(enum_values):
        raise FinalProviderAttemptQualificationError("tool_registry_scoped_enum_invalid")


def _provider_protocol_digest(value: object) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise FinalProviderAttemptQualificationError("provider_protocol_not_canonical_json") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _validate_provider_protocol_contract(
    *,
    tools: list[Any],
    tool_choice: object,
    coverage: Mapping[str, Any],
) -> list[Any]:
    """Remove one exact non-executable result protocol from registry validation."""

    protocol = coverage.get("provider_protocol_schema_coverage")
    if protocol is None:
        return list(tools)
    if not isinstance(protocol, Mapping):
        raise FinalProviderAttemptQualificationError("provider_protocol_coverage_invalid")
    if protocol.get("schema_version") != "polaris.provider_protocol_schema_coverage.v1":
        raise FinalProviderAttemptQualificationError("provider_protocol_coverage_schema_invalid")
    if protocol.get("active") is not True:
        if protocol.get("active") is not False or protocol.get("valid") is not True:
            raise FinalProviderAttemptQualificationError("provider_protocol_coverage_invalid")
        return list(tools)

    failure_code = str(protocol.get("failure_code") or "")
    if protocol.get("valid") is not True:
        if failure_code not in {
            "provider_protocol_tool_missing",
            "provider_protocol_tool_surface_mixed",
            "provider_protocol_tool_schema_drift",
            "provider_protocol_tool_choice_drift",
        }:
            failure_code = "provider_protocol_contract_invalid"
        raise FinalProviderAttemptQualificationError(failure_code)
    if (
        protocol.get("protocol_source") != "roles.kernel.structured_output_transport"
        or protocol.get("transport_schema") != STRUCTURED_OUTPUT_TRANSPORT_SCHEMA
        or protocol.get("tool_name") != STRUCTURED_OUTPUT_TOOL_NAME
        or protocol.get("transport") != "provider_tool"
        or protocol.get("strict") is not True
        or protocol.get("executable_tool") is not False
        or protocol.get("side_effect") is not False
        or protocol.get("tool_lifecycle") is not False
    ):
        raise FinalProviderAttemptQualificationError("provider_protocol_authority_drift")
    schema_name = str(protocol.get("schema_name") or "")
    contract_hash = str(protocol.get("contract_hash") or "")
    if not schema_name or CONTEXT_HASH_PATTERN.fullmatch(contract_hash) is None:
        raise FinalProviderAttemptQualificationError("provider_protocol_contract_identity_invalid")

    if len(tools) != 1:
        raise FinalProviderAttemptQualificationError(
            "provider_protocol_tool_missing" if not tools else "provider_protocol_tool_surface_mixed"
        )
    tool = tools[0]
    if type(tool) is not dict or set(tool) != {"type", "function"} or tool.get("type") != "function":
        raise FinalProviderAttemptQualificationError("provider_protocol_tool_schema_drift")
    function = tool.get("function")
    if not isinstance(function, Mapping) or set(function) != {"name", "description", "parameters", "strict"}:
        raise FinalProviderAttemptQualificationError("provider_protocol_tool_schema_drift")
    description = str(function.get("description") or "")
    parameters = function.get("parameters")
    if (
        function.get("name") != STRUCTURED_OUTPUT_TOOL_NAME
        or function.get("strict") is not True
        or not description.endswith(
            "Call this result-submission tool exactly once. "
            "It records no side effect and is not an executable workspace tool."
        )
        or not isinstance(parameters, Mapping)
        or parameters.get("type") != "object"
    ):
        raise FinalProviderAttemptQualificationError("provider_protocol_tool_schema_drift")
    expected_choice = {
        "type": "function",
        "function": {"name": STRUCTURED_OUTPUT_TOOL_NAME},
    }
    if tool_choice != expected_choice:
        raise FinalProviderAttemptQualificationError("provider_protocol_tool_choice_drift")
    tool_hash = _provider_protocol_digest(tool)
    choice_hash = _provider_protocol_digest(tool_choice)
    if protocol.get("tool_schema_hash") != tool_hash or protocol.get("observed_tool_schema_hash") != tool_hash:
        raise FinalProviderAttemptQualificationError("provider_protocol_tool_schema_drift")
    if protocol.get("tool_choice_hash") != choice_hash or protocol.get("observed_tool_choice_hash") != choice_hash:
        raise FinalProviderAttemptQualificationError("provider_protocol_tool_choice_drift")
    try:
        registered = ToolSpecRegistry.get_llm_schema(
            STRUCTURED_OUTPUT_TOOL_NAME,
            include_arg_aliases=True,
            deterministic=True,
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        raise FinalProviderAttemptQualificationError("provider_protocol_registry_lookup_failed") from exc
    if registered is not None:
        raise FinalProviderAttemptQualificationError("provider_protocol_execution_authority_conflict")
    return []


def _validate_tool_registry_contract(
    tools: object,
    tool_choice: object,
    audit: Mapping[str, Any],
) -> None:
    if type(tools) is not list:
        raise FinalProviderAttemptQualificationError("final_request_tools_invalid")
    coverage = audit.get("final_request_evidence_coverage")
    if not isinstance(coverage, Mapping):
        raise FinalProviderAttemptQualificationError("final_request_evidence_coverage_missing")
    executable_tools = _validate_provider_protocol_contract(
        tools=tools,
        tool_choice=tool_choice,
        coverage=coverage,
    )
    registry = coverage.get("tool_schema_registry_coverage")
    if not isinstance(registry, Mapping):
        raise FinalProviderAttemptQualificationError("tool_registry_coverage_missing")
    if registry.get("missing_schema_tools"):
        raise FinalProviderAttemptQualificationError("tool_registry_schema_missing")
    if not executable_tools:
        return
    observed_names: set[str] = set()
    for tool in executable_tools:
        if type(tool) is not dict:
            raise FinalProviderAttemptQualificationError("tool_registry_contract_drift")
        name = _tool_schema_name(tool)
        if not name:
            raise FinalProviderAttemptQualificationError("tool_schema_name_missing")
        if name in observed_names:
            raise FinalProviderAttemptQualificationError("tool_registry_tool_name_duplicate")
        observed_names.add(name)
        try:
            expected = ToolSpecRegistry.get_llm_schema(
                name,
                include_arg_aliases=True,
                deterministic=True,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            raise FinalProviderAttemptQualificationError("tool_registry_lookup_failed") from exc
        if expected is None:
            raise FinalProviderAttemptQualificationError("tool_registry_contract_missing")
        if set(expected) != set(tool):
            raise FinalProviderAttemptQualificationError("tool_registry_contract_drift")
        if any(tool.get(key) != value for key, value in expected.items() if key != "function"):
            raise FinalProviderAttemptQualificationError("tool_registry_contract_drift")
        expected_function = expected.get("function")
        actual_function = tool.get("function")
        if not isinstance(expected_function, Mapping) or not isinstance(actual_function, Mapping):
            raise FinalProviderAttemptQualificationError("tool_registry_contract_drift")
        if set(expected_function) != set(actual_function):
            raise FinalProviderAttemptQualificationError("tool_registry_function_contract_drift")
        if any(actual_function.get(key) != value for key, value in expected_function.items() if key != "parameters"):
            raise FinalProviderAttemptQualificationError("tool_registry_function_contract_drift")
        expected_parameters = expected_function.get("parameters")
        actual_parameters = actual_function.get("parameters")
        if not isinstance(expected_parameters, Mapping) or not isinstance(actual_parameters, Mapping):
            raise FinalProviderAttemptQualificationError("tool_registry_parameters_drift")
        if set(expected_parameters) != set(actual_parameters):
            raise FinalProviderAttemptQualificationError("tool_registry_parameters_drift")
        if any(
            actual_parameters.get(key) != value for key, value in expected_parameters.items() if key != "properties"
        ):
            raise FinalProviderAttemptQualificationError("tool_registry_parameters_drift")
        expected_properties = expected_parameters.get("properties")
        actual_properties = actual_parameters.get("properties")
        if not isinstance(expected_properties, Mapping) or not isinstance(actual_properties, Mapping):
            raise FinalProviderAttemptQualificationError("tool_registry_arg_aliases_missing")
        if set(expected_properties) - set(actual_properties):
            raise FinalProviderAttemptQualificationError("tool_registry_arg_aliases_missing")
        if set(actual_properties) - set(expected_properties):
            raise FinalProviderAttemptQualificationError("tool_registry_arg_aliases_drift")
        for property_name, expected_property in expected_properties.items():
            actual_property = actual_properties.get(property_name)
            if not isinstance(expected_property, Mapping) or not isinstance(actual_property, Mapping):
                raise FinalProviderAttemptQualificationError("tool_registry_arg_contract_drift")
            _validate_registry_property_contract(
                tool_name=name,
                property_name=property_name,
                expected=expected_property,
                actual=actual_property,
            )
    schema_hash = str(registry.get("schema_hash") or "")
    if CONTEXT_HASH_PATTERN.fullmatch(schema_hash) is None:
        raise FinalProviderAttemptQualificationError("tool_registry_schema_hash_invalid")
    expected_schema_hash = hashlib.sha256(
        json.dumps(executable_tools, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    if schema_hash != expected_schema_hash:
        raise FinalProviderAttemptQualificationError("tool_registry_schema_hash_mismatch")
    if registry.get("registry_source") != "polaris.kernelone.tool_execution.ToolSpecRegistry":
        raise FinalProviderAttemptQualificationError("tool_registry_source_mismatch")
    if registry.get("aliases_present") is not True or registry.get("arg_aliases_present") is not True:
        raise FinalProviderAttemptQualificationError("tool_registry_alias_coverage_unproven")


def _audit_nonnegative_int(audit: Mapping[str, Any], key: str) -> int:
    value = audit.get(key)
    if type(value) is not int or value < 0:
        raise FinalProviderAttemptQualificationError(f"final_request_{key}_invalid")
    return value


def _validate_token_audit_contract(*, audit: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    messages = payload.get("messages")
    tools = payload.get("tools")
    if type(messages) is not list or type(tools) is not list:
        raise FinalProviderAttemptQualificationError("final_request_projection_invalid")
    if _audit_nonnegative_int(audit, "message_count") != len(messages):
        raise FinalProviderAttemptQualificationError("final_request_message_count_mismatch")
    if _audit_nonnegative_int(audit, "tool_schema_count") != len(tools):
        raise FinalProviderAttemptQualificationError("final_request_tool_schema_count_mismatch")
    message_chars = canonical_message_chars(messages)
    tool_schema_chars = len(json.dumps(tools, ensure_ascii=False, separators=(",", ":")))
    response_format = payload.get("response_format")
    response_format_chars = (
        0 if response_format is None else len(json.dumps(response_format, ensure_ascii=False, separators=(",", ":")))
    )
    if _audit_nonnegative_int(audit, "message_chars") != message_chars:
        raise FinalProviderAttemptQualificationError("final_request_message_chars_mismatch")
    if _audit_nonnegative_int(audit, "tool_schema_chars") != tool_schema_chars:
        raise FinalProviderAttemptQualificationError("final_request_tool_schema_chars_mismatch")
    if _audit_nonnegative_int(audit, "response_format_chars") != response_format_chars:
        raise FinalProviderAttemptQualificationError("final_request_response_format_chars_mismatch")
    message_tokens = _audit_nonnegative_int(audit, "message_token_estimate")
    tool_tokens = _audit_nonnegative_int(audit, "tool_schema_token_estimate")
    response_tokens = _audit_nonnegative_int(audit, "response_format_token_estimate")
    if message_tokens != message_chars // 4:
        raise FinalProviderAttemptQualificationError("final_request_message_token_estimate_mismatch")
    if tool_tokens != tool_schema_chars // 4:
        raise FinalProviderAttemptQualificationError("final_request_tool_schema_token_estimate_mismatch")
    if response_tokens != response_format_chars // 4:
        raise FinalProviderAttemptQualificationError("final_request_response_format_token_estimate_mismatch")
    final_tokens = _audit_nonnegative_int(audit, "final_request_token_estimate")
    if final_tokens <= 0:
        raise FinalProviderAttemptQualificationError("final_request_token_estimate_invalid")
    if message_tokens + tool_tokens + response_tokens != final_tokens:
        raise FinalProviderAttemptQualificationError("final_request_token_estimate_inconsistent")
    window = _audit_nonnegative_int(audit, "context_window_tokens")
    if window <= 0:
        raise FinalProviderAttemptQualificationError("final_request_context_window_invalid")
    utilization = audit.get("context_window_utilization")
    if isinstance(utilization, bool) or not isinstance(utilization, (int, float)) or utilization < 0:
        raise FinalProviderAttemptQualificationError("final_request_context_utilization_invalid")
    if final_tokens > window or utilization > 1:
        raise FinalProviderAttemptQualificationError("final_request_context_clipped")
    headroom = _audit_nonnegative_int(audit, "available_token_headroom")
    if headroom != max(0, window - final_tokens):
        raise FinalProviderAttemptQualificationError("final_request_token_headroom_mismatch")
    if utilization != round(final_tokens / window, 4):
        raise FinalProviderAttemptQualificationError("final_request_context_utilization_mismatch")


def _validate_native_token_audit_contract(
    *,
    audit: Mapping[str, Any],
    body: Mapping[str, Any],
    native_protocol: str,
) -> None:
    """Recompute final request metrics from the exact provider-native body."""

    window = _audit_nonnegative_int(audit, "context_window_tokens")
    if window <= 0:
        raise FinalProviderAttemptQualificationError("final_request_context_window_invalid")
    try:
        expected = provider_native_request_metrics(
            body=body,
            native_protocol=native_protocol,
            context_window_tokens=window,
        )
    except (TypeError, ValueError) as exc:
        raise FinalProviderAttemptQualificationError("final_physical_request_metrics_invalid") from exc
    for key, expected_value in expected.items():
        if audit.get(key) != expected_value:
            raise FinalProviderAttemptQualificationError(f"final_request_native_{key}_mismatch")
    if _audit_nonnegative_int(audit, "final_request_token_estimate") <= 0:
        raise FinalProviderAttemptQualificationError("final_request_token_estimate_invalid")


def _coverage_string_list(coverage: Mapping[str, Any], key: str) -> list[str]:
    values = coverage.get(key)
    if type(values) is not list or any(type(value) is not str or not value for value in values):
        raise FinalProviderAttemptQualificationError(f"final_request_{key}_invalid")
    if len(set(values)) != len(values):
        raise FinalProviderAttemptQualificationError(f"final_request_{key}_duplicate")
    return list(values)


def _validate_authoritative_evidence_coverage(
    *,
    coverage: Mapping[str, Any],
    binding: FactoryRoleEvidenceBindingV1,
    payload: Mapping[str, Any],
    context_snapshot_ref: str,
) -> None:
    """Recompute role slots and tool availability from frozen authority."""

    role = str(payload.get("role") or "")
    try:
        binding.policy_facts.__post_init__()
        policy = role_final_request_policy(role)
    except (AttributeError, TypeError, ValueError) as exc:
        raise FinalProviderAttemptQualificationError("final_request_role_policy_invalid") from exc
    required_refs = _coverage_string_list(coverage, "required_refs")
    included_refs = _coverage_string_list(coverage, "included_refs")
    missing_refs = _coverage_string_list(coverage, "missing_required_refs")
    authoritative_required = [
        final_request_evidence_ref_for_requirement(ref_kind) or ref_kind for ref_kind in policy.required_present_slots
    ]
    authoritative_present = [
        final_request_evidence_ref_for_requirement(slot.ref_kind) or slot.ref_kind
        for slot in binding.policy_facts.slots
        if slot.state == "present"
    ]
    if required_refs != authoritative_required:
        raise FinalProviderAttemptQualificationError("final_request_role_required_refs_drift")
    if included_refs != authoritative_present:
        raise FinalProviderAttemptQualificationError("final_request_role_included_refs_drift")
    recomputed_missing_refs = [ref for ref in required_refs if ref not in included_refs]
    if missing_refs != recomputed_missing_refs:
        raise FinalProviderAttemptQualificationError("final_request_missing_refs_formula_mismatch")
    if str(coverage.get("context_snapshot_ref") or "") != context_snapshot_ref:
        raise FinalProviderAttemptQualificationError("context_snapshot_ref_audit_mismatch")

    tools = payload.get("tools")
    if type(tools) is not list:
        raise FinalProviderAttemptQualificationError("final_request_tools_invalid")
    available_tools = [_tool_schema_name(tool) for tool in tools]
    if any(not name for name in available_tools) or len(set(available_tools)) != len(available_tools):
        raise FinalProviderAttemptQualificationError("final_request_available_tools_invalid")
    required_tools = _coverage_string_list(coverage, "required_tools")
    authoritative_required_tools = payload.get("required_tools")
    if type(authoritative_required_tools) is not list or any(
        type(tool) is not str or not tool for tool in authoritative_required_tools
    ):
        raise FinalProviderAttemptQualificationError("final_request_required_tools_authority_invalid")
    if required_tools != authoritative_required_tools:
        raise FinalProviderAttemptQualificationError("final_request_required_tools_authority_drift")
    audited_available_tools = _coverage_string_list(coverage, "available_tools")
    missing_tools = _coverage_string_list(coverage, "missing_required_tools")
    if audited_available_tools != available_tools:
        raise FinalProviderAttemptQualificationError("final_request_available_tools_drift")
    recomputed_missing_tools = [tool for tool in required_tools if tool not in available_tools]
    if missing_tools != recomputed_missing_tools:
        raise FinalProviderAttemptQualificationError("final_request_missing_tools_formula_mismatch")
    recomputed_pass = bool(
        coverage.get("role_identity_ok") is True and not recomputed_missing_refs and not recomputed_missing_tools
    )
    if coverage.get("pass") is not recomputed_pass or not recomputed_pass:
        raise FinalProviderAttemptQualificationError("final_request_evidence_coverage_failed")


def _read_context_snapshot(*, workspace: str, context_snapshot_ref: str) -> dict[str, Any]:
    try:
        context_snapshot_ref = validate_context_hash(context_snapshot_ref)
    except ValueError as exc:
        raise FinalProviderAttemptQualificationError("context_snapshot_ref_invalid") from exc
    repository = ContextSnapshotAuditPinRepository(workspace=workspace)
    try:
        guarded = read_guarded_regular_file_snapshot(
            repository.contexts_root,
            f"{context_snapshot_ref[:2]}/{context_snapshot_ref}",
            _MAX_CONTEXT_SNAPSHOT_BYTES,
        )
        raw = guarded.content.decode("utf-8")
        if hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24] != context_snapshot_ref:
            raise FinalProviderAttemptQualificationError("context_snapshot_hash_mismatch")
        payload = json.loads(raw)
    except FinalProviderAttemptQualificationError:
        raise
    except (
        GuardedRegularFileSnapshotError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise FinalProviderAttemptQualificationError("context_snapshot_unreadable") from exc
    if type(payload) is not dict:
        raise FinalProviderAttemptQualificationError("context_snapshot_payload_invalid")
    return payload


def _validate_final_physical_context_snapshot(
    *,
    workspace: str,
    context_snapshot_ref: str,
    wire_request: Mapping[str, Any],
    physical_route_authority: Mapping[str, Any],
) -> None:
    """Bind the public ContextOS ref to this exact provider-native attempt."""

    snapshot = _read_context_snapshot(
        workspace=workspace,
        context_snapshot_ref=context_snapshot_ref,
    )
    if snapshot.get("schema_version") != "llm.final_physical_provider_request_context.v1":
        raise FinalProviderAttemptQualificationError("final_physical_context_snapshot_schema_mismatch")
    provider_request = snapshot.get("provider_request")
    if not isinstance(provider_request, Mapping):
        raise FinalProviderAttemptQualificationError("context_snapshot_provider_request_missing")
    redacted_wire = redact_provider_transport(dict(wire_request))
    redacted_route = redact_provider_transport(dict(physical_route_authority))
    wire_hash = _canonical_mapping_hash(redacted_wire)
    if provider_request.get("final_physical_request") != redacted_wire:
        raise FinalProviderAttemptQualificationError("context_snapshot_physical_request_drift")
    if provider_request.get("physical_route_authority") != redacted_route:
        raise FinalProviderAttemptQualificationError("context_snapshot_physical_route_drift")
    if provider_request.get("final_physical_wire_hash") != wire_hash:
        raise FinalProviderAttemptQualificationError("context_snapshot_physical_wire_hash_drift")
    snapshot_audit = provider_request.get("final_request_context_audit")
    if not isinstance(snapshot_audit, Mapping) or snapshot_audit.get("final_physical_wire_hash") != wire_hash:
        raise FinalProviderAttemptQualificationError("context_snapshot_physical_audit_drift")


def _stable_snapshot_audit_projection(audit: Mapping[str, Any]) -> dict[str, Any]:
    """Project fields knowable both before and after snapshot-ref publication."""

    stable_keys = (
        "schema_version",
        "message_count",
        "message_chars",
        "message_token_estimate",
        "tool_schema_count",
        "tool_schema_chars",
        "tool_schema_token_estimate",
        "response_format_chars",
        "response_format_token_estimate",
        "request_control_chars",
        "request_control_token_estimate",
        "final_request_token_estimate",
        "context_window_tokens",
        "context_window_utilization",
        "available_token_headroom",
        "audit_scope",
        "native_protocol",
        "final_semantic_request_hash",
        "request_identity",
        "context_os_audit",
    )
    projection = {key: audit.get(key) for key in stable_keys}
    coverage = audit.get("final_request_evidence_coverage")
    if not isinstance(coverage, Mapping):
        projection["final_request_evidence_coverage"] = None
        return projection
    coverage_keys = (
        "schema_version",
        "request_hash",
        "role_id",
        "expected_role_id",
        "role_identity_ok",
        "required_tools",
        "allowed_tools",
        "available_tools",
        "missing_required_tools",
        "tool_evidence_slots",
        "removed_allowed_tools",
        "tool_surface",
        "unexpected_tool_pruning",
        "tool_schema_registry_coverage",
        "structured_evidence",
        "workflow_chain",
        "ledger_evidence",
        "redaction_safety",
    )
    stable_coverage = {key: coverage.get(key) for key in coverage_keys}
    for key in ("required_refs", "included_refs", "missing_required_refs"):
        values = coverage.get(key)
        stable_coverage[key] = (
            [str(value) for value in values if str(value) != "context_snapshot_ref"]
            if isinstance(values, list)
            else None
        )
    projection["final_request_evidence_coverage"] = stable_coverage
    return projection


def context_snapshot_matches_frozen_attempt(
    *,
    workspace: str,
    context_snapshot_ref: str,
    frozen: FactoryRoleFrozenSemanticRequestV1,
) -> bool:
    """Return whether an existing readable ContextOS ref is this exact freeze."""

    try:
        snapshot = _read_context_snapshot(
            workspace=workspace,
            context_snapshot_ref=context_snapshot_ref,
        )
        payload = _frozen_payload(frozen)
    except FinalProviderAttemptQualificationError:
        return False
    provider_request = snapshot.get("provider_request")
    return bool(
        snapshot.get("trace_id") == frozen.identity.run_id
        and snapshot.get("call_id") == frozen.identity.call_id
        and snapshot.get("messages") == payload.get("messages")
        and isinstance(provider_request, Mapping)
        and str(provider_request.get("role") or "") == str(payload.get("role") or "")
        and str(provider_request.get("provider_id") or "") == str(payload.get("provider_id") or "")
        and str(provider_request.get("model") or "") == str(payload.get("model") or "")
        and provider_request.get("factory_final_request") == final_request_snapshot_evidence(frozen)
    )


def qualify_final_provider_request(
    *,
    workspace: str,
    frozen: FactoryRoleFrozenSemanticRequestV1,
    binding: FactoryRoleEvidenceBindingV1,
    final_request_context_audit: Mapping[str, Any],
    context_snapshot_ref: str,
) -> dict[str, Any]:
    """Qualify immutable semantic evidence before any reservation is minted."""

    payload = _frozen_payload(frozen)
    if type(binding) is not FactoryRoleEvidenceBindingV1:
        raise FinalProviderAttemptQualificationError("factory_evidence_binding_type_invalid")
    binding_error = binding.validation_error(expected_role=str(payload.get("role") or ""))
    if binding_error:
        raise FinalProviderAttemptQualificationError("factory_evidence_binding_invalid")
    audit = json.loads(
        json.dumps(
            dict(final_request_context_audit),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    if audit.get("schema_version") != "llm.final_request_context_audit.v1":
        raise FinalProviderAttemptQualificationError("final_request_context_audit_schema_invalid")
    if audit.get("final_semantic_request_hash") != frozen.final_semantic_request_hash:
        raise FinalProviderAttemptQualificationError("final_request_audit_hash_mismatch")
    expected_identity = final_request_snapshot_evidence(frozen)["request_identity"]
    if audit.get("request_identity") != expected_identity:
        raise FinalProviderAttemptQualificationError("final_request_audit_identity_mismatch")
    context_quality = audit.get("context_quality")
    if isinstance(context_quality, Mapping):
        findings = context_quality.get("findings")
        if isinstance(findings, list) and any(
            isinstance(finding, Mapping) and str(finding.get("severity") or "").strip().lower() == "error"
            for finding in findings
        ):
            raise FinalProviderAttemptQualificationError("final_request_context_quality_failed")
    context_os_audit = audit.get("context_os_audit")
    if (
        isinstance(context_os_audit, Mapping)
        and context_os_audit.get("expected") is True
        and context_os_audit.get("ok") is not True
    ):
        raise FinalProviderAttemptQualificationError("final_request_context_os_audit_failed")
    snapshot = _read_context_snapshot(workspace=workspace, context_snapshot_ref=context_snapshot_ref)
    snapshot_provider_request = snapshot.get("provider_request")
    if audit.get("audit_scope") == "provider_native_wire":
        if not isinstance(snapshot_provider_request, Mapping):
            raise FinalProviderAttemptQualificationError("context_snapshot_provider_request_missing")
        final_wire = snapshot_provider_request.get("final_physical_request")
        physical_route = snapshot_provider_request.get("physical_route_authority")
        if not isinstance(final_wire, Mapping) or not isinstance(physical_route, Mapping):
            raise FinalProviderAttemptQualificationError("context_snapshot_final_physical_request_missing")
        native_body = final_wire.get("body")
        if not isinstance(native_body, Mapping):
            raise FinalProviderAttemptQualificationError("context_snapshot_final_physical_body_missing")
        _validate_native_token_audit_contract(
            audit=audit,
            body=native_body,
            native_protocol=str(physical_route.get("native_protocol") or ""),
        )
    else:
        _validate_token_audit_contract(audit=audit, payload=payload)
    coverage = audit.get("final_request_evidence_coverage")
    if not isinstance(coverage, Mapping):
        raise FinalProviderAttemptQualificationError("final_request_evidence_coverage_failed")
    role = str(payload.get("role") or "")
    if coverage.get("role_identity_ok") is not True or str(coverage.get("role_id") or "") != role:
        raise FinalProviderAttemptQualificationError("final_request_role_identity_mismatch")
    if str(coverage.get("expected_role_id") or "") != role:
        raise FinalProviderAttemptQualificationError("final_request_expected_role_identity_mismatch")
    _validate_authoritative_evidence_coverage(
        coverage=coverage,
        binding=binding,
        payload=payload,
        context_snapshot_ref=context_snapshot_ref,
    )

    messages = payload.get("messages")
    if type(messages) is not list or not messages or type(messages[0]) is not dict:
        raise FinalProviderAttemptQualificationError("final_request_messages_missing")
    first = messages[0]
    expected_role_marker = f"polaris.role_identity.v1:{role}"
    content = str(first.get("content") or "")
    if first.get("role") != "system" or expected_role_marker not in content:
        raise FinalProviderAttemptQualificationError("final_request_role_identity_mismatch")
    if content.count(_EVIDENCE_BEGIN) != 1 or content.count(_EVIDENCE_END) != 1:
        raise FinalProviderAttemptQualificationError("final_request_evidence_anchor_invalid")
    from polaris.kernelone.events.final_request_evidence import render_role_final_request_policy_facts

    expected_policy_line = render_role_final_request_policy_facts(binding.policy_facts)
    expected_block = f"{_EVIDENCE_BEGIN}\n{expected_policy_line}\n{_EVIDENCE_END}"
    if expected_block not in content or not content.endswith(expected_block):
        raise FinalProviderAttemptQualificationError("final_request_evidence_slots_drift")
    _validate_tool_registry_contract(
        payload.get("tools"),
        payload.get("tool_choice"),
        audit,
    )

    if snapshot.get("trace_id") != frozen.identity.run_id or snapshot.get("call_id") != frozen.identity.call_id:
        raise FinalProviderAttemptQualificationError("context_snapshot_attempt_mismatch")
    if snapshot.get("messages") != messages:
        raise FinalProviderAttemptQualificationError("context_snapshot_messages_mismatch")
    provider_request = snapshot_provider_request
    if not isinstance(provider_request, Mapping):
        raise FinalProviderAttemptQualificationError("context_snapshot_provider_request_missing")
    if (
        str(provider_request.get("role") or "") != role
        or str(provider_request.get("provider_id") or "") != str(payload.get("provider_id") or "")
        or str(provider_request.get("model") or "") != str(payload.get("model") or "")
    ):
        raise FinalProviderAttemptQualificationError("context_snapshot_provider_identity_mismatch")
    if provider_request.get("factory_final_request") != final_request_snapshot_evidence(frozen):
        raise FinalProviderAttemptQualificationError("context_snapshot_frozen_request_mismatch")
    snapshot_audit = provider_request.get("final_request_context_audit")
    if not isinstance(snapshot_audit, Mapping):
        raise FinalProviderAttemptQualificationError("context_snapshot_final_request_audit_missing")
    if _stable_snapshot_audit_projection(snapshot_audit) != _stable_snapshot_audit_projection(audit):
        raise FinalProviderAttemptQualificationError("context_snapshot_final_request_audit_mismatch")
    return dict(audit)


def _mint_final_provider_attempt_qualification_proof(
    *,
    workspace: str,
    frozen: FactoryRoleFrozenSemanticRequestV1,
    binding: FactoryRoleEvidenceBindingV1,
    final_request_context_audit: Mapping[str, Any],
    context_snapshot_ref: str,
    wire_request: Mapping[str, Any],
    physical_route_authority: Mapping[str, Any],
) -> _FinalProviderAttemptQualificationProofV1:
    """Private sidecar mint; the mint itself executes all qualification."""

    return _FinalProviderAttemptQualificationProofV1._mint(
        workspace=workspace,
        frozen=frozen,
        binding=binding,
        context_snapshot_ref=context_snapshot_ref,
        final_request_context_audit=final_request_context_audit,
        wire_request=wire_request,
        physical_route_authority=physical_route_authority,
    )


def validate_exact_wire_before_reservation(
    *,
    frozen: FactoryRoleFrozenSemanticRequestV1,
    wire_request: Mapping[str, Any],
    physical_route_authority: Mapping[str, Any],
) -> None:
    payload = _frozen_payload(frozen)
    body = wire_request.get("body")
    if not isinstance(body, Mapping):
        raise FinalProviderAttemptQualificationError("physical_wire_body_missing")
    if physical_route_authority.get("schema_version") != "llm.factory_physical_provider_route.v2":
        raise FinalProviderAttemptQualificationError("physical_provider_route_authority_invalid")
    expected_route = {
        "provider_id": str(payload.get("provider_id") or ""),
        "model": str(payload.get("model") or ""),
        "mode": "stream" if payload.get("stream") is True else "invoke",
    }
    if any(str(physical_route_authority.get(key) or "") != value for key, value in expected_route.items()):
        raise FinalProviderAttemptQualificationError("physical_provider_route_authority_drift")
    expected_body = physical_route_authority.get("expected_body")
    if not isinstance(expected_body, Mapping):
        raise FinalProviderAttemptQualificationError("physical_provider_route_body_invalid")
    if dict(body) != dict(expected_body):
        drifted_keys = sorted(key for key in set(body).union(expected_body) if body.get(key) != expected_body.get(key))
        if len(drifted_keys) == 1 and drifted_keys[0] in {
            "messages",
            "tools",
            "tool_choice",
            "response_format",
            "model",
            "temperature",
            "max_tokens",
            "stream",
        }:
            raise FinalProviderAttemptQualificationError(f"physical_wire_{drifted_keys[0]}_drift")
        raise FinalProviderAttemptQualificationError(
            f"physical_wire_body_drift:{','.join(str(key) for key in drifted_keys)}"
        )
    endpoint = str(wire_request.get("endpoint") or "").strip()
    expected_endpoint = str(physical_route_authority.get("exact_endpoint") or "").strip()
    if not endpoint or endpoint != expected_endpoint:
        raise FinalProviderAttemptQualificationError("physical_wire_endpoint_drift")
    transport = wire_request.get("transport")
    if not isinstance(transport, Mapping):
        raise FinalProviderAttemptQualificationError("physical_wire_transport_drift")
    transport_kind = str(transport.get("kind") or "")
    expected_transport_kind = str(physical_route_authority.get("exact_transport_kind") or "")
    if not transport_kind or transport_kind != expected_transport_kind:
        raise FinalProviderAttemptQualificationError("physical_wire_transport_drift")


__all__ = [
    "FINAL_PROVIDER_ATTEMPT_QUALIFICATION_REJECTION_SCHEMA",
    "FinalProviderAttemptQualificationError",
    "FinalProviderAttemptQualificationRejectionV1",
    "append_qualification_rejection",
    "context_snapshot_matches_frozen_attempt",
    "final_gate_semantic_request",
    "qualification_rejection_stream",
    "qualify_final_provider_request",
    "validate_exact_wire_before_reservation",
]
