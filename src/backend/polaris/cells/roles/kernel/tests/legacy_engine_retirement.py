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
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    ExecuteRoleTaskCommandV1,
)
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


async def assert_legacy_task_execution_entry_is_closed(
    *,
    role: str = "director",
    task_id: str = "retired-task",
    workspace: str = "/tmp/retired-role-task",
    objective: str = "do not execute",
) -> None:
    """The retired task execution entry must refuse and name the current owner.

    A TaskRuntime execution attempt is the authority of the task execution
    entry, so the retired entry has to refuse before it reads the attempt,
    prepares a kernel or records any effect.
    """
    service = RoleRuntimeService()
    command = ExecuteRoleTaskCommandV1(
        role=role,
        task_id=task_id,
        workspace=workspace,
        objective=objective,
    )

    with pytest.raises(RuntimeError) as caught:
        await service.execute_role_task(command)

    message = str(caught.value)
    assert "legacy_engine_retired" in message
    assert "engineering run service" in message


def assert_legacy_kernel_entry_is_closed(*, workspace: str = "/tmp/retired-role-kernel") -> None:
    """The retired kernel composition entry must refuse and name the current owner.

    The kernel is the retired role execution kernel. Composing it is not a
    live operation any more, so the entry refuses instead of building a
    kernel that no run owns.
    """
    service = RoleRuntimeService()

    with pytest.raises(RuntimeError) as caught:
        service._get_kernel(workspace)

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
