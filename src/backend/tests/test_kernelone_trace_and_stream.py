from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from polaris.kernelone.llm.engine import stream_executor as stream_executor_module
from polaris.kernelone.llm.engine.contracts import AIRequest, ModelSpec, TaskType, TokenBudgetDecision
from polaris.kernelone.llm.engine.stream_executor import StreamExecutor
from polaris.kernelone.trace import ContextManager, PolarisContext
from polaris.kernelone.trace.context import get_trace_id
from polaris.kernelone.trace.tracer import TraceRecorder, UnifiedTracer


class _DummyModelCatalog:
    def resolve(self, provider_id: str, model: str, provider_cfg: dict[str, object]) -> ModelSpec:
        del provider_cfg
        return ModelSpec(
            provider_id=provider_id,
            provider_type="fake",
            model=model,
            max_context_tokens=4096,
            max_output_tokens=512,
        )


class _DummyBudgetManager:
    def enforce(
        self,
        prompt_input: str,
        model_spec: ModelSpec,
        *,
        requested_output_tokens: int,
        workspace: str | None,
        role: str,
    ) -> TokenBudgetDecision:
        del prompt_input, model_spec, requested_output_tokens, workspace, role
        return TokenBudgetDecision(
            allowed=True,
            max_context_tokens=4096,
            allowed_prompt_tokens=2048,
            requested_prompt_tokens=32,
            reserved_output_tokens=256,
            safety_margin_tokens=128,
        )


def test_get_trace_id_is_stable_within_same_context() -> None:
    ContextManager.clear()

    first = get_trace_id()
    second = get_trace_id()

    assert first == second


def test_unified_tracer_pops_span_stack_and_avoids_duplicate_records() -> None:
    recorder = TraceRecorder()
    tracer = UnifiedTracer(recorder=recorder)
    ContextManager.clear()
    trace_context = PolarisContext(trace_id="trace-1")

    with ContextManager.bind_context(trace_context):
        outer = tracer.start_span("outer")
        inner = tracer.start_span("inner")
        assert len(ContextManager.get_current().span_stack) == 2

        tracer.end_span(inner)
        current_after_inner = ContextManager.get_current()
        assert len(current_after_inner.span_stack) == 1
        assert current_after_inner.span_stack[-1]["span_id"] == outer.span_id

        tracer.end_span(outer)
        assert ContextManager.get_current().span_stack == []

        spans = recorder.get_trace("trace-1")
        assert [span.name for span in spans] == ["outer", "inner"]


@pytest.mark.asyncio
async def test_stream_executor_closes_async_generator_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    closed = asyncio.Event()

    class _SlowProvider:
        async def invoke_stream(
            self,
            prompt: str,
            model: str,
            config: dict[str, object],
        ):
            del prompt, model, config
            try:
                await asyncio.sleep(1.2)
                yield "late-token"
            finally:
                closed.set()

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _SlowProvider | None:
            assert provider_type == "fake"
            return _SlowProvider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.providers.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = StreamExecutor(
        workspace=str(tmp_path),
        model_catalog=_DummyModelCatalog(),
        token_budget=_DummyBudgetManager(),
    )
    monkeypatch.setattr(executor, "_resolve_provider_model", lambda request: ("fake-provider", "fake-model"))
    monkeypatch.setattr(executor, "_get_provider_config", lambda provider_id: {"type": "fake", "timeout": 0.01})

    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="director",
        input="hello",
    )

    events = [event async for event in executor.invoke_stream(request)]

    assert any(event.type.value == "error" for event in events)
    assert closed.is_set() is True


