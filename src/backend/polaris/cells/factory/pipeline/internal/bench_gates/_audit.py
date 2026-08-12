"""Failure attribution, canonical projection, and goal-audit aggregation."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ..factory_stage_helpers import _runtime_row_execution_completed
from ..run_ledger import summarize_run_ledger_projection
from ._core import (
    _FAILURE_CATEGORIES,
    _FINAL_QA_GATE_NAMES,
    _TASK_RUNTIME_FACT_SOURCE,
    CANONICAL_BENCH_PROJECTION_SCHEMA,
    CANONICAL_BENCH_PROJECTION_SOURCE,
    LEGACY_BENCH_ARTIFACT_SOURCE,
)
from ._gates import collect_llm_events


def _gate_failures(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [gate for gate in record.get("factory_gates") or [] if isinstance(gate, dict) and not gate.get("ok")]


def _check_failures(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [check for check in record.get("checks") or [] if isinstance(check, dict) and not check.get("ok")]


def _contains_context_budget_signal(text: str) -> bool:
    return bool(
        re.search(
            r"context[_ -]?(?:window|budget|length|limit)|"
            r"token[_ -]?budget|max[_ -]?tokens|"
            r"context_length_exceeded|prompt[_ -]?too[_ -]?long|"
            r"input[_ -]?tokens?[^.]{0,80}(?:exceed|limit)|"
            r"(?:context|prompt|message)[_ -]?truncated",
            text,
            re.IGNORECASE,
        )
    )


def _first_real_run_failure(real_run_gate: dict[str, Any]) -> str:
    requirements = real_run_gate.get("requirements")
    if not isinstance(requirements, dict):
        return ""
    for name, payload in requirements.items():
        if isinstance(payload, dict) and not payload.get("ok"):
            return str(name)
    return ""


def _category_signature(category: str, reason: str) -> str:
    stable_category = category if category in _FAILURE_CATEGORIES else "unknown"
    stable_reason = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", reason.strip().lower()).strip("_") or "unknown"
    return f"{stable_category}:{stable_reason}"


def _iter_mapping_payloads(value: Any, *, limit: int = 600) -> Iterable[dict[str, Any]]:
    """Yield nested dict payloads without treating text projections as facts."""

    stack: list[Any] = [value]
    seen = 0
    while stack and seen < limit:
        current = stack.pop()
        if isinstance(current, dict):
            seen += 1
            yield current
            stack.extend(current.values())
        elif isinstance(current, list | tuple):
            stack.extend(current)


_TASK_BOUNDARY_FAILURE_STATUSES = frozenset(
    {
        "artifact_semantic_mismatch",
        "dependency_not_unlocked",
        "execution_evidence_missing",
        "incomplete_materialization",
        "missing_entrypoint_target",
        "required_evidence_failed",
        "required_verifier_failed",
        "required_verifier_missing",
        "tool_dispatch_dropped",
        "unresolved_local_import",
    }
)
_TASK_BOUNDARY_FAILURE_CLASSES = frozenset(
    {
        "blueprint_scope_mismatch",
        "dependency_not_unlocked",
        "execution_evidence_missing",
        "implementation_defect",
        "incomplete_materialization",
        "missing_entrypoint_target",
        "tool_dispatch_dropped",
        "unresolved_local_import",
    }
)


def _runtime_dir_candidates(record: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    runtime_dir = str(record.get("runtime_dir") or "").strip()
    if runtime_dir:
        candidates.append(Path(runtime_dir))
    runtime_dirs = record.get("runtime_dirs")
    if isinstance(runtime_dirs, list):
        for item in runtime_dirs:
            path_text = str(item or "").strip()
            if path_text:
                candidates.append(Path(path_text))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _load_runtime_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_repair_plan_probe(record: dict[str, Any]) -> dict[str, Any]:
    """Return the first nested repair plan-probe payload with stable structure."""

    for payload in _iter_mapping_payloads(record):
        for key in (
            "plan_probe_preaudit",
            "repair_plan_probe",
            "workspace_quality_repair_plan_probe",
            "workspace_quality_repair_plan_probe_report",
        ):
            nested = payload.get(key)
            if isinstance(nested, dict) and _is_repair_plan_probe_payload(nested):
                return nested
        if _is_repair_plan_probe_payload(payload):
            return payload
    return {}


def _is_repair_plan_probe_payload(payload: dict[str, Any]) -> bool:
    schema = str(payload.get("schema_version") or "")
    status = str(payload.get("status") or "")
    if schema.startswith("director.repair_plan_probe_result"):
        return True
    return bool(
        status
        and (
            "plannable_source_tools" in payload
            or "covered_unplannable_source_tools" in payload
            or "covered_unplannable_diagnostics" in payload
            or "uncovered_diagnostics" in payload
        )
    )


def _record_repair_convergence_attribution(record: dict[str, Any]) -> tuple[str, str, str] | None:
    """Classify failures where runtime repair knows a concrete plan but convergence still failed."""

    plan_probe = _first_repair_plan_probe(record)
    if not plan_probe:
        return None

    status = str(plan_probe.get("status") or "").strip()
    plannable_source_tools = [
        str(item).strip() for item in plan_probe.get("plannable_source_tools") or [] if str(item or "").strip()
    ]
    covered_unplannable_source_tools = [
        str(item).strip()
        for item in plan_probe.get("covered_unplannable_source_tools") or []
        if str(item or "").strip()
    ]
    if status == "covered_plannable" and plannable_source_tools:
        return (
            "repair_convergence",
            "covered_plannable_not_converged",
            f"plan_probe:covered_plannable;plannable_source_tools={','.join(plannable_source_tools[:8])}",
        )
    if status == "coverage_matched_but_unplannable" or covered_unplannable_source_tools:
        return (
            "task_boundary",
            "repair_plan_probe_unplannable",
            "plan_probe:coverage_matched_but_unplannable;"
            f"covered_unplannable_source_tools={','.join(covered_unplannable_source_tools[:8])}",
        )
    return None


def _first_task_boundary_verdict(record: dict[str, Any]) -> dict[str, Any]:
    """Return an unresolved TaskBoundary verdict from the canonical projection.

    Factory/bench is a read-only consumer of the Execution Ledger projection.
    Director result files, prompt text, and recursively discovered mappings are
    deliberately excluded because reconstructing a verdict from those views
    creates a second execution-state authority.
    """

    projection = record.get("run_ledger_projection")
    projection_map = projection if isinstance(projection, dict) else {}
    if projection_map.get("source") != "run_ledger_projection":
        return {}
    task_boundary = projection_map.get("task_boundary")
    task_boundary_map = task_boundary if isinstance(task_boundary, dict) else {}
    failed = task_boundary_map.get("failed")
    candidates = [dict(item) for item in failed if isinstance(item, dict)] if isinstance(failed, list) else []
    for payload in candidates:
        status = str(payload.get("status") or payload.get("verdict_status") or "").strip().lower()
        failure_class = str(payload.get("failure_class") or "").strip().lower()
        if status != "dependency_not_unlocked" and failure_class != "dependency_not_unlocked":
            return payload
    return candidates[0] if candidates else {}


def _record_task_boundary_attribution(record: dict[str, Any]) -> tuple[str, str, str] | None:
    verdict = _first_task_boundary_verdict(record)
    if not verdict:
        return None

    status = str(verdict.get("status") or verdict.get("verdict_status") or "").strip() or "task_boundary_failed"
    failure_class = str(verdict.get("failure_class") or "").strip()
    responsible_layer = str(verdict.get("responsible_layer") or "").strip()
    reason = str(verdict.get("reason") or "").strip()
    responsible_layer_key = responsible_layer.lower()
    if responsible_layer_key in {"control_plane", "execution_control_plane"}:
        control_plane_reason = failure_class.lower() or status.lower() or "task_boundary_failed"
        return (
            "control_plane",
            control_plane_reason,
            reason or f"TaskBoundary verdict: {control_plane_reason}",
        )
    return (
        "task_boundary",
        status,
        ";".join(
            item
            for item in (
                f"failure_class={failure_class}" if failure_class else "",
                f"responsible_layer={responsible_layer}" if responsible_layer else "",
                reason,
            )
            if item
        ),
    )


_EXECUTION_CONTROL_PLANE_FAILURE_CLASSES: dict[str, str] = {
    "required_tool_text_fallback_not_dispatched": "required_tool_text_fallback_not_dispatched",
    "session_not_active": "session_not_active",
    "tool_dispatch_dropped": "tool_dispatch_dropped",
    "tool_dispatch_failed": "tool_dispatch_failed",
    "tool_lifecycle_failed": "tool_dispatch_failed",
}


def _record_execution_control_plane_attribution(record: dict[str, Any]) -> tuple[str, str, str] | None:
    """Classify unresolved tool-transaction facts from the Run Ledger only."""

    projection = record.get("run_ledger_projection")
    projection_map = projection if isinstance(projection, dict) else {}
    if projection_map.get("source") != "run_ledger_projection":
        return None
    lifecycle = projection_map.get("tool_lifecycle")
    lifecycle_map = lifecycle if isinstance(lifecycle, dict) else {}
    unresolved = lifecycle_map.get("unresolved_by_task")
    unresolved_map = unresolved if isinstance(unresolved, dict) else {}
    for raw_event in unresolved_map.values():
        if not isinstance(raw_event, dict):
            continue
        failure_class = str(raw_event.get("failure_class") or "").strip().lower()
        reason = _EXECUTION_CONTROL_PLANE_FAILURE_CLASSES.get(failure_class)
        if not reason:
            continue
        detail = str(raw_event.get("reason") or raw_event.get("status") or failure_class).strip()
        return "control_plane", reason, detail
    for raw_failure_class in projection_map.get("failed_control_plane_events") or ():
        failure_class = str(raw_failure_class or "").strip().lower()
        reason = _EXECUTION_CONTROL_PLANE_FAILURE_CLASSES.get(failure_class)
        if reason:
            return "control_plane", reason, failure_class
    return None


def _check_failure_is_runtime_environment(check: dict[str, Any]) -> bool:
    text = json.dumps(check, ensure_ascii=False, default=str)
    return bool(re.search(r"\bunavailable\b|not found|toolchain unavailable|compiler unavailable", text, re.IGNORECASE))


def _record_has_generated_artifact_failure(record: dict[str, Any]) -> bool:
    """Return true when the failure points at malformed generated artifacts."""
    failed_checks = _check_failures(record)
    if any(
        str(check.get("check") or "").lower() in {"ts_syntax", "js_syntax", "py_compile"} for check in failed_checks
    ):
        return True

    text = json.dumps(
        {
            "checks": failed_checks,
            "real_run_gate": record.get("real_run_gate"),
        },
        ensure_ascii=False,
        default=str,
    )
    return bool(
        re.search(
            r"syntax check failed|syntaxerror|unexpected keyword|"
            r"\bTS\d{3,5}\b|compile failed|build failed|test failed|lint failed|"
            r"npm run (?:build|test|lint|check) failed|"
            r"package\.json missing devDependency 'typescript'|"
            r"shell command substitution|package manifest script|"
            r"sh:\s*\d+:\s*[A-Za-z0-9_.-]+:\s*not found|invalid source content",
            text,
            re.IGNORECASE,
        )
    )


def _nested_chain_results(record: dict[str, Any]) -> dict[str, Any]:
    chain_results = record.get("chain_results")
    if isinstance(chain_results, dict):
        return chain_results
    chain = record.get("chain")
    if isinstance(chain, dict):
        chain_results = chain.get("chain_results")
        if isinstance(chain_results, dict):
            return chain_results
    return {}


def _director_failure_evidence(record: dict[str, Any]) -> str:
    chain = record.get("chain")
    audit_bundle = chain.get("audit_bundle") if isinstance(chain, dict) else {}
    failure = audit_bundle.get("failure") if isinstance(audit_bundle, dict) else {}
    if isinstance(failure, dict):
        detail = str(failure.get("detail") or failure.get("code") or "").strip()
        if detail:
            return detail
    return ""


def _director_failure_tokens(record: dict[str, Any]) -> str:
    chain = record.get("chain")
    audit_bundle = chain.get("audit_bundle") if isinstance(chain, dict) else {}
    failure = audit_bundle.get("failure") if isinstance(audit_bundle, dict) else {}
    if not isinstance(failure, dict):
        return ""
    values: list[str] = []
    for key in (
        "detail",
        "code",
        "error_code",
        "reason_code",
        "failure_class",
        "materialization_error",
        "materialization_mode",
    ):
        value = str(failure.get(key) or "").strip()
        if value:
            values.append(value)
    return "\n".join(values).lower()


def _director_failure_reason(record: dict[str, Any]) -> str:
    text = _director_failure_tokens(record)
    if "binding fanout" in text or "quarantined" in text:
        return "director_binding_fanout_failed"
    if (
        "director_materialization_quality_failed" in text
        or "director_missing_write_receipt" in text
        or "director_no_materialized_changes" in text
    ):
        return "director_materialization_failed"
    if "director.run_status_non_success" in text:
        return "director_run_status_non_success"
    return "director_execution_failed"


def _record_has_director_execution_failure(record: dict[str, Any]) -> bool:
    chain_results = _nested_chain_results(record)
    director = chain_results.get("director") if isinstance(chain_results, dict) else {}
    if isinstance(director, dict) and (int(director.get("failures") or 0) > 0 or int(director.get("blocked") or 0) > 0):
        return True

    text = json.dumps(
        {
            "terminal_status": record.get("terminal_status"),
            "chain": record.get("chain"),
            "failed_gates": _gate_failures(record),
        },
        ensure_ascii=False,
        default=str,
    )
    return bool(
        re.search(
            r"director(?:[_ .-]?binding)?[_ .-]?fanout|"
            r"director[_ .-]?dispatch failed|"
            r"director[_ .-]?materialization[_ .-]?quality[_ .-]?failed|"
            r"director[_ .-]?missing[_ .-]?write[_ .-]?receipt|"
            r"director[_ .-]?no[_ .-]?materialized[_ .-]?changes|"
            r"director\.run_status_non_success|"
            r"director_partial|"
            r"\\bquarantined\\b",
            text,
            re.IGNORECASE,
        )
    )


def _record_has_explicit_director_execution_failure(record: dict[str, Any]) -> bool:
    chain_results = _nested_chain_results(record)
    director = chain_results.get("director") if isinstance(chain_results, dict) else {}
    if isinstance(director, dict) and (int(director.get("failures") or 0) > 0 or int(director.get("blocked") or 0) > 0):
        return True

    text = json.dumps(
        {
            "chain": record.get("chain"),
            "failed_gates": _gate_failures(record),
        },
        ensure_ascii=False,
        default=str,
    )
    return bool(
        re.search(
            r"director(?:[_ .-]?binding)?[_ .-]?fanout|"
            r"director[_ .-]?dispatch failed|"
            r"director[_ .-]?materialization[_ .-]?quality[_ .-]?failed|"
            r"director[_ .-]?missing[_ .-]?write[_ .-]?receipt|"
            r"director[_ .-]?no[_ .-]?materialized[_ .-]?changes|"
            r"director\.run_status_non_success|"
            r"\\bquarantined\\b",
            text,
            re.IGNORECASE,
        )
    )


_RUNTIME_ENVIRONMENT_FAILURE_TOKENS = (
    "cognitive_runtime_mainline_unavailable",
    "event_wait_timeout",
    "runtime_v2_connection_failed",
    "mainline_unavailable:process",
    "process:filenotfounderror",
    "pm.runtime.exception",
    "runtime.environment",
    "workspace_switch_failed",
)
_MODEL_PROVIDER_RATE_LIMIT_TOKENS = (
    "director.provider_rate_limit",
    "rate_limit",
    "rate limit",
    "rate-limited",
    "too many requests",
    "token plan",
    "用量上限",
)
_MODEL_PROVIDER_UNAVAILABLE_TOKENS = (
    "director.provider_unavailable",
    "circuit_open",
    "circuit breaker is open",
)
_MODEL_PROVIDER_TIMEOUT_TOKENS = (
    "director.provider_timeout",
    "model_provider_timeout",
    "provider_timeout",
    "request timeout",
    "transport timeout",
    "connecttimeouterror",
    "readtimeouterror",
    "timed out",
    "connect timeout",
)
_MODEL_PROVIDER_INVALID_REQUEST_TOKENS = (
    "invalid_request_error",
    "tool_choice",
    "incompatible with thinking",
    "thinking mode does not support",
)


def _has_model_provider_invalid_request(text: str) -> bool:
    lowered = str(text or "").lower()
    return all(token in lowered for token in _MODEL_PROVIDER_INVALID_REQUEST_TOKENS[:3]) or (
        "thinking mode does not support" in lowered and "tool_choice" in lowered
    )


def _record_model_provider_failure_text(record: dict[str, Any]) -> str:
    event_error_texts: list[str] = []
    for event in _record_llm_events(record):
        if str(event.get("role") or "").strip().lower() != "director":
            continue
        if bool(event.get("skipped")):
            continue
        event_name = str(event.get("event") or "").strip().lower()
        if event_name not in {"llm_error", "call_error", "error"} and not bool(event.get("terminal")):
            continue
        error_text = _llm_event_error_text(event)
        if error_text:
            event_error_texts.append(error_text)
    return json.dumps(
        {
            "failure_reasons": record.get("failure_reasons"),
            "failure_evidence": record.get("failure_evidence"),
            "chain": record.get("chain"),
            "chain_diagnostics": record.get("chain_diagnostics"),
            "llm_route_audit": record.get("llm_route_audit"),
            "factory_gates": record.get("factory_gates"),
            "llm_event_errors": event_error_texts,
        },
        ensure_ascii=False,
        default=str,
    ).lower()


def _record_llm_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    workspace_text = str(record.get("workspace") or record.get("project_workspace") or "").strip()
    if not workspace_text:
        return []
    runtime_candidates: list[Path] = []
    runtime_dir = str(record.get("runtime_dir") or "").strip()
    if runtime_dir:
        runtime_candidates.append(Path(runtime_dir))
    runtime_dirs = record.get("runtime_dirs")
    if isinstance(runtime_dirs, list):
        for item in runtime_dirs:
            path_text = str(item or "").strip()
            if path_text:
                runtime_candidates.append(Path(path_text))
    try:
        return collect_llm_events(Path(workspace_text), runtime_candidates or None)
    except (OSError, RuntimeError, ValueError, TypeError):
        return []


def _llm_event_error_text(event: dict[str, Any]) -> str:
    raw_value = event.get("raw")
    raw = raw_value if isinstance(raw_value, dict) else {}
    data_value = raw.get("data")
    data = data_value if isinstance(data_value, dict) else {}
    metadata_value = raw.get("metadata")
    metadata = metadata_value if isinstance(metadata_value, dict) else {}
    data_metadata_value = data.get("metadata")
    data_metadata = data_metadata_value if isinstance(data_metadata_value, dict) else {}
    parts: list[str] = []
    for source in (event, raw, data, metadata, data_metadata):
        for key in (
            "event",
            "event_type",
            "error_category",
            "error_code",
            "error_message",
            "message",
            "status",
            "retry_decision",
        ):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n".join(parts)


def _record_has_model_provider_failure(record: dict[str, Any]) -> bool:
    text = _record_model_provider_failure_text(record)
    return (
        any(token in text for token in _MODEL_PROVIDER_RATE_LIMIT_TOKENS)
        or any(token in text for token in _MODEL_PROVIDER_TIMEOUT_TOKENS)
        or any(token in text for token in _MODEL_PROVIDER_UNAVAILABLE_TOKENS)
        or _has_model_provider_invalid_request(text)
    )


def _model_provider_failure_reason(record: dict[str, Any]) -> str:
    text = _record_model_provider_failure_text(record)
    if any(token in text for token in _MODEL_PROVIDER_RATE_LIMIT_TOKENS):
        return "director_provider_rate_limit"
    if any(token in text for token in _MODEL_PROVIDER_TIMEOUT_TOKENS):
        return "director_provider_timeout"
    if any(token in text for token in _MODEL_PROVIDER_UNAVAILABLE_TOKENS):
        return "director_provider_unavailable"
    if _has_model_provider_invalid_request(text):
        return "director_provider_invalid_request"
    return "model_provider_failure"


def _model_provider_failure_evidence(record: dict[str, Any]) -> str:
    for event in _record_llm_events(record):
        if str(event.get("role") or "").strip().lower() != "director":
            continue
        error_text = _llm_event_error_text(event)
        lowered = error_text.lower()
        if (
            any(token in lowered for token in _MODEL_PROVIDER_RATE_LIMIT_TOKENS)
            or any(token in lowered for token in _MODEL_PROVIDER_TIMEOUT_TOKENS)
            or any(token in lowered for token in _MODEL_PROVIDER_UNAVAILABLE_TOKENS)
            or _has_model_provider_invalid_request(lowered)
        ):
            return error_text[:1000]
    chain = record.get("chain")
    audit_bundle = chain.get("audit_bundle") if isinstance(chain, dict) else {}
    failure = audit_bundle.get("failure") if isinstance(audit_bundle, dict) else {}
    if isinstance(failure, dict):
        detail = str(failure.get("detail") or failure.get("code") or "").strip()
        if detail:
            return detail
    return ""


def _record_has_runtime_environment_failure(record: dict[str, Any]) -> bool:
    text = json.dumps(
        {
            "failure_reasons": record.get("failure_reasons"),
            "failure_evidence": record.get("failure_evidence"),
            "chain": record.get("chain"),
            "chain_diagnostics": record.get("chain_diagnostics"),
            "factory_gates": record.get("factory_gates"),
        },
        ensure_ascii=False,
        default=str,
    ).lower()
    if "event_wait_timeout" in text or "runtime_v2_connection_failed" in text:
        return True
    if "workspace_switch_failed" in text:
        return True
    if "runtime_roles_not_ready" in text:
        return True
    if "cognitive_runtime_mainline_unavailable" in text:
        return True
    if "no available director binding after readiness filtering" in text:
        return True
    if (
        "active_binding_count" in text
        and "provider_unreachable" in text
        and re.search(r'"active_binding_count"\s*:\s*0', text)
    ):
        return True
    return "filenotfounderror" in text and (
        "pm.run_status_non_success" in text or "pm.runtime.exception" in text or "mainline_unavailable" in text
    )


def _runtime_environment_failure_reason(record: dict[str, Any]) -> str:
    text = json.dumps(record, ensure_ascii=False, default=str).lower()
    if "runtime_v2_connection_failed" in text:
        return "event_wait_runtime_v2_connection_failed"
    if "event_wait_timeout" in text:
        return "event_wait_timeout"
    if "workspace_switch_failed" in text:
        return "workspace_switch_failed"
    if "runtime_roles_not_ready" in text:
        return "runtime_roles_not_ready"
    if "cognitive_runtime_mainline_unavailable" in text:
        return "cognitive_runtime_mainline_unavailable"
    if "no available director binding after readiness filtering" in text or (
        "provider_unreachable" in text and re.search(r'"active_binding_count"\s*:\s*0', text)
    ):
        return "director_bindings_unavailable"
    if "filenotfounderror" in text:
        return "file_not_found"
    return "runtime_environment_failed"


def _runtime_environment_failure_evidence(record: dict[str, Any]) -> str:
    chain = record.get("chain")
    diagnostics = record.get("chain_diagnostics")
    if isinstance(diagnostics, dict):
        event_wait_error = diagnostics.get("event_wait_error")
        if isinstance(event_wait_error, dict):
            detail = str(event_wait_error.get("message") or event_wait_error.get("kind") or "").strip()
            if detail:
                return detail
        cancel_error = diagnostics.get("cancel_error")
        if isinstance(cancel_error, dict):
            detail = str(cancel_error.get("reason") or cancel_error.get("exception") or "").strip()
            if detail:
                return detail
    if isinstance(chain, dict):
        event_wait_error = chain.get("event_wait_error")
        if isinstance(event_wait_error, dict):
            detail = str(event_wait_error.get("message") or event_wait_error.get("kind") or "").strip()
            if detail:
                return detail
    real_run_gate = record.get("real_run_gate")
    if isinstance(real_run_gate, dict) and real_run_gate.get("skipped"):
        detail = str(real_run_gate.get("summary") or "").strip()
        if detail:
            return detail
    if isinstance(chain, dict) and str(chain.get("error") or "") == "workspace_switch_failed":
        workspace_switch = chain.get("workspace_switch")
        if isinstance(workspace_switch, dict):
            detail = str(workspace_switch.get("workspace") or workspace_switch.get("detail") or "").strip()
            if detail:
                return detail
    start_error = chain.get("start_error") if isinstance(chain, dict) else {}
    if isinstance(start_error, dict):
        payload = start_error.get("json")
        if isinstance(payload, dict):
            detail = json.dumps(payload, ensure_ascii=False, default=str)
            if detail:
                return detail
        detail = str(start_error.get("body") or "").strip()
        if detail:
            return detail
    audit_bundle = chain.get("audit_bundle") if isinstance(chain, dict) else {}
    failure = audit_bundle.get("failure") if isinstance(audit_bundle, dict) else {}
    if isinstance(failure, dict):
        detail = str(failure.get("detail") or failure.get("code") or "").strip()
        if detail:
            return detail
    for key in ("failure_evidence", "failure_reasons"):
        values = record.get(key)
        if isinstance(values, list):
            for value in values:
                text = str(value or "").strip()
                if text:
                    return text
    return ""


def _record_has_qa_artifact_quality_failure(record: dict[str, Any]) -> bool:
    """Return true when QA ran and failed on malformed generated artifacts."""
    chain_results = _nested_chain_results(record)
    if not bool(chain_results.get("qa_ran")):
        return False
    real_run_gate = record.get("real_run_gate")
    if not (isinstance(real_run_gate, dict) and not real_run_gate.get("ok")):
        return False
    return _record_has_generated_artifact_failure(record)


def _chief_engineer_failure_evidence(record: dict[str, Any]) -> str:
    chain = record.get("chain")
    audit_bundle = chain.get("audit_bundle") if isinstance(chain, dict) else {}
    failure = audit_bundle.get("failure") if isinstance(audit_bundle, dict) else {}
    if isinstance(failure, dict):
        detail = str(failure.get("detail") or failure.get("code") or "").strip()
        if detail:
            return detail
    return ""


def _chief_engineer_failure_reason(record: dict[str, Any]) -> str:
    text = json.dumps(record.get("chain") or {}, ensure_ascii=False, default=str).lower()
    if "chief_engineer.llm_review_failed" in text or "no json object matched chief_engineer blueprint keys" in text:
        return "llm_review_failed"
    if _has_partial_chief_engineer_blueprint_generation(text):
        return "partial_blueprint_generation"
    return "missing_or_invalid_blueprint"


_CE_BLUEPRINT_GENERATED_RE = re.compile(
    r"chief engineer review generated\s+(?P<generated>\d+)\s*/\s*(?P<total>\d+)\s+blueprints",
    re.IGNORECASE,
)


def _has_partial_chief_engineer_blueprint_generation(text: str) -> bool:
    for match in _CE_BLUEPRINT_GENERATED_RE.finditer(str(text or "")):
        try:
            generated = int(match.group("generated"))
            total = int(match.group("total"))
        except (TypeError, ValueError):
            continue
        if total > 0 and generated < total:
            return True
    return False


def _record_has_chief_engineer_blueprint_failure(record: dict[str, Any]) -> bool:
    if record.get("has_blueprint_doc") is False or any(
        gate.get("gate") == "blueprint_artifact_present" and not gate.get("ok") for gate in _gate_failures(record)
    ):
        return True

    text = json.dumps(
        {
            "chain_state": record.get("chain_state"),
            "chain": record.get("chain"),
            "director_convergence": record.get("director_convergence"),
        },
        ensure_ascii=False,
        default=str,
    )
    if str(record.get("chain_state") or "") == "clean":
        return False
    return bool(
        re.search(
            r"chief_engineer\.llm_review_failed|"
            r"no json object matched chief_engineer blueprint keys|"
            r"current_stage['\"]?:\s*['\"]chief_engineer_review|"
            r"blocking_phase['\"]?:\s*['\"]chief_engineer_review",
            text,
            re.IGNORECASE,
        )
        or _has_partial_chief_engineer_blueprint_generation(text)
    )


def _mapping_copy(value: object) -> dict[str, Any]:
    """Return a shallow mapping copy without coercing text into facts."""

    return dict(value) if isinstance(value, Mapping) else {}


def _first_mapping(source: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    """Return the first explicitly named mapping from ``source``."""

    for key in keys:
        value = source.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _project_runtime_status(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project TaskRuntime authority from its explicit typed audit surface.

    TaskBoundary delivery verification is an independent axis and cannot
    rewrite failed, pending, or in-progress TaskRuntime lifecycle facts.
    """

    task_runtime_projection = _mapping_copy(record.get("task_runtime_projection"))
    readiness = _mapping_copy(task_runtime_projection.get("readiness"))
    rows_raw = task_runtime_projection.get("rows")
    rows = [dict(row) for row in rows_raw if isinstance(row, Mapping)] if isinstance(rows_raw, list) else []
    rows_authoritative = all(
        row.get("source") == _TASK_RUNTIME_FACT_SOURCE
        and row.get("status_source") == _TASK_RUNTIME_FACT_SOURCE
        and isinstance(row.get("fact_event_seq"), int)
        and not isinstance(row.get("fact_event_seq"), bool)
        and int(row.get("fact_event_seq") or 0) >= 1
        for row in rows
    )
    authoritative = (
        task_runtime_projection.get("schema_version") == "task_runtime.observable_task_rows_authority.v1"
        and task_runtime_projection.get("source") == _TASK_RUNTIME_FACT_SOURCE
        and task_runtime_projection.get("authoritative") is True
        and task_runtime_projection.get("degraded") is False
        and readiness.get("ready") is True
        and rows_authoritative
    )
    incomplete_task_ids = [
        str(row.get("task_id") or "").strip()
        for row in rows
        if str(row.get("task_id") or "").strip() and not _runtime_row_execution_completed(row)
    ]
    completed = authoritative and bool(rows) and not incomplete_task_ids
    status = "completed" if completed else "incomplete"
    return {
        "source": _TASK_RUNTIME_FACT_SOURCE,
        "authoritative": authoritative,
        "degraded": not authoritative,
        "status": status,
        "phase": "",
        "current_stage": "",
        "failed_stage": "",
        "error_code": "",
        "terminal_observed": bool(rows),
        "terminal_source": _TASK_RUNTIME_FACT_SOURCE if authoritative else "",
        "task_row_read_model_source": str(task_runtime_projection.get("source") or "").strip(),
        "task_count": len(rows),
        "incomplete_task_ids": incomplete_task_ids,
        "completed": completed,
    }


