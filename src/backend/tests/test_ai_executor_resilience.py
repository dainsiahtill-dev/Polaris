from __future__ import annotations

import pytest
from polaris.kernelone.llm.engine import AIExecutor, ResilienceManager
from polaris.kernelone.llm.engine.contracts import AIRequest, AIResponse, ErrorCategory, TaskType


def test_build_request_resilience_applies_request_options() -> None:
    executor = AIExecutor()
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="hello",
        options={"timeout": 420, "total_timeout": 900, "max_retries": 0},
    )

    resilience = executor._build_request_resilience(request)

    assert isinstance(resilience, ResilienceManager)
    assert resilience.timeout_config.request_timeout == 420
    assert resilience.timeout_config.total_timeout == 900
    assert resilience.retry_config.max_attempts == 1


@pytest.mark.asyncio
async def test_invoke_with_resilience_uses_request_level_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, float | int] = {}

    async def _fake_execute_with_resilience(self, operation, operation_name):
        del operation, operation_name
        captured["request_timeout"] = self.timeout_config.request_timeout
        captured["max_attempts"] = self.retry_config.max_attempts
        return AIResponse.failure(error="timeout", category=ErrorCategory.TIMEOUT)

    monkeypatch.setattr(
        ResilienceManager,
        "execute_with_resilience",
        _fake_execute_with_resilience,
    )

    executor = AIExecutor()
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="hello",
        options={"timeout": 333, "max_retries": 2},
    )

    result = await executor._invoke_with_resilience(request, trace_id="trace-test")

    assert result.ok is False
    assert captured["request_timeout"] == 333
    assert captured["max_attempts"] == 3