@pytest.mark.asyncio
async def test_stream_executor_decodes_structured_openai_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _StructuredProvider:
        async def invoke_stream_events(
            self,
            prompt: str,
            model: str,
            config: dict[str, object],
        ):
            del prompt, model, config
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_readme",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "{\"path\":\"README",
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {
                                        "arguments": ".md\"}",
                                    },
                                }
                            ]
                        }
                    }
                ]
            }

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _StructuredProvider | None:
            assert provider_type == "openai_compat"
            return _StructuredProvider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.providers.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = StreamExecutor(
        workspace=str(tmp_path),
        model_catalog=_DummyModelCatalog(),
        token_budget=_DummyBudgetManager(),
    )
    monkeypatch.setattr(
        executor,
        "_resolve_provider_model",
        lambda request: ("fake-provider", "gpt-5"),
    )
    monkeypatch.setattr(
        executor,
        "_get_provider_config",
        lambda provider_id: {"type": "openai_compat", "timeout": 1},
    )

    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="director",
        input="hello",
        options={
            "tools": [{"type": "function", "function": {"name": "read_file"}}],
            "tool_choice": "auto",
        },
    )

    events = [event async for event in executor.invoke_stream(request)]
    tool_call_events = [event for event in events if event.type == stream_executor_module.StreamEventType.TOOL_CALL]

    assert len(tool_call_events) == 1
    assert tool_call_events[0].tool_call == {
        "tool": "read_file",
        "arguments": {"path": "README.md"},
        "call_id": "call_readme",
        "provider_meta": {
            "provider": "openai_compat",
            "index": 0,
            "content_block_index": None,
        },
    }


@pytest.mark.asyncio
async def test_stream_executor_anthropic_partial_tool_delta_keeps_arguments_and_named_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _StructuredProvider:
        async def invoke_stream_events(
            self,
            prompt: str,
            model: str,
            config: dict[str, object],
        ):
            del prompt, model, config
            yield {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": "{\"path\":\"README",
                },
            }
            yield {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": ".md\"}",
                },
            }
            yield {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "call_readme",
                    "name": "read_file",
                    "input": {},
                },
            }

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _StructuredProvider | None:
            assert provider_type == "anthropic_compat"
            return _StructuredProvider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.providers.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = StreamExecutor(
        workspace=str(tmp_path),
        model_catalog=_DummyModelCatalog(),
        token_budget=_DummyBudgetManager(),
    )
    monkeypatch.setattr(
        executor,
        "_resolve_provider_model",
        lambda request: ("fake-provider", "claude-3-7-sonnet"),
    )
    monkeypatch.setattr(
        executor,
        "_get_provider_config",
        lambda provider_id: {"type": "anthropic_compat", "timeout": 1},
    )

    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="director",
        input="hello",
    )

    events = [event async for event in executor.invoke_stream(request)]
    tool_call_events = [
        event
        for event in events
        if event.type == stream_executor_module.StreamEventType.TOOL_CALL
    ]

    assert len(tool_call_events) == 1
    assert tool_call_events[0].tool_call == {
        "tool": "read_file",
        "arguments": {"path": "README.md"},
        "call_id": "call_readme",
        "provider_meta": {
            "provider": "anthropic_compat",
            "index": None,
            "content_block_index": 0,
        },
    }


@pytest.mark.asyncio
async def test_stream_executor_anthropic_placeholder_input_waits_for_json_delta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _StructuredProvider:
        async def invoke_stream_events(
            self,
            prompt: str,
            model: str,
            config: dict[str, object],
        ):
            del prompt, model, config
            yield {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "call_architecture",
                    "name": "read_file",
                    "input": {},
                },
            }
            for chunk in ["{\"", "file", "\":\"", "ARCH", "ITECT", "URE", ".md", "\"}"]:
                yield {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": chunk,
                    },
                }

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _StructuredProvider | None:
            assert provider_type == "anthropic_compat"
            return _StructuredProvider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.providers.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = StreamExecutor(
        workspace=str(tmp_path),
        model_catalog=_DummyModelCatalog(),
        token_budget=_DummyBudgetManager(),
    )
    monkeypatch.setattr(
        executor,
        "_resolve_provider_model",
        lambda request: ("fake-provider", "claude-3-7-sonnet"),
    )
    monkeypatch.setattr(
        executor,
        "_get_provider_config",
        lambda provider_id: {"type": "anthropic_compat", "timeout": 1},
    )

    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="chief_engineer",
        input="hello",
    )

    events = [event async for event in executor.invoke_stream(request)]
    tool_call_events = [
        event
        for event in events
        if event.type == stream_executor_module.StreamEventType.TOOL_CALL
    ]

    assert len(tool_call_events) == 1
    assert tool_call_events[0].tool_call == {
        "tool": "read_file",
        "arguments": {"file": "ARCHITECTURE.md"},
        "call_id": "call_architecture",
        "provider_meta": {
            "provider": "anthropic_compat",
            "index": None,
            "content_block_index": 0,
        },
    }


