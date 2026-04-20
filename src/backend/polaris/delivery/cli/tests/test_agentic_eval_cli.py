from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

from polaris.delivery.cli import __main__ as cli_main, agentic_eval


def _local_tmp_dir(label: str) -> Path:
    path = Path("tmp_pytest_agentic_eval_local") / f"{label}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fake_report(*, passed: bool) -> dict:
    return {
        "ok": passed,
        "details": {
            "artifact_path": "C:/tmp/AGENTIC_BENCHMARK_REPORT.json",
            "report": {
                "suite": "agentic_benchmark",
                "test_run_id": "run-001",
                "summary": {
                    "total_cases": 2,
                    "passed_cases": 1 if not passed else 2,
                    "failed_cases": 1 if not passed else 0,
                    "average_score": 0.6 if not passed else 0.95,
                },
                "cases": [
                    {
                        "case": {
                            "case_id": "pm_task_contract",
                            "role": "pm",
                            "title": "PM task contract",
                            "prompt": "Generate a PM delivery plan in strict JSON.",
                            "tags": ["pm", "contract"],
                            "judge": {
                                "required_tools": ["read_file"],
                                "validators": ["pm_plan_json"],
                            },
                        },
                        "sandbox_workspace": "C:/tmp/sandbox/pm_task_contract",
                        "workspace_files": ["README.md", "docs/spec.md"],
                        "observed": {
                            "output": "[TOOL_CALL] {tool: 'read_file'} [/TOOL_CALL] free-form text",
                            "thinking": "Need to inspect the repo and emit JSON.",
                            "tool_calls": [
                                {"tool": "read_file", "args": {"path": "README.md"}, "event_index": 2},
                            ],
                            "duration_ms": 321,
                            "event_count": 3,
                            "fingerprint": {"profile_id": "pm_default"},
                        },
                        "judge": {
                            "passed": passed,
                            "score": 0.4 if not passed else 1.0,
                            "threshold": 0.85,
                            "summary": "failed checks: validator:pm_plan_json"
                            if not passed
                            else "all deterministic checks passed",
                            "checks": [
                                {
                                    "code": "validator:pm_plan_json",
                                    "category": "contract",
                                    "passed": passed,
                                    "critical": False,
                                    "message": "pm output contract failed",
                                    "evidence": {},
                                }
                            ],
                        },
                        "raw_events": [
                            {"type": "fingerprint", "profile_id": "pm_default", "run_id": "run-001"},
                            {"type": "thinking_chunk", "content": "Need to inspect the repo"},
                            {"type": "tool_call", "tool": "read_file", "args": {"path": "README.md"}},
                        ],
                    },
                    {
                        "case": {
                            "case_id": "qa_release_verdict",
                            "role": "qa",
                            "title": "QA verdict",
                            "prompt": "Return the QA verdict as JSON.",
                        },
                        "sandbox_workspace": "C:/tmp/sandbox/qa_release_verdict",
                        "observed": {
                            "output": '{"passed": true, "findings": []}',
                            "tool_calls": [{"tool": "read_file", "args": {"path": "README.md"}}],
                            "duration_ms": 111,
                            "event_count": 1,
                        },
                        "judge": {
                            "passed": True,
                            "score": 1.0,
                            "threshold": 0.85,
                            "summary": "all deterministic checks passed",
                            "checks": [],
                        },
                        "raw_events": [],
                    },
                ],
            },
        },
    }


def test_main_parser_accepts_agentic_eval_subcommand() -> None:
    parser = cli_main.create_parser()
    args = parser.parse_args(
        ["agentic-eval", "--suite", "tool_calling_matrix", "--role", "pm", "--case-id", "pm_task_contract"]
    )
    assert args.command == "agentic-eval"
    assert args.suite == "tool_calling_matrix"
    assert args.role == "pm"
    assert args.case_id == ["pm_task_contract"]
    assert args.matrix_transport == "stream"


