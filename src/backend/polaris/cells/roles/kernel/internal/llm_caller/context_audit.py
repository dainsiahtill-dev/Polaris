"""Final LLM request context audit helpers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    looks_like_failure_evidence_payload,
    merge_failure_evidence_payload,
    summarize_failed_gate_evidence_context_slot,
)
from polaris.kernelone.context.projection_engine import is_empty_run_card_message
from polaris.kernelone.events.final_request_evidence import (
    build_final_request_coverage_sources,
    build_final_request_evidence_slots,
    build_final_request_tool_slots,
    final_request_evidence_ref_for_requirement,
    final_request_evidence_refs_for_coverage_flags,
    final_request_included_evidence_refs,
    final_request_structured_evidence_from_metadata_summary,
    looks_like_ce_blueprint_payload,
    looks_like_pm_contract_payload,
    looks_like_workspace_quality_evidence_payload,
    missing_required_refs_from_evidence_coverage,
    missing_required_tools_from_evidence_coverage,
    role_final_request_policy,
    structured_context_coverage_flags,
    summarize_target_scope_evidence_payload,
    summarize_workspace_quality_evidence_context_slot,
    target_scope_evidence_entry,
)
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

from .final_request_metrics import canonical_message_chars
from .response_types import PreparedLLMRequest

_UNDERUTILIZED_WINDOW_THRESHOLD = 8192
_UNDERUTILIZED_RATIO = 0.15
_UNTRUSTED_USER_MESSAGE_RE = re.compile(r"\[UNTRUSTED_USER_MESSAGE\].*", re.IGNORECASE | re.DOTALL)
_REF_BASED_SUPERSEDED_FINDING_CODES = frozenset(
    {
        "missing_context_coverage",
        "underutilized_with_missing_context",
    }
)
_OPTIONAL_CONTEXT_QUALITY_FLAGS = frozenset(
    {
        # Only tasks with prior sibling modules can provide actual export evidence.
        # Explicit evidence requirements still fail closed through
        # final_request_evidence_coverage.
        "has_actual_sibling_exports",
    }
)
_TOOL_REGISTRY_SOURCE = "polaris.kernelone.tool_execution.ToolSpecRegistry"


class FinalRequestEvidenceCoverageError(RuntimeError):
    """Raised when a strict final provider request evidence policy fails."""

    def __init__(self, violation: dict[str, Any]) -> None:
        self.violation = dict(violation)
        super().__init__(str(self.violation.get("message") or "Final provider request evidence coverage failed"))


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
    return canonical_message_chars(messages)


def _message_content_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        if isinstance(message, dict):
            total += len(str(message.get("content") or ""))
    return total


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


def _unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _string_list(values):
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


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


def _coverage_flags(*, ai_request: Any | None = None) -> dict[str, bool]:
    structured_flags = structured_context_coverage_flags(_request_context(ai_request)) if ai_request is not None else {}
    module_interface_contract = _module_interface_contract_payload(ai_request) if ai_request is not None else {}
    actual_sibling_exports = (
        _actual_sibling_exports_payload(ai_request, module_interface_contract) if ai_request is not None else {}
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


_MODULE_INTERFACE_CONTRACT_KEYS = (
    "module_interface_contract",
    "cross_file_interface_contract",
    "cross_artifact_interface_contract",
    "interface_contract",
)

_ARCHITECTURE_OR_FILE_PLAN_KEYS = (
    "architecture_or_file_plan",
    "architecture_plan",
    "file_plan",
    "construction_plan",
    "scope_for_apply",
    "architecture_decisions",
    "implementation_phases",
    "module_boundaries",
    "scope_for_apply_advisory",
)

_PM_CONTRACT_CONTEXT_KEYS = (
    "pm_contract",
    "pm_task_contract",
    "task_contract",
    "execution_task_contract",
)

_CE_BLUEPRINT_CONTEXT_KEYS = (
    "ce_blueprint",
    "chief_engineer_blueprint",
    "blueprint",
    "blueprint_payload",
    "task_blueprint",
)

_INTERFACE_DISCREPANCY_CONTEXT_KEYS = (
    "interface_discrepancy_context",
    "interface_discrepancy_evidence",
    "interface_discrepancy_receipt",
    "interface_discrepancy_receipts",
    "director_interface_discrepancy_receipt",
    "director_interface_discrepancy_receipts",
    "task_boundary_interface_discrepancy",
    "task_boundary_interface_discrepancy_retry",
    "director_interface_discrepancy_retry",
)

_FAILED_GATE_EVIDENCE_CONTEXT_KEYS = (
    "failed_gate_evidence",
    "failed_gate_or_verification_evidence",
    "failure_evidence",
    "failure_evidence_summary",
    "verification_failure_evidence",
    "verification_evidence",
    "failure_feedback",
    "qa_failure_evidence",
)

_WORKSPACE_QUALITY_EVIDENCE_CONTEXT_KEYS = (
    "workspace_quality_evidence",
    "factory_workspace_quality",
    "artifact_quality_evidence",
    "quality_gate_evidence",
    "workspace_quality",
    "real_run_gate",
)


def _looks_like_module_interface_contract(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    schema_version = str(value.get("schema_version") or "").strip()
    if schema_version == "chief_engineer.module_interface_contract.v1":
        return True
    modules = value.get("modules")
    if not isinstance(modules, (list, tuple)):
        return False
    for module in modules:
        if not isinstance(module, dict):
            continue
        if module.get("path") and (
            module.get("actual_public_symbols")
            or module.get("planned_public_symbols")
            or module.get("consumes_symbols")
        ):
            return True
    return False


def _find_module_interface_contract(value: Any, *, depth: int = 0) -> dict[str, Any]:
    if depth > 5:
        return {}
    if isinstance(value, dict):
        for key in _MODULE_INTERFACE_CONTRACT_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, dict) and _looks_like_module_interface_contract(candidate):
                return dict(candidate)
        if _looks_like_module_interface_contract(value):
            return dict(value)
        for key in (
            "ce_blueprint",
            "chief_engineer_blueprint",
            "blueprint",
            "blueprint_payload",
            "task_blueprint",
            "task",
            "metadata",
            "context",
            "delivery_contract",
            "quality_contract",
        ):
            found = _find_module_interface_contract(value.get(key), depth=depth + 1)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_module_interface_contract(item, depth=depth + 1)
            if found:
                return found
    return {}


def _looks_like_pm_contract_evidence(value: Any) -> bool:
    """Accept one PM contract or a validated project contract set."""

    if looks_like_pm_contract_payload(value):
        return True
    if not isinstance(value, dict):
        return False
    tasks = value.get("tasks")
    return isinstance(tasks, (list, tuple)) and any(looks_like_pm_contract_payload(task) for task in tasks)


def _pm_contract_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    context_payload = _request_context(ai_request)
    task_payload = _mapping(context_payload.get("task"))
    for container in (
        context_payload,
        _mapping(context_payload.get("metadata")),
        task_payload,
        _mapping(task_payload.get("metadata")),
        _execution_contract(ai_request),
        _task_metadata(ai_request),
    ):
        for key in _PM_CONTRACT_CONTEXT_KEYS:
            candidate = container.get(key)
            if isinstance(candidate, dict) and _looks_like_pm_contract_evidence(candidate):
                return dict(candidate)
    return {}


def _ce_blueprint_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    context_payload = _request_context(ai_request)
    task_payload = _mapping(context_payload.get("task"))
    for container in (
        context_payload,
        _mapping(context_payload.get("metadata")),
        task_payload,
        _mapping(task_payload.get("metadata")),
        _task_metadata(ai_request),
        _execution_contract(ai_request),
    ):
        for key in _CE_BLUEPRINT_CONTEXT_KEYS:
            candidate = container.get(key)
            if isinstance(candidate, dict) and looks_like_ce_blueprint_payload(candidate):
                return dict(candidate)
    return {}


def _pm_contract_summary(contract: dict[str, Any]) -> dict[str, Any]:
    if not contract:
        return {}
    task_id = str(contract.get("task_id") or contract.get("id") or "").strip()
    return {
        "schema_version": str(contract.get("schema_version") or ""),
        "task_id": task_id,
        "target_file_count": len(_string_list(contract.get("target_files") or contract.get("targets"))),
        "acceptance_count": len(_string_list(contract.get("acceptance") or contract.get("acceptance_criteria"))),
        "dependency_count": len(_string_list(contract.get("depends_on") or contract.get("dependencies"))),
    }


def _ce_blueprint_summary(blueprint: dict[str, Any]) -> dict[str, Any]:
    if not blueprint:
        return {}
    return {
        "schema_version": str(blueprint.get("schema_version") or ""),
        "target_file_count": len(_string_list(blueprint.get("target_files") or blueprint.get("scope_for_apply"))),
        "execution_checklist_count": len(_string_list(blueprint.get("execution_checklist"))),
        "has_module_interface_contract": bool(_find_module_interface_contract(blueprint)),
        "has_construction_plan": bool(blueprint.get("construction_plan")),
    }


def _target_scope_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}

    context_payload = _request_context(ai_request)
    task_payload = _mapping(context_payload.get("task"))
    task_metadata = _task_metadata(ai_request)
    execution_envelope = _execution_envelope(ai_request)
    authorization = _mapping(execution_envelope.get("authorization"))
    candidates = (
        ("execution_envelope.authorization", authorization),
        ("execution_profile", _execution_profile(ai_request)),
        ("execution_contract", _execution_contract(ai_request)),
        ("task_metadata", task_metadata),
        ("context", context_payload),
        ("context.metadata", _mapping(context_payload.get("metadata"))),
        ("task", task_payload),
        ("task.metadata", _mapping(task_payload.get("metadata"))),
        ("pm_contract", _pm_contract_payload(ai_request)),
        ("ce_blueprint", _ce_blueprint_payload(ai_request)),
    )
    sources = [
        entry for source, payload in candidates if payload and (entry := target_scope_evidence_entry(source, payload))
    ]
    if not sources:
        return {}
    return {
        "schema_version": "polaris.target_scope.evidence.v1",
        "sources": sources,
    }


def _module_interface_contract_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    context_payload = _request_context(ai_request)
    for payload in (
        context_payload,
        _execution_contract(ai_request),
        _task_metadata(ai_request),
    ):
        found = _find_module_interface_contract(payload)
        if found:
            return found
    return {}


def _module_interface_contract_summary(contract: dict[str, Any]) -> dict[str, Any]:
    if not contract:
        return {}
    modules = contract.get("modules")
    module_rows = [item for item in modules if isinstance(item, dict)] if isinstance(modules, (list, tuple)) else []
    actual_export_module_count = sum(1 for item in module_rows if _string_list(item.get("actual_public_symbols")))
    planned_export_module_count = sum(1 for item in module_rows if _string_list(item.get("planned_public_symbols")))
    return {
        "schema_version": str(contract.get("schema_version") or ""),
        "source": str(contract.get("source") or ""),
        "authority": str(contract.get("authority") or ""),
        "language": str(contract.get("language") or ""),
        "module_count": len(module_rows),
        "actual_export_module_count": actual_export_module_count,
        "planned_export_module_count": planned_export_module_count,
        "actual_interface_snapshot_sources": _string_list(contract.get("actual_interface_snapshot_sources")),
        "actual_interface_snapshot_file_count": _int_value(contract.get("actual_interface_snapshot_file_count")),
        "interface_conflict_count": len(contract.get("interface_conflicts") or [])
        if isinstance(contract.get("interface_conflicts"), list)
        else 0,
    }


def _looks_like_actual_sibling_exports(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    schema_version = str(value.get("schema_version") or "").strip()
    if schema_version == "polaris.actual_sibling_exports.evidence.v1":
        return True
    return (
        isinstance(value.get("modules"), (list, tuple))
        or _int_value(value.get("actual_interface_snapshot_file_count")) > 0
    )


def _direct_actual_sibling_exports_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    context_payload = _request_context(ai_request)
    for container in (
        context_payload,
        _mapping(context_payload.get("metadata")),
        _task_metadata(ai_request),
        _mapping(context_payload.get("ce_blueprint")),
        _mapping(context_payload.get("chief_engineer_blueprint")),
        _mapping(context_payload.get("blueprint")),
    ):
        candidate = container.get("actual_sibling_exports")
        if isinstance(candidate, dict) and _looks_like_actual_sibling_exports(candidate):
            return dict(candidate)
    return {}


def _actual_sibling_exports_payload(
    ai_request: Any | None,
    module_interface_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if ai_request is None:
        return {}
    direct_payload = _direct_actual_sibling_exports_payload(ai_request)
    if direct_payload:
        return direct_payload
    contract = module_interface_contract or _module_interface_contract_payload(ai_request)
    modules = contract.get("modules") if isinstance(contract, dict) else None
    rows: list[dict[str, Any]] = []
    if isinstance(modules, (list, tuple)):
        for module in modules:
            if not isinstance(module, dict):
                continue
            symbols = _string_list(module.get("actual_public_symbols"))
            if not symbols:
                continue
            rows.append(
                {
                    "path": str(module.get("path") or "").strip(),
                    "symbols": symbols,
                    "symbol_source": str(module.get("symbol_source") or "").strip(),
                }
            )
    context_payload = _request_context(ai_request)
    existing_target_files: list[dict[str, Any]] = []
    for container in (
        context_payload,
        _mapping(context_payload.get("ce_blueprint")),
        _mapping(context_payload.get("chief_engineer_blueprint")),
        _mapping(context_payload.get("blueprint")),
        _task_metadata(ai_request),
    ):
        raw_rows = container.get("existing_target_files")
        if isinstance(raw_rows, (list, tuple)):
            existing_target_files.extend(dict(item) for item in raw_rows if isinstance(item, dict))
    snapshot_file_count = (
        _int_value(contract.get("actual_interface_snapshot_file_count")) if isinstance(contract, dict) else 0
    )
    if not rows and not existing_target_files and snapshot_file_count <= 0:
        return {}
    return {
        "schema_version": "polaris.actual_sibling_exports.evidence.v1",
        "modules": rows[:20],
        "module_count": len(rows),
        "existing_target_file_count": len(existing_target_files),
        "actual_interface_snapshot_sources": _string_list(contract.get("actual_interface_snapshot_sources"))
        if isinstance(contract, dict)
        else [],
        "actual_interface_snapshot_file_count": snapshot_file_count,
    }


def _looks_like_interface_discrepancy_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    schema_version = str(value.get("schema_version") or "").strip()
    if "interface_discrepancy" in schema_version or schema_version == "director.interface_delta.v1":
        return True
    if isinstance(value.get("interface_delta"), dict) or isinstance(value.get("triage_summary"), dict):
        return True
    if str(value.get("recommended_route") or "").strip() in {
        "pending_design_interface_contract",
        "director_retry_with_interface_discrepancy_context",
        "task_boundary_interface_discrepancy",
    }:
        return True
    if str(value.get("plan_probe_status") or value.get("reason") or "").strip() == "coverage_matched_but_unplannable":
        return True
    return bool(value.get("interface_delta_available") or value.get("triage_summary_available"))


def _first_interface_discrepancy_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                return dict(item)
    return {}


def _find_interface_discrepancy_context(value: Any, *, depth: int = 0) -> dict[str, Any]:
    if depth > 5:
        return {}
    if isinstance(value, dict):
        for key in _INTERFACE_DISCREPANCY_CONTEXT_KEYS:
            found = _first_interface_discrepancy_mapping(value.get(key))
            if _looks_like_interface_discrepancy_payload(found):
                return found
        if _looks_like_interface_discrepancy_payload(value):
            return dict(value)
        for key in (
            "metadata",
            "context",
            "repair",
            "run_ledger",
            "run_ledger_projection",
            "evidence",
            "physical_evidence",
            "task_boundary",
            "task_boundary_quality",
            "plan_probe_preaudit",
            "task_metadata",
        ):
            found = _find_interface_discrepancy_context(value.get(key), depth=depth + 1)
            if found:
                return found
        modalities = value.get("modalities")
        if isinstance(modalities, dict):
            for modality in modalities.values():
                found = _find_interface_discrepancy_context(modality, depth=depth + 1)
                if found:
                    return found
        elif isinstance(modalities, (list, tuple)):
            for modality in modalities:
                found = _find_interface_discrepancy_context(modality, depth=depth + 1)
                if found:
                    return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_interface_discrepancy_context(item, depth=depth + 1)
            if found:
                return found
    return {}


def _interface_discrepancy_context_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    context_payload = _request_context(ai_request)
    payload = _find_interface_discrepancy_context(context_payload)
    if not payload:
        payload = _find_interface_discrepancy_context(_task_metadata(ai_request))
    if not payload:
        payload = _find_interface_discrepancy_context(_execution_contract(ai_request))
    if not payload:
        payload = _find_interface_discrepancy_context(_execution_envelope(ai_request))
    if not payload:
        return {}
    nested_evidence = _find_interface_discrepancy_context(payload.get("interface_discrepancy_evidence"))
    if nested_evidence:
        payload = {**nested_evidence, **payload}

    metadata = _mapping(payload.get("metadata"))
    interface_delta = payload.get("interface_delta")
    if not isinstance(interface_delta, dict):
        interface_delta = metadata.get("interface_delta")
    triage_summary = payload.get("triage_summary")
    if not isinstance(triage_summary, dict):
        triage_summary = metadata.get("triage_summary")
    interface_delta_map = dict(interface_delta) if isinstance(interface_delta, dict) else {}
    triage_summary_map = dict(triage_summary) if isinstance(triage_summary, dict) else {}
    diagnostic_count = _int_value(
        payload.get("covered_unplannable_diagnostic_count")
        or payload.get("diagnostic_count")
        or metadata.get("covered_unplannable_diagnostic_count")
        or interface_delta_map.get("diagnostic_count")
    )
    diagnostics = payload.get("diagnostics")
    if diagnostic_count <= 0 and isinstance(diagnostics, (list, tuple)):
        diagnostic_count = len(diagnostics)
    return {
        "schema_version": "polaris.interface_discrepancy_context.evidence.v1",
        "source_schema_version": str(payload.get("schema_version") or ""),
        "source": str(payload.get("source") or payload.get("modality") or "interface_discrepancy_context"),
        "plan_probe_status": str(payload.get("plan_probe_status") or metadata.get("plan_probe_status") or ""),
        "reason": str(payload.get("reason") or metadata.get("reason") or ""),
        "recommended_owner": str(
            payload.get("recommended_owner")
            or metadata.get("recommended_owner")
            or triage_summary_map.get("recommended_owner")
            or ""
        ),
        "recommended_route": str(
            payload.get("recommended_route")
            or metadata.get("recommended_route")
            or triage_summary_map.get("recommended_route")
            or ""
        ),
        "director_retry_allowed": _bool_value(
            payload.get("director_retry_allowed")
            if payload.get("director_retry_allowed") is not None
            else metadata.get("director_retry_allowed")
            if metadata.get("director_retry_allowed") is not None
            else triage_summary_map.get("director_retry_allowed")
        ),
        "llm_fallback_blocked": _bool_value(
            payload.get("llm_fallback_blocked")
            if payload.get("llm_fallback_blocked") is not None
            else metadata.get("llm_fallback_blocked")
            if metadata.get("llm_fallback_blocked") is not None
            else triage_summary_map.get("llm_fallback_blocked")
        ),
        "interface_delta_available": bool(interface_delta_map),
        "interface_delta": interface_delta_map,
        "interface_delta_hash": _stable_digest(interface_delta_map) if interface_delta_map else "",
        "triage_summary_available": bool(triage_summary_map),
        "triage_summary": triage_summary_map,
        "triage_summary_hash": _stable_digest(triage_summary_map) if triage_summary_map else "",
        "diagnostic_count": diagnostic_count,
        "source_tools": _string_list(payload.get("source_tools") or metadata.get("source_tools")),
    }


def _first_evidence_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                return dict(item)
    return {}


def _evidence_mapping_for_keys(value: Any, *, keys: tuple[str, ...]) -> dict[str, Any]:
    accepted_refs = {_evidence_ref(key) for key in keys}
    if "failed_gate_evidence" in accepted_refs:
        merged = merge_failure_evidence_payload({}, value)
        if merged.get("items"):
            return dict(merged)
    return _first_evidence_mapping(value)


def _evidence_ref(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return str(final_request_evidence_ref_for_requirement(value) or "")


def _context_slot_payload(value: Any, *, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    accepted_refs = {_evidence_ref(key) for key in keys}
    slot_ref = _evidence_ref(
        str(
            value.get("ref_type")
            or value.get("evidence_ref")
            or value.get("slot_type")
            or value.get("evidence_type")
            or value.get("name")
            or ""
        )
    )
    if slot_ref not in accepted_refs:
        return {}
    for payload_key in ("payload", "evidence", "value", "source_payload", "details"):
        payload = _evidence_mapping_for_keys(value.get(payload_key), keys=keys)
        if payload:
            return payload
    return dict(value)


def _find_structured_evidence_context(
    value: Any,
    *,
    keys: tuple[str, ...],
    predicate: Any,
    depth: int = 0,
) -> dict[str, Any]:
    if depth > 5:
        return {}
    if isinstance(value, dict):
        slot_payload = _context_slot_payload(value, keys=keys)
        if predicate(slot_payload):
            return slot_payload
        for key in keys:
            found = _evidence_mapping_for_keys(value.get(key), keys=keys)
            if predicate(found):
                return found
        if predicate(value):
            return dict(value)
        for key in (
            "metadata",
            "context",
            "evidence",
            "run_ledger",
            "run_ledger_projection",
            "evidence_policy",
            "quality",
            "quality_gate",
            "workspace_quality",
            "context_evidence_slots",
            "evidence_slots",
            "typed_evidence_slots",
            "required_evidence_slots",
            "task_boundary",
            "task_boundary_verdict",
            "task_metadata",
        ):
            found = _find_structured_evidence_context(
                value.get(key),
                keys=keys,
                predicate=predicate,
                depth=depth + 1,
            )
            if found:
                return found
        modalities = value.get("modalities")
        if isinstance(modalities, dict):
            for modality in modalities.values():
                found = _find_structured_evidence_context(
                    modality,
                    keys=keys,
                    predicate=predicate,
                    depth=depth + 1,
                )
                if found:
                    return found
        elif isinstance(modalities, (list, tuple)):
            for modality in modalities:
                found = _find_structured_evidence_context(
                    modality,
                    keys=keys,
                    predicate=predicate,
                    depth=depth + 1,
                )
                if found:
                    return found
    elif isinstance(value, (list, tuple)):
        for item in value:
            found = _find_structured_evidence_context(item, keys=keys, predicate=predicate, depth=depth + 1)
            if found:
                return found
    return {}


def _failed_gate_evidence_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    for payload in (
        _request_context(ai_request),
        _task_metadata(ai_request),
        _execution_contract(ai_request),
        _execution_envelope(ai_request),
    ):
        found = _find_structured_evidence_context(
            payload,
            keys=_FAILED_GATE_EVIDENCE_CONTEXT_KEYS,
            predicate=looks_like_failure_evidence_payload,
        )
        if found:
            return dict(summarize_failed_gate_evidence_context_slot(found))
    return {}


def _workspace_quality_evidence_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    for payload in (
        _request_context(ai_request),
        _task_metadata(ai_request),
        _execution_contract(ai_request),
        _execution_envelope(ai_request),
    ):
        found = _find_structured_evidence_context(
            payload,
            keys=_WORKSPACE_QUALITY_EVIDENCE_CONTEXT_KEYS,
            predicate=looks_like_workspace_quality_evidence_payload,
        )
        if found:
            return dict(summarize_workspace_quality_evidence_context_slot(found))
    return {}


def _architecture_payload_from_blueprint(value: Any) -> dict[str, Any]:
    blueprint = _mapping(value)
    if not blueprint:
        return {}
    payload: dict[str, Any] = {
        key: blueprint.get(key)
        for key in (
            "construction_plan",
            "scope_for_apply",
            "architecture_decisions",
            "execution_checklist",
            "target_files",
            "scope_paths",
        )
        if blueprint.get(key) not in (None, "", [])
    }
    llm_blueprint = _mapping(blueprint.get("llm_blueprint"))
    for key in (
        "implementation_phases",
        "module_boundaries",
        "verification_steps",
        "scope_for_apply_advisory",
        "risk_flags",
    ):
        if llm_blueprint.get(key) not in (None, "", []):
            payload[f"llm_blueprint.{key}"] = llm_blueprint.get(key)
    return payload


def _looks_like_architecture_or_file_plan_payload(value: Any) -> bool:
    """Return whether a direct context value is structured plan evidence."""

    if isinstance(value, dict):
        payload = value.get("payload")
        if payload is not None and _looks_like_architecture_or_file_plan_payload(payload):
            return True
        if _architecture_payload_from_blueprint(value):
            return True
        return any(
            value.get(key) not in (None, "", [])
            for key in (
                "implementation_phases",
                "module_boundaries",
                "verification_steps",
                "file_plan",
                "architecture_plan",
                "architecture_or_file_plan",
            )
        )
    if isinstance(value, (list, tuple)):
        return any(_looks_like_architecture_or_file_plan_payload(item) for item in value)
    return False


def _architecture_payload_from_delivery_contracts(ai_request: Any) -> dict[str, Any]:
    delivery_plan = _delivery_contract_payload(ai_request, "delivery_plan_document")
    delivery_depth = _delivery_contract_payload(ai_request, "delivery_depth_contract")
    payload: dict[str, Any] = {}
    if delivery_plan:
        payload["delivery_plan_document"] = {
            key: delivery_plan.get(key)
            for key in (
                "title",
                "language",
                "project_type",
                "product_summary",
                "user_journey",
                "capability_plan",
                "behavior_plan",
                "verification_plan",
                "evolution_notes",
            )
            if delivery_plan.get(key) not in (None, "", [])
        }
    if delivery_depth:
        payload["delivery_depth_contract"] = {
            key: delivery_depth.get(key)
            for key in (
                "product_intent",
                "behavior_contract",
                "acceptance_contract",
                "level_contract",
                "required_evidence",
            )
            if delivery_depth.get(key) not in (None, "", [])
        }
    return payload


def _architecture_or_file_plan_payload(ai_request: Any | None) -> dict[str, Any]:
    if ai_request is None:
        return {}
    context_payload = _request_context(ai_request)
    for key in _ARCHITECTURE_OR_FILE_PLAN_KEYS:
        raw = context_payload.get(key)
        if _looks_like_architecture_or_file_plan_payload(raw):
            return {"source": f"context.{key}", "payload": raw}
    for key in ("ce_blueprint", "chief_engineer_blueprint", "blueprint", "blueprint_payload", "task_blueprint"):
        payload = _architecture_payload_from_blueprint(context_payload.get(key))
        if payload:
            return {"source": f"context.{key}", "payload": payload}
    delivery_contract_payload = _architecture_payload_from_delivery_contracts(ai_request)
    if delivery_contract_payload:
        return {"source": "delivery_contracts", "payload": delivery_contract_payload}
    task_metadata = _task_metadata(ai_request)
    for key in ("architecture_decisions", "execution_checklist", "implementation_plan", "file_plan"):
        raw = task_metadata.get(key)
        if raw not in (None, "", []):
            return {"source": f"task_metadata.{key}", "payload": raw}
    execution_profile = _execution_profile(ai_request)
    raw_decisions = execution_profile.get("architecture_decisions")
    if raw_decisions not in (None, "", []):
        return {"source": "execution_profile.architecture_decisions", "payload": raw_decisions}
    return {}


def _architecture_or_file_plan_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    plan_payload = payload.get("payload")
    plan_mapping = _mapping(plan_payload)
    return {
        "source": str(payload.get("source") or ""),
        "construction_plan_present": bool(plan_mapping.get("construction_plan")),
        "scope_for_apply_count": len(_string_list(plan_mapping.get("scope_for_apply"))),
        "architecture_decision_count": len(_string_list(plan_mapping.get("architecture_decisions"))),
        "execution_checklist_count": len(_string_list(plan_mapping.get("execution_checklist"))),
        "target_files_count": len(_string_list(plan_mapping.get("target_files"))),
        "payload_hash": _stable_digest(plan_payload),
    }


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
    execution_contract = _execution_contract(ai_request)
    execution_envelope = _execution_envelope(ai_request)
    execution_profile_summary = _execution_profile_summary(ai_request)
    execution_contract_summary = _execution_contract_summary(ai_request)
    execution_envelope_summary = _execution_envelope_summary(ai_request)
    delivery_plan_document = _delivery_contract_payload(ai_request, "delivery_plan_document")
    delivery_depth_contract = _delivery_contract_payload(ai_request, "delivery_depth_contract")
    task_metadata = _task_metadata(ai_request)
    pm_contract = _pm_contract_payload(ai_request)
    ce_blueprint = _ce_blueprint_payload(ai_request)
    target_scope = _target_scope_payload(ai_request)
    module_interface_contract = _module_interface_contract_payload(ai_request)
    actual_sibling_exports = _actual_sibling_exports_payload(ai_request, module_interface_contract)
    interface_discrepancy_context = _interface_discrepancy_context_payload(ai_request)
    architecture_or_file_plan = _architecture_or_file_plan_payload(ai_request)
    failed_gate_evidence = _failed_gate_evidence_payload(ai_request)
    workspace_quality_evidence = _workspace_quality_evidence_payload(ai_request)
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
        "reasoning_budget_tokens": (
            options.get("reasoning_budget_tokens") if isinstance(options.get("reasoning_budget_tokens"), int) else None
        ),
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
        "has_execution_contract": bool(execution_contract),
        "execution_contract_summary": execution_contract_summary,
        "execution_contract_hash": _stable_digest(execution_contract) if execution_contract else "",
        "has_execution_envelope": bool(execution_envelope),
        "execution_envelope_summary": execution_envelope_summary,
        "execution_envelope_hash": _execution_envelope_hash(ai_request, execution_envelope),
        "has_delivery_plan_document": bool(delivery_plan_document),
        "delivery_plan_document_hash": _stable_digest(delivery_plan_document) if delivery_plan_document else "",
        "has_delivery_depth_contract": bool(delivery_depth_contract),
        "delivery_depth_contract_hash": _stable_digest(delivery_depth_contract) if delivery_depth_contract else "",
        "has_pm_contract": bool(pm_contract),
        "pm_contract_summary": _pm_contract_summary(pm_contract),
        "pm_contract_hash": _stable_digest(pm_contract) if pm_contract else "",
        "has_chief_engineer_blueprint": bool(ce_blueprint),
        "chief_engineer_blueprint_summary": _ce_blueprint_summary(ce_blueprint),
        "chief_engineer_blueprint_hash": _stable_digest(ce_blueprint) if ce_blueprint else "",
        "has_target_scope": bool(target_scope),
        "target_scope_summary": summarize_target_scope_evidence_payload(target_scope),
        "target_scope_hash": _stable_digest(target_scope) if target_scope else "",
        "has_task_metadata": bool(task_metadata),
        "task_metadata_keys": sorted(str(key) for key in task_metadata),
        "task_metadata_hash": _stable_digest(task_metadata) if task_metadata else "",
        "has_module_interface_contract": bool(module_interface_contract),
        "module_interface_contract_summary": _module_interface_contract_summary(module_interface_contract),
        "module_interface_contract_hash": _stable_digest(module_interface_contract)
        if module_interface_contract
        else "",
        "has_actual_sibling_exports": bool(actual_sibling_exports),
        "actual_sibling_exports_summary": actual_sibling_exports,
        "actual_sibling_exports_hash": _stable_digest(actual_sibling_exports) if actual_sibling_exports else "",
        "has_interface_discrepancy_context": bool(interface_discrepancy_context),
        "interface_discrepancy_context_summary": interface_discrepancy_context,
        "interface_discrepancy_context_hash": _stable_digest(interface_discrepancy_context)
        if interface_discrepancy_context
        else "",
        "has_architecture_or_file_plan": bool(architecture_or_file_plan),
        "architecture_or_file_plan_summary": _architecture_or_file_plan_summary(architecture_or_file_plan),
        "architecture_or_file_plan_hash": _stable_digest(architecture_or_file_plan)
        if architecture_or_file_plan
        else "",
        "has_failed_gate_evidence": bool(failed_gate_evidence),
        "failed_gate_evidence_summary": failed_gate_evidence,
        "failed_gate_evidence_hash": _stable_digest(failed_gate_evidence) if failed_gate_evidence else "",
        "has_workspace_quality_evidence": bool(workspace_quality_evidence),
        "workspace_quality_evidence_summary": workspace_quality_evidence,
        "workspace_quality_evidence_hash": _stable_digest(workspace_quality_evidence)
        if workspace_quality_evidence
        else "",
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
    request_sampling = _mapping(_request_context(ai_request).get("request_sampling"))
    profile = _execution_profile(ai_request)
    contract = _execution_contract(ai_request)
    raw_contract_sampling = contract.get("sampling")
    contract_sampling: dict[str, Any] = dict(raw_contract_sampling) if isinstance(raw_contract_sampling, dict) else {}
    temperature = options.get("temperature")
    max_tokens = options.get("max_tokens")
    return {
        "temperature": temperature if isinstance(temperature, (int, float)) else None,
        "max_tokens": max_tokens if isinstance(max_tokens, int) else None,
        "temperature_source": str(
            request_sampling.get("temperature_source")
            or profile.get("temperature_source")
            or contract_sampling.get("temperature_source")
            or "request_options"
        ),
        "temperature_phase": str(profile.get("temperature_phase") or contract_sampling.get("temperature_phase") or ""),
        "sampling_mode": str(profile.get("sampling_mode") or contract_sampling.get("sampling_mode") or ""),
        "task_type": str(profile.get("task_type") or contract.get("task_type") or _task_type_value(ai_request)),
        "phase": str(profile.get("phase") or contract.get("phase") or ""),
        "execution_profile_schema": str(profile.get("schema_version") or ""),
        "execution_profile_source": str(profile.get("source") or ""),
        "execution_contract_schema": str(contract.get("schema_version") or ""),
        "execution_contract_source": str(contract.get("source") or ""),
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


def _tool_name_from_schema(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    function_payload = tool.get("function")
    function = function_payload if isinstance(function_payload, dict) else tool
    return str(function.get("name") or "").strip()


def _tool_names_from_payload(value: Any) -> list[str]:
    if isinstance(value, str):
        return _unique_strings(value)
    if isinstance(value, dict):
        direct_name = str(value.get("name") or "").strip()
        if direct_name:
            return [direct_name]
        function_payload = value.get("function")
        if isinstance(function_payload, dict):
            function_name = str(function_payload.get("name") or "").strip()
            if function_name:
                return [function_name]
        names: list[str] = []
        for key in ("required_tools", "tools", "allowed_tools", "available_tools"):
            names.extend(_tool_names_from_payload(value.get(key)))
        return _unique_strings(names)
    if isinstance(value, (list, tuple, set, frozenset)):
        item_names: list[str] = []
        for item in value:
            item_names.extend(_tool_names_from_payload(item))
        return _unique_strings(item_names)
    return []


def _canonical_tool_name(name: Any) -> str:
    token = str(name or "").strip()
    if not token:
        return ""
    try:
        return str(ToolSpecRegistry.get_canonical(token) or token).strip()
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
        return token


def _canonical_tool_names(values: Any) -> list[str]:
    return _unique_strings(
        [canonical for value in _tool_names_from_payload(values) if (canonical := _canonical_tool_name(value))]
    )


def _required_tool_names_from_payload(value: Any) -> list[str]:
    if isinstance(value, str):
        return _canonical_tool_names(value)
    if isinstance(value, dict):
        names: list[str] = []
        for key in (
            "required_tools",
            "task_required_tools",
            "must_call_tools",
            "mandatory_tools",
            "contract_required_tools",
            "tool_requirements",
        ):
            names.extend(_required_tool_names_from_payload(value.get(key)))
        return _unique_strings(names)
    if isinstance(value, (list, tuple, set, frozenset)):
        nested_names: list[str] = []
        for item in value:
            nested_names.extend(_required_tool_names_from_payload(item))
        return _unique_strings(nested_names)
    return []


def _allowed_tool_names_from_payload(value: Any) -> list[str]:
    if isinstance(value, str):
        return _canonical_tool_names(value)
    if isinstance(value, dict):
        names: list[str] = []
        direct_name = str(value.get("name") or "").strip()
        if direct_name:
            names.append(direct_name)
        function_payload = value.get("function")
        if isinstance(function_payload, dict):
            function_name = str(function_payload.get("name") or "").strip()
            if function_name:
                names.append(function_name)
        for key in ("allowed_tools", "available_tools", "offered_tools", "tools"):
            names.extend(_allowed_tool_names_from_payload(value.get(key)))
        return _unique_strings([canonical for name in names if (canonical := _canonical_tool_name(name))])
    if isinstance(value, (list, tuple, set, frozenset)):
        nested_names: list[str] = []
        for item in value:
            nested_names.extend(_allowed_tool_names_from_payload(item))
        return _unique_strings(nested_names)
    return []


def _available_tool_names(tool_schema_payload: Any) -> list[str]:
    if not isinstance(tool_schema_payload, list):
        return []
    return _unique_strings(
        [canonical for tool in tool_schema_payload if (canonical := _canonical_tool_name(_tool_name_from_schema(tool)))]
    )


def _tool_schema_properties(tool_schema: Any) -> dict[str, Any]:
    if not isinstance(tool_schema, dict):
        return {}
    function_payload = tool_schema.get("function")
    if not isinstance(function_payload, dict):
        return {}
    parameters = function_payload.get("parameters")
    if not isinstance(parameters, dict):
        return {}
    properties = parameters.get("properties")
    return dict(properties) if isinstance(properties, dict) else {}


def _tool_schema_registry_coverage(
    tool_schema_payload: Any,
    *,
    missing_required_tools: list[str],
) -> dict[str, Any]:
    """Project registry provenance from the exact final provider tool surface.

    The provider request is authoritative.  Registry provenance must therefore
    be reconstructed from its offered schemas, not trusted from caller-supplied
    context flags.  B3.5 performs the stricter byte/shape validation again at
    qualification time; this projection supplies the auditable source and
    alias coverage that qualification binds to that same request.
    """

    if not isinstance(tool_schema_payload, list) or not tool_schema_payload:
        return {
            "registry_source": "",
            "aliases_present": False,
            "arg_aliases_present": False,
            "schema_hash": "",
            "missing_schema_tools": _unique_strings(missing_required_tools),
        }

    missing_schema_tools = list(missing_required_tools)
    aliases_present = True
    arg_aliases_present = True
    for tool_schema in tool_schema_payload:
        raw_name = _tool_name_from_schema(tool_schema)
        canonical_name = _canonical_tool_name(raw_name)
        try:
            captured = ToolSpecRegistry.capture_effective_spec(raw_name)
            schema_with_aliases = ToolSpecRegistry.get_llm_schema(
                canonical_name,
                include_arg_aliases=True,
                deterministic=True,
            )
            schema_without_aliases = ToolSpecRegistry.get_llm_schema(
                canonical_name,
                include_arg_aliases=False,
                deterministic=True,
            )
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
            captured = None
            schema_with_aliases = None
            schema_without_aliases = None

        if (
            captured is None
            or captured.registered is not True
            or not canonical_name
            or schema_with_aliases is None
            or schema_without_aliases is None
        ):
            missing_schema_tools.append(canonical_name or raw_name)
            aliases_present = False
            arg_aliases_present = False
            continue

        expected_alias_properties = set(_tool_schema_properties(schema_with_aliases)).difference(
            _tool_schema_properties(schema_without_aliases)
        )
        actual_properties = set(_tool_schema_properties(tool_schema))
        if not expected_alias_properties.issubset(actual_properties):
            arg_aliases_present = False

    missing_schema_tools = _unique_strings(missing_schema_tools)
    if missing_schema_tools:
        aliases_present = False
        arg_aliases_present = False
    return {
        "registry_source": _TOOL_REGISTRY_SOURCE if not missing_schema_tools else "",
        "aliases_present": aliases_present,
        "arg_aliases_present": arg_aliases_present,
        "schema_hash": _stable_digest(tool_schema_payload),
        "missing_schema_tools": missing_schema_tools,
    }


def _required_tool_names(ai_request: Any) -> list[str]:
    context_payload = _request_context(ai_request)
    names: list[str] = []
    for key in ("required_tools", "task_required_tools", "tool_requirements", "tool_contract"):
        names.extend(_required_tool_names_from_payload(context_payload.get(key)))
    return _unique_strings(names)


_NO_TOOL_CONTRACT_CONTEXT_KEYS = (
    "tool_contract_require_no_tool_calls",
    "require_no_tool_calls",
    "no_tool_calls",
)


def _required_tools_exempt_reason(ai_request: Any, prepared: PreparedLLMRequest) -> str:
    """Reason string when this request's tool surface is disabled BY DESIGN.

    A finalization-style call (tool_choice ``none``/``disabled``, an explicit
    no-tool contract, or a TransactionKernel forced tool disable) exposes zero
    callable tools on purpose. Required-tool semantics inherited from the turn
    context must not be reported as ``missing_required_tools`` for such a call:
    the tools are not missing — they are not exposed by design. An empty tool
    surface WITHOUT one of these explicit disable signals is still treated as
    required-tool pruning and keeps failing coverage.
    """

    options = _request_options(ai_request, prepared)
    tool_choice = str(options.get("tool_choice") or "").strip().lower()
    if tool_choice in {"none", "disabled"}:
        return "tool_choice_disabled_by_design"
    context_payload = _request_context(ai_request)
    if any(bool(context_payload.get(key)) for key in _NO_TOOL_CONTRACT_CONTEXT_KEYS):
        return "tool_contract_requires_no_tool_calls"
    tool_contract = _mapping(context_payload.get("tool_contract"))
    if any(bool(tool_contract.get(key)) for key in _NO_TOOL_CONTRACT_CONTEXT_KEYS):
        return "tool_contract_requires_no_tool_calls"
    forced_definitions = context_payload.get("_transaction_kernel_forced_tool_definitions")
    forced_choice = str(context_payload.get("_transaction_kernel_forced_tool_choice") or "").strip().lower()
    if isinstance(forced_definitions, list) and not forced_definitions and forced_choice == "none":
        return "transaction_kernel_tools_disabled"
    return ""


def _allowed_tool_names(ai_request: Any) -> list[str]:
    context_payload = _request_context(ai_request)
    names: list[str] = []
    for key in ("allowed_tools", "available_tools", "offered_tools", "tool_policy", "tool_contract"):
        names.extend(_allowed_tool_names_from_payload(context_payload.get(key)))
    return _unique_strings(names)


def _envelope_hash_for_ref(envelope: dict[str, Any], section: str) -> str:
    payload = _mapping(envelope.get(section))
    raw_hash = payload.get("hash")
    return str(raw_hash or "").strip()


def _workflow_chain(
    *,
    ai_request: Any,
    request_metadata_summary: dict[str, Any],
    envelope: dict[str, Any],
) -> dict[str, str]:
    context_payload = _request_context(ai_request)
    return {
        "pm_contract_hash": str(
            context_payload.get("pm_contract_hash")
            or context_payload.get("contract_hash")
            or _envelope_hash_for_ref(envelope, "pm_contract")
            or ""
        ),
        "ce_blueprint_hash": str(
            context_payload.get("ce_blueprint_hash")
            or context_payload.get("blueprint_hash")
            or _envelope_hash_for_ref(envelope, "ce_blueprint")
            or ""
        ),
        "handoff_decision_hash": str(
            context_payload.get("handoff_decision_hash")
            or context_payload.get("ce_handoff_decision_hash")
            or _envelope_hash_for_ref(envelope, "handoff_decision")
            or ""
        ),
        "execution_profile_hash": str(request_metadata_summary.get("execution_profile_hash") or ""),
        "execution_envelope_hash": str(request_metadata_summary.get("execution_envelope_hash") or ""),
    }


def _mapped_evidence_requirements(raw_requirements: Any) -> list[str]:
    refs: list[str] = []
    for item in _string_list(raw_requirements):
        refs.append(final_request_evidence_ref_for_requirement(item))
    return _unique_strings(refs)


def _required_evidence_refs(
    *,
    ai_request: Any,
    role_id: str,
    coverage: dict[str, bool],
    request_metadata_summary: dict[str, Any],
    execution_strategy: dict[str, Any],
    envelope: dict[str, Any],
) -> list[str]:
    envelope_audit_policy = _mapping(envelope.get("audit_policy"))
    refs = _mapped_evidence_requirements(execution_strategy.get("evidence_requirements"))
    refs.extend(_mapped_evidence_requirements(envelope_audit_policy.get("required_evidence")))
    if not refs:
        normalized_role = role_id.strip().lower()
        try:
            refs.extend(
                _mapped_evidence_requirements(
                    role_final_request_policy(normalized_role).required_present_slots,
                )
            )
        except ValueError:
            refs.extend(
                final_request_evidence_refs_for_coverage_flags(
                    coverage,
                    excluded_flags=_OPTIONAL_CONTEXT_QUALITY_FLAGS,
                )
            )
    if request_metadata_summary.get("has_execution_profile"):
        refs.extend(_mapped_evidence_requirements(("execution_profile",)))
    if request_metadata_summary.get("has_execution_strategy"):
        refs.extend(_mapped_evidence_requirements(("execution_strategy", "execution_envelope")))
    if request_metadata_summary.get("has_execution_envelope"):
        refs.extend(_mapped_evidence_requirements(("execution_envelope",)))
    context_payload = _request_context(ai_request)
    refs.extend(_mapped_evidence_requirements(context_payload.get("required_evidence")))
    if any(key in context_payload for key in _INTERFACE_DISCREPANCY_CONTEXT_KEYS):
        refs.extend(_mapped_evidence_requirements(("interface_discrepancy_context",)))
    return _unique_strings(refs)


def _final_request_evidence_enforcement_source(ai_request: Any) -> str:
    context_payload = _request_context(ai_request)
    option_payload = getattr(ai_request, "options", None)
    option_payload = option_payload if isinstance(option_payload, dict) else {}
    execution_strategy = _execution_strategy(ai_request)
    envelope = _execution_envelope(ai_request)
    envelope_audit_policy = _mapping(envelope.get("audit_policy"))

    for key in (
        "final_request_evidence_required",
        "enforce_final_request_evidence_coverage",
        "required_evidence_enforcement",
    ):
        if _bool_value(context_payload.get(key)) or _bool_value(option_payload.get(key)):
            return f"request.{key}"
        if _bool_value(execution_strategy.get(key)):
            return f"execution_strategy.{key}"
        if _bool_value(envelope_audit_policy.get(key)):
            return f"execution_envelope.audit_policy.{key}"

    if _bool_value(envelope_audit_policy.get("final_provider_request_required")):
        return "execution_envelope.audit_policy.final_provider_request_required"

    return ""


def final_request_evidence_coverage_violation(
    *,
    ai_request: Any,
    audit: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a strict evidence coverage violation, if this request must fail closed."""

    source = _final_request_evidence_enforcement_source(ai_request)
    if not source:
        return None
    evidence_coverage = audit.get("final_request_evidence_coverage")
    if not isinstance(evidence_coverage, dict) or evidence_coverage.get("pass") is True:
        return None
    missing_refs = missing_required_refs_from_evidence_coverage(evidence_coverage)
    missing_tools = missing_required_tools_from_evidence_coverage(evidence_coverage)
    if not missing_refs and not missing_tools and evidence_coverage.get("role_identity_ok", True):
        return None
    message_parts = ["Final provider request evidence coverage failed"]
    if missing_refs:
        message_parts.append("missing_required_refs=" + ",".join(missing_refs))
    if missing_tools:
        message_parts.append("missing_required_tools=" + ",".join(missing_tools))
    if evidence_coverage.get("role_identity_ok") is False:
        message_parts.append("role_identity_mismatch")
    return {
        "schema_version": "polaris.final_request_evidence_enforcement.v1",
        "source": source,
        "role_id": str(evidence_coverage.get("role_id") or ""),
        "expected_role_id": str(evidence_coverage.get("expected_role_id") or ""),
        "role_identity_ok": bool(evidence_coverage.get("role_identity_ok", True)),
        "missing_required_refs": missing_refs,
        "missing_required_tools": missing_tools,
        "request_hash": str(evidence_coverage.get("request_hash") or audit.get("request_hash") or ""),
        "message": "; ".join(message_parts),
    }


