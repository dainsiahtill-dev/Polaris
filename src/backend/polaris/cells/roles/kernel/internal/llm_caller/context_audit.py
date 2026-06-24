"""Final LLM request context audit helpers."""

from __future__ import annotations

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


def _coverage_flags(text: str) -> dict[str, bool]:
    lowered = text.lower()
    return {
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
        "has_chief_engineer_blueprint": any(
            needle in lowered
            for needle in (
                "chief engineer",
                "chief_engineer",
                "blueprint",
                "blueprint_id",
                "ce handoff",
                "ce 蓝图",
                "construction signatures",
                "construction target",
                "construction verify",
                "scope_for_apply",
                "construction_plan",
                "蓝图交接",
            )
        ),
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


def _context_quality_findings(
    *,
    coverage: dict[str, bool],
    context_underutilized: bool,
    final_request_token_estimate: int,
    context_window_tokens: int,
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
    return {
        "missing_coverage": missing,
        "context_needs_review": bool(findings),
        "findings": findings,
    }


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


def _request_sampling_audit(ai_request: Any, prepared: PreparedLLMRequest) -> dict[str, Any]:
    options = _request_options(ai_request, prepared)
    ctx = getattr(ai_request, "context", None)
    context_payload = ctx if isinstance(ctx, dict) else {}
    raw_profile = context_payload.get("director_execution_profile")
    profile = raw_profile if isinstance(raw_profile, dict) else {}
    temperature = options.get("temperature")
    return {
        "temperature": temperature if isinstance(temperature, (int, float)) else None,
        "temperature_source": str(profile.get("temperature_source") or "request_options"),
        "temperature_phase": str(profile.get("temperature_phase") or ""),
        "sampling_mode": str(profile.get("sampling_mode") or ""),
        "task_type": str(profile.get("task_type") or ""),
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
    coverage = _coverage_flags(message_text)
    prompt_profile_selection = _prompt_profile_selection(ai_request)
    sampling = _request_sampling_audit(ai_request, prepared)
    quality = _context_quality_findings(
        coverage=coverage,
        context_underutilized=context_underutilized,
        final_request_token_estimate=final_request_token_estimate,
        context_window_tokens=window_tokens,
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
        "prompt_profile_selection": prompt_profile_selection,
        "selected_prompt_profile_ids": prompt_profile_selection.get("selected_prompt_profile_ids", []),
    }
