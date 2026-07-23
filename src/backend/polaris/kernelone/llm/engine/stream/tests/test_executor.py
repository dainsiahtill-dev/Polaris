"""Tests for StreamExecutor core streaming functionality.

Covers:
- StreamExecutor initialization and configuration
- invoke_stream error handling (invalid provider, budget exceeded)
- _provider_supports_structured_stream detection
- Tool call accumulator utilities
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.errors import BudgetExceededError
from polaris.kernelone.llm.engine._executor_base import clamp_output_tokens_to_window
from polaris.kernelone.llm.engine.contracts import (
    AIRequest,
    AIStreamEvent,
    CompressionResult,
    ModelSpec,
    TaskType,
    TokenBudgetDecision,
    bind_physical_provider_dispatch_port,
    get_physical_provider_dispatch_port,
)
from polaris.kernelone.llm.engine.stream import (
    StreamExecutor,
    stream_to_response,
)
from polaris.kernelone.llm.engine.stream.config import StreamConfig as DirectStreamConfig
from polaris.kernelone.llm.engine.stream.tool_accumulator import (
    _normalize_arguments,
    _provider_supports_structured_stream,
    _tool_accumulator_key,
)
from polaris.kernelone.llm.engine.telemetry import TelemetryCollector


def test_stream_clamp_fails_closed_when_prompt_exceeds_window() -> None:
    class _Spec:
        max_context_tokens = 1000

    cfg = {"max_tokens": 800}
    with pytest.raises(BudgetExceededError):
        clamp_output_tokens_to_window(cfg, _Spec(), "中" * 990, logger_prefix="[stream-executor]")


class _ModelCatalog:
    def resolve(self, provider_id: str, model: str, provider_cfg: dict[str, object]) -> ModelSpec:
        del provider_cfg
        return ModelSpec(
            provider_id=provider_id,
            provider_type="fake",
            model=model,
            max_context_tokens=4096,
            max_output_tokens=1024,
            supports_tools=True,
        )


class _BudgetManager:
    def enforce(
        self,
        prompt_input: str,
        model_spec: ModelSpec,
        *,
        requested_output_tokens: int,
        workspace: str | None,
        role: str,
        overhead_tokens: int = 0,
    ) -> TokenBudgetDecision:
        del prompt_input, model_spec, requested_output_tokens, workspace, role
        return TokenBudgetDecision(
            allowed=True,
            max_context_tokens=4096,
            allowed_prompt_tokens=2048,
            requested_prompt_tokens=64,
            reserved_output_tokens=512,
            safety_margin_tokens=128,
            overhead_tokens=overhead_tokens,
        )


class TestStreamExecutorInit:
    """Tests for StreamExecutor initialization."""

    def test_default_initialization(self) -> None:
        """StreamExecutor must initialize with default values."""
        executor = StreamExecutor()

        assert executor.workspace is None
        assert executor.telemetry is None
        assert executor.model_catalog is not None
        assert executor.token_budget is not None
        assert executor.config is not None

    def test_initialization_with_workspace(self) -> None:
        """StreamExecutor must accept workspace parameter."""
        executor = StreamExecutor(workspace="/tmp/test")

        assert executor.workspace == "/tmp/test"

    def test_initialization_with_custom_config(self) -> None:
        """StreamExecutor must accept custom StreamConfig."""
        config = DirectStreamConfig(buffer_size=200, timeout_sec=60.0)
        executor = StreamExecutor(config=config)

        assert executor.config.buffer_size == 200
        assert executor.timeout == 60.0


class TestStreamExecutorInvokeStreamErrors:
    """Tests for invoke_stream error handling."""

    @pytest.mark.asyncio
    async def test_empty_provider_timeout_keeps_typed_error_evidence(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                del prompt, model, config
                raise asyncio.TimeoutError
                yield "unreachable"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider:
                assert provider_type == "fake"
                return _Provider()

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )
        executor = StreamExecutor(model_catalog=_ModelCatalog(), token_budget=_BudgetManager())
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake"})
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="chief_engineer",
            provider_id="provider-a",
            model="model-a",
            input="build portfolio",
            options={"timeout": 125},
        )

        events = [event async for event in executor.invoke_stream(request)]

        errors = [event.error for event in events if event.type.value == "error"]
        assert errors == ["provider_stream_timeout:125s"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "provider_type",
        ["anthropic_compat", "openai_compat"],
    )
    async def test_invoke_stream_binds_exact_port_for_iteration_and_resets(
        self,
        monkeypatch: pytest.MonkeyPatch,
        provider_type: str,
    ) -> None:
        """B3.3 sidecar lives through lazy iteration, then resets in finally."""

        seen: list[object | None] = []

        class _FactoryGovernedPort:
            def bind_provider_route_authority(self, **_: object) -> None:
                return None

            def dispatch_stream_async(self, **_: object) -> None:
                return None

        port = _FactoryGovernedPort()

        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                del prompt, model, config
                seen.append(get_physical_provider_dispatch_port())
                yield "ok"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider | None:
                assert provider_type in {
                    "anthropic_compat",
                    "openai_compat",
                }
                return _Provider()

            def get_factory_default_provider_instance(self, provider_type: str) -> _Provider | None:
                return self.get_provider_instance(provider_type)

            def is_factory_default_provider_implementation(
                self,
                provider_type: str,
                provider_instance: object,
            ) -> bool:
                return (
                    provider_type
                    in {
                        "anthropic_compat",
                        "openai_compat",
                    }
                    and type(provider_instance) is _Provider
                )

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )
        executor = StreamExecutor(model_catalog=_ModelCatalog(), token_budget=_BudgetManager())
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": provider_type})
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="director",
            provider_id="provider-a",
            model="model-a",
            input="hello",
        )

        events = [
            event
            async for event in executor.invoke_stream(
                request,
                physical_dispatch_port=port,
            )
        ]

        assert any(event.type.value == "complete" for event in events)
        assert seen == [port]
        assert get_physical_provider_dispatch_port() is None

    @pytest.mark.asyncio
    async def test_arbitrary_non_null_sidecar_retains_legacy_overall_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only the exact Factory dispatch capabilities transfer deadline ownership."""

        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                del prompt, model, config
                await asyncio.sleep(0.03)
                yield "late"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider:
                assert provider_type == "openai_compat"
                return _Provider()

            def get_factory_default_provider_instance(self, provider_type: str) -> _Provider:
                return self.get_provider_instance(provider_type)

            def is_factory_default_provider_implementation(
                self,
                provider_type: str,
                provider_instance: object,
            ) -> bool:
                return provider_type == "openai_compat" and type(provider_instance) is _Provider

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )
        executor = StreamExecutor(
            model_catalog=_ModelCatalog(),
            token_budget=_BudgetManager(),
            config=DirectStreamConfig(timeout_sec=0.01),
        )
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "openai_compat"})
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="chief_engineer",
            provider_id="provider-a",
            model="model-a",
            input="unqualified sidecar",
        )

        events = [event async for event in executor.invoke_stream(request, physical_dispatch_port=object())]

        assert [event.error for event in events if event.error] == ["Stream overall timeout after 0.01s"]

    @pytest.mark.asyncio
    async def test_factory_governed_terminal_replay_ignores_legacy_overall_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A qualified physical deadline owns Factory stream timing.

        Factory dispatch consumes and durably terminalizes the physical stream
        before replaying buffered events.  The legacy executor wall clock must
        not reject that post-terminal replay or cause the role layer to issue a
        second physical request.
        """

        seen: list[object | None] = []

        class _FactoryGovernedPort:
            def bind_provider_route_authority(self, **_: object) -> None:
                return None

            def dispatch_stream_async(self, **_: object) -> None:
                return None

        port = _FactoryGovernedPort()

        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                del prompt, model, config
                seen.append(get_physical_provider_dispatch_port())
                await asyncio.sleep(0.03)
                yield "terminal replay"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider:
                assert provider_type == "openai_compat"
                return _Provider()

            def get_factory_default_provider_instance(self, provider_type: str) -> _Provider:
                return self.get_provider_instance(provider_type)

            def is_factory_default_provider_implementation(
                self,
                provider_type: str,
                provider_instance: object,
            ) -> bool:
                return provider_type == "openai_compat" and type(provider_instance) is _Provider

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )
        executor = StreamExecutor(
            model_catalog=_ModelCatalog(),
            token_budget=_BudgetManager(),
            config=DirectStreamConfig(timeout_sec=0.01),
        )
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "openai_compat"})
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="chief_engineer",
            provider_id="provider-a",
            model="model-a",
            input="build portfolio",
        )

        events = [event async for event in executor.invoke_stream(request, physical_dispatch_port=port)]

        assert not [event.error for event in events if event.error]
        assert any(event.type.value == "complete" for event in events)
        assert seen == [port]
        assert get_physical_provider_dispatch_port() is None

    @pytest.mark.asyncio
    async def test_factory_governed_tiny_reasoning_replay_is_compacted(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Post-terminal replay must not project one role event per tiny SSE delta."""

        class _FactoryGovernedPort:
            def bind_provider_route_authority(self, **_: object) -> None:
                return None

            def dispatch_stream_async(self, **_: object) -> None:
                return None

        class _Provider:
            async def invoke_stream_events(self, prompt: str, model: str, config: dict[str, Any]):
                del prompt, model, config
                for _ in range(4_096):
                    yield {
                        "type": "content_block_delta",
                        "delta": {"type": "thinking_delta", "thinking": "r"},
                    }

        class _ProviderManager:
            def get_factory_default_provider_instance(self, provider_type: str) -> _Provider:
                assert provider_type == "anthropic_compat"
                return _Provider()

            def is_factory_default_provider_implementation(
                self,
                provider_type: str,
                provider_instance: object,
            ) -> bool:
                return provider_type == "anthropic_compat" and type(provider_instance) is _Provider

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )
        executor = StreamExecutor(model_catalog=_ModelCatalog(), token_budget=_BudgetManager())
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "anthropic_compat"})
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="chief_engineer",
            provider_id="provider-a",
            model="model-a",
            input="build portfolio",
        )

        events = [
            event
            async for event in executor.invoke_stream(
                request,
                physical_dispatch_port=_FactoryGovernedPort(),
            )
        ]

        reasoning = [str(event.reasoning or "") for event in events if event.type.value == "reasoning_chunk"]
        assert "".join(reasoning) == "r" * 4_096
        assert len(reasoning) <= 8
        assert [event.type.value for event in events].count("complete") == 1

    @pytest.mark.asyncio
    async def test_factory_replay_compaction_preserves_text_and_tool_order(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _FactoryGovernedPort:
            def bind_provider_route_authority(self, **_: object) -> None:
                return None

            def dispatch_stream_async(self, **_: object) -> None:
                return None

        class _Provider:
            async def invoke_stream_events(self, prompt: str, model: str, config: dict[str, Any]):
                del prompt, model, config
                yield {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": "plan-"},
                }
                yield {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": "before-tool"},
                }
                yield {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "call-readme",
                        "name": "read_file",
                        "input": {},
                    },
                }
                yield {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": '{"path":"README.md"}'},
                }
                yield {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "answer-after-tool"},
                }

        class _ProviderManager:
            def get_factory_default_provider_instance(self, provider_type: str) -> _Provider:
                assert provider_type == "anthropic_compat"
                return _Provider()

            def is_factory_default_provider_implementation(
                self,
                provider_type: str,
                provider_instance: object,
            ) -> bool:
                return provider_type == "anthropic_compat" and type(provider_instance) is _Provider

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )
        executor = StreamExecutor(model_catalog=_ModelCatalog(), token_budget=_BudgetManager())
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "anthropic_compat"})

        events = [
            event
            async for event in executor.invoke_stream(
                AIRequest(
                    task_type=TaskType.DIALOGUE,
                    role="chief_engineer",
                    provider_id="provider-a",
                    model="model-a",
                    input="build portfolio",
                ),
                physical_dispatch_port=_FactoryGovernedPort(),
            )
        ]

        semantic = [event for event in events if event.type.value in {"reasoning_chunk", "tool_call", "chunk"}]
        assert [event.type.value for event in semantic] == ["reasoning_chunk", "tool_call", "chunk"]
        assert semantic[0].reasoning == "plan-before-tool"
        assert semantic[1].tool_call == {
            "tool": "read_file",
            "arguments": {"path": "README.md"},
            "call_id": "call-readme",
            "provider_meta": {
                "provider": "anthropic_compat",
                "index": None,
                "content_block_index": 0,
            },
        }
        assert semantic[2].chunk == "answer-after-tool"

    @pytest.mark.asyncio
    async def test_ungoverned_stream_retains_tiny_reasoning_granularity(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _Provider:
            async def invoke_stream_events(self, prompt: str, model: str, config: dict[str, Any]):
                del prompt, model, config
                for _ in range(32):
                    yield {
                        "type": "content_block_delta",
                        "delta": {"type": "thinking_delta", "thinking": "r"},
                    }

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider:
                assert provider_type == "anthropic_compat"
                return _Provider()

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )
        executor = StreamExecutor(model_catalog=_ModelCatalog(), token_budget=_BudgetManager())
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "anthropic_compat"})

        events = [
            event
            async for event in executor.invoke_stream(
                AIRequest(
                    task_type=TaskType.DIALOGUE,
                    role="chief_engineer",
                    provider_id="provider-a",
                    model="model-a",
                    input="live stream",
                )
            )
        ]

        reasoning = [event for event in events if event.type.value == "reasoning_chunk"]
        assert len(reasoning) == 32

    @pytest.mark.asyncio
    async def test_ungoverned_stream_retains_legacy_overall_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                del prompt, model, config
                await asyncio.sleep(0.03)
                yield "late"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider:
                assert provider_type == "fake"
                return _Provider()

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )
        executor = StreamExecutor(
            model_catalog=_ModelCatalog(),
            token_budget=_BudgetManager(),
            config=DirectStreamConfig(timeout_sec=0.01),
        )
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake"})
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="director",
            provider_id="provider-a",
            model="model-a",
            input="legacy stream",
        )

        events = [event async for event in executor.invoke_stream(request)]

        assert [event.error for event in events if event.error] == ["Stream overall timeout after 0.01s"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "provider_type",
        [
            "codex_cli",
            "codex_sdk",
            "gemini_api",
            "gemini_cli",
            "kimi",
            "minimax",
            "ollama",
        ],
    )
    async def test_factory_bound_opaque_stream_route_fails_before_instance_or_outbound(
        self,
        monkeypatch: pytest.MonkeyPatch,
        provider_type: str,
    ) -> None:
        manager_calls: list[str] = []
        outbound: list[str] = []

        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                del model, config
                outbound.append(prompt)
                yield "must-not-escape"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider:
                manager_calls.append(provider_type)
                return _Provider()

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )
        executor = StreamExecutor(model_catalog=_ModelCatalog(), token_budget=_BudgetManager())
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": provider_type})
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="director",
            provider_id="provider-a",
            model="model-a",
            input="must-not-launch-gemini",
        )

        events = [
            event
            async for event in executor.invoke_stream(
                request,
                physical_dispatch_port=object(),
            )
        ]

        errors = [event.error for event in events if event.error]
        assert errors == [f"factory_provider_route_disabled_opaque:{provider_type}:stream"]
        assert manager_calls == []
        assert outbound == []
        assert get_physical_provider_dispatch_port() is None

    @pytest.mark.asyncio
    async def test_factory_bound_governed_stream_rejects_replaced_provider_implementation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        outbound: list[str] = []

        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                del model, config
                outbound.append(prompt)
                yield "must-not-escape"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider:
                del provider_type
                raise AssertionError("untrusted replacement must not be instantiated")

            def get_factory_default_provider_instance(self, provider_type: str) -> None:
                assert provider_type == "openai_compat"
                return None

            def is_factory_default_provider_implementation(
                self,
                provider_type: str,
                provider_instance: object,
            ) -> bool:
                del provider_type, provider_instance
                return False

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )
        executor = StreamExecutor(model_catalog=_ModelCatalog(), token_budget=_BudgetManager())
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "openai_compat"})
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="director",
            provider_id="provider-a",
            model="model-a",
            input="must-not-reach-replaced-stream-provider",
        )

        events = [event async for event in executor.invoke_stream(request, physical_dispatch_port=object())]

        errors = [event.error for event in events if event.error]
        assert errors == ["factory_provider_route_implementation_untrusted:openai_compat:stream"]
        assert outbound == []

    @pytest.mark.asyncio
    async def test_stream_binding_nested_aclose_restores_prior_token(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cleanup: list[object | None] = []

        async def _bound(request: AIRequest):
            del request
            try:
                yield AIStreamEvent.chunk_event("ok")
                await asyncio.Event().wait()
            finally:
                cleanup.append(get_physical_provider_dispatch_port())

        executor = StreamExecutor()
        monkeypatch.setattr(executor, "_invoke_stream_bound", _bound)

        def _request(text: str) -> AIRequest:
            return AIRequest(
                task_type=TaskType.DIALOGUE,
                role="director",
                provider_id="provider-a",
                model="model-a",
                input=text,
            )

        outer_port = object()
        inner_port = object()
        sentinel = object()
        with bind_physical_provider_dispatch_port(sentinel):
            outer = executor.invoke_stream(_request("outer"), physical_dispatch_port=outer_port)
            await anext(outer)
            assert get_physical_provider_dispatch_port() is sentinel
            inner = executor.invoke_stream(_request("inner"), physical_dispatch_port=inner_port)
            await anext(inner)
            assert get_physical_provider_dispatch_port() is sentinel
            await inner.aclose()
            assert get_physical_provider_dispatch_port() is sentinel
            await outer.aclose()
            assert get_physical_provider_dispatch_port() is sentinel

        assert get_physical_provider_dispatch_port() is None
        assert cleanup == [inner_port, outer_port]

    @pytest.mark.asyncio
    async def test_stream_binding_cross_task_aclose_is_safe_and_cleanup_sees_port(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cleanup: list[object | None] = []

        async def _bound(request: AIRequest):
            del request
            try:
                yield AIStreamEvent.chunk_event("ok")
                await asyncio.Event().wait()
            finally:
                cleanup.append(get_physical_provider_dispatch_port())

        executor = StreamExecutor()
        monkeypatch.setattr(executor, "_invoke_stream_bound", _bound)
        port = object()
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="director",
            provider_id="provider-a",
            model="model-a",
            input="cross-task-close",
        )
        stream = executor.invoke_stream(request, physical_dispatch_port=port)

        _ = await asyncio.create_task(anext(stream))
        assert get_physical_provider_dispatch_port() is None
        await asyncio.create_task(stream.aclose())

        assert cleanup == [port]
        assert get_physical_provider_dispatch_port() is None

    @pytest.mark.asyncio
    async def test_stream_binding_concurrent_tasks_and_to_thread_are_isolated(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[tuple[str, object | None, object | None]] = []

        async def _bound(request: AIRequest):
            seen.append(
                (
                    str(request.input),
                    get_physical_provider_dispatch_port(),
                    await asyncio.to_thread(get_physical_provider_dispatch_port),
                )
            )
            await asyncio.sleep(0)
            yield AIStreamEvent.chunk_event("ok")

        executor = StreamExecutor()
        monkeypatch.setattr(executor, "_invoke_stream_bound", _bound)
        port_one = object()
        port_two = object()

        async def _consume(text: str, port: object) -> None:
            request = AIRequest(
                task_type=TaskType.DIALOGUE,
                role="director",
                provider_id="provider-a",
                model="model-a",
                input=text,
            )
            _ = [
                event
                async for event in executor.invoke_stream(
                    request,
                    physical_dispatch_port=port,
                )
            ]
            assert get_physical_provider_dispatch_port() is None

        await asyncio.gather(
            _consume("one", port_one),
            _consume("two", port_two),
        )

        assert sorted((prompt, id(bound), id(thread_bound)) for prompt, bound, thread_bound in seen) == sorted(
            [("one", id(port_one), id(port_one)), ("two", id(port_two), id(port_two))]
        )
        assert get_physical_provider_dispatch_port() is None

    @pytest.mark.asyncio
    async def test_stream_binding_resets_after_error_and_cancellation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cleanup: list[object | None] = []
        started = asyncio.Event()

        async def _cancelled_bound(request: AIRequest):
            del request
            try:
                started.set()
                await asyncio.Event().wait()
                if False:
                    yield AIStreamEvent.chunk_event("unreachable")
            finally:
                cleanup.append(get_physical_provider_dispatch_port())

        executor = StreamExecutor()
        monkeypatch.setattr(executor, "_invoke_stream_bound", _cancelled_bound)
        port = object()
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="director",
            provider_id="provider-a",
            model="model-a",
            input="cancel",
        )

        async def _consume() -> None:
            _ = [
                event
                async for event in executor.invoke_stream(
                    request,
                    physical_dispatch_port=port,
                )
            ]

        task = asyncio.create_task(_consume())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleanup == [port]
        assert get_physical_provider_dispatch_port() is None

        async def _error_bound(request: AIRequest):
            del request
            raise RuntimeError("stream failed")
            if False:
                yield AIStreamEvent.chunk_event("unreachable")

        monkeypatch.setattr(executor, "_invoke_stream_bound", _error_bound)
        with pytest.raises(RuntimeError, match="stream failed"):
            _ = [
                event
                async for event in executor.invoke_stream(
                    request,
                    physical_dispatch_port=port,
                )
            ]
        assert get_physical_provider_dispatch_port() is None

    @pytest.mark.asyncio
    async def test_invoke_stream_error_on_invalid_provider(self) -> None:
        """invoke_stream must yield error event when provider is invalid."""
        executor = StreamExecutor()

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="test",
            provider_id="nonexistent_provider",
            model="gpt-4",
        )

        events = []
        async for event in executor.invoke_stream(request):
            events.append(event)

        assert len(events) >= 1
        error_events = [e for e in events if e.type.value == "error"]
        assert len(error_events) >= 1
        error_msg = error_events[0].error or ""
        assert "Provider" in error_msg or "provider" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_invoke_stream_error_on_missing_provider_type(self) -> None:
        """invoke_stream must yield error event when provider type is unknown."""
        executor = StreamExecutor()

        # Use a valid provider_id format but with unknown type
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="test",
            provider_id="invalid_provider_type",
            model="gpt-4",
        )

        events = []
        async for event in executor.invoke_stream(request):
            events.append(event)

        assert len(events) >= 1
        error_events = [e for e in events if e.type.value == "error"]
        assert len(error_events) >= 1

    @pytest.mark.asyncio
    async def test_invoke_stream_debug_event_uses_resolved_provider_and_model(self, monkeypatch) -> None:
        """invoke_start debug event must use resolved provider/model, not empty request fields."""
        executor = StreamExecutor()
        captured_debug_events: list[dict[str, object]] = []

        def _capture_debug_event(**kwargs: object) -> None:
            captured_debug_events.append(dict(kwargs))

        monkeypatch.setattr(
            executor,
            "_resolve_provider_model",
            lambda _request: ("resolved-provider", "resolved-model"),
        )
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {})
        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._debug_stream_module.emit_debug_event",
            _capture_debug_event,
        )

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="hello",
            provider_id=None,
            model=None,
        )

        events = []
        async for event in executor.invoke_stream(request):
            events.append(event)

        assert any(event.type.value == "error" for event in events)
        invoke_start = next(item for item in captured_debug_events if item.get("label") == "invoke_start")
        payload = invoke_start.get("payload")
        assert isinstance(payload, dict)
        assert payload["provider_id"] == "resolved-provider"
        assert payload["model"] == "resolved-model"

    @pytest.mark.asyncio
    async def test_invoke_stream_records_provider_bound_final_request_receipt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        receipts: list[dict[str, Any]] = []
        provider_calls: list[dict[str, Any]] = []

        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                provider_calls.append({"prompt": prompt, "model": model, "config": dict(config)})
                yield "ok"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider | None:
                assert provider_type == "fake"
                return _Provider()

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )

        executor = StreamExecutor(
            workspace=str(tmp_path),
            model_catalog=_ModelCatalog(),
            token_budget=_BudgetManager(),
            final_request_receipt_sink=receipts.append,
        )
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

        request = AIRequest(
            task_type=TaskType.GENERATION,
            role="director",
            provider_id="fake-provider",
            model="fake-model",
            input="SECRET STREAM PROMPT",
            options={
                "max_tokens": 1024,
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "read_file", "parameters": {"type": "object"}},
                    }
                ],
            },
            context={
                "run_id": "run-1",
                "task_id": "task-1",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "attempt": 2,
                "fix_attempt_id": "fix-1",
                "native_tool_mode": "native_tools",
                "capability_profile_ref": {
                    "sha256": "b" * 64,
                    "source": "roles.kernel.llm_caller.pre_projection",
                },
                "context_projection_id": "projection-1",
                "prompt_profile_audit": {
                    "selected_prompt_profile_ids": ["builtin.language.typescript", "builtin.task.implement"],
                    "inferred_language": "typescript",
                    "inferred_task_type": "implement",
                    "content": "SECRET STREAM PROFILE TEMPLATE",
                },
                "selected_prompt_profile_ids": ["builtin.language.typescript", "builtin.task.implement"],
                "chat_messages": [
                    {"role": "system", "content": "SECRET STREAM SYSTEM"},
                    {"role": "user", "content": "SECRET STREAM PROMPT"},
                ],
            },
        )

        events = [event async for event in executor.invoke_stream(request)]

        assert any(event.type.value == "complete" for event in events)
        assert len(provider_calls) == 1
        assert len(receipts) == 1
        payload = receipts[0]["payload"]
        assert payload["source"] == "kernelone.llm.engine.stream_executor"
        assert payload["stream"] is True
        assert payload["provider_id"] == "fake-provider"
        assert payload["provider_type"] == "fake"
        assert payload["model"] == "fake-model"
        assert payload["turn_id"] == "turn-1"
        assert payload["attempt"] == 2
        assert payload["fix_attempt_id"] == "fix-1"
        assert payload["context_projection_id"] == "projection-1"
        assert payload["capability_profile_sha256"] == "b" * 64
        assert payload["capability_profile_source"] == "roles.kernel.llm_caller.pre_projection"
        assert len(payload["budget_admission_id"]) == 64
        assert payload["final_max_tokens"] == provider_calls[0]["config"]["max_tokens"]
        assert payload["chat_message_count_before"] == 2
        assert payload["chat_message_count_after"] == 2
        assert payload["tool_count"] == 1
        assert len(payload["input_sha256"]) == 64
        assert len(payload["effective_prompt_sha256"]) == 64
        assert len(payload["tool_schema_sha256"]) == 64
        assert payload["selected_prompt_profile_ids"] == [
            "builtin.language.typescript",
            "builtin.task.implement",
        ]
        assert payload["prompt_profile_selection"]["inferred_language"] == "typescript"

        serialized_receipt = json.dumps(receipts[0], ensure_ascii=False, sort_keys=True)
        assert "SECRET STREAM PROMPT" not in serialized_receipt
        assert "SECRET STREAM SYSTEM" not in serialized_receipt
        assert "SECRET STREAM PROFILE TEMPLATE" not in serialized_receipt

    @pytest.mark.asyncio
    async def test_invoke_stream_traceability_ids_reach_telemetry_without_optional_sink(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                del prompt, model, config
                yield "ok"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider | None:
                assert provider_type == "fake"
                return _Provider()

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )

        executor = StreamExecutor(
            workspace=str(tmp_path),
            telemetry=TelemetryCollector(),
            model_catalog=_ModelCatalog(),
            token_budget=_BudgetManager(),
        )
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

        events = [
            event
            async for event in executor.invoke_stream(
                AIRequest(
                    task_type=TaskType.GENERATION,
                    role="director",
                    provider_id="fake-provider",
                    model="fake-model",
                    input="prompt",
                    options={"max_tokens": 128},
                    context={
                        "turn_id": "turn-stream-optional",
                        "context_projection_id": "projection-stream-optional",
                        "context_result_id": "ctxres-stream-optional",
                    },
                )
            )
        ]

        assert any(event.type.value == "complete" for event in events)
        invoke_end = [event for event in executor.telemetry.get_events() if event.event_type == "invoke_end"][-1]
        assert invoke_end.metadata["projection_id"] == "projection-stream-optional"
        assert invoke_end.metadata["context_result_id"] == "ctxres-stream-optional"
        assert len(invoke_end.metadata["final_request_receipt_id"]) == 64
        assert len(invoke_end.metadata["provider_request_id"]) == 64
        assert invoke_end.metadata["telemetry_trace_id"] == invoke_end.trace_id

    @pytest.mark.asyncio
    async def test_invoke_stream_final_request_receipt_redacts_compression_payload(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        receipts: list[dict[str, Any]] = []

        class _CompressingBudgetManager:
            def enforce(
                self,
                prompt_input: str,
                model_spec: ModelSpec,
                *,
                requested_output_tokens: int,
                workspace: str | None,
                role: str,
                overhead_tokens: int = 0,
            ) -> TokenBudgetDecision:
                del prompt_input, model_spec, requested_output_tokens, workspace, role
                return TokenBudgetDecision(
                    allowed=True,
                    max_context_tokens=4096,
                    allowed_prompt_tokens=1024,
                    requested_prompt_tokens=4096,
                    reserved_output_tokens=512,
                    safety_margin_tokens=128,
                    compression_applied=True,
                    compression=CompressionResult(
                        compressed_input="SECRET STREAM COMPRESSED /home/alice sk-stream-secret",
                        original_tokens=4096,
                        compressed_tokens=900,
                        strategy="hard_trim",
                        quality_flag="degraded",
                        drop_ratio=0.78,
                    ),
                    overhead_tokens=overhead_tokens,
                )

        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                del prompt, model, config
                yield "ok"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider | None:
                assert provider_type == "fake"
                return _Provider()

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )

        executor = StreamExecutor(
            workspace=str(tmp_path),
            model_catalog=_ModelCatalog(),
            token_budget=_CompressingBudgetManager(),
            final_request_receipt_sink=receipts.append,
        )
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

        events = [
            event
            async for event in executor.invoke_stream(
                AIRequest(
                    task_type=TaskType.GENERATION,
                    role="director",
                    provider_id="fake-provider",
                    model="fake-model",
                    input="SECRET STREAM INPUT",
                    options={"max_tokens": 512},
                    context={"turn_id": "turn-stream-compressed"},
                )
            )
        ]

        assert any(event.type.value == "complete" for event in events)
        assert len(receipts) == 1
        serialized_receipt = json.dumps(receipts[0], ensure_ascii=False, sort_keys=True)
        complete_events = [event for event in events if event.type.value == "complete"]
        serialized_complete = json.dumps(
            [event.to_dict() for event in complete_events],
            ensure_ascii=False,
            sort_keys=True,
        )
        assert "compressed_input" not in serialized_receipt
        assert "compressed_input" not in serialized_complete
        assert "SECRET STREAM COMPRESSED" not in serialized_receipt
        assert "SECRET STREAM COMPRESSED" not in serialized_complete
        assert "sk-stream-secret" not in serialized_receipt
        assert "sk-stream-secret" not in serialized_complete
        assert "/home/alice" not in serialized_receipt
        assert "/home/alice" not in serialized_complete
        assert receipts[0]["payload"]["token_budget"]["compression"]["compressed_text_sha256"]

    @pytest.mark.asyncio
    async def test_required_final_request_receipt_missing_sink_yields_error_before_provider(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        provider_calls: list[str] = []

        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                del model, config
                provider_calls.append(prompt)
                yield "should not run"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider | None:
                assert provider_type == "fake"
                return _Provider()

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )

        executor = StreamExecutor(
            workspace=str(tmp_path),
            model_catalog=_ModelCatalog(),
            token_budget=_BudgetManager(),
        )
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

        events = [
            event
            async for event in executor.invoke_stream(
                AIRequest(
                    task_type=TaskType.GENERATION,
                    role="director",
                    provider_id="fake-provider",
                    model="fake-model",
                    input="must record receipt",
                    options={"max_tokens": 128},
                    context={"context_os_expected": True},
                )
            )
        ]

        assert [event.type.value for event in events] == ["error"]
        assert "final request receipt sink required" in str(events[0].error)
        assert provider_calls == []

    @pytest.mark.asyncio
    async def test_complete_event_omits_full_output_and_reasoning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                del prompt, model, config
                yield {
                    "type": "reasoning_chunk",
                    "reasoning": "SECRET STREAM REASONING /home/alice sk-stream-reasoning",
                }
                yield "SECRET STREAM OUTPUT /home/alice sk-stream-output"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider | None:
                assert provider_type == "fake"
                return _Provider()

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )

        executor = StreamExecutor(
            workspace=str(tmp_path),
            model_catalog=_ModelCatalog(),
            token_budget=_BudgetManager(),
            final_request_receipt_sink=lambda _receipt: None,
        )
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

        events = [
            event
            async for event in executor.invoke_stream(
                AIRequest(
                    task_type=TaskType.GENERATION,
                    role="director",
                    provider_id="fake-provider",
                    model="fake-model",
                    input="prompt",
                    options={"max_tokens": 128},
                    context={"turn_id": "turn-complete-redaction"},
                )
            )
        ]

        complete_events = [event for event in events if event.type.value == "complete"]
        assert len(complete_events) == 1
        serialized_complete = json.dumps(complete_events[0].to_dict(), ensure_ascii=False, sort_keys=True)
        assert "output_sha256" in serialized_complete
        assert "reasoning_sha256" in serialized_complete
        assert "SECRET STREAM OUTPUT" not in serialized_complete
        assert "SECRET STREAM REASONING" not in serialized_complete
        assert "sk-stream" not in serialized_complete
        assert "/home/alice" not in serialized_complete

    @pytest.mark.asyncio
    async def test_invoke_stream_compresses_chat_messages_without_flat_prompt_compression(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        receipts: list[dict[str, Any]] = []
        provider_calls: list[dict[str, Any]] = []

        class _SmallPromptBudgetManager:
            def enforce(
                self,
                prompt_input: str,
                model_spec: ModelSpec,
                *,
                requested_output_tokens: int,
                workspace: str | None,
                role: str,
                overhead_tokens: int = 0,
            ) -> TokenBudgetDecision:
                del prompt_input, model_spec, requested_output_tokens, workspace, role
                return TokenBudgetDecision(
                    allowed=True,
                    max_context_tokens=4096,
                    allowed_prompt_tokens=300,
                    requested_prompt_tokens=2000,
                    reserved_output_tokens=512,
                    safety_margin_tokens=128,
                    overhead_tokens=overhead_tokens,
                )

        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                provider_calls.append({"prompt": prompt, "model": model, "config": dict(config)})
                yield "ok"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider | None:
                assert provider_type == "fake"
                return _Provider()

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )

        executor = StreamExecutor(
            workspace=str(tmp_path),
            model_catalog=_ModelCatalog(),
            token_budget=_SmallPromptBudgetManager(),
            final_request_receipt_sink=receipts.append,
        )
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

        system_message = "system constraints " * 120
        user_message = "implement feature " * 160
        request = AIRequest(
            task_type=TaskType.GENERATION,
            role="director",
            provider_id="fake-provider",
            model="fake-model",
            input="short flattened prompt",
            options={"max_tokens": 128},
            context={
                "chat_messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message},
                ]
            },
        )

        events = [event async for event in executor.invoke_stream(request)]

        assert any(event.type.value == "complete" for event in events)
        effective_messages = provider_calls[0]["config"]["chat_messages"]
        assert len(effective_messages) == 2
        assert effective_messages[0]["content"] != system_message
        assert effective_messages[1]["content"] != user_message
        payload = receipts[0]["payload"]
        assert payload["chat_message_count_before"] == 2
        assert payload["chat_message_count_after"] == 2
        assert payload["chat_messages_compressed"] is True

    @pytest.mark.asyncio
    async def test_invoke_stream_stops_when_collected_output_exceeds_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _Provider:
            async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]):
                del prompt, model, config
                yield "12345"
                yield "67890"

        class _ProviderManager:
            def get_provider_instance(self, provider_type: str) -> _Provider | None:
                assert provider_type == "fake"
                return _Provider()

        monkeypatch.setattr(
            "polaris.kernelone.llm.engine.stream.executor._providers_module.get_provider_manager",
            lambda: _ProviderManager(),
        )

        executor = StreamExecutor(
            workspace=str(tmp_path),
            model_catalog=_ModelCatalog(),
            token_budget=_BudgetManager(),
            config=DirectStreamConfig(max_collected_chars=8),
            final_request_receipt_sink=lambda _receipt: None,
        )
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

        events = [
            event
            async for event in executor.invoke_stream(
                AIRequest(
                    task_type=TaskType.GENERATION,
                    role="director",
                    provider_id="fake-provider",
                    model="fake-model",
                    input="prompt",
                    options={"max_tokens": 128},
                    context={},
                )
            )
        ]

        assert [event.type.value for event in events] == ["chunk", "error"]
        assert "stream output exceeded accumulation limit" in str(events[-1].error)

    def test_stream_tool_argument_buffer_is_bounded(self) -> None:
        executor = StreamExecutor(config=DirectStreamConfig(max_tool_argument_chars=8))

        with pytest.raises(RuntimeError, match="stream tool arguments exceeded accumulation limit"):
            executor._accumulate_stream_tool_call(
                {},
                {"tool": "write_file", "arguments_text": "123456789", "index": 0},
                ordinal=1,
                provider_type="fake",
            )

    @pytest.mark.asyncio
    async def test_stream_to_response_returns_failure_when_collected_output_exceeds_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("KERNELONE_LLM_STREAM_MAX_COLLECTED_CHARS", "8")

        async def _events():
            yield AIStreamEvent.chunk_event("12345")
            yield AIStreamEvent.chunk_event("67890")

        response = await stream_to_response(_events())

        assert response.ok is False
        assert "stream output exceeded accumulation limit" in str(response.error)


