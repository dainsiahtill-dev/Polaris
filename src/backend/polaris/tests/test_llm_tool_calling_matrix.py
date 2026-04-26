from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from polaris.cells.llm.evaluation.internal.tool_calling_matrix import (
    load_builtin_tool_calling_matrix_cases,
    run_tool_calling_matrix_suite,
)
from polaris.kernelone.storage import resolve_runtime_path


class _FakeMatrixExecutor:
    def __init__(
        self,
        *,
        stream_plans: dict[str, list[dict[str, Any]]],
        non_stream_results: dict[str, dict[str, Any]],
    ) -> None:
        self._stream_plans = stream_plans
        self._non_stream_results = non_stream_results
        self.stream_commands: list[Any] = []
        self.non_stream_commands: list[Any] = []

    def stream_session(self, command):
        self.stream_commands.append(command)
        case_id = str((command.metadata or {}).get("matrix_case_id", ""))
        events = [dict(item) for item in self._stream_plans.get(case_id, [])]

        async def _generator():
            for event in events:
                yield event

        return _generator()

    async def run_session(self, command):
        self.non_stream_commands.append(command)
        case_id = str((command.metadata or {}).get("matrix_case_id", ""))
        return dict(self._non_stream_results.get(case_id, {}))


class _FailingStreamExecutor(_FakeMatrixExecutor):
    def stream_session(self, command):
        self.stream_commands.append(command)

        async def _generator():
            raise RuntimeError("stream exploded")
            yield  # pragma: no cover - async generator shape guard

        return _generator()


def _local_tmp_dir(label: str) -> Path:
    path = Path("tmp_pytest_agentic_eval_local") / f"{label}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_load_tool_calling_matrix_cases_filtering() -> None:
    all_cases = load_builtin_tool_calling_matrix_cases(role="all")
    selected = load_builtin_tool_calling_matrix_cases(
        role="director",
        case_ids=["l1_single_tool_accuracy", "l6_adversarial_boundary"],
    )

    assert len(all_cases) >= 7
    assert {case.case_id for case in selected} == {
        "l1_single_tool_accuracy",
        "l6_adversarial_boundary",
    }


def test_run_tool_calling_matrix_suite_persists_artifact() -> None:
    tmp_path = _local_tmp_dir("matrix-artifact")
    executor = _FakeMatrixExecutor(
        stream_plans={
            "l1_single_tool_accuracy": [
                {"type": "tool_call", "tool": "repo_read_head", "args": {"file": "src/utils/helpers.py", "n": 50}},
                {"type": "content_chunk", "content": "helpers excerpt"},
            ],
            "l4_zero_tool_irrelevance": [
                {"type": "content_chunk", "content": "Python 异步编程基于事件循环与协程。"},
            ],
        },
        non_stream_results={
            "l1_single_tool_accuracy": {
                "output": "helpers excerpt",
                "thinking": "",
                "tool_calls": ["repo_read_head"],
            },
            "l4_zero_tool_irrelevance": {
                "output": "异步编程使用事件循环调度协程任务。",
                "thinking": "",
                "tool_calls": [],
            },
        },
    )

    result = asyncio.run(
        run_tool_calling_matrix_suite(
            {},
            "fake-model",
            "benchmark",
            workspace=str(tmp_path),
            context={"provider_id": "runtime_binding", "role_session_executor": executor},
            options={"matrix_case_ids": ["l1_single_tool_accuracy", "l4_zero_tool_irrelevance"]},
        )
    )

    assert result["ok"] is True
    details = result["details"]
    assert details["passed_cases"] == 2
    artifact_path = Path(details["artifact_path"])
    assert artifact_path.is_file()

    with open(artifact_path, encoding="utf-8") as handle:
        artifact = json.load(handle)
    assert artifact["suite"] == "tool_calling_matrix"
    assert artifact["summary"]["passed_cases"] == 2


