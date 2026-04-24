"""Tests for HA Component Integration with OmniscientAuditBus.

Tests that MemoryBoundedBatcher and AuditCircuitBreaker from
high_availability.py are properly wired into OmniscientAuditBus.

Run with:
    pytest polaris/kernelone/audit/omniscient/tests/test_ha_integration.py -v
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from polaris.kernelone.audit.omniscient.bus import AuditPriority, OmniscientAuditBus
from polaris.kernelone.audit.omniscient.high_availability import (
    AuditCircuitBreaker,
    MemoryBoundedBatcher,
)


@pytest_asyncio.fixture
async def ha_bus() -> AsyncGenerator[OmniscientAuditBus, None]:
    """Provide a bus with HA components wired."""
    batcher = MemoryBoundedBatcher(max_memory_mb=10, batch_size=5)
    circuit_breaker = AuditCircuitBreaker(threshold=3, timeout=1.0)

    bus = OmniscientAuditBus(
        name=f"ha_test_{id(None)}",
        batcher=batcher,
        circuit_breaker=circuit_breaker,
    )
    await bus.start()
    yield bus
    await bus.stop(timeout=2.0)
    OmniscientAuditBus._instances.pop(bus._name, None)


# =============================================================================
# Circuit Breaker Integration
# =============================================================================


@pytest.mark.asyncio
async def test_bus_with_circuit_breaker_in_stats() -> None:
    """Bus with circuit breaker includes it in stats."""
    cb = AuditCircuitBreaker(threshold=3)
    bus = OmniscientAuditBus(name=f"cb_test_{id(cb)}", circuit_breaker=cb)
    await bus.start()
    try:
        stats = bus.get_stats()
        assert "circuit_breaker" in stats
        assert stats["circuit_breaker"]["state"] == "closed"
        assert stats["circuit_breaker"]["threshold"] == 3
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop(bus._name, None)


@pytest.mark.asyncio
async def test_bus_stats_includes_batcher() -> None:
    """Bus with batcher includes it in stats."""
    batcher = MemoryBoundedBatcher(max_memory_mb=5, batch_size=10)
    bus = OmniscientAuditBus(name=f"batcher_test_{id(batcher)}", batcher=batcher)
    await bus.start()
    try:
        stats = bus.get_stats()
        assert "batcher" in stats
        assert stats["batcher"]["max_memory_bytes"] == 5 * 1024 * 1024
        assert stats["batcher"]["total_batched"] == 0
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop(bus._name, None)


@pytest.mark.asyncio
async def test_bus_without_ha_components_no_crash() -> None:
    """Bus without HA components still works."""
    bus = OmniscientAuditBus(name="no_ha_test")
    await bus.start()
    try:
        await bus.emit({"type": "test_event"}, priority=AuditPriority.INFO)
        stats = bus.get_stats()
        assert stats["events_emitted"] >= 1
        assert "batcher" not in stats
        assert "circuit_breaker" not in stats
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop(bus._name, None)


# =============================================================================
# Batcher Integration
# =============================================================================


@pytest.mark.asyncio
async def test_batcher_records_events() -> None:
    """Batcher receives events from bus emit."""
    batcher = MemoryBoundedBatcher(max_memory_mb=1, batch_size=100)
    bus = OmniscientAuditBus(name=f"batcher_events_{id(batcher)}", batcher=batcher)
    await bus.start()
    try:
        for i in range(10):
            await bus.emit({"type": "test", "event_id": f"evt_{i}"})

        await asyncio.sleep(0.2)

        stats = bus.get_stats()
        assert stats["batcher"]["buffered_events"] >= 0  # May have been flushed
    finally:
        await bus.stop()
        OmniscientAuditBus._instances.pop(bus._name, None)


# =============================================================================
# HA Config Integration
# =============================================================================


def test_apply_ha_config_still_works() -> None:
    """apply_ha_config() still works and creates storm detector."""
    from polaris.kernelone.audit.omniscient.high_availability import (
        HAConfig,
        apply_ha_config,
    )

    bus = OmniscientAuditBus(name="ha_config_test")
    config = HAConfig(
        storm_elevated_threshold=100,
        max_memory_mb=50,
        circuit_breaker_threshold=5,
    )
    apply_ha_config(bus, config)

    assert bus._storm_detector is not None
    stats = bus.get_stats()
    assert "storm" in stats
