"""Unit tests for `agentic_eval.py` helper functions.

These tests cover small, pure helpers that the main ``test_agentic_eval_cli.py``
file does not exercise directly. They exist to:

* lock in trend logic for ``_build_baseline_comparison`` (improved / regressed /
  mixed / unchanged)
* verify the probe / level-range / progress / repair-hint / dedup helpers
* give the build-time helpers a fast feedback loop without standing up the
  full LLM evaluation pipeline

All tests are pure (no LLM network), so they run quickly in unit-test mode.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any
from uuid import uuid4

from polaris.delivery.cli import agentic_eval

# ---------------------------------------------------------------------------
# Local helpers / fixtures
# ---------------------------------------------------------------------------


def _local_tmp_dir(label: str) -> Path:
    path = Path("tmp_pytest_agentic_eval_helpers_local") / f"{label}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_audit_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fake_audit_payload(*, run_id: str = "run-001") -> dict[str, Any]:
    return {
        "status": "FAIL",
        "benchmark": {"run_id": run_id, "suite": "agentic_benchmark"},
        "score": {
            "overall_percent": 60.0,
            "pass_rate": 0.5,
            "failed_cases": 1,
            "total_cases": 2,
            "passed_cases": 1,
        },
        "tool_audit": {"total_calls": 4},
        "failures": [
            {
                "case_id": "pm_task_contract",
                "failed_checks": [
                    {"code": "validator:pm_plan_json"},
                    {"code": "required_tool:read_file"},
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Type-coercion helpers
# ---------------------------------------------------------------------------


def test_to_int_returns_default_on_invalid_input() -> None:
    assert agentic_eval._to_int("not-a-number") == 0
    assert agentic_eval._to_int(None, default=7) == 7
    assert agentic_eval._to_int(3.7) == 3
    assert agentic_eval._to_int("42") == 42


def test_to_int_handles_non_numeric_strings() -> None:
    # Note: Python ints are arbitrary precision, so "9"*400 won't overflow.
    # What we want is for non-numeric strings to fall back to the default.
    assert agentic_eval._to_int("not-a-number", default=99) == 99
    assert agentic_eval._to_int(["a", "b"], default=99) == 99


def test_to_float_returns_default_on_invalid_input() -> None:
    assert agentic_eval._to_float("not-a-float", default=0.5) == 0.5
    assert agentic_eval._to_float(None) == 0.0
    assert agentic_eval._to_float("3.14") == 3.14


def test_to_percent_multiplies_by_100() -> None:
    assert agentic_eval._to_percent(0.5) == 50.0
    assert agentic_eval._to_percent(0.1234) == 12.34
    assert agentic_eval._to_percent("oops") == 0.0


def test_as_dict_and_as_list_handle_non_collections() -> None:
    assert agentic_eval._as_dict(None) == {}
    assert agentic_eval._as_dict("string") == {}
    assert agentic_eval._as_list(None) == []
    assert agentic_eval._as_list("string") == []
    assert agentic_eval._as_list([1, 2, 3]) == [1, 2, 3]


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def test_normalise_case_ids_dedupes_and_strips() -> None:
    result = agentic_eval._normalise_case_ids(["  a ", "b", "a", "", None, "c"])
    assert result == ["a", "b", "c"]


def test_normalise_case_ids_handles_none() -> None:
    assert agentic_eval._normalise_case_ids(None) == []


def test_normalize_tokens_lowercases_and_dedupes() -> None:
    result = agentic_eval._normalize_tokens([" BFCL ", "toolbench", "bfcl", ""])
    assert result == ["bfcl", "toolbench"]


def test_normalize_suite_name_falls_back_to_agentic_benchmark() -> None:
    assert agentic_eval._normalize_suite_name("agentic_benchmark") == "agentic_benchmark"
    assert agentic_eval._normalize_suite_name("tool_calling_matrix") == "tool_calling_matrix"
    assert agentic_eval._normalize_suite_name("  ") == "agentic_benchmark"
    assert agentic_eval._normalize_suite_name(None) == "agentic_benchmark"
    assert agentic_eval._normalize_suite_name("does-not-exist") == "agentic_benchmark"


def test_normalize_matrix_transport() -> None:
    assert agentic_eval._normalize_matrix_transport("stream") == "stream"
    assert agentic_eval._normalize_matrix_transport("non_stream") == "non_stream"
    assert agentic_eval._normalize_matrix_transport("NON_STREAM") == "non_stream"
    assert agentic_eval._normalize_matrix_transport("anything-else") == "stream"
    assert agentic_eval._normalize_matrix_transport(None) == "stream"


# ---------------------------------------------------------------------------
# Level-range helpers
# ---------------------------------------------------------------------------


def test_parse_level_range_supports_all_forms() -> None:
    assert agentic_eval._parse_level_range("l1") == {1}
    assert agentic_eval._parse_level_range("1") == {1}
    assert agentic_eval._parse_level_range("l1-l3") == {1, 2, 3}
    assert agentic_eval._parse_level_range("1-3") == {1, 2, 3}
    assert agentic_eval._parse_level_range("l1,l3,l5") == {1, 3, 5}
    assert agentic_eval._parse_level_range("l1-l3, l7") == {1, 2, 3, 7}
    assert agentic_eval._parse_level_range("") == set()
    assert agentic_eval._parse_level_range("garbage") == set()
    assert agentic_eval._parse_level_range("l0") == set()  # out of range
    assert agentic_eval._parse_level_range("l99") == set()  # out of range


def test_expand_level_range_to_case_ids() -> None:
    assert agentic_eval._expand_level_range_to_case_ids(None) == []
    assert agentic_eval._expand_level_range_to_case_ids([]) == []
    result = agentic_eval._expand_level_range_to_case_ids(["l1-l3", "l7"])
    assert result == ["l1_", "l2_", "l3_", "l7_"]
    # Unknown levels should be silently dropped
    result = agentic_eval._expand_level_range_to_case_ids(["l1", "l99"])
    assert result == ["l1_"]


# ---------------------------------------------------------------------------
# Text / counter helpers
# ---------------------------------------------------------------------------


def test_truncate_text_handles_short_and_long() -> None:
    assert agentic_eval._truncate_text("hi") == "hi"
    assert agentic_eval._truncate_text("a" * 100, limit=10).endswith("...")
    assert agentic_eval._truncate_text(None) == ""
    # Whitespace collapse
    assert agentic_eval._truncate_text("a\n\n  b\t\tc") == "a b c"


def test_format_counter_renders_pairs() -> None:
    assert agentic_eval._format_counter({"a": 2, "b": 1}) == "a:2, b:1"
    assert agentic_eval._format_counter({"": 1, "x": 3}) == "unknown:1, x:3"
    assert agentic_eval._format_counter({}) == ""


# ---------------------------------------------------------------------------
# Event / tool-call summarizers
# ---------------------------------------------------------------------------


def test_event_type_histogram_sorts_keys() -> None:
    events = [
        {"type": "tool_call"},
        {"type": "chunk"},
        {"type": "tool_call"},
        {"type": "thinking_chunk"},
    ]
    histogram = agentic_eval._event_type_histogram(events)
    assert histogram == {"chunk": 1, "thinking_chunk": 1, "tool_call": 2}


def test_event_type_histogram_handles_missing_type() -> None:
    events = [{"type": ""}, {}, {"type": None}]
    histogram = agentic_eval._event_type_histogram(events)
    assert histogram == {"unknown": 3}


def test_summarize_tool_calls_caps_at_limit() -> None:
    calls = [{"tool": f"t{i}", "args": {"k": i}, "event_index": i} for i in range(20)]
    summary = agentic_eval._summarize_tool_calls(calls, limit=3)
    assert len(summary) == 3
    assert summary[0]["tool"] == "t0"
    assert "args_preview" in summary[0]
    # Non-serialisable args fall back to str()
    bad = [{"tool": "x", "args": {"bad": object()}}]
    rendered = agentic_eval._summarize_tool_calls(bad, limit=1)
    assert rendered[0]["args_preview"]


def test_summarize_raw_events_branch_coverage() -> None:
    events = [
        {"type": "thinking_chunk", "content": "considering..."},
        {"type": "tool_call", "tool": "read_file", "args": {"path": "x"}},
        {"type": "error", "error": "boom"},
        {"type": "fingerprint", "profile_id": "p1", "profile_hash": "h1"},
        {"type": "unknown", "x": 1},
        {"type": ""},
    ]
    samples = agentic_eval._summarize_raw_events(events, limit=10)
    assert len(samples) == 6
    # The fingerprint sample should include the fingerprint fields
    fp = next(s for s in samples if s.get("type") == "fingerprint")
    assert "fingerprint" in fp
    # Unknown types without extras should expose the available keys
    unknown = next(s for s in samples if s.get("type") == "unknown")
    assert "keys" in unknown


def test_build_transport_observation_returns_empty_for_empty_payload() -> None:
    assert agentic_eval._build_transport_observation(observed=None) == {}
    assert agentic_eval._build_transport_observation(observed={}) == {}


def test_build_transport_observation_extracts_tool_names() -> None:
    payload = {
        "tool_calls": [
            {"tool": "read_file", "args": {"path": "x"}},
            {"tool": "", "args": {}},
            {"tool": "grep", "args": {"pattern": "p"}},
        ],
        "output": "hello world",
        "thinking": "thinking...",
        "error": "",
        "duration_ms": 42,
        "event_count": 5,
    }
    obs = agentic_eval._build_transport_observation(observed=payload)
    assert obs["tool_call_count"] == 3
    assert obs["tool_names"] == ["read_file", "grep"]
    assert obs["error"] == ""
    assert obs["duration_ms"] == 42
    assert obs["event_count"] == 5
    assert obs["tool_calls_preview"]


# ---------------------------------------------------------------------------
# Failure-diagnosis / observed-trace
# ---------------------------------------------------------------------------


def test_extract_failed_checks_sorts_by_priority() -> None:
    case = {
        "judge": {
            "checks": [
                {"code": "validator:qa_passfail_json", "category": "contract", "passed": False, "critical": False},
                {"code": "forbidden_tool:rm", "category": "safety", "passed": False, "critical": True},
                {"code": "min_tool_calls", "category": "tooling", "passed": False, "critical": False},
                {"code": "ok-check", "category": "contract", "passed": True, "critical": False},
            ]
        }
    }
    failed = agentic_eval._extract_failed_checks(case)
    assert len(failed) == 3
    # First by critical (0), then by category rank (safety=0, contract=1, tooling=2)
    assert failed[0]["code"] == "forbidden_tool:rm"
    assert failed[1]["code"] == "validator:qa_passfail_json"
    assert failed[2]["code"] == "min_tool_calls"


def test_check_priority_orders_correctly() -> None:
    # Critical beats non-critical; safety < contract < tooling
    crit_safety = {"critical": True, "category": "safety", "code": "a"}
    noncrit_safety = {"critical": False, "category": "safety", "code": "b"}
    noncrit_contract = {"critical": False, "category": "contract", "code": "c"}
    items = sorted(
        [noncrit_contract, crit_safety, noncrit_safety],
        key=agentic_eval._check_priority,
    )
    assert items[0] is crit_safety
    assert items[1] is noncrit_safety
    assert items[2] is noncrit_contract


def test_failure_priority_returns_known_buckets() -> None:
    assert agentic_eval._failure_priority({"critical": True}) == 0
    assert agentic_eval._failure_priority({"critical": False, "category": "safety"}) == 0
    assert agentic_eval._failure_priority({"critical": False, "category": "contract"}) == 1
    assert agentic_eval._failure_priority({"critical": False, "category": "tooling"}) == 2
    assert agentic_eval._failure_priority({"critical": False, "category": "evidence"}) == 2


def test_priority_label_uses_p0_p1_p2() -> None:
    assert agentic_eval._priority_label(0) == "P0"
    assert agentic_eval._priority_label(1) == "P1"
    assert agentic_eval._priority_label(2) == "P2"
    assert agentic_eval._priority_label(99) == "P2"


def test_build_failure_diagnosis_handles_each_code_prefix() -> None:
    failed_checks = [
        {"code": "required_tool:read_file", "category": "tooling", "passed": False, "critical": False},
        {"code": "forbidden_tool:rm_rf", "category": "tooling", "passed": False, "critical": False},
        {"code": "required_output:phase", "category": "contract", "passed": False, "critical": False},
        {"code": "validator:pm_plan_json", "category": "contract", "passed": False, "critical": False},
        {"code": "textual_tool_protocol_without_trace", "category": "tooling", "passed": False, "critical": False},
    ]
    observed = {
        "tool_calls": [{"tool": "read_file", "args": {"path": "x"}}],
        "output": "I would call [TOOL_CALL] but I cannot",
        "thinking": "",
    }
    diagnosis = agentic_eval._build_failure_diagnosis(
        failed_checks=failed_checks,
        observed=observed,
        stream_observed={"tool_calls": [{"tool": "read_file"}], "error": ""},
        non_stream_observed={"tool_calls": [], "error": "non_stream failed"},
    )
    assert diagnosis["missing_required_tools"] == ["read_file"]
    assert diagnosis["forbidden_tools_triggered"] == ["rm_rf"]
    assert diagnosis["missing_output_tokens"] == ["phase"]
    assert diagnosis["failed_validators"] == ["pm_plan_json"]
    assert "[TOOL_CALL]" in diagnosis["textual_tool_protocol_markers"]
    assert diagnosis["has_native_tool_trace"] is True
    assert diagnosis["transport_errors"] == {"non_stream": "non_stream failed"}
    assert diagnosis["transport_tool_counts"] == {"stream": 1, "non_stream": 0}


def test_build_observed_trace_uses_histogram_for_event_count() -> None:
    # raw_events provided once; event_count should derive from histogram, not a
    # second consumption of the iterable.
    raw_events = [
        {"type": "chunk"},
        {"type": "tool_call", "tool": "t", "args": {}},
        {"type": "tool_call", "tool": "t2", "args": {}},
    ]
    observed = {
        "output": "out",
        "thinking": "th",
        "error": "",
        "duration_ms": 10,
        "tool_calls": [{"tool": "t"}, {"tool": "t2"}],
    }
    trace = agentic_eval._build_observed_trace(
        observed=observed,
        raw_events=raw_events,
        workspace_files=["a.py", "b.py", "c.py"],
    )
    assert trace["event_count"] == 3
    assert trace["event_type_histogram"] == {"chunk": 1, "tool_call": 2}
    assert trace["tool_names"] == ["t", "t2"]


def test_build_observed_trace_observed_event_count_takes_precedence() -> None:
    raw_events = [{"type": "chunk"}]
    observed = {"event_count": 99}
    trace = agentic_eval._build_observed_trace(
        observed=observed,
        raw_events=raw_events,
        workspace_files=[],
    )
    assert trace["event_count"] == 99


# ---------------------------------------------------------------------------
# Progress-bar helpers
# ---------------------------------------------------------------------------


def test_render_progress_bar_full_and_empty() -> None:
    assert agentic_eval._render_progress_bar(0, 0) == "[------------------------]"
    assert agentic_eval._render_progress_bar(5, 5) == "[########################]"
    # Partial progress
    bar = agentic_eval._render_progress_bar(1, 4)
    assert bar.startswith("[#")
    assert bar.endswith("]")


def test_build_progress_callback_disabled_returns_none() -> None:
    assert agentic_eval._build_progress_callback(enabled=False) is None


def test_build_progress_callback_emits_suite_started() -> None:
    emit = agentic_eval._build_progress_callback(enabled=True)
    assert emit is not None
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit({"type": "suite_started", "suite": "agentic_benchmark", "run_id": "r1", "total_cases": 3})
    out = buf.getvalue()
    assert "start suite=agentic_benchmark run_id=r1 total=3" in out


def test_build_progress_callback_emits_case_started() -> None:
    emit = agentic_eval._build_progress_callback(enabled=True)
    assert emit is not None
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit({"type": "case_started", "index": 1, "total_cases": 2, "case_id": "c1", "level": "L1", "title": "T"})
    out = buf.getvalue()
    assert "c1 :: L1 T" in out


def test_build_progress_callback_emits_phase_started() -> None:
    emit = agentic_eval._build_progress_callback(enabled=True)
    assert emit is not None
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit({"type": "phase_started", "phase": "judge", "case_id": "c1", "title": "T"})
    out = buf.getvalue()
    assert "phase=judge" in out
    # Empty phase should be a no-op
    buf.truncate(0)
    buf.seek(0)
    emit({"type": "phase_started", "phase": "", "case_id": "c1", "title": "T"})
    assert buf.getvalue() == ""


def test_build_progress_callback_emits_case_completed_and_suite_completed() -> None:
    emit = agentic_eval._build_progress_callback(enabled=True)
    assert emit is not None
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit({"type": "suite_started", "suite": "x", "run_id": "r", "total_cases": 2})
        emit(
            {
                "type": "case_completed",
                "index": 1,
                "total_cases": 2,
                "passed": True,
                "score": 0.9,
                "duration_ms": 100,
                "case_id": "c1",
            }
        )
        emit(
            {
                "type": "case_completed",
                "index": 2,
                "total_cases": 2,
                "passed": False,
                "score": 0.2,
                "duration_ms": 50,
                "case_id": "c2",
            }
        )
        emit(
            {
                "type": "suite_completed",
                "suite": "x",
                "total_cases": 2,
                "passed_cases": 1,
                "failed_cases": 1,
                "artifact_path": "/tmp/x",
            }
        )
    out = buf.getvalue()
    assert "status=PASS" in out
    assert "status=FAIL" in out
    assert "complete" in out
    assert "artifact=/tmp/x" in out


def test_build_progress_callback_silently_drops_unknown_event() -> None:
    emit = agentic_eval._build_progress_callback(enabled=True)
    assert emit is not None
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit({"type": "no_such_event"})
        emit({"type": ""})
    assert buf.getvalue() == ""


# ---------------------------------------------------------------------------
# Repair-hint helpers
# ---------------------------------------------------------------------------


def test_repair_hint_handles_each_known_prefix() -> None:
    hints = {
        "parity:stream_eq_non_stream": "stream/non-stream",
        "required_tool:repo_rg": "required tool `repo_rg`",
        "required_tool_argument:file": "tool args",
        "min_tool_calls": "tool-call loop policy",
        "textual_tool_protocol_without_trace": "native tool",
        "forbidden_tool:rm": "allowlist",
        "forbidden_tool_argument:rm": "allowlist",
        "required_output:phase": "output contract",
        "forbidden_output:token": "output sanitizer",
        "validator:no_prompt_leakage": "prompt leakage",
        "validator:pm_plan_json": "PM JSON",
        "validator:qa_passfail_json": "QA JSON",
        "validator:director_safe_scope": "Director plans",
        "validator:no_hallucinated_paths": "workspace file",
        "validator:structured_steps": "numbered step",
    }
    for code, expected_substring in hints.items():
        hint = agentic_eval._repair_hint(code, "tooling")
        assert expected_substring.lower() in hint.lower(), f"code={code!r} hint={hint!r}"


def test_repair_hint_falls_back_to_category() -> None:
    assert "safety guardrails" in agentic_eval._repair_hint("mystery", "safety")
    assert "schema validation" in agentic_eval._repair_hint("mystery", "contract")
    assert "tool invocation" in agentic_eval._repair_hint("mystery", "tooling")
    assert "local evidence" in agentic_eval._repair_hint("mystery", "evidence")


def test_normalize_check_code_strips_stream_prefix() -> None:
    assert agentic_eval._normalize_check_code("stream:required_tool:foo") == "required_tool:foo"
    assert agentic_eval._normalize_check_code("non_stream:validator:x") == "validator:x"
    assert agentic_eval._normalize_check_code("plain_code") == "plain_code"
    assert agentic_eval._normalize_check_code("") == ""


# ---------------------------------------------------------------------------
# Failed-id / check-code extractors
# ---------------------------------------------------------------------------


def test_extract_failed_case_ids_and_check_codes() -> None:
    payload = {
        "failures": [
            {"case_id": "a", "failed_checks": [{"code": "validator:x"}, {"code": "required_tool:y"}]},
            {"case_id": "b", "failed_checks": [{"code": "validator:x"}]},
            {"case_id": "", "failed_checks": [{"code": "min_tool_calls"}]},
        ]
    }
    assert agentic_eval._extract_failed_case_ids(payload) == {"a", "b"}
    assert agentic_eval._extract_failed_check_codes(payload) == {"validator:x", "required_tool:y", "min_tool_calls"}


# ---------------------------------------------------------------------------
# Baseline-comparison branches
# ---------------------------------------------------------------------------


def test_build_baseline_comparison_trend_improved() -> None:
    current = _fake_audit_payload()
    current["status"] = "PASS"
    current["score"]["overall_percent"] = 80.0
    current["score"]["pass_rate"] = 1.0
    current["score"]["failed_cases"] = 0
    current["failures"] = []
    baseline = _fake_audit_payload()
    comparison = agentic_eval._build_baseline_comparison(
        current_payload=current,
        baseline_payload=baseline,
        baseline_path=Path("/tmp/baseline.json"),
        baseline_ref="baseline-ref",
    )
    assert comparison["trend"] == "improved"


def test_build_baseline_comparison_trend_regressed() -> None:
    # "regressed" requires new failures but no resolved failures
    current = _fake_audit_payload()
    current["score"]["overall_percent"] = 30.0
    current["failures"] = [
        {"case_id": "newly_broken", "failed_checks": [{"code": "validator:pm_plan_json"}]},
    ]
    baseline = _fake_audit_payload()
    baseline["failures"] = []  # baseline had no failures → only new failures
    comparison = agentic_eval._build_baseline_comparison(
        current_payload=current,
        baseline_payload=baseline,
        baseline_path=Path("/tmp/baseline.json"),
        baseline_ref="baseline-ref",
    )
    assert comparison["trend"] == "regressed"
    assert "newly_broken" in comparison["cases"]["new_failures"]


def test_build_baseline_comparison_trend_mixed() -> None:
    current = _fake_audit_payload()
    current["score"]["overall_percent"] = 50.0
    current["failures"] = [
        {"case_id": "pm_task_contract", "failed_checks": [{"code": "validator:pm_plan_json"}]},
        {"case_id": "new_case", "failed_checks": [{"code": "required_tool:foo"}]},
    ]
    baseline = _fake_audit_payload()
    baseline["failures"] = [
        {"case_id": "pm_task_contract", "failed_checks": [{"code": "validator:pm_plan_json"}]},
        {"case_id": "legacy_case", "failed_checks": [{"code": "required_output:phase"}]},
    ]
    comparison = agentic_eval._build_baseline_comparison(
        current_payload=current,
        baseline_payload=baseline,
        baseline_path=Path("/tmp/baseline.json"),
        baseline_ref="baseline-ref",
    )
    assert comparison["trend"] == "mixed"
    assert "new_case" in comparison["cases"]["new_failures"]
    assert "legacy_case" in comparison["cases"]["resolved_failures"]
    assert "pm_task_contract" in comparison["cases"]["persistent_failures"]


def test_build_baseline_comparison_trend_unchanged() -> None:
    payload = _fake_audit_payload()
    comparison = agentic_eval._build_baseline_comparison(
        current_payload=payload,
        baseline_payload=payload,
        baseline_path=Path("/tmp/baseline.json"),
        baseline_ref="baseline-ref",
    )
    assert comparison["trend"] == "unchanged"


# ---------------------------------------------------------------------------
# Suite-runner dispatch / mode aggregation
# ---------------------------------------------------------------------------


def test_suite_runners_contains_all_suites() -> None:
    runners = agentic_eval._suite_runners()
    assert set(runners) >= {
        "agentic_benchmark",
        "tool_calling_matrix",
        "speculation_matrix",
        "context_projection_matrix",
        "projection_adaptive_matrix",
    }


def test_aggregate_all_mode_results_computes_total_score() -> None:
    agentic = {
        "ok": False,
        "details": {
            "total_cases": 4,
            "passed_cases": 3,
            "failed_cases": 1,
            "average_score": 0.75,
            "cases": [],
            "report": {"test_run_id": "all-run-1"},
        },
    }
    context = {
        "summary": {"total": 2, "passed": 2, "failed": 0, "average_score": 0.9, "pass_rate": 1.0},
        "results": [],
    }
    strategy = {
        "summary": {"total": 1, "passed": 0, "failed": 1, "average_score": 0.1, "pass_rate": 0.0},
        "results": [],
    }
    aggregated = agentic_eval._aggregate_all_mode_results(agentic=agentic, context=context, strategy=strategy)
    assert aggregated["ok"] is False
    assert aggregated["details"]["total_cases"] == 7
    assert aggregated["details"]["passed_cases"] == 5
    assert aggregated["details"]["failed_cases"] == 2
    # average of the three per-suite scores
    assert abs(aggregated["details"]["average_score"] - (0.75 + 0.9 + 0.1) / 3) < 1e-6
    assert aggregated["details"]["report"]["test_run_id"] == "all-run-1"


# ---------------------------------------------------------------------------
# Audit-path resolvers
# ---------------------------------------------------------------------------


def test_resolve_baseline_audit_path_rejects_pull_manifest(tmp_path: Path) -> None:
    # Write a pull manifest under the BASELINE_LIBRARY_PULL.json path.
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    metadata_dir = get_workspace_metadata_dir_name()
    pull_dir = tmp_path / metadata_dir / "runtime" / "llm_evaluations" / "baselines" / "token1"
    pull_dir.mkdir(parents=True, exist_ok=True)
    (pull_dir / "BASELINE_LIBRARY_PULL.json").write_text("{}", encoding="utf-8")
    try:
        agentic_eval._resolve_baseline_audit_path(str(tmp_path), "token1")
    except ValueError as exc:
        assert "AGENTIC_EVAL_AUDIT.json" in str(exc)
    else:
        raise AssertionError("expected ValueError for pull manifest")


def test_resolve_baseline_audit_path_resolves_explicit_file(tmp_path: Path) -> None:
    audit = tmp_path / "explicit.json"
    _write_audit_json(audit, _fake_audit_payload())
    resolved = agentic_eval._resolve_baseline_audit_path(str(tmp_path), str(audit))
    assert resolved == audit.resolve()


def test_resolve_rerun_audit_path_rejects_missing_reference(tmp_path: Path) -> None:
    try:
        agentic_eval._resolve_rerun_audit_path(str(tmp_path), "definitely-missing")
    except FileNotFoundError as exc:
        assert "definitely-missing" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_resolve_rerun_audit_path_resolves_explicit_file(tmp_path: Path) -> None:
    audit = tmp_path / "rerun.json"
    payload = _fake_audit_payload()
    _write_audit_json(audit, payload)
    resolved, loaded = agentic_eval._resolve_rerun_audit_path(str(tmp_path), str(audit))
    assert resolved == audit.resolve()
    assert loaded["benchmark"]["run_id"] == "run-001"


# ---------------------------------------------------------------------------
# Probe (no-LLM, stub the runtime service)
# ---------------------------------------------------------------------------


def _stub_role_command_cls() -> type:
    """Build a stub ``ExecuteRoleSessionCommandV1`` that swallows kwargs."""

    class _StubCommand:
        def __init__(self, **_kwargs: Any) -> None:
            pass

    return _StubCommand


def _stub_runtime_config_module() -> Any:
    class _StubManager:
        @staticmethod
        def get_role_config(_role: str) -> None:
            return None

    module = type("FakeRuntimeConfig", (), {"RuntimeConfigManager": _StubManager})
    return module


def _install_probe_stubs(
    execute_coro: Any,
) -> tuple[list[str], list[str]]:
    """Install probe-related module stubs and return teardown + sys.modules keys."""
    fake_service_module = type(
        "FakeModule",
        (),
        {
            "RoleRuntimeService": type(
                "RoleRuntimeService",
                (),
                {"execute_role_session": execute_coro},
            )
        },
    )
    fake_contracts_module = type(
        "FakeContracts",
        (),
        {"ExecuteRoleSessionCommandV1": _stub_role_command_cls()},
    )
    sys.modules["polaris.cells.roles.runtime.public.contracts"] = fake_contracts_module  # type: ignore[assignment]
    sys.modules["polaris.cells.roles.runtime.public.service"] = fake_service_module  # type: ignore[assignment]
    sys.modules["polaris.kernelone.llm.runtime_config"] = _stub_runtime_config_module()  # type: ignore[assignment]
    return [
        "polaris.cells.roles.runtime.public.contracts",
        "polaris.cells.roles.runtime.public.service",
        "polaris.kernelone.llm.runtime_config",
    ], []


def test_run_probe_async_returns_aggregate_for_all_roles() -> None:
    """Probe should aggregate role results from the runtime service stub."""

    class _Result:
        output = "ok"
        thinking = ""
        error_message = ""
        metadata = {"provider": "stub", "model": "stub-model"}

    async def _fake_execute(self, command):  # type: ignore[no-untyped-def]
        return _Result()

    to_pop, _ = _install_probe_stubs(_fake_execute)
    try:
        result = asyncio.run(agentic_eval._run_probe_async(workspace="."))
    finally:
        for name in to_pop:
            sys.modules.pop(name, None)

    assert result["ok"] is True
    assert set(result["roles"]) == set(agentic_eval._ALL_PROBE_ROLES)
    assert result["failed_roles"] == []
    assert result["passed_roles"] == list(agentic_eval._ALL_PROBE_ROLES)
    for role_data in result["roles"].values():
        assert role_data["duration_ms"] >= 0


def test_run_probe_async_records_failures_when_role_throws() -> None:
    async def _fake_execute(self, command):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated outage")

    to_pop, _ = _install_probe_stubs(_fake_execute)
    try:
        result = asyncio.run(agentic_eval._run_probe_async(workspace="."))
    finally:
        for name in to_pop:
            sys.modules.pop(name, None)

    assert result["ok"] is False
    assert set(result["failed_roles"]) == set(agentic_eval._ALL_PROBE_ROLES)
    for role_data in result["roles"].values():
        assert role_data["ok"] is False
        assert "simulated outage" in role_data["error"]


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------


def test_print_probe_human_reports_pass_and_fail(capsys: Any) -> None:
    agentic_eval._print_probe_human(
        {
            "ok": False,
            "roles": {
                "pm": {"ok": True, "duration_ms": 12, "provider": "p", "model": "m", "output_preview": "ok"},
                "qa": {"ok": False, "duration_ms": 30, "provider": "p", "model": "m", "error": "boom"},
            },
            "failed_roles": ["qa"],
            "passed_roles": ["pm"],
        }
    )
    captured = capsys.readouterr()
    assert "status=FAIL" in captured.out
    assert "pm: PASS" in captured.out
    assert "qa: FAIL" in captured.out
    assert "failed_roles=qa" in captured.out


def test_print_probe_human_all_passed(capsys: Any) -> None:
    agentic_eval._print_probe_human(
        {
            "ok": True,
            "roles": {"pm": {"ok": True, "duration_ms": 1, "provider": "p", "model": "m"}},
            "failed_roles": [],
            "passed_roles": ["pm"],
        }
    )
    captured = capsys.readouterr()
    assert "all roles accessible" in captured.out


def test_print_baseline_pull_human_renders_summary(capsys: Any) -> None:
    payload = {
        "ok": True,
        "pull_id": "20260327T010203Z",
        "output_root": "/tmp/baselines",
        "manifest_path": "/tmp/baselines/manifest.json",
        "cache_root": "/tmp/cache",
        "use_cache": True,
        "refresh_cache": False,
        "check_only": False,
        "unknown_sources": ["weird-one"],
        "source_results": [
            {
                "source": "bfcl",
                "status": "ok",
                "downloaded_count": 4,
                "failed_count": 0,
                "cache_hits": 2,
                "cache_misses": 2,
                "network_downloads": 2,
                "manifest_path": "/tmp/bfcl/manifest.json",
            }
        ],
    }
    agentic_eval._print_baseline_pull_human(payload)
    captured = capsys.readouterr()
    assert "status=PASS" in captured.out
    assert "source=bfcl" in captured.out
    assert "unknown_sources:" in captured.out
    assert "weird-one" in captured.out


def test_print_human_renders_full_audit_package(capsys: Any) -> None:
    payload = {
        "status": "FAIL",
        "benchmark": {
            "run_id": "r1",
            "role_scope": "all",
            "provider_id": "p",
            "model": "m",
            "transport_mode": "stream",
        },
        "score": {"overall_percent": 50.0, "passed_cases": 1, "total_cases": 2, "failed_cases": 1, "pass_rate": 0.5},
        "tool_audit": {"total_calls": 3, "critical_failures": 1, "by_tool": {"read_file": 3}},
        "evidence_paths": {
            "benchmark_artifact": "/tmp/AGENTIC_BENCHMARK_REPORT.json",
            "audit_package": "/tmp/AGENTIC_EVAL_AUDIT.json",
        },
        "failures": [
            {
                "case_id": "c1",
                "role": "pm",
                "title": "PM test",
                "score_percent": 30.0,
                "threshold_percent": 85.0,
                "summary": "failed",
                "root_cause": {"code": "validator:pm_plan_json", "category": "contract", "message": "bad"},
                "failed_checks": [
                    {"code": "validator:pm_plan_json", "category": "contract", "message": "bad", "passed": False}
                ],
                "observed_trace": {
                    "tool_names": ["read_file"],
                    "tool_call_count": 1,
                    "event_count": 4,
                    "event_type_histogram": {"tool_call": 1, "chunk": 3},
                    "transport_observations": {},
                    "output_preview": "preview-text",
                },
                "diagnosis": {
                    "failed_validators": ["pm_plan_json"],
                    "textual_tool_protocol_markers": [],
                    "transport_errors": {"stream": "boom"},
                },
                "evidence": {"sandbox_workspace": "/tmp/sandbox", "raw_event_count": 4},
            }
        ],
        "repair_plan": [{"priority": "P1", "action": "tighten schema"}],
        "comparison": {
            "enabled": True,
            "trend": "regressed",
            "baseline_ref": "ref-1",
            "current": {"run_id": "r1", "status": "FAIL", "overall_percent": 50.0},
            "baseline": {"run_id": "r0", "status": "PASS", "overall_percent": 90.0},
            "delta": {"overall_percent": -40.0, "failed_cases": 1},
            "cases": {
                "new_failures": ["c1"],
                "resolved_failures": [],
            },
        },
    }
    agentic_eval._print_human(payload)
    captured = capsys.readouterr()
    assert "status=FAIL" in captured.out
    assert "tool_calls=3" in captured.out
    assert "top_failures" in captured.out
    assert "c1 [contract/validator:pm_plan_json]" in captured.out
    assert "failure_diagnostics" in captured.out
    assert "repair_plan:" in captured.out
    assert "baseline_compare trend=regressed" in captured.out
    assert "new_failures=c1" in captured.out
    assert "audit_package=" in captured.out


# ---------------------------------------------------------------------------
# Default output path
# ---------------------------------------------------------------------------


def test_default_output_path_uses_run_id() -> None:
    assert agentic_eval._default_output_path("r-1") == "runtime/llm_evaluations/r-1/AGENTIC_EVAL_AUDIT.json"


def test_default_output_path_falls_back_to_timestamp() -> None:
    out = agentic_eval._default_output_path("")
    assert out.startswith("runtime/llm_evaluations/cli-")
    assert out.endswith("/AGENTIC_EVAL_AUDIT.json")
