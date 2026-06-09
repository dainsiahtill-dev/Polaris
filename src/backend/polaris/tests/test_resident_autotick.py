"""Tests for the opt-in Resident auto-tick background driver.

Covers the delivery-layer scheduler in
``polaris.delivery.http.resident_autotick``: env gating, interval resolution
(parse + floor clamp), single-tick error isolation, and task lifecycle.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from polaris.delivery.http import resident_autotick as autotick

# -- env gating -------------------------------------------------------------


def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(autotick.ENABLE_ENV, raising=False)
    assert autotick.is_autotick_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_enabled_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(autotick.ENABLE_ENV, value)
    assert autotick.is_autotick_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "garbage", ""])
def test_disabled_for_non_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(autotick.ENABLE_ENV, value)
    assert autotick.is_autotick_enabled() is False


# -- interval resolution ----------------------------------------------------


def test_interval_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(autotick.INTERVAL_ENV, raising=False)
    assert autotick.resolve_interval_seconds() == 600.0


def test_interval_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(autotick.INTERVAL_ENV, "120")
    assert autotick.resolve_interval_seconds() == 120.0


def test_interval_clamped_to_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(autotick.INTERVAL_ENV, "5")
    assert autotick.resolve_interval_seconds() == 30.0


def test_interval_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(autotick.INTERVAL_ENV, "not-a-number")
    assert autotick.resolve_interval_seconds() == 600.0


# -- single tick error isolation -------------------------------------------


class _FakeService:
    def __init__(self, status: dict[str, Any]) -> None:
        self._status = status

    def tick(self, *, force: bool = False) -> dict[str, Any]:
        return self._status


async def test_run_once_returns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    status = {"runtime": {"active": True, "last_summary": {"decision_count": 3}}}
    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.get_resident_service",
        lambda ws: _FakeService(status),
    )
    result = await autotick.run_autotick_once("/tmp/ws")
    assert result is not None
    assert result.ok is True
    assert result.status == "completed"
    assert result.workspace == "/tmp/ws"
    assert result.metrics == {"decision_count": 3}


async def test_run_once_skipped_when_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.get_resident_service",
        lambda ws: _FakeService({"runtime": {"active": False}}),
    )
    result = await autotick.run_autotick_once("/tmp/ws")
    assert result is not None
    assert result.status == "skipped_inactive"
    assert result.actions == ()


async def test_completed_cycle_published_to_fact_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    status = {"runtime": {"active": True, "last_summary": {"decision_count": 4}}}
    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.get_resident_service",
        lambda ws: _FakeService(status),
    )
    published: list[Any] = []
    monkeypatch.setattr(
        "polaris.cells.events.fact_stream.public.service.append_fact_event",
        lambda command: published.append(command),
    )
    await autotick.run_autotick_once("/tmp/ws")
    assert len(published) == 1
    assert published[0].stream == "resident.cycle.events"
    assert published[0].event_type == "resident.cycle.completed"
    assert published[0].payload["status"] == "completed"


async def test_skipped_cycle_not_published(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.get_resident_service",
        lambda ws: _FakeService({"runtime": {"active": False}}),
    )
    published: list[Any] = []
    monkeypatch.setattr(
        "polaris.cells.events.fact_stream.public.service.append_fact_event",
        lambda command: published.append(command),
    )
    await autotick.run_autotick_once("/tmp/ws")
    assert published == []


async def test_cycle_survives_fact_sink_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    status = {"runtime": {"active": True, "last_summary": {}}}
    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.get_resident_service",
        lambda ws: _FakeService(status),
    )

    def _boom(_command: Any) -> None:
        raise RuntimeError("fact stream down")

    monkeypatch.setattr(
        "polaris.cells.events.fact_stream.public.service.append_fact_event",
        _boom,
    )
    # Sink failure must not break the cycle result.
    result = await autotick.run_autotick_once("/tmp/ws")
    assert result is not None
    assert result.status == "completed"


async def test_run_once_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_ws: str) -> _FakeService:
        raise RuntimeError("tick exploded")

    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.get_resident_service",
        _boom,
    )
    # Must NOT raise — the background loop has to survive a failed tick.
    assert await autotick.run_autotick_once("/tmp/ws") is None


# -- task lifecycle ---------------------------------------------------------


def test_maybe_start_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(autotick.ENABLE_ENV, raising=False)
    assert autotick.maybe_start_resident_autotick("/tmp/ws") is None


async def test_maybe_start_returns_none_without_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(autotick.ENABLE_ENV, "1")
    assert autotick.maybe_start_resident_autotick("   ") is None


async def test_maybe_start_returns_cancellable_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(autotick.ENABLE_ENV, "1")
    monkeypatch.setenv(autotick.INTERVAL_ENV, "30")
    task = autotick.maybe_start_resident_autotick("/tmp/ws")
    assert task is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