def enforce_final_request_evidence_coverage(
    *,
    ai_request: Any,
    audit: dict[str, Any],
) -> None:
    """Fail closed when strict final-request evidence coverage is incomplete."""

    violation = final_request_evidence_coverage_violation(ai_request=ai_request, audit=audit)
    if violation is not None:
        raise FinalRequestEvidenceCoverageError(violation)


def _ledger_evidence(ai_request: Any, *, receipt_refs: list[str] | None = None) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    ledger = _mapping(context_payload.get("run_ledger")) or _mapping(context_payload.get("run_ledger_projection"))
    ledger_policy = _mapping(ledger.get("evidence_policy"))
    merged_receipt_refs: list[str] = []
    merged_receipt_refs.extend(_string_list(context_payload.get("receipt_refs")))
    merged_receipt_refs.extend(_string_list(ledger.get("receipt_refs")))
    merged_receipt_refs.extend(receipt_refs or [])
    return {
        "run_ledger_ref": str(
            context_payload.get("run_ledger_ref")
            or context_payload.get("run_ledger_projection_ref")
            or ledger.get("ref")
            or ""
        ),
        "failed_required_modalities": _string_list(
            context_payload.get("failed_required_modalities")
            or ledger.get("failed_required_modalities")
            or ledger_policy.get("failed_required_modalities")
        ),
        "missing_required_modalities": _string_list(
            context_payload.get("missing_required_modalities")
            or ledger.get("missing_required_modalities")
            or ledger_policy.get("missing_required_modalities")
        ),
        "receipt_refs": _unique_strings(merged_receipt_refs),
    }


