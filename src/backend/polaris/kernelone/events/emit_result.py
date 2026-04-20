"""EmitResult for dual-write event publishing operations.

This module provides the EmitResult dataclass returned by TypedEventBusAdapter.emit_to_both()
when handling partial failures programmatically rather than raising exceptions.

Architecture:
    EmitResult is part of the event subsystem and belongs here rather than
    in exceptions.py (which contains error/exception classes only).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EmitResult:
    """Result of a dual-write emit operation.

    This dataclass is returned by emit_to_both() when you want to handle
    partial failures programmatically rather than raising an exception.

    Attributes:
        registry_success: Whether EventRegistry emit succeeded.
        message_bus_success: Whether MessageBus emit succeeded (None if dual_write disabled).
        registry_error: Exception from EventRegistry emit, if any.
        message_bus_error: Exception from MessageBus emit, if any.
        event_name: Name of the event that was emitted.

    Example:
        result = await adapter.emit_to_both(event)
        if not result.registry_success:
            logger.error(f"Registry failed: {result.registry_error}")
        if not result.message_bus_success:
            logger.error(f"MessageBus failed: {result.message_bus_error}")
    """

    registry_success: bool = False
    message_bus_success: bool | None = None
    registry_error: Exception | None = None
    message_bus_error: Exception | None = None
    event_name: str = ""

    @property
    def is_full_success(self) -> bool:
        """True if all enabled sides succeeded."""
        return self.registry_success and (self.message_bus_success is None or self.message_bus_success)

    @property
    def failed_sides(self) -> list[str]:
        """List of sides that failed."""
        sides = []
        if not self.registry_success:
            sides.append("registry")
        if self.message_bus_success is False:
            sides.append("message_bus")
        return sides

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "registry_success": self.registry_success,
            "message_bus_success": self.message_bus_success,
            "registry_error": str(self.registry_error) if self.registry_error else None,
            "message_bus_error": str(self.message_bus_error) if self.message_bus_error else None,
            "event_name": self.event_name,
            "is_full_success": self.is_full_success,
            "failed_sides": self.failed_sides,
        }


__all__ = ["EmitResult"]
