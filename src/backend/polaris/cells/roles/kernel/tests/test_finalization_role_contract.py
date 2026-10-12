"""Finalization must preserve caller-owned role and output contracts."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
from polaris.cells.roles.kernel.internal.llm_caller.finalization_caller import FinalizationCaller
from polaris.cells.roles.kernel.internal.llm_caller.response_types import LLMResponse


class _CapturingInvoker:
    """Capture the request boundary without making external provider calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(content='{"status":"blocked"}', metadata={})


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["pm", "chief_engineer", "director", "qa"])
async def test_finalization_preserves_role_and_json_contract_in_invocation(role: str) -> None:
    role_system = f'<role_definition role="{role}">Use only this role authority.</role_definition>'
    output_contract = 'Output JSON only with fields "status", "evidence", and "remaining_work".'
    current_request = "请继续当前任务，缺少验证时保留阻塞，不得宣称完成。"
    original_messages = [
        {"role": "system", "content": f"{role_system}\n{output_contract}", "name": "role_prompt"},
        {"role": "system", "content": "Only committed receipts prove effects.", "name": "effect_contract"},
        {"role": "user", "content": f"[FINALIZATION]\n{current_request}\nread_file succeeded; build failed."},
    ]
    override = {
        "_transaction_kernel_prebuilt_messages": original_messages,
        "_transaction_kernel_forced_tool_definitions": [{"type": "function", "function": {"name": "read_file"}}],
        "_transaction_kernel_forced_tool_choice": "auto",
        "domain": "code",
    }
    original_override = deepcopy(override)
    strategy_receipt = object()
    context = ContextRequest(
        message=current_request,
        task_id="task-finalization",
        context_override=override,
        strategy_receipt=strategy_receipt,
        context_os_snapshot={"role": role, "evidence": "current"},
        strategy_override={"context_budget": 4096},
        run_id="run-finalization",
        events_path="runtime/events/runtime.events.jsonl",
    )
    invoker = _CapturingInvoker()
    caller = FinalizationCaller(cast(Any, invoker))

    await caller.call(
        profile=cast(Any, SimpleNamespace(role_id=role)),
        system_prompt="unused fallback role prompt",
        context=context,
        run_id="run-finalization",
        task_id="task-finalization",
        attempt=2,
        turn_round=3,
    )

    assert len(invoker.calls) == 1
    invocation = invoker.calls[0]
    projected_context = invocation["context"]
    projected_override = projected_context.context_override
    assert projected_override is not None
    messages = projected_override["_transaction_kernel_prebuilt_messages"]
    assert role_system in messages[0]["content"]
    assert output_contract in messages[0]["content"]
    assert messages[0]["name"] == "role_prompt"
    assert messages[1:] == original_messages[1:]
    assert current_request in messages[-1]["content"]
    assert projected_override["_transaction_kernel_forced_tool_definitions"] == []
    assert projected_override["_transaction_kernel_forced_tool_choice"] == "none"
    assert context.context_override == original_override
    assert projected_context.strategy_receipt is strategy_receipt
    assert projected_context.context_os_snapshot == context.context_os_snapshot
    assert projected_context.strategy_override == context.strategy_override
    assert projected_context.run_id == context.run_id
    assert projected_context.events_path == context.events_path
    assert invocation["run_id"] == "run-finalization"
    assert invocation["task_id"] == "task-finalization"
    assert invocation["attempt"] == 2
    assert invocation["turn_round"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("prebuilt", [None, []])
async def test_finalization_without_prebuilt_uses_role_prompt_and_normal_context_projection(
    prebuilt: list[dict[str, str]] | None,
) -> None:
    role_system = '<role_definition role="pm">Produce the caller-owned PM contract JSON.</role_definition>'
    override: dict[str, Any] = {"domain": "code"}
    if prebuilt is not None:
        override["_transaction_kernel_prebuilt_messages"] = prebuilt
    context = ContextRequest(
        message="修正当前合同，保留失败验证的诊断。",
        history=(("user", "此前请求"), ("assistant", "此前读取结果")),
        context_override=override,
    )
    invoker = _CapturingInvoker()

    await FinalizationCaller(cast(Any, invoker)).call(
        profile=cast(Any, SimpleNamespace(role_id="pm")), system_prompt=role_system, context=context
    )

    assert len(invoker.calls) == 1
    invocation = invoker.calls[0]
    assert role_system in invocation["system_prompt"]
    projected_context = invocation["context"]
    assert projected_context.message == context.message
    assert projected_context.history == context.history
    assert "_transaction_kernel_prebuilt_messages" not in projected_context.context_override
    assert projected_context.context_override["_transaction_kernel_forced_tool_definitions"] == []
    assert projected_context.context_override["_transaction_kernel_forced_tool_choice"] == "none"
    assert context.context_override == override


@pytest.mark.asyncio
async def test_finalization_prebuilt_without_system_prepends_caller_role_contract() -> None:
    role_system = (
        '<role_definition role="qa">Return JSON with a verdict grounded in verifier evidence.</role_definition>'
    )
    context = ContextRequest(
        message="Review the failed build.",
        context_override={"_transaction_kernel_prebuilt_messages": [{"role": "user", "content": "build exited 1"}]},
    )
    invoker = _CapturingInvoker()

    await FinalizationCaller(cast(Any, invoker)).call(
        profile=cast(Any, SimpleNamespace(role_id="qa")), system_prompt=role_system, context=context
    )

    messages = invoker.calls[0]["context"].context_override["_transaction_kernel_prebuilt_messages"]
    assert messages[0]["role"] == "system"
    assert role_system in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "build exited 1"}


@pytest.mark.asyncio
async def test_finalization_promotes_existing_role_system_before_non_system_messages() -> None:
    role_system = '<role_definition role="director">Return the execution receipt JSON.</role_definition>'
    context = ContextRequest(
        message="Report current execution evidence.",
        context_override={
            "_transaction_kernel_prebuilt_messages": [
                {"role": "user", "content": "verifier failed"},
                {"role": "system", "content": role_system},
            ]
        },
    )
    invoker = _CapturingInvoker()

    await FinalizationCaller(cast(Any, invoker)).call(
        profile=cast(Any, SimpleNamespace(role_id="director")), system_prompt="", context=context
    )

    messages = invoker.calls[0]["context"].context_override["_transaction_kernel_prebuilt_messages"]
    assert role_system in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "verifier failed"}


@pytest.mark.asyncio
async def test_finalization_does_not_assert_complete_information_or_override_output_format() -> None:
    context = ContextRequest(
        message="推进当前任务；实际证据只有读取成功和验证失败。",
        context_override={
            "_transaction_kernel_prebuilt_messages": [
                {"role": "system", "content": "PM role. Return caller-defined JSON only."},
                {"role": "user", "content": "read_file succeeded; tests exited 1; no committed write."},
            ]
        },
    )
    invoker = _CapturingInvoker()

    await FinalizationCaller(cast(Any, invoker)).call(
        profile=cast(Any, SimpleNamespace(role_id="pm")), system_prompt="", context=context
    )

    messages = invoker.calls[0]["context"].context_override["_transaction_kernel_prebuilt_messages"]
    system_prompt = messages[0]["content"]
    assert "所有信息已收集完毕" not in system_prompt
    assert "一次性完成用户请求" not in system_prompt
    assert "证据不足" in system_prompt
    assert "不得宣称" in system_prompt
    assert "输出" in system_prompt and "契约" in system_prompt
    assert "tools" not in invoker.calls[0]
