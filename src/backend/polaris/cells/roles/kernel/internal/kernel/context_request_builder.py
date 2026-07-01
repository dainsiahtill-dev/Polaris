"""Context request projection helpers for role-kernel turns."""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
from polaris.cells.roles.profile.public.service import RoleTurnRequest


def build_context_request(request: RoleTurnRequest) -> ContextRequest:
    """Project a role turn request into the ContextOS gateway request.

    Args:
        request: Canonical role turn request.

    Returns:
        Context request with immutable history and copied override metadata.
    """
    context_override: dict[str, Any] = (
        dict(request.context_override) if isinstance(request.context_override, dict) else {}
    )
    context_os_snapshot = context_override.get("context_os_snapshot") if context_override else None
    return ContextRequest(
        message=request.message,
        history=tuple(request.history) if request.history else (),
        task_id=request.task_id,
        context_os_snapshot=context_os_snapshot,
        context_override=context_override or None,
    )


__all__ = ["build_context_request"]
