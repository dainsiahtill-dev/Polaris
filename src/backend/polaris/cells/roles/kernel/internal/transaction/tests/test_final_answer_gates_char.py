"""Characterization tests for transaction.final_answer_gates (REMAINING_06 step 5).

UTF-8 编码验证: 本文所有文本使用 UTF-8。

Pins the behavior of the two FINAL_ANSWER block-gate evaluators extracted
verbatim from ``TurnTransactionController._handle_final_answer``:

* ``evaluate_materialize_violation_gate`` — Invariant A write-side gate
  (no-write-tool vs inline-patch-escape vs refusal-exempt vs not-fired).
* ``evaluate_recon_required_gate`` — ADR-0091 R1 read-side gate
  (blocked vs refusal-exempt vs not-required vs recon-satisfied).

These assert the returned :class:`FinalAnswerBlock` descriptor AND the ledger
side-effects (anomaly flags, inline-patch rejection accounting, ``mark_blocked``)
that the evaluators perform in the original pre-block order.
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    BlockedReason,
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.final_answer_gates import (
    FinalAnswerBlock,
    evaluate_materialize_violation_gate,
    evaluate_recon_required_gate,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger


def _materialize_ledger(turn_id: str = "t") -> TurnLedger:
    ledger = TurnLedger(turn_id=turn_id)
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))
    return ledger


# ----------------------------------------------------------------------------
# materialize-violation gate
# ----------------------------------------------------------------------------


def test_materialize_gate_not_fired_when_contract_is_not_materialize() -> None:
    ledger = TurnLedger(turn_id="t")
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.ANALYZE_ONLY))
    assert evaluate_materialize_violation_gate(turn_id="t", visible_content="done", ledger=ledger) is None


def test_materialize_gate_not_fired_when_mutation_satisfied() -> None:
    ledger = _materialize_ledger()
    ledger.mutation_obligation.record_write_receipt()
    assert ledger.mutation_obligation.mutation_satisfied is True
    assert evaluate_materialize_violation_gate(turn_id="t", visible_content="任意内容", ledger=ledger) is None


def test_materialize_gate_blocks_no_write_tool() -> None:
    ledger = _materialize_ledger("turn_block")
    block = evaluate_materialize_violation_gate(
        turn_id="turn_block",
        visible_content="这是我对实现的描述说明，没有任何代码块。",
        ledger=ledger,
    )
    assert isinstance(block, FinalAnswerBlock)
    assert block.kind == "mutation_bypass_blocked"
    assert block.finalization["error"] == "MUTATION_BYPASS_BLOCKED"
    assert block.finalization["blocked_reason"] == BlockedReason.NO_WRITE_TOOL_AVAILABLE.value
    assert block.finalization["escape_metrics"] is None
    assert ledger.mutation_obligation.blocked_reason is BlockedReason.NO_WRITE_TOOL_AVAILABLE


def test_materialize_gate_blocks_inline_patch_escape() -> None:
    ledger = _materialize_ledger("turn_escape")
    # A large fenced code block dominates the response -> inline-patch escape.
    big_code = "\n".join(f"line_{i} = {i}" for i in range(80))
    visible = f"这是实现：\n```python\n{big_code}\n```\n"
    block = evaluate_materialize_violation_gate(turn_id="turn_escape", visible_content=visible, ledger=ledger)
    assert isinstance(block, FinalAnswerBlock)
    assert block.kind == "inline_patch_escape_blocked"
    assert block.finalization["blocked_reason"] == BlockedReason.SAFETY_CONSTRAINT.value
    assert block.finalization["escape_metrics"] is not None
    assert block.finalization["escape_metrics"]["is_escape"] is True
    # Side-effects: inline-patch rejection + anomaly flag recorded.
    assert any(flag.get("type") == "INLINE_PATCH_ESCAPE" for flag in ledger.anomaly_flags)
    assert ledger.mutation_obligation.blocked_reason is BlockedReason.SAFETY_CONSTRAINT


def test_materialize_gate_exempts_refusal() -> None:
    ledger = _materialize_ledger("turn_refusal")
    # REFUSAL_MARKERS contains "无法" — an explicit refusal is exempt.
    block = evaluate_materialize_violation_gate(
        turn_id="turn_refusal",
        visible_content="无法完成该请求，因为缺少必要的写工具。",
        ledger=ledger,
    )
    assert block is None
    assert ledger.mutation_obligation.blocked_reason is None


# ----------------------------------------------------------------------------
# recon-required gate
# ----------------------------------------------------------------------------


def test_recon_gate_not_fired_when_not_required() -> None:
    ledger = TurnLedger(turn_id="t")
    assert (
        evaluate_recon_required_gate(turn_id="t", visible_content="answer", ledger=ledger, recon_required=False) is None
    )


def test_recon_gate_not_fired_when_recon_satisfied() -> None:
    ledger = TurnLedger(turn_id="t")
    ledger.record_tool_execution("repo_rg", "call_1", "success", 5)
    assert (
        evaluate_recon_required_gate(turn_id="t", visible_content="answer", ledger=ledger, recon_required=True) is None
    )


def test_recon_gate_blocks_zero_recon() -> None:
    ledger = TurnLedger(turn_id="turn_recon")
    block = evaluate_recon_required_gate(
        turn_id="turn_recon",
        visible_content="答案在 core/contracts.py。",
        ledger=ledger,
        recon_required=True,
    )
    assert isinstance(block, FinalAnswerBlock)
    assert block.kind == "recon_bypass_blocked"
    assert block.finalization["error"] == "RECON_BYPASS_BLOCKED"
    assert block.finalization["blocked_reason"] == BlockedReason.NO_RECON_PERFORMED.value
    assert "escape_metrics" not in block.finalization
    assert ledger.mutation_obligation.blocked_reason is BlockedReason.NO_RECON_PERFORMED


def test_recon_gate_exempts_refusal() -> None:
    ledger = TurnLedger(turn_id="turn_recon_refusal")
    block = evaluate_recon_required_gate(
        turn_id="turn_recon_refusal",
        visible_content="无法在当前工作区定位该符号。",
        ledger=ledger,
        recon_required=True,
    )
    assert block is None
    assert ledger.mutation_obligation.blocked_reason is None


def test_recon_gate_all_failed_recon_still_blocks() -> None:
    ledger = TurnLedger(turn_id="turn_recon_failed")
    ledger.record_tool_execution("repo_rg", "call_1", "error", 5)
    block = evaluate_recon_required_gate(
        turn_id="turn_recon_failed",
        visible_content="答案在某处。",
        ledger=ledger,
        recon_required=True,
    )
    assert isinstance(block, FinalAnswerBlock)
    assert block.kind == "recon_bypass_blocked"
