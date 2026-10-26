"""Read-only RoleKernel tool-policy adapter for the permission owner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import Lock
from typing import Any

from polaris.cells.policy.permission.public.contracts import ToolPolicyDecisionPort
from polaris.cells.roles.profile.public.service import (
    RoleProfile,
    RoleProfileRegistry,
    load_core_roles,
    registry,
)

from ..internal.tool_gateway import RoleToolGateway


class RoleKernelToolPolicyAdapter(ToolPolicyDecisionPort):
    """Reuse actual gateway checks without exposing any execution capability."""

    def __init__(
        self,
        workspace: str,
        *,
        profile_registry: RoleProfileRegistry | None = None,
        gateway_factory: Callable[[RoleProfile, str], RoleToolGateway] | None = None,
    ) -> None:
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError("tool policy adapter requires an explicit workspace")
        self._workspace = workspace
        self._profile_registry = profile_registry
        self._gateway_factory = gateway_factory or RoleToolGateway

    @property
    def workspace(self) -> str:
        return self._workspace

    def _profiles(self) -> RoleProfileRegistry:
        if self._profile_registry is not None:
            return self._profile_registry
        load_core_roles()
        return registry

    def role_permissions(self) -> Mapping[str, frozenset[str]]:
        """Project the existing role matrix; exact tool checks remain separate."""
        result: dict[str, frozenset[str]] = {}
        for role_id, profile in self._profiles().get_all_profiles().items():
            gateway = self._gateway_factory(profile, self.workspace)
            permissions = {f"tool:execute:{name}" for name in gateway.get_available_tools()}
            if profile.tool_policy.allow_code_write:
                permissions.add("file:write:**/*")
            if profile.tool_policy.allow_command_execution:
                permissions.add("api:execute:execute_command")
            if profile.tool_policy.allow_file_delete:
                permissions.add("file:delete:**/*")
            result[role_id] = frozenset(permissions)
        return result

    def check_tool_permission(
        self, role_id: str, tool_name: str, tool_args: dict[str, Any] | None = None
    ) -> tuple[bool, str]:
        profile = self._profiles().get_profile(role_id)
        if profile is None:
            return False, "tool_policy_unknown_role"
        # The gateway captures and normalizes the current ToolSpec snapshot.
        return self._gateway_factory(profile, self.workspace).check_tool_permission(tool_name, tool_args)


_ADAPTERS: dict[str, RoleKernelToolPolicyAdapter] = {}
_ADAPTER_LOCK = Lock()


def get_permission_tool_policy_adapter(workspace: str) -> RoleKernelToolPolicyAdapter:
    """Reuse one workspace binding; profiles and tool snapshots remain live."""
    with _ADAPTER_LOCK:
        adapter = _ADAPTERS.get(workspace)
        if adapter is None:
            adapter = RoleKernelToolPolicyAdapter(workspace)
            _ADAPTERS[workspace] = adapter
        return adapter


__all__ = ["RoleKernelToolPolicyAdapter", "get_permission_tool_policy_adapter"]
