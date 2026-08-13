"""Resident-AGI participation helpers for LLM request preparation.

Extracted losslessly from ``request_preparer.py``. These pure functions resolve
how the resident-AGI (advisory, non-authoritative) subsystem participates in a
turn: participation scopes/policy, capability surface, and the audit context
derived from a context override. They are module-level functions consumed by
``LLMRequestPreparer`` methods and by each other; moving them into this sibling
module changes no behavior (the class resolves them via the module namespace).
"""

from __future__ import annotations

from typing import Any

from ._value_coercion import _bool_option, _mapping, _sequence_len, _string_list

__all__ = [
    "_resident_agi_audit_context_from_override",
    "_resident_agi_capability_surface",
    "_resident_agi_enabled_value",
    "_resident_agi_participation_flag_values",
    "_resident_agi_participation_policy",
    "_resident_agi_participation_scope_key",
    "_resident_agi_participation_scope_keys",
    "_resident_agi_participation_scopes",
]


_RESIDENT_AGI_ENABLE_KEYS = ("resident_agi_enabled", "enable_resident_agi", "agi_enabled")
_RESIDENT_AGI_DEFAULT_SCOPES = (
    "final_request_audit",
    "decision_trace",
    "capability_surface",
    "decision_boundary",
)
_RESIDENT_AGI_PARTICIPATION_FLAGS = (
    "final_request_audit",
    "quality_gate_response",
    "architecture_option_selection",
    "evidence_interface_selection",
    "goal_promotion",
    "decision_trace",
    "capability_surface",
    "decision_boundary",
)


def _resident_agi_participation_scope_key(value: Any) -> str:
    return str(value or "").strip().lower().replace(".", "_").replace("-", "_").replace(" ", "_")


def _resident_agi_participation_scope_keys(scopes: list[str]) -> set[str]:
    result: set[str] = set()
    for scope in scopes:
        token = str(scope or "").strip()
        if token:
            result.add(token)
        normalized = _resident_agi_participation_scope_key(token)
        if normalized:
            result.add(normalized)
    return result


