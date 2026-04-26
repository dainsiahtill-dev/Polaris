from __future__ import annotations

import pytest
from polaris.cells.llm.dialogue.internal.docs_suggest import generate_docs_fields, generate_docs_fields_stream
from polaris.kernelone.llm.engine.contracts import AIResponse, AIStreamEvent


class _SequenceAIExecutor:
    responses: list[AIResponse] = []

    def __init__(self, workspace: str | None = None) -> None:
        self.workspace = workspace

    async def invoke(self, request):
        assert self.responses, "expected queued AI responses"
        return self.responses.pop(0)


class _PreviewStreamExecutor:
    def __init__(self, workspace: str | None = None) -> None:
        self.workspace = workspace

    async def invoke_stream(self, request):
        yield AIStreamEvent.reasoning_event("first-thought")
        yield AIStreamEvent.chunk_event("not-json-at-all")
        yield AIStreamEvent.complete({"output": "not-json-at-all"})


@pytest.mark.asyncio
async def test_generate_docs_fields_repairs_non_json_output(monkeypatch):
    monkeypatch.setattr("polaris.cells.llm.dialogue.internal.docs_suggest.AIExecutor", _SequenceAIExecutor)
    _SequenceAIExecutor.responses = [
        AIResponse.success("preface before malformed output"),
        AIResponse.success(
            """
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
            """
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
async def test_generate_docs_fields_stream_falls_back_after_parse_failure(monkeypatch):
    monkeypatch.setattr("polaris.cells.llm.dialogue.internal.docs_suggest.StreamExecutor", _PreviewStreamExecutor)
    monkeypatch.setattr("polaris.cells.llm.dialogue.internal.docs_suggest.AIExecutor", _SequenceAIExecutor)
    _SequenceAIExecutor.responses = [AIResponse.success("still not valid json")]

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
