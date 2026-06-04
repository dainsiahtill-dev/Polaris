from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.llm.dialogue.internal.docs_suggest import generate_docs_fields, generate_docs_fields_stream
from polaris.cells.llm.provider_runtime.public.service import CellAIResponse


class _DocsAIExecutor:
    responses: list[CellAIResponse] = []
    stream_events: list[dict[str, object]] = []
    requests: list[Any] = []

    def __init__(self, workspace: str | None = None) -> None:
        self.workspace = workspace

    async def invoke(self, request):
        self.requests.append(request)
        assert self.responses, "expected queued AI responses"
        return self.responses.pop(0)

    async def invoke_stream(self, request):
        self.requests.append(request)
        assert self.stream_events, "expected queued stream events"
        for event in self.stream_events:
            yield event


class _FakeSnapshot:
    workspace = "."
    role = "architect"
    run_id = "docs_suggest"
    session_id = "docs-suggest-session"
    mode = "docs_suggest"
    token_usage_estimate = 77
    source_refs = ("runtime/contracts/project.json",)
    context_os_summary = {"state_first_context_os": True, "projection": "docs"}
    rendered_prompt = "Existing runtime facts: repository already has a Vite frontend and Python backend."


class _FakeResolveResult:
    ok = True
    snapshot = _FakeSnapshot()


class _FakeReceipt:
    receipt_id = "receipt-docs-suggest"


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


@pytest.mark.asyncio
async def test_generate_docs_fields_repairs_non_json_output(monkeypatch):
    monkeypatch.setattr("polaris.cells.llm.dialogue.internal.docs_suggest.CellAIExecutor", _DocsAIExecutor)
    _DocsAIExecutor.requests = []
    _DocsAIExecutor.responses = [
        CellAIResponse(ok=True, output="preface before malformed output"),
        CellAIResponse(
            ok=True,
            output="""
            {
              "fields": {
                "goal": ["修复后的目标"],
                "in_scope": ["修复后的范围"],
                "out_of_scope": ["修复后的排除项"],
                "constraints": ["修复后的约束"],
                "definition_of_done": ["修复后的完成定义"],
                "backlog": ["修复后的任务"]
              }
            }
            """,
        ),
    ]

    result = await generate_docs_fields(
        workspace=".",
        settings=None,  # type: ignore[arg-type]
        fields={"goal": "原始目标"},
    )

    assert result == {
        "goal": ["修复后的目标"],
        "in_scope": ["修复后的范围"],
        "out_of_scope": ["修复后的排除项"],
        "constraints": ["修复后的约束"],
        "definition_of_done": ["修复后的完成定义"],
        "backlog": ["修复后的任务"],
    }


@pytest.mark.asyncio
async def test_generate_docs_fields_uses_context_os_prompt_and_records_receipt(monkeypatch):
    fake_runtime = _FakeCognitiveRuntimeService()
    monkeypatch.setattr("polaris.cells.llm.dialogue.internal.docs_suggest.CellAIExecutor", _DocsAIExecutor)
    monkeypatch.setattr(
        "polaris.cells.llm.dialogue.internal.cognitive_evidence._get_cognitive_runtime_public_service",
        lambda: fake_runtime,
    )
    _DocsAIExecutor.requests = []
    _DocsAIExecutor.responses = [
        CellAIResponse(
            ok=True,
            output="""
            {
              "goal": ["生成可审计项目"],
              "in_scope": ["利用现有前后端结构"],
              "out_of_scope": ["不重写平台"],
              "constraints": ["保留运行时证据"],
              "definition_of_done": ["存在端到端验收"],
              "backlog": ["建立最小功能闭环"]
            }
            """,
        )
    ]

    result = await generate_docs_fields(
        workspace=".",
        settings=None,  # type: ignore[arg-type]
        fields={"goal": "生成复杂全栈项目"},
    )

    assert result is not None
    assert result["goal"] == ["生成可审计项目"]
    assert _DocsAIExecutor.requests
    prompt = _DocsAIExecutor.requests[0].input
    assert "Context OS grounding" in prompt
    assert "Existing runtime facts" in prompt
    assert fake_runtime.resolve_commands
    assert fake_runtime.resolve_commands[0].mode == "docs_suggest"
    assert fake_runtime.receipt_commands
    receipt_command = fake_runtime.receipt_commands[0]
    assert receipt_command.receipt_type == "llm_docs_suggest"
    assert receipt_command.payload["context_os"]["ok"] is True
    assert receipt_command.payload["llm"]["ok"] is True


@pytest.mark.asyncio
async def test_generate_docs_fields_stream_falls_back_after_parse_failure(monkeypatch):
    monkeypatch.setattr("polaris.cells.llm.dialogue.internal.docs_suggest.CellAIExecutor", _DocsAIExecutor)
    _DocsAIExecutor.requests = []
    _DocsAIExecutor.responses = [CellAIResponse(ok=True, output="still not valid json")]
    _DocsAIExecutor.stream_events = [
        {"type": "reasoning_chunk", "reasoning": "first-thought"},
        {"type": "chunk", "chunk": "not-json-at-all"},
        {"type": "complete", "meta": {"output": "not-json-at-all"}},
    ]

    events = [
        event
        async for event in generate_docs_fields_stream(
            workspace=".",
            settings=None,  # type: ignore[arg-type]
            fields={"goal": "构建企业级多租户任务管理系统"},
        )
    ]

    assert events[0] == {"type": "thinking", "content": "first-thought"}
    assert events[-1]["type"] == "result"
    assert events[-1].get("fallback") is True
    assert events[-1]["fields"]["goal"] == ["构建企业级多租户任务管理系统"]
    assert "backlog" in events[-1]["fields"]
    assert "cognitive_runtime" in events[-1]
