from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.roles.kernel.internal.interaction_contract import TurnIntent, infer_turn_intent
from polaris.cells.roles.kernel.internal.kernel.prompt_assembly import resolve_prompt_layer_options
from polaris.cells.roles.kernel.internal.kernel.request_tool_gating import (
    tool_contract_requires_no_tools,
)
from polaris.cells.roles.kernel.internal.llm_caller.finalization_caller import FinalizationCaller
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    resolve_mutation_target_guard_violation,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import requires_mutation_intent
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.retry_context_builders import (
    build_retry_write_after_bootstrap_context,
)
from polaris.cells.roles.kernel.internal.transaction.retry_escalation_policy import resolve_retry_model_override
from polaris.cells.roles.kernel.internal.transaction.retry_tool_definitions import (
    bootstrap_receipt_contains_whole_file_replacement_marker,
    build_forced_write_only_retry_tool_definitions,
    build_retry_tool_definitions_for_mutation,
    select_bootstrap_followup_write_tool_name,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    build_single_batch_task_contract_hint,
    extract_allowed_tool_names_from_definitions,
)
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    fill_content_only_write_file_from_remaining_targets,
    fill_single_target_line_range_edit_blocks,
    rewrite_existing_file_paths_in_invocations,
)
from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnStateMachine
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TurnTransactionController
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    RawLLMResponse,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
    classify_tool_invocation,
)
from polaris.cells.roles.profile.public.service import RoleTurnRequest
from polaris.kernelone.context.contracts import TurnEngineContextRequest

_WRITE_TOOL_NAMES = frozenset({"edit_blocks", "edit_file", "write_file"})


