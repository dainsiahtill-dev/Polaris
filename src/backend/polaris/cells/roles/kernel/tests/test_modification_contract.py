"""Tests for ModificationContract + ReadinessEvaluator (FIX-20250422-v3).

Covers:
- ModificationContract serialization, status transitions, SESSION_PATCH extraction
- ReadinessEvaluator verdict logic
- Pre-execution gate integration with tool_batch_executor
- Backward compatibility (checkpoint v4, feature flag)
"""

from __future__ import annotations

from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.modification_contract import (
    ModificationContract,
    ModificationContractStatus,
    ModificationIntent,
    ReadinessVerdict,
    evaluate_modification_readiness,
)
from polaris.cells.roles.kernel.internal.transaction.phase_manager import Phase
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_emit_event() -> Any:
    return lambda event: None


@pytest.fixture
def mock_guard_assert() -> Any:
    return lambda **kw: None


def _make_executor(
    mock_emit_event: Any,
    mock_guard_assert: Any,
    *,
    guard_mode: Literal["strict", "warn", "off"] = "warn",
    enable_modification_contract: bool = True,
) -> ToolBatchExecutor:
    return ToolBatchExecutor(
        tool_runtime=AsyncMock(),
        config=TransactionConfig(
            mutation_guard_mode=guard_mode,
            enable_modification_contract=enable_modification_contract,
        ),
        emit_event=mock_emit_event,
        guard_assert_single_tool_batch=mock_guard_assert,
        finalization_handler=AsyncMock(),
        handoff_handler=AsyncMock(),
    )


def _make_state_machine(turn_id: str) -> TurnStateMachine:
    sm = TurnStateMachine(turn_id=turn_id)
    sm.transition_to(TurnState.CONTEXT_BUILT)
    sm.transition_to(TurnState.DECISION_REQUESTED)
    sm.transition_to(TurnState.DECISION_RECEIVED)
    sm.transition_to(TurnState.DECISION_DECODED)
    return sm


