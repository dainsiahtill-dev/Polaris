"""Mutation-contract retry must wait out provider circuit_open instead of fail-closed.

Live L2-12 TASK-3-source-models (factory_a1b49b0460f2 task 220):
forced edit_file retry hit MiniMax SSL, retried immediately, then
``circuit_open:58s_remaining`` and settled as director_no_materialized_changes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
    RetryOrchestrator,
    _circuit_open_wait_seconds,
    _is_transient_llm_provider_exception,
)
from polaris.cells.roles.kernel.public.turn_contracts import RawLLMResponse


def _raw_response() -> RawLLMResponse:
    return RawLLMResponse(content="ok", native_tool_calls=[], model="test", usage={})


def _build_orchestrator(*, call_llm_for_decision: Any) -> RetryOrchestrator:
    return RetryOrchestrator(
        tool_runtime=SimpleNamespace(),
        config=SimpleNamespace(max_retry_attempts=4, max_tool_execution_time_ms=1000),
        decoder=SimpleNamespace(),
        call_llm_for_decision=call_llm_for_decision,
        call_llm_for_decision_stream=None,
        execute_tool_batch=None,
        guard_assert_single_tool_batch=lambda **_kwargs: None,
    )


def test_circuit_open_is_transient_and_parses_remaining_seconds() -> None:
    exc = RuntimeError("circuit_open:58s_remaining")
    assert _is_transient_llm_provider_exception(exc) is True
    assert _circuit_open_wait_seconds(exc) == 59.0


@pytest.mark.asyncio
async def test_execute_retry_batch_waits_circuit_open_after_ssl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(float(delay))

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    async def _fake_llm(*_args: Any, **_kwargs: Any) -> RawLLMResponse:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise RuntimeError(
                "HTTPSConnectionPool(host='api.minimaxi.com', port=443): "
                "Max retries exceeded (Caused by SSLError(SSLEOFError(8, "
                "'[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred')))"
            )
        if len(calls) == 2:
            raise RuntimeError("circuit_open:58s_remaining")
        return _raw_response()

    orchestrator = _build_orchestrator(call_llm_for_decision=_fake_llm)
    response = await orchestrator._execute_retry_batch(
        turn_id="director-8f7992c8b2d4--TASK-3-source-models",
        attempt_context=[],
        attempt_tool_definitions=[],
        ledger=TurnLedger(turn_id="t-circuit"),
        attempt_tool_choice_override={"type": "tool", "name": "edit_file"},
        attempt_model_override=None,
        stream=False,
        shadow_engine=None,
        attempt_temperature_override=None,
    )

    assert response.content == "ok"
    assert calls == [1, 2, 3]
    assert any(wait >= 58.0 for wait in sleeps)
