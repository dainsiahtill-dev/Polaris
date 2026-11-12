"""The persistent Director session over HTTP stays retired.

The canonical Director session over HTTP was driven by the legacy role
execution kernel: one turn chain planned, wrote, repaired and verified.
That engine is retired. A new engineering run has exactly one owner, the
engineering run service, so the legacy write entry has to stay closed: a
stale caller must not reach the retired kernel, and the refusal has to
name the owner instead of failing somewhere deeper.

The architecture gate in
``polaris/tests/architecture/test_engineering_no_legacy_admission.py``
covers the same contract for the whole public facade. This file keeps the
kernel-side scenarios honest, so the retired session entry cannot come
back unnoticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_retired_director_session_entry_refuses_and_names_the_owner(tmp_path: Path) -> None:
    from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    service = RoleRuntimeService()
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="retired-session",
        workspace=str(tmp_path),
        user_message="do not execute",
    )

    with pytest.raises(RuntimeError) as caught:
        await service.execute_role_session(command)

    message = str(caught.value)
    assert "legacy_engine_retired" in message
    assert "engineering run service" in message


@pytest.mark.asyncio
async def test_retired_director_session_refusal_has_no_workspace_or_runtime_effect(
    tmp_path: Path,
) -> None:
    from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    service = RoleRuntimeService()
    command = ExecuteRoleSessionCommandV1(
        role="director",
        session_id="retired-session",
        workspace=str(tmp_path),
        user_message="do not execute",
    )

    with pytest.raises(RuntimeError, match="legacy_engine_retired"):
        await service.execute_role_session(command)

    # The retired entry refuses before it bootstraps a workspace or a runtime.
    assert not list(tmp_path.iterdir())


def test_engineering_run_service_is_the_single_owner_of_a_new_run() -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    assert EngineeringRunService.__module__ == ("polaris.cells.roles.runtime.public.engineering_service")
    # The owner is the only entry that may start, observe and cancel a run.
    for entry in ("start", "wait", "cancel"):
        assert callable(getattr(EngineeringRunService, entry, None))
