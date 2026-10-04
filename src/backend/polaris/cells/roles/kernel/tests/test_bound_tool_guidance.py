"""Generated transaction guidance must match the final callable tool surface."""

from __future__ import annotations

import re
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.decision_message_builder import build_decision_messages
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import StreamOrchestrator
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import build_single_batch_task_contract_hint
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TurnTransactionController

_TOOL_NAMES = {
    "read_file",
    "repo_read_head",
    "repo_read_slice",
    "repo_read_tail",
    "repo_read_around",
    "repo_tree",
    "repo_rg",
    "glob",
    "ripgrep",
    "edit_file",
    "edit_blocks",
    "write_file",
    "search_replace",
    "repo_apply_diff",
    "append_to_file",
    "create_file",
    "execute_command",
}
_REPO_FACTS = "【仓库身份】(确定性扫描结果,以下为事实而非推测)\n- 顶层条目: src/, package.json\n"
_OLD_DISCOVERY = "- 路径不存在时,用 repo_rg 搜索符号或 repo_tree 浏览;不要按其它项目的惯例假设文件存在。"


def _tools(*names: str) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}} for name in names]


def _context(*, single_batch: bool = True, required: list[str] | None = None) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "You are Polaris Director. Preserve original role and scope."},
        {"role": "system", "name": "repo_identity", "content": _REPO_FACTS + _OLD_DISCOVERY},
        {
            "role": "user",
            "content": "Create src/main.ts from the supplied plan.",
            "metadata": {"tool_contract": {"single_batch": single_batch, "required_tools": required or []}},
        },
    ]


def _guidance(messages: list[dict[str, Any]]) -> str:
    # Role identity and the original user request are not mutable generic guidance.
    return "\n".join(str(m.get("content", "")) for m in messages[1:] if m.get("role") == "system")


def _mentioned_tools(text: str) -> set[str]:
    return {name for name in _TOOL_NAMES if re.search(r"\b" + re.escape(name) + r"\b", text)}


def test_write_only_materialize_assembly_never_reintroduces_unavailable_recovery_tools() -> None:
    # Break caught: positive sanitizer adds read/edit names after a correctly narrowed schema.
    context = _context()
    before = deepcopy(context)
    tools = _tools("write_file")
    schema_before = deepcopy(tools)
    ledger = SimpleNamespace(delivery_contract=SimpleNamespace(mode=DeliveryMode.MATERIALIZE_CHANGES))
    messages = build_decision_messages(context, tools, ledger=ledger)  # type: ignore[arg-type]
    guidance = _guidance(messages)
    assert _mentioned_tools(guidance) <= {"write_file"}
    assert "failure" in guidance.lower()
    assert "no subsequent" in guidance.lower() or "no further" in guidance.lower()
    assert "never claim completion" in guidance.lower() or "do not claim completion" in guidance.lower()
    assert messages[0] == before[0]
    assert next(m for m in messages if m.get("role") == "user") == before[-1]
    identity = next(m for m in messages if m.get("name") == "repo_identity")
    assert identity["content"].startswith(_REPO_FACTS)
    assert context == before and tools == schema_before


def test_normal_read_edit_recovery_uses_only_available_alternatives() -> None:
    context = _context(single_batch=False, required=["read_file", "edit_file"])
    tools = _tools("read_file", "edit_file", "write_file", "repo_rg", "repo_tree")
    messages = build_decision_messages(context, tools)
    guidance = _guidance(messages)
    assert _mentioned_tools(guidance) <= {"read_file", "edit_file", "write_file", "repo_rg", "repo_tree"}
    assert "EDIT FAILURE" in guidance
    assert "read_file" in guidance and "edit_file" in guidance
    assert "exact" in guidance.lower()
    assert "same incorrect" in guidance.lower()
    assert "repo_rg" in next(m["content"] for m in messages if m.get("name") == "repo_identity")


@pytest.mark.parametrize("required_source", ["metadata", "verbatim_user"])
def test_genuine_required_read_without_callable_schema_fails_closed(required_source: str) -> None:
    context = _context(required=["read_file"] if required_source == "metadata" else [])
    if required_source == "verbatim_user":
        context[-1]["content"] = "Required tools (at least once): read_file\nCreate src/main.ts."
    before = deepcopy(context)
    with pytest.raises(ValueError, match=r"required.*tool.*unavailable|tool.*required.*unavailable"):
        build_decision_messages(context, _tools("write_file"))
    assert context == before


