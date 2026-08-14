"""Provider tool-surface violations recover inside the owning mutation turn.

UTF-8 编码验证: 本文所有文本使用 UTF-8。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_allowed_tool_names_from_definitions,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TurnTransactionController
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    RawLLMResponse,
    ToolBatch,
    ToolCallId,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
    classify_tool_invocation,
)


def _edit_decision(turn_id: str) -> TurnDecision:
    classification = classify_tool_invocation("edit_file")
    invocation = ToolInvocation(
        call_id=ToolCallId(f"{turn_id}_edit"),
        tool_name="edit_file",
        arguments={
            "file": "src/engine/rules.go",
            "search": "old",
            "replace": "new",
        },
        effect_type=classification.effect_type,
        execution_mode=classification.execution_mode,
    )
    return TurnDecision(
        turn_id=TurnId(turn_id),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        tool_batch=ToolBatch(batch_id=BatchId(f"{turn_id}_batch"), invocations=[invocation]),
        finalize_mode=FinalizeMode.NONE,
        domain="code",
        metadata={},
    )


@pytest.mark.asyncio
async def test_execute_turn_routes_surface_violation_to_same_task_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    tool_runtime = AsyncMock(return_value={})
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=tool_runtime,
        config=TransactionConfig(domain="code", mutation_guard_mode="strict"),
    )
    state_machine = TurnStateMachine(turn_id="turn_surface_retry")
    ledger = TurnLedger(turn_id="turn_surface_retry")
    context = [{"role": "user", "content": "请修改 src/engine/rules.go 并修复测试失败"}]
    tool_definitions = [{"type": "function", "function": {"name": "edit_file"}}]
    captured: dict[str, Any] = {}

    async def _raise_surface_violation(*_args: Any, **_kwargs: Any) -> RawLLMResponse:
        raise RuntimeError("provider_tool_surface_violation: requested=read_file; allowed=edit_file")

    async def _fake_retry(
        *,
        turn_id: str,
        context: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]],
        state_machine: TurnStateMachine,
        initial_failure_reason: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured.update(
            turn_id=turn_id,
            context=context,
            tool_definitions=tool_definitions,
            state=state_machine.current_state,
            initial_failure_reason=initial_failure_reason,
            original_decision=kwargs.get("original_decision"),
        )
        return {"kind": "tool_batch_with_receipt", "visible_content": "已执行精确修复"}

    monkeypatch.setattr(controller, "_call_llm_for_decision", _raise_surface_violation)
    monkeypatch.setattr(controller._retry_orchestrator, "retry_tool_batch_after_contract_violation", _fake_retry)

    result = await controller._execute_turn(
        "turn_surface_retry",
        context,
        tool_definitions,
        state_machine,
        ledger,
        stream=False,
        tool_choice_override={"type": "function", "function": {"name": "edit_file"}},
    )

    assert result["kind"] == "tool_batch_with_receipt"
    assert captured["state"] == TurnState.DECISION_DECODED
    assert str(captured["initial_failure_reason"]).startswith("provider_tool_surface_violation:")
    assert captured["original_decision"] is None
    assert ledger.anomaly_flags[-1]["violating_tool_executed"] is False
    tool_runtime.assert_not_awaited()


@pytest.mark.asyncio
async def test_mutation_retry_reasks_after_surface_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code", mutation_guard_mode="strict", max_retry_attempts=3),
    )
    state_machine = TurnStateMachine(turn_id="turn_surface_reask")
    for state in (
        TurnState.CONTEXT_BUILT,
        TurnState.DECISION_REQUESTED,
        TurnState.DECISION_RECEIVED,
        TurnState.DECISION_DECODED,
    ):
        state_machine.transition_to(state)
    ledger = TurnLedger(turn_id="turn_surface_reask")
    attempts: list[list[str]] = []

    async def _fake_retry_call(*, attempt_tool_definitions: list[dict[str, Any]], **_kwargs: Any) -> RawLLMResponse:
        attempts.append(sorted(extract_allowed_tool_names_from_definitions(attempt_tool_definitions)))
        if len(attempts) == 1:
            raise RuntimeError("provider_tool_surface_violation: requested=read_file; allowed=edit_file")
        return RawLLMResponse(content="", native_tool_calls=[])

    async def _fake_execute(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"kind": "tool_batch_with_receipt", "visible_content": "已修复"}

    monkeypatch.setattr(controller._retry_orchestrator, "_execute_retry_batch", _fake_retry_call)
    monkeypatch.setattr(controller.decoder, "decode", lambda *_args: _edit_decision("turn_surface_reask"))
    monkeypatch.setattr(controller._retry_orchestrator, "execute_tool_batch", _fake_execute)

    result = await controller._retry_orchestrator.retry_tool_batch_after_contract_violation(
        turn_id="turn_surface_reask",
        context=[{"role": "user", "content": "请修改 src/engine/rules.go 并修复测试失败"}],
        tool_definitions=[{"type": "function", "function": {"name": "edit_file"}}],
        state_machine=state_machine,
        ledger=ledger,
        stream=False,
        initial_failure_reason="provider_tool_surface_violation: requested=read_file; allowed=edit_file",
    )

    assert result["kind"] == "tool_batch_with_receipt"
    assert attempts == [["edit_file"], ["edit_file"]]


def test_surface_violation_is_not_a_generic_provider_failure() -> None:
    """Type guard remains exact; ordinary provider errors must still fail."""

    from polaris.cells.roles.kernel.internal.llm_caller.final_request_tool_surface import (
        is_provider_tool_surface_violation,
    )

    assert is_provider_tool_surface_violation(
        RuntimeError("provider_tool_surface_violation: requested=read_file; allowed=edit_file")
    )
    # The physical invoker projects guarded provider failures into its public
    # response envelope before DecisionCaller raises them back to the turn.
    assert is_provider_tool_surface_violation(
        RuntimeError(
            "LLM call failed: provider_tool_surface_violation: "
            "requested=read_file; allowed=edit_file"
        )
    )
    assert not is_provider_tool_surface_violation(RuntimeError("provider timeout"))
    assert not is_provider_tool_surface_violation("prefix provider_tool_surface_violation: fake")


def test_edit_decision_has_authorized_tool() -> None:
    decision = _edit_decision("turn_shape")
    batch = decision.get("tool_batch")
    assert batch is not None
    invocations = batch.get("invocations") if isinstance(batch, Mapping) else batch.invocations
    assert [item.tool_name for item in invocations] == ["edit_file"]