def _canonical_decision(
    *,
    turn_id: str,
    kind: TurnDecisionKind,
    invocations: list[Mapping[str, Any]] | None = None,
    visible_message: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> TurnDecision:
    """Build a complete public turn decision for orchestration-focused tests."""

    normalized_invocations: list[ToolInvocation] = []
    for index, invocation in enumerate(invocations or []):
        tool_name = str(invocation.get("tool_name") or "")
        classification = classify_tool_invocation(tool_name)
        normalized_invocations.append(
            ToolInvocation(
                call_id=ToolCallId(str(invocation.get("call_id") or f"{turn_id}_call_{index + 1}")),
                tool_name=tool_name,
                arguments=dict(cast(Mapping[str, Any], invocation.get("arguments") or {})),
                effect_type=classification.effect_type,
                execution_mode=classification.execution_mode,
            )
        )

    tool_batch = None
    if kind == TurnDecisionKind.TOOL_BATCH:
        tool_batch = ToolBatch(
            batch_id=BatchId(f"{turn_id}_batch"),
            invocations=normalized_invocations,
        )

    return TurnDecision(
        turn_id=TurnId(turn_id),
        kind=kind,
        visible_message=visible_message,
        tool_batch=tool_batch,
        finalize_mode=FinalizeMode.NONE,
        domain="code",
        metadata=dict(metadata or {}),
    )


def _successful_write_tool_result(file_path: str, *, bytes_written: int = 1) -> dict[str, Any]:
    """Return the production write-tool result shape with effect evidence."""

    return {
        "ok": True,
        "file": file_path,
        "bytes_written": bytes_written,
        "effect_receipt": {
            "file": file_path,
            "bytes_written": bytes_written,
            "operation": "write_file",
        },
    }


def _successful_write_batch_result(
    file_path: str,
    *,
    tool_name: str = "write_file",
    visible_content: str = "",
) -> dict[str, Any]:
    """Return a successful batch carrying authoritative write-effect evidence."""

    effect_receipt = {
        "file": file_path,
        "bytes_written": 1,
        "operation": tool_name,
    }
    return {
        "kind": "tool_batch_with_receipt",
        "visible_content": visible_content,
        "batch_receipt": {
            "batch_id": f"batch_{tool_name}",
            "results": [
                {
                    "call_id": f"call_{tool_name}",
                    "tool_name": tool_name,
                    "status": "success",
                    "result": {"file": file_path},
                    "effect_receipt": effect_receipt,
                }
            ],
            "success_count": 1,
            "failure_count": 0,
            "effect_receipts": [effect_receipt],
        },
    }


@pytest.mark.asyncio
async def test_transaction_kernel_executes_single_transaction_turn() -> None:
    llm = AsyncMock(
        return_value={
            "content": "Final answer.",
            "model": "test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
    )
    tool_runtime = AsyncMock()
    kernel = TransactionKernel(llm_provider=llm, tool_runtime=tool_runtime, config=TransactionConfig(domain="code"))

    result = await kernel.execute(
        turn_id="turn_tx",
        context=[{"role": "user", "content": "say hi"}],
        tool_definitions=[],
    )

    assert result["turn_id"] == "turn_tx"
    assert result["kind"] == "final_answer"
    assert result["metrics"]["llm_calls"] == 1
    assert result["metrics"]["tool_calls"] == 0


@pytest.mark.asyncio
async def test_transaction_kernel_execute_forwards_tool_choice_override_to_provider_request() -> None:
    llm = AsyncMock(
        return_value={
            "content": "Final answer.",
            "model": "test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
    )
    tool_runtime = AsyncMock()
    kernel = TransactionKernel(llm_provider=llm, tool_runtime=tool_runtime, config=TransactionConfig(domain="code"))
    forced_choice = {"type": "function", "function": {"name": "write_file"}}
    tool_definitions = [{"type": "function", "function": {"name": "write_file"}}]

    result = await kernel.execute(
        turn_id="turn_tx",
        context=[{"role": "user", "content": "say hi"}],
        tool_definitions=tool_definitions,
        tool_choice_override=forced_choice,
    )

    assert result["kind"] == "final_answer"
    assert llm.await_args is not None
    assert llm.await_args.args[0]["tools"] == tool_definitions
    assert llm.await_args.args[0]["tool_choice"] == forced_choice




def test_build_decision_messages_adds_equivalent_hint_for_missing_required_tool() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    context = [
        {
            "role": "user",
            "content": "在 server.py 中查找并替换 localhost。",
            "metadata": {
                "tool_contract": {
                    "single_batch": True,
                    "required_tools": ["repo_rg", "search_replace"],
                    "min_tool_calls": 2,
                    "allow_mixed_read_write_batch": True,
                }
            },
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "repo_rg"}},
        {"type": "function", "function": {"name": "edit_blocks"}},
    ]
    messages = controller._build_decision_messages(context, tool_definitions)
    system_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "system"]

    assert any(
        "Required contract tool `search_replace` is not exposed in this profile" in text for text in system_messages
    )
    assert any("edit_blocks" in text for text in system_messages)


def test_build_decision_messages_includes_required_groups_and_min_calls_hint() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    context = [
        {
            "role": "user",
            "content": "把 config.py 里的 DEBUG = True 改成 False。",
            "metadata": {
                "tool_contract": {
                    "single_batch": True,
                    "required_tool_groups": [
                        ["read_file", "repo_read_head"],
                        ["search_replace", "edit_blocks"],
                    ],
                    "min_tool_calls": 2,
                    "allow_mixed_read_write_batch": True,
                }
            },
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "edit_blocks"}},
    ]

    messages = controller._build_decision_messages(context, tool_definitions)
    system_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "system"]

    assert any("Contract-required tool groups must all be satisfied" in text for text in system_messages)
    assert any("Contract minimum tool-call count for this batch: >= 2." in text for text in system_messages)
    assert any("A single read-only tool call is invalid" in text for text in system_messages)


def test_tool_contract_requires_no_tools_uses_platform_metadata_only() -> None:
    legacy_marker = "[" + "Benchmark Tool " + "Contract]"
    request_from_prompt = RoleTurnRequest(
        message=f"{legacy_marker}\nDo not call any tools for this case.",
        metadata={},
    )
    request_from_metadata = RoleTurnRequest(
        message="normal request",
        metadata={"tool_contract_require_no_tool_calls": True},
    )
    request_from_nested_context = RoleTurnRequest(
        message="normal request",
        context_override={"tool_contract": {"require_no_tool_calls": True}},
    )
    normal_request = RoleTurnRequest(message="read and summarize README", metadata={})

    assert tool_contract_requires_no_tools(request_from_prompt) is False
    assert tool_contract_requires_no_tools(request_from_metadata) is True
    assert tool_contract_requires_no_tools(request_from_nested_context) is True
    assert tool_contract_requires_no_tools(normal_request) is False


def test_prompt_layer_single_batch_uses_platform_contract_not_benchmark_text() -> None:
    marker_only_options = resolve_prompt_layer_options(
        {},
        message="Tool call count must be between 1 and 2.",
    )
    contract_options = resolve_prompt_layer_options(
        {"tool_contract": {"single_batch": True}},
        message="Tool call count must be between 1 and 2.",
    )

    assert marker_only_options == {}
    assert contract_options == {"include_working_memory_contract": False}


