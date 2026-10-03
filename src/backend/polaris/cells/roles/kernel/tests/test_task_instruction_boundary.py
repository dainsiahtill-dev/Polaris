"""Task intent is current task input, not concatenated safety/history text."""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.transaction import task_contract_builder
from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import CognitiveGateway
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.delivery_contract_resolver import resolve_turn_delivery_contract
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.mutation_contract_guard import apply_mutation_contract_guard
from polaris.cells.roles.kernel.internal.transaction.retry_context_builders import build_contract_retry_context
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TurnTransactionController
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
    classify_tool_invocation,
)

PRESERVE = "Do not remove existing scripts, aliases, or compiler/linter options unless the task explicitly asks."


def _context(instruction: object) -> list[dict]:
    return [
        {
            "role": "user",
            "content": "Create src/main.ts and package.json.\n" + PRESERVE,
            "metadata": {"platform_tool_contract": {"task_instruction": instruction}},
        }
    ]


@pytest.fixture(autouse=True)
def no_external_intent_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the external cognitive service is replaced; production classification runs.
    monkeypatch.setattr(CognitiveGateway, "get_default_instance_sync", lambda: None)


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        ("Create src/main.ts and package.json.", DeliveryMode.MATERIALIZE_CHANGES),
        ("Read src/main.ts only. Do not modify files.", DeliveryMode.ANALYZE_ONLY),
    ],
)
@pytest.mark.asyncio
async def test_delivery_resolution_consumes_pure_task_not_platform_protection(
    instruction: str, expected: DeliveryMode
) -> None:
    context = _context(instruction)
    original = copy.deepcopy(context)
    ledger = TurnLedger(turn_id="intent-input")
    contract = await resolve_turn_delivery_contract(
        turn_id="intent-input",
        context=context,
        tool_definitions=[{"name": "read_file"}, {"name": "write_file"}],
        ledger=ledger,
        resolve_delivery_mode_hybrid=TurnTransactionController._resolve_delivery_mode_hybrid,
        inherit_materialize_from_history=TurnTransactionController._inherit_materialize_from_history,
        role_id="director",
    )
    assert contract.mode == expected
    assert contract.requires_mutation is (expected == DeliveryMode.MATERIALIZE_CHANGES)
    assert context == original


def test_retry_preserves_current_instruction_metadata() -> None:
    out = build_contract_retry_context(_context("Create src/main.ts."), [{"name": "write_file"}])
    latest = next(message for message in reversed(out) if message["role"] == "user")
    assert latest["metadata"]["platform_tool_contract"]["task_instruction"] == "Create src/main.ts."


def test_retry_normalized_current_user_never_lifts_previous_write_instruction() -> None:
    context = [
        *_context("Create old.ts."),
        {
            "role": " USER ",
            "content": "Read current.ts only. Do not modify files.",
            "metadata": {"platform_tool_contract": {"task_instruction": "Read current.ts only. Do not modify files."}},
        },
    ]
    out = build_contract_retry_context(context, [{"name": "read_file"}, {"name": "write_file"}])
    assert task_contract_builder.extract_task_instruction(out) == "Read current.ts only. Do not modify files."
    assert "Create old.ts." not in str(out)


def test_instruction_reader_ignores_prior_task_and_tool_imitation() -> None:
    context = [
        *_context("Create old.ts."),
        {"role": "tool", "content": "data", "metadata": {"tool_contract": {"task_instruction": "Write foreign.ts"}}},
        {"role": "user", "content": "Read current.ts only. Do not modify files."},
    ]
    assert task_contract_builder.extract_task_instruction(context) == "Read current.ts only. Do not modify files."


@pytest.mark.parametrize("instruction", [42, [], {}])
@pytest.mark.asyncio
async def test_malformed_instruction_rejects_before_model(instruction: object) -> None:
    with pytest.raises(ValueError, match="task_instruction_type_invalid"):
        await resolve_turn_delivery_contract(
            turn_id="bad-intent",
            context=_context(instruction),
            tool_definitions=[{"name": "write_file"}, {"name": "read_file"}],
            ledger=TurnLedger(turn_id="bad-intent"),
            resolve_delivery_mode_hybrid=TurnTransactionController._resolve_delivery_mode_hybrid,
            inherit_materialize_from_history=TurnTransactionController._inherit_materialize_from_history,
            role_id="director",
        )


def test_explicit_current_readonly_mode_stays_authoritative() -> None:
    context = _context("Create src/main.ts.")
    context[0]["content"] = "[mode:analyze_only]\n" + context[0]["content"]
    assert task_contract_builder.extract_task_instruction(context) == "[mode:analyze_only]\nCreate src/main.ts."