def test_main_parser_accepts_baseline_pull_flags() -> None:
    parser = cli_main.create_parser()
    args = parser.parse_args(
        [
            "agentic-eval",
            "--baseline-pull",
            "bfcl",
            "--baseline-pull",
            "toolbench",
            "--baseline-only",
            "--baseline-output",
            "runtime/llm_evaluations/baselines",
            "--baseline-timeout",
            "12.5",
            "--baseline-retries",
            "4",
            "--baseline-cache-check",
        ]
    )
    assert args.command == "agentic-eval"
    assert args.baseline_pull == ["bfcl", "toolbench"]
    assert args.baseline_only is True
    assert args.baseline_output.endswith("baselines")
    assert abs(float(args.baseline_timeout) - 12.5) < 1e-6
    assert int(args.baseline_retries) == 4
    assert args.baseline_cache_check is True
    assert args.baseline_refresh is False


def test_main_parser_accepts_compare_baseline_flag() -> None:
    parser = cli_main.create_parser()
    args = parser.parse_args(
        [
            "agentic-eval",
            "--compare-baseline",
            "run-20260327",
        ]
    )
    assert args.command == "agentic-eval"
    assert args.compare_baseline == "run-20260327"


def test_main_parser_accepts_matrix_transport_flag() -> None:
    parser = cli_main.create_parser()
    args = parser.parse_args(
        [
            "agentic-eval",
            "--suite",
            "tool_calling_matrix",
            "--matrix-transport",
            "non_stream",
        ]
    )
    assert args.command == "agentic-eval"
    assert args.matrix_transport == "non_stream"


def test_build_agentic_eval_audit_package_contains_failures_and_repairs() -> None:
    payload = agentic_eval.build_agentic_eval_audit_package(
        workspace=".",
        scope_role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        run_result=_fake_report(passed=False),
        max_fixes=5,
    )

    assert payload["status"] == "FAIL"
    assert payload["score"]["failed_cases"] == 1
    assert len(payload["failures"]) == 1
    assert payload["failures"][0]["case_id"] == "pm_task_contract"
    assert payload["failures"][0]["observed_trace"]["event_type_histogram"]["tool_call"] == 1
    assert "pm_plan_json" in payload["failures"][0]["diagnosis"]["failed_validators"]
    assert payload["repair_plan"]
    assert "schema" in payload["repair_plan"][0]["action"].lower()


def test_build_agentic_eval_audit_package_maps_stream_prefixed_check_code() -> None:
    report = _fake_report(passed=False)
    report["details"]["report"]["cases"][0]["judge"]["checks"][0]["code"] = "stream:required_tool:repo_rg"
    report["details"]["report"]["cases"][0]["judge"]["checks"][0]["category"] = "tooling"

    payload = agentic_eval.build_agentic_eval_audit_package(
        workspace=".",
        scope_role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        run_result=report,
        max_fixes=5,
    )

    actions = [str(item.get("action") or "") for item in payload.get("repair_plan", [])]
    assert any("required tool `repo_rg`" in action.lower() for action in actions)


def test_build_agentic_eval_audit_package_maps_textual_tool_protocol_check() -> None:
    report = _fake_report(passed=False)
    report["details"]["report"]["cases"][0]["judge"]["checks"][0]["code"] = "textual_tool_protocol_without_trace"
    report["details"]["report"]["cases"][0]["judge"]["checks"][0]["category"] = "tooling"

    payload = agentic_eval.build_agentic_eval_audit_package(
        workspace=".",
        scope_role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        run_result=report,
        max_fixes=5,
    )

    actions = [str(item.get("action") or "") for item in payload.get("repair_plan", [])]
    assert any("native tool" in action.lower() for action in actions)


