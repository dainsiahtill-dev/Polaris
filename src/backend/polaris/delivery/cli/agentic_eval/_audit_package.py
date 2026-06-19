"""Audit-package builder, formatters and failure diagnosers for agentic-eval.

Transforms a raw benchmark ``run_result`` into the canonical
AGENTIC_EVAL_AUDIT.json structure: scored failures with root-cause
checks, observed tool/event traces, transport diagnosis and an
aggregated deterministic repair plan.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.kernelone.utils.time_utils import utc_now_iso

from ._coerce import (
    _CHECK_CATEGORY_ORDER,
    _as_dict,
    _as_list,
    _to_float,
    _to_int,
    _to_percent,
    _truncate_text,
)

__all__ = [
    "_build_failure_diagnosis",
    "_build_observed_trace",
    "_build_transport_observation",
    "_check_priority",
    "_default_output_path",
    "_event_type_histogram",
    "_extract_failed_checks",
    "_extract_textual_tool_markers",
    "_failure_priority",
    "_normalize_check_code",
    "_priority_label",
    "_repair_hint",
    "_resolve_runtime_role_bindings",
    "_summarize_raw_events",
    "_summarize_tool_calls",
    "build_agentic_eval_audit_package",
]


def _normalize_check_code(check_code: str) -> str:
    token = str(check_code or "").strip().lower()
    if token.startswith("stream:") or token.startswith("non_stream:"):
        _, remainder = token.split(":", 1)
        return remainder
    return token


def _repair_hint(check_code: str, category: str) -> str:
    code = _normalize_check_code(check_code)
    cat = str(category or "").strip().lower()

    if code.startswith("parity:"):
        return "Align stream/non-stream execution policy so both transports emit equivalent tool traces."
    if code.startswith("required_tool:"):
        tool = code.split(":", 1)[-1]
        return f"Enforce required tool `{tool}` in role policy and add trace assertion in regression suite."
    if code.startswith("required_tool_argument:"):
        return "Pin required file/path evidence in tool args before final answer."
    if code in {"min_tool_calls", "max_tool_calls"}:
        return "Tune tool-call loop policy to keep calls within benchmark bounds."
    if code == "textual_tool_protocol_without_trace":
        return (
            "Provider/runtime is emitting textual pseudo-tool calls instead of native tool traces; "
            "verify tool schema binding, provider tool support, and suppress `[TOOL_CALL]` wrappers."
        )
    if code.startswith("forbidden_tool:") or code.startswith("forbidden_tool_argument:"):
        return "Tighten tool/path allowlist and block unsafe write scopes at policy layer."
    if code.startswith("required_output:"):
        return "Harden output contract template so required fields/tokens are always emitted."
    if code.startswith("forbidden_output:"):
        return "Add output sanitizer to strip forbidden markers before response emission."
    if code == "validator:no_prompt_leakage":
        return "Prevent prompt leakage by filtering system/thinking/tool tags in final output."
    if code == "validator:pm_plan_json":
        return "Enforce strict PM JSON schema (`goal`, `backlog`, `timeline`) and schema-validate before return."
    if code == "validator:qa_passfail_json":
        return "Enforce strict QA JSON schema (`passed`, `findings`) and reject free-form text."
    if code == "validator:director_safe_scope":
        return "Constrain Director plans to allowed paths and include explicit verification step."
    if code == "validator:no_hallucinated_paths":
        return "Require path mentions to come from actual workspace file listing."
    if code == "validator:structured_steps":
        return "Use numbered step output template to guarantee structured plans."

    if cat == "safety":
        return "Add safety guardrails for tool usage and output sanitization."
    if cat == "contract":
        return "Strengthen response schema validation in role post-processor."
    if cat == "tooling":
        return "Adjust tool invocation policy to satisfy deterministic benchmark expectations."
    if cat == "evidence":
        return "Require explicit local evidence references before verdict output."
    return "Review failed check and add deterministic policy + regression assertion."


def _check_priority(check: Mapping[str, Any]) -> tuple[int, int, str]:
    critical = 0 if bool(check.get("critical")) else 1
    category_rank = _CHECK_CATEGORY_ORDER.get(str(check.get("category") or "").strip().lower(), 9)
    code = str(check.get("code") or "")
    return critical, category_rank, code


def _failure_priority(check: Mapping[str, Any]) -> int:
    if bool(check.get("critical")):
        return 0
    category = str(check.get("category") or "").strip().lower()
    if category == "safety":
        return 0
    if category == "contract":
        return 1
    return 2


def _priority_label(priority: int) -> str:
    if priority <= 0:
        return "P0"
    if priority == 1:
        return "P1"
    return "P2"


def _default_output_path(run_id: str) -> str:
    token = str(run_id or "").strip() or datetime.now(timezone.utc).strftime("cli-%Y%m%d%H%M%S")
    return f"runtime/llm_evaluations/{token}/AGENTIC_EVAL_AUDIT.json"


def _extract_failed_checks(case_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    judge = _as_dict(case_payload.get("judge"))
    checks = _as_list(judge.get("checks"))
    failed: list[dict[str, Any]] = []
    for raw in checks:
        check = _as_dict(raw)
        if not check or bool(check.get("passed")):
            continue
        failed.append(
            {
                "code": str(check.get("code") or "").strip(),
                "category": str(check.get("category") or "").strip().lower(),
                "critical": bool(check.get("critical")),
                "message": str(check.get("message") or "").strip(),
                "evidence": _as_dict(check.get("evidence")),
            }
        )
    failed.sort(key=_check_priority)
    return failed


def _event_type_histogram(raw_events: Iterable[Any]) -> dict[str, int]:
    histogram: Counter[str] = Counter()
    for raw in raw_events:
        event = _as_dict(raw)
        event_type = str(event.get("type") or "").strip() or "unknown"
        histogram[event_type] += 1
    return dict(sorted(histogram.items()))


def _summarize_tool_calls(tool_calls: Iterable[Any], *, limit: int = 5) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for index, raw in enumerate(tool_calls):
        if index >= limit:
            break
        call = _as_dict(raw)
        args = _as_dict(call.get("args"))
        try:
            args_text = json.dumps(args, ensure_ascii=False, sort_keys=True)
        except (RuntimeError, ValueError, TypeError):
            args_text = str(args)
        summary.append(
            {
                "tool": str(call.get("tool") or "").strip(),
                "args_preview": _truncate_text(args_text, limit=180),
                "event_index": _to_int(call.get("event_index"), 0),
            }
        )
    return summary


def _summarize_raw_events(raw_events: Iterable[Any], *, limit: int = 8) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_events):
        if index >= limit:
            break
        event = _as_dict(raw)
        event_type = str(event.get("type") or "").strip() or "unknown"
        sample: dict[str, Any] = {"index": index, "type": event_type}
        if event_type in {"content_chunk", "thinking_chunk", "chunk", "reasoning_chunk"}:
            sample["content_preview"] = _truncate_text(event.get("content"), limit=160)
        if event_type == "tool_call":
            sample["tool"] = str(event.get("tool") or "").strip()
            sample["args_preview"] = _truncate_text(
                json.dumps(_as_dict(event.get("args")), ensure_ascii=False, sort_keys=True),
                limit=160,
            )
        if event_type == "error":
            sample["error"] = _truncate_text(event.get("error"), limit=160)
        if event_type == "fingerprint":
            fingerprint = {
                key: event.get(key)
                for key in ("profile_id", "profile_hash", "bundle_id", "bundle_version", "run_id", "turn_index")
                if event.get(key) not in (None, "")
            }
            if fingerprint:
                sample["fingerprint"] = fingerprint
        if len(sample) == 2:
            sample["keys"] = sorted(event.keys())
        samples.append(sample)
    return samples


def _extract_textual_tool_markers(
    *,
    failed_checks: Iterable[Mapping[str, Any]],
    observed: Mapping[str, Any],
) -> list[str]:
    for raw in failed_checks:
        check = _as_dict(raw)
        if str(check.get("code") or "").strip().lower() != "textual_tool_protocol_without_trace":
            continue
        evidence = _as_dict(check.get("evidence"))
        markers = [str(item).strip() for item in _as_list(evidence.get("markers")) if str(item).strip()]
        if markers:
            return markers
    combined_text = "\n".join(
        str(item or "").strip()
        for item in (observed.get("output"), observed.get("thinking"))
        if str(item or "").strip()
    )
    found_markers: list[str] = []
    for token in ("[TOOL_CALL]", "[/TOOL_CALL]", "<tool_call>", "</tool_call>"):
        if token.lower() in combined_text.lower():
            found_markers.append(token)
    return found_markers


def _build_failure_diagnosis(
    *,
    failed_checks: Iterable[Mapping[str, Any]],
    observed: Mapping[str, Any],
    stream_observed: Mapping[str, Any] | None = None,
    non_stream_observed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    missing_required_tools: list[str] = []
    forbidden_tools_triggered: list[str] = []
    missing_output_tokens: list[str] = []
    failed_validators: list[str] = []

    for raw in failed_checks:
        check = _as_dict(raw)
        code = str(check.get("code") or "").strip()
        if code.startswith("required_tool:"):
            missing_required_tools.append(code.split(":", 1)[-1])
        elif code.startswith("forbidden_tool:"):
            forbidden_tools_triggered.append(code.split(":", 1)[-1])
        elif code.startswith("required_output:"):
            missing_output_tokens.append(code.split(":", 1)[-1])
        elif code.startswith("validator:"):
            failed_validators.append(code.split(":", 1)[-1])

    stream_payload = _as_dict(stream_observed)
    non_stream_payload = _as_dict(non_stream_observed)
    transport_errors: dict[str, str] = {}
    transport_tool_counts: dict[str, int] = {}
    for mode, payload in (("stream", stream_payload), ("non_stream", non_stream_payload)):
        if not payload:
            continue
        transport_tool_counts[mode] = len(_as_list(payload.get("tool_calls")))
        error_text = str(payload.get("error") or "").strip()
        if error_text:
            transport_errors[mode] = error_text

    return {
        "missing_required_tools": missing_required_tools,
        "forbidden_tools_triggered": forbidden_tools_triggered,
        "missing_output_tokens": missing_output_tokens,
        "failed_validators": failed_validators,
        "textual_tool_protocol_markers": _extract_textual_tool_markers(
            failed_checks=failed_checks,
            observed=observed,
        ),
        "has_native_tool_trace": bool(_as_list(observed.get("tool_calls"))),
        "transport_errors": transport_errors,
        "transport_tool_counts": transport_tool_counts,
    }


def _build_transport_observation(
    *,
    observed: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload = _as_dict(observed)
    if not payload:
        return {}
    tool_calls = _as_list(payload.get("tool_calls"))
    return {
        "tool_call_count": len(tool_calls),
        "tool_names": [
            str(_as_dict(item).get("tool") or "").strip()
            for item in tool_calls
            if str(_as_dict(item).get("tool") or "").strip()
        ],
        "tool_calls_preview": _summarize_tool_calls(tool_calls),
        "output_preview": _truncate_text(payload.get("output"), limit=240),
        "thinking_preview": _truncate_text(payload.get("thinking"), limit=180),
        "error": str(payload.get("error") or "").strip(),
        "duration_ms": _to_int(payload.get("duration_ms"), 0),
        "event_count": _to_int(payload.get("event_count"), 0),
    }


def _build_observed_trace(
    *,
    observed: Mapping[str, Any],
    raw_events: Iterable[Any],
    workspace_files: Iterable[Any],
    stream_observed: Mapping[str, Any] | None = None,
    non_stream_observed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    tool_calls = _as_list(observed.get("tool_calls"))
    event_histogram = _event_type_histogram(raw_events)
    transport_observations: dict[str, dict[str, Any]] = {}
    stream_trace = _build_transport_observation(observed=stream_observed)
    non_stream_trace = _build_transport_observation(observed=non_stream_observed)
    if stream_trace:
        transport_observations["stream"] = stream_trace
    if non_stream_trace:
        transport_observations["non_stream"] = non_stream_trace
    return {
        "tool_call_count": len(tool_calls),
        "tool_names": [
            str(_as_dict(item).get("tool") or "").strip()
            for item in tool_calls
            if str(_as_dict(item).get("tool") or "").strip()
        ],
        "tool_calls_preview": _summarize_tool_calls(tool_calls),
        "output_preview": _truncate_text(observed.get("output"), limit=320),
        "thinking_preview": _truncate_text(observed.get("thinking"), limit=240),
        "error": str(observed.get("error") or "").strip(),
        "duration_ms": _to_int(observed.get("duration_ms"), 0),
        "event_count": _to_int(observed.get("event_count"), default=sum(event_histogram.values())),
        "event_type_histogram": event_histogram,
        "raw_event_samples": _summarize_raw_events(raw_events),
        "workspace_files_sample": [str(item).strip() for item in list(workspace_files)[:12] if str(item).strip()],
        "fingerprint": _as_dict(observed.get("fingerprint")),
        "transport_observations": transport_observations,
        "transport_modes": sorted(transport_observations.keys()),
    }


def _resolve_runtime_role_bindings() -> dict[str, dict[str, str]]:
    """Resolve the actual role→provider/model bindings for audit persistence.

    ADR-0090 W3.2: audits previously recorded only the "runtime_binding"
    placeholder, making historical score comparisons across models impossible.
    Failure to resolve must never break the audit — fail soft per role.
    """
    bindings: dict[str, dict[str, str]] = {}
    try:
        from polaris.kernelone.llm.runtime_config import load_role_config
    except ImportError:
        return bindings
    for role_id in ("pm", "architect", "chief_engineer", "director", "qa", "scout"):
        try:
            config = load_role_config(role_id)
        except (RuntimeError, ValueError, OSError):
            continue
        if config is None:
            continue
        bindings[role_id] = {
            "provider_id": str(config.provider_id or ""),
            "model": str(config.model or ""),
        }
    return bindings


def build_agentic_eval_audit_package(
    *,
    workspace: str,
    scope_role: str,
    provider_id: str,
    model: str,
    run_result: Mapping[str, Any],
    max_fixes: int,
) -> dict[str, Any]:
    details = _as_dict(run_result.get("details"))
    report = _as_dict(details.get("report"))
    report_summary = _as_dict(report.get("summary"))
    benchmark_cases = _as_list(report.get("cases"))

    total_cases = _to_int(report_summary.get("total_cases"), default=len(benchmark_cases))
    passed_cases = _to_int(
        report_summary.get("passed_cases"),
        default=sum(1 for case_payload in benchmark_cases if bool(_as_dict(case_payload.get("judge")).get("passed"))),
    )
    failed_cases = max(total_cases - passed_cases, 0)
    average_score = _to_float(
        report_summary.get("average_score"),
        default=(
            sum(_to_float(_as_dict(case_payload.get("judge")).get("score")) for case_payload in benchmark_cases)
            / total_cases
            if total_cases > 0
            else 0.0
        ),
    )
    pass_rate = (passed_cases / total_cases) if total_cases > 0 else 0.0

    failure_entries: list[dict[str, Any]] = []
    repair_index: dict[str, dict[str, Any]] = {}
    tool_histogram: Counter[str] = Counter()
    total_tool_calls = 0
    safety_violations: list[dict[str, Any]] = []
    critical_failures = 0

    for case_payload_raw in benchmark_cases:
        case_payload = _as_dict(case_payload_raw)
        case_meta = _as_dict(case_payload.get("case"))
        observed = _as_dict(case_payload.get("observed"))
        stream_observed = _as_dict(case_payload.get("stream_observed"))
        non_stream_observed = _as_dict(case_payload.get("non_stream_observed"))
        judge = _as_dict(case_payload.get("judge"))
        raw_events = _as_list(case_payload.get("raw_events"))
        workspace_files = _as_list(case_payload.get("workspace_files"))
        failed_checks = _extract_failed_checks(case_payload)

        for tool_call_raw in _as_list(observed.get("tool_calls")):
            tool_call = _as_dict(tool_call_raw)
            tool = str(tool_call.get("tool") or "").strip()
            if not tool:
                continue
            tool_histogram[tool] += 1
            total_tool_calls += 1

        for check in failed_checks:
            code = str(check.get("code") or "")
            category = str(check.get("category") or "")
            if bool(check.get("critical")):
                critical_failures += 1
            if category == "safety":
                safety_violations.append(
                    {
                        "case_id": str(case_meta.get("case_id") or ""),
                        "check_code": code,
                        "message": str(check.get("message") or ""),
                    }
                )
            hint = _repair_hint(code, category)
            bucket = repair_index.get(hint)
            if bucket is None:
                bucket = {
                    "action": hint,
                    "priority_rank": _failure_priority(check),
                    "case_ids": set(),
                    "check_codes": set(),
                }
                repair_index[hint] = bucket
            else:
                bucket["priority_rank"] = min(int(bucket["priority_rank"]), _failure_priority(check))
            bucket["case_ids"].add(str(case_meta.get("case_id") or "").strip())
            bucket["check_codes"].add(code)

        if not bool(judge.get("passed")):
            diagnosis = _build_failure_diagnosis(
                failed_checks=failed_checks,
                observed=observed,
                stream_observed=stream_observed,
                non_stream_observed=non_stream_observed,
            )
            observed_trace = _build_observed_trace(
                observed=observed,
                raw_events=raw_events,
                workspace_files=workspace_files,
                stream_observed=stream_observed,
                non_stream_observed=non_stream_observed,
            )
            failure_entries.append(
                {
                    "case_id": str(case_meta.get("case_id") or "").strip(),
                    "role": str(case_meta.get("role") or "").strip(),
                    "title": str(case_meta.get("title") or "").strip(),
                    "score_percent": _to_percent(judge.get("score")),
                    "threshold_percent": _to_percent(judge.get("threshold")),
                    "summary": str(judge.get("summary") or "").strip(),
                    "root_cause": failed_checks[0] if failed_checks else {},
                    "failed_checks": failed_checks,
                    "repair_suggestions": [_repair_hint(item["code"], item["category"]) for item in failed_checks[:3]],
                    "expected_contract": {
                        "prompt_preview": _truncate_text(case_meta.get("prompt"), limit=220),
                        "tags": [str(item).strip() for item in _as_list(case_meta.get("tags")) if str(item).strip()],
                        "judge": _as_dict(case_meta.get("judge")),
                    },
                    "observed_trace": observed_trace,
                    "diagnosis": diagnosis,
                    "evidence": {
                        "sandbox_workspace": str(case_payload.get("sandbox_workspace") or ""),
                        "raw_event_count": len(raw_events),
                        "raw_event_types": observed_trace.get("event_type_histogram"),
                        "tool_call_count": len(_as_list(observed.get("tool_calls"))),
                        "benchmark_artifact": str(details.get("artifact_path") or "").strip(),
                    },
                }
            )

    failure_entries.sort(
        key=lambda item: (
            _failure_priority(_as_dict(item.get("root_cause"))),
            str(item.get("case_id") or ""),
        )
    )

    repair_plan: list[dict[str, Any]] = []
    for entry in sorted(
        repair_index.values(),
        key=lambda item: (
            int(item.get("priority_rank", 2)),
            -len(item.get("case_ids", set())),
            str(item.get("action") or ""),
        ),
    )[: max(1, int(max_fixes))]:
        repair_plan.append(
            {
                "priority": _priority_label(int(entry.get("priority_rank", 2))),
                "action": str(entry.get("action") or ""),
                "case_ids": sorted(str(item) for item in entry.get("case_ids", set()) if str(item).strip()),
                "check_codes": sorted(str(item) for item in entry.get("check_codes", set()) if str(item).strip()),
            }
        )

    benchmark_run_id = str(report.get("test_run_id") or "").strip()
    benchmark_artifact_path = str(details.get("artifact_path") or "").strip()
    report_target = _as_dict(report.get("target"))
    status = "PASS" if bool(run_result.get("ok")) and failed_cases == 0 and total_cases > 0 else "FAIL"

    return {
        "status": status,
        "workspace": str(Path(workspace).resolve()),
        "generated_at": utc_now_iso(),
        "benchmark": {
            "suite": str(report.get("suite") or "agentic_benchmark"),
            "run_id": benchmark_run_id,
            "role_scope": str(scope_role or "").strip().lower() or "all",
            "provider_id": str(provider_id or "").strip() or "runtime_binding",
            "model": str(model or "").strip() or "runtime_binding",
            # ADR-0090 W3.2: persist the RESOLVED role bindings so cross-model
            # score comparisons are possible from the audit alone ("runtime_binding"
            # placeholders made historical audits unattributable to a model).
            "resolved_role_bindings": _resolve_runtime_role_bindings(),
            "transport_mode": str(report_target.get("transport_mode") or "").strip() or "stream",
        },
        "score": {
            "overall_percent": _to_percent(average_score),
            "average_score": round(average_score, 4),
            "pass_rate": round(pass_rate, 4),
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": failed_cases,
        },
        "failures": failure_entries,
        "tool_audit": {
            "total_calls": total_tool_calls,
            "by_tool": dict(sorted(tool_histogram.items())),
            "critical_failures": critical_failures,
            "safety_violations": safety_violations,
        },
        "repair_plan": repair_plan,
        "errors": [str(run_result.get("error") or "").strip()] if str(run_result.get("error") or "").strip() else [],
        "evidence_paths": {
            "benchmark_artifact": benchmark_artifact_path,
            "audit_package": "",
            "case_sandboxes": [
                str(_as_dict(item).get("sandbox_workspace") or "")
                for item in benchmark_cases
                if str(_as_dict(item).get("sandbox_workspace") or "").strip()
            ],
        },
    }
