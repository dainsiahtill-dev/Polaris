"""Provider-bound ContextOS receipt coverage for AIExecutor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.llm.engine.contracts import (
    AIRequest,
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
            "native_tool_mode": "native_tools",
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
