"""Provider result compatibility and request-local retry policy boundaries."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from polaris.kernelone.errors import ErrorCategory
from polaris.kernelone.llm.engine.contracts import AIRequest, AIResponse, ModelSpec, TaskType, Usage
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.resilience import ResilienceManager, RetryConfig
from polaris.kernelone.llm.types import InvokeResult


def test_invoke_result_typed_category_keeps_legacy_encoding() -> None:
    usage = Usage(prompt_tokens=1, completion_tokens=0, total_tokens=1)
    legacy = InvokeResult(False, "", 1, usage, "opaque", {"raw": True}, False, "reason")
    assert legacy.to_dict() == {
        "ok": False,
        "output": "",
        "latency_ms": 1,
        "usage": usage.to_dict(),
        "streaming": False,
        "error": "opaque",
        "raw": {"raw": True},
        "thinking": "reason",
    }
    delegated = InvokeResult(False, "", 1, usage, "trusted HTTP 529", error_category=ErrorCategory.SERVICE_UNAVAILABLE)
    assert delegated.to_dict()["error_category"] == ErrorCategory.SERVICE_UNAVAILABLE.value


def test_request_policy_adds_delegation_without_mutating_shared_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = AIExecutor()
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider: {"timeout": 360})
    request = AIRequest(task_type=TaskType.DIALOGUE, role="director", provider_id="synthetic", input="x")
    policy = executor._build_request_resilience(request)
    assert policy.retry_config.max_attempts == 2
    assert ErrorCategory.SERVICE_UNAVAILABLE in policy.retry_config.retryable_errors
    assert ErrorCategory.SERVICE_UNAVAILABLE not in executor.resilience.retry_config.retryable_errors
    assert ErrorCategory.SERVICE_UNAVAILABLE not in RetryConfig().retryable_errors


def test_explicit_host_denial_is_not_broadened(monkeypatch: pytest.MonkeyPatch) -> None:
    host = ResilienceManager(retry_config=RetryConfig(max_attempts=1, retryable_errors=[]))
    executor = AIExecutor(resilience=host)
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider: {})
    request = AIRequest(task_type=TaskType.DIALOGUE, role="director", input="x")
    policy = executor._build_request_resilience(request)
    assert policy.retry_config.max_attempts == 1
    assert policy.retry_config.retryable_errors == []
    assert host.retry_config.retryable_errors == []


@pytest.mark.parametrize("categories", [[], [ErrorCategory.TIMEOUT]])
def test_valid_request_options_keep_host_category_restrictions(
    categories: list[ErrorCategory], monkeypatch: pytest.MonkeyPatch
) -> None:
    host = ResilienceManager(retry_config=RetryConfig(retryable_errors=categories))
    executor = AIExecutor(resilience=host)
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider: {"timeout": 360})
    request = AIRequest(
        task_type=TaskType.DIALOGUE, role="director", provider_id="synthetic", input="x", options={"max_retries": 0}
    )
    policy = executor._build_request_resilience(request)
    assert policy.retry_config.max_attempts == 1
    assert policy.retry_config.retryable_errors == categories
    assert policy.retry_config.retryable_errors is not host.retry_config.retryable_errors


def test_invalid_options_fallback_keeps_shared_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = AIExecutor()
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider: {"timeout": 360})
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        provider_id="synthetic",
        input="x",
        options={"max_retries": "invalid"},
    )
    policy = executor._build_request_resilience(request)
    assert policy is executor.resilience
    assert ErrorCategory.SERVICE_UNAVAILABLE not in policy.retry_config.retryable_errors


async def test_actual_policy_is_task_local_ignores_forgery_and_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = AIExecutor()
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider: {"timeout": 360})
    ready = asyncio.Event()
    entered = 0
    observed: dict[str, Any] = {}
    model = ModelSpec("synthetic", "anthropic_compat", "synthetic-model", 1048576, 524288)

    def snapshot(request: AIRequest) -> dict[str, Any]:
        return executor._build_context_snapshot_provider_request(
            request=request, messages=None, invoke_cfg={}, model_spec=model, prompt_input="x"
        )

    async def execute(request: AIRequest, _trace: str, **_kwargs: Any) -> AIResponse:
        nonlocal entered
        entered += 1
        if entered == 2:
            ready.set()
        await ready.wait()
        observed[str(request.input)] = snapshot(request)["effective_caller_policy"]
        return AIResponse.success(output="ok")

    monkeypatch.setattr(executor, "_execute_invoke", execute)
    requests = [
        AIRequest(
            task_type=TaskType.DIALOGUE,
            role="director",
            provider_id="synthetic",
            model="synthetic-model",
            input=str(index),
            options={"max_retries": index, "effective_caller_policy": {"max_attempts": 99}},
            context={"effective_caller_policy": {"max_attempts": 99}},
        )
        for index in (0, 1)
    ]
    await asyncio.gather(*(executor.invoke(request) for request in requests))
    assert [observed[str(index)]["max_attempts"] for index in (0, 1)] == [1, 2]
    assert all(policy["owner"] == "AIExecutor.request" for policy in observed.values())
    assert "effective_caller_policy" not in snapshot(requests[0])
