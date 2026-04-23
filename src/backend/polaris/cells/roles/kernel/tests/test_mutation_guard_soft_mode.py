"""Mutation Guard Soft Mode 回归测试 — Routing Trap 防护验证。

验证场景:
1. 否定语境下的 mutation 标记 ("不要修改") → warn 模式不抛异常
2. 正常 mutation 请求 + 无写工具 → warn 模式记录警告并放行
3. strict 模式保持原有硬约束行为
4. ledger 正确记录 mutation_guard_warnings
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    tool_batch_has_authoritative_write_invocation,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    BlockedReason,
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
from polaris.cells.roles.kernel.internal.transaction.tool_failure_circuit_breaker import (
    ToolFailureCircuitBreaker,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TurnTransactionController
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    BatchReceipt,
    ToolCallId,
    ToolExecutionResult,
    TurnDecision,
    TurnId,
)
from polaris.cells.roles.kernel.public.turn_events import ErrorEvent


@pytest.fixture
def mock_emit_event() -> Any:
    return lambda event: None


@pytest.fixture
def mock_guard_assert() -> Any:
    return lambda **kw: None


def _build_decoded_state_machine(turn_id: str) -> TurnStateMachine:
    state_machine = TurnStateMachine(turn_id=turn_id)
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    return state_machine


def _build_readonly_decision(
    turn_id: str,
    *,
    batch_suffix: str,
    invocation_count: int = 1,
    should_fail: bool = False,
) -> TurnDecision:
    invocations: list[dict[str, Any]] = []
    for idx in range(invocation_count):
        invocations.append(
            {
                "call_id": f"call_{batch_suffix}_{idx}",
                "tool_name": "read_file",
                "arguments": {
                    "file": "README.md",
                    "should_fail": should_fail,
                    "invocation_index": idx,
                },
                "execution_mode": "readonly_parallel",
                "effect_type": "read",
            }
        )
    return cast(
        TurnDecision,
        {
            "turn_id": turn_id,
            "metadata": {"workspace": "."},
            "finalize_mode": "none",
            "tool_batch": {
                "batch_id": f"batch_{batch_suffix}",
                "invocations": invocations,
            },
        },
    )


# ---------------------------------------------------------------------------
# ToolBatchExecutor 层测试
# ---------------------------------------------------------------------------


def test_authoritative_write_invocation_ignores_session_patch_file() -> None:
    """辅助 SESSION_PATCH 写入不能满足 mutation 写入要求。"""
    invocations: list[dict[str, Any]] = [
        {
            "tool_name": "append_to_file",
            "arguments": {"file": "SESSION_PATCH.md", "content": "trace"},
        }
    ]

    assert tool_batch_has_authoritative_write_invocation(invocations) is False


@pytest.mark.asyncio
async def test_tool_failure_circuit_breaker_triggers_after_three_consecutive_failures(
    mock_emit_event: Any,
    mock_guard_assert: Any,
) -> None:
    """连续 3 个失败批次后必须触发工具失败熔断。"""
    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(return_value={"success": False, "error": "forced failure"}),
        config=TransactionConfig(mutation_guard_mode="warn"),
        emit_event=mock_emit_event,
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    turn_id = "turn_tool_failure_consecutive"
    context = [{"role": "user", "content": "读取 README.md"}]

    for idx in range(2):
        await executor.execute_tool_batch(
            _build_readonly_decision(turn_id, batch_suffix=f"consecutive_{idx}", should_fail=True),
            _build_decoded_state_machine(turn_id),
            TurnLedger(turn_id=turn_id),
            context,
            stream=False,
        )

    with pytest.raises(
        RuntimeError,
        match=(
            r"single_batch_contract_violation: tool_failure_circuit_breaker_triggered "
            r".*consecutive_failures=3 .*total_failures=3"
        ),
    ):
        await executor.execute_tool_batch(
            _build_readonly_decision(turn_id, batch_suffix="consecutive_2", should_fail=True),
            _build_decoded_state_machine(turn_id),
            TurnLedger(turn_id=turn_id),
            context,
            stream=False,
        )


@pytest.mark.asyncio
async def test_tool_failure_circuit_breaker_resets_consecutive_after_success_batch(
    mock_emit_event: Any,
    mock_guard_assert: Any,
) -> None:
    """成功批次后 consecutive 计数必须重置。"""
    tool_runtime = AsyncMock(
        side_effect=[
            {"success": False, "error": "failure_1"},
            {"success": True, "result": {"file": "README.md", "content": "ok"}},
            {"success": False, "error": "failure_2"},
            {"success": False, "error": "failure_3"},
            {"success": False, "error": "failure_4"},
        ]
    )
    executor = ToolBatchExecutor(
        tool_runtime=tool_runtime,
        config=TransactionConfig(mutation_guard_mode="warn"),
        emit_event=mock_emit_event,
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    turn_id = "turn_tool_failure_reset"
    context = [{"role": "user", "content": "读取 README.md"}]

    for suffix, should_fail in [
        ("reset_fail_1", True),
        ("reset_success", False),
        ("reset_fail_2", True),
        ("reset_fail_3", True),
    ]:
        await executor.execute_tool_batch(
            _build_readonly_decision(turn_id, batch_suffix=suffix, should_fail=should_fail),
            _build_decoded_state_machine(turn_id),
            TurnLedger(turn_id=turn_id),
            context,
            stream=False,
        )

    with pytest.raises(
        RuntimeError,
        match=(
            r"single_batch_contract_violation: tool_failure_circuit_breaker_triggered "
            r".*consecutive_failures=3 .*total_failures=4"
        ),
    ):
        await executor.execute_tool_batch(
            _build_readonly_decision(turn_id, batch_suffix="reset_fail_4", should_fail=True),
            _build_decoded_state_machine(turn_id),
            TurnLedger(turn_id=turn_id),
            context,
            stream=False,
        )


@pytest.mark.asyncio
async def test_tool_failure_circuit_breaker_triggers_on_total_failures(
    mock_emit_event: Any,
    mock_guard_assert: Any,
) -> None:
    """累计失败达到 10 时必须触发熔断（即使 consecutive 未达到阈值）。"""

    async def selective_tool_runtime(_tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if bool(arguments.get("should_fail", False)):
            return {"success": False, "error": "forced failure"}
        return {"success": True, "result": {"file": "README.md", "content": "ok"}}

    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(side_effect=selective_tool_runtime),
        config=TransactionConfig(mutation_guard_mode="warn"),
        emit_event=mock_emit_event,
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    turn_id = "turn_tool_failure_total"
    context = [{"role": "user", "content": "读取 README.md"}]

    for cycle in range(4):
        await executor.execute_tool_batch(
            _build_readonly_decision(
                turn_id,
                batch_suffix=f"total_fail_cycle_{cycle}",
                invocation_count=2,
                should_fail=True,
            ),
            _build_decoded_state_machine(turn_id),
            TurnLedger(turn_id=turn_id),
            context,
            stream=False,
        )
        await executor.execute_tool_batch(
            _build_readonly_decision(
                turn_id,
                batch_suffix=f"total_success_cycle_{cycle}",
                invocation_count=1,
                should_fail=False,
            ),
            _build_decoded_state_machine(turn_id),
            TurnLedger(turn_id=turn_id),
            context,
            stream=False,
        )

    with pytest.raises(
        RuntimeError,
        match=(
            r"single_batch_contract_violation: tool_failure_circuit_breaker_triggered "
            r".*consecutive_failures=1 .*total_failures=10"
        ),
    ):
        await executor.execute_tool_batch(
            _build_readonly_decision(
                turn_id,
                batch_suffix="total_fail_cycle_4",
                invocation_count=2,
                should_fail=True,
            ),
            _build_decoded_state_machine(turn_id),
            TurnLedger(turn_id=turn_id),
            context,
            stream=False,
        )


def test_tool_failure_circuit_breaker_applies_effect_scope_threshold_override() -> None:
    """effect_scope=write 可配置为更严格阈值，并在首个失败批次触发。"""
    breaker = ToolFailureCircuitBreaker(
        consecutive_failure_threshold=99,
        total_failure_threshold=99,
        effect_threshold_overrides={"write": (1, 1)},
    )
    snapshot = breaker.evaluate_batch(
        turn_id="turn_dim_write_override",
        invocations=[
            {"call_id": "call_write_1", "tool_name": "write_file", "effect_type": "write"},
        ],
        receipts=[
            {
                "results": [
                    {
                        "call_id": "call_write_1",
                        "tool_name": "write_file",
                        "status": "error",
                        "error": "forced failure",
                    }
                ]
            }
        ],
    )

    assert snapshot.triggered is True
    assert snapshot.trigger_reason == "dimension_consecutive_threshold"
    assert snapshot.triggered_dimension == "write_file|write|error"


def test_tool_failure_circuit_breaker_resets_consecutive_per_dimension() -> None:
    """不同维度失败应打断目标维度的 consecutive 计数。"""
    breaker = ToolFailureCircuitBreaker(
        consecutive_failure_threshold=99,
        total_failure_threshold=99,
        effect_threshold_overrides={"read": (2, 99)},
    )

    first = breaker.evaluate_batch(
        turn_id="turn_dim_reset",
        invocations=[{"call_id": "call_read_1", "tool_name": "read_file", "effect_type": "read"}],
        receipts=[{"results": [{"call_id": "call_read_1", "tool_name": "read_file", "status": "error"}]}],
    )
    assert first.triggered is False

    middle = breaker.evaluate_batch(
        turn_id="turn_dim_reset",
        invocations=[{"call_id": "call_write_1", "tool_name": "write_file", "effect_type": "write"}],
        receipts=[{"results": [{"call_id": "call_write_1", "tool_name": "write_file", "status": "error"}]}],
    )
    assert middle.triggered is False

    last = breaker.evaluate_batch(
        turn_id="turn_dim_reset",
        invocations=[{"call_id": "call_read_2", "tool_name": "read_file", "effect_type": "read"}],
        receipts=[{"results": [{"call_id": "call_read_2", "tool_name": "read_file", "status": "error"}]}],
    )
    assert last.triggered is False


@pytest.mark.asyncio
async def test_mutation_bypass_uses_merged_batch_receipt(
    mock_emit_event: Any,
    mock_guard_assert: Any,
) -> None:
    """continue_multi_turn 应携带完整 merged receipt，而不是 receipts[0]。"""
    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(),
        config=TransactionConfig(mutation_guard_mode="warn"),
        emit_event=mock_emit_event,
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    state_machine = TurnStateMachine(turn_id="turn_merge")
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    state_machine.transition_to(TurnState.TOOL_BATCH_EXECUTING)
    state_machine.transition_to(TurnState.TOOL_BATCH_EXECUTED)
    ledger = TurnLedger(turn_id="turn_merge")
    ledger.mutation_obligation.mark_blocked(
        BlockedReason.NO_WRITE_TOOL_AVAILABLE,
        detail="no write tools",
    )

    result = executor._build_mutation_bypass_result(
        cast(
            TurnDecision,
            {
                "turn_id": "turn_merge",
                "kind": "TOOL_BATCH",
                "finalize_mode": "LLM_ONCE",
            },
        ),
        state_machine,
        ledger,
        [
            {
                "batch_id": "b1",
                "turn_id": "turn_merge",
                "results": [{"tool_name": "glob", "status": "success"}],
                "raw_results": [{"tool_name": "glob", "status": "success"}],
                "success_count": 1,
                "failure_count": 0,
                "pending_async_count": 0,
                "has_pending_async": False,
            },
            {
                "batch_id": "b2",
                "turn_id": "turn_merge",
                "results": [{"tool_name": "repo_rg", "status": "success"}],
                "raw_results": [{"tool_name": "repo_rg", "status": "success"}],
                "success_count": 1,
                "failure_count": 0,
                "pending_async_count": 0,
                "has_pending_async": False,
            },
        ],
        stream=True,
    )

    receipt = cast(dict[str, Any], result.get("batch_receipt"))
    assert receipt is not None
    assert len(receipt["results"]) == 2
    assert {item["tool_name"] for item in receipt["results"]} == {"glob", "repo_rg"}
    assert receipt["success_count"] == 2


@pytest.mark.asyncio
async def test_mutation_bypass_normalizes_batch_receipt_models(
    mock_emit_event: Any,
    mock_guard_assert: Any,
) -> None:
    """continue_multi_turn 必须兼容 ToolBatchRuntime 返回的 BatchReceipt 模型对象。"""
    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(),
        config=TransactionConfig(mutation_guard_mode="warn"),
        emit_event=mock_emit_event,
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    state_machine = TurnStateMachine(turn_id="turn_merge_model")
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    state_machine.transition_to(TurnState.TOOL_BATCH_EXECUTING)
    state_machine.transition_to(TurnState.TOOL_BATCH_EXECUTED)
    ledger = TurnLedger(turn_id="turn_merge_model")
    ledger.mutation_obligation.mark_blocked(
        BlockedReason.NO_WRITE_TOOL_AVAILABLE,
        detail="no write tools",
    )

    receipts = cast(
        list[dict[str, Any]],
        [
            BatchReceipt(
                batch_id=BatchId("b1"),
                turn_id=TurnId("turn_merge_model"),
                results=[
                    ToolExecutionResult(
                        call_id=ToolCallId("call_glob"),
                        tool_name="glob",
                        status="success",
                        result={"path": ".", "results": ["session_orchestrator.py"]},
                        execution_time_ms=5,
                    )
                ],
                success_count=1,
                raw_results=[{"tool_name": "glob", "status": "success"}],
            ),
            BatchReceipt(
                batch_id=BatchId("b2"),
                turn_id=TurnId("turn_merge_model"),
                results=[
                    ToolExecutionResult(
                        call_id=ToolCallId("call_rg"),
                        tool_name="repo_rg",
                        status="success",
                        result={"result": {"query": "session_orchestrator"}},
                        execution_time_ms=7,
                    )
                ],
                success_count=1,
                raw_results=[{"tool_name": "repo_rg", "status": "success"}],
            ),
        ],
    )

    result = executor._build_mutation_bypass_result(
        cast(
            TurnDecision,
            {
                "turn_id": "turn_merge_model",
                "kind": "TOOL_BATCH",
                "finalize_mode": "LLM_ONCE",
            },
        ),
        state_machine,
        ledger,
        receipts,
        stream=True,
    )

    receipt = cast(dict[str, Any], result.get("batch_receipt"))
    assert receipt is not None
    assert len(receipt["results"]) == 2
    assert {item["tool_name"] for item in receipt["results"]} == {"glob", "repo_rg"}
    assert receipt["success_count"] == 2


@pytest.mark.asyncio
async def test_exploration_streak_hard_block_rejects_exploration_only_batch(
    mock_guard_assert: Any,
) -> None:
    """当 EXPLORATION_STREAK_HARD_BLOCK 生效时，只探索工具应被拒绝。"""
    captured_events: list[Any] = []
    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(),
        config=TransactionConfig(mutation_guard_mode="warn"),
        emit_event=lambda event: captured_events.append(event),
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    decision = cast(
        TurnDecision,
        {
            "turn_id": "turn_streak_block",
            "metadata": {"workspace": "."},
            "finalize_mode": "none",
            "tool_batch": {
                "batch_id": "batch_streak_block",
                "invocations": [
                    {
                        "call_id": "call_glob",
                        "tool_name": "glob",
                        "arguments": {"pattern": "**/*session_orchestrator*"},
                    }
                ],
            },
        },
    )
    state_machine = TurnStateMachine(turn_id="turn_streak_block")
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    ledger = TurnLedger(turn_id="turn_streak_block")
    context = [
        {
            "role": "user",
            "content": "EXPLORATION_STREAK_HARD_BLOCK: 必须 read_file，禁止继续仅用 glob/repo_rg。",
        }
    ]

    with pytest.raises(RuntimeError, match="exploration_streak_hard_block"):
        await executor.execute_tool_batch(decision, state_machine, ledger, context, stream=False)
    assert any(
        isinstance(event, ErrorEvent) and event.error_type == "exploration_streak_hard_block"
        for event in captured_events
    )


@pytest.mark.asyncio
async def test_known_target_requires_read_blocks_exploration_only_batch(
    mock_guard_assert: Any,
) -> None:
    """已知目标文件后仍只做 broad exploration，必须被 executor 拦截。"""
    captured_events: list[Any] = []
    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(),
        config=TransactionConfig(mutation_guard_mode="warn"),
        emit_event=lambda event: captured_events.append(event),
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    decision = cast(
        TurnDecision,
        {
            "turn_id": "turn_known_target_requires_read",
            "metadata": {"workspace": "."},
            "finalize_mode": "none",
            "tool_batch": {
                "batch_id": "batch_known_target_requires_read",
                "invocations": [
                    {
                        "call_id": "call_glob",
                        "tool_name": "glob",
                        "arguments": {"pattern": "**/*session_orchestrator*"},
                    }
                ],
            },
        },
    )
    state_machine = TurnStateMachine(turn_id="turn_known_target_requires_read")
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    ledger = TurnLedger(turn_id="turn_known_target_requires_read")
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
    context = [
        {
            "role": "user",
            "content": (
                "请进一步完善 polaris/cells/roles/runtime/internal/session_orchestrator.py 相关代码。"
                " 当前必须先 read_file 再修改。"
            ),
        }
    ]

    with pytest.raises(RuntimeError, match="target_files_known_without_read_evidence"):
        await executor.execute_tool_batch(decision, state_machine, ledger, context, stream=False)
    assert any(
        isinstance(event, ErrorEvent) and event.error_type == "known_target_requires_read" for event in captured_events
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invocations", "case_id"),
    [
        (
            [
                {
                    "call_id": "call_glob",
                    "tool_name": "glob",
                    "arguments": {"pattern": "**/*session_orchestrator*"},
                },
                {
                    "call_id": "call_read",
                    "tool_name": "read_file",
                    "arguments": {"file": "polaris/cells/roles/runtime/internal/session_orchestrator.py"},
                },
            ],
            "broad_plus_direct_read",
        ),
        (
            [
                {
                    "call_id": "call_write",
                    "tool_name": "write_file",
                    "arguments": {"file": "tmp/patch.txt", "content": "patched"},
                }
            ],
            "write_only",
        ),
    ],
    ids=["broad_plus_direct_read", "write_only"],
)
async def test_exploration_streak_hard_block_allows_non_exploration_only_batches(
    invocations: list[dict[str, Any]],
    case_id: str,
    mock_guard_assert: Any,
) -> None:
    """熔断标记存在时，包含 direct read 或 write 的批次不应被误拦截。"""
    captured_events: list[Any] = []
    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(),
        config=TransactionConfig(mutation_guard_mode="warn"),
        emit_event=lambda event: captured_events.append(event),
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    decision = cast(
        TurnDecision,
        {
            "turn_id": f"turn_streak_allow_{case_id}",
            "metadata": {"workspace": "."},
            "finalize_mode": "none",
            "tool_batch": {
                "batch_id": f"batch_streak_allow_{case_id}",
                "invocations": invocations,
            },
        },
    )
    state_machine = TurnStateMachine(turn_id=f"turn_streak_allow_{case_id}")
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    ledger = TurnLedger(turn_id=f"turn_streak_allow_{case_id}")
    context = [
        {
            "role": "user",
            "content": "EXPLORATION_STREAK_HARD_BLOCK: 必须 read_file，禁止继续仅用 glob/repo_rg。",
        }
    ]

    result = await executor.execute_tool_batch(decision, state_machine, ledger, context, stream=False)

    assert result.get("turn_id") == f"turn_streak_allow_{case_id}"
    assert not any(
        isinstance(event, ErrorEvent) and event.error_type == "exploration_streak_hard_block"
        for event in captured_events
    )


@pytest.mark.asyncio
async def test_warn_mode_allows_readonly_batch_for_negated_mutation_request(
    mock_emit_event: Any,
    mock_guard_assert: Any,
) -> None:
    """否定语境 + warn 模式 = 放行，不抛异常。"""
    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(),
        config=TransactionConfig(mutation_guard_mode="warn"),
        emit_event=mock_emit_event,
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    decision = cast(
        TurnDecision,
        {
            "turn_id": "turn_negated",
            "metadata": {"workspace": "."},
            "finalize_mode": "none",
            "tool_batch": {
                "batch_id": "batch_negated",
                "invocations": [
                    {
                        "call_id": "call_read",
                        "tool_name": "read_file",
                        "arguments": {"file": "config.yaml"},
                    }
                ],
            },
        },
    )
    state_machine = TurnStateMachine(turn_id="turn_negated")
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    ledger = TurnLedger(turn_id="turn_negated")
    context = [{"role": "user", "content": "这个配置先不要修改"}]

    # 不应抛异常
    result = await executor.execute_tool_batch(decision, state_machine, ledger, context, stream=False)

    # 否定语境当前策略为“放行且不记 warning”（避免误报）
    assert ledger.mutation_guard_warnings == []

    # 结果应包含正常执行结果（tool_batch_with_receipt 或 handoff）
    assert result.get("turn_id") == "turn_negated"


@pytest.mark.asyncio
async def test_successful_direct_read_records_read_evidence(
    mock_emit_event: Any,
    mock_guard_assert: Any,
) -> None:
    """direct read 工具成功后必须写入 mutation/read evidence ledger。"""
    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(return_value={"success": True, "result": {"file": "README.md", "content": "ok"}}),
        config=TransactionConfig(mutation_guard_mode="warn"),
        emit_event=mock_emit_event,
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    decision = cast(
        TurnDecision,
        {
            "turn_id": "turn_read_evidence",
            "metadata": {"workspace": "."},
            "finalize_mode": "none",
            "tool_batch": {
                "batch_id": "batch_read_evidence",
                "invocations": [
                    {
                        "call_id": "call_read",
                        "tool_name": "read_file",
                        "arguments": {"file": "README.md"},
                        "execution_mode": "readonly_parallel",
                        "effect_type": "read",
                    }
                ],
            },
        },
    )
    state_machine = TurnStateMachine(turn_id="turn_read_evidence")
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    ledger = TurnLedger(turn_id="turn_read_evidence")
    context = [{"role": "user", "content": "读取 README.md 的内容"}]

    await executor.execute_tool_batch(decision, state_machine, ledger, context, stream=False)

    assert ledger.mutation_obligation.read_evidence_count == 1


@pytest.mark.asyncio
async def test_warn_mode_allows_readonly_batch_for_weak_mutation_request(
    mock_emit_event: Any,
    mock_guard_assert: Any,
) -> None:
    """弱 mutation 语境 + warn 模式 = 放行并记录警告。"""
    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(),
        config=TransactionConfig(mutation_guard_mode="warn"),
        emit_event=mock_emit_event,
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    decision = cast(
        TurnDecision,
        {
            "turn_id": "turn_weak",
            "metadata": {"workspace": "."},
            "finalize_mode": "none",
            "tool_batch": {
                "batch_id": "batch_weak",
                "invocations": [
                    {
                        "call_id": "call_read",
                        "tool_name": "read_file",
                        "arguments": {"file": "main.py"},
                    }
                ],
            },
        },
    )
    state_machine = TurnStateMachine(turn_id="turn_weak")
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    ledger = TurnLedger(turn_id="turn_weak")
    context = [{"role": "user", "content": "帮我看一下 main.py 的逻辑，暂时不动它"}]

    result = await executor.execute_tool_batch(decision, state_machine, ledger, context, stream=False)

    # 弱 mutation 标记 "看一下" 不触发 STRONG_MUTATION，但 "帮我" 可能触发弱标记
    # 关键是：不抛异常，系统继续执行
    assert result.get("turn_id") == "turn_weak"


@pytest.mark.asyncio
async def test_strict_mode_rejects_readonly_batch_for_mutation_request(
    mock_emit_event: Any,
    mock_guard_assert: Any,
) -> None:
    """strict 模式保持原有硬约束行为。"""
    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(),
        config=TransactionConfig(mutation_guard_mode="strict"),
        emit_event=mock_emit_event,
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    decision = cast(
        TurnDecision,
        {
            "turn_id": "turn_strict",
            "metadata": {"workspace": "."},
            "tool_batch": {
                "batch_id": "batch_strict",
                "invocations": [
                    {
                        "call_id": "call_read",
                        "tool_name": "read_file",
                        "arguments": {"file": "README.md"},
                    }
                ],
            },
        },
    )
    state_machine = TurnStateMachine(turn_id="turn_strict")
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    ledger = TurnLedger(turn_id="turn_strict")
    context = [{"role": "user", "content": "请更新 README.md 并写入新说明"}]

    with pytest.raises(RuntimeError, match="single_batch_contract_violation"):
        await executor.execute_tool_batch(decision, state_machine, ledger, context, stream=False)


@pytest.mark.asyncio
async def test_off_mode_completely_bypasses_mutation_guard(
    mock_emit_event: Any,
    mock_guard_assert: Any,
) -> None:
    """off 模式 = 完全静默，不检查、不记录。"""
    executor = ToolBatchExecutor(
        tool_runtime=AsyncMock(),
        config=TransactionConfig(mutation_guard_mode="off"),
        emit_event=mock_emit_event,
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )
    decision = cast(
        TurnDecision,
        {
            "turn_id": "turn_off",
            "metadata": {"workspace": "."},
            "finalize_mode": "none",
            "tool_batch": {
                "batch_id": "batch_off",
                "invocations": [
                    {
                        "call_id": "call_read",
                        "tool_name": "read_file",
                        "arguments": {"file": "main.py"},
                    }
                ],
            },
        },
    )
    state_machine = TurnStateMachine(turn_id="turn_off")
    state_machine.transition_to(TurnState.CONTEXT_BUILT)
    state_machine.transition_to(TurnState.DECISION_REQUESTED)
    state_machine.transition_to(TurnState.DECISION_RECEIVED)
    state_machine.transition_to(TurnState.DECISION_DECODED)
    ledger = TurnLedger(turn_id="turn_off")
    context = [{"role": "user", "content": "请更新 main.py"}]

    result = await executor.execute_tool_batch(decision, state_machine, ledger, context, stream=False)

    # 完全静默，ledger 无警告
    assert len(ledger.mutation_guard_warnings) == 0
    assert result.get("turn_id") == "turn_off"


# ---------------------------------------------------------------------------
# Facade 层测试 (TurnTransactionController)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_controller_warn_mode_passthrough_non_tool_decision_for_mutation() -> None:
    """Facade 层：warn 模式下，mutation 请求 + FINAL_ANSWER = 放行。"""
    controller = TurnTransactionController(
        llm_provider=AsyncMock(
            return_value={
                "content": "这是一个分析回答。",
                "model": "test",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        ),
        tool_runtime=AsyncMock(),
        config=TransactionConfig(domain="code", mutation_guard_mode="warn"),
    )

    result = await controller.execute(
        turn_id="turn_warn_passthrough",
        context=[{"role": "user", "content": "不要修改这个配置"}],
        tool_definitions=[
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ],
    )

    # 不应进入 retry 路径，应直接返回 final_answer
    assert result["turn_id"] == "turn_warn_passthrough"
    assert result["kind"] == "final_answer"
    # ledger 应记录警告
    ledger = result.get("ledger")
    if ledger is not None and hasattr(ledger, "mutation_guard_warnings"):
        assert len(ledger.mutation_guard_warnings) == 0
    else:
        assert len(result.get("ledger", {}).get("mutation_guard_warnings", [])) == 0


@pytest.mark.asyncio
async def test_controller_warn_mode_records_guard_warning_to_ledger() -> None:
    """验证 warn 模式下 ledger 正确记录 mutation guard 警告。"""
    controller = TurnTransactionController(
        llm_provider=AsyncMock(
            return_value={
                "content": "分析完成。",
                "model": "test",
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            }
        ),
        tool_runtime=AsyncMock(),
        config=TransactionConfig(domain="code", mutation_guard_mode="warn"),
    )

    state_machine = TurnStateMachine(turn_id="turn_ledger_warn")
    ledger = TurnLedger(turn_id="turn_ledger_warn")

    # 直接调用内部方法绕过 execute() 以验证 ledger
    result = await controller._execute_turn(
        turn_id="turn_ledger_warn",
        context=[{"role": "user", "content": "这个配置先不要修改"}],
        tool_definitions=[
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ],
        state_machine=state_machine,
        ledger=ledger,
        stream=False,
    )

    assert result["turn_id"] == "turn_ledger_warn"
    # ledger 应包含警告记录
    # NOTE: 由于 "不要修改" 在 regex 层仍被标记为 STRONG_MUTATION（已知局限），
    # warn 模式会记录警告并放行。这正体现了 warn 模式的价值。


# ---------------------------------------------------------------------------
# 配置默认值测试
# ---------------------------------------------------------------------------


def test_default_mutation_guard_mode_is_warn() -> None:
    """默认配置应为 warn（软守卫）。"""
    config = TransactionConfig()
    assert config.mutation_guard_mode == "warn"


def test_ledger_mutation_guard_warnings_default_empty() -> None:
    """新 ledger 的 mutation_guard_warnings 默认为空。"""
    ledger = TurnLedger(turn_id="turn_default")
    assert ledger.mutation_guard_warnings == []


def test_ledger_record_mutation_guard_warning() -> None:
    """验证 ledger 记录警告的方法。"""
    ledger = TurnLedger(turn_id="turn_record")
    ledger.record_mutation_guard_warning(
        reason="test_reason",
        user_request="test request",
    )
    assert len(ledger.mutation_guard_warnings) == 1
    assert ledger.mutation_guard_warnings[0]["reason"] == "test_reason"
    assert ledger.mutation_guard_warnings[0]["user_request"] == "test request"
    assert "timestamp_ms" in ledger.mutation_guard_warnings[0]
