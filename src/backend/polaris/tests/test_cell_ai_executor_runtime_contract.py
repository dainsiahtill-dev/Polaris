from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.llm.provider_runtime.public.service import CellAIExecutor, CellAIRequest, TaskType


@pytest.mark.asyncio
async def test_cell_ai_executor_passes_workspace_to_kernel_llm_stream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from polaris.kernelone.llm.engine.contracts import AIStreamEvent

    captured: dict[str, Any] = {}

    class _FakeKernelLLM:
        def __init__(self, adapter: Any) -> None:
            captured["adapter"] = adapter

        async def invoke_stream(
            self,
            *,
            task_type: str,
            role: str,
            prompt: str,
            options: dict[str, Any],
            context: dict[str, Any],
        ) -> Any:
            captured["call"] = {
                "task_type": task_type,
                "role": role,
                "prompt": prompt,
                "options": options,
                "context": context,
            }
            yield AIStreamEvent.chunk_event("hello")
            yield AIStreamEvent.complete({"output": "hello"})

    monkeypatch.setattr("polaris.kernelone.llm.KernelLLM", _FakeKernelLLM)

    executor = CellAIExecutor(workspace=str(tmp_path))
    events = [
        event
        async for event in executor.invoke_stream(
            CellAIRequest(
                task_type=TaskType.GENERATION,
                role="architect",
                input="prompt",
                options={"max_tokens": 10},
            )
        )
    ]

    assert captured["call"]["context"]["workspace"] == str(tmp_path)
    assert events == [
        {"type": "chunk", "chunk": "hello"},
        {"type": "complete", "meta": {"output": "hello"}},
    ]


@pytest.mark.asyncio
async def test_cell_ai_executor_preserves_kernel_llm_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    class _FakeKernelLLM:
        def __init__(self, adapter: Any) -> None:
            self.adapter = adapter

        async def invoke(
            self,
            *,
            task_type: str,
            role: str,
            prompt: str,
            options: dict[str, Any],
            context: dict[str, Any],
        ) -> dict[str, Any]:
            return {"ok": False, "output": "", "error": "provider failed"}

    monkeypatch.setattr("polaris.kernelone.llm.KernelLLM", _FakeKernelLLM)

    executor = CellAIExecutor(workspace=str(tmp_path))
    response = await executor.invoke(CellAIRequest(task_type=TaskType.DIALOGUE, role="architect", input="prompt"))

    assert response.ok is False
    assert response.error == "provider failed"
    assert response.metadata["ok"] is False
