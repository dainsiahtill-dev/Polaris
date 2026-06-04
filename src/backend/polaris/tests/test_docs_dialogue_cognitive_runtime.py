from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.llm.dialogue.internal import docs_dialogue
from polaris.cells.llm.provider_runtime.public.service import CellAIResponse


class _DialogueAIExecutor:
    responses: list[CellAIResponse] = []
    requests: list[Any] = []

    def __init__(self, workspace: str | None = None) -> None:
        self.workspace = workspace

    async def invoke(self, request: Any) -> CellAIResponse:
        self.requests.append(request)
        assert self.responses, "expected queued AI responses"
        return self.responses.pop(0)


class _FakeSnapshot:
    workspace = "."
    role = "architect"
    run_id = "docs_dialogue"
    session_id = "docs-dialogue-session"
    mode = "docs_dialogue"
    token_usage_estimate = 91
    source_refs = ("runtime/events/dialogue.transcript.jsonl",)
    context_os_summary = {"state_first_context_os": True, "projection": "dialogue"}
    rendered_prompt = "Existing council context: PM requires auditable tasks and integration evidence."


class _FakeResolveResult:
    ok = True
    snapshot = _FakeSnapshot()


class _FakeReceipt:
    receipt_id = "receipt-docs-dialogue"


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
async def test_generate_dialogue_turn_uses_context_os_prompt_and_records_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_runtime = _FakeCognitiveRuntimeService()
    monkeypatch.setattr(docs_dialogue, "CellAIExecutor", _DialogueAIExecutor)
    monkeypatch.setattr(
        "polaris.cells.llm.dialogue.internal.cognitive_evidence._get_cognitive_runtime_public_service",
        lambda: fake_runtime,
    )
    _DialogueAIExecutor.requests = []
    _DialogueAIExecutor.responses = [
        CellAIResponse(
            ok=True,
            output="""
            {
              "reply": "已补充验收证据要求",
              "questions": [],
              "tiaochen": ["建立审计闭环"],
              "fields": {
                "goal": ["生成复杂全栈项目"],
                "in_scope": ["PM/CE/Director/QA 全链路"],
                "out_of_scope": ["跳过验收"],
                "constraints": ["保留 Context OS 证据"],
                "definition_of_done": ["E2E 通过"],
                "backlog": ["拆分任务"]
              },
              "meta": {"phase": "ready_for_draft", "answered_slots": [], "unresolved_slots": []},
              "handoffs": {"pm": ["拆分任务"], "director": ["实现代码"]}
            }
            """,
        )
    ]

    result = await docs_dialogue.generate_dialogue_turn(
        workspace=".",
        settings=None,  # type: ignore[arg-type]
        fields={"goal": "生成复杂全栈项目"},
        history=[],
        message="请补齐可验收的规划",
    )

    assert result is not None
    assert _DialogueAIExecutor.requests
    assert "Context OS grounding" in _DialogueAIExecutor.requests[0].input
    assert "Existing council context" in _DialogueAIExecutor.requests[0].input
    assert fake_runtime.resolve_commands
    assert fake_runtime.resolve_commands[0].mode == "docs_dialogue"
    assert fake_runtime.receipt_commands
    receipt_command = fake_runtime.receipt_commands[0]
    assert receipt_command.receipt_type == "llm_docs_dialogue"
    assert receipt_command.payload["context_os"]["ok"] is True
    assert result["meta"]["cognitive_runtime"]["ok"] is True
    assert result["meta"]["cognitive_runtime"]["receipt_ok"] is True
