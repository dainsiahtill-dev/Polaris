"""Final LLM request context audit helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .response_types import PreparedLLMRequest

_UNDERUTILIZED_WINDOW_THRESHOLD = 8192
_UNDERUTILIZED_RATIO = 0.15


def _json_chars(value: Any) -> int:
    if value is None:
        return 0
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        return len(str(value))


def _json_canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _stable_digest(value: Any) -> str:
    payload = _json_canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def _estimate_tokens_from_chars(char_count: int) -> int:
    return max(0, int(char_count) // 4)


def _message_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            total += len(str(message))
            continue
        total += len(str(message.get("role") or ""))
        total += len(str(message.get("content") or ""))
    return total


def _request_messages(ai_request: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ctx = getattr(ai_request, "context", None)
    raw_messages: Any = None
    if isinstance(ctx, dict):
        raw_messages = ctx.get("chat_messages")
        if raw_messages is None:
            raw_messages = ctx.get("messages")
    if isinstance(raw_messages, list):
        messages: list[dict[str, Any]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")
            content = str(item.get("content") or "")
            messages.append({"role": role, "content": content})
        if messages:
            return messages

    input_text = str(getattr(ai_request, "input", "") or "")
    if input_text.strip():
        return [{"role": "user", "content": input_text}]
    return [dict(item) for item in fallback if isinstance(item, dict)]


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


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _bool_value(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _int_value(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
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
        if token:
            result.append(token)
    return result


def _resident_agi_audit_context(ai_request: Any) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    raw_context = context_payload.get("resident_agi_audit_context")
    return dict(raw_context) if isinstance(raw_context, dict) else {}


def _resident_agi_coverage_flags(text: str, ai_request: Any | None) -> dict[str, bool]:
    lowered = text.lower()
    audit_context = _resident_agi_audit_context(ai_request) if ai_request is not None else {}
    participation = _mapping(audit_context.get("participation"))
    enabled = _bool_value(audit_context.get("enabled"), default=bool(audit_context))
    final_request_participation = _bool_value(
        participation.get("final_request_audit"),
        default=enabled,
    )
    if not enabled or not final_request_participation:
        return {}
    text_flags = {
        "has_resident_agi_decision_trace": any(
            needle in lowered
            for needle in (
                "resident_agi_decision_trace",
                "resident agi 决策交接",
                "resident agi decision",
                "resident.agi_decision_trace_signal.v1",
                "resident.decision_event.v1",
                "source_of_truth: workspace/meta/resident/decision_trace.jsonl",
                "workspace/meta/resident/decision_trace.jsonl",
            )
        ),
        "has_resident_agi_capability_surface": any(
            needle in lowered
            for needle in (
                "resident_agi_capability_surface",
                "resident agi 能力面",
                "resident.agi_capability_surface.v1",
                "runtime_foundation: roles.runtime + contextos + turnengine",
                "embedded_agi_supervisor",
            )
        ),
        "has_resident_agi_decision_boundary": any(
            needle in lowered
            for needle in (
                "resident.agi_decision_boundary.v1",
                "decision_boundary_schema",
                "decision_boundaries",
                "platform_hard_rule",
                "agi_decision_scope",
                "agi_governed_execution",
                "agi_recommendation",
            )
        ),
    }
    return {
        "has_resident_agi_decision_trace": bool(
            text_flags["has_resident_agi_decision_trace"]
            or audit_context.get("decision_contract_schema_version")
            or audit_context.get("audit_pack_schema_version")
            or audit_context.get("role_runtime_required")
        ),
        "has_resident_agi_capability_surface": bool(
            text_flags["has_resident_agi_capability_surface"]
            or audit_context.get("capability_surface_schema_version")
            or audit_context.get("decision_capability_registry_schema_version")
        ),
        "has_resident_agi_decision_boundary": bool(
            text_flags["has_resident_agi_decision_boundary"]
            or audit_context.get("decision_boundary_schema")
            or _int_value(audit_context.get("decision_boundary_count")) > 0
        ),
    }


def _coverage_flags(text: str, *, ai_request: Any | None = None) -> dict[str, bool]:
    lowered = text.lower()
    blueprint_absent = any(
        marker in lowered
        for marker in (
            "无 ce 蓝图可用",
            "无 chief engineer 蓝图可用",
            "no ce blueprint available",
            "no chief engineer blueprint available",
            "chief engineer blueprint evidence: unavailable",
            "蓝图/技术架构（降级）",
            "非 ce 权威蓝图",
        )
    )
    strong_blueprint_evidence = any(
        needle in lowered
        for needle in (
            "blueprint_id",
            "construction signatures",
            "construction target",
            "construction verify",
            "scope_for_apply",
            "construction_plan",
            "handoff_ready",
            "generated_blueprints",
            'blueprints":',
            "蓝图交接",
        )
    )
    has_chief_engineer_blueprint = strong_blueprint_evidence or (
        not blueprint_absent
        and any(
            needle in lowered
            for needle in (
                "chief engineer",
                "chief_engineer",
                "blueprint",
                "ce handoff",
                "ce 蓝图",
            )
        )
    )
    coverage = {
        "has_pm_contract": any(
            needle in lowered
            for needle in (
                "task-",
                "acceptance",
                "acceptance criteria",
                "depends_on",
                "pm task contract",
                "quality gates",
                "verification commands",
                "任务:",
                "任务合同",
                "执行步骤",
                "验收标准",
            )
        ),
        "has_chief_engineer_blueprint": has_chief_engineer_blueprint,
        "has_target_files": any(
            needle in lowered
            for needle in (
                "target_files",
                "scope_paths",
                "src/",
                "tests/",
            )
        ),
        "has_failure_feedback": any(
            needle in lowered
            for needle in (
                "exit_code",
                "stderr",
                "stdout",
                "failed",
                "error",
                "retry",
                "工具执行返回失败",
            )
        ),
        "has_workspace_quality_evidence": any(
            needle in lowered
            for needle in (
                "factory_workspace_quality",
                "workspace quality",
                "npm run build",
                "npm test",
                "real_run_gate",
            )
        ),
    }
    coverage.update(_resident_agi_coverage_flags(text, ai_request))
    return coverage


def _context_quality_findings(
    *,
    coverage: dict[str, bool],
    context_underutilized: bool,
    final_request_token_estimate: int,
    context_window_tokens: int,
    sampling: dict[str, Any],
    execution_profile: dict[str, Any],
    execution_strategy: dict[str, Any],
) -> dict[str, Any]:
    missing = [key for key, ok in coverage.items() if not ok]
    findings: list[dict[str, Any]] = []
    if missing:
        findings.append(
            {
                "code": "missing_context_coverage",
                "severity": "advisory",
                "missing": missing,
            }
        )
    if context_underutilized and missing:
        findings.append(
            {
                "code": "underutilized_with_missing_context",
                "severity": "warning",
                "missing": missing,
                "final_request_token_estimate": final_request_token_estimate,
                "context_window_tokens": context_window_tokens,
            }
        )
    findings.extend(
        _execution_strategy_consistency_findings(
            sampling=sampling,
            execution_profile=execution_profile,
            execution_strategy=execution_strategy,
        )
    )
    return {
        "missing_coverage": missing,
        "context_needs_review": bool(findings),
        "findings": findings,
    }


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _execution_strategy_consistency_findings(
    *,
    sampling: dict[str, Any],
    execution_profile: dict[str, Any],
    execution_strategy: dict[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not execution_profile and not execution_strategy:
        return findings

    actual_temperature = _coerce_float(sampling.get("temperature"))
    expected_temperature = _coerce_float(execution_strategy.get("temperature"))
    if expected_temperature is None:
        expected_temperature = _coerce_float(execution_profile.get("temperature"))
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
            }
        )

    actual_max_tokens = _coerce_int(sampling.get("max_tokens"))
    expected_max_tokens = _coerce_int(execution_strategy.get("output_budget_tokens"))
    if actual_max_tokens is not None and expected_max_tokens is not None and actual_max_tokens < expected_max_tokens:
        findings.append(
            {
                "code": "execution_strategy_output_budget_under_applied",
                "severity": "warning",
                "expected_max_tokens": expected_max_tokens,
                "actual_max_tokens": actual_max_tokens,
                "strategy_schema": str(execution_strategy.get("schema_version") or ""),
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


_EXECUTION_PROFILE_SUMMARY_KEYS = (
    "schema_version",
    "source",
    "dispatch_type",
    "task_type",
    "phase",
    "project_type",
    "artifact_type",
    "language",
    "language_version",
    "runtime",
    "framework",
    "file_role",
    "task_focus",
    "sampling_mode",
    "temperature_phase",
    "temperature_source",
    "output_contract_id",
)


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


def _task_metadata(ai_request: Any) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    for key in ("task_metadata", "canonical_metadata", "metadata"):
        raw_metadata = context_payload.get(key)
        if isinstance(raw_metadata, dict):
            return dict(raw_metadata)
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


def _request_metadata_summary(ai_request: Any, prepared: PreparedLLMRequest) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    options = _request_options(ai_request, prepared)
    execution_profile = _execution_profile(ai_request)
    execution_strategy = _execution_strategy(ai_request)
    execution_profile_summary = _execution_profile_summary(ai_request)
    task_metadata = _task_metadata(ai_request)
    resident_agi_audit_context = _resident_agi_audit_context_summary(ai_request)
    summary: dict[str, Any] = {
        "schema_version": "llm.request_metadata_summary.v1",
        "task_type": _task_type_value(ai_request),
        "role": _non_empty_attr(ai_request, name="role"),
        "mode": str(context_payload.get("mode") or "").strip(),
        "native_tool_mode": str(context_payload.get("native_tool_mode") or "").strip(),
        "response_format_mode": str(context_payload.get("response_format_mode") or "").strip(),
        "context_keys": sorted(str(key) for key in context_payload),
        "option_keys": sorted(str(key) for key in options),
        "temperature": options.get("temperature") if isinstance(options.get("temperature"), (int, float)) else None,
        "max_tokens": options.get("max_tokens") if isinstance(options.get("max_tokens"), int) else None,
        "has_execution_profile": bool(execution_profile),
        "execution_profile_summary": execution_profile_summary,
        "execution_profile_hash": _stable_digest(execution_profile) if execution_profile else "",
        "has_execution_strategy": bool(execution_strategy),
        "execution_strategy_summary": {
            key: execution_strategy.get(key)
            for key in (
                "schema_version",
                "source",
                "temperature",
                "temperature_phase",
                "output_budget_tokens",
                "input_budget_tokens",
                "prompt_max_chars",
                "min_context_utilization",
                "context_underutilized_policy",
            )
            if execution_strategy.get(key) not in (None, "")
        },
        "execution_strategy_hash": _stable_digest(execution_strategy) if execution_strategy else "",
        "has_task_metadata": bool(task_metadata),
        "task_metadata_keys": sorted(str(key) for key in task_metadata),
        "task_metadata_hash": _stable_digest(task_metadata) if task_metadata else "",
        "has_resident_agi_audit_context": bool(resident_agi_audit_context),
        "resident_agi_audit_context": resident_agi_audit_context,
        "resident_agi_audit_context_hash": _stable_digest(resident_agi_audit_context)
        if resident_agi_audit_context
        else "",
    }
    summary["has_language_guidance"] = bool(
        execution_profile_summary.get("language")
        or execution_profile_summary.get("framework")
        or execution_profile_summary.get("runtime")
    )
    summary["has_output_contract"] = bool(
        execution_profile_summary.get("output_contract_id")
        or context_payload.get("response_format_mode")
        or options.get("response_format")
    )
    return summary


def _request_sampling_audit(ai_request: Any, prepared: PreparedLLMRequest) -> dict[str, Any]:
    options = _request_options(ai_request, prepared)
    profile = _execution_profile(ai_request)
    temperature = options.get("temperature")
    max_tokens = options.get("max_tokens")
    return {
        "temperature": temperature if isinstance(temperature, (int, float)) else None,
        "max_tokens": max_tokens if isinstance(max_tokens, int) else None,
        "temperature_source": str(profile.get("temperature_source") or "request_options"),
        "temperature_phase": str(profile.get("temperature_phase") or ""),
        "sampling_mode": str(profile.get("sampling_mode") or ""),
        "task_type": str(profile.get("task_type") or _task_type_value(ai_request)),
        "phase": str(profile.get("phase") or ""),
        "execution_profile_schema": str(profile.get("schema_version") or ""),
        "execution_profile_source": str(profile.get("source") or ""),
    }


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return str(value)


def _summarize_tool_schema(tool: Any) -> dict[str, Any]:
    if not isinstance(tool, dict):
        return {"type": type(tool).__name__, "name": "", "argument_keys": [], "required": []}
    function_payload = tool.get("function")
    function = function_payload if isinstance(function_payload, dict) else tool
    parameters_payload = function.get("parameters") if isinstance(function, dict) else {}
    parameters = parameters_payload if isinstance(parameters_payload, dict) else {}
    properties_payload = parameters.get("properties")
    properties = properties_payload if isinstance(properties_payload, dict) else {}
    required_payload = parameters.get("required")
    required = required_payload if isinstance(required_payload, list) else []
    return {
        "type": str(tool.get("type") or "function"),
        "name": str(function.get("name") or ""),
        "argument_keys": sorted(str(key) for key in properties),
        "required": [str(item) for item in required],
    }


def _summarize_response_format(response_format: Any) -> Any:
    if response_format is None:
        return None
    if not isinstance(response_format, dict):
        return _json_safe(response_format)
    summary: dict[str, Any] = {"type": response_format.get("type")}
    json_schema = response_format.get("json_schema")
    if isinstance(json_schema, dict):
        summary["json_schema_name"] = json_schema.get("name")
        schema = json_schema.get("schema")
        if isinstance(schema, dict):
            properties = schema.get("properties")
            if isinstance(properties, dict):
                summary["json_schema_property_keys"] = sorted(str(key) for key in properties)
    return _json_safe(summary)


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


def _non_empty_attr(*owners: Any, name: str) -> str:
    for owner in owners:
        if owner is None:
            continue
        value = getattr(owner, name, None)
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text:
            return text
    return ""


def build_final_provider_request_snapshot(
    *,
    ai_request: Any,
    prepared: PreparedLLMRequest,
    profile: Any,
) -> dict[str, Any]:
    """Build a durable, non-content provider request audit snapshot."""
    tool_schema_payload, response_format_payload, tool_choice_payload = _request_option_payloads(ai_request, prepared)
    tools = tool_schema_payload if isinstance(tool_schema_payload, list) else []
    messages = _request_messages(ai_request, [dict(item) for item in prepared.messages if isinstance(item, dict)])
    prompt_profile_selection = _prompt_profile_selection(ai_request)
    request_metadata_summary = _request_metadata_summary(ai_request, prepared)
    return {
        "schema_version": "llm.provider_request_snapshot.v1",
        "source": "roles.kernel.llm_caller.context_audit",
        "role": _non_empty_attr(ai_request, name="role") or _non_empty_attr(profile, name="role_id"),
        "provider_id": _non_empty_attr(ai_request, profile, name="provider_id"),
        "provider_type": _non_empty_attr(ai_request, profile, name="provider_type"),
        "model": _non_empty_attr(ai_request, profile, name="model"),
        "message_count": len(messages),
        "tool_schema_count": len(tools),
        "tools": [_summarize_tool_schema(tool) for tool in tools],
        "tool_choice": _json_safe(tool_choice_payload),
        "response_format": _summarize_response_format(response_format_payload),
        "sampling": _request_sampling_audit(ai_request, prepared),
        "request_metadata_summary": request_metadata_summary,
        "task_type": request_metadata_summary.get("task_type", ""),
        "prompt_profile_selection": prompt_profile_selection,
        "selected_prompt_profile_ids": prompt_profile_selection.get("selected_prompt_profile_ids", []),
        "final_request_context_audit": build_final_request_context_audit_for_request(
            ai_request=ai_request,
            prepared=prepared,
            profile=profile,
        ),
    }


def build_final_request_context_audit(
    *,
    prepared: PreparedLLMRequest,
    profile: Any,
) -> dict[str, Any]:
    return build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request,
        prepared=prepared,
        profile=profile,
    )


def build_final_request_context_audit_for_request(
    *,
    ai_request: Any,
    prepared: PreparedLLMRequest,
    profile: Any,
) -> dict[str, Any]:
    """Build stable observability for the final request sent to a model.

    ``context_result.token_estimate`` only accounts for chat messages. The
    provider request can also include native tool schemas and response_format
    contracts, so ContextOS needs this combined view to reason about actual
    context-window usage.
    """

    messages = _request_messages(ai_request, [dict(item) for item in prepared.messages if isinstance(item, dict)])
    message_chars = _message_chars(messages)
    message_token_estimate = _estimate_tokens_from_chars(message_chars)

    tool_schema_payload, response_format_payload, _tool_choice_payload = _request_option_payloads(ai_request, prepared)
    tool_schema_chars = _json_chars(tool_schema_payload)
    tool_schema_token_estimate = _estimate_tokens_from_chars(tool_schema_chars)
    tool_schema_count = len(tool_schema_payload) if isinstance(tool_schema_payload, list) else 0

    response_format_chars = _json_chars(response_format_payload)
    response_format_token_estimate = _estimate_tokens_from_chars(response_format_chars)

    final_request_token_estimate = message_token_estimate + tool_schema_token_estimate + response_format_token_estimate
    window_tokens = _context_window_tokens(prepared, profile)
    utilization = (final_request_token_estimate / window_tokens) if window_tokens > 0 else None
    message_text = "\n".join(str(message.get("content") or "") for message in messages)

    context_underutilized = bool(
        window_tokens >= _UNDERUTILIZED_WINDOW_THRESHOLD
        and final_request_token_estimate < int(window_tokens * _UNDERUTILIZED_RATIO)
    )
    coverage = _coverage_flags(message_text, ai_request=ai_request)
    prompt_profile_selection = _prompt_profile_selection(ai_request)
    sampling = _request_sampling_audit(ai_request, prepared)
    request_metadata_summary = _request_metadata_summary(ai_request, prepared)
    execution_profile_summary = request_metadata_summary.get("execution_profile_summary", {})
    execution_profile = _execution_profile(ai_request)
    execution_strategy = _execution_strategy(ai_request)
    quality = _context_quality_findings(
        coverage=coverage,
        context_underutilized=context_underutilized,
        final_request_token_estimate=final_request_token_estimate,
        context_window_tokens=window_tokens,
        sampling=sampling,
        execution_profile=execution_profile,
        execution_strategy=execution_strategy,
    )

    return {
        "schema_version": "llm.final_request_context_audit.v1",
        "message_count": len(messages),
        "message_chars": message_chars,
        "message_token_estimate": message_token_estimate,
        "tool_schema_count": tool_schema_count,
        "tool_schema_chars": tool_schema_chars,
        "tool_schema_token_estimate": tool_schema_token_estimate,
        "response_format_chars": response_format_chars,
        "response_format_token_estimate": response_format_token_estimate,
        "final_request_token_estimate": final_request_token_estimate,
        "context_window_tokens": window_tokens,
        "context_window_utilization": round(utilization, 4) if utilization is not None else None,
        "context_underutilized": context_underutilized,
        "available_token_headroom": max(0, window_tokens - final_request_token_estimate),
        "coverage": coverage,
        "context_quality": quality,
        "sampling": sampling,
        "request_metadata_summary": request_metadata_summary,
        "execution_profile_summary": execution_profile_summary if isinstance(execution_profile_summary, dict) else {},
        "execution_profile_hash": request_metadata_summary.get("execution_profile_hash", ""),
        "task_metadata_hash": request_metadata_summary.get("task_metadata_hash", ""),
        "has_execution_profile": bool(request_metadata_summary.get("has_execution_profile")),
        "has_execution_strategy": bool(request_metadata_summary.get("has_execution_strategy")),
        "has_language_guidance": bool(request_metadata_summary.get("has_language_guidance")),
        "has_output_contract": bool(request_metadata_summary.get("has_output_contract")),
        "prompt_profile_selection": prompt_profile_selection,
        "selected_prompt_profile_ids": prompt_profile_selection.get("selected_prompt_profile_ids", []),
    }
