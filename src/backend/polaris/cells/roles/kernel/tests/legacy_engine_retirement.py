"""Shared assertions for the retired legacy role-session write entry.

The legacy role execution kernel was retired. A new engineering run has
exactly one owner, the engineering run service, and the legacy write entry
has to stay closed: a stale caller must not reach the retired kernel
again, and the refusal has to name the current owner.

The modules that used to drive the retired session assert the retirement
here instead of pretending the retired path still works.
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService


async def assert_legacy_session_entry_is_closed(
    *,
    role: str = "director",
    session_id: str = "retired-session",
    workspace: str = "/tmp/retired-role-session",
    user_message: str = "do not execute",
) -> None:
    """The retired session entry must refuse and name the current owner."""
    service = RoleRuntimeService()
    command = ExecuteRoleSessionCommandV1(
        role=role,
        session_id=session_id,
        workspace=workspace,
        user_message=user_message,
    )

    with pytest.raises(RuntimeError) as caught:
        await service.execute_role_session(command)

    message = str(caught.value)
    assert "legacy_engine_retired" in message
    assert "engineering run service" in message


def legacy_retirement_reason() -> str:
    """Explain why a legacy scenario no longer has a live subject."""
    return (
        "The legacy role execution kernel is retired. The scenario it drove has no live "
        "subject: the engineering run service is the single owner of a new run, and the "
        "legacy write entry refuses instead of executing."
    )
