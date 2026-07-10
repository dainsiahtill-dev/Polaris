"""Turn-id resolution helpers for RoleExecutionKernel.

Extracted verbatim (behavior-preserving) from ``core.py`` so both the
coordinator (``core.py``) and the transaction-turn executor
(``transaction_turn_executor.py``) can share them without a circular import.

Callers import these helpers from this module directly.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleTurnRequest


def _turn_id_component(value: Any) -> str:
    raw = str(value or "").strip()
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in raw)[:120]


def _start_transaction_invocation(request: RoleTurnRequest) -> str:
    """Assign a unique identity to one logical RoleKernel invocation.

    A logical turn may execute more than one transaction attempt when output
    validation requests a retry. The invocation id remains stable across those
    attempts while each attempt receives its own terminal fact identity.
    """

    invocation_id = uuid.uuid4().hex[:12]
    metadata = getattr(request, "metadata", None)
    metadata_payload = dict(metadata) if isinstance(metadata, dict) else {}
    metadata_payload["transaction_invocation_id"] = invocation_id
    metadata_payload.pop("transaction_attempt", None)
    metadata_payload.pop("transaction_attempt_id", None)
    request.metadata = metadata_payload
    return invocation_id


def _bind_transaction_attempt(
    request: RoleTurnRequest,
    *,
    invocation_id: str,
    attempt: int,
) -> str:
    """Bind one transaction attempt to a request and return its identity."""

    normalized_invocation = _turn_id_component(invocation_id)
    if not normalized_invocation:
        raise ValueError("transaction invocation id must be non-empty")
    normalized_attempt = max(0, int(attempt))
    attempt_id = f"{normalized_invocation}-{normalized_attempt}"
    metadata = getattr(request, "metadata", None)
    metadata_payload = dict(metadata) if isinstance(metadata, dict) else {}
    metadata_payload["transaction_invocation_id"] = normalized_invocation
    metadata_payload["transaction_attempt"] = normalized_attempt
    metadata_payload["transaction_attempt_id"] = attempt_id
    request.metadata = metadata_payload
    return attempt_id


def _resolve_transaction_turn_id(request: RoleTurnRequest, observer_run_id: str) -> str:
    base = _turn_id_component(getattr(request, "run_id", None) or observer_run_id)
    if not base:
        base = uuid.uuid4().hex[:12]
    task_id = _turn_id_component(getattr(request, "task_id", None))
    if not task_id:
        metadata = getattr(request, "metadata", None)
        if isinstance(metadata, dict):
            task_id = _turn_id_component(metadata.get("task_id") or metadata.get("pm_task_id"))
    logical_turn_id = f"{base}--{task_id}" if task_id and task_id not in base else base
    metadata = getattr(request, "metadata", None)
    attempt_id = _turn_id_component(metadata.get("transaction_attempt_id")) if isinstance(metadata, dict) else ""
    if attempt_id:
        return f"{logical_turn_id}--attempt-{attempt_id}"
    return logical_turn_id
