"""Turn-id resolution helpers for RoleExecutionKernel.

Extracted verbatim (behavior-preserving) from ``core.py`` so both the
coordinator (``core.py``) and the turn-execution collaborator
(``turn_execution.py``) can share them without a circular import.

``core.py`` re-exports both names to preserve its original module namespace
(``kernel_core._resolve_transaction_turn_id`` is read via ``getattr`` in tests).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleTurnRequest


def _turn_id_component(value: Any) -> str:
    raw = str(value or "").strip()
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in raw)[:120]


def _resolve_transaction_turn_id(request: RoleTurnRequest, observer_run_id: str) -> str:
    base = _turn_id_component(getattr(request, "run_id", None) or observer_run_id)
    if not base:
        base = uuid.uuid4().hex[:12]
    task_id = _turn_id_component(getattr(request, "task_id", None))
    if not task_id:
        metadata = getattr(request, "metadata", None)
        if isinstance(metadata, dict):
            task_id = _turn_id_component(metadata.get("task_id") or metadata.get("pm_task_id"))
    if task_id and task_id not in base:
        return f"{base}--{task_id}"
    return base
