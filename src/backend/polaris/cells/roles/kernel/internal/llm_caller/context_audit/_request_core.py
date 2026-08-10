from __future__ import annotations

from typing import Any

from ..response_types import PreparedLLMRequest
from ._constants import (
    _EXECUTION_PROFILE_SUMMARY_KEYS,
)
from ._primitives import (
    _bool_value,
    _coerce_float,
    _coerce_int,
    _int_value,
    _json_safe,
    _mapping,
    _message_content_chars,
    _stable_digest,
    _string_list,
    _unique_strings,
)


def _final_request_redaction_safety(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe whether coverage metadata embeds prompt text."""

    return {
        "schema_version": "polaris.final_request_redaction_safety.v1",
        "snapshot_content_policy": "metadata_only",
        "message_count": len(messages),
        "message_content_chars_observed": _message_content_chars(messages),
        "message_content_embedded": False,
        "evidence_coverage_embeds_content": False,
        "safe": True,
    }


def _request_messages(ai_request: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages for final-request evidence binding.

    Prepared/provider-bound messages are authoritative (final provider request
    SSoT). Cognitive ``chat_messages`` / context history is only a fallback when
    the prepared list is empty — otherwise stale short history can win and make
    ``actual_sibling_exports`` message-binding fail closed incorrectly.
    """

    prepared_messages = [dict(item) for item in fallback if isinstance(item, dict)]
    if prepared_messages:
        return prepared_messages

    ctx = getattr(ai_request, "context", None)
    raw_messages: Any = None
    if isinstance(ctx, dict):
        raw_messages = ctx.get("chat_messages")
        if raw_messages is None:
            raw_messages = ctx.get("messages")
    if isinstance(raw_messages, list):
        messages = [dict(item) for item in raw_messages if isinstance(item, dict)]
        if messages:
            return messages

    input_text = str(getattr(ai_request, "input", "") or "")
    if input_text.strip():
        return [{"role": "user", "content": input_text}]
    return []


def _context_window_tokens(prepared: PreparedLLMRequest, profile: Any) -> int:
    capability_profile = getattr(prepared, "capability_profile", None)
    context_policy = getattr(profile, "context_policy", None)
    raw_candidates = [
        capability_profile.get("model_window_tokens") if isinstance(capability_profile, dict) else None,
        capability_profile.get("max_context_tokens") if isinstance(capability_profile, dict) else None,
        capability_profile.get("context_window_tokens") if isinstance(capability_profile, dict) else None,
        getattr(profile, "max_context_tokens", None),
        getattr(context_policy, "max_context_tokens", None),
        profile.get("max_context_tokens") if isinstance(profile, dict) else None,
        profile.get("context_window_tokens") if isinstance(profile, dict) else None,
        profile.get("context_policy", {}).get("max_context_tokens")
        if isinstance(profile, dict) and isinstance(profile.get("context_policy"), dict)
        else None,
    ]
    for raw in raw_candidates:
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, float) and raw > 0:
            return int(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = int(float(raw.strip()))
            except ValueError:
                continue
            if parsed > 0:
                return parsed
    return 0


def _receipt_refs_from_payload(value: Any, *, depth: int = 0) -> list[str]:
    """Return structured receipt refs from final-request message metadata.

    Boundary:
        Receipt references are evidence links, so they must come from explicit
        ``receipt_refs`` fields. Message prose may mention ``receipt://...`` for
        display, but content text is not authoritative evidence.
    """

    if depth > 4:
        return []
    if isinstance(value, dict):
        mapping_refs = _string_list(value.get("receipt_refs"))
        for key in ("messages", "parts", "items"):
            mapping_refs.extend(_receipt_refs_from_payload(value.get(key), depth=depth + 1))
        return mapping_refs
    if isinstance(value, (list, tuple, set)):
        sequence_refs: list[str] = []
        for item in value:
            sequence_refs.extend(_receipt_refs_from_payload(item, depth=depth + 1))
        return sequence_refs
    return []


def _final_request_receipt_refs(
    *,
    ai_request: Any,
    prepared: PreparedLLMRequest,
    messages: list[dict[str, Any]],
) -> list[str]:
    context_payload = _request_context(ai_request)
    raw_messages = context_payload.get("chat_messages")
    if raw_messages is None:
        raw_messages = context_payload.get("messages")
    refs: list[str] = []
    refs.extend(_receipt_refs_from_payload(raw_messages))
    refs.extend(_receipt_refs_from_payload(getattr(prepared, "messages", None)))
    refs.extend(_receipt_refs_from_payload(messages))
    return _unique_strings(refs)


def _resident_agi_audit_context(ai_request: Any) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    raw_context = context_payload.get("resident_agi_audit_context")
    return dict(raw_context) if isinstance(raw_context, dict) else {}


def _resident_agi_coverage_flags(ai_request: Any | None) -> dict[str, bool]:
    audit_context = _resident_agi_audit_context(ai_request) if ai_request is not None else {}
    participation = _mapping(audit_context.get("participation"))
    enabled = _bool_value(audit_context.get("enabled"), default=bool(audit_context))
    final_request_participation = _bool_value(
        participation.get("final_request_audit"),
        default=enabled,
    )
    if not enabled or not final_request_participation:
        return {}
    return {
        "has_resident_agi_decision_trace": bool(
            audit_context.get("decision_contract_schema_version")
            or audit_context.get("audit_pack_schema_version")
            or audit_context.get("role_runtime_required")
        ),
        "has_resident_agi_capability_surface": bool(
            audit_context.get("capability_surface_schema_version")
            or audit_context.get("decision_capability_registry_schema_version")
        ),
        "has_resident_agi_decision_boundary": bool(
            audit_context.get("decision_boundary_schema")
            or _int_value(audit_context.get("decision_boundary_count")) > 0
        ),
    }


def _execution_strategy_consistency_findings(
    *,
    sampling: dict[str, Any],
    execution_profile: dict[str, Any],
    execution_strategy: dict[str, Any],
    execution_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not execution_profile and not execution_strategy and not execution_contract:
        return findings
    raw_contract_sampling = execution_contract.get("sampling")
    contract_sampling = dict(raw_contract_sampling) if isinstance(raw_contract_sampling, dict) else {}
    raw_contract_context_budget = execution_contract.get("context_budget")
    contract_context_budget = dict(raw_contract_context_budget) if isinstance(raw_contract_context_budget, dict) else {}

    actual_temperature = _coerce_float(sampling.get("temperature"))
    expected_temperature = _coerce_float(execution_strategy.get("temperature"))
    if expected_temperature is None:
        expected_temperature = _coerce_float(execution_profile.get("temperature"))
    if expected_temperature is None:
        expected_temperature = _coerce_float(contract_sampling.get("temperature"))
    if (
        actual_temperature is not None
        and expected_temperature is not None
        and abs(actual_temperature - expected_temperature) > 0.001
    ):
        findings.append(
            {
                "code": "execution_profile_temperature_mismatch",
                "severity": "warning",
                "expected_temperature": expected_temperature,
                "actual_temperature": actual_temperature,
                "profile_schema": str(execution_profile.get("schema_version") or ""),
                "strategy_schema": str(execution_strategy.get("schema_version") or ""),
                "contract_schema": str(execution_contract.get("schema_version") or ""),
            }
        )

    actual_max_tokens = _coerce_int(sampling.get("max_tokens"))
    expected_max_tokens = _coerce_int(execution_strategy.get("output_budget_tokens"))
    if expected_max_tokens is None:
        expected_max_tokens = _coerce_int(contract_context_budget.get("output_budget_tokens"))
    if actual_max_tokens is not None and expected_max_tokens is not None and actual_max_tokens < expected_max_tokens:
        budget_ratio = actual_max_tokens / expected_max_tokens if expected_max_tokens > 0 else 0.0
        findings.append(
            {
                "code": "execution_strategy_output_budget_under_applied",
                "severity": "error",
                "expected_max_tokens": expected_max_tokens,
                "actual_max_tokens": actual_max_tokens,
                "budget_ratio": round(budget_ratio, 4),
                "remediation": "select a model/provider binding whose max output budget can satisfy the task execution strategy",
                "strategy_schema": str(execution_strategy.get("schema_version") or ""),
                "contract_schema": str(execution_contract.get("schema_version") or ""),
            }
        )
    return findings


def _request_option_payloads(ai_request: Any, prepared: PreparedLLMRequest) -> tuple[Any, Any, Any]:
    request_options = getattr(ai_request, "options", None)
    if isinstance(request_options, dict):
        tool_schema_payload = request_options.get("tools") if "tools" in request_options else []
        response_format_payload = request_options.get("response_format")
        tool_choice_payload = request_options.get("tool_choice")
        return tool_schema_payload, response_format_payload, tool_choice_payload

    raw_prepared_options = getattr(prepared, "request_options", {})
    prepared_options = raw_prepared_options if isinstance(raw_prepared_options, dict) else {}
    tool_schema_payload = prepared_options.get("tools", getattr(prepared, "native_tool_schemas", []))
    response_format_payload = prepared_options.get("response_format", getattr(prepared, "native_response_format", None))
    tool_choice_payload = prepared_options.get("tool_choice")
    return tool_schema_payload, response_format_payload, tool_choice_payload


def _request_options(ai_request: Any, prepared: PreparedLLMRequest) -> dict[str, Any]:
    request_options = getattr(ai_request, "options", None)
    if isinstance(request_options, dict):
        return dict(request_options)
    raw_prepared_options = getattr(prepared, "request_options", {})
    return dict(raw_prepared_options) if isinstance(raw_prepared_options, dict) else {}


def _task_type_value(ai_request: Any) -> str:
    raw = getattr(ai_request, "task_type", "")
    value = getattr(raw, "value", raw)
    return str(value or "").strip()


def _request_context(ai_request: Any) -> dict[str, Any]:
    ctx = getattr(ai_request, "context", None)
    return ctx if isinstance(ctx, dict) else {}


def _tool_execution_surface_audit(
    *,
    ai_request: Any,
    tool_schema_count: int,
    tool_choice: Any,
) -> dict[str, Any]:
    """Describe dispatch capability separately from context evidence coverage."""

    context = _request_context(ai_request)
    text_fallback_requested = bool(context.get("required_tool_text_fallback"))
    native_surface_absent = text_fallback_requested and tool_schema_count == 0
    if text_fallback_requested:
        compatibility_mode = "required_tool_text_fallback"
        convergence_status = "pending_text_parser_dispatch"
    elif tool_schema_count > 0:
        compatibility_mode = "native_tools"
        convergence_status = "pending_native_dispatch"
    else:
        compatibility_mode = "no_tool_surface"
        convergence_status = "not_required_or_unavailable"
    return {
        "schema_version": "llm.tool_execution_surface_audit.v1",
        "compatibility_mode": compatibility_mode,
        "text_fallback_requested": text_fallback_requested,
        "native_tool_surface_absent_because_text_fallback": native_surface_absent,
        "parser_required": text_fallback_requested,
        "tool_schema_count": int(tool_schema_count),
        "tool_choice": tool_choice,
        "convergence_status": convergence_status,
        "convergence_proven": False,
    }


def _execution_profile(ai_request: Any) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    for key in (
        "director_execution_profile",
        "task_execution_profile",
        "execution_profile",
        "task_runtime_metadata",
    ):
        raw_profile = context_payload.get(key)
        if isinstance(raw_profile, dict):
            return dict(raw_profile)
    return {}


def _execution_strategy(ai_request: Any) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    for key in (
        "director_execution_strategy",
        "task_execution_strategy",
        "execution_strategy",
    ):
        raw_strategy = context_payload.get(key)
        if isinstance(raw_strategy, dict):
            return dict(raw_strategy)
    return {}


def _execution_contract(ai_request: Any) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    for key in (
        "director_execution_contract",
        "task_execution_contract",
        "execution_contract",
    ):
        raw_contract = context_payload.get(key)
        if isinstance(raw_contract, dict):
            return dict(raw_contract)
    return {}


def _delivery_contract_payload(ai_request: Any, key: str) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    for container in (
        context_payload,
        _mapping(context_payload.get("metadata")),
        _mapping(context_payload.get("task")),
        _mapping(_mapping(context_payload.get("task")).get("metadata")),
        _execution_contract(ai_request),
    ):
        raw_payload = container.get(key)
        if isinstance(raw_payload, dict):
            return dict(raw_payload)
    return {}


def _execution_envelope(ai_request: Any) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    for key in (
        "director_execution_envelope",
        "task_execution_envelope",
        "execution_envelope",
    ):
        raw_envelope = context_payload.get(key)
        if isinstance(raw_envelope, dict):
            return dict(raw_envelope)
    return {}


def _execution_envelope_hash(ai_request: Any, envelope: dict[str, Any] | None = None) -> str:
    context_payload = _request_context(ai_request)
    for key in (
        "execution_envelope_hash",
        "director_execution_envelope_hash",
        "task_execution_envelope_hash",
    ):
        raw_hash = context_payload.get(key)
        if isinstance(raw_hash, str) and raw_hash.strip():
            return raw_hash.strip()
    payload = envelope if envelope is not None else _execution_envelope(ai_request)
    raw_hash = payload.get("envelope_hash") if isinstance(payload, dict) else None
    if isinstance(raw_hash, str) and raw_hash.strip():
        return raw_hash.strip()
    return _stable_digest(payload) if payload else ""


def _execution_envelope_summary(ai_request: Any) -> dict[str, Any]:
    envelope = _execution_envelope(ai_request)
    if not envelope:
        return {}
    authorization = _mapping(envelope.get("authorization"))
    audit_policy = _mapping(envelope.get("audit_policy"))
    budget_policy = _mapping(envelope.get("budget_policy"))
    return {
        "schema_version": str(envelope.get("schema_version") or ""),
        "run_id": str(envelope.get("run_id") or ""),
        "task_id": str(envelope.get("task_id") or ""),
        "trace_id": str(envelope.get("trace_id") or ""),
        "envelope_hash": _execution_envelope_hash(ai_request, envelope),
        "target_files_count": len(_string_list(authorization.get("target_files"))),
        "scope_paths_count": len(_string_list(authorization.get("scope_paths"))),
        "allowed_write_paths_count": len(_string_list(authorization.get("allowed_write_paths"))),
        "required_evidence_count": len(_string_list(audit_policy.get("required_evidence"))),
        "output_budget_tokens": budget_policy.get("output_budget_tokens"),
    }


def _execution_profile_summary(ai_request: Any) -> dict[str, Any]:
    profile = _execution_profile(ai_request)
    if not profile:
        return {}
    summary: dict[str, Any] = {
        key: str(profile.get(key) or "").strip()
        for key in _EXECUTION_PROFILE_SUMMARY_KEYS
        if str(profile.get(key) or "").strip()
    }
    for key in ("target_files", "quality_gates", "selected_libraries", "architecture_decisions"):
        value = profile.get(key)
        if isinstance(value, list):
            summary[f"{key}_count"] = len(value)
    return summary


def _execution_contract_summary(ai_request: Any) -> dict[str, Any]:
    contract = _execution_contract(ai_request)
    if not contract:
        return {}
    summary: dict[str, Any] = {
        key: contract.get(key)
        for key in (
            "schema_version",
            "source",
            "task_type",
            "phase",
            "project_type",
            "language",
            "framework",
            "output_contract_id",
            "generation_mode",
        )
        if contract.get(key) not in (None, "")
    }
    for nested_key in ("sampling", "context_budget", "delivery_contract", "quality_contract", "audit_contract"):
        payload = contract.get(nested_key)
        if isinstance(payload, dict):
            summary[nested_key] = {
                key: payload.get(key)
                for key in (
                    "temperature",
                    "temperature_phase",
                    "sampling_mode",
                    "output_budget_tokens",
                    "input_budget_tokens",
                    "prompt_max_chars",
                    "primary_entities",
                    "rule_count",
                    "edge_case_count",
                    "level",
                    "quality_gates",
                    "verification_commands",
                    "deterministic_checks",
                    "contract_hash",
                )
                if payload.get(key) not in (None, "", [])
            }
    return summary


def _task_metadata(ai_request: Any) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    for key in ("task_metadata", "canonical_metadata", "metadata"):
        raw_metadata = context_payload.get(key)
        if isinstance(raw_metadata, dict):
            return dict(raw_metadata)
    request_metadata = getattr(ai_request, "metadata", None)
    if isinstance(request_metadata, dict):
        return dict(request_metadata)
    return {}


def _resident_agi_audit_context_summary(ai_request: Any) -> dict[str, Any]:
    audit_context = _resident_agi_audit_context(ai_request)
    if not audit_context:
        return {}
    participation = _mapping(audit_context.get("participation"))
    return {
        "schema_version": str(audit_context.get("schema_version") or ""),
        "enabled": _bool_value(audit_context.get("enabled"), default=True),
        "participation_scopes": _string_list(audit_context.get("participation_scopes")),
        "participation": {
            key: _bool_value(value) for key, value in sorted(participation.items()) if isinstance(value, (bool, str))
        },
        "audit_pack_schema_version": str(audit_context.get("audit_pack_schema_version") or ""),
        "decision_contract_schema_version": str(audit_context.get("decision_contract_schema_version") or ""),
        "capability_surface_schema_version": str(audit_context.get("capability_surface_schema_version") or ""),
        "decision_boundary_schema": str(audit_context.get("decision_boundary_schema") or ""),
        "decision_boundary_count": _int_value(audit_context.get("decision_boundary_count")),
        "decision_capability_id": str(audit_context.get("decision_capability_id") or ""),
    }


def _prompt_profile_selection(ai_request: Any) -> dict[str, Any]:
    ctx = getattr(ai_request, "context", None)
    if not isinstance(ctx, dict):
        return {}
    raw_audit = ctx.get("prompt_profile_audit")
    if isinstance(raw_audit, dict):
        audit = _json_safe(raw_audit)
        return audit if isinstance(audit, dict) else {}
    raw_ids = ctx.get("selected_prompt_profile_ids")
    if isinstance(raw_ids, (list, tuple, set)):
        selected = [str(item).strip() for item in raw_ids if str(item or "").strip()]
        if selected:
            return {"selected_prompt_profile_ids": selected}
    return {}
