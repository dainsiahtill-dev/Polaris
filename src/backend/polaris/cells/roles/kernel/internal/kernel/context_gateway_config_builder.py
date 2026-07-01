"""ContextGatewayConfig factory adapter for role-kernel turns."""

from __future__ import annotations

import logging
from collections.abc import Callable

from polaris.cells.roles.kernel.internal.context_gateway import ContextGatewayConfig
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest

logger = logging.getLogger(__name__)

ContextGatewayConfigFactory = Callable[[str, RoleProfile, RoleTurnRequest], ContextGatewayConfig | None]


def build_context_gateway_config(
    factory: ContextGatewayConfigFactory | None,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
) -> ContextGatewayConfig | None:
    """Resolve ContextGatewayConfig through the injected runtime factory."""
    if factory is None:
        return None
    try:
        return factory(role, profile, request)
    except Exception:  # noqa: BLE001 - context asset providers must degrade to baseline context
        logger.debug("ContextGatewayConfig factory failed", exc_info=True)
        return None


__all__ = ["ContextGatewayConfigFactory", "build_context_gateway_config"]
