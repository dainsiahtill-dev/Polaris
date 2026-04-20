from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from polaris.bootstrap.config import Settings
from polaris.cells.llm.evaluation.internal.agentic_benchmark import run_agentic_benchmark_suite
from polaris.cells.llm.evaluation.internal.benchmark_loader import (
    load_builtin_agentic_benchmark_cases,
    materialize_case_workspace,
)
from polaris.cells.llm.evaluation.internal.benchmark_models import (
    ObservedBenchmarkRun,
    ToolCallObservation,
)
from polaris.cells.llm.evaluation.internal.deterministic_judge import judge_agentic_case
from polaris.cells.llm.evaluation.internal.runner import EvaluationRequest
from polaris.cells.llm.evaluation.public.service import EvaluationRunner


class _FakeStreamExecutor:
    def __init__(self, plans: dict[str, list[dict[str, Any]]]) -> None:
        self._plans = plans
        self.commands = []

    def stream_session(self, command):  # noqa: ANN001
        self.commands.append(command)
        metadata = dict(command.metadata or {})
        case_id = str(metadata.get("benchmark_case_id"))
        events = [dict(item) for item in self._plans[case_id]]

        async def _generator():
            for item in events:
                yield item

        return _generator()


def _local_tmp_dir(label: str) -> Path:
    path = Path("tmp_pytest_agentic_eval_local") / f"{label}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_load_builtin_agentic_benchmark_cases_filters_by_role() -> None:
    all_cases = load_builtin_agentic_benchmark_cases(role="all")
    director_cases = load_builtin_agentic_benchmark_cases(role="director")

    assert len(all_cases) >= 6
    director_case_ids = {case.case_id for case in director_cases}
    assert {"director_root_cause_locator", "director_safe_scope_plan"}.issubset(director_case_ids)
    assert all(case.role == "director" for case in director_cases)


def test_deterministic_judge_rejects_forbidden_write_scope() -> None:
    tmp_path = _local_tmp_dir("judge-scope")
    case = next(
        item
        for item in load_builtin_agentic_benchmark_cases(role="director")
        if item.case_id == "director_safe_scope_plan"
    )
    sandbox_workspace = materialize_case_workspace(
        base_workspace=str(tmp_path),
        run_id="judge-scope",
        case=case,
    )
    observed = ObservedBenchmarkRun(
        case_id=case.case_id,
        role=case.role,
        workspace=sandbox_workspace,
        output="1. Change docs/README.md\n2. Verify later",
        tool_calls=(
            ToolCallObservation(tool="read_file", args={"path": "src/app.py"}, event_index=0),
            ToolCallObservation(tool="write_file", args={"path": "docs/README.md"}, event_index=1),
        ),
        duration_ms=12,
        event_count=2,
    )

    verdict = judge_agentic_case(case, observed)

    assert verdict.passed is False
    assert any(check.code.startswith("forbidden_tool_argument:") and check.passed is False for check in verdict.checks)


def test_deterministic_judge_flags_textual_tool_protocol_without_trace() -> None:
    tmp_path = _local_tmp_dir("judge-textual-tool")
    case = next(
        item
        for item in load_builtin_agentic_benchmark_cases(role="architect")
        if item.case_id == "architect_graph_first_boundary"
    )
    sandbox_workspace = materialize_case_workspace(
        base_workspace=str(tmp_path),
        run_id="judge-textual-tool",
        case=case,
    )
    observed = ObservedBenchmarkRun(
        case_id=case.case_id,
        role=case.role,
        workspace=sandbox_workspace,
        output=(
            "I will inspect the graph first.\n"
            "[TOOL_CALL]\n"
            '{tool => "read_file", args => {"path": "docs/graph/catalog/cells.yaml"}}\n'
            "[/TOOL_CALL]"
        ),
        tool_calls=(),
        duration_ms=8,
        event_count=1,
    )

    verdict = judge_agentic_case(case, observed)
    target_check = next(check for check in verdict.checks if check.code == "textual_tool_protocol_without_trace")

    assert target_check.passed is False
    assert "[TOOL_CALL]" in list(target_check.evidence.get("markers") or [])