def test_tool_calling_matrix_suite_rejects_unknown_first_call_args() -> None:
    tmp_path = _local_tmp_dir("matrix-unknown-args")
    executor = _FakeMatrixExecutor(
        stream_plans={
            "l1_single_tool_accuracy": [
                {
                    "type": "tool_call",
                    "tool": "repo_read_head",
                    "args": {"file": "src/utils/helpers.py", "n": 50, "ghost_param": 1},
                },
                {"type": "content_chunk", "content": "helpers excerpt"},
            ],
        },
        non_stream_results={
            "l1_single_tool_accuracy": {
                "output": "helpers excerpt",
                "thinking": "",
                "tool_calls": ["repo_read_head"],
            },
        },
    )

    result = asyncio.run(
        run_tool_calling_matrix_suite(
            {},
            "fake-model",
            "benchmark",
            workspace=str(tmp_path),
            context={"provider_id": "runtime_binding", "role_session_executor": executor},
            options={"matrix_case_ids": ["l1_single_tool_accuracy"]},
        )
    )

    assert result["ok"] is False
    report = result["details"]["report"]
    checks = report["cases"][0]["judge"]["checks"]
    assert any(item["code"] == "stream:first_call_no_unknown_args" and item["passed"] is False for item in checks)


def test_tool_calling_matrix_suite_accepts_string_case_ids() -> None:
    tmp_path = _local_tmp_dir("matrix-string-case-id")
    executor = _FakeMatrixExecutor(
        stream_plans={
            "l4_zero_tool_irrelevance": [
                {"type": "content_chunk", "content": "异步编程依赖事件循环。"},
            ],
        },
        non_stream_results={
            "l4_zero_tool_irrelevance": {
                "output": "异步编程依赖事件循环。",
                "thinking": "",
                "tool_calls": [],
            },
        },
    )

    result = asyncio.run(
        run_tool_calling_matrix_suite(
            {},
            "fake-model",
            "benchmark",
            workspace=str(tmp_path),
            context={"provider_id": "runtime_binding", "role_session_executor": executor},
            options={"matrix_case_ids": "l4_zero_tool_irrelevance"},
        )
    )

    assert result["ok"] is True
    assert result["details"]["total_cases"] == 1
    assert result["details"]["passed_cases"] == 1


def test_tool_calling_matrix_suite_materializes_sandbox_under_runtime_root() -> None:
    tmp_path = _local_tmp_dir("matrix-runtime-sandbox")
    executor = _FakeMatrixExecutor(
        stream_plans={
            "l1_single_tool_accuracy": [
                {"type": "tool_call", "tool": "repo_read_head", "args": {"file": "src/utils/helpers.py", "n": 50}},
                {"type": "content_chunk", "content": "helpers excerpt"},
            ]
        },
        non_stream_results={},
    )

    result = asyncio.run(
        run_tool_calling_matrix_suite(
            {},
            "fake-model",
            "benchmark",
            workspace=str(tmp_path),
            context={"provider_id": "runtime_binding", "role_session_executor": executor},
            options={"matrix_case_ids": ["l1_single_tool_accuracy"], "matrix_transport": "stream"},
        )
    )

    assert result["ok"] is True
    report = result["details"]["report"]
    sandbox_workspace = str(report["cases"][0]["sandbox_workspace"])
    assert "/sandboxes/" in sandbox_workspace.replace("\\", "/")
    runtime_root = str(resolve_runtime_path(str(tmp_path), "runtime/llm_evaluations"))
    assert sandbox_workspace.startswith(runtime_root)


def test_tool_calling_matrix_suite_stream_only_disables_parity_requirement() -> None:
    tmp_path = _local_tmp_dir("matrix-stream-only")
    executor = _FakeMatrixExecutor(
        stream_plans={
            "l4_zero_tool_irrelevance": [
                {"type": "content_chunk", "content": "异步编程基于协程与事件循环。"},
            ],
        },
        non_stream_results={},
    )

    result = asyncio.run(
        run_tool_calling_matrix_suite(
            {},
            "fake-model",
            "benchmark",
            workspace=str(tmp_path),
            context={"provider_id": "runtime_binding", "role_session_executor": executor},
            options={
                "matrix_case_ids": ["l4_zero_tool_irrelevance"],
                "matrix_transport": "stream",
            },
        )
    )

    assert result["ok"] is True
    report = result["details"]["report"]
    assert report["summary"]["passed_cases"] == 1
    assert report["cases"][0]["non_stream_observed"] is None