class TestProviderSupportsStructuredStream:
    """Tests for _provider_supports_structured_stream detection."""

    def test_provider_with_invoke_stream_events(self) -> None:
        """Provider with invoke_stream_events must return True."""

        class FakeStructuredProvider:
            async def invoke_stream_events(self, prompt: str, model: str, config: dict) -> None:
                return

        provider = FakeStructuredProvider()
        assert _provider_supports_structured_stream(provider) is True

    def test_provider_without_invoke_stream_events(self) -> None:
        """Provider without invoke_stream_events must return False."""

        class FakeTextProvider:
            async def invoke_stream(self, prompt: str, model: str, config: dict) -> None:
                return

        provider = FakeTextProvider()
        assert _provider_supports_structured_stream(provider) is False


class TestNormalizeArguments:
    """Tests for _normalize_arguments utility."""

    def test_dict_arguments(self) -> None:
        """Dict arguments must be returned as-is."""
        args = {"path": "README.md", "lines": 10}
        result, complete = _normalize_arguments(args)

        assert result == args
        assert complete is True

    def test_none_arguments(self) -> None:
        """None arguments must return empty dict with False."""
        result, complete = _normalize_arguments(None)

        assert result == {}
        assert complete is False

    def test_empty_string_arguments(self) -> None:
        """Empty string arguments must return empty dict with False."""
        result, complete = _normalize_arguments("")

        assert result == {}
        assert complete is False

    def test_valid_json_string_arguments(self) -> None:
        """Valid JSON string arguments must be parsed."""
        result, complete = _normalize_arguments('{"path": "README.md"}')

        assert result == {"path": "README.md"}
        assert complete is True

    def test_invalid_json_string_arguments(self) -> None:
        """Invalid JSON string arguments must return empty dict."""
        result, complete = _normalize_arguments("not json")

        assert result == {}
        assert complete is False

    def test_primitive_value_arguments(self) -> None:
        """Primitive non-JSON values must return empty dict."""
        # str("hello") is not valid JSON, so it returns empty dict
        result, complete = _normalize_arguments("hello")

        assert result == {}
        assert complete is False


