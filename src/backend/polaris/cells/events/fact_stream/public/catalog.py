"""Platform-owned FactStream bootstrap enrollment catalog.

This catalog is an enrollment projection, not a domain-event registry. It
contains only stable platform streams that can be known before a backend starts.
Callers that create dynamic stream names must use the explicit maintenance
provision command before ordinary FactStream acquisition.
"""

from __future__ import annotations

from typing import Final

_BOOTSTRAP_STREAMS_V1: Final[tuple[str, ...]] = (
    "execution.control_plane",
    "factory.settlement",
    "resident.cycle.events",
    "roles.kernel.turn_outcomes",
    "task_market.events",
    "task_runtime.execution",
    "taskboard.terminal.events",
)


def fact_stream_bootstrap_streams() -> tuple[str, ...]:
    """Return the immutable static stream enrollment set for one backend startup."""

    return _BOOTSTRAP_STREAMS_V1


__all__ = ["fact_stream_bootstrap_streams"]