def test_build_finalization_context_keeps_latest_user_request() -> None:
    from polaris.cells.roles.kernel.internal.transaction.finalization import FinalizationHandler

    context = [{"role": "user", "content": "请开始全量落地项目代码并运行测试"}]
    receipts = [
        {
            "results": [
                {
                    "tool_name": "read_file",
                    "status": "success",
                    "result": {"file": "app.py", "line_count": 120},
                }
            ]
        }
    ]

    # ANALYZE_ONLY 模式下应保留 user request 且提示词不鼓励贴完整代码
    messages = FinalizationHandler._build_finalization_context(context, receipts)

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content = str(messages[0]["content"])
    assert "请开始全量落地项目代码并运行测试" in content
    # 新提示词已移除鼓励贴代码的表述
    assert "一次性完成输出" not in content
    assert "直接给出完整答复" not in content
    assert "不要贴出完整文件内容" in content


async def test_finalization_caller_execution_prompt_overrides_analysis_template() -> None:
    invoker = _StubFinalizationInvoker()
    caller = FinalizationCaller(invoker)  # type: ignore[arg-type]
    context = TurnEngineContextRequest(
        message="继续全量推进并落地代码修复",
        history=(),
        context_override={
            "_transaction_kernel_prebuilt_messages": [
                {"role": "system", "content": "legacy system prompt"},
                {"role": "user", "content": "legacy user prompt"},
            ]
        },
    )

    await caller.call(
        profile=cast(Any, SimpleNamespace(role_id="director")),
        system_prompt="ignored",
        context=context,
    )

    captured = invoker.captured_context
    assert captured is not None
    prebuilt = list((captured.context_override or {}).get("_transaction_kernel_prebuilt_messages", []))
    assert prebuilt
    system_prompt = str(prebuilt[0].get("content", ""))
    assert "当前用户请求是推进/落地任务" in system_prompt
    assert "历史上下文（后者仅作参考" in system_prompt
    assert "资深技术审计官" not in system_prompt


def test_infer_turn_intent_treats_luodi_tuijin_as_execute() -> None:
    intent = infer_turn_intent(role_id="director", message="请继续推进并落地所有代码改动", domain="code")
    assert intent is TurnIntent.EXECUTE


def test_infer_turn_intent_prefers_execute_when_review_and_execute_coexist() -> None:
    intent = infer_turn_intent(role_id="director", message="请落地代码修复并验证结果", domain="code")
    assert intent is TurnIntent.EXECUTE


