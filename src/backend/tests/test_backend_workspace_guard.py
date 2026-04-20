from __future__ import annotations

import pytest
from polaris.bootstrap import BackendBootstrapper
from polaris.bootstrap.contracts.backend_launch import BackendLaunchRequest
from polaris.cells.policy.workspace_guard.service import get_meta_project_root


@pytest.mark.asyncio
async def test_bootstrap_rejects_meta_project_workspace_without_self_upgrade() -> None:
    bootstrapper = BackendBootstrapper()
    request = BackendLaunchRequest(
        workspace=get_meta_project_root(),
        explicit_workspace=True,
    )

    result = await bootstrapper.bootstrap(request)

    assert not result.is_success()
    assert "self_upgrade_mode" in result.get_error()

