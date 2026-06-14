"""Provider-bound ContextOS receipt coverage for AIExecutor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.llm.engine.contracts import (
    AIRequest,
    CompressionResult,
    ModelSpec,
    TaskType,
    TokenBudgetDecision,
    Usage,
)
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.types import InvokeResult


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


@pytest.mark.asyncio
async def test_final_request_receipt_records_provider_bound_shape_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipts: list[dict[str, Any]] = []
    provider_calls: list[dict[str, Any]] = []

    class _Provider:
        def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
            provider_calls.append({"prompt": prompt, "model": model, "config": dict(config)})
            return InvokeResult(
                ok=True,
                output="ok",
                latency_ms=1,
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                raw={"provider": "fake"},
            )

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _Provider | None:
            assert provider_type == "fake"
            return _Provider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.executor.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = AIExecutor(
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
        input="SECRET PROMPT TEXT",
        options={
            "max_tokens": 1024,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "parameters": {"type": "object"},
                    },
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
                "sha256": "a" * 64,
                "source": "roles.kernel.llm_caller.pre_projection",
            },
            "context_projection_id": "projection-1",
            "chat_messages": [
                {"role": "system", "content": "SECRET SYSTEM TEXT"},
                {"role": "user", "content": "SECRET PROMPT TEXT"},
            ],
        },
    )

    response = await executor.invoke(request)

    assert response.ok is True
    assert len(provider_calls) == 1
    assert len(receipts) == 1

    receipt = receipts[0]
    assert receipt["receipt_type"] == "contextos.final_request"
    assert receipt["trace_refs"]

    payload = receipt["payload"]
    assert payload["provider_id"] == "fake-provider"
    assert payload["provider_type"] == "fake"
    assert payload["model"] == "fake-model"
    assert payload["turn_id"] == "turn-1"
    assert payload["attempt"] == 2
    assert payload["fix_attempt_id"] == "fix-1"
    assert payload["context_projection_id"] == "projection-1"
    assert payload["capability_profile_sha256"] == "a" * 64
    assert payload["capability_profile_source"] == "roles.kernel.llm_caller.pre_projection"
    assert len(payload["budget_admission_id"]) == 64
    assert payload["model_window_tokens"] == 4096
    assert payload["requested_output_tokens"] == 1024
    assert payload["final_max_tokens"] == provider_calls[0]["config"]["max_tokens"]
    assert payload["payload_overhead_tokens"] > 0
    assert payload["token_budget"]["overhead_tokens"] == payload["payload_overhead_tokens"]
    assert payload["chat_message_count_before"] == 2
    assert payload["chat_message_count_after"] == 2
    assert payload["tool_count"] == 1
    assert len(payload["input_sha256"]) == 64
    assert len(payload["tool_schema_sha256"]) == 64

    serialized_receipt = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "SECRET PROMPT TEXT" not in serialized_receipt
    assert "SECRET SYSTEM TEXT" not in serialized_receipt


@pytest.mark.asyncio
async def test_final_request_receipt_redacts_compression_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipts: list[dict[str, Any]] = []

    class _Provider:
        def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
            del prompt, model, config
            return InvokeResult(
                ok=True,
                output="ok",
                latency_ms=1,
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                raw={"provider": "fake"},
            )

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _Provider | None:
            assert provider_type == "fake"
            return _Provider()

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
                    compressed_input="SECRET COMPRESSED PROMPT /home/alice sk-live-secret",
                    original_tokens=4096,
                    compressed_tokens=900,
                    strategy="hard_trim",
                    quality_flag="degraded",
                    drop_ratio=0.78,
                    notes=["contains secret preview"],
                ),
                overhead_tokens=overhead_tokens,
            )

    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.executor.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = AIExecutor(
        workspace=str(tmp_path),
        model_catalog=_ModelCatalog(),
        token_budget=_CompressingBudgetManager(),
        final_request_receipt_sink=receipts.append,
    )
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

    response = await executor.invoke(
        AIRequest(
            task_type=TaskType.GENERATION,
            role="director",
            provider_id="fake-provider",
            model="fake-model",
            input="SECRET INPUT",
            options={"max_tokens": 512},
            context={"turn_id": "turn-compressed", "context_projection_id": "projection-compressed"},
        )
    )

    assert response.ok is True
    assert len(receipts) == 1
    serialized_receipt = json.dumps(receipts[0], ensure_ascii=False, sort_keys=True)
    serialized_response = json.dumps(response.to_dict(), ensure_ascii=False, sort_keys=True)
    assert "compressed_input" not in serialized_receipt
    assert "compressed_input" not in serialized_response
    assert "SECRET COMPRESSED PROMPT" not in serialized_receipt
    assert "SECRET COMPRESSED PROMPT" not in serialized_response
    assert "sk-live-secret" not in serialized_receipt
    assert "sk-live-secret" not in serialized_response
    assert "/home/alice" not in serialized_receipt
    assert "/home/alice" not in serialized_response
    assert "contains secret preview" not in serialized_response
    assert receipts[0]["payload"]["token_budget"]["compression"]["compressed_tokens"] == 900
    assert receipts[0]["payload"]["token_budget"]["compression"]["compressed_text_sha256"]


@pytest.mark.asyncio
async def test_required_final_request_receipt_sink_failure_blocks_invoke(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Provider:
        def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
            del prompt, model, config
            return InvokeResult(ok=True, output="should not run", latency_ms=1)

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _Provider | None:
            assert provider_type == "fake"
            return _Provider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.executor.get_provider_manager",
        lambda: _ProviderManager(),
    )

    def _failing_sink(_receipt: dict[str, Any]) -> None:
        raise RuntimeError("receipt store unavailable")

    executor = AIExecutor(
        workspace=str(tmp_path),
        model_catalog=_ModelCatalog(),
        token_budget=_BudgetManager(),
        final_request_receipt_sink=_failing_sink,
    )
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

    response = await executor.invoke(
        AIRequest(
            task_type=TaskType.GENERATION,
            role="director",
            provider_id="fake-provider",
            model="fake-model",
            input="must record receipt",
            options={"max_tokens": 128},
            context={
                "turn_id": "turn-required",
                "context_os_expected": True,
                "cognitive_runtime_required": True,
            },
        )
    )

    assert response.ok is False
    assert "receipt store unavailable" in str(response.error)


@pytest.mark.asyncio
async def test_required_final_request_receipt_missing_sink_blocks_invoke(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_calls: list[str] = []

    class _Provider:
        def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
            del model, config
            provider_calls.append(prompt)
            return InvokeResult(ok=True, output="should not run", latency_ms=1)

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _Provider | None:
            assert provider_type == "fake"
            return _Provider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.executor.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = AIExecutor(
        workspace=str(tmp_path),
        model_catalog=_ModelCatalog(),
        token_budget=_BudgetManager(),
    )
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

    response = await executor.invoke(
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

    assert response.ok is False
    assert "final request receipt sink required" in str(response.error)
    assert provider_calls == []


@pytest.mark.asyncio
async def test_provider_raw_payload_is_recursively_redacted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Provider:
        def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
            del prompt, model, config
            return InvokeResult(
                ok=True,
                output="ok",
                latency_ms=1,
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                raw={
                    "provider": "fake",
                    "compressed_input": "SECRET RAW PROMPT /home/alice sk-raw-secret",
                    "nested": {
                        "notes": ["SECRET RAW NOTE /home/alice sk-raw-note"],
                        "safe": "kept",
                    },
                    "path_hint": "/home/alice sk-raw-path",
                },
            )

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _Provider | None:
            assert provider_type == "fake"
            return _Provider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.executor.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = AIExecutor(
        workspace=str(tmp_path),
        model_catalog=_ModelCatalog(),
        token_budget=_BudgetManager(),
    )
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

    response = await executor.invoke(
        AIRequest(
            task_type=TaskType.GENERATION,
            role="director",
            provider_id="fake-provider",
            model="fake-model",
            input="prompt",
            options={"max_tokens": 128},
            context={"context_os_expected": "false"},
        )
    )

    assert response.ok is True
    serialized_response = json.dumps(response.to_dict(), ensure_ascii=False, sort_keys=True)
    assert "compressed_input" not in serialized_response
    assert "SECRET RAW PROMPT" not in serialized_response
    assert "SECRET RAW NOTE" not in serialized_response
    assert "sk-raw" not in serialized_response
    assert "/home/alice" not in serialized_response
    assert response.raw["provider"] == "fake"
    assert response.raw["nested"]["safe"] == "kept"


@pytest.mark.asyncio
async def test_final_request_receipt_sink_failure_does_not_block_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_calls: list[dict[str, Any]] = []

    class _Provider:
        def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
            provider_calls.append({"prompt": prompt, "model": model, "config": dict(config)})
            return InvokeResult(
                ok=True,
                output="ok",
                latency_ms=1,
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                raw={"provider": "fake"},
            )

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _Provider | None:
            assert provider_type == "fake"
            return _Provider()

    def _broken_sink(_receipt: dict[str, Any]) -> None:
        raise AttributeError("receipt sink shape changed")

    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.executor.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = AIExecutor(
        workspace=str(tmp_path),
        model_catalog=_ModelCatalog(),
        token_budget=_BudgetManager(),
        final_request_receipt_sink=_broken_sink,
    )
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="director",
        provider_id="fake-provider",
        model="fake-model",
        input="prompt",
        options={"max_tokens": 128},
        context={"run_id": "run-1", "task_id": "task-1"},
    )

    response = await executor.invoke(request)

    assert response.ok is True
    assert len(provider_calls) == 1


@pytest.mark.asyncio
async def test_final_request_receipt_marks_same_count_chat_message_compression(
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
        def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
            provider_calls.append({"prompt": prompt, "model": model, "config": dict(config)})
            return InvokeResult(
                ok=True,
                output="ok",
                latency_ms=1,
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                raw={"provider": "fake"},
            )

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _Provider | None:
            assert provider_type == "fake"
            return _Provider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.executor.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = AIExecutor(
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
        input=user_message,
        options={"max_tokens": 128},
        context={
            "chat_messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ]
        },
    )

    response = await executor.invoke(request)

    assert response.ok is True
    assert len(provider_calls) == 1
    effective_messages = provider_calls[0]["config"]["chat_messages"]
    assert len(effective_messages) == 2
    assert effective_messages[0]["content"] != system_message
    assert effective_messages[1]["content"] != user_message
    payload = receipts[0]["payload"]
    assert payload["chat_message_count_before"] == 2
    assert payload["chat_message_count_after"] == 2
    assert payload["chat_messages_compressed"] is True


@pytest.mark.asyncio
async def test_executor_drops_structured_chat_messages_when_budget_compression_has_no_fit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipts: list[dict[str, Any]] = []
    provider_calls: list[dict[str, Any]] = []

    class _ImpossibleChatBudgetManager:
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
                allowed_prompt_tokens=1,
                requested_prompt_tokens=2000,
                reserved_output_tokens=512,
                safety_margin_tokens=128,
                overhead_tokens=overhead_tokens,
            )

    class _Provider:
        def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
            provider_calls.append({"prompt": prompt, "model": model, "config": dict(config)})
            return InvokeResult(
                ok=True,
                output="ok",
                latency_ms=1,
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                raw={"provider": "fake"},
            )

    class _ProviderManager:
        def get_provider_instance(self, provider_type: str) -> _Provider | None:
            assert provider_type == "fake"
            return _Provider()

    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.executor.get_provider_manager",
        lambda: _ProviderManager(),
    )

    executor = AIExecutor(
        workspace=str(tmp_path),
        model_catalog=_ModelCatalog(),
        token_budget=_ImpossibleChatBudgetManager(),
        final_request_receipt_sink=receipts.append,
    )
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"type": "fake", "timeout": 1})

    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="director",
        provider_id="fake-provider",
        model="fake-model",
        input="fallback flattened prompt",
        options={"max_tokens": 128},
        context={
            "chat_messages": [
                {"role": "system", "content": "system constraints " * 120},
                {"role": "user", "content": "implement feature " * 160},
            ]
        },
    )

    response = await executor.invoke(request)

    assert response.ok is True
    assert len(provider_calls) == 1
    assert "chat_messages" not in provider_calls[0]["config"]
    payload = receipts[0]["payload"]
    assert payload["chat_message_count_before"] == 2
    assert payload["chat_message_count_after"] == 0
    assert payload["chat_messages_compressed"] is True