def test_tool_batch_write_detection_supports_tool_invocation_models() -> None:
    invocations = [
        ToolInvocation(
            call_id=ToolCallId("call_write"),
            tool_name="edit_file",
            arguments={"file": "README.md"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
    ]
    from polaris.cells.roles.kernel.internal.transaction.contract_guards import tool_batch_has_write_invocation

    assert tool_batch_has_write_invocation(invocations) is True


@pytest.mark.asyncio
async def test_execute_turn_stream_yields_completion_after_mutation_contract_retry(monkeypatch) -> None:
    """Regression: stream must yield CompletionEvent after mutation-contract retry succeeds.

    When the LLM emits a read-only tool batch for a mutation request, the controller
    retries with a forced write tool. After the retry succeeds, the stream must still
    yield a CompletionEvent so the CLI does not hang or return without output.
    """
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code", mutation_guard_mode="strict"),
        llm_provider_stream=AsyncMock(),
    )
    state_machine = TurnStateMachine(turn_id="turn_stream_retry")
    ledger = TurnLedger(turn_id="turn_stream_retry")
    context = [{"role": "user", "content": "落地高优先级的任务"}]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]

    async def _fake_call_llm_for_decision_stream(
        ctx,
        tool_definitions,
        llm_ledger,
        shadow_engine=None,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        yield {
            "type": "_internal_materialize",
            "response": RawLLMResponse(
                content="",
                native_tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"file": "tasks.md"}'},
                    }
                ],
            ),
        }

    def _fake_decode(_response, _turn_id):
        return _canonical_decision(
            turn_id="turn_stream_retry",
            kind=TurnDecisionKind.TOOL_BATCH,
            invocations=[{"tool_name": "read_file", "arguments": {"file": "tasks.md"}}],
        )

    async def _fake_retry(
        *, turn_id, context, tool_definitions, state_machine, ledger, stream, shadow_engine, **_kwargs
    ):
        result = _successful_write_batch_result(
            "高优先级任务清单.md",
            visible_content="已写入 高优先级任务清单.md",
        )
        result["metrics"] = {"duration_ms": 100, "llm_calls": 2, "tool_calls": 1}
        return result

    monkeypatch.setattr(
        controller._stream_orchestrator,
        "_call_llm_for_decision_stream_impl",
        _fake_call_llm_for_decision_stream,
    )
    monkeypatch.setattr(controller.decoder, "decode", _fake_decode)
    monkeypatch.setattr(
        controller._retry_orchestrator,
        "retry_tool_batch_after_contract_violation",
        _fake_retry,
    )

    events: list[Any] = []
    async for event in controller._stream_orchestrator.execute_turn_stream(
        turn_id="turn_stream_retry",
        context=context,
        tool_definitions=tool_definitions,
        state_machine=state_machine,
        ledger=ledger,
    ):
        events.append(event)

    from polaris.cells.roles.kernel.public.turn_events import CompletionEvent, ContentChunkEvent, ToolBatchEvent

    tool_batch_events = [e for e in events if isinstance(e, ToolBatchEvent)]
    content_chunks = [e for e in events if isinstance(e, ContentChunkEvent)]
    completions = [e for e in events if isinstance(e, CompletionEvent)]

    assert len(tool_batch_events) == 1, f"Expected 1 ToolBatchEvent, got {len(tool_batch_events)}: {events}"
    assert tool_batch_events[0].tool_name == "write_file"
    assert len(content_chunks) == 1, f"Expected 1 ContentChunkEvent, got {len(content_chunks)}: {events}"
    assert content_chunks[0].chunk == "已写入 高优先级任务清单.md"
    assert len(completions) == 1, f"Expected 1 CompletionEvent, got {len(completions)}: {events}"
    assert completions[0].status == "success"