def test_run_agentic_benchmark_suite_persists_artifact() -> None:
    tmp_path = _local_tmp_dir("benchmark-artifact")
    executor = _FakeStreamExecutor(
        {
            "pm_task_contract": [
                {"type": "fingerprint", "profile_id": "pm-profile", "run_id": "case-run"},
                {"type": "tool_call", "tool": "read_file", "args": {"path": "README.md"}},
                {
                    "type": "content_chunk",
                    "content": json.dumps(
                        {
                            "goal": "Ship benchmark runner",
                            "backlog": ["define judge", "persist report"],
                            "timeline": "week 1",
                        },
                        ensure_ascii=False,
                    ),
                },
                {"type": "complete", "result": SimpleNamespace(content="", thinking="")},
            ],
            "director_root_cause_locator": [
                {"type": "fingerprint", "profile_id": "director-profile", "run_id": "case-run"},
                {"type": "tool_call", "tool": "search_code", "args": {"query": "median"}},
                {"type": "tool_call", "tool": "read_file", "args": {"path": "src/median.py"}},
                {"type": "tool_call", "tool": "read_file", "args": {"path": "tests/test_median.py"}},
                {
                    "type": "content_chunk",
                    "content": (
                        "The root cause is in src/median.py: the empty list branch returns 0 "
                        "instead of raising ValueError."
                    ),
                },
            ],
        }
    )

    result = asyncio.run(
        run_agentic_benchmark_suite(
            {},
            "fake-model",
            "benchmark",
            workspace=str(tmp_path),
            context={"role_session_executor": executor},
            options={"benchmark_case_ids": ["pm_task_contract", "director_root_cause_locator"]},
        )
    )

    assert result["ok"] is True
    details = result["details"]
    assert details["passed_cases"] == 2
    artifact_path = Path(details["artifact_path"])
    assert artifact_path.is_file()

    with open(artifact_path, encoding="utf-8") as handle:
        artifact = json.load(handle)

    assert artifact["suite"] == "agentic_benchmark"
    assert artifact["summary"]["passed_cases"] == 2
    assert len(executor.commands) == 2
    assert "benchmark_case_id" in executor.commands[0].metadata
    assert all("prompt_appendix" in command.metadata for command in executor.commands)
    assert all("Benchmark acceptance contract:" in command.metadata["prompt_appendix"] for command in executor.commands)
    assert any("README.md" in command.metadata["prompt_appendix"] for command in executor.commands)
    assert any(
        "Final answer must be exactly one JSON object" in command.metadata["prompt_appendix"]
        for command in executor.commands
    )
    assert "benchmark_case_id" not in executor.commands[0].context


def test_run_agentic_benchmark_suite_accepts_string_case_id() -> None:
    tmp_path = _local_tmp_dir("benchmark-case-id")
    executor = _FakeStreamExecutor(
        {
            "qa_release_verdict": [
                {"type": "fingerprint", "profile_id": "qa-profile", "run_id": "case-run"},
                {"type": "tool_call", "tool": "read_file", "args": {"path": "README.md"}},
                {
                    "type": "content_chunk",
                    "content": json.dumps(
                        {"passed": True, "findings": ["release note verified"]},
                        ensure_ascii=False,
                    ),
                },
            ]
        }
    )

    result = asyncio.run(
        run_agentic_benchmark_suite(
            {},
            "fake-model",
            "qa",
            workspace=str(tmp_path),
            context={"role_session_executor": executor},
            options={"benchmark_case_ids": "qa_release_verdict"},
        )
    )

    assert result["ok"] is True
    assert result["details"]["passed_cases"] == 1
    assert len(executor.commands) == 1
    assert executor.commands[0].metadata["benchmark_case_id"] == "qa_release_verdict"


def test_benchmark_appendix_strengthens_structured_steps_contract() -> None:
    tmp_path = _local_tmp_dir("benchmark-appendix-steps")
    executor = _FakeStreamExecutor(
        {
            "architect_graph_first_boundary": [
                {"type": "fingerprint", "profile_id": "architect-profile", "run_id": "case-run"},
                {"type": "tool_call", "tool": "read_file", "args": {"path": "docs/graph/catalog/cells.yaml"}},
                {
                    "type": "content_chunk",
                    "content": "1. Inspect graph\n2. Inspect cell manifest\n3. Name `polaris/cells/llm/evaluation`",
                },
            ]
        }
    )

    asyncio.run(
        run_agentic_benchmark_suite(
            {},
            "fake-model",
            "architect",
            workspace=str(tmp_path),
            context={"role_session_executor": executor},
            options={"benchmark_case_ids": ["architect_graph_first_boundary"]},
        )
    )

    appendix = str(executor.commands[0].metadata.get("prompt_appendix") or "")
    assert "Final answer must start directly with `1.`" in appendix
    assert "Do not repeat the final answer." in appendix
    assert "put `docs/graph/catalog/cells.yaml` literally inside `file`" in appendix