@pytest.mark.asyncio
async def test_stream_executor_decodes_structured_ollama_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _StructuredProvider:
        async def invoke_stream_events(
            self,
            prompt: str,
            model: str,
            config: dict[str, object],
        ):
            del prompt, model, config
            yield {
                "message": {
                    "thinking": "Need to inspect the README first.",
                },
                "done": False,
            }
            yield {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "read_file",
                                "arguments": {"path": "README.md"},
                            }
                        }
                    ]
                },
                "done": False,
            }

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _StructuredProvider | None:
            assert provider_type == "ollama"
            return _StructuredProvider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.providers.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = StreamExecutor(
        workspace=str(tmp_path),
        model_catalog=_DummyModelCatalog(),
        token_budget=_DummyBudgetManager(),
    )
    monkeypatch.setattr(
        executor,
        "_resolve_provider_model",
        lambda request: (
            "ollama-local",
            "modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest",
        ),
    )
    monkeypatch.setattr(
        executor,
        "_get_provider_config",
        lambda provider_id: {"type": "ollama", "timeout": 1},
    )

    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="architect",
        input="hello",
        options={
            "tools": [{"type": "function", "function": {"name": "read_file"}}],
            "tool_choice": "auto",
        },
    )

    events = [event async for event in executor.invoke_stream(request)]
    reasoning_events = [
        event for event in events if event.type == stream_executor_module.StreamEventType.REASONING_CHUNK
    ]
    tool_call_events = [
        event for event in events if event.type == stream_executor_module.StreamEventType.TOOL_CALL
    ]

    assert len(reasoning_events) == 1
    assert reasoning_events[0].reasoning == "Need to inspect the README first."
    assert len(tool_call_events) == 1
    assert tool_call_events[0].tool_call == {
        "tool": "read_file",
        "arguments": {"path": "README.md"},
        "call_id": "",
        "provider_meta": {
            "provider": "ollama",
            "index": 0,
            "content_block_index": None,
        },
    }


@pytest.mark.asyncio
async def test_stream_executor_ignores_ollama_terminal_content_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _StructuredProvider:
        async def invoke_stream_events(
            self,
            prompt: str,
            model: str,
            config: dict[str, object],
        ):
            del prompt, model, config
            yield {
                "message": {
                    "content": "hello world",
                },
                "done": False,
            }
            yield {
                "message": {
                    "content": "hello world",
                },
                "done": True,
            }

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _StructuredProvider | None:
            assert provider_type == "ollama"
            return _StructuredProvider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.providers.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = StreamExecutor(
        workspace=str(tmp_path),
        model_catalog=_DummyModelCatalog(),
        token_budget=_DummyBudgetManager(),
    )
    monkeypatch.setattr(
        executor,
        "_resolve_provider_model",
        lambda request: (
            "ollama-local",
            "modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest",
        ),
    )
    monkeypatch.setattr(
        executor,
        "_get_provider_config",
        lambda provider_id: {"type": "ollama", "timeout": 1},
    )

    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="architect",
        input="hello",
    )

    events = [event async for event in executor.invoke_stream(request)]
    chunk_events = [
        event for event in events if event.type == stream_executor_module.StreamEventType.CHUNK
    ]

    assert [event.chunk for event in chunk_events] == ["hello world"]