@pytest.mark.asyncio
async def test_execute_turn_stream_passes_narrowed_tool_names_to_direct_batch_executor(monkeypatch) -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
        llm_provider_stream=AsyncMock(),
    )
    state_machine = TurnStateMachine(turn_id="turn_stream_allowed_tools")
    ledger = TurnLedger(turn_id="turn_stream_allowed_tools")
    context = [{"role": "user", "content": "请只读取 README.md 的必要片段"}]
    tool_definitions = [{"type": "function", "function": {"name": "repo_read_slice"}}]
    captured: dict[str, Any] = {}

    async def _fake_call_llm_for_decision_stream(
        ctx,
        tool_definitions,
        llm_ledger,
        shadow_engine=None,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        yield {
            "type": "_internal_materialize",
            "response": RawLLMResponse(content="", native_tool_calls=[]),
        }

    def _fake_decode(_response, _turn_id):
        return _canonical_decision(
            turn_id="turn_stream_allowed_tools",
            kind=TurnDecisionKind.TOOL_BATCH,
            invocations=[
                {
                    "tool_name": "repo_read_slice",
                    "arguments": {"file": "README.md", "start": 1, "end": 20},
                }
            ],
        )

    async def _fake_execute_tool_batch(
        decision,
        state_machine,
        ledger,
        context,
        *,
        stream=False,
        shadow_engine=None,
        allowed_tool_names=None,
        **_kwargs,
    ):
        captured["decision"] = decision
        captured["stream"] = stream
        captured["allowed_tool_names"] = allowed_tool_names
        return {
            "kind": "tool_batch_with_receipt",
            "visible_content": "已读取",
            "batch_receipt": {
                "batch_id": "batch_allowed_tools",
                "results": [
                    {
                        "tool_name": "repo_read_slice",
                        "call_id": "call_allowed_tools",
                        "status": "success",
                        "result": "README slice",
                    }
                ],
            },
        }

    monkeypatch.setattr(
        controller._stream_orchestrator,
        "_call_llm_for_decision_stream_impl",
        _fake_call_llm_for_decision_stream,
    )
    monkeypatch.setattr(controller._stream_orchestrator.decoder, "decode", _fake_decode)
    monkeypatch.setattr(
        controller._stream_orchestrator.tool_batch_executor,
        "execute_tool_batch",
        _fake_execute_tool_batch,
    )

    events: list[Any] = []
    async for event in controller._stream_orchestrator.execute_turn_stream(
        turn_id="turn_stream_allowed_tools",
        context=context,
        tool_definitions=tool_definitions,
        state_machine=state_machine,
        ledger=ledger,
    ):
        events.append(event)

    from polaris.cells.roles.kernel.public.turn_events import CompletionEvent, ToolBatchEvent

    tool_batch_events = [e for e in events if isinstance(e, ToolBatchEvent)]
    completions = [e for e in events if isinstance(e, CompletionEvent)]

    assert captured["stream"] is True
    assert captured["allowed_tool_names"] == {"repo_read_slice"}
    assert len(tool_batch_events) == 1
    assert tool_batch_events[0].tool_name == "repo_read_slice"
    assert len(completions) == 1
    assert completions[0].status == "success"


@pytest.mark.asyncio
async def test_execute_turn_stream_fails_closed_when_native_tool_call_decodes_without_batch(monkeypatch) -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
        llm_provider_stream=AsyncMock(),
    )
    state_machine = TurnStateMachine(turn_id="turn_stream_dropped_tool")
    ledger = TurnLedger(turn_id="turn_stream_dropped_tool")
    context = [{"role": "user", "content": "请读取 README.md"}]
    tool_definitions = [{"type": "function", "function": {"name": "repo_read_slice"}}]

    async def _fake_call_llm_for_decision_stream(
        ctx,
        tool_definitions,
        llm_ledger,
        shadow_engine=None,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        del ctx, tool_definitions, llm_ledger, shadow_engine
        del tool_choice_override, model_override, temperature_override, max_tokens_floor
        yield {
            "type": "_internal_materialize",
            "response": RawLLMResponse(
                content="",
                native_tool_calls=[
                    {
                        "id": "call_stream_drop",
                        "function": {
                            "name": "repo_read_slice",
                            "arguments": '{"file":"README.md","start":1,"end":20}',
                        },
                    }
                ],
            ),
        }

    def _fake_decode(_response, _turn_id):
        return {
            "kind": TurnDecisionKind.FINAL_ANSWER,
            "turn_id": "turn_stream_dropped_tool",
            "visible_message": "",
            "finalize_mode": "answer",
            "domain": "code",
        }

    monkeypatch.setattr(
        controller._stream_orchestrator,
        "_call_llm_for_decision_stream_impl",
        _fake_call_llm_for_decision_stream,
    )
    monkeypatch.setattr(controller._stream_orchestrator.decoder, "decode", _fake_decode)

    with pytest.raises(RuntimeError, match="tool_dispatch_dropped"):
        async for _event in controller._stream_orchestrator.execute_turn_stream(
            turn_id="turn_stream_dropped_tool",
            context=context,
            tool_definitions=tool_definitions,
            state_machine=state_machine,
            ledger=ledger,
        ):
            pass

    dropped_flags = [
        item for item in ledger.anomaly_flags if isinstance(item, dict) and item.get("type") == "TOOL_DISPATCH_DROPPED"
    ]
    assert len(dropped_flags) == 1
    assert dropped_flags[0]["native_tool_calls_count"] == 1
    assert dropped_flags[0]["streaming"] is True
    assert dropped_flags[0]["provider_response_hash"]
    lifecycle = dropped_flags[0]["tool_call_lifecycle_receipt"]
    assert lifecycle["native_tool_calls_count"] == dropped_flags[0]["native_tool_calls_count"]
    assert lifecycle["provider_response_hash"] == dropped_flags[0]["provider_response_hash"]
    assert lifecycle["dispatch_status"] == "dropped"


@pytest.mark.asyncio
async def test_execute_stream_yields_completion_after_mutation_contract_retry_real_path(monkeypatch) -> None:
    """End-to-end: TransactionKernel.execute_stream must yield CompletionEvent after retry.

    This test does NOT mock _retry_tool_batch_after_contract_violation;
    it mocks only the LLM stream to exercise the real retry path.
    """

    from polaris.cells.roles.kernel.public.turn_events import CompletionEvent, ToolBatchEvent

    call_ordinal = 0

    async def _fake_call_llm_for_decision_stream(
        ctx,
        tool_definitions,
        ledger,
        shadow_engine=None,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        """Yield _internal_materialize events directly, bypassing StreamEventHandler."""
        nonlocal call_ordinal
        call_ordinal += 1
        # 直接返回包含 write_file 的决策，避免 mutation bypass 阻断 LLM_ONCE
        arguments = {"file": "tasks.md", "content": "hi"}
        call_id = f"call_{call_ordinal}"
        if shadow_engine is not None:
            await shadow_engine.speculate_tool_call(
                tool_name="write_file",
                arguments=arguments,
                call_id=call_id,
                turn_id=ledger.turn_id,
            )
        yield {
            "type": "_internal_materialize",
            "response": RawLLMResponse(
                content="",
                native_tool_calls=[
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": arguments,
                        },
                    }
                ],
            ),
        }

    kernel = TransactionKernel(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value=_successful_write_tool_result("tasks.md", bytes_written=2)),
        config=TransactionConfig(domain="code"),
        llm_provider_stream=AsyncMock(),  # Non-None so retry path uses stream materialization.
    )

    # Monkeypatch the stream owner to inject RawLLMResponse directly.
    monkeypatch.setattr(
        kernel._stream_orchestrator,
        "_call_llm_for_decision_stream_impl",
        _fake_call_llm_for_decision_stream,
    )

    context = [{"role": "user", "content": "落地高优先级的任务"}]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]

    events: list[Any] = []
    async for event in kernel.execute_stream("turn_retry_e2e", context, tool_definitions):
        events.append(event)

    completions = [e for e in events if isinstance(e, CompletionEvent)]
    tool_batches = [e for e in events if isinstance(e, ToolBatchEvent)]

    # The stream MUST contain a CompletionEvent
    assert len(completions) == 1, (
        f"Expected 1 CompletionEvent, got {len(completions)} in {len(events)} events: {[type(e).__name__ for e in events]}"
    )
    assert completions[0].status == "success"
    # There should be at least one tool batch event (the write_file result)
    assert len(tool_batches) >= 1


