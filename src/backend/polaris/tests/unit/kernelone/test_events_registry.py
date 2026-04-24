"""Tests for polaris.kernelone.events.registry."""

from __future__ import annotations

from polaris.kernelone.events.registry import get_global_bus, reset_global_bus, set_global_bus


class TestGlobalBus:
    def test_get_default_none(self) -> None:
        reset_global_bus()
        assert get_global_bus() is None

    def test_set_and_get(self) -> None:
        fake_bus = object()
        set_global_bus(fake_bus)  # type: ignore[arg-type]
        assert get_global_bus() is fake_bus

    def test_reset(self) -> None:
        fake_bus = object()
        set_global_bus(fake_bus)  # type: ignore[arg-type]
        reset_global_bus()
        assert get_global_bus() is None
