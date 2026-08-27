from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import StreamOrchestrator


@pytest.mark.asyncio
async def test_factory_stream_materialization_preserves_provider_argument_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the Factory transaction path must not drop stream argument evidence."""
    argument_audit = {
        "provider": "anthropic_compat",
        "tool_name": "write_file",
        "call_id": "call-audit-1",
        "target_path": "src/main.cpp",
        "raw_arguments_length": 91,
        "raw_arguments_sha256": "a" * 64,
        "decoded_arguments_sha256": "b" * 64,
        "content_length": 42,
        "content_sha256": "c" * 64,
        "content_lt_count": 1,
        "content_gt_count": 1,
        "content_closing_tag_count": 0,
        "assembly": {"delta_count": 3},
    }

    class _FakeStreamEventHandler:
        def __init__(self, *, workspace: str) -> None:
            assert workspace == "."

        async def process_stream(self, _stream: Any, **_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
            yield {
                "type": "tool_call",
                "tool": "write_file",
                "call_id": "call-audit-1",
                "args": {"path": "src/main.cpp", "content": "int main() { return 0; }"},
                "metadata": {"tool_call_argument_audit": argument_audit},
            }
            yield {
                "type": "_internal_materialize",
                "raw_output": "",
                "native_tool_calls": [],
                "usage": {},
                "metadata": {},
                "model": "test-model",
            }

    from polaris.cells.roles.kernel.internal.turn_engine import stream_handler

    monkeypatch.setattr(stream_handler, "StreamEventHandler", _FakeStreamEventHandler)

    orchestrator = StreamOrchestrator(
        llm_provider=lambda *_args, **_kwargs: None,
        llm_provider_stream=lambda _payload: object(),
        decoder=object(),
        emit_event=lambda _event: None,
        build_decision_messages=lambda _context, _tools: [],
        build_stream_shadow_engine=lambda **_kwargs: None,
        call_llm_for_decision=lambda *_args, **_kwargs: None,
        handoff_handler=object(),  # type: ignore[arg-type]
        tool_batch_executor=object(),  # type: ignore[arg-type]
        retry_orchestrator=object(),  # type: ignore[arg-type]
        handle_final_answer=lambda *_args, **_kwargs: None,
        requires_mutation_intent_hybrid=lambda *_args, **_kwargs: False,
        extract_monitoring_metrics=lambda *_args, **_kwargs: {},
    )

    responses = []
    async for event in orchestrator._call_llm_for_decision_stream_impl(
        [],
        [{"name": "write_file"}],
        TurnLedger(turn_id="turn-audit"),
    ):
        if isinstance(event, dict) and event.get("type") == "_internal_materialize":
            responses.append(event["response"])

    assert len(responses) == 1
    native_call = responses[0].native_tool_calls[0]
    assert native_call["provider_argument_audit"] == argument_audit