def test_tool_calling_matrix_suite_handles_stream_collection_errors() -> None:
    tmp_path = _local_tmp_dir("matrix-stream-error")
    executor = _FailingStreamExecutor(stream_plans={}, non_stream_results={})

    result = asyncio.run(
        run_tool_calling_matrix_suite(
            {},
            "fake-model",
            "benchmark",
            workspace=str(tmp_path),
            context={"provider_id": "runtime_binding", "role_session_executor": executor},
            options={
                "matrix_case_ids": ["l4_zero_tool_irrelevance"],
                "matrix_transport": "stream",
            },
        )
    )

    assert result["ok"] is False
    report = result["details"]["report"]
    assert report["summary"]["total_cases"] == 1
    stream_observed = report["cases"][0]["stream_observed"] or {}
    assert "stream exploded" in str(stream_observed.get("error") or "")


def test_tool_calling_matrix_suite_emits_progress_events() -> None:
    tmp_path = _local_tmp_dir("matrix-progress")
    executor = _FakeMatrixExecutor(
        stream_plans={
            "l4_zero_tool_irrelevance": [
                {"type": "content_chunk", "content": "异步编程依赖事件循环。"},
            ],
        },
        non_stream_results={},
    )
    progress_events: list[dict[str, Any]] = []

    result = asyncio.run(
        run_tool_calling_matrix_suite(
            {},
            "fake-model",
            "benchmark",
            workspace=str(tmp_path),
            context={
                "provider_id": "runtime_binding",
                "role_session_executor": executor,
                "progress_callback": lambda event: progress_events.append(dict(event)),
            },
            options={
                "matrix_case_ids": ["l4_zero_tool_irrelevance"],
                "matrix_transport": "stream",
            },
        )
    )

    assert result["ok"] is True
    assert [event["type"] for event in progress_events] == [
        "suite_started",
        "case_started",
        "phase_started",
        "case_completed",
        "suite_completed",
    ]
    assert progress_events[1]["case_id"] == "l4_zero_tool_irrelevance"
    assert progress_events[2]["phase"] == "stream"


def test_tool_calling_matrix_suite_collects_post_handoff_stream_events() -> None:
    """Verify that _collect_stream_observation captures ExplorationWorkflowRuntime events.

    This simulates the exact event structure produced by kernel/core.py when
    ExplorationWorkflowRuntime.execute_stream() yields ToolBatchEvents after a
    handoff_workflow transition.
    """
    tmp_path = _local_tmp_dir("matrix-handoff-stream")
    executor = _FakeMatrixExecutor(
        stream_plans={
            "l3_search_then_read": [
                # ToolBatchEvent(status="started") maps to tool_call in kernel/core.py
                {
                    "type": "tool_call",
                    "tool": "repo_rg",
                    "args": {"path": "src", "query": "API_HOST"},
                    "call_id": "c1",
                    "status": "started",
                },
                # ToolBatchEvent(status="success") maps to tool_result in kernel/core.py
                {
                    "type": "tool_result",
                    "tool": "repo_rg",
                    "args": {"path": "src", "query": "API_HOST"},
                    "call_id": "c1",
                    "status": "success",
                    "result": {"results": [{"path": "src/config.py"}]},
                },
                {
                    "type": "tool_call",
                    "tool": "read_file",
                    "args": {"path": "src/config.py"},
                    "call_id": "c2",
                    "status": "started",
                },
                {
                    "type": "tool_result",
                    "tool": "read_file",
                    "args": {"path": "src/config.py"},
                    "call_id": "c2",
                    "status": "success",
                    "result": {"content": "API_HOST=127.0.0.1"},
                },
                {"type": "content_chunk", "content": "The API_HOST is 127.0.0.1"},
            ],
        },
        non_stream_results={
            "l3_search_then_read": {
                "output": "The API_HOST is 127.0.0.1",
                "thinking": "",
                "tool_calls": ["repo_rg", "read_file"],
            },
        },
    )

    result = asyncio.run(
        run_tool_calling_matrix_suite(
            {},
            "fake-model",
            "director",
            workspace=str(tmp_path),
            context={"provider_id": "runtime_binding", "role_session_executor": executor},
            options={
                "matrix_case_ids": ["l3_search_then_read"],
                "matrix_transport": "stream",
            },
        )
    )

    assert result["ok"] is True
    report = result["details"]["report"]
    assert report["summary"]["passed_cases"] == 1
    case_report = report["cases"][0]
    stream_observed = case_report["stream_observed"] or {}
    # Only tool_call events (started) are counted; tool_result events are not
    assert len(stream_observed["tool_calls"]) == 2
    assert "API_HOST" in str(stream_observed.get("output") or "")
    assert "127.0.0.1" in str(stream_observed.get("output") or "")