def test_build_agentic_eval_audit_package_includes_transport_observation_details() -> None:
    report = _fake_report(passed=False)
    report["details"]["report"]["suite"] = "tool_calling_matrix"
    report["details"]["report"]["target"] = {"transport_mode": "stream"}
    report["details"]["report"]["cases"][0]["stream_observed"] = {
        "mode": "stream",
        "output": "stream output",
        "thinking": "",
        "tool_calls": [{"tool": "read_file", "args": {"file": "README.md"}}],
        "error": "",
        "duration_ms": 120,
        "event_count": 5,
    }
    report["details"]["report"]["cases"][0]["non_stream_observed"] = {
        "mode": "non_stream",
        "output": "",
        "thinking": "",
        "tool_calls": [],
        "error": "non_stream failed: invalid payload",
        "duration_ms": 80,
        "event_count": 1,
    }

    payload = agentic_eval.build_agentic_eval_audit_package(
        workspace=".",
        scope_role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        run_result=report,
        max_fixes=5,
    )

    failure = payload["failures"][0]
    transport_observations = failure["observed_trace"]["transport_observations"]
    assert payload["benchmark"]["transport_mode"] == "stream"
    assert transport_observations["stream"]["tool_call_count"] == 1
    assert transport_observations["non_stream"]["error"] == "non_stream failed: invalid payload"
    assert failure["diagnosis"]["transport_errors"]["non_stream"] == "non_stream failed: invalid payload"