def test_run_agentic_benchmark_suite_retries_failed_case_with_repair_appendix() -> None:
    tmp_path = _local_tmp_dir("benchmark-repair-retry")

    class _RetryingExecutor:
        def __init__(self) -> None:
            self.commands: list[Any] = []
            self._attempts = 0

        def stream_session(self, command):  # noqa: ANN001
            self.commands.append(command)
            self._attempts += 1

            async def _generator():
                if self._attempts == 1:
                    yield {"type": "fingerprint", "profile_id": "qa-profile", "run_id": "case-run"}
                    yield {"type": "content_chunk", "content": "not json"}
                    return
                yield {"type": "fingerprint", "profile_id": "qa-profile", "run_id": "case-run"}
                yield {"type": "tool_call", "tool": "read_file", "args": {"file": "README.md"}}
                yield {
                    "type": "content_chunk",
                    "content": json.dumps(
                        {"passed": True, "findings": ["release note verified"]},
                        ensure_ascii=False,
                    ),
                }

            return _generator()

    executor = _RetryingExecutor()

    result = asyncio.run(
        run_agentic_benchmark_suite(
            {},
            "fake-model",
            "qa",
            workspace=str(tmp_path),
            context={"role_session_executor": executor},
            options={"benchmark_case_ids": ["qa_release_verdict"], "benchmark_repair_attempts": 1},
        )
    )

    assert result["ok"] is True
    assert result["details"]["passed_cases"] == 1
    assert len(executor.commands) == 2
    second_appendix = str(executor.commands[1].metadata.get("prompt_appendix") or "")
    assert "Benchmark repair contract:" in second_appendix
    assert "Return exactly one JSON object with keys `passed` and `findings`." in second_appendix


def test_run_agentic_benchmark_suite_emits_progress_events() -> None:
    tmp_path = _local_tmp_dir("benchmark-progress")
    executor = _FakeStreamExecutor(
        {
            "qa_release_verdict": [
                {"type": "fingerprint", "profile_id": "qa-profile", "run_id": "case-run"},
                {"type": "tool_call", "tool": "read_file", "args": {"path": "README.md"}},
                {
                    "type": "content_chunk",
                    "content": json.dumps(
                        {"passed": True, "findings": ["release note verified"]},
                        ensure_ascii=False,
                    ),
                },
            ]
        }
    )
    progress_events: list[dict[str, Any]] = []

    result = asyncio.run(
        run_agentic_benchmark_suite(
            {},
            "fake-model",
            "qa",
            workspace=str(tmp_path),
            context={
                "role_session_executor": executor,
                "progress_callback": lambda event: progress_events.append(dict(event)),
            },
            options={"benchmark_case_ids": ["qa_release_verdict"]},
        )
    )

    assert result["ok"] is True
    assert [event["type"] for event in progress_events] == [
        "suite_started",
        "case_started",
        "case_completed",
        "suite_completed",
    ]
    assert progress_events[1]["case_id"] == "qa_release_verdict"
    assert progress_events[2]["passed"] is True


def test_evaluation_runner_supports_agentic_benchmark_suite() -> None:
    tmp_path = _local_tmp_dir("benchmark-runner")
    settings = Settings(workspace=str(tmp_path), ramdisk_root="")
    runner = EvaluationRunner(workspace=str(tmp_path), settings=settings)
    executor = _FakeStreamExecutor(
        {
            "qa_release_verdict": [
                {"type": "fingerprint", "profile_id": "qa-profile", "run_id": "case-run"},
                {"type": "tool_call", "tool": "read_file", "args": {"path": "README.md"}},
                {
                    "type": "content_chunk",
                    "content": json.dumps(
                        {"passed": False, "findings": ["unit test still failing"]},
                        ensure_ascii=False,
                    ),
                },
            ]
        }
    )
    request = EvaluationRequest(
        provider_id="runtime",
        model="fake-model",
        role="qa",
        suites=["agentic_benchmark"],
        context={"role_session_executor": executor},
        options={"benchmark_case_ids": ["qa_release_verdict"], "update_index": False},
    )

    report = asyncio.run(runner.run(request))

    assert report.summary["ready"] is True
    assert report.suites[0].suite_name == "agentic_benchmark"
    assert report.suites[0].passed_cases == 1