@pytest.mark.asyncio
async def test_execute_stream_mutation_retry_from_ask_user_yields_completion_no_error_event(monkeypatch) -> None:
    """Bug 3 regression: initial ASK_USER + mutation retry must not leak ErrorEvent.

    When the LLM's initial decision is ASK_USER (no tools) but the user request
    requires mutation, the mutation-contract retry path must:
    1. Successfully retry with a write tool
    2. Yield ToolBatchEvent for the write result
    3. Yield CompletionEvent
    4. NEVER yield ErrorEvent (the pre-fix bug: control flow fell through to ASK_USER branch)
    """

    from polaris.cells.roles.kernel.public.turn_events import CompletionEvent, ErrorEvent, ToolBatchEvent

    call_ordinal = 0

    async def _fake_call_llm_for_decision_stream(
        ctx,
        tool_definitions,
        ledger,
        shadow_engine=None,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        """First call: ASK_USER (no tools). Retry call: write_file."""
        nonlocal call_ordinal
        call_ordinal += 1
        if call_ordinal == 1:
            # Initial decision: ASK_USER — triggers mutation-contract retry
            yield {
                "type": "_internal_materialize",
                "response": RawLLMResponse(
                    content="我需要更多信息才能继续。请澄清您的需求。",
                    native_tool_calls=[],
                ),
            }
            return
        # Retry: write_file succeeds
        arguments = {"file": "output.md", "content": "done"}
        if shadow_engine is not None:
            await shadow_engine.speculate_tool_call(
                tool_name="write_file",
                arguments=arguments,
                call_id="call_retry",
                turn_id=ledger.turn_id,
            )
        yield {
            "type": "_internal_materialize",
            "response": RawLLMResponse(
                content="",
                native_tool_calls=[
                    {
                        "id": "call_retry",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": arguments},
                    }
                ],
            ),
        }

    kernel = TransactionKernel(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value=_successful_write_tool_result("output.md", bytes_written=4)),
        config=TransactionConfig(domain="code", mutation_guard_mode="strict"),
        llm_provider_stream=AsyncMock(),
    )

    monkeypatch.setattr(
        kernel._stream_orchestrator,
        "_call_llm_for_decision_stream_impl",
        _fake_call_llm_for_decision_stream,
    )

    context = [{"role": "user", "content": "落地高优先级的任务"}]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]

    events: list[Any] = []
    async for event in kernel.execute_stream("turn_retry_ask_user", context, tool_definitions):
        events.append(event)

    completions = [e for e in events if isinstance(e, CompletionEvent)]
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    tool_batches = [e for e in events if isinstance(e, ToolBatchEvent)]

    # Bug 3 fix: absolutely no ErrorEvent must leak into the stream
    assert len(errors) == 0, (
        f"Bug regression: ErrorEvent leaked into stream. Events: {[type(e).__name__ for e in events]}"
    )

    # Must have exactly one CompletionEvent
    assert len(completions) == 1, (
        f"Expected 1 CompletionEvent, got {len(completions)} in {len(events)} events: "
        f"{[type(e).__name__ for e in events]}"
    )
    assert completions[0].status == "success"

    # Must have at least one tool batch event (the write_file result)
    assert len(tool_batches) >= 1


