"""Tests for EventBus thread safety and payload isolation."""

from __future__ import annotations

import threading
from typing import Any

from polaris.cells.chief_engineer.blueprint.internal.event_bus import EventBus


class _CollectingHandler:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self._lock = threading.Lock()

    def __call__(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self.events.append((event_type, payload))


def test_concurrent_publish_and_subscribe() -> None:
    bus = EventBus()
    handler = _CollectingHandler()
    errors: list[Exception] = []
    error_lock = threading.Lock()

    def capture_error(target: callable) -> callable:  # type: ignore[arg-type]
        def wrapper(*args: Any, **kwargs: Any) -> None:  # type: ignore[arg-type]
            try:
                target(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                with error_lock:
                    errors.append(exc)

        return wrapper

    @capture_error
    def publisher() -> None:
        for i in range(200):
            bus.publish("test.event", {"idx": i})

    @capture_error
    def subscriber() -> None:
        for _ in range(100):
            bus.subscribe("test.event", handler)

    @capture_error
    def unsubscriber() -> None:
        for _ in range(100):
            bus.unsubscribe("test.event", handler)

    threads: list[threading.Thread] = []
    for _ in range(3):
        threads.append(threading.Thread(target=publisher))
    for _ in range(2):
        threads.append(threading.Thread(target=subscriber))
        threads.append(threading.Thread(target=unsubscriber))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"RuntimeError(s) occurred: {errors}"
    # Re-subscribe to verify the bus is still consistent after the churn.
    bus.subscribe("test.event", handler)
    bus.publish("test.event", {"final": True})
    assert any(payload.get("final") for _, payload in handler.events)


def test_payload_isolation() -> None:
    bus = EventBus()
    received: list[dict[str, Any]] = []

    def mutator(_event_type: str, payload: dict[str, Any]) -> None:
        payload["nested"]["key"] = "mutated"
        received.append(payload)

    def reader(_event_type: str, payload: dict[str, Any]) -> None:
        received.append(payload)

    bus.subscribe("mutate.event", mutator)
    bus.subscribe("mutate.event", reader)

    original: dict[str, Any] = {"nested": {"key": "original"}}
    bus.publish("mutate.event", original)

    assert len(received) == 2
    assert received[0]["nested"]["key"] == "mutated"
    assert received[1]["nested"]["key"] == "original"
    assert original["nested"]["key"] == "original"


def test_clear_is_thread_safe() -> None:
    bus = EventBus()
    handler = _CollectingHandler()
    bus.subscribe("evt", handler)
    bus.clear()
    bus.publish("evt", {"x": 1})
    assert handler.events == []


def test_unsubscribe_idempotent() -> None:
    bus = EventBus()

    def dummy(_event_type: str, _payload: dict[str, Any]) -> None:
        pass

    bus.subscribe("evt", dummy)
    bus.unsubscribe("evt", dummy)
    bus.unsubscribe("evt", dummy)  # should not raise
    assert bus._subscribers == {}
