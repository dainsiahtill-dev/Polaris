"""Public contracts for `roles.host` cell.

Unified host protocol: HostKind, HostCapabilityProfile, and host-typed
ExecuteRoleSessionCommandV1 extension.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Re-export enums from session.public so callers get a single import point
from polaris.cells.roles.session.public.contracts import (
    AttachmentMode,
    RoleHostKind,
    SessionState,
    SessionType,
)

__all__ = [
    "AttachmentMode",
    "HostCapabilityProfile",
    "HostKind",
    "RoleHostKind",
    "SessionState",
    "SessionType",
]


# Alias for public-facing use
HostKind = RoleHostKind


@dataclass(frozen=True)
class HostCapabilityProfile:
    """Capability profile for a given host kind.

    Describes what features/limits a host environment provides so that
    role runtime can adapt its execution strategy accordingly.
    """

    host_kind: str
    supports_streaming: bool = True
    supports_tool_async: bool = True
    supports_file_write: bool = True
    max_context_tokens: int | None = None
    supports_audit_export: bool = True
    supports_artifact_persistence: bool = True
    enable_session_orchestrator: bool = False  # Enable multi-turn orchestrator for CLI/Director workflows
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "host_kind": self.host_kind,
            "supports_streaming": self.supports_streaming,
            "supports_tool_async": self.supports_tool_async,
            "supports_file_write": self.supports_file_write,
            "max_context_tokens": self.max_context_tokens,
            "supports_audit_export": self.supports_audit_export,
            "supports_artifact_persistence": self.supports_artifact_persistence,
            "enable_session_orchestrator": self.enable_session_orchestrator,
            "metadata": dict(self.metadata),
        }


# ── Default capability profiles per host kind ──────────────────────────────

HOST_KIND_PROFILES: dict[str, HostCapabilityProfile] = {
    RoleHostKind.WORKFLOW.value: HostCapabilityProfile(
        host_kind=RoleHostKind.WORKFLOW.value,
        supports_streaming=True,
        supports_tool_async=True,
        supports_file_write=True,
        max_context_tokens=None,
        supports_audit_export=True,
        supports_artifact_persistence=True,
    ),
    RoleHostKind.ELECTRON_WORKBENCH.value: HostCapabilityProfile(
        host_kind=RoleHostKind.ELECTRON_WORKBENCH.value,
        supports_streaming=True,
        supports_tool_async=True,
        supports_file_write=True,
        max_context_tokens=None,
        supports_audit_export=True,
        supports_artifact_persistence=True,
    ),
    RoleHostKind.TUI.value: HostCapabilityProfile(
        host_kind=RoleHostKind.TUI.value,
        supports_streaming=True,
        supports_tool_async=False,
        supports_file_write=True,
        max_context_tokens=128000,
        supports_audit_export=False,
        supports_artifact_persistence=True,
    ),
    RoleHostKind.CLI.value: HostCapabilityProfile(
        host_kind=RoleHostKind.CLI.value,
        supports_streaming=True,
        supports_tool_async=False,
        supports_file_write=True,
        max_context_tokens=128000,
        supports_audit_export=False,
        supports_artifact_persistence=False,
        enable_session_orchestrator=True,
    ),
    RoleHostKind.API_SERVER.value: HostCapabilityProfile(
        host_kind=RoleHostKind.API_SERVER.value,
        supports_streaming=True,
        supports_tool_async=True,
        supports_file_write=True,
        max_context_tokens=None,
        supports_audit_export=True,
        supports_artifact_persistence=True,
    ),
    RoleHostKind.HEADLESS.value: HostCapabilityProfile(
        host_kind=RoleHostKind.HEADLESS.value,
        supports_streaming=False,
        supports_tool_async=True,
        supports_file_write=True,
        max_context_tokens=None,
        supports_audit_export=False,
        supports_artifact_persistence=True,
    ),
}


def get_capability_profile(host_kind: str) -> HostCapabilityProfile:
    """Get the capability profile for a host kind.

    Args:
        host_kind: Host kind string (e.g. "workflow", "electron_workbench").

    Returns:
        HostCapabilityProfile for the given host kind.
    """
    return HOST_KIND_PROFILES.get(
        host_kind,
        HostCapabilityProfile(host_kind=host_kind),
    )