@pytest.mark.asyncio
async def test_stream_named_choice_rejects_two_argument_builder_before_callback_or_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks: list[str] = []
    provider = AsyncMock(return_value={"content": "fixture", "model": "fixture", "usage": {}})
    controller = TurnTransactionController(llm_provider=provider, tool_runtime=AsyncMock())
    orchestrator = controller._stream_orchestrator

    def legacy_builder(_context: list[dict], _tools: list[dict]) -> list[dict]:
        callbacks.append("legacy")
        return []

    monkeypatch.setattr(orchestrator, "build_decision_messages", legacy_builder)
    with pytest.raises(ValueError, match="decision_message_builder_tool_choice_contract_required"):
        async for _event in orchestrator._call_llm_for_decision_stream_impl(
            _context(),
            _tools("write_file"),
            TurnLedger(turn_id="legacy-forced"),
            tool_choice_override={"type": "function", "function": {"name": "write_file"}},
        ):
            pass
    assert callbacks == []
    assert provider.await_count == 0


@pytest.mark.asyncio
async def test_stream_default_choice_preserves_two_argument_builder_call(monkeypatch: pytest.MonkeyPatch) -> None:
    callbacks: list[str] = []
    provider = AsyncMock(return_value={"content": "fixture", "model": "fixture", "usage": {}})
    controller = TurnTransactionController(llm_provider=provider, tool_runtime=AsyncMock())
    orchestrator = controller._stream_orchestrator

    def legacy_builder(_context: list[dict], _tools: list[dict]) -> list[dict]:
        callbacks.append("legacy")
        return []

    monkeypatch.setattr(orchestrator, "build_decision_messages", legacy_builder)
    async for _event in orchestrator._call_llm_for_decision_stream_impl(
        _context(), _tools("write_file"), TurnLedger(turn_id="legacy-default")
    ):
        pass
    assert callbacks == ["legacy"]
    assert provider.await_count == 1


@pytest.mark.asyncio
async def test_stream_builder_body_typeerror_is_not_retried_or_reclassified(monkeypatch: pytest.MonkeyPatch) -> None:
    callbacks: list[str] = []
    provider = AsyncMock()
    controller = TurnTransactionController(llm_provider=provider, tool_runtime=AsyncMock())
    orchestrator = controller._stream_orchestrator

    def failing_builder(
        _context: list[dict],
        _tools: list[dict],
        _ledger: TurnLedger | None = None,
        *,
        tool_choice_override: Any = None,
    ) -> list[dict]:
        callbacks.append("body")
        raise TypeError("internal_builder_failure")

    monkeypatch.setattr(orchestrator, "build_decision_messages", failing_builder)
    with pytest.raises(TypeError, match="internal_builder_failure"):
        async for _event in orchestrator._call_llm_for_decision_stream_impl(
            _context(), _tools("write_file"), TurnLedger(turn_id="body-error"), tool_choice_override="required"
        ):
            pass
    assert callbacks == ["body"]
    assert provider.await_count == 0


def test_missing_required_tool_is_not_hidden_by_empty_surface() -> None:
    context = _context(required=["write_file"])
    with pytest.raises(ValueError, match=r"required.*tool.*unavailable|tool.*required.*unavailable"):
        build_decision_messages(context, [])


def test_task_contract_write_only_guidance_does_not_invent_read_or_edit_capabilities() -> None:
    hint, _metadata = build_single_batch_task_contract_hint(_context(), _tools("write_file"))
    assert _mentioned_tools(hint) <= {"write_file"}


def test_unowned_system_and_user_text_are_not_scrubbed_as_platform_repo_guidance() -> None:
    context = _context()
    quoted = {"role": "system", "name": "external_note", "content": _OLD_DISCOVERY}
    context.insert(1, quoted)
    context[-1]["content"] += " Quoted documentation mentions repo_tree and read_file."
    before = deepcopy(context)
    messages = build_decision_messages(context, _tools("write_file"))
    assert quoted in messages
    assert next(m for m in messages if m.get("role") == "user") == before[-1]
    assert context == before


