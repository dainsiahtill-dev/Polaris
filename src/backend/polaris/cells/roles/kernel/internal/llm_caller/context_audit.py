"""Final LLM request context audit helpers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from polaris.kernelone.context.projection_engine import is_empty_run_card_message

from .response_types import PreparedLLMRequest

_UNDERUTILIZED_WINDOW_THRESHOLD = 8192
_UNDERUTILIZED_RATIO = 0.15
_RECEIPT_REF_RE = re.compile(r"receipt://([A-Za-z0-9_.:/-]+)")
_REF_BASED_SUPERSEDED_FINDING_CODES = frozenset(
    {
        "missing_context_coverage",
        "underutilized_with_missing_context",
    }
)
_COVERAGE_FLAG_TO_REF = {
    "has_pm_contract": "pm_contract",
    "has_chief_engineer_blueprint": "ce_blueprint",
    "has_target_files": "target_files",
    "has_failure_feedback": "failed_gate_evidence",
    "has_workspace_quality_evidence": "workspace_quality_evidence",
    "has_resident_agi_decision_trace": "resident_agi_decision_trace",
    "has_resident_agi_capability_surface": "resident_agi_capability_surface",
    "has_resident_agi_decision_boundary": "resident_agi_decision_boundary",
}
_EVIDENCE_REQUIREMENT_TO_REF = {
    "pm_task_contract": "pm_contract",
    "pm_contract": "pm_contract",
    "pm_delivery_plan_document": "delivery_plan_document",
    "delivery_plan_document": "delivery_plan_document",
    "delivery_plan": "delivery_plan_document",
    "design_intent": "delivery_plan_document",
    "pm_delivery_depth_contract": "delivery_depth_contract",
    "delivery_depth_contract": "delivery_depth_contract",
    "behavior_contract": "delivery_depth_contract",
    "behavior_matrix": "delivery_depth_contract",
    "chief_engineer_blueprint": "ce_blueprint",
    "ce_blueprint": "ce_blueprint",
    "target_files_or_declared_scopes": "target_files",
    "target_files": "target_files",
    "declared_scopes": "target_files",
    "language_best_practices": "language_guidance",
    "execution_profile": "execution_profile",
    "execution_strategy": "execution_strategy",
    "execution_envelope": "execution_envelope",
    "final_provider_request": "final_provider_request",
    "final_provider_request_audit": "final_provider_request",
    "run_ledger": "run_ledger",
    "workspace_quality_evidence": "workspace_quality_evidence",
    "failed_gate_evidence": "failed_gate_evidence",
}


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
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            total += len(str(message))
            continue
        total += len(str(message.get("role") or ""))
        total += len(str(message.get("content") or ""))
    return total


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
        messages: list[dict[str, Any]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")
            content = str(item.get("content") or "")
            message = {"role": role, "content": content}
            name = str(item.get("name") or "").strip()
            if name:
                message["name"] = name
            messages.append(message)
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
    if depth > 4:
        return []
    if isinstance(value, str):
        return [match.group(1).strip() for match in _RECEIPT_REF_RE.finditer(value) if match.group(1).strip()]
    if isinstance(value, dict):
        mapping_refs = _string_list(value.get("receipt_refs"))
        for key in ("content", "text", "message", "messages", "parts"):
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
    execution_contract: dict[str, Any],
    message_projection_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    missing = [key for key, ok in coverage.items() if not ok]
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
    execution_contract = _execution_contract(ai_request)
    execution_envelope = _execution_envelope(ai_request)
    execution_profile_summary = _execution_profile_summary(ai_request)
    execution_contract_summary = _execution_contract_summary(ai_request)
    execution_envelope_summary = _execution_envelope_summary(ai_request)
    delivery_plan_document = _delivery_contract_payload(ai_request, "delivery_plan_document")
    delivery_depth_contract = _delivery_contract_payload(ai_request, "delivery_depth_contract")
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
    contract = _execution_contract(ai_request)
    raw_contract_sampling = contract.get("sampling")
    contract_sampling: dict[str, Any] = dict(raw_contract_sampling) if isinstance(raw_contract_sampling, dict) else {}
    temperature = options.get("temperature")
    max_tokens = options.get("max_tokens")
    return {
        "temperature": temperature if isinstance(temperature, (int, float)) else None,
        "max_tokens": max_tokens if isinstance(max_tokens, int) else None,
        "temperature_source": str(
            profile.get("temperature_source") or contract_sampling.get("temperature_source") or "request_options"
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


def _available_tool_names(tool_schema_payload: Any) -> list[str]:
    if not isinstance(tool_schema_payload, list):
        return []
    return _unique_strings([name for tool in tool_schema_payload if (name := _tool_name_from_schema(tool))])


def _required_tool_names(ai_request: Any) -> list[str]:
    context_payload = _request_context(ai_request)
    names: list[str] = []
    for key in (
        "required_tools",
        "task_required_tools",
        "tool_requirements",
        "allowed_tools",
        "tool_policy",
        "tool_contract",
    ):
        names.extend(_tool_names_from_payload(context_payload.get(key)))
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
        key = item.strip().lower()
        refs.append(_EVIDENCE_REQUIREMENT_TO_REF.get(key, key))
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
        if normalized_role == "director":
            refs.extend(["pm_contract", "ce_blueprint", "target_files"])
        elif normalized_role == "chief_engineer":
            refs.extend(["pm_contract", "target_files"])
        elif normalized_role == "pm":
            refs.extend(["pm_raw_intent"])
        else:
            refs.extend(ref for flag, ref in _COVERAGE_FLAG_TO_REF.items() if flag in coverage)
    if request_metadata_summary.get("has_execution_profile"):
        refs.append("execution_profile")
    if request_metadata_summary.get("has_execution_strategy"):
        refs.append("execution_strategy")
        refs.append("execution_envelope")
    if request_metadata_summary.get("has_execution_envelope"):
        refs.append("execution_envelope")
    context_payload = _request_context(ai_request)
    refs.extend(_mapped_evidence_requirements(context_payload.get("required_evidence")))
    return _unique_strings(refs)


def _included_evidence_refs(
    *,
    coverage: dict[str, bool],
    request_metadata_summary: dict[str, Any],
    receipt_refs: list[str] | None = None,
) -> list[str]:
    refs = ["final_provider_request"]
    refs.extend(ref for flag, ref in _COVERAGE_FLAG_TO_REF.items() if coverage.get(flag))
    if request_metadata_summary.get("has_execution_profile"):
        refs.append("execution_profile")
    if request_metadata_summary.get("has_execution_strategy"):
        refs.append("execution_strategy")
    if request_metadata_summary.get("has_execution_contract"):
        refs.append("execution_contract")
    if request_metadata_summary.get("has_execution_envelope"):
        refs.append("execution_envelope")
    if request_metadata_summary.get("has_delivery_plan_document"):
        refs.append("delivery_plan_document")
    if request_metadata_summary.get("has_delivery_depth_contract"):
        refs.append("delivery_depth_contract")
    if request_metadata_summary.get("has_language_guidance"):
        refs.append("language_guidance")
    if request_metadata_summary.get("has_output_contract"):
        refs.append("output_contract")
    if request_metadata_summary.get("has_task_metadata"):
        refs.append("task_metadata")
    if receipt_refs:
        refs.append("receipt_store_refs")
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
    missing_refs = [str(item) for item in evidence_coverage.get("missing_required_refs") or [] if str(item).strip()]
    missing_tools = [str(item) for item in evidence_coverage.get("missing_required_tools") or [] if str(item).strip()]
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


def _coverage_source(
    *,
    ref_type: str,
    present: bool,
    workflow_chain: dict[str, str],
    request_metadata_summary: dict[str, Any],
) -> dict[str, Any]:
    hash_by_ref = {
        "pm_contract": workflow_chain.get("pm_contract_hash", ""),
        "ce_blueprint": workflow_chain.get("ce_blueprint_hash", ""),
        "handoff_decision": workflow_chain.get("handoff_decision_hash", ""),
        "execution_profile": workflow_chain.get("execution_profile_hash", ""),
        "execution_envelope": workflow_chain.get("execution_envelope_hash", ""),
        "execution_contract": str(request_metadata_summary.get("execution_contract_hash") or ""),
        "task_metadata": str(request_metadata_summary.get("task_metadata_hash") or ""),
    }
    structured_refs = {
        "execution_profile",
        "execution_strategy",
        "execution_contract",
        "execution_envelope",
        "task_metadata",
        "language_guidance",
        "output_contract",
    }
    source = "final_provider_request"
    confidence = "absent"
    if present and ref_type in structured_refs:
        confidence = "structured_metadata"
    elif present:
        confidence = "text_heuristic"
    result = {
        "ref_type": ref_type,
        "present": present,
        "source": source,
        "confidence": confidence,
        "freshness": "current_turn" if present else "unknown",
    }
    hash_value = hash_by_ref.get(ref_type, "")
    if hash_value:
        result["hash"] = hash_value
    return result


def _ledger_evidence(ai_request: Any, *, receipt_refs: list[str] | None = None) -> dict[str, Any]:
    context_payload = _request_context(ai_request)
    ledger = _mapping(context_payload.get("run_ledger")) or _mapping(context_payload.get("run_ledger_projection"))
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
            context_payload.get("failed_required_modalities") or ledger.get("failed_required_modalities")
        ),
        "missing_required_modalities": _string_list(
            context_payload.get("missing_required_modalities") or ledger.get("missing_required_modalities")
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
    included_refs = _included_evidence_refs(
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
    missing_required_tools = [tool for tool in required_tools if tool not in available_tools]
    workflow_chain = _workflow_chain(
        ai_request=ai_request,
        request_metadata_summary=request_metadata_summary,
        envelope=envelope,
    )
    coverage_source_refs = _unique_strings([*required_refs, *included_refs])
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
        "coverage_sources": [
            _coverage_source(
                ref_type=ref,
                present=ref in included_refs,
                workflow_chain=workflow_chain,
                request_metadata_summary=request_metadata_summary,
            )
            for ref in coverage_source_refs
        ],
        "required_tools": required_tools,
        "available_tools": available_tools,
        "missing_required_tools": missing_required_tools,
        "unexpected_tool_pruning": [
            {
                "tool": tool,
                "reason": "required_tool_missing_from_final_provider_request",
                "source": "final_request_evidence_coverage",
            }
            for tool in missing_required_tools
        ],
        "tool_schema_registry_coverage": {
            "registry_source": str(_request_context(ai_request).get("tool_registry_source") or ""),
            "aliases_present": bool(_request_context(ai_request).get("tool_aliases")),
            "arg_aliases_present": bool(_request_context(ai_request).get("tool_arg_aliases")),
            "schema_hash": _stable_digest(tool_schema_payload) if tool_schema_payload else "",
            "missing_schema_tools": missing_required_tools,
        },
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
    missing_refs = evidence_coverage.get("missing_required_refs")
    if isinstance(missing_refs, list) and missing_refs:
        findings.append(
            {
                "code": "missing_required_final_request_evidence",
                "severity": "warning",
                "missing_required_refs": [str(item) for item in missing_refs],
                "request_hash": evidence_coverage.get("request_hash", ""),
            }
        )
    missing_tools = evidence_coverage.get("missing_required_tools")
    if isinstance(missing_tools, list) and missing_tools:
        findings.append(
            {
                "code": "missing_required_final_request_tools",
                "severity": "error",
                "missing_required_tools": [str(item) for item in missing_tools],
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
        "missing_required_refs": list(evidence_coverage.get("missing_required_refs") or []),
        "missing_required_tools": list(evidence_coverage.get("missing_required_tools") or []),
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
        "prompt_profile_selection": prompt_profile_selection,
        "selected_prompt_profile_ids": prompt_profile_selection.get("selected_prompt_profile_ids", []),
    }
