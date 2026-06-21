"""Context / handoff assembly helpers for RoleExecutionKernel.

Holds the bodies of ``RoleExecutionKernel._build_context_handoff_pack`` and
``RoleExecutionKernel._build_context_request_for_stream`` extracted verbatim
(behavior-preserving) into free functions. The class methods become thin
delegating shims.

FROZEN behavior notes (do NOT change):
- ``build_context_handoff_pack`` maps a TransactionKernel ``handoff_workflow``
  result to the canonical ``ContextHandoffPack``; the field-mapping, the
  ``handoff_{turn_id}_<uuid>`` id shape, and the workspace/run_id fallback chain
  are preserved verbatim.
- ``build_context_request_for_stream`` preserves the BOM-stripping
  ``_normalize_user_text`` nested helper and the duplicate-current-user-message
  filter verbatim, plus the function-local public ``ContextRequest`` import.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.profile.public.service import RoleTurnRequest
from polaris.domain.cognitive_runtime.models import ContextHandoffPack, TurnEnvelope

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel


def build_context_handoff_pack(
    kernel: RoleExecutionKernel,
    turn_result: dict[str, Any],
    role: str,
    request: RoleTurnRequest,
) -> ContextHandoffPack:
    """Map TransactionKernel handoff_workflow result to canonical ContextHandoffPack."""
    workflow_context = turn_result.get("workflow_context") or {}
    recoverable_context = workflow_context.get("recoverable_context") or {}
    decision = recoverable_context.get("decision") or {}
    batch_receipts = recoverable_context.get("batch_receipts") or []
    turn_id = str(turn_result.get("turn_id", ""))
    run_id = str(request.run_id or "").strip() or turn_id

    receipt_refs: list[str] = []
    for receipt in batch_receipts:
        batch_id = str(receipt.get("batch_id", ""))
        if batch_id:
            receipt_refs.append(batch_id)

    turn_envelope = TurnEnvelope(
        turn_id=turn_id,
        session_id=str(request.task_id or "").strip() or None,
        run_id=run_id if run_id else None,
        role=role,
        receipt_ids=tuple(receipt_refs),
    )

    return ContextHandoffPack(
        handoff_id=f"handoff_{turn_id}_{uuid.uuid4().hex[:8]}",
        workspace=str(request.workspace or kernel.workspace or "."),
        created_at=str(int(time.time())),
        session_id=str(request.task_id or "").strip() or turn_id,
        run_id=run_id if run_id else None,
        reason=str(workflow_context.get("handoff_reason", "transaction_kernel_handoff")),
        current_goal=str(decision.get("metadata", {}).get("current_goal", "")),
        run_card=dict(decision.get("metadata", {}).get("run_card", {})),
        context_slice_plan={"workflow_context": workflow_context},
        decision_log=(recoverable_context,),
        receipt_refs=tuple(receipt_refs),
        turn_envelope=turn_envelope,
    )


def build_context_request_for_stream(messages: list[dict[str, Any]], request: RoleTurnRequest) -> Any:
    """Build a minimal ContextRequest for compatibility call_stream providers."""
    from polaris.cells.roles.kernel.public.service import ContextRequest

    def _normalize_user_text(value: Any) -> str:
        return str(value or "").replace("\ufeff", "").strip()

    history: list[tuple[str, str]] = []
    for msg in messages:
        role_label = str(msg.get("role", ""))
        content = str(msg.get("content", ""))
        if role_label in ("user", "assistant", "tool"):
            history.append((role_label, content))

    normalized_current = _normalize_user_text(request.message)
    if normalized_current:
        history = [
            (role_label, content)
            for role_label, content in history
            if not (role_label == "user" and _normalize_user_text(content) == normalized_current)
        ]

    return ContextRequest(
        message=request.message,
        history=tuple(history),
        task_id=request.task_id,
    )