def test_bootstrap_followup_never_forces_write_file_on_large_real_files() -> None:
    """Phase-1 live regression (phase1smoke django-15213): a large real source
    file containing 'NotImplemented'/'TODO:' as ordinary code must NOT trip the
    scaffold marker — forcing write_file makes a weak model regenerate the
    whole file and blow the LLM timeout (observed: 600s, dead session)."""
    large_real_content = (
        "class ExpressionWrapper:\n    # TODO: optimize\n    def __eq__(self, other):\n        return NotImplemented\n"
    ) * 200
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {"file": "django/db/models/expressions.py", "content": large_real_content},
            }
        ]
    }
    assert bootstrap_receipt_contains_whole_file_replacement_marker(receipt) is False
    selected = select_bootstrap_followup_write_tool_name(
        allowed_tool_names={"read_file", "edit_blocks", "write_file", "edit_file"},
        default_write_tool_name="edit_blocks",
        bootstrap_receipt=receipt,
        failed_bootstrap_files=[],
    )
    assert selected == "edit_blocks"


def test_small_scaffold_with_markers_still_qualifies_for_whole_file_replacement() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {"file": "src/seed.ts", "content": "// TODO: implement\nexport {}\n"},
            }
        ]
    }
    assert bootstrap_receipt_contains_whole_file_replacement_marker(receipt) is True


