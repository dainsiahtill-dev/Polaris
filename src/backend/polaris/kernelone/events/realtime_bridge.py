from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class LLMRealtimeEvent:
    """Kernel-level contract for publishing LLM lifecycle events."""

    workspace: str
    run_id: str
    role: str
    event_type: str
    source: str = "system"
    timestamp: str = ""
    iteration: int = 0
    data: dict[str, Any] = field(default_factory=dict)


class LLMRealtimeEventBridge(Protocol):
    """Port for bridging LLM lifecycle events into the realtime plane."""

    def publish(self, event: LLMRealtimeEvent) -> None:
        """Publish one realtime LLM event."""


_bridge_lock = Lock()
_default_bridge: LLMRealtimeEventBridge | None = None


def set_llm_realtime_bridge(bridge: LLMRealtimeEventBridge | None) -> None:
    """Inject the default LLM realtime bridge."""
    global _default_bridge
    with _bridge_lock:
        _default_bridge = bridge


def get_llm_realtime_bridge() -> LLMRealtimeEventBridge | None:
    """Return the injected LLM realtime bridge."""
    with _bridge_lock:
        return _default_bridge


def publish_llm_realtime_event(event: LLMRealtimeEvent) -> None:
    """Best-effort publish through the injected bridge."""
    bridge = get_llm_realtime_bridge()
    if bridge is None:
        return
    bridge.publish(event)


__all__ = [
    "LLMRealtimeEvent",
    "LLMRealtimeEventBridge",
    "get_llm_realtime_bridge",
    "publish_llm_realtime_event",
    "set_llm_realtime_bridge",
]
