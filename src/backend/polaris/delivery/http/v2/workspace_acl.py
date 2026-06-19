"""Advisory workspace ACL helpers shared by HTTP v2 routers.

Best-effort scoping on top of ``require_auth``.  When a request carries
the ``X-ContextOS-Workspace`` header, that workspace must match the
server-bound active workspace — otherwise we deny with a structured 403.
Without the header we keep the previous behaviour (single-tenant desktop
flow).  This is NOT a security boundary — there is no principal-to-workspace
mapping yet — but it stops a confused-deputy caller from accidentally
reading a different workspace's snapshots.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from polaris.delivery.http.workspace import (
    requested_or_active_workspace,
    workspace_values_match,
)

from ._shared import StructuredHTTPException

WORKSPACE_HEADER: str = "X-ContextOS-Workspace"


def check_advisory_workspace_acl(
    *,
    request: Request,
    settings: Any,
    code: str,
    message: str,
) -> str:
    """Validate the X-ContextOS-Workspace header against the active workspace.

    Returns the resolved active workspace path (useful for downstream
    lookups that already needed the value).  Raises
    :class:`StructuredHTTPException` with status 403 when the caller
    explicitly names a different workspace via the header.

    Args:
        request: The incoming FastAPI request.
        settings: The app state settings object.
        code: Structured error code to surface on denial.
        message: Human-readable message attached to the error.

    Returns:
        The resolved active workspace text (string).
    """
    header_value = request.headers.get(WORKSPACE_HEADER)
    if not header_value:
        # Single-tenant desktop UX: no header → no ACL → no extra round-trip.
        active = requested_or_active_workspace(settings, "")
        return active or "."

    requested = requested_or_active_workspace(settings, header_value)
    active = requested_or_active_workspace(settings, "")
    if requested and active and not workspace_values_match(requested, active):
        raise StructuredHTTPException(
            status_code=403,
            code=code,
            message=message,
            details={
                "requested_workspace": requested,
                "active_workspace": active,
            },
        )
    return active or "."


__all__ = [
    "WORKSPACE_HEADER",
    "check_advisory_workspace_acl",
]
