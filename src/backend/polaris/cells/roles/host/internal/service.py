"""Internal service for `roles.host` cell.

Provides the unified host adapter that resolves host_kind to
capability profiles and adapts role execution accordingly.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.host.public.contracts import (
    HostCapabilityProfile,
    get_capability_profile,
)

__all__ = ["UnifiedHostAdapter"]


class UnifiedHostAdapter:
    """Unified host adapter.

    Resolves host_kind strings to HostCapabilityProfile and provides
    host-aware execution hints for role runtime.

    Usage:
        adapter = UnifiedHostAdapter(host_kind="workflow")
        profile = adapter.get_profile()
        if not profile.supports_streaming:
            # disable streaming
    """

    def __init__(self, host_kind: str) -> None:
        self._host_kind = host_kind
        self._profile: HostCapabilityProfile | None = None

    @property
    def host_kind(self) -> str:
        """The host kind this adapter is configured for."""
        return self._host_kind

    def get_profile(self) -> HostCapabilityProfile:
        """Get the capability profile for this host kind."""
        if self._profile is None:
            self._profile = get_capability_profile(self._host_kind)
        return self._profile

    def supports_streaming(self) -> bool:
        """Whether this host supports SSE streaming."""
        return self.get_profile().supports_streaming

    def supports_tool_async(self) -> bool:
        """Whether this host supports async tool execution."""
        return self.get_profile().supports_tool_async

    def supports_file_write(self) -> bool:
        """Whether this host supports direct file system writes."""
        return self.get_profile().supports_file_write

    def get_execution_hints(self) -> dict[str, Any]:
        """Get execution hints for role runtime based on host capabilities.

        Returns:
            dict with execution hints (streaming, async_tools, etc.)
        """
        profile = self.get_profile()
        return {
            "streaming_enabled": profile.supports_streaming,
            "async_tools_enabled": profile.supports_tool_async,
            "file_write_enabled": profile.supports_file_write,
            "max_context_tokens": profile.max_context_tokens,
            "audit_export_enabled": profile.supports_audit_export,
            "artifact_persistence_enabled": profile.supports_artifact_persistence,
        }