def test_run_agentic_eval_command_writes_utf8_audit_package(
    monkeypatch,
) -> None:
    tmp_path = _local_tmp_dir("cli-audit")

    async def _fake_suite(*_args, **_kwargs):
        return _fake_report(passed=True)

    monkeypatch.setattr(agentic_eval, "run_agentic_benchmark_suite", _fake_suite)

    args = argparse.Namespace(
        workspace=str(tmp_path),
        suite="agentic_benchmark",
        role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        format="json",
        output="runtime/llm_evaluations/custom/AGENTIC_EVAL_AUDIT.json",
        max_fixes=3,
        case_id=[],
    )

    exit_code = agentic_eval.run_agentic_eval_command(args)
    assert exit_code == 0

    output_path = tmp_path / "runtime" / "llm_evaluations" / "custom" / "AGENTIC_EVAL_AUDIT.json"
    assert output_path.is_file()
    with open(output_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["status"] == "PASS"
    assert str(payload["evidence_paths"]["audit_package"]).endswith("AGENTIC_EVAL_AUDIT.json")


def test_run_agentic_eval_command_supports_tool_calling_matrix_suite(
    monkeypatch,
    capsys,
) -> None:
    tmp_path = _local_tmp_dir("cli-matrix")

    async def _fake_matrix_suite(*_args, **_kwargs):
        report = _fake_report(passed=True)
        report["details"]["report"]["suite"] = "tool_calling_matrix"
        return report

    monkeypatch.setattr(agentic_eval, "run_tool_calling_matrix_suite", _fake_matrix_suite)

    args = argparse.Namespace(
        workspace=str(tmp_path),
        suite="tool_calling_matrix",
        role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        format="human",
        output="runtime/llm_evaluations/custom/AGENTIC_EVAL_AUDIT.json",
        max_fixes=3,
        case_id=["l1_single_tool_accuracy"],
    )

    exit_code = agentic_eval.run_agentic_eval_command(args)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "benchmark_artifact=" in captured.out


def test_run_agentic_eval_command_passes_matrix_transport_option(
    monkeypatch,
) -> None:
    tmp_path = _local_tmp_dir("cli-matrix-transport")
    captured_options: dict[str, object] = {}

    async def _fake_matrix_suite(_provider_cfg, _model, _role, *, workspace, context, options):
        del workspace, context
        captured_options.update(dict(options))
        report = _fake_report(passed=True)
        report["details"]["report"]["suite"] = "tool_calling_matrix"
        report["details"]["report"]["target"] = {"transport_mode": options.get("matrix_transport")}
        return report

    monkeypatch.setattr(agentic_eval, "run_tool_calling_matrix_suite", _fake_matrix_suite)

    args = argparse.Namespace(
        workspace=str(tmp_path),
        suite="tool_calling_matrix",
        role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        matrix_transport="non_stream",
        format="json",
        output="runtime/llm_evaluations/custom/AGENTIC_EVAL_AUDIT.json",
        max_fixes=3,
        case_id=[],
        baseline_pull=[],
        baseline_output="runtime/llm_evaluations/baselines",
        baseline_timeout=15.0,
        baseline_only=False,
        baseline_retries=2,
        compare_baseline="",
    )

    exit_code = agentic_eval.run_agentic_eval_command(args)
    assert exit_code == 0
    assert captured_options["matrix_transport"] == "non_stream"


def test_run_agentic_eval_command_prints_progress_and_failure_diagnostics(
    monkeypatch,
    capsys,
) -> None:
    tmp_path = _local_tmp_dir("cli-progress")

    async def _fake_suite(_provider_cfg, _model, _role, *, workspace, context, options):
        callback = context.get("progress_callback")
        if callable(callback):
            callback(
                {
                    "type": "suite_started",
                    "suite": "agentic_benchmark",
                    "run_id": "run-001",
                    "total_cases": 2,
                }
            )
            callback(
                {
                    "type": "case_started",
                    "suite": "agentic_benchmark",
                    "run_id": "run-001",
                    "index": 1,
                    "total_cases": 2,
                    "case_id": "pm_task_contract",
                    "role": "pm",
                    "title": "PM task contract",
                }
            )
            callback(
                {
                    "type": "case_completed",
                    "suite": "agentic_benchmark",
                    "run_id": "run-001",
                    "index": 1,
                    "total_cases": 2,
                    "case_id": "pm_task_contract",
                    "role": "pm",
                    "title": "PM task contract",
                    "passed": False,
                    "score": 0.4,
                    "duration_ms": 321,
                }
            )
            callback(
                {
                    "type": "suite_completed",
                    "suite": "agentic_benchmark",
                    "run_id": "run-001",
                    "total_cases": 2,
                    "passed_cases": 1,
                    "failed_cases": 1,
                    "artifact_path": "C:/tmp/AGENTIC_BENCHMARK_REPORT.json",
                }
            )
        return _fake_report(passed=False)

    monkeypatch.setattr(agentic_eval, "run_agentic_benchmark_suite", _fake_suite)

    args = argparse.Namespace(
        workspace=str(tmp_path),
        suite="agentic_benchmark",
        role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        format="human",
        output="runtime/llm_evaluations/custom/AGENTIC_EVAL_AUDIT.json",
        max_fixes=3,
        case_id=[],
    )

    exit_code = agentic_eval.run_agentic_eval_command(args)
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "progress [" in captured.err
    assert "pm_task_contract :: PM task contract" in captured.err
    assert "failure_diagnostics" in captured.out
    assert "output_preview=" in captured.out


def test_run_agentic_eval_command_supports_baseline_pull_only(
    monkeypatch,
) -> None:
    tmp_path = _local_tmp_dir("cli-baseline-pull")
    pull_called: dict[str, object] = {}

    def _fake_list_sources() -> dict[str, dict[str, object]]:
        return {
            "bfcl": {"display_name": "BFCL"},
            "toolbench": {"display_name": "ToolBench"},
        }

    def _fake_pull_baseline_library(**kwargs):
        pull_called.update(kwargs)
        return {
            "ok": True,
            "pull_id": "20260327T010203Z",
            "output_root": str(tmp_path / "runtime" / "llm_evaluations" / "baselines"),
            "manifest_path": str(
                tmp_path
                / "runtime"
                / "llm_evaluations"
                / "baselines"
                / "pull-20260327T010203Z"
                / "BASELINE_LIBRARY_PULL.json"
            ),
            "unknown_sources": [],
            "source_results": [
                {
                    "source": "bfcl",
                    "status": "ok",
                    "downloaded_count": 2,
                    "failed_count": 0,
                    "manifest_path": "C:/tmp/bfcl/SOURCE_MANIFEST.json",
                }
            ],
        }

    async def _unexpected_suite(*_args, **_kwargs) -> NoReturn:
        raise AssertionError("benchmark suite should not run in baseline-only mode")

    monkeypatch.setattr(agentic_eval, "list_baseline_library_sources", _fake_list_sources)
    monkeypatch.setattr(agentic_eval, "pull_baseline_library", _fake_pull_baseline_library)
    monkeypatch.setattr(agentic_eval, "run_agentic_benchmark_suite", _unexpected_suite)

    args = argparse.Namespace(
        workspace=str(tmp_path),
        suite="agentic_benchmark",
        role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        matrix_transport="stream",
        format="json",
        output="",
        max_fixes=3,
        case_id=[],
        baseline_pull=["bfcl"],
        baseline_output="runtime/llm_evaluations/baselines",
        baseline_timeout=15.0,
        baseline_only=True,
        baseline_retries=2,
        baseline_cache_check=False,
        baseline_refresh=False,
        compare_baseline="",
    )

    exit_code = agentic_eval.run_agentic_eval_command(args)

    assert exit_code == 0
    assert Path(str(pull_called["workspace"])).resolve() == tmp_path.resolve()
    assert pull_called["sources"] == ["bfcl"]
    assert pull_called["use_cache"] is True
    assert pull_called["check_only"] is False
    assert pull_called["refresh_cache"] is False


def test_run_agentic_eval_command_rejects_baseline_only_without_sources() -> None:
    tmp_path = _local_tmp_dir("cli-baseline-only-no-source")

    args = argparse.Namespace(
        workspace=str(tmp_path),
        suite="agentic_benchmark",
        role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        matrix_transport="stream",
        format="json",
        output="",
        max_fixes=3,
        case_id=[],
        baseline_pull=[],
        baseline_output="runtime/llm_evaluations/baselines",
        baseline_timeout=15.0,
        baseline_only=True,
        baseline_retries=2,
        baseline_cache_check=False,
        baseline_refresh=False,
        compare_baseline="",
    )

    exit_code = agentic_eval.run_agentic_eval_command(args)
    assert exit_code == 1


def test_run_agentic_eval_command_rejects_baseline_only_with_compare_before_pull(
    monkeypatch,
) -> None:
    tmp_path = _local_tmp_dir("cli-baseline-only-compare")

    def _unexpected_pull(**_kwargs) -> NoReturn:
        raise AssertionError("baseline pull should not run for invalid baseline-only+compare combination")

    monkeypatch.setattr(agentic_eval, "pull_baseline_library", _unexpected_pull)

    args = argparse.Namespace(
        workspace=str(tmp_path),
        suite="agentic_benchmark",
        role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        matrix_transport="stream",
        format="json",
        output="",
        max_fixes=3,
        case_id=[],
        baseline_pull=["bfcl"],
        baseline_output="runtime/llm_evaluations/baselines",
        baseline_timeout=15.0,
        baseline_only=True,
        baseline_retries=2,
        baseline_cache_check=False,
        baseline_refresh=False,
        compare_baseline="run-001",
    )

    exit_code = agentic_eval.run_agentic_eval_command(args)
    assert exit_code == 1


def test_run_agentic_eval_command_baseline_cache_check_mode(
    monkeypatch,
) -> None:
    tmp_path = _local_tmp_dir("cli-baseline-cache-check")
    pull_called: dict[str, object] = {}

    def _fake_list_sources() -> dict[str, dict[str, object]]:
        return {"bfcl": {"display_name": "BFCL"}}

    def _fake_pull_baseline_library(**kwargs):
        pull_called.update(kwargs)
        return {
            "ok": True,
            "pull_id": "20260327T010203Z",
            "output_root": str(tmp_path / "runtime" / "llm_evaluations" / "baselines"),
            "manifest_path": str(
                tmp_path
                / "runtime"
                / "llm_evaluations"
                / "baselines"
                / "pull-20260327T010203Z"
                / "BASELINE_LIBRARY_PULL.json"
            ),
            "unknown_sources": [],
            "use_cache": True,
            "check_only": True,
            "refresh_cache": False,
            "source_results": [
                {
                    "source": "bfcl",
                    "status": "cache_ready",
                    "downloaded_count": 2,
                    "failed_count": 0,
                    "cache_hits": 2,
                    "cache_misses": 0,
                    "network_downloads": 0,
                    "manifest_path": "C:/tmp/bfcl/SOURCE_MANIFEST.json",
                }
            ],
        }

    monkeypatch.setattr(agentic_eval, "list_baseline_library_sources", _fake_list_sources)
    monkeypatch.setattr(agentic_eval, "pull_baseline_library", _fake_pull_baseline_library)

    args = argparse.Namespace(
        workspace=str(tmp_path),
        suite="agentic_benchmark",
        role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        matrix_transport="stream",
        format="json",
        output="",
        max_fixes=3,
        case_id=[],
        baseline_pull=["bfcl"],
        baseline_output="runtime/llm_evaluations/baselines",
        baseline_timeout=15.0,
        baseline_only=True,
        baseline_retries=2,
        baseline_cache_check=True,
        baseline_refresh=False,
        compare_baseline="",
    )

    exit_code = agentic_eval.run_agentic_eval_command(args)
    assert exit_code == 0
    assert pull_called["check_only"] is True
    assert pull_called["refresh_cache"] is False


def test_run_agentic_eval_command_baseline_pull_rejects_invalid_source(
    monkeypatch,
) -> None:
    tmp_path = _local_tmp_dir("cli-baseline-invalid")

    def _fake_list_sources() -> dict[str, dict[str, object]]:
        return {"bfcl": {"display_name": "BFCL"}}

    monkeypatch.setattr(agentic_eval, "list_baseline_library_sources", _fake_list_sources)

    args = argparse.Namespace(
        workspace=str(tmp_path),
        suite="agentic_benchmark",
        role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        format="json",
        output="",
        max_fixes=3,
        case_id=[],
        baseline_pull=["unknown-benchmark"],
        baseline_output="runtime/llm_evaluations/baselines",
        baseline_timeout=15.0,
        baseline_only=True,
        baseline_retries=2,
        baseline_cache_check=False,
        baseline_refresh=False,
        compare_baseline="",
    )

    exit_code = agentic_eval.run_agentic_eval_command(args)
    assert exit_code == 1


def test_run_agentic_eval_command_rejects_cache_check_with_refresh() -> None:
    tmp_path = _local_tmp_dir("cli-baseline-cache-flag-conflict")
    args = argparse.Namespace(
        workspace=str(tmp_path),
        suite="agentic_benchmark",
        role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        matrix_transport="stream",
        format="json",
        output="",
        max_fixes=3,
        case_id=[],
        baseline_pull=["bfcl"],
        baseline_output="runtime/llm_evaluations/baselines",
        baseline_timeout=15.0,
        baseline_only=True,
        baseline_retries=2,
        baseline_cache_check=True,
        baseline_refresh=True,
        compare_baseline="",
    )

    exit_code = agentic_eval.run_agentic_eval_command(args)
    assert exit_code == 1


def test_run_agentic_eval_command_compares_with_baseline_audit(
    monkeypatch,
) -> None:
    tmp_path = _local_tmp_dir("cli-compare-baseline")
    baseline_path = tmp_path / "baseline_audit.json"
    baseline_payload = {
        "status": "FAIL",
        "benchmark": {"run_id": "baseline-run-001", "suite": "agentic_benchmark"},
        "score": {
            "overall_percent": 40.0,
            "pass_rate": 0.0,
            "failed_cases": 2,
        },
        "tool_audit": {"total_calls": 1},
        "failures": [
            {
                "case_id": "pm_task_contract",
                "failed_checks": [{"code": "validator:pm_plan_json"}],
            },
            {
                "case_id": "legacy_case",
                "failed_checks": [{"code": "required_output:phase"}],
            },
        ],
    }
    baseline_path.write_text(json.dumps(baseline_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    async def _fake_suite(*_args, **_kwargs):
        return _fake_report(passed=False)

    monkeypatch.setattr(agentic_eval, "run_agentic_benchmark_suite", _fake_suite)

    args = argparse.Namespace(
        workspace=str(tmp_path),
        suite="agentic_benchmark",
        role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        format="json",
        output="runtime/llm_evaluations/custom/AGENTIC_EVAL_AUDIT.json",
        max_fixes=3,
        case_id=[],
        baseline_pull=[],
        baseline_output="runtime/llm_evaluations/baselines",
        baseline_timeout=15.0,
        baseline_only=False,
        baseline_retries=2,
        compare_baseline=str(baseline_path),
    )

    exit_code = agentic_eval.run_agentic_eval_command(args)
    assert exit_code == 1

    output_path = tmp_path / "runtime" / "llm_evaluations" / "custom" / "AGENTIC_EVAL_AUDIT.json"
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    comparison = payload.get("comparison") or {}
    assert comparison.get("enabled") is True
    assert comparison.get("baseline", {}).get("run_id") == "baseline-run-001"
    assert comparison.get("trend") == "improved"
    assert "legacy_case" in (comparison.get("cases", {}).get("resolved_failures") or [])


def test_run_agentic_eval_command_rejects_missing_compare_baseline() -> None:
    tmp_path = _local_tmp_dir("cli-compare-missing")
    args = argparse.Namespace(
        workspace=str(tmp_path),
        suite="agentic_benchmark",
        role="all",
        provider_id="runtime_binding",
        model="runtime_binding",
        format="json",
        output="",
        max_fixes=3,
        case_id=[],
        baseline_pull=[],
        baseline_output="runtime/llm_evaluations/baselines",
        baseline_timeout=15.0,
        baseline_only=False,
        baseline_retries=2,
        compare_baseline="not-found-baseline",
    )

    exit_code = agentic_eval.run_agentic_eval_command(args)
    assert exit_code == 1


def test_assemble_core_services_initializes_uep_pipeline(
    monkeypatch,
) -> None:
    """Verify assemble_core_services initializes the UEP v2.0 pipeline.

    This ensures that when agentic_eval runs, UEPEventPublisher can publish
    events to the global MessageBus, and TypedEventBusAdapter is available
    for dual-write to both EventRegistry and MessageBus.

    Regression test for: Journal events missing in benchmark because
    agentic_eval only called ensure_minimal_kernelone_bindings() instead of
    assemble_core_services(), leaving MessageBus and TypedEventBusAdapter uninitialized.
    """
    import sys

    from polaris.bootstrap.assembly import assemble_core_services
    from polaris.kernelone.events.registry import get_global_bus
    from polaris.kernelone.events.typed import get_default_adapter, get_default_registry

    # ── Reset pre-existing global state so this test is deterministic regardless
    # of whether assemble_core_services was called at module-import time (by other
    # tests importing agentic_eval) or from a prior test run. The guard in
    # _ensure_typed_event_bridge skips re-init when _default_adapter is already
    # set, so we must clear it here to force a fresh initialization with a known
    # bus instance. We use sys.modules dict manipulation since monkeypatch.setattr
    # on module globals does not reliably propagate across pytest test boundaries.
    _bus_adapter_mod = sys.modules["polaris.kernelone.events.typed.bus_adapter"]
    _bus_adapter_mod.__dict__["_default_adapter"] = None
    _registry_mod = sys.modules["polaris.kernelone.events.registry"]
    _registry_mod.__dict__["_global_bus"] = None

    # Bootstrap - this mirrors what run_agentic_eval_command does
    assemble_core_services(container=None, settings=None)

    # Verify global MessageBus is set
    bus = get_global_bus()
    assert bus is not None, "Global MessageBus must be initialized by assemble_core_services"

    # Verify TypedEventBusAdapter is initialized (dual-write mode)
    adapter = get_default_adapter()
    assert adapter is not None, "TypedEventBusAdapter must be initialized by assemble_core_services"

    # Verify adapter has dual_write enabled
    assert adapter._dual_write is True, "Adapter must be in dual-write mode"

    # Verify adapter is wired to the global bus
    assert adapter._bus is bus, "Adapter must be wired to the global MessageBus"

    # Verify adapter is wired to the global registry
    registry = get_default_registry()
    assert adapter._registry is registry, "Adapter must be wired to the global EventRegistry"

    # Verify the adapter has registered the stream event mappings
    # ToolInvoked, ToolCompleted, TurnStarted, etc. should map to RUNTIME_EVENT
    assert "tool_invoked" in adapter._event_to_message_type, "tool_invoked must be mapped"
    assert "tool_completed" in adapter._event_to_message_type, "tool_completed must be mapped"
    assert "turn_started" in adapter._event_to_message_type, "turn_started must be mapped"
    assert "turn_completed" in adapter._event_to_message_type, "turn_completed must be mapped"

    # ── Teardown: restore pre-bootstrap global state so other tests are not polluted.
    # Reset _default_adapter so that test_publish_stream_event_no_bus (which expects
    # no adapter to be available) continues to work correctly.
    _bus_adapter_mod.__dict__["_default_adapter"] = None
    _registry_mod.__dict__["_global_bus"] = None
