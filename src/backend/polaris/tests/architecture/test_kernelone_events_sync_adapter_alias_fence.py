"""Architecture guards for KernelOne message-bus sync handler adapters."""

from __future__ import annotations

from polaris.kernelone import events
from polaris.kernelone.events import message_bus


def test_sync_handler_adapter_has_no_legacy_public_alias() -> None:
    """Sync handler adaptation must expose only the current explicit name."""
    assert hasattr(message_bus, "SyncMessageHandlerAdapter")
    assert hasattr(events, "SyncMessageHandlerAdapter")
    assert not hasattr(message_bus, "LegacySyncHandlerAdapter")
    assert not hasattr(events, "LegacySyncHandlerAdapter")
