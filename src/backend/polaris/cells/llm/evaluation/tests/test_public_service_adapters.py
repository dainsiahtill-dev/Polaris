"""Regression tests for llm.evaluation public service adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from polaris.cells.llm.evaluation.internal.benchmark_models import AgenticBenchmarkCase, AgenticJudgeConfig
from polaris.cells.llm.evaluation.public.service import (
    _convert_agentic_case_to_unified,
    _convert_result_to_public_report,
    _convert_suite_result_to_public_summary,
)
from polaris.kernelone.benchmark.unified_models import UnifiedJudgeVerdict
from polaris.kernelone.benchmark.unified_runner import BenchmarkRunResult, BenchmarkSuiteResult


def _benchmark_result() -> BenchmarkSuiteResult:
    verdict = UnifiedJudgeVerdict(
        case_id="case-a",
        passed=True,
        score=0.92,
        threshold=0.75,
        categories={"tooling": 0.92},
        checks=(),
        summary="case passed",
    )
    return BenchmarkSuiteResult(
        suite_name="agentic_benchmark",
        run_id="run-public-adapter",
        mode="agentic",
        total_cases=2,
        passed_cases=1,
        failed_cases=1,
        average_score=0.46,
        results=(
            BenchmarkRunResult(
                case_id="case-a",
                passed=True,
                score=0.92,
                duration_ms=37,
                verdict=verdict,
            ),
        ),
        timestamp="2026-01-01T00:00:00+00:00",
        wall_time_ms=41,
    )


def test_agentic_public_report_adapter_preserves_stable_shape(tmp_path: Path) -> None:
    result = _benchmark_result()
    report = _convert_result_to_public_report(
        result,
        [
            {"id": "case-a", "passed": False, "output": "", "score": 0.0, "error": "", "latency_ms": 0},
            {"id": "case-b", "passed": False, "output": "", "score": 0.0, "error": "", "latency_ms": 0},
        ],
        workspace=str(tmp_path),
    )

    details = report["details"]
    assert report["ok"] is False
    assert set(details) == {
        "average_score",
        "artifact_path",
        "cases",
        "failed_cases",
        "passed_cases",
        "report",
        "total_cases",
    }
    assert details["cases"] == details["report"]["cases"]
    assert details["cases"] == [
        {"id": "case-a", "passed": True, "output": "", "score": 0.92, "error": "", "latency_ms": 37},
        {"id": "case-b", "passed": False, "output": "", "score": 0.0, "error": "", "latency_ms": 0},
    ]
    assert details["report"]["final"] == {
        "ready": False,
        "grade": "FAIL",
        "next_action": "fix_failures",
    }
    assert str(details["artifact_path"]).endswith(
        "runtime/llm_evaluations/run-public-adapter/AGENTIC_BENCHMARK_REPORT.json"
    )


def test_suite_result_public_summary_preserves_cli_contract() -> None:
    summary = _convert_suite_result_to_public_summary(_benchmark_result())

    assert summary == {
        "results": [
            {
                "case_id": "case-a",
                "passed": True,
                "score": 0.92,
                "duration_ms": 37,
                "error": "",
            }
        ],
        "summary": {
            "total": 2,
            "passed": 1,
            "failed": 1,
            "average_score": 0.46,
            "pass_rate": 0.5,
        },
        "run_id": "run-public-adapter",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "mode": "agentic",
    }


def test_agentic_case_adapter_accepts_persisted_judge_payload() -> None:
    judge_payload: dict[str, Any] = {
        "score_threshold": 0.8,
        "required_tools": ("read_file",),
        "forbidden_tools": ("write_file",),
        "required_tool_arguments": (
            {"fragment": "src/main.py", "tools": ("read_file",), "description": "read the target file"},
        ),
        "forbidden_tool_arguments": (),
        "min_tool_calls": 1,
        "max_tool_calls": 2,
        "required_output_substrings": ("analysis",),
        "forbidden_output_substrings": ("placeholder",),
        "validators": ("no_placeholder",),
    }
    case = AgenticBenchmarkCase(
        case_id="agentic-case",
        role="director",
        title="Director reads target",
        prompt="Inspect the target file.",
        description="Convert persisted payloads into unified benchmark cases.",
        workspace_fixture="",
        history=(),
        context={"task_id": "TASK-1"},
        metadata={"source": "test"},
        tags=("agentic",),
        judge=cast(AgenticJudgeConfig, judge_payload),
    )

    unified = _convert_agentic_case_to_unified(case)

    assert unified.case_id == "agentic-case"
    assert unified.role == "director"
    assert unified.context == {"task_id": "TASK-1"}
    assert unified.metadata == {"source": "test"}
    assert unified.judge.score_threshold == 0.8
    assert unified.judge.required_tools == ("read_file",)
    assert unified.judge.forbidden_tools == ("write_file",)
    assert unified.judge.required_tool_arguments[0].fragment == "src/main.py"
    assert unified.judge.required_tool_arguments[0].tools == ("read_file",)
    assert unified.judge.min_tool_calls == 1
    assert unified.judge.max_tool_calls == 2