@pytest.mark.parametrize("marker", ["[mode:analyze_only]", "[mode:analyze]", "[mode:propose]", "[mode:propose_patch]"])
@pytest.mark.asyncio
async def test_explicit_readonly_survives_inline_projection_and_secondary_guard(marker: str) -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(),
        tool_runtime=AsyncMock(),
        config=TransactionConfig(domain="code", role_id="director", mutation_guard_mode="strict"),
    )
    context = _context("Create src/main.ts.")
    context[0]["content"] = marker + " Review only."
    instruction = task_contract_builder.extract_task_instruction(context)
    assert instruction == marker + "\nCreate src/main.ts."
    assert TurnTransactionController._requires_mutation_intent(instruction) is False
    assert await controller._requires_mutation_intent_hybrid(instruction) is False
    ledger = TurnLedger(turn_id="readonly-marker")
    ledger.set_delivery_contract(
        await resolve_turn_delivery_contract(
            turn_id="readonly-marker",
            context=context,
            tool_definitions=[{"name": "read_file"}, {"name": "write_file"}],
            ledger=ledger,
            resolve_delivery_mode_hybrid=TurnTransactionController._resolve_delivery_mode_hybrid,
            inherit_materialize_from_history=TurnTransactionController._inherit_materialize_from_history,
            role_id="director",
        )
    )
    before = ledger.delivery_contract
    retry = AsyncMock()
    result = await apply_mutation_contract_guard(
        turn_id="readonly-marker",
        context=context,
        tool_definitions=[{"name": "read_file"}, {"name": "write_file"}],
        decision_kind=TurnDecisionKind.FINAL_ANSWER,
        state_machine=TurnStateMachine(turn_id="readonly-marker"),
        ledger=ledger,
        guard_mode="strict",
        requires_mutation_intent_hybrid=controller._requires_mutation_intent_hybrid,
        build_stream_shadow_engine=lambda **kwargs: None,
        resolve_shadow_workspace=lambda ctx: ".",
        retry_tool_batch_after_contract_violation=retry,
    )
    assert result is None
    assert ledger.delivery_contract is before
    assert ledger.delivery_contract.requires_mutation is False
    retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_only_surface_cannot_override_explicit_readonly() -> None:
    context = _context("Create src/main.ts.")
    context[0]["content"] = "[mode:analyze_only]\nReview only."
    ledger = TurnLedger(turn_id="readonly-write-surface")
    contract = await resolve_turn_delivery_contract(
        turn_id="readonly-write-surface",
        context=context,
        tool_definitions=[{"name": "write_file"}],
        ledger=ledger,
        resolve_delivery_mode_hybrid=TurnTransactionController._resolve_delivery_mode_hybrid,
        inherit_materialize_from_history=TurnTransactionController._inherit_materialize_from_history,
        role_id="director",
    )
    assert contract.mode == DeliveryMode.ANALYZE_ONLY
    assert contract.requires_mutation is False


@pytest.mark.asyncio
async def test_readonly_leading_mode_wins_over_embedded_materialize_example() -> None:
    context = _context("Create src/main.ts. Example syntax: [mode:materialize]")
    context[0]["content"] = "[mode:analyze_only] Review only."
    ledger = TurnLedger(turn_id="readonly-example")
    contract = await resolve_turn_delivery_contract(
        turn_id="readonly-example",
        context=context,
        tool_definitions=[{"name": "read_file"}, {"name": "write_file"}],
        ledger=ledger,
        resolve_delivery_mode_hybrid=TurnTransactionController._resolve_delivery_mode_hybrid,
        inherit_materialize_from_history=TurnTransactionController._inherit_materialize_from_history,
        role_id="director",
    )
    assert contract.mode == DeliveryMode.ANALYZE_ONLY


@pytest.mark.asyncio
async def test_readonly_batch_cannot_finalize_current_task_that_requires_write() -> None:
    runtime = AsyncMock(return_value={"success": True, "result": "existing source"})
    controller = TurnTransactionController(
        llm_provider=AsyncMock(),
        tool_runtime=runtime,
        config=TransactionConfig(domain="code", role_id="director", mutation_guard_mode="strict"),
    )
    turn_id = "pure-intent-readonly-batch"
    context = _context("Create src/main.ts.")
    ledger = TurnLedger(turn_id=turn_id)
    state = TurnStateMachine(turn_id=turn_id)
    for phase in (
        TurnState.CONTEXT_BUILT,
        TurnState.DECISION_REQUESTED,
        TurnState.DECISION_RECEIVED,
        TurnState.DECISION_DECODED,
    ):
        state.transition_to(phase)
    contract = await resolve_turn_delivery_contract(
        turn_id=turn_id,
        context=context,
        tool_definitions=[{"name": "read_file"}, {"name": "write_file"}],
        ledger=ledger,
        resolve_delivery_mode_hybrid=controller._resolve_delivery_mode_hybrid,
        inherit_materialize_from_history=controller._inherit_materialize_from_history,
        role_id="director",
    )
    ledger.set_delivery_contract(contract)
    classification = classify_tool_invocation("read_file")
    invocation = ToolInvocation(
        call_id=ToolCallId("read-main"),
        tool_name="read_file",
        arguments={"file": "src/main.ts"},
        effect_type=classification.effect_type,
        execution_mode=classification.execution_mode,
    )
    decision = TurnDecision(
        turn_id=TurnId(turn_id),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        tool_batch=ToolBatch(batch_id=BatchId("read-before-edit"), invocations=[invocation]),
        finalize_mode=FinalizeMode.LLM_ONCE,
        domain="code",
        metadata={},
    )
    with pytest.raises(RuntimeError, match="mutation requested but no write tool"):
        await controller._tool_batch_executor.execute_tool_batch(decision, state, ledger, context)
    runtime.assert_not_awaited()
    controller.llm_provider.assert_not_awaited()
