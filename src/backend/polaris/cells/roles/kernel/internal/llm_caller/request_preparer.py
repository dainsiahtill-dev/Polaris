"""LLM request preparation service.

Owns provider request construction and fallback request shaping for
``LLMInvoker``.  This module deliberately has no public call/call_stream facade;
``LLMCaller`` is a removed facade; request construction belongs here.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.forced_tool_scope import augment_forced_transaction_tool_definitions
from polaris.cells.roles.kernel.internal.interaction_contract import (
    ProviderCapabilities,
    build_interaction_contract,
)
from polaris.kernelone.audit.context_os_prompt import audit_context_os_prompt_messages
from polaris.kernelone.context.projection_engine import is_empty_run_card_message
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

from .capability_profile import resolve_actor_capability_profile
from .error_handling import (
    append_runtime_fallback_instruction,
    build_text_response_fallback_instruction,
)
from .helpers import (
    build_native_response_format,
    build_native_tool_schemas,
    compute_context_summary,
    messages_to_input,
    resolve_max_tokens,
    resolve_platform_retry_max,
    resolve_temperature,
    resolve_timeout_seconds,
)
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
_FINAL_REQUEST_EVIDENCE_CONTEXT_KEYS = (
    "pm_contract",
    "ce_blueprint",
    "chief_engineer_blueprint",
    "task_contract",
    "module_interface_contract",
    "actual_sibling_exports",
    "interface_discrepancy_context",
    "architecture_or_file_plan",
    "architecture_plan",
    "file_plan",
    "construction_plan",
    "delivery_plan_document",
    "delivery_depth_contract",
    "behavior_contract",
    "acceptance_contract",
    "manifest_entrypoint_contract",
    "execution_contract",
    "task_metadata",
    "metadata",
)


# 5th floor (2026-06-15): reserved output budget for the reasoning-truncation re-ask.
_REASONING_TRUNCATION_RETRY_MAX_TOKENS = 8000
_REQUIRED_TOOL_RETRY_MAX_TOKENS = 7000
_REQUIRED_TOOL_RETRY_TIMEOUT_SECONDS = 120.0


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


def _copy_final_request_evidence_context_fields(context_override: Any) -> dict[str, Any]:
    if not isinstance(context_override, dict):
        return {}
    result: dict[str, Any] = {}
    for key in _FINAL_REQUEST_EVIDENCE_CONTEXT_KEYS:
        value = context_override.get(key)
        if value in (None, "", []):
            continue
        if isinstance(value, dict):
            if value:
                result[key] = dict(value)
            continue
        if isinstance(value, (list, tuple, set)):
            copied = [item for item in value if item not in (None, "")]
            if copied:
                result[key] = copied
            continue
        result[key] = value
    return result


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
    ) -> PreparedLLMRequest:
        """Build canonical LLM request bundle."""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.contracts import TurnEngineContextResult

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
        request_options: dict[str, Any] = {
            # ADR-0090 W2.6: escalated mutation retries override temperature via
            # the transaction-kernel channel (deterministic transcription phase).
            "temperature": resolve_temperature(temperature, override if isinstance(override, dict) else None),
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
        context_result = replace(
            context_result,
            messages=tuple(
                {
                    "role": str(message.get("role", "")),
                    "content": str(message.get("content", "")),
                }
                for message in messages
            ),
        )
        input_text = messages_to_input(
            messages,
            format_type="auto",
            provider_id=str(getattr(profile, "provider_id", "")),
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
        context_projection_id = str(
            context_metadata.get("projection_id") or context_os_audit.get("prompt_digest") or ""
        ).strip()
        context_result_id = str(context_metadata.get("context_result_id") or "").strip()
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
        ai_request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role=profile.role_id,
            input=input_text,
            options=request_options,
            context={
                "workspace": self.workspace,
                "mode": "chat",
                "native_tool_mode": native_tool_mode,
                "response_format_mode": response_format_mode,
                "interaction_contract": contract.to_metadata(),
                "context_os_audit": context_os_audit,
                "capability_profile": capability_profile,
                "capability_profile_ref": capability_profile_ref if isinstance(capability_profile_ref, dict) else {},
                "context_projection_id": context_projection_id,
                "context_result_id": context_result_id,
                "director_execution_profile": director_execution_profile,
                "director_execution_strategy": director_execution_strategy,
                "director_execution_envelope": director_execution_envelope,
                "task_execution_envelope": director_execution_envelope,
                "execution_envelope_hash": execution_envelope_hash,
                "resident_agi_audit_context": resident_agi_audit_context,
                "prompt_profile_audit": prompt_profile_audit,
                "selected_prompt_profile_ids": selected_prompt_profile_ids,
                **_copy_final_request_evidence_context_fields(prompt_profile_context_override),
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
