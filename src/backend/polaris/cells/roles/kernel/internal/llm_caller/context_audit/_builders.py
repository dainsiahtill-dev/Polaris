from __future__ import annotations

from typing import Any

from ..response_types import PreparedLLMRequest
from ._constants import (
    _UNDERUTILIZED_RATIO,
    _UNDERUTILIZED_WINDOW_THRESHOLD,
)
from ._evidence import (
    _add_context_os_audit_findings,
    _add_evidence_coverage_findings,
    _final_request_evidence_coverage,
    _prepared_context_os_audit,
)
from ._findings import (
    _context_quality_findings,
    _coverage_flags,
    _message_projection_findings,
)
from ._payloads import (
    _request_metadata_summary,
    _request_sampling_audit,
)
from ._primitives import (
    _estimate_tokens_from_chars,
    _json_chars,
    _json_safe,
    _message_chars,
    _non_empty_attr,
)
from ._request_core import (
    _context_window_tokens,
    _execution_contract,
    _execution_profile,
    _execution_strategy,
    _prompt_profile_selection,
    _request_messages,
    _request_option_payloads,
    _tool_execution_surface_audit,
)
from ._tools import (
    _summarize_response_format,
    _summarize_tool_schema,
)


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
    final_request_context_audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=profile,
    )
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
        "final_request_evidence_coverage": final_request_context_audit.get("final_request_evidence_coverage", {}),
        "final_request_context_audit": final_request_context_audit,
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

    tool_schema_payload, response_format_payload, tool_choice_payload = _request_option_payloads(ai_request, prepared)
    tool_schema_chars = _json_chars(tool_schema_payload)
    tool_schema_token_estimate = _estimate_tokens_from_chars(tool_schema_chars)
    tool_schema_count = len(tool_schema_payload) if isinstance(tool_schema_payload, list) else 0

    response_format_chars = _json_chars(response_format_payload)
    response_format_token_estimate = _estimate_tokens_from_chars(response_format_chars)

    final_request_token_estimate = message_token_estimate + tool_schema_token_estimate + response_format_token_estimate
    window_tokens = _context_window_tokens(prepared, profile)
    utilization = (final_request_token_estimate / window_tokens) if window_tokens > 0 else None
    context_underutilized = bool(
        window_tokens >= _UNDERUTILIZED_WINDOW_THRESHOLD
        and final_request_token_estimate < int(window_tokens * _UNDERUTILIZED_RATIO)
    )
    coverage = _coverage_flags(ai_request=ai_request, prepared=prepared)
    prompt_profile_selection = _prompt_profile_selection(ai_request)
    sampling = _request_sampling_audit(ai_request, prepared)
    request_metadata_summary = _request_metadata_summary(ai_request, prepared)
    execution_profile_summary = request_metadata_summary.get("execution_profile_summary", {})
    execution_contract_summary = request_metadata_summary.get("execution_contract_summary", {})
    execution_profile = _execution_profile(ai_request)
    execution_strategy = _execution_strategy(ai_request)
    execution_contract = _execution_contract(ai_request)
    context_os_audit = _prepared_context_os_audit(
        prepared=prepared,
        ai_request=ai_request,
    )
    quality = _context_quality_findings(
        coverage=coverage,
        context_underutilized=context_underutilized,
        final_request_token_estimate=final_request_token_estimate,
        context_window_tokens=window_tokens,
        sampling=sampling,
        execution_profile=execution_profile,
        execution_strategy=execution_strategy,
        execution_contract=execution_contract,
        message_projection_findings=_message_projection_findings(messages),
    )
    evidence_coverage = _final_request_evidence_coverage(
        ai_request=ai_request,
        prepared=prepared,
        profile=profile,
        messages=messages,
        coverage=coverage,
        request_metadata_summary=request_metadata_summary,
        tool_schema_payload=tool_schema_payload,
        tool_choice_payload=tool_choice_payload,
        response_format_payload=response_format_payload,
    )
    quality = _add_evidence_coverage_findings(quality, evidence_coverage)
    quality = _add_context_os_audit_findings(quality, context_os_audit)
    tool_execution_surface = _tool_execution_surface_audit(
        ai_request=ai_request,
        tool_schema_count=tool_schema_count,
        tool_choice=tool_choice_payload,
    )
    if tool_execution_surface["text_fallback_requested"]:
        findings = quality.get("findings")
        if isinstance(findings, list):
            findings.append(
                {
                    "code": "tool_execution_convergence_pending_text_fallback",
                    "severity": "warning",
                    "native_tool_surface_absent_because_text_fallback": True,
                }
            )
        quality["context_needs_review"] = True

    return {
        "schema_version": "llm.final_request_context_audit.v1",
        "message_count": len(messages),
        "message_chars": message_chars,
        "message_token_estimate": message_token_estimate,
        "tool_schema_count": tool_schema_count,
        "tool_schema_chars": tool_schema_chars,
        "tool_schema_token_estimate": tool_schema_token_estimate,
        "tool_execution_surface": tool_execution_surface,
        "native_tool_surface_absent_because_text_fallback": bool(
            tool_execution_surface["native_tool_surface_absent_because_text_fallback"]
        ),
        "response_format_chars": response_format_chars,
        "response_format_token_estimate": response_format_token_estimate,
        "final_request_token_estimate": final_request_token_estimate,
        "context_window_tokens": window_tokens,
        "context_window_utilization": round(utilization, 4) if utilization is not None else None,
        "context_underutilized": context_underutilized,
        "available_token_headroom": max(0, window_tokens - final_request_token_estimate),
        "coverage": coverage,
        "final_request_evidence_coverage": evidence_coverage,
        "context_os_audit": context_os_audit,
        "context_quality": quality,
        "sampling": sampling,
        "request_metadata_summary": request_metadata_summary,
        "execution_profile_summary": execution_profile_summary if isinstance(execution_profile_summary, dict) else {},
        "execution_contract_summary": execution_contract_summary
        if isinstance(execution_contract_summary, dict)
        else {},
        "execution_profile_hash": request_metadata_summary.get("execution_profile_hash", ""),
        "execution_envelope_hash": request_metadata_summary.get("execution_envelope_hash", ""),
        "execution_contract_hash": request_metadata_summary.get("execution_contract_hash", ""),
        "task_metadata_hash": request_metadata_summary.get("task_metadata_hash", ""),
        "has_execution_profile": bool(request_metadata_summary.get("has_execution_profile")),
        "has_execution_strategy": bool(request_metadata_summary.get("has_execution_strategy")),
        "has_execution_contract": bool(request_metadata_summary.get("has_execution_contract")),
        "has_execution_envelope": bool(request_metadata_summary.get("has_execution_envelope")),
        "has_language_guidance": bool(request_metadata_summary.get("has_language_guidance")),
        "has_output_contract": bool(request_metadata_summary.get("has_output_contract")),
        "has_module_interface_contract": bool(request_metadata_summary.get("has_module_interface_contract")),
        "has_actual_sibling_exports": bool(request_metadata_summary.get("has_actual_sibling_exports")),
        "has_interface_discrepancy_context": bool(request_metadata_summary.get("has_interface_discrepancy_context")),
        "has_architecture_or_file_plan": bool(request_metadata_summary.get("has_architecture_or_file_plan")),
        "prompt_profile_selection": prompt_profile_selection,
        "selected_prompt_profile_ids": prompt_profile_selection.get("selected_prompt_profile_ids", []),
    }
