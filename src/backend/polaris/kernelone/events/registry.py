"""MessageBus global state management.

This module provides global MessageBus management with test isolation support.
For test isolation, use reset_global_bus() to clear the singleton.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .message_bus import MessageBus

_global_bus: MessageBus | None = None


def set_global_bus(bus: MessageBus) -> None:
    """Set the global message bus."""
    global _global_bus
    _global_bus = bus


def get_global_bus() -> MessageBus | None:
    """Get the global message bus."""
    global _global_bus
    return _global_bus


def reset_global_bus() -> None:
    """Reset the global message bus.

    This function is primarily for test isolation. It clears the singleton
    so tests can inject fresh buses without state pollution.
    """
    global _global_bus
    _global_bus = None
