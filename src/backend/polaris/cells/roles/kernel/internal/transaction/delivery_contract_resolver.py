"""Phase-1b delivery-contract resolution for the turn kernel.

UTF-8 编码验证: 本文所有文本使用 UTF-8。

Extracted verbatim (behavior-preserving) from
``TurnTransactionController._execute_turn`` Phase-1b per the REMAINING_06
decomposition blueprint (step 2). Resolves the per-turn ``DeliveryContract``
(hybrid SLM/regex resolution + explicit-marker enforcement + multi-turn
materialize inheritance + no-write-tools downgrade) and records the matching
anomaly flags on the ledger.

The two role-policy helpers (``resolve_delivery_mode_hybrid`` and
``inherit_materialize_from_history``) are passed in as callables so that
monkeypatching them on the facade instance still penetrates this module.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.contract_guards import has_available_write_tool
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_intent_resolver import (
    enforce_explicit_materialize_delivery_marker,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import extract_latest_user_message

logger = logging.getLogger(__name__)

_NO_WRITE_STRUCTURED_ROLES = frozenset({"pm", "chief_engineer", "chiefengineer", "architect", "qa"})

_STRUCTURED_OUTPUT_MARKERS = (
    "output contract",
    "return exactly one json object",
    "required top-level keys",
    "只输出 json",
    "输出 json",
    "禁止输出 [tool_call]",
    "禁止输出 <tool_call>",
    "禁止输出工具调用",
    "do not emit tool calls",
    "do not emit tool call",
    "do not emit tool_calls",
    "pm 合同",
    "任务合同",
    "chief engineer output contract",
    "construction_plan",
    "scope_for_apply",
    "risk_flags",
)


def _is_no_write_structured_role(role_id: str) -> bool:
    return role_id.strip().lower().replace("-", "_") in _NO_WRITE_STRUCTURED_ROLES


def _looks_like_structured_output_contract(message: str) -> bool:
    lowered = message.strip().lower()
    return any(marker in lowered for marker in _STRUCTURED_OUTPUT_MARKERS)


def _structured_no_write_contract() -> DeliveryContract:
    return DeliveryContract(
        mode=DeliveryMode.PROPOSE_PATCH,
        requires_mutation=False,
        requires_verification=False,
        allow_inline_code=True,
        allow_patch_proposal=True,
    )


async def resolve_turn_delivery_contract(
    *,
    turn_id: str,
    context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
    ledger: TurnLedger,
    resolve_delivery_mode_hybrid: Callable[[str], Awaitable[DeliveryContract]],
    inherit_materialize_from_history: Callable[[list[dict[str, Any]], str], DeliveryContract | None],
    role_id: str = "",
) -> DeliveryContract:
    """Resolve and record the Phase-1b delivery contract for the turn.

    Mirrors the original inline Phase-1b logic exactly, including the
    ``KERNELONE_DELIVERY_MODE_TRACE`` trace, the no-user-turn anomaly flag,
    explicit-marker override, multi-turn materialize inheritance, and the
    no-write-tools downgrade. Returns the resolved contract; the caller still
    performs ``ledger.set_delivery_contract`` and target-files detection.
    """
    latest_user_request = extract_latest_user_message(context)
    if os.getenv("KERNELONE_DELIVERY_MODE_TRACE") == "1":
        context_has_materialize_marker = any(
            isinstance(message, Mapping)
            and str(message.get("role") or "").strip().lower() == "user"
            and (
                "[mode:materialize]" in str(message.get("content") or "").lower()
                or "[mode:materialize_changes]" in str(message.get("content") or "").lower()
            )
            for message in context
        )
        logger.warning(
            "delivery-mode-controller-trace: turn_id=%s latest_marker=%s context_marker=%s latest_user_preview=%r",
            turn_id,
            "[mode:materialize]" in latest_user_request.lower()
            or "[mode:materialize_changes]" in latest_user_request.lower(),
            context_has_materialize_marker,
            latest_user_request[:160],
        )
    if not latest_user_request and context:
        # A user-turn-free context resolves to the ANALYZE_ONLY default and
        # silently neuters mutation turns (write tools filtered). The
        # projection layer must always preserve the current instruction
        # (see CompressionEngine.emergency_truncate); flag loudly if not.
        logger.warning(
            "delivery-contract-no-user-turn: turn_id=%s context_roles=%s — "
            "defaulting to ANALYZE_ONLY with no user intent available",
            turn_id,
            [str(m.get("role") or "") for m in context if isinstance(m, Mapping)][:12],
        )
        ledger.anomaly_flags.append(
            {
                "type": "DELIVERY_CONTRACT_NO_USER_TURN",
                "turn_id": turn_id,
                "message_count": len(context),
            }
        )
    if (
        _is_no_write_structured_role(role_id)
        and not has_available_write_tool(tool_definitions)
        and _looks_like_structured_output_contract(latest_user_request)
    ):
        logger.warning(
            "delivery-contract-role-no-write-structured: turn_id=%s role=%s latest_msg=%r "
            "forcing PROPOSE_PATCH before mutation intent resolution",
            turn_id,
            role_id,
            latest_user_request[:160],
        )
        ledger.anomaly_flags.append(
            {
                "type": "DELIVERY_CONTRACT_ROLE_NO_WRITE_STRUCTURED_OUTPUT",
                "turn_id": turn_id,
                "role_id": role_id,
                "reason": "structured_output_role_has_no_write_tools",
                "latest_request": latest_user_request,
            }
        )
        return _structured_no_write_contract()

    delivery_contract = await resolve_delivery_mode_hybrid(latest_user_request)
    enforced_contract = enforce_explicit_materialize_delivery_marker(latest_user_request, delivery_contract)
    if enforced_contract is not delivery_contract:
        logger.warning(
            "delivery-contract-explicit-marker-overrode: turn_id=%s previous_mode=%s latest_msg=%r",
            turn_id,
            delivery_contract.mode.value,
            latest_user_request[:160],
        )
        ledger.anomaly_flags.append(
            {
                "type": "DELIVERY_CONTRACT_EXPLICIT_MARKER_OVERRIDDEN",
                "turn_id": turn_id,
                "previous_mode": delivery_contract.mode.value,
                "latest_request": latest_user_request,
            }
        )
        delivery_contract = enforced_contract

    # 多轮对话保护：如果最新消息丢失 mutation 意图（如"继续""开始吧"），
    # 但历史消息中最近存在 MATERIALIZE_CHANGES 意图，则继承该意图
    if delivery_contract.mode != DeliveryMode.MATERIALIZE_CHANGES:
        inherited = inherit_materialize_from_history(context, latest_user_request)
        if inherited is not None:
            logger.warning(
                "delivery-contract-inherited: turn_id=%s latest_msg=%r "
                "inherited MATERIALIZE_CHANGES from historical user message",
                turn_id,
                latest_user_request,
            )
            delivery_contract = inherited
            ledger.anomaly_flags.append(
                {
                    "type": "DELIVERY_CONTRACT_INHERITED",
                    "turn_id": turn_id,
                    "reason": "latest_message_lost_mutation_intent",
                    "latest_request": latest_user_request,
                }
            )

    if delivery_contract.mode == DeliveryMode.MATERIALIZE_CHANGES and not has_available_write_tool(tool_definitions):
        logger.warning(
            "delivery-contract-downgraded-no-write-tools: turn_id=%s latest_msg=%r "
            "downgrading MATERIALIZE_CHANGES -> PROPOSE_PATCH",
            turn_id,
            latest_user_request,
        )
        delivery_contract = DeliveryContract(
            mode=DeliveryMode.PROPOSE_PATCH,
            requires_mutation=False,
            requires_verification=delivery_contract.requires_verification,
            allow_inline_code=True,
            allow_patch_proposal=True,
            enrichment=delivery_contract.enrichment,
        )
        ledger.anomaly_flags.append(
            {
                "type": "DELIVERY_CONTRACT_DOWNGRADED_NO_WRITE_TOOLS",
                "turn_id": turn_id,
                "reason": "no_write_tools_exposed_for_current_role",
                "latest_request": latest_user_request,
            }
        )

    return delivery_contract