def _make_tool_batch_decision(
    turn_id: str,
    batch_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> TurnDecision:
    invocation = ToolInvocation(
        call_id=ToolCallId(f"call_{tool_name}"),
        tool_name=tool_name,
        arguments=arguments or {},
        effect_type=ToolEffectType.READ if tool_name in {"read_file", "glob", "repo_rg"} else ToolEffectType.WRITE,
        execution_mode=(
            ToolExecutionMode.READONLY_PARALLEL
            if tool_name in {"read_file", "glob", "repo_rg"}
            else ToolExecutionMode.WRITE_SERIAL
        ),
    )
    return TurnDecision(
        turn_id=TurnId(turn_id),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        finalize_mode=FinalizeMode.NONE,
        domain="code",
        metadata={"workspace": "."},
        tool_batch=ToolBatch(
            batch_id=BatchId(batch_id),
            invocations=[invocation],
        ),
    )


def _make_ledger_in_content_gathered(
    turn_id: str,
    *,
    turns_in_phase: int = 3,
    materialize: bool = True,
    contract_status: ModificationContractStatus = ModificationContractStatus.EMPTY,
    contract_targets: list[str] | None = None,
    contract_modifications: list[ModificationIntent] | None = None,
) -> TurnLedger:
    ledger = TurnLedger(turn_id=turn_id)
    if materialize:
        ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
    ledger.phase_manager._current_phase = Phase.CONTENT_GATHERED
    ledger.phase_manager._turns_in_current_phase = turns_in_phase
    ledger.modification_contract.status = contract_status
    if contract_targets:
        ledger.modification_contract.target_files = contract_targets
    if contract_modifications:
        ledger.modification_contract.modifications = contract_modifications
    return ledger


# ===========================================================================
# 1. ModificationContract Unit Tests
# ===========================================================================


class TestModificationContractBasics:
    """ModificationContract data structure and serialization tests."""

    def test_contract_default_empty(self) -> None:
        """New contract has EMPTY status."""
        tc = ModificationContract()
        assert tc.status == ModificationContractStatus.EMPTY
        assert tc.target_files == []
        assert tc.modifications == []
        assert tc.declared_at_turn == 0

    def test_contract_to_dict_from_dict_roundtrip(self) -> None:
        """Serialization preserves all fields."""
        tc = ModificationContract(
            status=ModificationContractStatus.READY,
            target_files=["a.py", "b.py"],
            modifications=[
                ModificationIntent(target_file="a.py", action="add error handling", confidence="confirmed"),
                ModificationIntent(target_file="b.py", action="refactor class", confidence="likely"),
            ],
            rationale="User requested improvements",
            declared_at_turn=2,
            last_updated_at_turn=3,
        )
        data = tc.to_dict()
        restored = ModificationContract.from_dict(data)

        assert restored.status == ModificationContractStatus.READY
        assert restored.target_files == ["a.py", "b.py"]
        assert len(restored.modifications) == 2
        assert restored.modifications[0].action == "add error handling"
        assert restored.modifications[0].confidence == "confirmed"
        assert restored.rationale == "User requested improvements"
        assert restored.declared_at_turn == 2
        assert restored.last_updated_at_turn == 3

    def test_update_from_session_patch_empty_plan(self) -> None:
        """Empty modification_plan keeps EMPTY status."""
        tc = ModificationContract()
        tc.update_from_session_patch({"modification_plan": []}, current_turn=1)
        assert tc.status == ModificationContractStatus.EMPTY

    def test_update_from_session_patch_partial_plan(self) -> None:
        """target_files only (no action) -> DRAFT."""
        tc = ModificationContract()
        tc.update_from_session_patch(
            {"modification_plan": [{"target_file": "orchestrator.py"}]},
            current_turn=1,
        )
        assert tc.status == ModificationContractStatus.DRAFT
        assert tc.target_files == ["orchestrator.py"]
        assert tc.declared_at_turn == 1

    def test_update_from_session_patch_complete_plan(self) -> None:
        """target_files + actions -> READY."""
        tc = ModificationContract()
        tc.update_from_session_patch(
            {
                "modification_plan": [
                    {"target_file": "a.py", "action": "add timeout handling"},
                    {"target_file": "b.py", "action": "refactor connect()"},
                ]
            },
            current_turn=2,
        )
        assert tc.status == ModificationContractStatus.READY
        assert tc.target_files == ["a.py", "b.py"]
        assert len(tc.modifications) == 2
        assert tc.modifications[0].action == "add timeout handling"
        assert tc.declared_at_turn == 2
        assert tc.last_updated_at_turn == 2

    def test_update_idempotent(self) -> None:
        """Multiple updates don't regress status. READY stays READY."""
        tc = ModificationContract()
        tc.update_from_session_patch(
            {"modification_plan": [{"target_file": "a.py", "action": "fix bug"}]},
            current_turn=1,
        )
        assert tc.status == ModificationContractStatus.READY

        # Second update with partial info doesn't regress
        tc.update_from_session_patch(
            {"modification_plan": [{"target_file": "b.py"}]},
            current_turn=2,
        )
        assert tc.status == ModificationContractStatus.READY
        assert "b.py" in tc.target_files
        assert tc.last_updated_at_turn == 2

    def test_from_dict_missing_fields_graceful(self) -> None:
        """Missing keys -> EMPTY (backward compat)."""
        assert ModificationContract.from_dict({}).status == ModificationContractStatus.EMPTY
        assert ModificationContract.from_dict(None).status == ModificationContractStatus.EMPTY  # type: ignore[arg-type]
        assert ModificationContract.from_dict({"status": "invalid"}).status == ModificationContractStatus.EMPTY

    def test_format_for_prompt_empty(self) -> None:
        """Empty contract formats correctly."""
        tc = ModificationContract()
        result = tc.format_for_prompt()
        assert "EMPTY" in result

    def test_format_for_prompt_ready(self) -> None:
        """Ready contract includes target files and actions."""
        tc = ModificationContract(
            status=ModificationContractStatus.READY,
            target_files=["a.py"],
            modifications=[ModificationIntent(target_file="a.py", action="fix bug")],
        )
        result = tc.format_for_prompt()
        assert "READY" in result
        assert "a.py" in result
        assert "fix bug" in result


# ===========================================================================
# 2. ReadinessEvaluator Tests
# ===========================================================================


class TestReadinessEvaluator:
    """evaluate_modification_readiness() pure function tests."""

    def test_analyze_only_always_ready(self) -> None:
        """Non-MATERIALIZE -> READY_TO_WRITE."""
        tc = ModificationContract()
        verdict = evaluate_modification_readiness(tc, "content_gathered", "analyze_only", 5, 3)
        assert verdict == ReadinessVerdict.READY_TO_WRITE

    def test_exploring_phase_always_ready(self) -> None:
        """Not CONTENT_GATHERED -> READY_TO_WRITE."""
        tc = ModificationContract()
        verdict = evaluate_modification_readiness(tc, "exploring", "materialize_changes", 5, 3)
        assert verdict == ReadinessVerdict.READY_TO_WRITE

    def test_content_gathered_ready_contract(self) -> None:
        """READY contract -> READY_TO_WRITE."""
        tc = ModificationContract(status=ModificationContractStatus.READY)
        verdict = evaluate_modification_readiness(tc, "content_gathered", "materialize_changes", 2, 3)
        assert verdict == ReadinessVerdict.READY_TO_WRITE

    def test_content_gathered_draft_with_complete_info(self) -> None:
        """Complete DRAFT auto-promotes to READY."""
        tc = ModificationContract(
            status=ModificationContractStatus.DRAFT,
            target_files=["a.py"],
            modifications=[ModificationIntent(target_file="a.py", action="fix")],
        )
        verdict = evaluate_modification_readiness(tc, "content_gathered", "materialize_changes", 2, 3)
        assert verdict == ReadinessVerdict.READY_TO_WRITE
        assert tc.status == ModificationContractStatus.READY  # auto-promoted

    def test_content_gathered_empty_contract(self) -> None:
        """EMPTY -> NEEDS_PLAN."""
        tc = ModificationContract()
        verdict = evaluate_modification_readiness(tc, "content_gathered", "materialize_changes", 2, 3)
        assert verdict == ReadinessVerdict.NEEDS_PLAN

    def test_content_gathered_draft_incomplete(self) -> None:
        """Incomplete DRAFT (targets but no actions) -> NEEDS_PLAN."""
        tc = ModificationContract(
            status=ModificationContractStatus.DRAFT,
            target_files=["a.py"],
            modifications=[ModificationIntent(target_file="a.py", action="")],
        )
        verdict = evaluate_modification_readiness(tc, "content_gathered", "materialize_changes", 2, 3)
        assert verdict == ReadinessVerdict.NEEDS_PLAN


# ===========================================================================
# 3. Pre-Execution Gate Integration Tests
# ===========================================================================


class TestPreExecutionGateIntegration:
    """tool_batch_executor readiness gate integration tests."""

    @pytest.mark.asyncio
    async def test_readiness_gate_allows_reads_when_needs_plan(
        self, mock_emit_event: Any, mock_guard_assert: Any
    ) -> None:
        """NEEDS_PLAN + turns < max -> no exception (reads allowed)."""
        executor = _make_executor(mock_emit_event, mock_guard_assert)
        decision = _make_tool_batch_decision("turn_rg_1", "batch_1", "read_file", {"file": "a.py"})
        sm = _make_state_machine("turn_rg_1")
        ledger = _make_ledger_in_content_gathered(
            "turn_rg_1",
            turns_in_phase=1,
            contract_status=ModificationContractStatus.EMPTY,
        )
        context = [{"role": "user", "content": "完善代码"}]

        # Should NOT raise (NEEDS_PLAN + turns=1 < max=3 -> allow)
        result = await executor.execute_tool_batch(decision, sm, ledger, context, stream=False)
        assert result.get("turn_id") == "turn_rg_1"

    @pytest.mark.asyncio
    async def test_readiness_gate_blocks_reads_when_ready(self, mock_emit_event: Any, mock_guard_assert: Any) -> None:
        """READY + no writes -> RuntimeError."""
        executor = _make_executor(mock_emit_event, mock_guard_assert)
        decision = _make_tool_batch_decision("turn_rg_2", "batch_2", "read_file", {"file": "a.py"})
        sm = _make_state_machine("turn_rg_2")
        ledger = _make_ledger_in_content_gathered(
            "turn_rg_2",
            turns_in_phase=2,
            contract_status=ModificationContractStatus.READY,
            contract_targets=["a.py"],
            contract_modifications=[ModificationIntent(target_file="a.py", action="fix bug")],
        )
        context = [{"role": "user", "content": "完善代码"}]

        with pytest.raises(RuntimeError, match="modification plan is confirmed"):
            await executor.execute_tool_batch(decision, sm, ledger, context, stream=False)

    @pytest.mark.asyncio
    async def test_readiness_gate_degrades_past_max_turns(self, mock_emit_event: Any, mock_guard_assert: Any) -> None:
        """NEEDS_PLAN + turns >= max -> RuntimeError (timeout degradation)."""
        executor = _make_executor(mock_emit_event, mock_guard_assert)
        decision = _make_tool_batch_decision("turn_rg_3", "batch_3", "read_file", {"file": "a.py"})
        sm = _make_state_machine("turn_rg_3")
        ledger = _make_ledger_in_content_gathered(
            "turn_rg_3",
            turns_in_phase=3,  # == max_turns_per_phase (default 3)
            contract_status=ModificationContractStatus.EMPTY,
        )
        context = [{"role": "user", "content": "完善代码"}]

        with pytest.raises(RuntimeError, match="phase timeout"):
            await executor.execute_tool_batch(decision, sm, ledger, context, stream=False)

    @pytest.mark.asyncio
    async def test_feature_flag_disabled_uses_old_behavior(self, mock_emit_event: Any, mock_guard_assert: Any) -> None:
        """enable_modification_contract=False -> old turns>=2 check."""
        executor = _make_executor(mock_emit_event, mock_guard_assert, enable_modification_contract=False)
        decision = _make_tool_batch_decision("turn_rg_4", "batch_4", "read_file", {"file": "a.py"})
        sm = _make_state_machine("turn_rg_4")
        # turns_in_phase=2 should trigger the old hard block
        ledger = _make_ledger_in_content_gathered(
            "turn_rg_4",
            turns_in_phase=2,
            contract_status=ModificationContractStatus.EMPTY,
        )
        context = [{"role": "user", "content": "完善代码"}]

        with pytest.raises(RuntimeError, match="CONTENT_GATHERED phase requires write tools"):
            await executor.execute_tool_batch(decision, sm, ledger, context, stream=False)


# ===========================================================================
# 4. Backward Compatibility
# ===========================================================================


class TestBackwardCompatibility:
    """Checkpoint v4 compatibility and schema migration."""

    def test_checkpoint_v4_loads_with_empty_contract(self) -> None:
        """v4 checkpoint data (no modification_contract key) -> empty ModificationContract."""
        tc = ModificationContract.from_dict({})
        assert tc.status == ModificationContractStatus.EMPTY
        assert tc.target_files == []
        assert tc.modifications == []

    def test_ledger_has_modification_contract(self) -> None:
        """TurnLedger has modification_contract field with default EMPTY status."""
        ledger = TurnLedger(turn_id="test")
        assert ledger.modification_contract.status == ModificationContractStatus.EMPTY


# ===========================================================================
# 5. SUPER_MODE Bypass Tests (FIX-20250422-SUPER)
# ===========================================================================


class TestSuperModeBypass:
    """SUPER_MODE marker detection and readiness bypass tests."""

    def test_super_mode_handoff_bypasses_empty_contract(self) -> None:
        """[SUPER_MODE_HANDOFF] in context bypasses EMPTY contract -> READY_TO_WRITE."""
        tc = ModificationContract(status=ModificationContractStatus.EMPTY)
        context = [{"role": "user", "content": "[SUPER_MODE_HANDOFF]\nExecute plan\n[/SUPER_MODE_HANDOFF]"}]
        verdict = evaluate_modification_readiness(
            tc, "content_gathered", "materialize_changes", 2, 3, conversation_context=context
        )
        assert verdict == ReadinessVerdict.READY_TO_WRITE

    def test_super_mode_continue_bypasses_empty_contract(self) -> None:
        """[SUPER_MODE_DIRECTOR_CONTINUE] in context bypasses EMPTY contract -> READY_TO_WRITE."""
        tc = ModificationContract(status=ModificationContractStatus.EMPTY)
        context = [{"role": "user", "content": "[SUPER_MODE_DIRECTOR_CONTINUE]\nKeep going\n[/SUPER_MODE_DIRECTOR_CONTINUE]"}]
        verdict = evaluate_modification_readiness(
            tc, "content_gathered", "materialize_changes", 2, 3, conversation_context=context
        )
        assert verdict == ReadinessVerdict.READY_TO_WRITE

    def test_no_super_mode_markers_uses_normal_rules(self) -> None:
        """No SUPER_MODE markers -> normal rules apply (EMPTY -> NEEDS_PLAN)."""
        tc = ModificationContract(status=ModificationContractStatus.EMPTY)
        context = [{"role": "user", "content": "完善代码"}]
        verdict = evaluate_modification_readiness(
            tc, "content_gathered", "materialize_changes", 2, 3, conversation_context=context
        )
        assert verdict == ReadinessVerdict.NEEDS_PLAN

    def test_super_mode_bypass_with_none_context(self) -> None:
        """None context -> normal rules apply."""
        tc = ModificationContract(status=ModificationContractStatus.EMPTY)
        verdict = evaluate_modification_readiness(
            tc, "content_gathered", "materialize_changes", 2, 3, conversation_context=None
        )
        assert verdict == ReadinessVerdict.NEEDS_PLAN

    def test_super_mode_bypass_with_empty_context(self) -> None:
        """Empty list context -> normal rules apply."""
        tc = ModificationContract(status=ModificationContractStatus.EMPTY)
        verdict = evaluate_modification_readiness(
            tc, "content_gathered", "materialize_changes", 2, 3, conversation_context=[]
        )
        assert verdict == ReadinessVerdict.NEEDS_PLAN

    def test_super_mode_marker_in_assistant_message(self) -> None:
        """SUPER_MODE marker in assistant message also triggers bypass."""
        tc = ModificationContract(status=ModificationContractStatus.EMPTY)
        context = [
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "[SUPER_MODE_HANDOFF]\nplan\n[/SUPER_MODE_HANDOFF]"},
        ]
        verdict = evaluate_modification_readiness(
            tc, "content_gathered", "materialize_changes", 2, 3, conversation_context=context
        )
        assert verdict == ReadinessVerdict.READY_TO_WRITE

    @pytest.mark.asyncio
    async def test_pre_execution_gate_bypasses_for_super_mode(
        self, mock_emit_event: Any, mock_guard_assert: Any
    ) -> None:
        """ToolBatchExecutor bypasses readiness gate when SUPER_MODE markers present.

        When SUPER_MODE is detected, the readiness evaluator returns READY_TO_WRITE,
        which means read tools are blocked and write tools are required. So we use
        write_file (a write tool) to verify the gate allows execution.
        """
        executor = _make_executor(mock_emit_event, mock_guard_assert)
        # Use write_file (write tool) instead of read_file — SUPER_MODE says "ready to write"
        decision = _make_tool_batch_decision("turn_sm_1", "batch_sm_1", "write_file", {"file": "a.py", "content": "x"})
        sm = _make_state_machine("turn_sm_1")
        ledger = _make_ledger_in_content_gathered(
            "turn_sm_1",
            turns_in_phase=1,
            contract_status=ModificationContractStatus.EMPTY,
        )
        context = [{"role": "user", "content": "[SUPER_MODE_HANDOFF]\nExecute this plan immediately\n[/SUPER_MODE_HANDOFF]"}]

        # Should NOT raise — SUPER_MODE bypasses the readiness gate, allowing write tools
        result = await executor.execute_tool_batch(decision, sm, ledger, context, stream=False)
        assert result.get("turn_id") == "turn_sm_1"