def _final_request_hash(
    *,
    ai_request: Any,
    prepared: PreparedLLMRequest,
    messages: list[dict[str, Any]],
    tool_schema_payload: Any,
    response_format_payload: Any,
) -> str:
    return _stable_digest(
        {
            "role": _non_empty_attr(ai_request, name="role"),
            "task_type": _task_type_value(ai_request),
            "messages_hash": _stable_digest(messages),
            "tool_schema_hash": _stable_digest(tool_schema_payload),
            "response_format_hash": _stable_digest(response_format_payload),
            "sampling": _request_sampling_audit(ai_request, prepared),
        }
    )


def _final_request_evidence_coverage(
    *,
    ai_request: Any,
    prepared: PreparedLLMRequest,
    profile: Any,
    messages: list[dict[str, Any]],
    coverage: dict[str, bool],
    request_metadata_summary: dict[str, Any],
    tool_schema_payload: Any,
    response_format_payload: Any,
) -> dict[str, Any]:
    envelope = _execution_envelope(ai_request)
    execution_strategy = _execution_strategy(ai_request)
    role_id = _non_empty_attr(ai_request, name="role") or _non_empty_attr(profile, name="role_id") or "unknown"
    expected_role_id = _non_empty_attr(profile, name="role_id") or role_id
    role_identity_ok = role_id.strip().lower() == expected_role_id.strip().lower()
    receipt_refs = _final_request_receipt_refs(
        ai_request=ai_request,
        prepared=prepared,
        messages=messages,
    )
    included_refs = final_request_included_evidence_refs(
        coverage=coverage,
        request_metadata_summary=request_metadata_summary,
        receipt_refs=receipt_refs,
    )
    required_refs = _required_evidence_refs(
        ai_request=ai_request,
        role_id=role_id,
        coverage=coverage,
        request_metadata_summary=request_metadata_summary,
        execution_strategy=execution_strategy,
        envelope=envelope,
    )
    missing_required_refs = [ref for ref in required_refs if ref not in included_refs]
    available_tools = _available_tool_names(tool_schema_payload)
    required_tools = _required_tool_names(ai_request)
    allowed_tools = _allowed_tool_names(ai_request)
    required_tools_exempt: list[str] = []
    required_tools_exempt_reason = ""
    if required_tools:
        required_tools_exempt_reason = _required_tools_exempt_reason(ai_request, prepared)
        if required_tools_exempt_reason:
            # The call exposes no callable tools BY DESIGN: keep the stale claim
            # as audit evidence, but do not require tools this call cannot call.
            required_tools_exempt = required_tools
            required_tools = []
    missing_required_tools = [tool for tool in required_tools if tool not in available_tools]
    removed_allowed_tools = [tool for tool in allowed_tools if available_tools and tool not in available_tools]
    workflow_chain = _workflow_chain(
        ai_request=ai_request,
        request_metadata_summary=request_metadata_summary,
        envelope=envelope,
    )
    coverage_source_refs = _unique_strings([*required_refs, *included_refs])
    coverage_sources = build_final_request_coverage_sources(
        refs=coverage_source_refs,
        included_refs=included_refs,
        workflow_chain=workflow_chain,
        request_metadata_summary=request_metadata_summary,
    )
    total_required = len(required_refs) + len(required_tools)
    total_missing = len(missing_required_refs) + len(missing_required_tools)
    coverage_ratio = 1.0 if total_required == 0 else max(0.0, (total_required - total_missing) / total_required)
    return {
        "schema_version": "polaris.final_request_evidence_coverage.v1",
        "request_hash": _final_request_hash(
            ai_request=ai_request,
            prepared=prepared,
            messages=messages,
            tool_schema_payload=tool_schema_payload,
            response_format_payload=response_format_payload,
        ),
        "context_snapshot_ref": str(_request_context(ai_request).get("context_snapshot_ref") or ""),
        "role_id": role_id,
        "expected_role_id": expected_role_id,
        "role_identity_ok": role_identity_ok,
        "required_refs": required_refs,
        "included_refs": included_refs,
        "missing_required_refs": missing_required_refs,
        "coverage_sources": coverage_sources,
        "evidence_slots": build_final_request_evidence_slots(
            coverage_sources=coverage_sources,
            required_refs=required_refs,
            included_refs=included_refs,
            missing_required_refs=missing_required_refs,
        ),
        "required_tools": required_tools,
        "allowed_tools": allowed_tools,
        "available_tools": available_tools,
        "missing_required_tools": missing_required_tools,
        "tool_evidence_slots": build_final_request_tool_slots(
            required_tools=required_tools,
            available_tools=available_tools,
            missing_required_tools=missing_required_tools,
        ),
        "removed_allowed_tools": removed_allowed_tools,
        "tool_surface": {
            "required_tools": required_tools,
            "allowed_tools": allowed_tools,
            "offered_tools": available_tools,
            "missing_required_tools": missing_required_tools,
            "removed_allowed_tools": removed_allowed_tools,
            "required_tools_exempt": required_tools_exempt,
            "required_tools_exempt_reason": required_tools_exempt_reason,
            "required_tool_source": "explicit_required_tool_fields_only",
            "allowed_tool_source": "allowed_available_policy_contract_fields",
            "canonicalized": True,
        },
        "unexpected_tool_pruning": [
            {
                "tool": tool,
                "reason": "required_tool_missing_from_final_provider_request",
                "source": "final_request_evidence_coverage",
            }
            for tool in missing_required_tools
        ],
        "tool_schema_registry_coverage": _tool_schema_registry_coverage(
            tool_schema_payload,
            missing_required_tools=missing_required_tools,
        ),
        "structured_evidence": final_request_structured_evidence_from_metadata_summary(request_metadata_summary),
        "workflow_chain": workflow_chain,
        "ledger_evidence": _ledger_evidence(ai_request, receipt_refs=receipt_refs),
        "redaction_safety": _final_request_redaction_safety(messages),
        "coverage_ratio": round(coverage_ratio, 4),
        "pass": bool(role_identity_ok and not missing_required_refs and not missing_required_tools),
    }


