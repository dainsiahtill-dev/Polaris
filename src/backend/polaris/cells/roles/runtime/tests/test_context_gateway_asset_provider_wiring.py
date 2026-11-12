"""Runtime wiring for role-owned ContextGateway asset providers."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.roles.runtime.tests.engineering_support import (
    isolated_workspace as isolated_workspace,
)


def test_context_gateway_config_is_kernel_public_contract() -> None:
    """Runtime must consume ContextGatewayConfig through the kernel public boundary."""
    from polaris.cells.roles.kernel.public.service import ContextGatewayConfig

    assert ContextGatewayConfig.__name__ == "ContextGatewayConfig"


def test_retired_runtime_kernel_context_asset_readers_stay_closed(
    isolated_workspace: Any,
) -> None:
    """The runtime kernel asset readers have no live subject after the retirement.

    The reader wiring belonged to the legacy role execution kernel, which is
    retired: a new engineering run has exactly one owner, the engineering run
    service. The scenario is replaced by the retirement contract.
    """
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    service = RoleRuntimeService()
    with pytest.raises(RuntimeError, match="legacy_engine_retired"):
        service._get_kernel(str(isolated_workspace))
