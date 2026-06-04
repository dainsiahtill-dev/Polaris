from __future__ import annotations

from typing import Any, AsyncGenerator

import pytest
from polaris.cells.factory.cognitive_runtime.public import (
    RecordRuntimeReceiptCommandV1,
    ResolveContextCommandV1,
)
from polaris.cells.llm.evaluation.internal import interview
from polaris.cells.llm.provider_runtime.public.service import CellAIResponse


class _InterviewAIExecutor:
    responses: list[CellAIResponse] = []
    stream_events: list[dict[str, object]] = []
    requests: list[Any] = []

    def __init__(self, workspace: str | None = None) -> None:
        self.workspace = workspace

    async def invoke(self, request: Any) -> CellAIResponse:
        self.requests.append(request)
        assert self.responses, "expected queued AI response"
        return self.responses.pop(0)

    async def invoke_stream(self, request: Any) -> AsyncGenerator[dict[str, object], None]:
        self.requests.append(request)
        assert self.stream_events, "expected queued stream events"
        for event in self.stream_events:
            yield event


class _FakeSnapshot:
    workspace = "."
    role = "Director"
    run_id = "llm_interview"
    session_id = "interview-session"
    mode = "llm_interview"
    token_usage_estimate = 88
    source_refs = ("runtime/llm_tests/interview.context.json",)
    context_os_summary = {"state_first_context_os": True, "projection": "interview"}
    rendered_prompt = "Interview runtime context: the target project requires PM/CE/Director/QA evidence."


class _FakeResolveResult:
    ok = True
    snapshot = _FakeSnapshot()


class _FakeReceipt:
    receipt_id = "receipt-llm-interview"


class _FakeReceiptResult:
    ok = True
    receipt = _FakeReceipt()


class _FakeCognitiveRuntimeService:
    def __init__(self) -> None:
        self.resolve_commands: list[Any] = []
        self.receipt_commands: list[Any] = []

    def resolve_context(self, command: Any) -> _FakeResolveResult:
        self.resolve_commands.append(command)
        return _FakeResolveResult()

    def record_runtime_receipt(self, command: Any) -> _FakeReceiptResult:
        self.receipt_commands.append(command)
        return _FakeReceiptResult()


def _patch_runtime(monkeypatch: pytest.MonkeyPatch) -> _FakeCognitiveRuntimeService:
    fake_runtime = _FakeCognitiveRuntimeService()
    monkeypatch.setattr(interview, "CellAIExecutor", _InterviewAIExecutor)
    monkeypatch.setattr(
        interview,
        "_get_cognitive_runtime_public",
        lambda: (ResolveContextCommandV1, RecordRuntimeReceiptCommandV1, lambda: fake_runtime),
    )
    _InterviewAIExecutor.requests = []
    _InterviewAIExecutor.responses = []
    _InterviewAIExecutor.stream_events = []
    return fake_runtime


@pytest.mark.asyncio
async def test_generate_interview_answer_uses_context_os_prompt_and_records_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_runtime = _patch_runtime(monkeypatch)
    _InterviewAIExecutor.responses = [
        CellAIResponse(
            ok=True,
            output="<thinking>检查风险</thinking>我会按 PM/CE/Director/QA 证据链执行，并保留验收记录。",
        )
    ]

    result = await interview.generate_interview_answer(
        workspace=".",
        settings=None,  # type: ignore[arg-type]
        role="Director",
        question="你如何实现可审计任务？",
        criteria=["证据链", "可测试"],
        project_path="C:/Temp/TargetProject",
    )

    assert result is not None
    assert _InterviewAIExecutor.requests
    assert "Context OS grounding" in _InterviewAIExecutor.requests[0].input
    assert "Interview runtime context" in _InterviewAIExecutor.requests[0].input
    assert fake_runtime.resolve_commands
    assert fake_runtime.resolve_commands[0].mode == "llm_interview"
    assert fake_runtime.receipt_commands
    receipt_command = fake_runtime.receipt_commands[0]
    assert receipt_command.receipt_type == "llm_interview"
    assert receipt_command.payload["context_os"]["ok"] is True
    assert receipt_command.payload["llm"]["task_type"] == "interview"
    assert result["cognitive_runtime"]["receipt_ok"] is True


@pytest.mark.asyncio
async def test_generate_interview_answer_streaming_emits_cognitive_runtime_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_runtime = _patch_runtime(monkeypatch)
    output_queue: Any = __import__("asyncio").Queue()
    _InterviewAIExecutor.stream_events = [
        {"type": "chunk", "chunk": "我会生成计划、蓝图、代码和测试证据。"},
        {"type": "complete"},
    ]

    await interview.generate_interview_answer_streaming(
        workspace=".",
        settings=None,  # type: ignore[arg-type]
        role="Director",
        question="如何保证自动开发质量？",
        output_queue=output_queue,
        criteria=["质量"],
    )

    events: list[dict[str, Any]] = []
    while not output_queue.empty():
        events.append(output_queue.get_nowait())

    assert events[-1]["type"] == "complete"
    cognitive_runtime = events[-1]["data"]["cognitive_runtime"]
    assert cognitive_runtime["ok"] is True
    assert cognitive_runtime["receipt_ok"] is True
    assert fake_runtime.receipt_commands
    assert fake_runtime.receipt_commands[0].payload["llm"]["streaming"] is True
