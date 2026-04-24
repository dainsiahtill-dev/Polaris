"""Tests for polaris.cells.chief_engineer.blueprint.internal.event_bus."""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.cells.chief_engineer.blueprint.internal.event_bus import EventBus


class TestEventBus:
    def test_publish_no_subscribers(self) -> None:
        bus = EventBus()
        bus.publish("test", {"key": "value"})

    def test_subscribe_and_publish(self) -> None:
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe("test", handler)
        bus.publish("test", {"key": "value"})
        handler.assert_called_once()
        assert handler.call_args[0][0] == "test"
        assert handler.call_args[0][1] == {"key": "value"}

    def test_multiple_subscribers(self) -> None:
        bus = EventBus()
        handler1 = MagicMock()
        handler2 = MagicMock()
        bus.subscribe("test", handler1)
        bus.subscribe("test", handler2)
        bus.publish("test", {"key": "value"})
        handler1.assert_called_once()
        handler2.assert_called_once()

    def test_unsubscribe(self) -> None:
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe("test", handler)
        bus.unsubscribe("test", handler)
        bus.publish("test", {"key": "value"})
        handler.assert_not_called()

    def test_clear(self) -> None:
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe("test", handler)
        bus.clear()
        bus.publish("test", {"key": "value"})
        handler.assert_not_called()

    def test_empty_event_type_ignored(self) -> None:
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe("", handler)
        bus.publish("", {"key": "value"})
        handler.assert_not_called()

    def test_isolated_failure(self) -> None:
        bus = EventBus()
        bad_handler = MagicMock(side_effect=RuntimeError("boom"))
        good_handler = MagicMock()
        bus.subscribe("test", bad_handler)
        bus.subscribe("test", good_handler)
        bus.publish("test", {"key": "value"})
        bad_handler.assert_called_once()
        good_handler.assert_called_once()
