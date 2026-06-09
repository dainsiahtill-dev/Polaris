"""Tests for G4: the `resident.autonomy` public contract handlers.

The declared CQRS contracts (RunResidentCycleCommandV1, QueryResidentStatusV1,
RecordResidentEvidenceCommandV1, ResidentAutonomyResultV1,
ResidentCycleCompletedEventV1, ResidentAutonomyError) were inert dataclasses
with no handler/caller.  These tests pin the handlers that now map them onto
ResidentService.
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.resident.autonomy.public import service
from polaris.cells.resident.autonomy.public.contracts import (
    QueryResidentStatusV1,
    RecordResidentEvidenceCommandV1,
    ResidentAutonomyError,
    RunResidentCycleCommandV1,
)


class _FakeService:
    def __init__(self, status: dict[str, Any]) -> None:
        self._status = status
        self.tick_calls: list[bool] = []
        self.detail_calls: list[bool] = []

    def tick(self, *, force: bool = False) -> dict[str, Any]:
        self.tick_calls.append(force)
        return self._status

    def get_status(self, *, include_details: bool = False) -> dict[str, Any]:
        self.detail_calls.append(include_details)
        return {"status": "ok", "details": include_details}


def _patch_service(monkeypatch: pytest.MonkeyPatch, fake: _FakeService) -> None:
    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.get_resident_service",
        lambda _ws: fake,
    )


# -- QueryResidentStatusV1 --------------------------------------------------


def test_query_resident_status(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeService({})
    _patch_service(monkeypatch, fake)
    out = service.query_resident_status(QueryResidentStatusV1(workspace="/ws"), include_details=True)
    assert out == {"status": "ok", "details": True}
    assert fake.detail_calls == [True]


# -- RunResidentCycleCommandV1 / ResidentAutonomyResultV1 / event -----------


def test_run_resident_cycle_active(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeService({"runtime": {"active": True, "last_summary": {"decision_count": 5, "skill_count": 2}}})
    _patch_service(monkeypatch, fake)
    result = service.run_resident_cycle(RunResidentCycleCommandV1(workspace="/ws", cycle_id="c1", goal="g"))
    assert result.ok is True
    assert result.status == "completed"
    assert "meta_cognition" in result.actions
    assert result.metrics == {"decision_count": 5, "skill_count": 2}
    assert fake.tick_calls == [False]  # no force in context → respects active gate


def test_run_resident_cycle_inactive_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeService({"runtime": {"active": False}})
    _patch_service(monkeypatch, fake)
    result = service.run_resident_cycle(RunResidentCycleCommandV1(workspace="/ws", cycle_id="c1", goal="g"))
    assert result.status == "skipped_inactive"
    assert result.actions == ()
    assert result.metrics == {}


def test_run_resident_cycle_force_from_context(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeService({"runtime": {"active": True}})
    _patch_service(monkeypatch, fake)
    service.run_resident_cycle(
        RunResidentCycleCommandV1(workspace="/ws", cycle_id="c1", goal="g", context={"force": True})
    )
    assert fake.tick_calls == [True]


def test_run_resident_cycle_wraps_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        def tick(self, *, force: bool = False) -> dict[str, Any]:
            raise RuntimeError("kaboom")

    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.get_resident_service",
        lambda _ws: _Boom(),
    )
    with pytest.raises(ResidentAutonomyError) as excinfo:
        service.run_resident_cycle(RunResidentCycleCommandV1(workspace="/ws", cycle_id="c1", goal="g"))
    assert excinfo.value.code == "cycle_execution_failed"
    assert excinfo.value.details["cycle_id"] == "c1"


# -- RecordResidentEvidenceCommandV1 ----------------------------------------


def test_record_resident_evidence_maps_to_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    def _fake_record(ws: str, payload: Any) -> dict[str, Any]:
        captured.append((ws, dict(payload)))
        return {"ok": True}

    monkeypatch.setattr(
        "polaris.cells.resident.autonomy.public.service.record_resident_decision",
        _fake_record,
    )
    out = service.record_resident_evidence(
        RecordResidentEvidenceCommandV1(
            workspace="/ws",
            cycle_id="c1",
            evidence_kind="task_review",
            payload={"task_id": "t1", "status": "reviewed"},
        )
    )
    assert out == {"ok": True}
    ws, payload = captured[0]
    assert ws == "/ws"
    assert payload["actor"] == "resident"
    assert payload["stage"] == "evidence:task_review"
    assert payload["context_refs"] == ["c1"]
    assert payload["actual_outcome"] == {"task_id": "t1", "status": "reviewed"}
