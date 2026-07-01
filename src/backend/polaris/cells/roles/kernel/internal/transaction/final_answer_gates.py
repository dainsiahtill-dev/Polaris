"""FINAL_ANSWER block-gate evaluators for the transaction kernel.

UTF-8 编码验证: 本文所有文本使用 UTF-8。

Extracted verbatim (behavior-preserving) from
``TurnTransactionController._handle_final_answer`` per the REMAINING_06
decomposition blueprint (step 5). Owns the two symmetric finalize-time gates:

* the MATERIALIZE_CHANGES write-side gate (Invariant A): a FINAL_ANSWER that
  carries no successful write receipt in a must-materialize contract is blocked
  (``mutation_bypass_blocked``), unless the visible content is an inline-patch
  escape (``inline_patch_escape_blocked``) or an explicit refusal.
* the recon-required read-side gate (ADR-0091 R1, symmetric to the write-side
  gate): a zero-reconnaissance FINAL_ANSWER in a ``recon_required`` kernel is
  blocked (``recon_bypass_blocked``), unless the visible content is a refusal.

Both gates are *block-only*: they never inject a second TurnDecision or a
ToolBatch, preserving the ADR-0071 single-commit / 1-TurnDecision invariants.
Each evaluator performs the ledger mutations that precede the shared block tail
(anomaly flagging, inline-patch rejection accounting, ``mark_blocked``) in the
exact original order, then returns a :class:`FinalAnswerBlock` descriptor for
the controller to finalize through the single commit path. ``None`` means the
gate did not fire and the turn should fall through to normal completion.

The evaluators are pure with respect to ``self`` (they depend only on their
arguments and module-level imports), so the controller can delegate to them
without any per-turn object allocation beyond the returned descriptor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from polaris.cells.roles.kernel.internal.transaction import constants as tx_constants
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    has_successful_recon_execution,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import BlockedReason
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    detect_inline_patch_escape,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FinalAnswerBlock:
    """Descriptor for a blocked FINAL_ANSWER produced by a finalize gate.

    ``kind`` is the TurnResult kind (e.g. ``mutation_bypass_blocked``); the
    controller renders the shared block tail (state transition, finalize,
    failed CompletionEvent, ``_build_turn_result``) using ``kind`` and
    ``finalization``.
    """

    kind: str
    finalization: dict[str, Any]


def evaluate_materialize_violation_gate(
    *,
    turn_id: Any,
    visible_content: str,
    ledger: TurnLedger,
) -> FinalAnswerBlock | None:
    """Evaluate the MATERIALIZE_CHANGES write-side gate.

    Returns a :class:`FinalAnswerBlock` when the gate fires (after recording the
    same anomaly/rejection/mark_blocked ledger side-effects as the original
    inline body, in the original order), or ``None`` to fall through.
    """
    # MATERIALIZE_CHANGES 模式下，FINAL_ANSWER 意味着没有 write receipt —— 违反 Invariant A
    if not (ledger.delivery_contract.must_materialize and not ledger.mutation_obligation.mutation_satisfied):
        return None

    # 例外：明确的拒绝响应
    lowered_visible = visible_content.lower()
    is_refusal = any(marker in lowered_visible for marker in tx_constants.REFUSAL_MARKERS)
    if is_refusal:
        return None

    # Inline Patch Escape 检测（附加诊断）
    escape_result = detect_inline_patch_escape(visible_content)
    if escape_result["is_escape"]:
        ledger.mutation_obligation.record_inline_patch_rejected()
        ledger.anomaly_flags.append(
            {
                "type": "INLINE_PATCH_ESCAPE",
                "turn_id": turn_id,
                "ratio": escape_result["ratio"],
                "code_block_chars": escape_result["code_block_chars"],
                "total_chars": escape_result["total_chars"],
                "code_blocks_count": escape_result["code_blocks_count"],
            }
        )
        blocked_reason = BlockedReason.SAFETY_CONSTRAINT
        blocked_detail = (
            f"INLINE_PATCH_ESCAPE detected: token density ratio={escape_result['ratio']:.2f}. "
            "MATERIALIZE_CHANGES mode requires write tools, not inline code blocks."
        )
        kind = "inline_patch_escape_blocked"
    else:
        blocked_reason = BlockedReason.NO_WRITE_TOOL_AVAILABLE
        blocked_detail = (
            "MATERIALIZE_CHANGES mode requires at least one successful write tool invocation, "
            "but FINAL_ANSWER was received without any write receipt."
        )
        kind = "mutation_bypass_blocked"

    logger.warning(
        "materialize-violation-blocked: turn_id=%s kind=%s reason=%s detail=%s",
        turn_id,
        kind,
        blocked_reason.value,
        blocked_detail,
    )
    ledger.mutation_obligation.mark_blocked(blocked_reason, detail=blocked_detail)
    return FinalAnswerBlock(
        kind=kind,
        finalization={
            "error": kind.upper(),
            "blocked_reason": blocked_reason.value,
            "blocked_detail": blocked_detail,
            "escape_metrics": escape_result if escape_result["is_escape"] else None,
        },
    )


def evaluate_recon_required_gate(
    *,
    turn_id: Any,
    visible_content: str,
    ledger: TurnLedger,
    recon_required: bool,
) -> FinalAnswerBlock | None:
    """Evaluate the recon-required read-side gate (ADR-0091 R1).

    Returns a :class:`FinalAnswerBlock` when the gate fires (after recording the
    same ``mark_blocked`` ledger side-effect as the original inline body), or
    ``None`` to fall through.
    """
    # recon_required 内核中，零侦察的 FINAL_ANSWER —— 违反读侧落地不变量
    # (ADR-0091 R1，与上方 must_materialize 写侧门禁对称)。block-only：
    # 不注入第二个 TurnDecision、不注入 ToolBatch（ADR-0071 兼容）。
    if not (recon_required and not has_successful_recon_execution(ledger.tool_executions)):
        return None

    # 例外：明确的拒绝响应（ADR-0091 R2，与写侧豁免一致）
    lowered_visible = visible_content.lower()
    is_refusal = any(marker in lowered_visible for marker in tx_constants.REFUSAL_MARKERS)
    if is_refusal:
        return None

    blocked_reason = BlockedReason.NO_RECON_PERFORMED
    blocked_detail = (
        "recon_required mode demands at least one successful reconnaissance "
        "tool execution (read/search) before FINAL_ANSWER, but none was recorded."
    )
    kind = "recon_bypass_blocked"
    logger.warning(
        "recon-violation-blocked: turn_id=%s kind=%s reason=%s detail=%s",
        turn_id,
        kind,
        blocked_reason.value,
        blocked_detail,
    )
    ledger.mutation_obligation.mark_blocked(blocked_reason, detail=blocked_detail)
    return FinalAnswerBlock(
        kind=kind,
        finalization={
            "error": kind.upper(),
            "blocked_reason": blocked_reason.value,
            "blocked_detail": blocked_detail,
        },
    )
