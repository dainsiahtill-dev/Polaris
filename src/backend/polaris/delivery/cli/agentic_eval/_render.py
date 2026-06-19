"""Human / JSON terminal rendering for agentic-eval.

Owns the progress bar + streaming progress callback, the audit-package
human renderer, the baseline-pull renderer, and the matrix-suite report
renderers (which also persist their own report artifacts).
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Mapping
from typing import Any

from ._coerce import (
    _as_dict,
    _as_list,
    _format_counter,
    _to_int,
    _to_percent,
    _truncate_text,
)
from ._persistence import _persist_runtime_json

__all__ = [
    "_build_progress_callback",
    "_print_baseline_pull_human",
    "_print_human",
    "_render_progress_bar",
    "_report_context_projection_matrix",
    "_report_projection_adaptive_matrix",
    "_report_speculation_matrix",
]


def _render_progress_bar(completed: int, total: int, *, width: int = 24) -> str:
    total_value = max(0, int(total))
    completed_value = max(0, min(int(completed), total_value))
    if total_value <= 0:
        return "[" + ("-" * width) + "]"
    filled = round((completed_value / total_value) * width)
    filled = max(0, min(filled, width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _build_progress_callback(*, enabled: bool) -> Any:
    if not enabled:
        return None

    state: dict[str, Any] = {
        "suite": "",
        "run_id": "",
        "total_cases": 0,
        "completed_cases": 0,
        "started_at": 0.0,
    }

    def emit(event: Mapping[str, Any]) -> None:
        event_payload = dict(event or {})
        event_type = str(event_payload.get("type") or "").strip().lower()
        if not event_type:
            return
        if event_type == "suite_started":
            state["suite"] = str(event_payload.get("suite") or "").strip()
            state["run_id"] = str(event_payload.get("run_id") or "").strip()
            state["total_cases"] = _to_int(event_payload.get("total_cases"), 0)
            state["completed_cases"] = 0
            state["started_at"] = time.perf_counter()
            print(
                "[agentic-eval] "
                f"progress {_render_progress_bar(0, state['total_cases'])} "
                f"start suite={state['suite']} run_id={state['run_id']} total={state['total_cases']}",
                file=sys.stderr,
                flush=True,
            )
            return
        if event_type == "case_started":
            total = _to_int(event_payload.get("total_cases"), _to_int(state.get("total_cases"), 0))
            index = _to_int(event_payload.get("index"), 0)
            bar = _render_progress_bar(max(0, index - 1), total)
            level = str(event_payload.get("level") or "").strip()
            level_prefix = f"{level} " if level else ""
            print(
                "[agentic-eval] "
                f"progress {bar} case {index}/{max(total, 1)} "
                f"{event_payload.get('case_id')} :: {level_prefix}{event_payload.get('title')}",
                file=sys.stderr,
                flush=True,
            )
            return
        if event_type == "phase_started":
            phase = str(event_payload.get("phase") or "").strip()
            if not phase:
                return
            print(
                f"[agentic-eval] phase={phase} case={event_payload.get('case_id')} title={event_payload.get('title')}",
                file=sys.stderr,
                flush=True,
            )
            return
        if event_type == "case_completed":
            total = _to_int(event_payload.get("total_cases"), _to_int(state.get("total_cases"), 0))
            index = _to_int(event_payload.get("index"), 0)
            state["completed_cases"] = max(_to_int(state.get("completed_cases"), 0), index)
            bar = _render_progress_bar(index, total)
            status = "PASS" if bool(event_payload.get("passed")) else "FAIL"
            started_at = float(state.get("started_at") or 0.0)
            elapsed_s = max(0.0, time.perf_counter() - started_at) if started_at > 0.0 else 0.0
            print(
                "[agentic-eval] "
                f"progress {bar} done {index}/{max(total, 1)} "
                f"{event_payload.get('case_id')} status={status} "
                f"score={_to_percent(event_payload.get('score'))} "
                f"duration_ms={_to_int(event_payload.get('duration_ms'), 0)} "
                f"elapsed_s={round(elapsed_s, 1)}",
                file=sys.stderr,
                flush=True,
            )
            return
        if event_type == "suite_completed":
            total = _to_int(event_payload.get("total_cases"), _to_int(state.get("total_cases"), 0))
            started_at = float(state.get("started_at") or 0.0)
            elapsed_s = max(0.0, time.perf_counter() - started_at) if started_at > 0.0 else 0.0
            print(
                "[agentic-eval] "
                f"progress {_render_progress_bar(total, total)} "
                f"complete suite={event_payload.get('suite')} "
                f"passed={event_payload.get('passed_cases')}/{total} "
                f"failed={event_payload.get('failed_cases')} "
                f"artifact={event_payload.get('artifact_path')} "
                f"elapsed_s={round(elapsed_s, 1)}",
                file=sys.stderr,
                flush=True,
            )

    return emit


def _print_human(payload: Mapping[str, Any]) -> None:
    score = _as_dict(payload.get("score"))
    benchmark = _as_dict(payload.get("benchmark"))
    failures = _as_list(payload.get("failures"))
    tool_audit = _as_dict(payload.get("tool_audit"))
    repair_plan = _as_list(payload.get("repair_plan"))
    evidence_paths = _as_dict(payload.get("evidence_paths"))
    comparison = _as_dict(payload.get("comparison"))

    print(
        "[agentic-eval] "
        f"status={payload.get('status')} "
        f"score={score.get('overall_percent')} "
        f"passed={score.get('passed_cases')}/{score.get('total_cases')}"
    )
    print(
        "[agentic-eval] "
        f"run_id={benchmark.get('run_id')} role={benchmark.get('role_scope')} "
        f"provider={benchmark.get('provider_id')} model={benchmark.get('model')} "
        f"transport={benchmark.get('transport_mode')}"
    )
    print(
        "[agentic-eval] "
        f"tool_calls={tool_audit.get('total_calls')} critical_failures={tool_audit.get('critical_failures')}"
    )
    benchmark_artifact = str(evidence_paths.get("benchmark_artifact") or "").strip()
    if benchmark_artifact:
        print(f"[agentic-eval] benchmark_artifact={benchmark_artifact}")

    # Print rerun information if applicable
    rerun_info = _as_dict(payload.get("rerun_info"))
    if rerun_info:
        prev_path = str(rerun_info.get("previous_audit_path") or "").strip()
        prev_failed = _to_int(rerun_info.get("previous_failed_count"), 0)
        prev_passed = _to_int(rerun_info.get("previous_passed_count"), 0)
        prev_total = _to_int(rerun_info.get("previous_total_count"), 0)
        if prev_path:
            print(f"[agentic-eval] rerun_from={prev_path}")
        if prev_total > 0:
            print(
                f"[agentic-eval] previous_run passed={prev_passed}/{prev_total} failed={prev_failed} "
                f"score={rerun_info.get('previous_score')}"
            )

    if failures:
        print("[agentic-eval] top_failures:")
        for item in failures[:5]:
            root = _as_dict(_as_dict(item).get("root_cause"))
            print(f"  - {item.get('case_id')} [{root.get('category')}/{root.get('code')}] {root.get('message')}")
        print("[agentic-eval] failure_diagnostics:")
        for item in failures[:5]:
            diagnosis = _as_dict(_as_dict(item).get("diagnosis"))
            observed_trace = _as_dict(_as_dict(item).get("observed_trace"))
            evidence = _as_dict(_as_dict(item).get("evidence"))
            failed_checks = _as_list(item.get("failed_checks"))
            print(
                "  - "
                f"{item.get('case_id')} role={item.get('role')} "
                f"score={item.get('score_percent')}/{item.get('threshold_percent')} "
                f"sandbox={evidence.get('sandbox_workspace')}"
            )
            print(f"    title={item.get('title')}")
            print(
                "    observed_tools="
                + (
                    ",".join(
                        str(tool).strip() for tool in _as_list(observed_trace.get("tool_names")) if str(tool).strip()
                    )
                    or "none"
                )
                + f" tool_calls={observed_trace.get('tool_call_count')} "
                + f"raw_events={observed_trace.get('event_count')}"
            )
            event_types = _format_counter(_as_dict(observed_trace.get("event_type_histogram")))
            if event_types:
                print(f"    event_types={event_types}")
            transport_observations = _as_dict(observed_trace.get("transport_observations"))
            if transport_observations:
                for mode in ("stream", "non_stream"):
                    mode_trace = _as_dict(transport_observations.get(mode))
                    if not mode_trace:
                        continue
                    mode_tools = (
                        ",".join(
                            str(tool).strip() for tool in _as_list(mode_trace.get("tool_names")) if str(tool).strip()
                        )
                        or "none"
                    )
                    mode_error = str(mode_trace.get("error") or "").strip() or "none"
                    print(
                        "    "
                        f"{mode}_trace tools={mode_tools} "
                        f"tool_calls={mode_trace.get('tool_call_count')} "
                        f"duration_ms={mode_trace.get('duration_ms')} "
                        f"error={mode_error}"
                    )
            markers = _as_list(diagnosis.get("textual_tool_protocol_markers"))
            if markers:
                print(
                    "    textual_markers=" + ",".join(str(marker).strip() for marker in markers if str(marker).strip())
                )
            missing_tools = _as_list(diagnosis.get("missing_required_tools"))
            if missing_tools:
                print(
                    "    missing_required_tools="
                    + ",".join(str(tool).strip() for tool in missing_tools if str(tool).strip())
                )
            missing_output = _as_list(diagnosis.get("missing_output_tokens"))
            if missing_output:
                print(
                    "    missing_output_tokens="
                    + ",".join(str(token).strip() for token in missing_output if str(token).strip())
                )
            failed_validators = _as_list(diagnosis.get("failed_validators"))
            if failed_validators:
                print(
                    "    failed_validators="
                    + ",".join(str(name).strip() for name in failed_validators if str(name).strip())
                )
            transport_errors = _as_dict(diagnosis.get("transport_errors"))
            if transport_errors:
                print(
                    "    transport_errors="
                    + ", ".join(
                        f"{mode}:{_truncate_text(message, limit=120)}" for mode, message in transport_errors.items()
                    )
                )
            if failed_checks:
                print(
                    "    failed_checks="
                    + ", ".join(str(_as_dict(check).get("code") or "").strip() for check in failed_checks[:4])
                )
            output_preview = str(observed_trace.get("output_preview") or "").strip()
            if output_preview:
                print(f"    output_preview={output_preview}")

    if repair_plan:
        print("[agentic-eval] repair_plan:")
        for item in repair_plan:
            print(f"  - {item.get('priority')} {item.get('action')}")

    if bool(comparison.get("enabled")):
        current = _as_dict(comparison.get("current"))
        baseline = _as_dict(comparison.get("baseline"))
        delta = _as_dict(comparison.get("delta"))
        cases = _as_dict(comparison.get("cases"))
        print(
            "[agentic-eval] "
            f"baseline_compare trend={comparison.get('trend')} "
            f"ref={comparison.get('baseline_ref')} "
            f"delta_score={delta.get('overall_percent')} "
            f"delta_failed_cases={delta.get('failed_cases')}"
        )
        print(
            "  "
            f"current(run_id={current.get('run_id')} status={current.get('status')} score={current.get('overall_percent')}) "
            f"vs baseline(run_id={baseline.get('run_id')} status={baseline.get('status')} score={baseline.get('overall_percent')})"
        )
        new_failures = _as_list(cases.get("new_failures"))
        if new_failures:
            print("  new_failures=" + ", ".join(str(item) for item in new_failures))
        resolved_failures = _as_list(cases.get("resolved_failures"))
        if resolved_failures:
            print("  resolved_failures=" + ", ".join(str(item) for item in resolved_failures))

    audit_path = str(evidence_paths.get("audit_package") or "").strip()
    if audit_path:
        print(f"[agentic-eval] audit_package={audit_path}")


def _print_baseline_pull_human(payload: Mapping[str, Any]) -> None:
    sources = _as_list(payload.get("source_results"))
    unknown = _as_list(payload.get("unknown_sources"))
    mode_tokens: list[str] = []
    if bool(payload.get("check_only")):
        mode_tokens.append("cache_check")
    if bool(payload.get("refresh_cache")):
        mode_tokens.append("refresh")
    if bool(payload.get("use_cache")) and not bool(payload.get("refresh_cache")):
        mode_tokens.append("cache_enabled")
    mode_label = ",".join(mode_tokens) if mode_tokens else "standard"
    print(
        "[agentic-eval] "
        f"baseline_pull status={'PASS' if bool(payload.get('ok')) else 'FAIL'} "
        f"sources={len(sources)} unknown={len(unknown)} mode={mode_label}"
    )
    print(f"[agentic-eval] pull_id={payload.get('pull_id')} output={payload.get('output_root')}")
    cache_root = str(payload.get("cache_root") or "").strip()
    if cache_root:
        print(f"[agentic-eval] baseline_cache_root={cache_root}")
    if unknown:
        print("[agentic-eval] unknown_sources:")
        for token in unknown:
            print(f"  - {token}")
    for source in sources:
        row = _as_dict(source)
        print(
            "[agentic-eval] "
            f"source={row.get('source')} status={row.get('status')} "
            f"downloaded={row.get('downloaded_count')} failed={row.get('failed_count')} "
            f"cache_hits={row.get('cache_hits')} cache_misses={row.get('cache_misses')} "
            f"network_downloads={row.get('network_downloads')}"
        )
        manifest_path = str(row.get("manifest_path") or "").strip()
        if manifest_path:
            print(f"  manifest={manifest_path}")
    manifest_path = str(payload.get("manifest_path") or "").strip()
    if manifest_path:
        print(f"[agentic-eval] baseline_manifest={manifest_path}")


def _report_projection_adaptive_matrix(run_result: dict[str, Any], *, output_format: str) -> int:
    """呈现自适应排序 A/B（ON vs OFF）结果。测量性套件，退出码 0 当且仅当 ok。"""
    details = _as_dict(run_result.get("details"))
    summary = _as_dict(details.get("summary"))
    cases_raw = details.get("cases")
    cases: list[Any] = cases_raw if isinstance(cases_raw, list) else []
    ok = bool(run_result.get("ok"))

    if output_format == "json":
        print(json.dumps({"status": "PASS" if ok else "FAIL", **run_result}, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print(f"\n=== projection_adaptive_matrix (A/B: adaptive ON vs OFF) {'OK' if ok else 'FAIL'} ===")
    if run_result.get("error"):
        print(f"error: {run_result['error']}")
    print(
        "cases={total_cases} repeats={repeats} helped={adaptive_helped} hurt={adaptive_hurt} "
        "tie={tie} inconclusive={inconclusive} mean_delta(on-off)={mean_delta_on_minus_off} "
        "(on={mean_score_on} off={mean_score_off})".format(
            total_cases=summary.get("total_cases", 0),
            repeats=summary.get("repeats", 1),
            adaptive_helped=summary.get("adaptive_helped", 0),
            adaptive_hurt=summary.get("adaptive_hurt", 0),
            tie=summary.get("tie", 0),
            inconclusive=summary.get("inconclusive", 0),
            mean_delta_on_minus_off=summary.get("mean_delta_on_minus_off", 0.0),
            mean_score_on=summary.get("mean_score_on", 0.0),
            mean_score_off=summary.get("mean_score_off", 0.0),
        )
    )
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        ci = case.get("ci95")
        ci_str = f" ±{ci}" if ci else ""
        print(
            "  [{v}] {cid}: on={on} off={off} delta={d}{ci}".format(
                v=case.get("verdict"),
                cid=case.get("case_id"),
                on=case.get("score_on"),
                off=case.get("score_off"),
                d=case.get("delta"),
                ci=ci_str,
            )
        )
    return 0 if ok else 1


def _report_context_projection_matrix(run_result: dict[str, Any], *, output_format: str) -> int:
    """呈现 context_projection_matrix 确定性结果。退出码 0 当且仅当 ok。"""
    ok = bool(run_result.get("ok"))
    details = _as_dict(run_result.get("details"))
    summary = _as_dict(details.get("summary"))
    cases_raw = details.get("cases")
    cases: list[Any] = cases_raw if isinstance(cases_raw, list) else []

    if output_format == "json":
        print(json.dumps({"status": "PASS" if ok else "FAIL", **run_result}, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print(f"\n=== context_projection_matrix {'PASS' if ok else 'FAIL'} ===")
    print(
        "cases={total_cases} passed={passed_cases} failed={failed_cases} "
        "control_plane_leaks={control_plane_leaks_total} receipt_chars_saved={receipt_chars_saved_total} "
        "adaptive_affects_prompt={adaptive_affects_prompt}".format(
            total_cases=summary.get("total_cases", 0),
            passed_cases=summary.get("passed_cases", 0),
            failed_cases=summary.get("failed_cases", 0),
            control_plane_leaks_total=summary.get("control_plane_leaks_total", 0),
            receipt_chars_saved_total=summary.get("receipt_chars_saved_total", 0),
            adaptive_affects_prompt=summary.get("adaptive_affects_prompt", 0),
        )
    )
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        flag = "PASS" if case.get("passed") else "FAIL"
        print(f"  [{flag}] {case.get('case')}")
        for check in case.get("checks", []) if isinstance(case.get("checks"), list) else []:
            if isinstance(check, Mapping) and not check.get("ok"):
                print(f"       x {check.get('name')}: {check.get('detail')}")
    return 0 if ok else 1


def _report_speculation_matrix(
    run_result: dict[str, Any],
    *,
    workspace: str,
    output_format: str,
) -> int:
    """呈现 speculation_matrix 差分评测结果并落盘.

    退出码 0 当且仅当 ``ok``（无 wrong_adoption 且无 ON 致命错误）。
    """
    ok = bool(run_result.get("ok"))
    details = _as_dict(run_result.get("details"))
    summary = _as_dict(details.get("summary"))
    cases_raw = details.get("cases")
    cases: list[Any] = cases_raw if isinstance(cases_raw, list) else []
    run_id = str(details.get("run_id") or "").strip()

    # 落盘报告。
    artifact_path = ""
    if run_id:
        try:
            persisted = _persist_runtime_json(
                workspace=workspace,
                output_path=f"runtime/llm_evaluations/{run_id}/SPECULATION_MATRIX_REPORT.json",
                payload=run_result,
            )
            artifact_path = persisted["absolute_path"]
        except (RuntimeError, ValueError, OSError):
            artifact_path = ""

    if output_format == "json":
        print(
            json.dumps(
                {"status": "PASS" if ok else "FAIL", **run_result, "artifact_path": artifact_path},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if ok else 1

    # human
    print(f"\n=== speculation_matrix {'PASS' if ok else 'FAIL'} (run_id={run_id}) ===")
    if run_result.get("error"):
        print(f"error: {run_result['error']}")
    print(
        "cases={total_cases} passed={passed_cases} failed={failed_cases} "
        "active={speculation_active_cases} regressed={regressed} early_stopped={early_stopped}".format(
            total_cases=summary.get("total_cases", 0),
            passed_cases=summary.get("passed_cases", 0),
            failed_cases=summary.get("failed_cases", 0),
            speculation_active_cases=summary.get("speculation_active_cases", 0),
            regressed=summary.get("speculation_regressed_cases", 0),
            early_stopped=summary.get("early_stopped", False),
        )
    )
    print(
        "adopted={adopted_total} joined={joined_total} replayed={replayed_total} "
        "hit_rate={hit_rate} saved_ms_total={saved_ms_total} wrong_adoption={wrong_adoption_total}".format(
            adopted_total=summary.get("adopted_total", 0),
            joined_total=summary.get("joined_total", 0),
            replayed_total=summary.get("replayed_total", 0),
            hit_rate=summary.get("hit_rate", 0.0),
            saved_ms_total=summary.get("saved_ms_total", 0),
            wrong_adoption_total=summary.get("wrong_adoption_total", 0),
        )
    )
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        flag = "PASS" if case.get("passed") else "FAIL"
        print(
            "  [{flag}] {cid}: adopted={a} joined={j} replayed={r} hit={hr} "
            "saved_ms={sm} wrong={w} dt_on={don}ms dt_off={doff}ms{err}".format(
                flag=flag,
                cid=case.get("case_id"),
                a=case.get("adopted"),
                j=case.get("joined"),
                r=case.get("replayed"),
                hr=case.get("hit_rate"),
                sm=case.get("saved_ms_total"),
                w=case.get("wrong_adoption"),
                don=case.get("duration_ms_on"),
                doff=case.get("duration_ms_off"),
                err=f" on_error={case.get('on_error')}" if case.get("on_error") else "",
            )
        )
    if artifact_path:
        print(f"report: {artifact_path}")
    return 0 if ok else 1