def _add_evidence_coverage_findings(quality: dict[str, Any], evidence_coverage: dict[str, Any]) -> dict[str, Any]:
    findings = list(quality.get("findings") or [])
    evidence_pass = bool(evidence_coverage.get("pass"))
    if evidence_pass:
        findings = [
            item
            for item in findings
            if not (isinstance(item, dict) and item.get("code") in _REF_BASED_SUPERSEDED_FINDING_CODES)
        ]
    missing_refs = missing_required_refs_from_evidence_coverage(evidence_coverage)
    if missing_refs:
        findings.append(
            {
                "code": "missing_required_final_request_evidence",
                "severity": "warning",
                "missing_required_refs": list(missing_refs),
                "request_hash": evidence_coverage.get("request_hash", ""),
            }
        )
    missing_tools = missing_required_tools_from_evidence_coverage(evidence_coverage)
    if missing_tools:
        findings.append(
            {
                "code": "missing_required_final_request_tools",
                "severity": "error",
                "missing_required_tools": list(missing_tools),
                "request_hash": evidence_coverage.get("request_hash", ""),
            }
        )
    if evidence_coverage.get("role_identity_ok") is False:
        findings.append(
            {
                "code": "final_request_role_identity_mismatch",
                "severity": "error",
                "role_id": evidence_coverage.get("role_id", ""),
                "expected_role_id": evidence_coverage.get("expected_role_id", ""),
                "request_hash": evidence_coverage.get("request_hash", ""),
            }
        )
    return {
        **quality,
        "missing_coverage": [] if evidence_pass else list(quality.get("missing_coverage") or []),
        "context_needs_review": bool(findings),
        "findings": findings,
        "final_request_evidence_coverage_pass": evidence_pass,
        "missing_required_refs": list(missing_refs),
        "missing_required_tools": list(missing_tools),
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
    coverage = _coverage_flags(ai_request=ai_request)
    prompt_profile_selection = _prompt_profile_selection(ai_request)
    sampling = _request_sampling_audit(ai_request, prepared)
    request_metadata_summary = _request_metadata_summary(ai_request, prepared)
    execution_profile_summary = request_metadata_summary.get("execution_profile_summary", {})
    execution_contract_summary = request_metadata_summary.get("execution_contract_summary", {})
    execution_profile = _execution_profile(ai_request)
    execution_strategy = _execution_strategy(ai_request)
    execution_contract = _execution_contract(ai_request)
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
        response_format_payload=response_format_payload,
    )
    quality = _add_evidence_coverage_findings(quality, evidence_coverage)
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