class TestToolAccumulatorKey:
    """Tests for _tool_accumulator_key utility."""

    def test_content_block_index_priority(self) -> None:
        """content_block_index must be used as primary key."""
        tool_call = {"content_block_index": 5}
        key = _tool_accumulator_key(tool_call, ordinal=0)

        assert key == "content_block_index:5"

    def test_index_fallback(self) -> None:
        """index must be used when content_block_index is absent."""
        tool_call = {"index": 3}
        key = _tool_accumulator_key(tool_call, ordinal=0)

        assert key == "index:3"

    def test_call_id_fallback(self) -> None:
        """call_id must be used when no index is present."""
        tool_call = {"call_id": "abc123"}
        key = _tool_accumulator_key(tool_call, ordinal=0)

        assert key == "call_id:abc123"

    def test_tool_name_fallback(self) -> None:
        """tool_name must be used when no call_id is present."""
        tool_call = {"tool": "read_file"}
        key = _tool_accumulator_key(tool_call, ordinal=10)

        assert key == "tool:read_file"

    def test_ordinal_fallback(self) -> None:
        """ordinal must be used as last resort."""
        tool_call: dict[str, object] = {}
        key = _tool_accumulator_key(tool_call, ordinal=42)

        assert key == "ordinal:42"

    def test_whitespace_stripping(self) -> None:
        """Whitespace in tool_name and call_id must be stripped."""
        tool_call = {"tool": "  read_file  ", "call_id": "  abc123  "}
        key = _tool_accumulator_key(tool_call, ordinal=0)

        assert key == "call_id:abc123"