class TestWriteArgumentShapeFailureGuard:
    """Phase-1 A8a: malformed-write batches must escalate through the retry ladder."""

    @staticmethod
    def _receipt(items: list[tuple[str, str, dict]]) -> dict:
        return {
            "results": [
                {"call_id": f"c{i}", "tool_name": tool, "status": status, "result": result}
                for i, (tool, status, result) in enumerate(items)
            ]
        }

    def test_all_writes_failed_on_shape_triggers(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        receipt = self._receipt(
            [
                ("read_file", "success", {"ok": True, "content": "x"}),
                (
                    "edit_blocks",
                    "error",
                    {"ok": False, "error": "edit_blocks received prose/narration instead of edit content"},
                ),
                (
                    "edit_blocks",
                    "error",
                    {"ok": False, "error": "All 1 edit block(s) had identical search and replace text (no-op)."},
                ),
            ]
        )
        assert batch_write_results_all_failed_on_argument_shape(receipt) is True

    def test_line_range_missing_file_triggers_shape_retry(self) -> None:
        """L6-32: edit_blocks line-range form omitted file and bypassed shape retry."""
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        receipt = self._receipt(
            [
                (
                    "edit_blocks",
                    "error",
                    {"ok": False, "error": "line-range edit requires a 'file' argument."},
                ),
            ]
        )

        assert batch_write_results_all_failed_on_argument_shape(receipt) is True

    def test_missing_edit_payload_triggers_shape_retry(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        receipt = self._receipt(
            [
                (
                    "edit_blocks",
                    "error",
                    {
                        "ok": False,
                        "error": "Missing edit payload. EASIEST: call edit_blocks with file + start + end + replace.",
                    },
                ),
            ]
        )

        assert batch_write_results_all_failed_on_argument_shape(receipt) is True

    def test_missing_blocks_start_argument_triggers_shape_retry(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        receipt = self._receipt(
            [
                (
                    "edit_blocks",
                    "error",
                    {"ok": False, "error": "Parameter failed: edit_blocks: missing argument: blocks start."},
                ),
            ]
        )

        assert batch_write_results_all_failed_on_argument_shape(receipt) is True

    def test_whole_file_write_not_edit_triggers_shape_retry(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        receipt = self._receipt(
            [
                (
                    "edit_blocks",
                    "error",
                    {
                        "ok": False,
                        "error": (
                            "edit_blocks received filename plus full file content tests/test_services.py. "
                            "whole-file write, not edit."
                        ),
                    },
                ),
            ]
        )

        assert batch_write_results_all_failed_on_argument_shape(receipt) is True

    def test_edit_block_validation_failed_triggers_shape_retry(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        receipt = self._receipt(
            [
                (
                    "edit_blocks",
                    "error",
                    {
                        "ok": False,
                        "error": (
                            "Validation failed 1 block(s). No files modified. "
                            "Check that SEARCH text exactly matches file content."
                        ),
                    },
                ),
            ]
        )

        assert batch_write_results_all_failed_on_argument_shape(receipt) is True

    def test_pre_write_syntax_block_triggers(self) -> None:
        """L2-11 r3 live regression: a single PreWriteGuard-blocked write
        (IndentationError) ended the turn as no_materialized_changes because
        the syntax-block error text was not an escalation anchor."""
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        receipt = self._receipt(
            [
                (
                    "write_file",
                    "error",
                    {
                        "ok": False,
                        "error": "Code syntax validation failed:\nmain.py:138: IndentationError: unexpected indent",
                    },
                ),
            ]
        )
        assert batch_write_results_all_failed_on_argument_shape(receipt) is True

    def test_empty_write_content_raw_error_triggers(self) -> None:
        """Wall 2 regression: ToolBatchRuntime can serialize a failed write with
        the diagnostic only in raw_results, leaving canonical ``result`` empty."""
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        receipt = {
            "results": [
                {
                    "call_id": "c0",
                    "tool_name": "write_file",
                    "status": "error",
                    "result": None,
                }
            ],
            "raw_results": [
                {
                    "call_id": "c0",
                    "tool_name": "write_file",
                    "status": "error",
                    "result": None,
                    "error": "Empty write content: write_file for src/app.py received blank content.",
                }
            ],
        }
        assert batch_write_results_all_failed_on_argument_shape(receipt) is True

    def test_raw_error_without_call_id_does_not_broadcast_to_mixed_write_failures(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        receipt = {
            "results": [
                {"tool_name": "write_file", "status": "error", "result": None},
                {"tool_name": "write_file", "status": "error", "result": None},
            ],
            "raw_results": [
                {
                    "tool_name": "write_file",
                    "status": "error",
                    "result": None,
                    "error": "Empty write content: write_file for src/app.py received blank content.",
                },
                {
                    "tool_name": "write_file",
                    "status": "error",
                    "result": None,
                    "error": "stale_edit: target not read in this session",
                },
            ],
        }
        assert batch_write_results_all_failed_on_argument_shape(receipt) is False

    def test_any_successful_write_disarms(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        receipt = self._receipt(
            [
                (
                    "edit_blocks",
                    "error",
                    {"ok": False, "error": "Parameter validation failed: edit_blocks: missing required argument"},
                ),
                ("write_file", "success", {"ok": True, "bytes_written": 10}),
            ]
        )
        assert batch_write_results_all_failed_on_argument_shape(receipt) is False

    def test_non_shape_write_failure_is_owned_by_other_guards(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        receipt = self._receipt(
            [("edit_blocks", "error", {"ok": False, "error": "stale_edit: target not read in this session"})]
        )
        assert batch_write_results_all_failed_on_argument_shape(receipt) is False

    def test_read_only_batch_never_triggers(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        receipt = self._receipt([("read_file", "error", {"ok": False, "error": "File not found: x.py"})])
        assert batch_write_results_all_failed_on_argument_shape(receipt) is False

    def test_empty_receipt_never_triggers(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
            batch_write_results_all_failed_on_argument_shape,
        )

        assert batch_write_results_all_failed_on_argument_shape({"results": []}) is False


class TestVoidBatchDoesNotConsumeBudget:
    """L2-11 r5 live regression: the A8a escalation's replacement batch became
    ToolBatch #2 because the void (all-writes-shape-failed, zero-effect)
    original batch still counted — KernelGuardError killed the turn
    mid-escalation."""

    def test_a8a_raise_rolls_back_batch_count(self) -> None:
        import re
        from pathlib import Path

        backend_root = Path(__file__).resolve().parents[5]
        source = (backend_root / "polaris/cells/roles/kernel/internal/transaction/tool_batch_executor.py").read_text(
            encoding="utf-8"
        )
        block = re.search(
            r"if _shape_guard_receipt and batch_write_results_all_failed_on_argument_shape\(_shape_guard_receipt\):"
            r"(?P<body>.*?)raise RuntimeError",
            source,
            re.DOTALL,
        )
        assert block is not None, "A8a escalation block not found"
        assert "ledger.tool_batch_count = max(0," in block.group("body"), (
            "void batch must release the single-batch budget before escalation"
        )