def _resident_agi_participation_policy(override: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    for candidate in (
        override.get("resident_agi_participation"),
        override.get("resident_agi_participation_policy"),
        override.get("agi_participation"),
        metadata.get("resident_agi_participation"),
    ):
        if isinstance(candidate, dict):
            return dict(candidate)
    return {}


def _resident_agi_participation_flag_values(policy: dict[str, Any]) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for candidate in (
        policy.get("configured_participation"),
        policy.get("automatic_participation"),
        policy.get("participation"),
    ):
        if not isinstance(candidate, dict):
            continue
        for key, value in candidate.items():
            normalized = _resident_agi_participation_scope_key(key)
            if normalized:
                flags[normalized] = _bool_option(value, default=False)
    for key, value in policy.items():
        normalized = _resident_agi_participation_scope_key(key)
        if not normalized or normalized in {
            "automatic_participation",
            "configured_participation",
            "participation",
        }:
            continue
        if isinstance(value, bool):
            flags[normalized] = value
    return flags


def _resident_agi_enabled_value(
    *,
    override: dict[str, Any],
    metadata: dict[str, Any],
    policy: dict[str, Any],
    default: bool,
) -> bool:
    for key in _RESIDENT_AGI_ENABLE_KEYS:
        if key in override:
            return _bool_option(override.get(key), default=default)
        if key in metadata:
            return _bool_option(metadata.get(key), default=default)
    if "enabled" in policy:
        return _bool_option(policy.get("enabled"), default=default)
    return default


def _resident_agi_participation_scopes(
    *,
    override: dict[str, Any],
    metadata: dict[str, Any],
    policy: dict[str, Any],
    enabled: bool,
    has_agi_payload: bool,
) -> list[str]:
    scopes: list[str] = []
    for candidate in (
        policy.get("scopes"),
        policy.get("participates_in"),
        policy.get("allowed_scopes"),
        override.get("resident_agi_participation_scopes"),
        metadata.get("resident_agi_participation_scopes"),
    ):
        for token in _string_list(candidate):
            if token not in scopes:
                scopes.append(token)
    if enabled and has_agi_payload and not scopes:
        scopes.extend(_RESIDENT_AGI_DEFAULT_SCOPES)
    return scopes


def _resident_agi_capability_surface(override: dict[str, Any], audit_pack: dict[str, Any]) -> dict[str, Any]:
    raw_surface = override.get("resident_agi_capability_surface") or audit_pack.get("capability_surface")
    surface = _mapping(raw_surface)
    if surface:
        return surface
    generic_surface = _mapping(override.get("capability_surface"))
    schema_version = str(generic_surface.get("schema_version") or "")
    role_id = str(generic_surface.get("role_id") or "")
    if schema_version.startswith("resident.agi") or role_id == "resident_agi":
        return generic_surface
    return {}


def _resident_agi_audit_context_from_override(override: Any) -> dict[str, Any]:
    if not isinstance(override, dict):
        return {}
    audit_pack = _mapping(override.get("resident_agi_audit_pack"))
    capability_surface = _resident_agi_capability_surface(override, audit_pack)
    decision_contract = _mapping(override.get("resident_agi_decision_contract"))
    boundary_summary = _mapping(audit_pack.get("boundary_summary") or override.get("resident_agi_boundary_summary"))
    metadata = _mapping(override.get("metadata"))
    policy = _resident_agi_participation_policy(override, metadata)
    has_explicit_enabled = any(key in override or key in metadata for key in _RESIDENT_AGI_ENABLE_KEYS) or (
        "enabled" in policy
    )
    has_agi_metadata = any(str(key).startswith("resident_agi_") for key in metadata)
    has_agi_payload = bool(
        audit_pack or capability_surface or decision_contract or boundary_summary or has_agi_metadata
    )
    if not has_agi_payload and not policy and not has_explicit_enabled:
        return {}
    enabled = _resident_agi_enabled_value(
        override=override,
        metadata=metadata,
        policy=policy,
        default=has_agi_payload,
    )
    participation_scopes = _resident_agi_participation_scopes(
        override=override,
        metadata=metadata,
        policy=policy,
        enabled=enabled,
        has_agi_payload=has_agi_payload,
    )
    decision_boundaries = capability_surface.get("decision_boundaries")
    participation_scope_keys = _resident_agi_participation_scope_keys(participation_scopes)
    normalized_policy_values = _resident_agi_participation_flag_values(policy)
    participation = {}
    for name in _RESIDENT_AGI_PARTICIPATION_FLAGS:
        raw_policy_value = policy.get(name)
        if raw_policy_value is None:
            raw_policy_value = normalized_policy_values.get(name)
        participation[name] = _bool_option(raw_policy_value, default=name in participation_scope_keys)
    automatic_participation_enabled = _bool_option(
        policy.get("automatic_participation_enabled"),
        default=_bool_option(policy.get("configured_enabled"), default=enabled),
    )
    return {
        "schema_version": "resident.agi_audit_context.v1",
        "source": "roles.kernel.llm_caller.context_override",
        "role_id": "resident_agi",
        "enabled": enabled,
        "role_turn_enabled": _bool_option(policy.get("role_turn_enabled"), default=enabled),
        "manual_role_turn_requested": _bool_option(policy.get("manual_role_turn_requested"), default=False),
        "automatic_participation_enabled": automatic_participation_enabled,
        "configured_enabled": _bool_option(policy.get("configured_enabled"), default=automatic_participation_enabled),
        "configured_scopes": _string_list(policy.get("configured_scopes")),
        "required_role_turn_scopes": _string_list(policy.get("required_role_turn_scopes")),
        "participation_scopes": participation_scopes,
        "participation": participation,
        "audit_pack_schema_version": str(audit_pack.get("schema_version") or ""),
        "decision_contract_schema_version": str(decision_contract.get("schema_version") or ""),
        "decision_capability_id": str(
            decision_contract.get("decision_capability_id")
            or metadata.get("resident_agi_selected_decision_capability")
            or ""
        ),
        "capability_surface_schema_version": str(capability_surface.get("schema_version") or ""),
        "decision_capability_registry_schema_version": str(
            _mapping(capability_surface.get("decision_capability_registry")).get("schema_version") or ""
        ),
        "decision_boundary_schema": str(
            capability_surface.get("decision_boundary_schema") or boundary_summary.get("decision_boundary_schema") or ""
        ),
        "decision_boundary_count": _sequence_len(decision_boundaries),
        "role_runtime_required": bool(
            metadata.get("resident_agi_role_runtime_required")
            or metadata.get("resident_agi_contextos_required")
            or metadata.get("resident_agi_turn_engine_required")
        ),
    }
