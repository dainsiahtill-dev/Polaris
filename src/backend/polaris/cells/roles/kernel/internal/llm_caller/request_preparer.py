"""LLM request preparation service.

Owns provider request construction and fallback request shaping for
``LLMInvoker``.  This module deliberately has no public call/call_stream facade;
``LLMCaller`` is a removed facade; request construction belongs here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.kernel.internal.forced_tool_scope import augment_forced_transaction_tool_definitions
from polaris.cells.roles.kernel.internal.interaction_contract import (
    ProviderCapabilities,
    build_interaction_contract,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
    FactoryRoleEvidenceCutoffAckV1,
    FactoryRoleEvidenceCutoffPort,
    FactoryRoleEvidenceCutoffProofV1,
    FactoryRoleEvidenceCutoffRequestV1,
    FactoryRoleFrozenSemanticRequestV1,
    FactoryRoleSemanticCandidateV1,
    FactoryRoleSemanticRequestIdentityV1,
    get_factory_role_evidence_authority_binding,
)
from polaris.kernelone.audit.context_os_prompt import audit_context_os_prompt_messages
from polaris.kernelone.context.context_os.decision_log import build_context_result_id
from polaris.kernelone.context.projection_engine import is_empty_run_card_message
from polaris.kernelone.events.final_request_evidence import (
    FINAL_REQUEST_EVIDENCE_ANCHOR_SCHEMA,
    FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA,
    ROLE_FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA,
    ROLE_FINAL_REQUEST_POLICY_FACTS_SCHEMA,
    render_role_final_request_policy_facts,
)
from polaris.kernelone.llm.budget_policy import (
    REASONING_TRUNCATION_RETRY_OUTPUT_TOKENS,
    REQUIRED_TOOL_RETRY_OUTPUT_TOKEN_CAP,
    REQUIRED_TOOL_RETRY_TIMEOUT_SECONDS,
    resolve_execution_budget,
)
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

from .capability_profile import resolve_actor_capability_profile
from .error_handling import (
    append_runtime_fallback_instruction,
    build_text_response_fallback_instruction,
)
from .factory_role_evidence_binding import (
    FactoryRoleEvidenceBindingV1,
    get_factory_role_evidence_binding,
)
from .helpers import (
    _resolve_context_max_tokens_override,
    _resolve_context_timeout_override,
    build_native_response_format,
    build_native_tool_schemas,
    compute_context_summary,
    messages_to_input,
    resolve_max_tokens,
    resolve_platform_retry_max,
    resolve_temperature_with_source,
    resolve_timeout_seconds,
)
from .request_facts import copy_final_request_evidence_context_fields
from .response_types import PreparedLLMRequest

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
    from polaris.cells.roles.profile.public.service import RoleProfile


_TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY = "_transaction_kernel_prebuilt_messages"
_TRANSACTION_KERNEL_PREBUILT_TOKEN_ESTIMATE_KEY = "_transaction_kernel_prebuilt_token_estimate"
_TRANSACTION_KERNEL_PREBUILT_COMPRESSION_APPLIED_KEY = "_transaction_kernel_prebuilt_compression_applied"
_TRANSACTION_KERNEL_PREBUILT_COMPRESSION_STRATEGY_KEY = "_transaction_kernel_prebuilt_compression_strategy"
_TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY = "_transaction_kernel_forced_tool_definitions"
_TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY = "_transaction_kernel_forced_tool_choice"
_PROVIDER_POLICY_KEYS = (
    "allowed_provider_types",
    "allow_provider_types",
    "blocked_provider_types",
    "provider_type_policy",
)
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
# Retry caps/floors are single-sourced in polaris.kernelone.llm.budget_policy
# (blueprint Phase 1); local names kept as compatibility aliases.
# 5th floor (2026-06-15): reserved output budget for the reasoning-truncation re-ask.
_REASONING_TRUNCATION_RETRY_MAX_TOKENS = REASONING_TRUNCATION_RETRY_OUTPUT_TOKENS
_REQUIRED_TOOL_RETRY_MAX_TOKENS = REQUIRED_TOOL_RETRY_OUTPUT_TOKEN_CAP
_REQUIRED_TOOL_RETRY_TIMEOUT_SECONDS = REQUIRED_TOOL_RETRY_TIMEOUT_SECONDS
_CORE_ROLE_IDENTITIES = frozenset({"architect", "pm", "chief_engineer", "director", "qa"})
_ROLE_IDENTITY_MARKER_PREFIX = "polaris.role_identity.v1:"
_ROLE_IDENTITY_TOKEN_RE = re.compile(r"[a-z_][a-z0-9_]*")
_FACTORY_EVIDENCE_PROTOCOL_MARKERS = (
    "polaris.final_request_evidence.v1:begin",
    "polaris.final_request_evidence.v1:end",
    FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA,
    ROLE_FINAL_REQUEST_EVIDENCE_SLOT_SCHEMA,
    FINAL_REQUEST_EVIDENCE_ANCHOR_SCHEMA,
    ROLE_FINAL_REQUEST_POLICY_FACTS_SCHEMA,
)

_FACTORY_EVIDENCE_BEGIN = "polaris.final_request_evidence.v1:begin"
_FACTORY_EVIDENCE_END = "polaris.final_request_evidence.v1:end"


@dataclass(frozen=True, slots=True)
class _FactoryAuthorityEntrySnapshot:
    carrier: FactoryRoleEvidenceAuthorityBindingV1
    schema_version: str
    verification_scope: str
    factory_run_id: str
    role: str
    cutoff_port: FactoryRoleEvidenceCutoffPort
    attempt_budget: int
    execution_authority_hash: str


def _snapshot_factory_authority(
    authority: object,
) -> _FactoryAuthorityEntrySnapshot:
    if type(authority) is not FactoryRoleEvidenceAuthorityBindingV1:
        raise TypeError("factory_role_evidence_authority_binding_exact_type_required")
    typed_authority = cast(FactoryRoleEvidenceAuthorityBindingV1, authority)
    typed_authority.__post_init__()
    return _FactoryAuthorityEntrySnapshot(
        carrier=typed_authority,
        schema_version=typed_authority.schema_version,
        verification_scope=typed_authority.verification_scope,
        factory_run_id=typed_authority.factory_run_id,
        role=typed_authority.role,
        cutoff_port=typed_authority.cutoff_port,
        attempt_budget=typed_authority.attempt_budget,
        execution_authority_hash=typed_authority.execution_authority_hash,
    )


def _revalidate_factory_authority_before_cutoff(
    authority: object,
    snapshot: _FactoryAuthorityEntrySnapshot,
) -> None:
    if type(authority) is not FactoryRoleEvidenceAuthorityBindingV1:
        raise TypeError("factory_role_evidence_authority_binding_exact_type_required")
    typed_authority = cast(FactoryRoleEvidenceAuthorityBindingV1, authority)
    raw_projection = (
        typed_authority.schema_version,
        typed_authority.verification_scope,
        typed_authority.factory_run_id,
        typed_authority.role,
        typed_authority.attempt_budget,
        typed_authority.execution_authority_hash,
    )
    raw_cutoff_port = typed_authority.cutoff_port
    typed_authority.__post_init__()
    entry_projection = (
        snapshot.schema_version,
        snapshot.verification_scope,
        snapshot.factory_run_id,
        snapshot.role,
        snapshot.attempt_budget,
        snapshot.execution_authority_hash,
    )
    if (
        typed_authority is not snapshot.carrier
        or raw_projection != entry_projection
        or raw_cutoff_port is not snapshot.cutoff_port
    ):
        raise RuntimeError("factory_role_evidence_authority_binding_drift")


def is_reasoning_truncation_error(error: str) -> bool:
    """True when an LLM call failed because a reasoning model truncated mid-thought
    (``finish_reason=length``) leaving no visible output / tool call — the 5th-floor
    wall. Matches the canonical message from ``LLMResponseParser.finalize_response``.
    """
    text = str(error or "").lower()
    return "empty visible output" in text and "reasoning truncated" in text


def _normalize_user_message_for_dedupe(value: Any) -> str:
    token = str(value or "")
    token = token.replace("\r\n", "\n").replace("\r", "\n")
    token = token.replace("\ufeff", "").strip()
    return token


def _ensure_current_user_message_final(
    messages: list[dict[str, Any]],
    current_user_instruction: Any,
) -> list[dict[str, Any]]:
    """Keep the active user turn as the final provider-visible instruction."""

    current_user_token = _normalize_user_message_for_dedupe(current_user_instruction)
    if not current_user_token:
        return [dict(message) for message in messages]

    normalized_messages = [dict(message) for message in messages if isinstance(message, dict)]
    last_match_index = -1
    for index, message in enumerate(normalized_messages):
        role = str(message.get("role", "")).strip().lower()
        if role != "user":
            continue
        content_token = _normalize_user_message_for_dedupe(message.get("content", ""))
        if content_token == current_user_token or current_user_token in content_token:
            last_match_index = index

    if last_match_index >= 0:
        current_message = normalized_messages.pop(last_match_index)
        current_message["role"] = "user"
        current_message["content"] = current_user_token
        normalized_messages.append(current_message)
        return normalized_messages

    normalized_messages.append({"role": "user", "content": current_user_token})
    return normalized_messages


def _ensure_core_role_identity(
    messages: list[dict[str, Any]],
    role: str,
    *,
    system_prompt: str,
) -> list[dict[str, Any]]:
    """Inject one canonical first-system identity marker or fail closed."""

    normalized = [dict(message) for message in messages if isinstance(message, dict)]
    canonical_role = str(role or "").strip()
    if canonical_role not in _CORE_ROLE_IDENTITIES:
        return normalized
    if not normalized or str(normalized[0].get("role") or "").strip().lower() != "system":
        normalized.insert(0, {"role": "system", "content": str(system_prompt or "")})
    elif normalized[0].get("role") != "system":
        raise RuntimeError("role_identity_marker_invalid:first_role_must_be_exact_system")
    markers: list[tuple[int, str]] = []
    for index, message in enumerate(normalized):
        content = str(message.get("content") or "")
        for line in content.splitlines():
            if _ROLE_IDENTITY_MARKER_PREFIX not in line:
                continue
            if not line.startswith(_ROLE_IDENTITY_MARKER_PREFIX) or line.count(_ROLE_IDENTITY_MARKER_PREFIX) != 1:
                raise RuntimeError("role_identity_marker_invalid:marker_must_be_complete_line")
            marker_role = line.removeprefix(_ROLE_IDENTITY_MARKER_PREFIX)
            if _ROLE_IDENTITY_TOKEN_RE.fullmatch(marker_role) is None:
                raise RuntimeError("role_identity_marker_invalid:marker_must_be_complete_line")
            markers.append((index, marker_role))
    if markers:
        if len(markers) != 1 or markers[0] != (0, canonical_role):
            raise RuntimeError("role_identity_marker_invalid:wrong_duplicate_or_nonfirst")
        first_content = str(normalized[0].get("content") or "")
        marker = f"{_ROLE_IDENTITY_MARKER_PREFIX}{canonical_role}"
        if first_content != marker and not first_content.endswith(f"\n\n{marker}"):
            raise RuntimeError("role_identity_marker_invalid:marker_must_be_terminal")
        return normalized
    first_content = str(normalized[0].get("content") or "").rstrip()
    marker = f"{_ROLE_IDENTITY_MARKER_PREFIX}{canonical_role}"
    normalized[0]["content"] = f"{first_content}\n\n{marker}" if first_content else marker
    return normalized


def _reject_preexisting_factory_evidence_protocol(
    messages: list[dict[str, Any]],
    *,
    factory_authority_bound: bool,
) -> None:
    """Reject every caller-supplied evidence frame before authority injection."""

    for message in messages:
        content = str(message.get("content") or "")
        if any(marker in content for marker in _FACTORY_EVIDENCE_PROTOCOL_MARKERS):
            error = (
                "factory_role_evidence_protocol_preexisting"
                if factory_authority_bound
                else "factory_role_evidence_protocol_without_binding"
            )
            raise RuntimeError(error)


def _inject_factory_evidence_block(
    messages: list[dict[str, Any]],
    *,
    role: str,
    binding: FactoryRoleEvidenceBindingV1,
) -> list[dict[str, Any]]:
    """Append the exact detached policy line after the first role marker."""

    if not messages or messages[0].get("role") != "system":
        raise RuntimeError("factory_role_evidence_first_system_required")
    marker = f"{_ROLE_IDENTITY_MARKER_PREFIX}{role}"
    first_content = str(messages[0].get("content") or "")
    lines = first_content.splitlines()
    if lines.count(marker) != 1 or marker not in lines:
        raise RuntimeError("factory_role_evidence_role_marker_invalid")
    if first_content != marker and not first_content.endswith(f"\n\n{marker}"):
        raise RuntimeError("factory_role_evidence_role_marker_not_terminal")
    policy_line = render_role_final_request_policy_facts(binding.policy_facts)
    evidence_block = f"{_FACTORY_EVIDENCE_BEGIN}\n{policy_line}\n{_FACTORY_EVIDENCE_END}"
    injected = [dict(message) for message in messages]
    injected[0]["content"] = f"{first_content.rstrip()}\n\n{evidence_block}"
    return injected


def _tool_surface_explicitly_disabled(override: dict[str, Any]) -> bool:
    """True when this call's tool surface is disabled by design (e.g. finalization).

    TransactionKernel marks tool-free calls with an explicit empty forced tool
    definition list plus tool_choice ``none`` (see FinalizationCaller and
    ``_build_context_override_with_prebuilt_messages``).
    """

    forced_definitions = override.get(_TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY)
    forced_choice = str(override.get(_TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY) or "").strip().lower()
    return isinstance(forced_definitions, list) and not forced_definitions and forced_choice == "none"


def _tool_contract_context_fields(override: Any) -> dict[str, Any]:
    """Project runtime tool obligations into AIRequest.context for final-request audit.

    The projection is call-scoped: a call whose tool surface is explicitly
    disabled (finalization-style ``tool_choice=none`` with zero tool schemas)
    must not inherit required-tool semantics from the shared turn context —
    it physically cannot call tools, so projecting ``required_tools`` would
    fail evidence coverage and trigger a futile required_tool_not_called retry.
    """

    if not isinstance(override, dict):
        return {}
    if _tool_surface_explicitly_disabled(override):
        return {}
    required_tools: list[str] = []
    for key in ("required_tools", "task_required_tools"):
        for tool_name in _string_list(override.get(key)):
            if tool_name not in required_tools:
                required_tools.append(tool_name)

    tool_contract: dict[str, Any] = {}
    raw_tool_contract = override.get("tool_contract")
    if isinstance(raw_tool_contract, dict):
        tool_contract = dict(raw_tool_contract)
        for tool_name in _string_list(raw_tool_contract.get("required_tools")):
            if tool_name not in required_tools:
                required_tools.append(tool_name)

    materialization_scope = override.get("director_first_call_materialization_scope")
    if isinstance(materialization_scope, dict) and materialization_scope.get("injected") is True:
        tool_name = str(materialization_scope.get("tool") or "").strip()
        if tool_name and tool_name not in required_tools:
            required_tools.append(tool_name)

    if required_tools:
        contract_required = _string_list(tool_contract.get("required_tools"))
        for tool_name in required_tools:
            if tool_name not in contract_required:
                contract_required.append(tool_name)
        tool_contract["required_tools"] = contract_required

    fields: dict[str, Any] = {}
    if required_tools:
        fields["required_tools"] = required_tools
    if tool_contract:
        fields["tool_contract"] = tool_contract
    return fields


def _copy_provider_policy_options(*, override: Any, request_options: dict[str, Any]) -> None:
    """Copy provider type policy from role-runtime context into AIRequest options."""
    if not isinstance(override, dict):
        return
    for key in _PROVIDER_POLICY_KEYS:
        value = override.get(key)
        if value is not None:
            request_options[key] = value
    policy = override.get("llm_provider_policy")
    if isinstance(policy, dict):
        for key in _PROVIDER_POLICY_KEYS:
            value = policy.get(key)
            if value is not None:
                request_options[key] = value


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _sequence_len(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple, set)) else 0


def _bool_option(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []
    result: list[str] = []
    for item in raw_items:
        token = str(item or "").strip()
        if token and token not in result:
            result.append(token)
    return result


def _bounded_required_tool_retry_max_tokens(value: Any) -> int:
    try:
        current = int(value)
    except (TypeError, ValueError):
        current = 0
    if current <= 0:
        return _REQUIRED_TOOL_RETRY_MAX_TOKENS
    return min(current, _REQUIRED_TOOL_RETRY_MAX_TOKENS)


def _bounded_required_tool_retry_timeout(value: Any) -> float:
    try:
        current = float(value)
    except (TypeError, ValueError):
        current = 0.0
    if current <= 0:
        return _REQUIRED_TOOL_RETRY_TIMEOUT_SECONDS
    return min(current, _REQUIRED_TOOL_RETRY_TIMEOUT_SECONDS)


def _build_execution_budget_projection(
    *,
    profile: Any,
    override: dict[str, Any] | None,
    request_options: dict[str, Any],
    request_max_tokens: int,
    request_timeout_seconds: int,
) -> dict[str, Any]:
    """Project the ACTUAL resolved budget as a ``ResolvedBudgetV1`` payload.

    Observability only (budget policy blueprint Phase 1 step 3): every number
    is copied from the already-resolved sampling values — this projection must
    never change what is sent to the provider. Provenance reuses the SAME
    detection funnels (`_resolve_context_*` helpers) that produced the values.
    """
    context_max_tokens_present = _resolve_context_max_tokens_override(override) is not None
    context_timeout_present = _resolve_context_timeout_override(override) is not None
    role_id = str(getattr(profile, "role_id", "") or "").strip().lower()

    output_floor_tokens = 0
    floor_provenance = "no_explicit_floor_visible"
    if isinstance(override, dict) and override.get("_transaction_kernel_retry_output_budget_bounded"):
        # transaction_factory wrote the retry floor into llm_max_tokens; at this
        # layer the floor and the resolved budget coincide by construction.
        output_floor_tokens = request_max_tokens
        floor_provenance = "transaction_kernel_retry_output_budget_bounded"

    return resolve_execution_budget(
        role_id=role_id,
        context=override,
        request_options=request_options,
        max_output_tokens=int(request_max_tokens),
        output_floor_tokens=int(output_floor_tokens),
        output_floor_provenance=floor_provenance,
        llm_timeout_seconds=float(request_timeout_seconds),
        request_timeout_seconds=float(request_timeout_seconds),
        context_max_tokens_present=context_max_tokens_present,
        context_timeout_present=context_timeout_present,
    ).to_payload()


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


def _with_projection_capability_profile(context: Any, capability_profile: dict[str, Any]) -> Any:
    """Attach provider-bound capabilities to the control plane before projection."""
    override = getattr(context, "context_override", None)
    merged_override = dict(override) if isinstance(override, dict) else {}
    raw_metadata = merged_override.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    metadata["capability_profile"] = dict(capability_profile)
    metadata["capability_profile_source"] = "roles.kernel.llm_caller.pre_projection"
    merged_override["metadata"] = metadata

    try:
        return replace(context, context_override=merged_override)
    except (TypeError, ValueError):
        pass

    if hasattr(context, "__dict__"):
        clone = SimpleNamespace(**vars(context))
        clone.context_override = merged_override
        return clone

    return SimpleNamespace(
        message=str(getattr(context, "message", "") or ""),
        history=tuple(getattr(context, "history", ()) or ()),
        task_id=getattr(context, "task_id", None),
        strategy_receipt=getattr(context, "strategy_receipt", None),
        context_os_snapshot=getattr(context, "context_os_snapshot", None),
        context_override=merged_override,
        strategy_override=getattr(context, "strategy_override", None),
    )


class LLMRequestPreparer:
    """Build canonical LLM requests without the removed LLMCaller facade."""

    def __init__(
        self,
        *,
        workspace: str = "",
        formatter: Any | None = None,
        model_catalog: ModelCatalog | None = None,
    ) -> None:
        self.workspace = workspace
        self._formatter = formatter
        self._model_catalog = model_catalog or ModelCatalog(workspace=workspace or ".")

    @staticmethod
    def _build_native_tool_schemas(profile: RoleProfile) -> list[dict[str, Any]]:
        """Build native tool schemas from profile for tests and request assembly."""
        return build_native_tool_schemas(profile)

    def _resolve_provider_capabilities(self, profile: RoleProfile) -> ProviderCapabilities:
        """Resolve per-model capability flags with conservative keyword fallback."""
        provider_id = str(getattr(profile, "provider_id", "") or "").strip()
        model = str(getattr(profile, "model", "") or "").strip()
        whitelist = [
            str(name).strip()
            for name in list(getattr(getattr(profile, "tool_policy", None), "whitelist", []) or [])
            if str(name).strip()
        ]
        supports_tools = False
        supports_json_schema = False

        try:
            spec = self._model_catalog.resolve(provider_id, model)
            supports_tools = bool(spec.supports_tools)
            supports_json_schema = bool(spec.supports_json_schema)
        except (RuntimeError, ValueError):
            spec = None

        token = " ".join([provider_id.lower(), model.lower()])
        if not supports_tools and any(
            keyword in token for keyword in ("openai", "gpt", "codex", "anthropic", "claude", "kimi", "minimax")
        ):
            supports_tools = True
        if not supports_tools and whitelist:
            unknown_tokens = {
                "",
                "unknown",
                "unknown-provider",
                "unknown-model",
                "n/a",
                "na",
                "none",
                "null",
                "default",
            }
            provider_unknown = provider_id.lower() in unknown_tokens or provider_id.lower().startswith("unknown")
            model_unknown = model.lower() in unknown_tokens or model.lower().startswith("unknown")
            if provider_unknown and model_unknown:
                supports_tools = True
        if not supports_json_schema and any(keyword in token for keyword in ("openai", "gpt", "codex")):
            supports_json_schema = True

        return ProviderCapabilities(
            supports_native_tools=supports_tools,
            supports_json_schema=supports_json_schema,
            supports_stream_native_tools=supports_tools,
        )

    @staticmethod
    def _extract_prebuilt_projection_messages(context: ContextRequest) -> list[dict[str, Any]] | None:
        """Extract TransactionKernel-provided projected messages from context override."""
        override = getattr(context, "context_override", None)
        if not isinstance(override, dict):
            return None
        raw_messages = override.get(_TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY)
        if not isinstance(raw_messages, list):
            return None

        normalized_user_turns: list[tuple[str, str, str]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            if not role:
                continue
            content = str(item.get("content", ""))
            name = str(item.get("name") or "").strip()
            if role.lower() == "system" and is_empty_run_card_message(name=name, content=content):
                continue
            normalized_content = _normalize_user_message_for_dedupe(content) if role == "user" else ""
            normalized_user_turns.append((role, content, normalized_content))

        current_user_token = _normalize_user_message_for_dedupe(getattr(context, "message", ""))
        last_current_user_index = -1
        if current_user_token:
            for index, (role, _content, normalized_content) in enumerate(normalized_user_turns):
                if role == "user" and normalized_content == current_user_token:
                    last_current_user_index = index

        messages: list[dict[str, Any]] = []
        last_user_token: str | None = None
        for index, (role, content, normalized_content) in enumerate(normalized_user_turns):
            if (
                role == "user"
                and current_user_token
                and normalized_content == current_user_token
                and index != last_current_user_index
            ):
                continue
            if role == "user":
                if last_user_token is not None and normalized_content == last_user_token:
                    continue
                last_user_token = normalized_content
                if current_user_token and normalized_content == current_user_token:
                    content = current_user_token
            else:
                last_user_token = None
            messages.append({"role": role, "content": content})
        return messages

    async def _prepare_llm_request(
        self,
        *,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        temperature: float,
        max_tokens: int,
        stream: bool,
        response_model: type | None = None,
        platform_retry_max: int = 1,
        factory_semantic_identity: FactoryRoleSemanticRequestIdentityV1 | None = None,
    ) -> PreparedLLMRequest:
        """Build canonical LLM request bundle."""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.contracts import TurnEngineContextResult

        factory_binding = get_factory_role_evidence_binding()
        if factory_binding is not None:
            binding_error = factory_binding.validation_error(expected_role=str(getattr(profile, "role_id", "") or ""))
            if binding_error:
                raise RuntimeError(f"factory_role_evidence_binding_malformed:{binding_error}")
            raise RuntimeError("factory_role_evidence_cutoff_not_enabled")

        factory_authority = get_factory_role_evidence_authority_binding()
        factory_authority_snapshot = (
            _snapshot_factory_authority(factory_authority) if factory_authority is not None else None
        )
        canonical_role = str(getattr(profile, "role_id", "") or "").strip()
        if factory_authority is not None:
            if factory_authority_snapshot is None:
                raise RuntimeError("factory_role_evidence_authority_binding_drift")
            if factory_authority_snapshot.role != canonical_role:
                raise RuntimeError("factory_role_evidence_authority_binding_role_mismatch")
            if type(factory_semantic_identity) is not FactoryRoleSemanticRequestIdentityV1:
                raise RuntimeError("factory_role_semantic_identity_required")
            assert factory_semantic_identity is not None
            factory_semantic_identity.__post_init__()

        override = getattr(context, "context_override", None)
        prebuilt_messages = self._extract_prebuilt_projection_messages(context)
        forced_tool_definitions: list[dict[str, Any]] | None = None
        forced_tool_choice: Any | None = None
        forced_tools_disabled = False
        if isinstance(override, dict):
            raw_forced_tool_definitions = override.get(_TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY)
            if isinstance(raw_forced_tool_definitions, list):
                forced_tool_definitions = [dict(item) for item in raw_forced_tool_definitions if isinstance(item, dict)]
            raw_forced_tool_choice = override.get(_TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY)
            if raw_forced_tool_choice is not None:
                if isinstance(raw_forced_tool_choice, str):
                    normalized_tool_choice = raw_forced_tool_choice.strip()
                    forced_tool_choice = normalized_tool_choice or None
                else:
                    forced_tool_choice = raw_forced_tool_choice
            forced_tools_disabled = (
                isinstance(raw_forced_tool_definitions, list)
                and not forced_tool_definitions
                and str(forced_tool_choice or "").strip().lower() == "none"
            )

        request_timeout_seconds = resolve_timeout_seconds(
            profile,
            override if isinstance(override, dict) else None,
        )
        request_max_tokens = resolve_max_tokens(
            max_tokens,
            override if isinstance(override, dict) else None,
        )
        temperature_decision = resolve_temperature_with_source(
            temperature,
            override if isinstance(override, dict) else None,
        )
        request_options: dict[str, Any] = {
            # ADR-0090 W2.6: escalated mutation retries override temperature via
            # the transaction-kernel channel (deterministic transcription phase).
            "temperature": temperature_decision.value,
            "max_tokens": request_max_tokens,
            "timeout": request_timeout_seconds,
        }
        _copy_provider_policy_options(
            override=override if isinstance(override, dict) else None,
            request_options=request_options,
        )
        capabilities = self._resolve_provider_capabilities(profile)
        context_override_for_domain = getattr(context, "context_override", None)
        contract = build_interaction_contract(
            profile=profile,
            message=str(getattr(context, "message", "") or ""),
            domain=str(
                getattr(context, "domain", "")
                or (context_override_for_domain.get("domain") if isinstance(context_override_for_domain, dict) else "")
                or "code"
            ),
            stream=stream,
            response_model=response_model,
            capabilities=capabilities,
        )
        native_tool_schemas: list[dict[str, Any]] = []
        native_tool_mode = "disabled"
        native_response_format: dict[str, Any] | None = None
        response_format_mode = "plain_text"
        provider_id = str(getattr(profile, "provider_id", "") or "")
        role_native_tool_schemas: list[dict[str, Any]] | None = None

        def _role_native_tools() -> list[dict[str, Any]]:
            nonlocal role_native_tool_schemas
            if role_native_tool_schemas is None:
                role_native_tool_schemas = self._build_native_tool_schemas(profile)
            return role_native_tool_schemas

        def _forced_or_role_tool_schemas() -> list[dict[str, Any]]:
            if forced_tool_definitions is not None:
                return augment_forced_transaction_tool_definitions(
                    tool_definitions=_role_native_tools(),
                    forced_definitions=forced_tool_definitions,
                    context_override=override,
                )
            return _role_native_tools() if contract.native_tools_enabled else []

        if stream:
            raw_tool_schemas = _forced_or_role_tool_schemas()
            if raw_tool_schemas:
                native_tool_schemas = [dict(item) for item in raw_tool_schemas]
                if self._formatter is not None:
                    request_options["tools"] = self._formatter.format_tools(raw_tool_schemas, provider_id)
                else:
                    request_options["tools"] = raw_tool_schemas
                request_options["tool_choice"] = forced_tool_choice if forced_tool_choice is not None else "auto"
                native_tool_mode = "native_tools_streaming"
            elif contract.tool_whitelist and not forced_tools_disabled:
                native_tool_schemas = _role_native_tools()
                native_tool_mode = "native_tools_unavailable"
        else:
            effective_platform_retry_max = resolve_platform_retry_max(profile, platform_retry_max)
            request_options["max_retries"] = effective_platform_retry_max
            request_options["platform_transport_only"] = True
            raw_tool_schemas = _forced_or_role_tool_schemas()
            if raw_tool_schemas:
                native_tool_schemas = [dict(item) for item in raw_tool_schemas]
                if self._formatter is not None:
                    request_options["tools"] = self._formatter.format_tools(raw_tool_schemas, provider_id)
                else:
                    request_options["tools"] = raw_tool_schemas
                request_options["tool_choice"] = forced_tool_choice if forced_tool_choice is not None else "auto"
                native_tool_mode = "native_tools"
            elif contract.tool_whitelist and not forced_tools_disabled:
                native_tool_schemas = _role_native_tools()
                native_tool_mode = "native_tools_unavailable"
            if contract.structured_output_enabled and response_model is not None:
                native_response_format = build_native_response_format(response_model)
                if native_response_format:
                    request_options["response_format"] = native_response_format
                    response_format_mode = "native_json_schema"
                else:
                    response_format_mode = "text_json_fallback"

        capability_profile = resolve_actor_capability_profile(
            profile=profile,
            model_catalog=self._model_catalog,
            provider_capabilities=capabilities,
            request_options=request_options,
            native_tool_mode=native_tool_mode,
            response_format_mode=response_format_mode,
        ).to_dict()
        projection_context = _with_projection_capability_profile(context, capability_profile)

        if prebuilt_messages is not None:
            messages = list(prebuilt_messages)
            if not messages or str(messages[0].get("role", "")).strip().lower() != "system":
                messages = [{"role": "system", "content": str(system_prompt or "")}, *messages]
            input_text = messages_to_input(
                messages,
                format_type="auto",
                provider_id=str(getattr(profile, "provider_id", "")),
            )
            default_token_estimate = max(0, len(input_text) // 4)
            token_estimate = default_token_estimate
            compression_applied = False
            compression_strategy: str | None = None
            if isinstance(override, dict):
                raw_token_estimate = override.get(_TRANSACTION_KERNEL_PREBUILT_TOKEN_ESTIMATE_KEY)
                if isinstance(raw_token_estimate, (int, float, str)):
                    try:
                        token_estimate = max(0, int(raw_token_estimate))
                    except ValueError:
                        token_estimate = default_token_estimate
                compression_applied = bool(override.get(_TRANSACTION_KERNEL_PREBUILT_COMPRESSION_APPLIED_KEY))
                raw_compression_strategy = override.get(_TRANSACTION_KERNEL_PREBUILT_COMPRESSION_STRATEGY_KEY)
                if raw_compression_strategy is not None:
                    normalized_strategy = str(raw_compression_strategy).strip()
                    compression_strategy = normalized_strategy or None
            context_result = TurnEngineContextResult(
                messages=tuple(
                    {
                        "role": str(message.get("role", "")),
                        "content": str(message.get("content", "")),
                    }
                    for message in messages
                ),
                token_estimate=token_estimate,
                compression_applied=compression_applied,
                compression_strategy=compression_strategy,
                metadata={
                    "prebuilt_projection_messages": True,
                    "source": "transaction_kernel",
                    "capability_profile": capability_profile,
                },
            )
        else:
            context_gateway = RoleContextGateway(profile, self.workspace)
            # ADR-0090 I4.3: gateway budgets AND prepends the role system prompt —
            # no second projection pass.
            context_result = await context_gateway.build_context(projection_context, system_prompt=system_prompt)
            messages = list(context_result.messages)

        messages = _ensure_current_user_message_final(messages, getattr(context, "message", ""))
        _reject_preexisting_factory_evidence_protocol(
            messages,
            factory_authority_bound=factory_authority is not None,
        )
        messages = _ensure_core_role_identity(
            messages,
            canonical_role,
            system_prompt=system_prompt,
        )

        factory_semantic_candidate: FactoryRoleSemanticCandidateV1 | None = None
        resolved_factory_binding: FactoryRoleEvidenceBindingV1 | None = None
        if factory_authority is not None:
            if factory_authority_snapshot is None:
                raise RuntimeError("factory_role_evidence_authority_binding_drift")
            _revalidate_factory_authority_before_cutoff(
                factory_authority,
                factory_authority_snapshot,
            )
            assert factory_semantic_identity is not None  # exact type checked above
            factory_semantic_candidate = FactoryRoleSemanticCandidateV1.create(
                identity=factory_semantic_identity,
                role=canonical_role,
                provider_id=provider_id,
                model=str(getattr(profile, "model", "") or ""),
                interaction_mode=native_tool_mode,
                capability_profile=capability_profile,
                messages=messages,
                tools=request_options.get("tools", []),
                tool_choice=request_options.get("tool_choice"),
                response_format=request_options.get("response_format"),
                temperature=request_options.get("temperature"),
                max_tokens=request_options.get("max_tokens"),
                stream=stream,
            )
            cutoff_request = FactoryRoleEvidenceCutoffRequestV1(
                schema_version=FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
                run_id=factory_semantic_identity.run_id,
                role=canonical_role,
                turn_id=factory_semantic_identity.turn_id,
                call_id=factory_semantic_identity.call_id,
                request_freeze_id=factory_semantic_identity.request_freeze_id,
                semantic_candidate_hash=factory_semantic_candidate.semantic_candidate_hash,
                attempt_budget=factory_authority_snapshot.attempt_budget,
                execution_authority_hash=factory_authority_snapshot.execution_authority_hash,
                candidate_refs=(),
            )
            cutoff_ack = await factory_authority_snapshot.cutoff_port.acquire_cutoff(cutoff_request)
            _revalidate_factory_authority_before_cutoff(
                factory_authority,
                factory_authority_snapshot,
            )
            if type(cutoff_ack) is not FactoryRoleEvidenceCutoffAckV1:
                raise RuntimeError("factory_role_evidence_cutoff_ack_exact_type_required")
            FactoryRoleEvidenceCutoffAckV1.__post_init__(cutoff_ack)
            expected_ack_projection = (
                factory_authority_snapshot.factory_run_id,
                cutoff_request.run_id,
                cutoff_request.role,
                cutoff_request.turn_id,
                cutoff_request.call_id,
                cutoff_request.request_freeze_id,
                cutoff_request.semantic_candidate_hash,
                cutoff_request.attempt_budget,
                cutoff_request.execution_authority_hash,
            )
            actual_ack_projection = (
                cutoff_ack.factory_run_id,
                cutoff_ack.run_id,
                cutoff_ack.role,
                cutoff_ack.turn_id,
                cutoff_ack.call_id,
                cutoff_ack.request_freeze_id,
                cutoff_ack.semantic_candidate_hash,
                cutoff_ack.attempt_budget,
                cutoff_ack.execution_authority_hash,
            )
            if actual_ack_projection != expected_ack_projection:
                raise RuntimeError("factory_role_evidence_cutoff_ack_request_mismatch")
            cutoff_proof = await factory_authority_snapshot.cutoff_port.resolve_cutoff_proof(cutoff_ack)
            _revalidate_factory_authority_before_cutoff(
                factory_authority,
                factory_authority_snapshot,
            )
            if type(cutoff_proof) is not FactoryRoleEvidenceCutoffProofV1:
                raise RuntimeError("factory_role_evidence_cutoff_proof_exact_type_required")
            FactoryRoleEvidenceCutoffProofV1.__post_init__(cutoff_proof)
            if cutoff_proof.ack != cutoff_ack:
                raise RuntimeError("factory_role_evidence_cutoff_proof_ack_mismatch")
            resolved_factory_binding = FactoryRoleEvidenceBindingV1.from_cutoff_proof(cutoff_proof)
            binding_error = resolved_factory_binding.validation_error(expected_role=canonical_role)
            if binding_error:
                raise RuntimeError(f"factory_role_evidence_binding_malformed:{binding_error}")
            messages = _inject_factory_evidence_block(
                messages,
                role=canonical_role,
                binding=resolved_factory_binding,
            )
        input_text = messages_to_input(
            messages,
            format_type="auto",
            provider_id=str(getattr(profile, "provider_id", "")),
        )
        context_result = replace(
            context_result,
            messages=tuple(
                {
                    "role": str(message.get("role", "")),
                    "content": str(message.get("content", "")),
                }
                for message in messages
            ),
            token_estimate=max(0, len(input_text) // 4),
        )
        context_summary = compute_context_summary(input_text)
        context_metadata = (
            dict(getattr(context_result, "metadata", {}) or {})
            if getattr(context_result, "metadata", None) is not None
            else {}
        )
        context_sources = tuple(str(item) for item in (getattr(context_result, "context_sources", ()) or ()))
        context_os_audit = audit_context_os_prompt_messages(
            messages=messages,
            context_sources=context_sources,
            metadata=context_metadata,
            current_user_instruction=str(getattr(context, "message", "") or ""),
            expected=True,
        )
        capability_profile_ref = context_metadata.get("capability_profile_ref")
        source_projection_id = str(context_metadata.get("projection_id") or "").strip()
        source_context_result_id = str(context_metadata.get("context_result_id") or "").strip()
        final_prompt_digest = str(context_os_audit.get("prompt_digest") or "").strip()
        if canonical_role in _CORE_ROLE_IDENTITIES:
            if not final_prompt_digest:
                raise RuntimeError("role_identity_final_projection_digest_missing")
            context_projection_id = final_prompt_digest
            if source_projection_id and source_projection_id != context_projection_id:
                context_metadata["source_projection_id"] = source_projection_id
            context_result_id = build_context_result_id(context_projection_id)
            if source_context_result_id and source_context_result_id != context_result_id:
                context_metadata["source_context_result_id"] = source_context_result_id
            context_metadata["projection_id"] = context_projection_id
            context_metadata["context_result_id"] = context_result_id
            context_result = replace(context_result, metadata=context_metadata)
        else:
            context_projection_id = source_projection_id or final_prompt_digest
            context_result_id = source_context_result_id
        prompt_profile_audit: dict[str, Any] = {}
        selected_prompt_profile_ids: list[str] = []
        director_execution_profile: dict[str, Any] = {}
        director_execution_strategy: dict[str, Any] = {}
        director_execution_envelope: dict[str, Any] = {}
        execution_envelope_hash = ""
        resident_agi_audit_context: dict[str, Any] = {}
        prompt_profile_context_override = getattr(context, "context_override", None)
        if isinstance(prompt_profile_context_override, dict):
            resident_agi_audit_context = _resident_agi_audit_context_from_override(prompt_profile_context_override)
            raw_director_execution_profile = prompt_profile_context_override.get("director_execution_profile")
            if isinstance(raw_director_execution_profile, dict):
                director_execution_profile = dict(raw_director_execution_profile)
            raw_director_execution_strategy = prompt_profile_context_override.get(
                "director_execution_strategy"
            ) or prompt_profile_context_override.get("task_execution_strategy")
            if isinstance(raw_director_execution_strategy, dict):
                director_execution_strategy = dict(raw_director_execution_strategy)
            raw_director_execution_envelope = (
                prompt_profile_context_override.get("director_execution_envelope")
                or prompt_profile_context_override.get("task_execution_envelope")
                or prompt_profile_context_override.get("execution_envelope")
            )
            if isinstance(raw_director_execution_envelope, dict):
                director_execution_envelope = dict(raw_director_execution_envelope)
            execution_envelope_hash = str(
                prompt_profile_context_override.get("execution_envelope_hash")
                or prompt_profile_context_override.get("director_execution_envelope_hash")
                or prompt_profile_context_override.get("task_execution_envelope_hash")
                or director_execution_envelope.get("envelope_hash")
                or ""
            ).strip()
            raw_prompt_profile_audit = prompt_profile_context_override.get("prompt_profile_audit")
            if isinstance(raw_prompt_profile_audit, dict):
                prompt_profile_audit = dict(raw_prompt_profile_audit)
            raw_selected_prompt_profile_ids = prompt_profile_context_override.get("selected_prompt_profile_ids")
            if isinstance(raw_selected_prompt_profile_ids, (list, tuple, set)):
                selected_prompt_profile_ids = [
                    str(item).strip() for item in raw_selected_prompt_profile_ids if str(item or "").strip()
                ]
        if not selected_prompt_profile_ids and prompt_profile_audit:
            raw_selected_prompt_profile_ids = prompt_profile_audit.get("selected_prompt_profile_ids")
            if isinstance(raw_selected_prompt_profile_ids, (list, tuple, set)):
                selected_prompt_profile_ids = [
                    str(item).strip() for item in raw_selected_prompt_profile_ids if str(item or "").strip()
                ]
        # Budget policy blueprint Phase 1 step 3: stamp the ACTUAL resolved
        # budget (observability only) under the single `execution_budget` key.
        execution_budget = _build_execution_budget_projection(
            profile=profile,
            override=override if isinstance(override, dict) else None,
            request_options=request_options,
            request_max_tokens=request_max_tokens,
            request_timeout_seconds=request_timeout_seconds,
        )
        ai_request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role=profile.role_id,
            input=input_text,
            options=request_options,
            context={
                "workspace": self.workspace,
                "mode": "chat",
                "execution_budget": execution_budget,
                "native_tool_mode": native_tool_mode,
                "response_format_mode": response_format_mode,
                "interaction_contract": contract.to_metadata(),
                "context_os_audit": context_os_audit,
                "capability_profile": capability_profile,
                "capability_profile_ref": capability_profile_ref if isinstance(capability_profile_ref, dict) else {},
                "context_projection_id": context_projection_id,
                "context_result_id": context_result_id,
                "request_sampling": temperature_decision.to_context(),
                "director_execution_profile": director_execution_profile,
                "director_execution_strategy": director_execution_strategy,
                "director_execution_envelope": director_execution_envelope,
                "task_execution_envelope": director_execution_envelope,
                "execution_envelope_hash": execution_envelope_hash,
                "resident_agi_audit_context": resident_agi_audit_context,
                "prompt_profile_audit": prompt_profile_audit,
                "selected_prompt_profile_ids": selected_prompt_profile_ids,
                **copy_final_request_evidence_context_fields(prompt_profile_context_override),
                **_tool_contract_context_fields(prompt_profile_context_override),
                # ADR-0090 W1.5: carry the STRUCTURED message array alongside the
                # flattened input so OpenAI-compatible providers can preserve real
                # chat-template role anchoring (weak local models lose system/user
                # structure when the whole transcript rides in one user message).
                "chat_messages": [
                    {"role": str(m.get("role", "")), "content": str(m.get("content", ""))} for m in messages
                ],
            },
        )
        frozen_factory_request: FactoryRoleFrozenSemanticRequestV1 | None = None
        if factory_semantic_candidate is not None and resolved_factory_binding is not None:
            frozen_factory_request = FactoryRoleFrozenSemanticRequestV1.create(
                candidate=factory_semantic_candidate,
                signed_factory_binding_ref=resolved_factory_binding.signed_factory_binding_ref,
                signed_factory_binding_hash=resolved_factory_binding.signed_factory_binding_hash,
                messages=messages,
                tools=request_options.get("tools", []),
                tool_choice=request_options.get("tool_choice"),
                response_format=request_options.get("response_format"),
                temperature=request_options.get("temperature"),
                max_tokens=request_options.get("max_tokens"),
                stream=stream,
            )
        return PreparedLLMRequest(
            messages=messages,
            input_text=input_text,
            context_result=context_result,
            context_summary=context_summary,
            request_options=request_options,
            ai_request=ai_request,
            native_tool_schemas=native_tool_schemas,
            native_tool_mode=native_tool_mode,
            response_model=response_model,
            native_response_format=native_response_format,
            response_format_mode=response_format_mode,
            context_os_audit=context_os_audit,
            capability_profile=capability_profile,
            factory_semantic_request=frozen_factory_request,
        )

    def _build_structured_fallback_request(
        self,
        *,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        response_model: type,
        mode: str = "structured",
    ) -> AIRequest:
        """Reuse prepared request baseline when native structured output is unavailable."""
        fallback_options = dict(prepared.request_options)
        fallback_options.pop("response_format", None)
        fallback_instruction = build_text_response_fallback_instruction(response_model)
        fallback_input = append_runtime_fallback_instruction(
            str(prepared.input_text or ""),
            fallback_instruction,
        )
        fallback_context = dict(prepared.ai_request.context if isinstance(prepared.ai_request.context, dict) else {})
        fallback_context["workspace"] = self.workspace
        fallback_context["mode"] = str(mode or "structured")
        fallback_context["response_format_mode"] = "text_json_fallback"
        self._append_fallback_instruction_to_chat_messages(fallback_context, fallback_instruction)
        return AIRequest(
            task_type=TaskType.DIALOGUE,
            role=profile.role_id,
            input=fallback_input,
            options=fallback_options,
            context=fallback_context,
        )

    def _build_reasoning_truncation_retry_request(
        self,
        *,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
    ) -> AIRequest:
        """Re-ask after a reasoning-model truncated mid-thought (5th floor).

        Live (factory-bench L2-08/11/12, 2026-06-15): on an edit/fill turn whose output
        budget collapsed, the weak qwen Director spends its tiny budget on
        ``reasoning_content`` and is truncated (``finish_reason=length``) BEFORE any
        visible output / tool call → ``Empty visible output`` → ``no_materialized``.
        This retry RESERVES a larger output budget AND tells the model to stop thinking
        and emit the tool call directly, so the write actually lands. Native tools and
        the original task_type are PRESERVED (unlike the native-tool-text fallback).
        """
        fallback_options = dict(prepared.request_options)
        try:
            current_max = int(fallback_options.get("max_tokens") or 0)
        except (TypeError, ValueError):
            current_max = 0
        fallback_options["max_tokens"] = max(current_max, _REASONING_TRUNCATION_RETRY_MAX_TOKENS)
        fallback_instruction = (
            "【推理截断回退】\n"
            "你上一次在推理(thinking)中途被截断(finish_reason=length),没有产出任何可见输出或工具调用。"
            "这次请【最小化推理】:不要长篇思考,直接输出工具调用(write_file/edit_blocks 等)。"
            "把要写的文件正文【完整放进工具参数】,严禁只在思考里描述而不调用工具。"
        )
        fallback_input = append_runtime_fallback_instruction(str(prepared.input_text or ""), fallback_instruction)
        fallback_context = dict(prepared.ai_request.context if isinstance(prepared.ai_request.context, dict) else {})
        fallback_context["workspace"] = self.workspace
        fallback_context["reasoning_truncation_retry"] = True
        self._append_fallback_instruction_to_chat_messages(fallback_context, fallback_instruction)
        return AIRequest(
            task_type=prepared.ai_request.task_type,
            role=profile.role_id,
            input=fallback_input,
            options=fallback_options,
            context=fallback_context,
        )

    def _build_required_tool_retry_request(
        self,
        *,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        error_message: str,
    ) -> AIRequest:
        """Re-ask when a provider returned prose despite final-request required tools."""

        fallback_options = dict(prepared.request_options)
        try:
            current_temperature = float(fallback_options.get("temperature") or 0.2)
        except (TypeError, ValueError):
            current_temperature = 0.2
        fallback_options["temperature"] = min(current_temperature, 0.2)
        retry_max_tokens = _bounded_required_tool_retry_max_tokens(fallback_options.get("max_tokens"))
        retry_timeout = _bounded_required_tool_retry_timeout(fallback_options.get("timeout"))
        fallback_options["max_tokens"] = retry_max_tokens
        fallback_options["timeout"] = retry_timeout
        fallback_context = dict(prepared.ai_request.context if isinstance(prepared.ai_request.context, dict) else {})
        tool_contract = _mapping(fallback_context.get("tool_contract"))
        required_tools = _string_list(fallback_context.get("required_tools")) or _string_list(
            tool_contract.get("required_tools")
        )
        tool_text = ", ".join(required_tools) if required_tools else "the required tool"
        fallback_instruction = (
            "【必需工具未调用回退】\n"
            f"上一次响应没有调用 final request 要求的工具: {tool_text}。\n"
            "这次必须立即发出真实工具调用；不要解释、不要先检查工作区、不要用自然语言替代工具调用。"
            "如果 write_file 是必需工具,请把目标文件的完整内容放入 write_file 参数。"
            f"\n上次错误: {str(error_message or '').strip()}"
        ).strip()
        fallback_input = append_runtime_fallback_instruction(str(prepared.input_text or ""), fallback_instruction)
        fallback_context["workspace"] = self.workspace
        fallback_context["required_tool_retry"] = True
        fallback_context["required_tool_retry_budget"] = {
            "schema_version": "llm.required_tool_retry_budget.v1",
            "max_tokens": retry_max_tokens,
            "timeout_seconds": retry_timeout,
            "reason": "required_tool_retry_must_emit_native_tool_call",
        }
        self._append_fallback_instruction_to_chat_messages(fallback_context, fallback_instruction)
        return AIRequest(
            task_type=prepared.ai_request.task_type,
            role=profile.role_id,
            input=fallback_input,
            options=fallback_options,
            context=fallback_context,
        )

    def _build_required_tool_text_fallback_request(
        self,
        *,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        error_message: str,
    ) -> AIRequest:
        """Ask for strict textual tool-call envelopes when forced native choice is unsupported."""

        fallback_options = dict(prepared.request_options)
        fallback_options["temperature"] = 0.0
        retry_max_tokens = _bounded_required_tool_retry_max_tokens(fallback_options.get("max_tokens"))
        retry_timeout = _bounded_required_tool_retry_timeout(fallback_options.get("timeout"))
        fallback_options["max_tokens"] = retry_max_tokens
        fallback_options["timeout"] = retry_timeout
        fallback_options.pop("tools", None)
        fallback_options["tool_choice"] = "none"

        fallback_context = dict(prepared.ai_request.context if isinstance(prepared.ai_request.context, dict) else {})
        tool_contract = _mapping(fallback_context.get("tool_contract"))
        required_tools = _string_list(fallback_context.get("required_tools")) or _string_list(
            tool_contract.get("required_tools")
        )
        tool_text = ", ".join(required_tools) if required_tools else "the required tool"
        fallback_instruction = (
            "【必需工具文本封装回退】\n"
            f"当前 provider 未能可靠发出 native tool call: {tool_text}。\n"
            "这次不要解释、不要输出 Markdown、不要输出代码块,只输出一个 UTF-8 JSON 数组。\n"
            '数组每一项必须是: {"name":"write_file","arguments":{"path":"相对路径","content":"完整文件内容"}}。\n'
            "如果需要写多个文件,数组中必须包含多个 write_file 项; content 必须是完整文件内容,不能省略。\n"
            f"上次错误: {str(error_message or '').strip()}"
        ).strip()
        fallback_input = append_runtime_fallback_instruction(str(prepared.input_text or ""), fallback_instruction)
        fallback_context["workspace"] = self.workspace
        fallback_context["required_tool_text_fallback"] = True
        fallback_context["required_tool_text_fallback_budget"] = {
            "schema_version": "llm.required_tool_text_fallback_budget.v1",
            "max_tokens": retry_max_tokens,
            "timeout_seconds": retry_timeout,
            "reason": "required_tool_retry_must_emit_text_tool_envelope",
            "required_tools": required_tools,
        }
        self._append_fallback_instruction_to_chat_messages(fallback_context, fallback_instruction)
        return AIRequest(
            task_type=prepared.ai_request.task_type,
            role=profile.role_id,
            input=fallback_input,
            options=fallback_options,
            context=fallback_context,
        )

    @staticmethod
    def _append_fallback_instruction_to_chat_messages(context: dict[str, Any], instruction: str) -> None:
        raw_messages = context.get("chat_messages")
        if not isinstance(raw_messages, list):
            return

        messages: list[dict[str, str]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "")
            if not content.strip():
                continue
            role = str(item.get("role") or "").strip().lower() or "user"
            messages.append({"role": role, "content": content})

        if not messages:
            return

        target_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if messages[index]["role"] == "user"),
            len(messages) - 1,
        )
        target = dict(messages[target_index])
        target["content"] = append_runtime_fallback_instruction(target["content"], instruction)
        messages[target_index] = target
        context["chat_messages"] = messages


__all__ = ["LLMRequestPreparer", "is_reasoning_truncation_error"]
