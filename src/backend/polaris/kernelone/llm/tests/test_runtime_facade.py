from __future__ import annotations

from typing import Any

import pytest
from polaris.kernelone.llm.runtime import KernelLLM


class _FakeAdapter:
    def get_role_model(self, role: str) -> tuple[str, str]:
        return "fake-provider", f"{role}-model"

    def load_provider_config(self, *, workspace: str, provider_id: str) -> dict[str, Any]:
        return {"type": "fake"}

    def get_provider_instance(self, provider_type: str) -> Any:
        return None

    def record_provider_failure(self, provider_type: str) -> None:
        return None


@pytest.mark.asyncio
async def test_kernel_llm_invoke_delegates_to_canonical_executor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from polaris.kernelone.llm.engine.contracts import AIResponse, TaskType

    captured: dict[str, Any] = {}

    class _FakeAIExecutor:
        def __init__(self, workspace: str | None = None) -> None:
            captured["workspace"] = workspace

        async def invoke(self, request: Any) -> AIResponse:
            captured["request"] = request
            return AIResponse.success(output="ok", model="fake-model", provider_id="fake-provider")

    monkeypatch.setattr("polaris.kernelone.llm.engine.executor.AIExecutor", _FakeAIExecutor)

    result = await KernelLLM(_FakeAdapter()).invoke(
        task_type="dialogue",
        role="architect",
        prompt="hello",
        options={"max_tokens": 10},
        context={"workspace": str(tmp_path)},
    )

    request = captured["request"]
    assert captured["workspace"] == str(tmp_path)
    assert request.task_type == TaskType.DIALOGUE
    assert request.role == "architect"
    assert request.input == "hello"
    assert request.options == {"max_tokens": 10}
    assert result["ok"] is True
    assert result["output"] == "ok"


@pytest.mark.asyncio
async def test_kernel_llm_invoke_stream_delegates_to_canonical_executor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from polaris.kernelone.llm.engine.contracts import AIStreamEvent, TaskType

    captured: dict[str, Any] = {}

    class _FakeAIExecutor:
        def __init__(self, workspace: str | None = None) -> None:
            captured["workspace"] = workspace

        async def invoke_stream(self, request: Any) -> Any:
            captured["request"] = request
            yield AIStreamEvent.chunk_event("partial")
            yield AIStreamEvent.complete({"output": "partial"})

    monkeypatch.setattr("polaris.kernelone.llm.engine.executor.AIExecutor", _FakeAIExecutor)

    events = [
        event
        async for event in KernelLLM(_FakeAdapter()).invoke_stream(
            task_type="generation",
            role="architect",
            prompt="hello",
            options={"temperature": 0.1},
            context={"workspace": str(tmp_path)},
        )
    ]

    request = captured["request"]
    assert captured["workspace"] == str(tmp_path)
    assert request.task_type == TaskType.GENERATION
    assert request.options == {"temperature": 0.1}
    assert events[0].chunk == "partial"
    assert events[-1].done is True
