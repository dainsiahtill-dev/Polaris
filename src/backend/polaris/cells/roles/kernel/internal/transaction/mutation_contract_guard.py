"""Phase-4 mutation-contract guard reconciliation for the turn kernel.

UTF-8 编码验证: 本文所有文本使用 UTF-8。

Extracted verbatim (behavior-preserving) from
``TurnTransactionController._execute_turn`` Phase-4 per the REMAINING_06
decomposition blueprint (step 3). Reconciles the delivery-contract mutation
requirement with the hybrid intent classifier (auto-upgrade / upgrade-blocked),
then enforces Invariant A for non-tool decisions in MATERIALIZE/strict modes.

ADR-0071 note: when the guard fires it routes through
``retry_tool_batch_after_contract_violation`` (a single corrective re-ask that
honours the <=1-ToolBatch invariant) rather than injecting a second decision.
The retry/shadow collaborators are injected as callables so controller-level
test seams still penetrate this module without duplicating execution logic.

Returns ``None`` when the turn should fall through to the normal decision
dispatch; returns a TurnResult ``dict`` when the guard produced a blocking
retry result that the caller must return directly.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from polaris.cells.roles.kernel.internal.stream_shadow_engine import StreamShadowEngine
from polaris.cells.roles.kernel.internal.transaction.contract_guards import has_available_write_tool
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import extract_latest_user_message
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import TurnDecisionKind

logger = logging.getLogger(__name__)


async def apply_mutation_contract_guard(
    *,
    turn_id: str,
    context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
    decision_kind: Any,
    state_machine: TurnStateMachine,
    ledger: TurnLedger,
    guard_mode: str,
    requires_mutation_intent_hybrid: Callable[[str], Awaitable[bool]],
    build_stream_shadow_engine: Callable[..., StreamShadowEngine | None],
    resolve_shadow_workspace: Callable[[list[dict[str, Any]]], str],
    retry_tool_batch_after_contract_violation: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Reconcile mutation intent and enforce the non-tool mutation guard.

    Returns the blocking retry result when the guard fires in strict/force-block
    mode, otherwise ``None`` to signal passthrough to the normal dispatch.
    """
    latest_user_request = extract_latest_user_message(context)
    # 统一 mutation 判断：delivery contract + intent hybrid 任一判定需要 mutation 即触发 guard
    requires_mutation_by_contract = ledger.delivery_contract.requires_mutation
    requires_mutation_by_intent = await requires_mutation_intent_hybrid(latest_user_request)
    # 两套系统不一致时，以"需要 mutation"为准，自动升级 delivery contract
    # FIX-20250422: 但如果当前角色没有写工具，不能升级，否则会导致死循环
    if requires_mutation_by_intent and not requires_mutation_by_contract:
        if not has_available_write_tool(tool_definitions):
            logger.warning(
                "delivery-contract-upgrade-blocked: intent_classifier detected mutation but "
                "current role has no write tools. Keeping PROPOSE_PATCH for turn_id=%s",
                turn_id,
            )
            ledger.anomaly_flags.append(
                {
                    "type": "DELIVERY_CONTRACT_UPGRADE_BLOCKED",
                    "turn_id": turn_id,
                    "reason": "no_write_tools_for_intent",
                    "user_request": latest_user_request,
                }
            )
        else:
            logger.warning(
                "delivery-contract-upgrade: intent_classifier detected mutation but delivery_contract was not "
                "MATERIALIZE_CHANGES. Upgrading for turn_id=%s",
                turn_id,
            )
            ledger.delivery_contract = DeliveryContract(
                mode=DeliveryMode.MATERIALIZE_CHANGES,
                requires_mutation=True,
                requires_verification=ledger.delivery_contract.requires_verification,
                allow_inline_code=False,
                allow_patch_proposal=False,
            )
            requires_mutation_by_contract = True
            ledger.anomaly_flags.append(
                {
                    "type": "DELIVERY_CONTRACT_AUTO_UPGRADED",
                    "turn_id": turn_id,
                    "reason": "intent_classifier_mismatch",
                    "user_request": latest_user_request,
                }
            )

    if (
        decision_kind != TurnDecisionKind.TOOL_BATCH
        and (requires_mutation_by_contract or requires_mutation_by_intent)
        and has_available_write_tool(tool_definitions)
    ):
        # MATERIALIZE_CHANGES 模式下必须阻止 non-tool 决策（Invariant A）
        force_block = ledger.delivery_contract.mode == DeliveryMode.MATERIALIZE_CHANGES
        if guard_mode == "strict" or force_block:
            if force_block and guard_mode == "warn":
                logger.warning(
                    "mutation-contract guard: MATERIALIZE_CHANGES mode forces block despite warn mode. "
                    "turn_id=%s decision_kind=%s",
                    turn_id,
                    decision_kind,
                )
            shadow_engine = build_stream_shadow_engine(
                workspace=resolve_shadow_workspace(context),
                turn_id=turn_id,
            )
            return await retry_tool_batch_after_contract_violation(
                turn_id=turn_id,
                context=context,
                tool_definitions=tool_definitions,
                state_machine=state_machine,
                ledger=ledger,
                stream=False,
                shadow_engine=shadow_engine,
            )
        elif guard_mode == "warn":
            logger.warning(
                "mutation-contract guard (soft): non-tool decision (%s) for mutation request, "
                "but mutation_guard_mode=warn allows passthrough. turn_id=%s",
                decision_kind,
                turn_id,
            )
            ledger.record_mutation_guard_warning(
                reason=(
                    "non_tool_decision_for_mutation_request:"
                    f"{decision_kind.value if hasattr(decision_kind, 'value') else decision_kind}"
                ),
                user_request=latest_user_request,
            )

    return None