def _project_qa_verdict(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Read the final QA verdict from canonical Run Ledger gate events."""

    gates = ledger.get("gates")
    candidates = [dict(item) for item in gates if isinstance(item, Mapping)] if isinstance(gates, list) else []
    for gate in reversed(candidates):
        name = str(gate.get("name") or "").strip().lower()
        stage = str(gate.get("stage") or "").strip().lower()
        if stage != "qa" or name not in _FINAL_QA_GATE_NAMES:
            continue
        content_id = str(gate.get("content_id") or "").strip()
        append_id = str(gate.get("append_id") or "").strip()
        if gate.get("capability_ok") is not True or not content_id or not append_id:
            continue
        return {
            "source": "run_ledger",
            "authoritative": True,
            "available": True,
            "ok": bool(gate.get("ok")),
            "name": name,
            "summary": str(gate.get("summary") or "").strip(),
            "content_id": content_id,
            "append_id": append_id,
            "job_token_id": str(gate.get("job_token_id") or "").strip(),
        }
    return {
        "source": "run_ledger",
        "authoritative": False,
        "available": False,
        "ok": False,
        "name": "",
        "summary": "canonical QA verdict missing",
        "content_id": "",
        "append_id": "",
        "job_token_id": "",
    }


def _project_task_boundary(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Read TaskBoundary state from the canonical Run Ledger projection."""

    raw_boundary = _mapping_copy(ledger.get("task_boundary"))
    latest = _mapping_copy(raw_boundary.get("latest"))
    failed_raw = raw_boundary.get("failed")
    failed = [dict(item) for item in failed_raw if isinstance(item, Mapping)] if isinstance(failed_raw, list) else []
    verdict_count = int(raw_boundary.get("verdict_count") or (1 if latest else 0))
    authoritative = ledger.get("source") == "run_ledger" and verdict_count > 0
    boundary_ok = authoritative and bool(raw_boundary.get("ok", latest.get("ok"))) and not failed
    return {
        **raw_boundary,
        "source": "run_ledger",
        "authoritative": authoritative,
        "available": authoritative,
        "ok": boundary_ok,
        "verdict_count": verdict_count,
        "latest": latest,
        "failed": failed,
    }


def _project_named_runtime_metadata(record: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    """Read an explicitly named runtime metadata mapping."""

    chain = _mapping_copy(record.get("chain"))
    terminal = _mapping_copy(record.get("factory_terminal_status"))
    if not terminal:
        terminal = _mapping_copy(chain.get("factory_terminal_status"))
    metadata = _mapping_copy(terminal.get("metadata"))
    for source in (record, terminal, metadata, chain):
        projected = _first_mapping(source, *keys)
        if projected:
            return projected
    return {}


def _project_factory_stage_failure(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project a structured upstream Factory-stage failure without prose parsing."""

    chain = _mapping_copy(record.get("chain"))
    audit_bundle = _mapping_copy(chain.get("audit_bundle"))
    failure = _mapping_copy(audit_bundle.get("failure"))
    stage = str(failure.get("stage") or audit_bundle.get("current_stage") or "").strip()
    code = str(failure.get("code") or "").strip()
    error_code = str(failure.get("error_code") or "").strip()
    failure_class = str(failure.get("failure_class") or "").strip()
    responsible_layer = str(failure.get("responsible_layer") or "").strip()
    authoritative = bool(
        code == "FACTORY_STAGE_FAILED" and stage and error_code and failure_class and responsible_layer
    )
    return {
        **failure,
        "source": "factory_run.stage_failure",
        "available": bool(failure),
        "authoritative": authoritative,
        "stage": stage,
        "code": code,
        "error_code": error_code,
        "failure_class": failure_class,
        "responsible_layer": responsible_layer,
    }


def _canonical_execution_verdict(
    *,
    ledger: Mapping[str, Any],
    runtime: Mapping[str, Any],
    boundary: Mapping[str, Any],
    qa: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve execution status from canonical control-plane authorities only."""

    ledger_available = ledger.get("source") == "run_ledger" and int(ledger.get("gate_count") or 0) > 0
    ledger_integrity_ok = ledger_available and bool(ledger.get("integrity_ok"))
    evidence_policy = _mapping_copy(ledger.get("evidence_policy"))
    missing_required = evidence_policy.get("missing_required_modalities")
    failed_required = evidence_policy.get("failed_required_modalities")
    evidence_integrity_ok = (
        bool(evidence_policy)
        and evidence_policy.get("integrity_ok") is True
        and not (isinstance(missing_required, list) and missing_required)
    )
    evidence_outcome_ok = (
        bool(evidence_policy)
        and evidence_policy.get("outcome_ok") is True
        and not (isinstance(failed_required, list) and failed_required)
    )
    if not ledger_available:
        reason_code = "run_ledger_projection_missing"
        failure_class = "EXECUTION_EVIDENCE_MISSING"
        responsible_layer = "control_plane"
    elif not ledger_integrity_ok:
        reason_code = "run_ledger_integrity_failed"
        failure_class = "RUN_LEDGER_INTEGRITY_FAILED"
        responsible_layer = "control_plane"
    elif not bool(boundary.get("authoritative")):
        reason_code = "task_boundary_verdict_missing"
        failure_class = "EXECUTION_EVIDENCE_MISSING"
        responsible_layer = "execution_control_plane"
    elif not bool(boundary.get("ok")):
        latest = _mapping_copy(boundary.get("latest"))
        failed = boundary.get("failed")
        if not latest and isinstance(failed, list) and failed and isinstance(failed[-1], Mapping):
            latest = dict(failed[-1])
        reason_code = str(latest.get("status") or "task_boundary_failed").strip().lower()
        failure_class = str(latest.get("failure_class") or "TASK_BOUNDARY_FAILED").strip()
        responsible_layer = str(latest.get("responsible_layer") or "task_boundary").strip()
    elif not bool(runtime.get("authoritative")):
        reason_code = "task_runtime_projection_not_authoritative"
        failure_class = "TASK_RUNTIME_PROJECTION_NOT_AUTHORITATIVE"
        responsible_layer = "execution_control_plane"
    elif bool(qa.get("authoritative")) and not bool(qa.get("ok")):
        # A failed QA verdict commonly terminal-fails its TaskRuntime helper.
        # Preserve the causal verifier failure instead of masking it behind the
        # derived ``task_runtime_not_completed`` state.
        reason_code = "qa_verdict_failed"
        failure_class = "QA_VERDICT_FAILED"
        responsible_layer = "qa"
    elif evidence_integrity_ok and not evidence_outcome_ok:
        reason_code = "required_evidence_failed"
        failure_class = "EXECUTION_EVIDENCE_FAILED"
        responsible_layer = "control_plane"
    elif not bool(runtime.get("completed")):
        reason_code = "task_runtime_not_completed"
        failure_class = "TASK_RUNTIME_NOT_COMPLETED"
        responsible_layer = "execution_control_plane"
    elif not evidence_integrity_ok:
        reason_code = "required_evidence_missing"
        failure_class = "EXECUTION_EVIDENCE_MISSING"
        responsible_layer = "control_plane"
    elif not bool(qa.get("authoritative")):
        reason_code = "qa_verdict_missing"
        failure_class = "EXECUTION_EVIDENCE_MISSING"
        responsible_layer = "qa"
    else:
        return {
            "ok": True,
            "status": "completed_verified",
            "reason_code": "completed_verified",
            "failure_class": "",
            "responsible_layer": "",
        }
    return {
        "ok": False,
        "status": "failed",
        "reason_code": reason_code,
        "failure_class": failure_class,
        "responsible_layer": responsible_layer,
    }


def build_canonical_bench_projection(record: Mapping[str, Any]) -> dict[str, Any]:
    """Build the only execution-status projection consumed by Factory Bench.

    Artifact discovery, deterministic checks, and the real-run gate remain
    separate measurements. They are deliberately excluded from the execution
    verdict so stale prose or JSON artifacts cannot rewrite canonical state.
    """

    ledger = _mapping_copy(record.get("run_ledger_projection"))
    boundary = _project_task_boundary(ledger)
    qa = _project_qa_verdict(ledger)
    lifecycle = _mapping_copy(ledger.get("tool_lifecycle"))
    effect = {
        "tool_receipts": _mapping_copy(ledger.get("tool_receipts")),
        "physical_evidence": _mapping_copy(ledger.get("physical_evidence")),
    }
    runtime = _project_runtime_status(record)
    factory_stage_failure = _project_factory_stage_failure(record)
    execution = _canonical_execution_verdict(
        ledger=ledger,
        runtime=runtime,
        boundary=boundary,
        qa=qa,
    )
    instance_id = str(record.get("instance_id") or "")
    workspace = str(record.get("workspace") or record.get("project_workspace") or "")
    run_id = str(record.get("run_id") or "")
    factory_run_id = str(record.get("factory_run_id") or "")
    backend_port = record.get("backend_port")
    frontend_port = record.get("frontend_port")
    refs_raw = record.get("final_request_refs")
    final_request_refs = (
        [dict(item) for item in refs_raw if isinstance(item, Mapping)] if isinstance(refs_raw, list) else []
    )
    legacy_artifacts = {
        "source": LEGACY_BENCH_ARTIFACT_SOURCE,
        "authoritative": False,
        "degraded": True,
        "has_plan_doc": bool(record.get("has_plan_doc")),
        "has_blueprint_doc": bool(record.get("has_blueprint_doc")),
        "has_qa_verdict": bool(record.get("has_qa_verdict")),
        "chain_results": _mapping_copy(record.get("chain_results")),
    }
    return {
        "schema_version": CANONICAL_BENCH_PROJECTION_SCHEMA,
        "source": CANONICAL_BENCH_PROJECTION_SOURCE,
        "authoritative": True,
        "degraded": False,
        "requested_project_id": str(record.get("requested_project_id") or record.get("project_id") or ""),
        "canonical_project_id": str(record.get("canonical_project_id") or record.get("project_id") or ""),
        "instance_id": instance_id,
        "instance": {"id": instance_id, "workspace": workspace},
        "workspace": workspace,
        "backend_port": backend_port,
        "frontend_port": frontend_port,
        "ports": {"backend": backend_port, "frontend": frontend_port},
        "run_id": run_id,
        "factory_run_id": factory_run_id,
        "run_ids": {"bench": run_id, "factory": factory_run_id},
        "final_request_refs": final_request_refs,
        "lifecycle": lifecycle,
        "effect": effect,
        "boundary": boundary,
        "runtime": runtime,
        "factory_stage_failure": factory_stage_failure,
        "ledger": ledger,
        "qa": qa,
        "barrier": _project_named_runtime_metadata(
            record,
            "run_ledger_barrier",
            "execution_barrier",
            "completion_barrier",
            "barrier",
        ),
        "fallback": _project_named_runtime_metadata(
            record,
            "fallback",
            "fallback_status",
            "degraded_fallback",
        ),
        "execution": execution,
        "legacy_artifacts": legacy_artifacts,
    }


def _run_ledger_projection_integrity_available(record: dict[str, Any]) -> bool:
    projection = _mapping_copy(record.get("canonical_projection"))
    ledger = _mapping_copy(projection.get("ledger"))
    if not ledger:
        ledger = _mapping_copy(record.get("run_ledger_projection"))
    return bool(
        ledger.get("source") == "run_ledger" and int(ledger.get("gate_count") or 0) > 0 and ledger.get("integrity_ok")
    )


def _stable_reason(value: object) -> str:
    """Normalize a machine field without regex or prose interpretation."""

    raw = str(value or "").strip().lower()
    normalized = "".join(character if character.isalnum() or character in "_.:-" else "_" for character in raw)
    return "_".join(part for part in normalized.split("_") if part) or "unknown"


def _canonical_failure_attribution(projection: Mapping[str, Any]) -> tuple[str, str, str]:
    """Map structured canonical fields to one stable taxonomy attribution."""

    runtime = _mapping_copy(projection.get("runtime"))
    runtime_error = str(runtime.get("error_code") or "").strip().lower()
    runtime_categories = {
        "event_wait_timeout": "event_wait_timeout",
        "runtime_v2_connection_failed": "event_wait_runtime_v2_connection_failed",
        "workspace_switch_failed": "workspace_switch_failed",
        "runtime_project_contamination": "runtime_project_contamination",
        "isolated_instance_start_failed": "isolated_instance_start_failed",
        "start_failed": "factory_start_failed",
    }
    if runtime_error in runtime_categories:
        return "runtime_environment", runtime_categories[runtime_error], runtime_error

    factory_failure = _mapping_copy(projection.get("factory_stage_failure"))
    if bool(factory_failure.get("authoritative")):
        stage = str(factory_failure.get("stage") or "").strip().lower()
        category_by_stage = {
            "pm_planning": "pm_contract",
            "chief_engineer_review": "chief_engineer_blueprint",
            "director_dispatch": "director_tool_execution",
            "quality_gate": "llm_output",
        }
        error_code = str(factory_failure.get("error_code") or "factory_stage_failed").strip()
        reason = error_code.rsplit(".", 1)[-1]
        detail = str(
            factory_failure.get("root_cause_hint")
            or factory_failure.get("detail")
            or factory_failure.get("failure_class")
            or error_code
        ).strip()
        return category_by_stage.get(stage, "control_plane"), reason, detail

    ledger = _mapping_copy(projection.get("ledger"))
    if ledger.get("source") != "run_ledger" or int(ledger.get("gate_count") or 0) <= 0:
        return "control_plane", "run_ledger_projection_missing", "canonical Run Ledger projection missing"

    lifecycle = _mapping_copy(projection.get("lifecycle"))
    if lifecycle and not bool(lifecycle.get("ok", True)):
        failure = _mapping_copy(lifecycle.get("latest_failure"))
        if not failure:
            unresolved = lifecycle.get("unresolved_by_task")
            if isinstance(unresolved, Mapping):
                failure = next(
                    (dict(item) for item in unresolved.values() if isinstance(item, Mapping)),
                    {},
                )
        reason = str(failure.get("failure_class") or lifecycle.get("failure_class") or "tool_lifecycle_failed")
        detail = str(failure.get("reason") or lifecycle.get("reason") or reason)
        return "control_plane", reason, detail
    if not bool(ledger.get("integrity_ok")):
        status = summarize_run_ledger_projection(ledger)
        return "control_plane", "run_ledger_integrity_failed", str(status.get("detail") or "")

    boundary = _mapping_copy(projection.get("boundary"))
    if not bool(boundary.get("authoritative")):
        return "control_plane", "task_boundary_verdict_missing", "canonical TaskBoundary verdict missing"
    if not bool(boundary.get("ok")):
        latest = _mapping_copy(boundary.get("latest"))
        failed = boundary.get("failed")
        if not latest and isinstance(failed, list) and failed and isinstance(failed[-1], Mapping):
            latest = dict(failed[-1])
        reason = str(latest.get("status") or latest.get("failure_class") or "task_boundary_failed")
        responsible_layer = str(latest.get("responsible_layer") or "task_boundary").strip().lower()
        category = (
            "control_plane" if responsible_layer in {"control_plane", "execution_control_plane"} else "task_boundary"
        )
        detail = ";".join(
            item
            for item in (
                f"failure_class={latest.get('failure_class')}" if latest.get("failure_class") else "",
                f"responsible_layer={latest.get('responsible_layer')}" if latest.get("responsible_layer") else "",
                str(latest.get("reason") or "").strip(),
            )
            if item
        )
        return category, reason, detail

    runtime = _mapping_copy(projection.get("runtime"))
    if not bool(runtime.get("authoritative")):
        return (
            "control_plane",
            "task_runtime_projection_not_authoritative",
            str(runtime.get("terminal_source") or "canonical TaskRuntime projection missing"),
        )
    if not bool(runtime.get("completed")):
        return (
            "control_plane",
            "task_runtime_not_completed",
            str(runtime.get("status") or "TaskRuntime terminal state missing"),
        )

    evidence_policy = _mapping_copy(ledger.get("evidence_policy"))
    missing_required = evidence_policy.get("missing_required_modalities")
    if (
        not evidence_policy
        or evidence_policy.get("integrity_ok") is not True
        or (isinstance(missing_required, list) and bool(missing_required))
    ):
        detail = ",".join(str(item) for item in missing_required or [])
        return "control_plane", "required_evidence_missing", detail
    failed_required = evidence_policy.get("failed_required_modalities")
    if evidence_policy.get("outcome_ok") is not True or (isinstance(failed_required, list) and bool(failed_required)):
        detail = ",".join(str(item) for item in failed_required or [])
        return "control_plane", "required_evidence_failed", detail

    qa = _mapping_copy(projection.get("qa"))
    if not bool(qa.get("authoritative")):
        return "control_plane", "qa_verdict_missing", "canonical QA verdict missing"
    if not bool(qa.get("ok")):
        return "llm_output", "qa_verdict_failed", str(qa.get("summary") or "canonical QA verdict failed")

    execution = _mapping_copy(projection.get("execution"))
    return (
        "control_plane",
        str(execution.get("reason_code") or "canonical_execution_failed"),
        str(execution.get("failure_class") or "canonical execution failed"),
    )


def _independent_gate_attribution(record: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """Classify independent measurements by explicit gate/check identifiers."""

    real_run_gate = _mapping_copy(record.get("real_run_gate"))
    if real_run_gate and not bool(real_run_gate.get("ok")):
        failed_requirement = _first_real_run_failure(real_run_gate) or "unknown"
        category_by_requirement = {
            "chain_terminal": "runtime_environment",
            "environment_prepared": "runtime_environment",
            "artifact_landed": "director_tool_execution",
        }
        return (
            category_by_requirement.get(failed_requirement, "target_project_baseline"),
            f"real_run_gate.{failed_requirement}",
            str(real_run_gate.get("summary") or ""),
        )

    llm_route_audit = _mapping_copy(record.get("llm_route_audit"))
    if llm_route_audit and not bool(llm_route_audit.get("ok")):
        return "llm_output", "llm_route_audit", str(llm_route_audit.get("summary") or "")

    if not bool(record.get("has_plan_doc")) or bool(record.get("wrong_product_suspect")):
        return "pm_contract", "missing_or_wrong_contract", ""
    if not bool(record.get("has_blueprint_doc")):
        return "chief_engineer_blueprint", "blueprint_artifact_missing", ""

    failed_checks = _check_failures(dict(record))
    if failed_checks:
        first_check = failed_checks[0]
        category = (
            "runtime_environment"
            if str(first_check.get("failure_category") or first_check.get("error_category") or "").strip()
            == "runtime_environment"
            else "target_project_baseline"
        )
        return category, str(first_check.get("check") or "check_failed"), str(first_check.get("detail") or "")

    for gate in _gate_failures(dict(record)):
        gate_name = str(gate.get("gate") or "")
        if gate_name in {
            "plan_artifact_present",
            "blueprint_artifact_present",
            "qa_verdict_artifact_present",
            "chain_clean",
            "integration_qa_passed",
        }:
            continue
        if gate_name != "canonical_execution":
            return "unknown", gate_name or "unclassified_failure", str(gate.get("detail") or "")
    return None


def _legacy_display_attribution(record: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Classify historical audit records for degraded display only.

    This compatibility path never handles records with a canonical projection
    and its result is explicitly non-authoritative. It can be removed after
    stored pre-projection bench reports age out.
    """

    evidence: list[str] = []
    combined = json.dumps(record, ensure_ascii=False, default=str)
    run_ledger_gate_failed = any(
        gate.get("gate") in {"run_ledger_projection", "run_ledger_event"} and not gate.get("ok")
        for gate in _gate_failures(record)
    )
    if _record_has_model_provider_failure(record):
        category, reason = "runtime_environment", _model_provider_failure_reason(record)
        evidence.append(_model_provider_failure_evidence(record))
    elif _record_has_runtime_environment_failure(record):
        category, reason = "runtime_environment", _runtime_environment_failure_reason(record)
        evidence.append(_runtime_environment_failure_evidence(record))
    elif run_ledger_gate_failed:
        category, reason = "control_plane", "run_ledger_projection_missing"
        evidence.append("run ledger projection missing")
    elif _contains_context_budget_signal(combined):
        category, reason = "context_budget", "context_or_token_budget"
    elif _record_has_chief_engineer_blueprint_failure(record):
        category, reason = "chief_engineer_blueprint", _chief_engineer_failure_reason(record)
        evidence.append(_chief_engineer_failure_evidence(record))
    elif isinstance(record.get("llm_route_audit"), dict) and not record["llm_route_audit"].get("ok"):
        category, reason = "llm_output", "llm_route_audit"
        evidence.append(str(record["llm_route_audit"].get("summary") or ""))
    elif (repair_attribution := _record_repair_convergence_attribution(record)) is not None:
        category, reason, detail = repair_attribution
        evidence.append(detail)
    elif _record_has_qa_artifact_quality_failure(record):
        real_run_gate = _mapping_copy(record.get("real_run_gate"))
        failed_requirement = _first_real_run_failure(real_run_gate)
        category, reason = "llm_output", f"real_run_gate.{failed_requirement or 'generated_artifact_quality'}"
        evidence.append(str(real_run_gate.get("summary") or ""))
    elif _record_has_explicit_director_execution_failure(record) or _record_has_director_execution_failure(record):
        category, reason = "director_tool_execution", _director_failure_reason(record)
        evidence.append(_director_failure_evidence(record))
    elif isinstance(record.get("real_run_gate"), dict) and not record["real_run_gate"].get("ok"):
        real_run_gate = record["real_run_gate"]
        failed_requirement = _first_real_run_failure(real_run_gate)
        reason = f"real_run_gate.{failed_requirement or 'unknown'}"
        if failed_requirement == "chain_terminal":
            category = "runtime_environment"
        elif failed_requirement == "artifact_landed":
            category = "director_tool_execution"
        elif failed_requirement == "environment_prepared" and _record_has_generated_artifact_failure(record):
            category = "llm_output"
        elif failed_requirement == "environment_prepared":
            category = "runtime_environment"
        elif _record_has_generated_artifact_failure(record):
            category = "llm_output"
        else:
            category = "target_project_baseline"
        evidence.append(str(real_run_gate.get("summary") or ""))
    elif any(gate.get("gate") == "integration_qa_passed" and not gate.get("ok") for gate in _gate_failures(record)):
        category, reason = "llm_output", "integration_qa_failed"
        chain_results = _mapping_copy(record.get("chain_results"))
        evidence.append(str(chain_results.get("qa_reason") or ""))
    elif not record.get("has_plan_doc") or record.get("wrong_product_suspect"):
        category, reason = "pm_contract", "missing_or_wrong_contract"
    elif _check_failures(record):
        first_check = _check_failures(record)[0]
        reason = str(first_check.get("check") or "check_failed")
        category = (
            "runtime_environment" if _check_failure_is_runtime_environment(first_check) else "target_project_baseline"
        )
    else:
        failed_gates = _gate_failures(record)
        category = "unknown"
        reason = str(failed_gates[0].get("gate") if failed_gates else "unclassified_failure")
    return category, reason, [item for item in evidence if item]


def classify_factory_bench_failure(record: dict[str, Any]) -> dict[str, Any]:
    """Assign one stable root-cause category to a per-project bench record."""
    if record.get("all_checks_passed"):
        canonical = _mapping_copy(record.get("canonical_projection"))
        execution = _mapping_copy(canonical.get("execution"))
        authoritative_pass = (
            canonical.get("source") == CANONICAL_BENCH_PROJECTION_SOURCE
            and canonical.get("authoritative") is True
            and execution.get("ok") is True
        )
        if authoritative_pass:
            return {
                "ok": True,
                "source": CANONICAL_BENCH_PROJECTION_SOURCE,
                "authoritative": True,
                "degraded": False,
                "category": "",
                "root_cause_signature": "pass",
                "reasons": [],
                "evidence": [],
            }

    evidence: list[str] = []
    reasons: list[str] = []
    canonical = _mapping_copy(record.get("canonical_projection"))
    has_canonical_input = canonical.get("source") == CANONICAL_BENCH_PROJECTION_SOURCE
    if not has_canonical_input:
        raw_ledger = _mapping_copy(record.get("run_ledger_projection"))
        has_canonical_input = raw_ledger.get("source") == "run_ledger"
        canonical = build_canonical_bench_projection(record)
    execution = _mapping_copy(canonical.get("execution"))
    independent = _independent_gate_attribution(record)
    taxonomy_source = CANONICAL_BENCH_PROJECTION_SOURCE if has_canonical_input else LEGACY_BENCH_ARTIFACT_SOURCE
    if not has_canonical_input:
        category, reason, evidence = _legacy_display_attribution(record)
    elif not bool(execution.get("ok")):
        category, reason, detail = _canonical_failure_attribution(canonical)
        if detail:
            evidence.append(detail)
    elif independent is not None:
        category, reason, detail = independent
        if detail:
            evidence.append(detail)
    else:
        category, reason = "unknown", "unclassified_failure"

    for gate in _gate_failures(record):
        reasons.append(f"gate:{gate.get('gate')}={gate.get('detail')}")
    for check in _check_failures(record):
        reasons.append(f"check:{check.get('check')}={check.get('detail')}")
    return {
        "ok": False,
        "source": taxonomy_source,
        "authoritative": has_canonical_input,
        "degraded": not has_canonical_input,
        "category": category,
        "root_cause_signature": f"{category if category in _FAILURE_CATEGORIES else 'unknown'}:{_stable_reason(reason)}",
        "reasons": reasons,
        "evidence": [item for item in evidence if item],
    }


def apply_factory_bench_failure_taxonomy(record: dict[str, Any]) -> dict[str, Any]:
    """Classify a bench record and expose stable top-level attribution fields."""
    raw_ledger = _mapping_copy(record.get("run_ledger_projection"))
    if not isinstance(record.get("canonical_projection"), Mapping) and raw_ledger.get("source") == "run_ledger":
        record["canonical_projection"] = build_canonical_bench_projection(record)
    taxonomy = classify_factory_bench_failure(record)
    record["failure_taxonomy"] = taxonomy
    record["failure_category"] = str(taxonomy.get("category") or "")
    record["root_cause_signature"] = str(taxonomy.get("root_cause_signature") or "")
    reasons = taxonomy.get("reasons")
    evidence = taxonomy.get("evidence")
    record["failure_reasons"] = list(reasons) if isinstance(reasons, list) else []
    record["failure_evidence"] = list(evidence) if isinstance(evidence, list) else []
    # OpenCode external audits are a main-Agent-only collaboration mechanism.
    # They must never become Polaris/Factory machine-readable platform state.
    record.pop("opencode_audit", None)
    record["goal_audit"] = aggregate_goal_audit([record])
    return taxonomy


def aggregate_goal_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    real_passed = sum(
        1 for record in records if isinstance(record.get("real_run_gate"), dict) and record["real_run_gate"].get("ok")
    )
    ledger_projected = sum(1 for record in records if _run_ledger_projection_integrity_available(record))
    route_passed = sum(
        1
        for record in records
        if isinstance(record.get("llm_route_audit"), dict) and record["llm_route_audit"].get("ok")
    )
    categories: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    for record in records:
        taxonomy = record.get("failure_taxonomy")
        if not isinstance(taxonomy, dict) or taxonomy.get("ok"):
            continue
        category = str(taxonomy.get("category") or "unknown")
        signature = str(taxonomy.get("root_cause_signature") or f"{category}:unknown")
        categories[category] += 1
        signatures[signature] += 1
    return {
        "total": total,
        "real_run_gate": {"passed": real_passed, "total": total},
        "run_ledger": {"projected": ledger_projected, "total": total, "missing": total - ledger_projected},
        "llm_route_audit": {"passed": route_passed, "total": total},
        "failure_categories": dict(sorted(categories.items())),
        "root_cause_signatures": dict(sorted(signatures.items())),
    }
