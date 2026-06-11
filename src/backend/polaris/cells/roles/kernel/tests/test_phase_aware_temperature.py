"""ADR-0090 W2.6: phase-aware low temperature for escalated mutation retries.

fix5 evidence (qwen3.6-27b-int4, django-15213): temperature=0.92 drove
run-to-run localization flips under forced-write pressure (correct file in
diag5e vs a hallucinated Flask edit in fix5). Escalated/forced attempts are
transcription work — "write what you already read" — so they sample
near-deterministically; attempts 1-2 keep the profile temperature so tool
selection stays exploratory.

Channel (mirrors the tool_choice override pipeline):
retry_orchestrator → call_llm_for_decision[_stream](temperature_override=…)
→ request_payload["temperature_override"] → llm_provider closures
→ context_override["_transaction_kernel_temperature_override"]
→ llm_caller helpers.resolve_temperature → request_options["temperature"].
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.llm_caller.helpers import resolve_temperature
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
    RetryOrchestrator,
    resolve_escalation_temperature,
    resolve_retry_temperature_override,
)
from polaris.cells.roles.kernel.public.turn_contracts import RawLLMResponse

_ENV = "KERNELONE_RETRY_ESCALATION_TEMPERATURE"
_CHANNEL_KEY = "_transaction_kernel_temperature_override"


class TestResolveEscalationTemperature:
    def test_unset_env_defaults_to_low(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV, raising=False)
        assert resolve_escalation_temperature() == 0.2

    @pytest.mark.parametrize("sentinel", ["", "off", "none", "disabled", "false", "-0.5"])
    def test_disable_sentinels_return_none(self, monkeypatch: pytest.MonkeyPatch, sentinel: str) -> None:
        monkeypatch.setenv(_ENV, sentinel)
        assert resolve_escalation_temperature() is None

    def test_env_value_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV, "0.05")
        assert resolve_escalation_temperature() == 0.05

    def test_zero_is_a_valid_deterministic_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV, "0")
        assert resolve_escalation_temperature() == 0.0

    def test_garbage_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV, "warm")
        assert resolve_escalation_temperature() == 0.2

    def test_clamped_to_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV, "9.9")
        assert resolve_escalation_temperature() == 2.0


class TestResolveRetryTemperatureOverride:
    def test_prompt_phase_attempts_keep_profile_temperature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV, raising=False)
        assert resolve_retry_temperature_override(attempt_index=0) is None
        assert resolve_retry_temperature_override(attempt_index=1) is None

    def test_escalated_attempts_get_low_temperature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV, raising=False)
        assert resolve_retry_temperature_override(attempt_index=2) == 0.2
        assert resolve_retry_temperature_override(attempt_index=3) == 0.2

    def test_env_off_disables_everywhere(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV, "off")
        assert resolve_retry_temperature_override(attempt_index=3) is None


class TestResolveTemperatureHelper:
    def test_no_override_returns_requested(self) -> None:
        assert resolve_temperature(0.92, None) == 0.92
        assert resolve_temperature(0.92, {}) == 0.92

    def test_override_wins(self) -> None:
        assert resolve_temperature(0.92, {_CHANNEL_KEY: 0.2}) == 0.2

    def test_zero_override_is_honored(self) -> None:
        assert resolve_temperature(0.92, {_CHANNEL_KEY: 0}) == 0.0

    def test_negative_garbage_and_bool_fall_back(self) -> None:
        assert resolve_temperature(0.92, {_CHANNEL_KEY: -1}) == 0.92
        assert resolve_temperature(0.92, {_CHANNEL_KEY: "hot"}) == 0.92
        assert resolve_temperature(0.92, {_CHANNEL_KEY: True}) == 0.92

    def test_clamped_to_two(self) -> None:
        assert resolve_temperature(0.92, {_CHANNEL_KEY: 7.5}) == 2.0

    def test_non_dict_override_ignored(self) -> None:
        assert resolve_temperature(0.92, "not-a-dict") == 0.92


def _raw_response() -> RawLLMResponse:
    return RawLLMResponse(content="ok", native_tool_calls=[], model="test", usage={})


def _build_orchestrator(
    *,
    call_llm_for_decision: Any,
    call_llm_for_decision_stream: Any,
) -> RetryOrchestrator:
    return RetryOrchestrator(
        tool_runtime=SimpleNamespace(),
        config=SimpleNamespace(max_retry_attempts=4, max_tool_execution_time_ms=1000),
        decoder=SimpleNamespace(),
        call_llm_for_decision=call_llm_for_decision,
        call_llm_for_decision_stream=call_llm_for_decision_stream,
        execute_tool_batch=None,
        guard_assert_single_tool_batch=lambda **_kwargs: None,
    )


class TestExecuteRetryBatchTemperaturePassThrough:
    @pytest.mark.asyncio
    async def test_stream_path_passes_temperature_when_active(self) -> None:
        captured: dict[str, Any] = {}

        async def _fake_stream(context: Any, tools: Any, ledger: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            yield {"type": "_internal_materialize", "response": _raw_response()}

        orchestrator = _build_orchestrator(
            call_llm_for_decision=None,
            call_llm_for_decision_stream=_fake_stream,
        )
        response = await orchestrator._execute_retry_batch(
            turn_id="t1",
            attempt_context=[],
            attempt_tool_definitions=[],
            ledger=TurnLedger(turn_id="t1"),
            attempt_tool_choice_override=None,
            attempt_model_override=None,
            stream=True,
            shadow_engine=None,
            attempt_temperature_override=0.2,
        )

        assert response.content == "ok"
        assert captured["temperature_override"] == 0.2

    @pytest.mark.asyncio
    async def test_default_path_keeps_legacy_call_shape(self) -> None:
        """attempt_temperature_override=None must NOT widen the call signature —
        pre-W2.6 fakes/callers with explicit kwargs keep working unchanged."""

        async def _strict_legacy_stream(
            context: Any,
            tools: Any,
            ledger: Any,
            *,
            shadow_engine: Any = None,
            tool_choice_override: Any = None,
            model_override: Any = None,
        ) -> Any:
            del context, tools, ledger, shadow_engine, tool_choice_override, model_override
            yield {"type": "_internal_materialize", "response": _raw_response()}

        orchestrator = _build_orchestrator(
            call_llm_for_decision=None,
            call_llm_for_decision_stream=_strict_legacy_stream,
        )
        response = await orchestrator._execute_retry_batch(
            turn_id="t2",
            attempt_context=[],
            attempt_tool_definitions=[],
            ledger=TurnLedger(turn_id="t2"),
            attempt_tool_choice_override=None,
            attempt_model_override=None,
            stream=True,
            shadow_engine=None,
            attempt_temperature_override=None,
        )

        assert response.content == "ok"

    @pytest.mark.asyncio
    async def test_non_stream_path_passes_temperature_when_active(self) -> None:
        captured: dict[str, Any] = {}

        async def _fake_decision(context: Any, tools: Any, ledger: Any, **kwargs: Any) -> RawLLMResponse:
            captured.update(kwargs)
            return _raw_response()

        orchestrator = _build_orchestrator(
            call_llm_for_decision=_fake_decision,
            call_llm_for_decision_stream=None,
        )
        response = await orchestrator._execute_retry_batch(
            turn_id="t3",
            attempt_context=[],
            attempt_tool_definitions=[],
            ledger=TurnLedger(turn_id="t3"),
            attempt_tool_choice_override=None,
            attempt_model_override=None,
            stream=False,
            shadow_engine=None,
            attempt_temperature_override=0.1,
        )

        assert response.content == "ok"
        assert captured["temperature_override"] == 0.1


class TestTransactionKernelTemperatureChannel:
    """request_payload["temperature_override"] → context_override channel key."""

    @pytest.mark.asyncio
    async def test_payload_temperature_lands_in_context_override(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = SimpleNamespace(role_id="director", version="1.0", model="test-model", provider_id="openai")
        request = SimpleNamespace(
            message="hello",
            run_id="run_123",
            task_id=None,
            workspace=".",
            context_override={"context_os_snapshot": {}},
        )
        captured_contexts: list[Any] = []

        async def _fake_call(*, context: Any, **_kwargs: Any) -> Any:
            captured_contexts.append(context)
            return SimpleNamespace(content="ok", tool_calls=[], error=None, metadata={})

        kernel.inject_llm_caller(SimpleNamespace(call=_fake_call))
        tk = kernel._create_transaction_kernel("director", profile, request)

        await tk.llm_provider(
            {
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hello"},
                ],
                "temperature_override": 0.2,
            }
        )

        assert len(captured_contexts) == 1
        context_override = getattr(captured_contexts[0], "context_override", None)
        assert isinstance(context_override, dict)
        assert context_override[_CHANNEL_KEY] == 0.2

    @pytest.mark.asyncio
    async def test_absent_payload_temperature_leaves_channel_clean(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = SimpleNamespace(role_id="director", version="1.0", model="test-model", provider_id="openai")
        request = SimpleNamespace(
            message="hello",
            run_id="run_123",
            task_id=None,
            workspace=".",
            context_override={"context_os_snapshot": {}},
        )
        captured_contexts: list[Any] = []

        async def _fake_call(*, context: Any, **_kwargs: Any) -> Any:
            captured_contexts.append(context)
            return SimpleNamespace(content="ok", tool_calls=[], error=None, metadata={})

        kernel.inject_llm_caller(SimpleNamespace(call=_fake_call))
        tk = kernel._create_transaction_kernel("director", profile, request)

        await tk.llm_provider(
            {
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hello"},
                ]
            }
        )

        assert len(captured_contexts) == 1
        context_override = getattr(captured_contexts[0], "context_override", None)
        assert isinstance(context_override, dict)
        assert _CHANNEL_KEY not in context_override
