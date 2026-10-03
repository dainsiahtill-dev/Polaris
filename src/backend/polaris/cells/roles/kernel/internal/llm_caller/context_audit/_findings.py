from __future__ import annotations

from typing import Any

from polaris.kernelone.audit.task_write_guidance import project_task_write_guidance, render_task_write_guidance
from polaris.kernelone.context.projection_engine import is_empty_run_card_message
from polaris.kernelone.events.final_request_evidence import (
    structured_context_coverage_flags,
)

from ..response_types import PreparedLLMRequest
from ._constants import (
    _OPTIONAL_CONTEXT_QUALITY_FLAGS,
)
from ._payloads import (
    _actual_sibling_exports_payload,
    _architecture_or_file_plan_payload,
    _failed_gate_evidence_payload,
    _module_interface_contract_payload,
    _workspace_quality_evidence_payload,
)
from ._request_core import (
    _execution_envelope,
    _execution_strategy_consistency_findings,
    _request_context,
    _request_messages,
    _resident_agi_coverage_flags,
)


def _task_write_guidance_findings(
    *,
    ai_request: Any,
    messages: list[dict[str, Any]],
    tools: Any,
) -> list[dict[str, Any]]:
    """Compare derived action guidance with the final request and admitted scope."""

    if str(getattr(ai_request, "role", "")).strip().lower() != "director":
        return []
    envelope = _execution_envelope(ai_request)
    write_tools = [
        item
        for item in (tools if isinstance(tools, list) else [])
        if isinstance(item, dict)
        and isinstance(item.get("function"), dict)
        and item["function"].get("name") in {"write_file", "edit_file", "edit_blocks", "append_to_file", "delete_file"}
    ]
    if not envelope or not write_tools:
        return []
    context = _request_context(ai_request)
    guidance = context.get("task_write_guidance")
    if not isinstance(guidance, dict):
        return [{"code": "task_write_guidance_missing", "severity": "error"}]
    try:
        expected = project_task_write_guidance(
            envelope,
            context.get("target_files", envelope.get("authorization", {}).get("target_files", [])),
        )
    except (TypeError, ValueError, AttributeError) as exc:
        return [{"code": "task_write_guidance_scope_mismatch", "severity": "error", "reason": str(exc)}]
    if (
        guidance.get("schema_version") != "task.write_guidance.v1"
        or guidance.get("write_targets") != expected["write_targets"]
    ):
        return [{"code": "task_write_guidance_scope_mismatch", "severity": "error"}]
    # ContextOS currently serializes safe mapping values with repr; the adapter
    # also emits a stable JSON view. Either must bind the exact same safe payload.
    markers = (render_task_write_guidance(guidance), "task_write_guidance: " + str(guidance))
    if not any(marker in str(message.get("content") or "") for marker in markers for message in messages):
        return [{"code": "task_write_guidance_not_projected", "severity": "error"}]
    for tool in write_tools:
        function = tool["function"]
        if function.get("name") != "write_file":
            continue
        properties = function.get("parameters", {}).get("properties", {})
        file_schema = properties.get("file", {})
        if "enum" not in file_schema:
            continue  # Broad tools still pass the existing runtime path guard.
        try:
            pinned = project_task_write_guidance(envelope, file_schema["enum"])
        except (TypeError, ValueError) as exc:
            return [{"code": "task_write_tool_scope_mismatch", "severity": "error", "reason": str(exc)}]
        # A single call may target only missing/failed owned files. Delivery
        # completeness belongs to artifact/verifier gates, not tool exposure.
        # Narrowing is safe; references or targets beyond the admitted action
        # guidance remain an error and runtime authorization stays unchanged.
        if pinned["reference_only_targets"] or not set(pinned["write_targets"]).issubset(expected["write_targets"]):
            return [{"code": "task_write_tool_scope_mismatch", "severity": "error"}]
    return []


def _coverage_flags(
    *,
    ai_request: Any | None = None,
    prepared: PreparedLLMRequest | None = None,
) -> dict[str, bool]:
    structured_flags = structured_context_coverage_flags(_request_context(ai_request)) if ai_request is not None else {}
    module_interface_contract = _module_interface_contract_payload(ai_request) if ai_request is not None else {}
    messages = (
        _request_messages(
            ai_request,
            [dict(item) for item in prepared.messages if isinstance(item, dict)],
        )
        if ai_request is not None and prepared is not None
        else None
    )
    actual_sibling_exports = (
        _actual_sibling_exports_payload(
            ai_request,
            module_interface_contract,
            messages=messages,
        )
        if ai_request is not None
        else {}
    )
    architecture_or_file_plan = _architecture_or_file_plan_payload(ai_request) if ai_request is not None else {}
    failed_gate_evidence = _failed_gate_evidence_payload(ai_request) if ai_request is not None else {}
    workspace_quality_evidence = _workspace_quality_evidence_payload(ai_request) if ai_request is not None else {}
    coverage = {
        "has_pm_contract": bool(structured_flags.get("has_pm_contract")),
        "has_chief_engineer_blueprint": bool(structured_flags.get("has_chief_engineer_blueprint")),
        "has_module_interface_contract": bool(module_interface_contract),
        "has_actual_sibling_exports": bool(actual_sibling_exports),
        "has_architecture_or_file_plan": bool(architecture_or_file_plan),
        "has_target_files": bool(structured_flags.get("has_target_files")),
        "has_failure_feedback": bool(structured_flags.get("has_failure_feedback") or failed_gate_evidence),
        "has_workspace_quality_evidence": bool(
            structured_flags.get("has_workspace_quality_evidence") or workspace_quality_evidence
        ),
    }
    coverage.update(_resident_agi_coverage_flags(ai_request))
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
    execution_contract: dict[str, Any],
    message_projection_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    missing = [key for key, ok in coverage.items() if not ok and key not in _OPTIONAL_CONTEXT_QUALITY_FLAGS]
    findings: list[dict[str, Any]] = []
    findings.extend(message_projection_findings)
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
            execution_contract=execution_contract,
        )
    )
    return {
        "missing_coverage": missing,
        "context_needs_review": bool(findings),
        "findings": findings,
    }


def _message_projection_findings(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        role = str(message.get("role") or "").strip().lower()
        if role != "system":
            continue
        name = str(message.get("name") or "").strip().lower()
        content = str(message.get("content") or "")
        if is_empty_run_card_message(name=name, content=content):
            findings.append(
                {
                    "code": "empty_run_card_message",
                    "severity": "warning",
                    "message_index": index,
                }
            )
    return findings