def test_single_batch_deferred_verification_does_not_promise_a_recovery_turn() -> None:
    context = _context()
    context[-1]["content"] = "Create src/main.ts and verify the project builds."
    hint, _metadata = build_single_batch_task_contract_hint(context, _tools("write_file"))
    recovery = hint.split("TOOL FAILURE RECOVERY PROTOCOL:", 1)[1]
    assert "no further recovery turn" in recovery
    assert "Retry only" not in recovery


def test_named_choice_does_not_erase_genuine_required_read_from_broader_schema() -> None:
    context = _context(required=["read_file"])
    before = deepcopy(context)
    with pytest.raises(ValueError, match="task_contract_required_tool_unavailable"):
        build_decision_messages(
            context,
            _tools("read_file", "write_file"),
            tool_choice_override={"type": "function", "function": {"name": "write_file"}},
        )
    assert context == before


def test_disabled_structured_result_choice_fails_closed() -> None:
    from polaris.cells.roles.kernel.internal.transaction.tests.test_decision_message_builder import (
        _structured_result_tool,
    )

    with pytest.raises(ValueError, match="structured_output_tool_choice_unavailable"):
        build_decision_messages(
            [{"role": "user", "content": "Return the requested structured result."}],
            [_structured_result_tool()],
            tool_choice_override="none",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("choice", ["named", "auto", "none"])
async def test_actual_request_assembly_preserves_schema_choice_and_binds_guidance(
    monkeypatch: pytest.MonkeyPatch, stream: bool, choice: str
) -> None:
    # Capture at the in-memory transport boundary; never dispatch a real Provider or tool.
    context = _context()
    before = deepcopy(context)
    tools = _tools("read_file", "write_file")
    tool_choice: Any = {"type": "function", "function": {"name": "write_file"}} if choice == "named" else choice
    captured: list[dict[str, Any]] = []

    async def capture(payload: dict[str, Any]) -> dict[str, Any]:
        captured.append(deepcopy(payload))
        return {"content": "fixture", "model": "fixture", "usage": {}}

    controller = TurnTransactionController(
        llm_provider=capture, tool_runtime=AsyncMock(), config=TransactionConfig(domain="code")
    )
    ledger = TurnLedger(turn_id="guidance-fixture")
    if stream:
        from polaris.cells.roles.kernel.internal.turn_engine import stream_handler

        class FixtureHandler:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            async def process_stream(self, _stream: Any, **_kwargs: Any) -> Any:
                yield {"type": "_internal_materialize", "raw_output": "fixture", "usage": {}, "metadata": {}}

        def capture_stream(payload: dict[str, Any]) -> object:
            captured.append(deepcopy(payload))
            return object()

        monkeypatch.setattr(stream_handler, "StreamEventHandler", FixtureHandler)
        orchestrator = StreamOrchestrator(
            llm_provider=capture,
            llm_provider_stream=capture_stream,
            decoder=object(),
            emit_event=lambda _event: None,
            build_decision_messages=controller._build_decision_messages,
            build_stream_shadow_engine=lambda **_kwargs: None,
            call_llm_for_decision=controller._call_llm_for_decision,
            handoff_handler=object(),  # type: ignore[arg-type]
            tool_batch_executor=object(),  # type: ignore[arg-type]
            retry_orchestrator=object(),  # type: ignore[arg-type]
            handle_final_answer=lambda *_args, **_kwargs: None,
            requires_mutation_intent_hybrid=lambda *_args, **_kwargs: False,
            extract_monitoring_metrics=lambda *_args, **_kwargs: {},
        )
        async for _event in orchestrator._call_llm_for_decision_stream_impl(
            context, tools, ledger, tool_choice_override=tool_choice
        ):
            pass
    else:
        await controller._call_llm_for_decision(context, tools, ledger, tool_choice_override=tool_choice)
    assert len(captured) == 1
    payload = captured[0]
    assert payload["tools"] == tools
    assert payload["tool_choice"] == tool_choice
    messages = payload["messages"]
    assert messages[0] == before[0]
    assert next(m for m in messages if m.get("role") == "user") == before[-1]
    action_messages = [m for m in messages if m.get("metadata", {}).get("kind") != "physical_tool_schema_truth"]
    permitted = {"read_file", "write_file"} if choice == "auto" else {"write_file"} if choice == "named" else set()
    assert _mentioned_tools(_guidance(action_messages)) <= permitted
    assert context == before
