"""Regression tests for the shared delivery-mode write-tool filter and for the
materialize-intent inheritance downgrade guard.

FINDING 1 (fail-open): the streaming path never applied the ANALYZE_ONLY /
PROPOSE_PATCH write-tool filter, so a streaming Director could mutate the
workspace despite a read-only / propose-only contract. The filter now lives in
``contract_guards.apply_delivery_mode_filter`` and is shared by both paths.

FINDING 2 (control-flow): ``_inherit_materialize_from_history`` re-armed
MATERIALIZE_CHANGES on short continuation messages without re-checking whether
the latest message itself negates the mutation or carries an explicit
analyze/propose downgrade marker.
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    apply_delivery_mode_filter,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
    TurnTransactionController,
)
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


def _write_decision(turn_id: str) -> TurnDecision:
    """Build a TOOL_BATCH decision containing a single write_file invocation."""
    invocation = ToolInvocation(
        call_id=ToolCallId("call_write_1"),
        tool_name="write_file",
        arguments={"file": "app.py", "content": "print('x')"},
        effect_type=ToolEffectType.WRITE,
        execution_mode=ToolExecutionMode.WRITE_SERIAL,
    )
    batch = ToolBatch(
        batch_id=BatchId(f"{turn_id}_batch"),
        invocations=[invocation],
        serial_writes=[invocation],
    )
    return TurnDecision(
        turn_id=TurnId(turn_id),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="writing",
        tool_batch=batch,
        finalize_mode=FinalizeMode.NONE,
        domain="code",
    )


def _mixed_decision(turn_id: str) -> TurnDecision:
    """Build a TOOL_BATCH decision with one read and one write invocation."""
    read_inv = ToolInvocation(
        call_id=ToolCallId("call_read_1"),
        tool_name="read_file",
        arguments={"file": "app.py"},
        effect_type=ToolEffectType.READ,
        execution_mode=ToolExecutionMode.READONLY_SERIAL,
    )
    write_inv = ToolInvocation(
        call_id=ToolCallId("call_write_1"),
        tool_name="write_file",
        arguments={"file": "app.py", "content": "print('x')"},
        effect_type=ToolEffectType.WRITE,
        execution_mode=ToolExecutionMode.WRITE_SERIAL,
    )
    batch = ToolBatch(
        batch_id=BatchId(f"{turn_id}_batch"),
        invocations=[read_inv, write_inv],
        readonly_serial=[read_inv],
        serial_writes=[write_inv],
    )
    return TurnDecision(
        turn_id=TurnId(turn_id),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="reading and writing",
        tool_batch=batch,
        finalize_mode=FinalizeMode.NONE,
        domain="code",
    )


def _ledger_with_mode(mode: DeliveryMode) -> TurnLedger:
    ledger = TurnLedger(turn_id="t1")
    ledger.set_delivery_contract(
        DeliveryContract(
            mode=mode,
            requires_mutation=(mode == DeliveryMode.MATERIALIZE_CHANGES),
            requires_verification=False,
            allow_inline_code=mode != DeliveryMode.MATERIALIZE_CHANGES,
            allow_patch_proposal=mode == DeliveryMode.PROPOSE_PATCH,
        )
    )
    return ledger


class TestApplyDeliveryModeFilterShared:
    """FINDING 1: the filter is a shared helper with identical semantics."""

    def test_analyze_only_strips_all_writes_and_downgrades_to_final_answer(self) -> None:
        ledger = _ledger_with_mode(DeliveryMode.ANALYZE_ONLY)
        decision = _write_decision("t1")

        filtered = apply_delivery_mode_filter(decision, ledger)

        assert filtered.get("kind") == TurnDecisionKind.FINAL_ANSWER
        assert filtered.get("tool_batch") is None
        anomalies = [a for a in ledger.anomaly_flags if a["type"] == "DELIVERY_MODE_WRITE_TOOL_FILTERED"]
        assert len(anomalies) == 1
        assert anomalies[0]["dropped_count"] == 1
        assert anomalies[0]["delivery_mode"] == DeliveryMode.ANALYZE_ONLY.value

    def test_propose_patch_strips_writes_keeps_reads(self) -> None:
        ledger = _ledger_with_mode(DeliveryMode.PROPOSE_PATCH)
        decision = _mixed_decision("t1")

        filtered = apply_delivery_mode_filter(decision, ledger)

        assert filtered.get("kind") == TurnDecisionKind.TOOL_BATCH
        batch = filtered.get("tool_batch")
        assert batch is not None
        names = [inv.get("tool_name") for inv in batch.get("invocations", [])]
        assert names == ["read_file"]
        assert any(a["type"] == "DELIVERY_MODE_WRITE_TOOL_FILTERED" for a in ledger.anomaly_flags)

    def test_materialize_changes_passthrough(self) -> None:
        ledger = _ledger_with_mode(DeliveryMode.MATERIALIZE_CHANGES)
        decision = _write_decision("t1")

        filtered = apply_delivery_mode_filter(decision, ledger)

        assert filtered is decision
        assert ledger.anomaly_flags == []

    def test_controller_static_method_delegates_to_shared_helper(self) -> None:
        # The controller's static method must produce identical results to the
        # shared helper (no divergent second implementation).
        ledger = _ledger_with_mode(DeliveryMode.ANALYZE_ONLY)
        decision = _write_decision("t1")

        via_controller = TurnTransactionController._apply_delivery_mode_filter(decision, ledger)

        assert via_controller.get("kind") == TurnDecisionKind.FINAL_ANSWER
        assert via_controller.get("tool_batch") is None


class TestInheritMaterializeDowngradeGuard:
    """FINDING 2: latest negation / downgrade marker must block inheritance."""

    @staticmethod
    def _history(latest: str) -> list[dict]:
        return [
            {"role": "user", "content": "实现登录功能"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": latest},
        ]

    def test_negated_latest_message_blocks_inheritance(self) -> None:
        # History wants MATERIALIZE, but the latest message negates mutation.
        result = TurnTransactionController._inherit_materialize_from_history(self._history("不要修改"), "不要修改")
        assert result is None

    def test_explicit_analyze_marker_blocks_inheritance(self) -> None:
        result = TurnTransactionController._inherit_materialize_from_history(
            self._history("[mode:analyze]"), "[mode:analyze]"
        )
        assert result is None

    def test_explicit_propose_marker_blocks_inheritance(self) -> None:
        result = TurnTransactionController._inherit_materialize_from_history(
            self._history("[mode:propose]"), "[mode:propose]"
        )
        assert result is None

    def test_plain_continuation_still_inherits_materialize(self) -> None:
        # Control case: a neutral continuation shortcut still inherits.
        result = TurnTransactionController._inherit_materialize_from_history(self._history("继续"), "继续")
        assert result is not None
        assert result.mode == DeliveryMode.MATERIALIZE_CHANGES
