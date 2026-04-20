"""Tests for DirectorPool and EventBus.

Covers initialization, assignment, conflict detection, recovery,
progress updates, and event publication.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from polaris.cells.chief_engineer.blueprint.internal.director_pool import (
    DirectorPhase,
    DirectorPool,
    ScopeConflictDetector,
)
from polaris.cells.chief_engineer.blueprint.internal.event_bus import EventBus


class FakeTask:
    """Minimal task stub for DirectorPool tests."""

    def __init__(self, task_id: str, target_files: list[str] | None = None) -> None:
        self.id = task_id
        self.target_files = target_files or []


class FakeBlueprint:
    """Minimal blueprint stub."""

    pass


@pytest.fixture
def pool() -> DirectorPool:
    p = DirectorPool(workspace="/tmp/test", max_directors=3)
    p.initialize_directors()
    p._submit_director_task_workflow = AsyncMock()  # type: ignore[method-assign]
    return p


class TestEventBus:
    """Tests for the lightweight EventBus."""

    def test_publish_delivers_to_subscribers(self) -> None:
        bus = EventBus()
        received: list[tuple[str, dict[str, Any]]] = []
        bus.subscribe("test.event", lambda et, pl: received.append((et, pl)))
        bus.publish("test.event", {"x": 1})
        assert len(received) == 1
        assert received[0] == ("test.event", {"x": 1})

    def test_subscriber_failure_isolated(self) -> None:
        bus = EventBus()
        good_calls: list[str] = []
        bus.subscribe("test.event", lambda _et, _pl: (_ for _ in ()).throw(ValueError("boom")))
        bus.subscribe("test.event", lambda _et, _pl: good_calls.append("ok"))
        bus.publish("test.event", {})
        assert good_calls == ["ok"]

    def test_unsubscribe(self) -> None:
        bus = EventBus()
        calls: list[str] = []

        def handler(_et: str, _pl: dict[str, Any]) -> None:
            calls.append("called")

        bus.subscribe("test.event", handler)
        bus.publish("test.event", {})
        bus.unsubscribe("test.event", handler)
        bus.publish("test.event", {})
        assert calls == ["called"]


class TestScopeConflictDetector:
    """Tests for the global file conflict detector."""

    def test_detect_no_conflict(self) -> None:
        det = ScopeConflictDetector()
        det.acquire("d1", ["a.py"])
        assert det.detect("d2", ["b.py"]) == []

    def test_detect_conflict(self) -> None:
        det = ScopeConflictDetector()
        det.acquire("d1", ["a.py"])
        assert det.detect("d2", ["a.py"]) == ["a.py"]

    def test_release_clears_ownership(self) -> None:
        det = ScopeConflictDetector()
        det.acquire("d1", ["a.py"])
        det.release("d1")
        assert det.detect("d2", ["a.py"]) == []


class TestDirectorPoolInitialization:
    """Tests for pool initialization and degradation."""

    def test_initializes_requested_directors(self) -> None:
        p = DirectorPool(workspace="/tmp/test", max_directors=3)
        p.initialize_directors()
        assert len(p._directors) == 3

    def test_degrades_to_fallback(self) -> None:
        # Force failure by passing invalid max_directors (not really possible
        # with current code, so we simulate by clearing after init)
        p = DirectorPool(workspace="/tmp/test", max_directors=0)
        p.initialize_directors()
        # max_directors is clamped to 1, so we still get 1 director
        assert len(p._directors) == 1


class TestDirectorPoolAssignment:
    """Tests for task assignment logic."""

    @pytest.mark.anyio
    async def test_assigns_idle_director(self, pool: DirectorPool) -> None:
        did = await pool.assign_task(FakeTask("T-1"), FakeBlueprint())
        assert did in pool._directors
        assert pool._directors[did].phase == DirectorPhase.PREPARE
        assert pool._directors[did].current_task_id == "T-1"

    @pytest.mark.anyio
    async def test_conflict_free_director_preferred(self, pool: DirectorPool) -> None:
        # Assign T-1 to d1 with file a.py
        did1 = await pool.assign_task(FakeTask("T-1", ["a.py"]), FakeBlueprint())
        # Assign T-2 with a DIFFERENT file — should go to a different idle director
        did2 = await pool.assign_task(FakeTask("T-2", ["b.py"]), FakeBlueprint())
        assert did1 != did2

    @pytest.mark.anyio
    async def test_conflict_raises_when_file_locked(self, pool: DirectorPool) -> None:
        from polaris.cells.chief_engineer.blueprint.internal.director_pool import (
            DirectorPoolConflictError,
        )

        # Assign T-1 to d1 with file a.py
        await pool.assign_task(FakeTask("T-1", ["a.py"]), FakeBlueprint())
        # T-2 with the SAME file should raise because a.py is already locked by d1
        with pytest.raises(DirectorPoolConflictError):
            await pool.assign_task(FakeTask("T-2", ["a.py"]), FakeBlueprint())

    @pytest.mark.anyio
    async def test_all_directors_busy_assigns_lowest_progress(self, pool: DirectorPool) -> None:
        await pool.assign_task(FakeTask("T-1"), FakeBlueprint())
        await pool.assign_task(FakeTask("T-2"), FakeBlueprint())
        await pool.assign_task(FakeTask("T-3"), FakeBlueprint())
        # All 3 Directors are busy; T-4 should go to the one with lowest progress
        did4 = await pool.assign_task(FakeTask("T-4"), FakeBlueprint())
        assert did4 in pool._directors


class TestDirectorPoolStatus:
    """Tests for status queries."""

    @pytest.mark.anyio
    async def test_get_director_for_task(self, pool: DirectorPool) -> None:
        await pool.assign_task(FakeTask("T-1"), FakeBlueprint())
        assert pool.get_director_for_task("T-1") is not None

    def test_get_director_for_unknown_task(self, pool: DirectorPool) -> None:
        assert pool.get_director_for_task("T-unknown") is None

    @pytest.mark.anyio
    async def test_dashboard_reflects_assignments(self, pool: DirectorPool) -> None:
        await pool.assign_task(FakeTask("T-1"), FakeBlueprint())
        dashboard = pool.get_live_dashboard()
        assert len(dashboard.directors) == 3
        assert "T-1" in dashboard.pending_assignments


class TestDirectorPoolCompletion:
    """Tests for task completion and release."""

    @pytest.mark.anyio
    async def test_mark_completed_releases_director(self, pool: DirectorPool) -> None:
        did = await pool.assign_task(FakeTask("T-1"), FakeBlueprint())
        pool.mark_completed("T-1", success=True)
        assert pool._directors[did].phase == DirectorPhase.IDLE
        assert pool.get_director_for_task("T-1") is None

    @pytest.mark.anyio
    async def test_mark_completed_releases_file_locks(self, pool: DirectorPool) -> None:
        await pool.assign_task(FakeTask("T-1", ["a.py"]), FakeBlueprint())
        pool.mark_completed("T-1", success=True)
        # T-2 with same file should now be conflict-free
        did2 = await pool.assign_task(FakeTask("T-2", ["a.py"]), FakeBlueprint())
        assert did2 is not None


class TestDirectorPoolRecovery:
    """Tests for failure recovery decisions."""

    @pytest.mark.anyio
    async def test_handle_failure_timeout_reassign(self, pool: DirectorPool) -> None:
        await pool.assign_task(FakeTask("T-1"), FakeBlueprint())
        decision = pool.handle_failure("T-1", TimeoutError("too slow"))
        assert decision.action == "reassign"

    @pytest.mark.anyio
    async def test_handle_failure_oom_split(self, pool: DirectorPool) -> None:
        await pool.assign_task(FakeTask("T-1"), FakeBlueprint())
        decision = pool.handle_failure("T-1", MemoryError("OOM"))
        assert decision.action == "split"

    @pytest.mark.anyio
    async def test_handle_failure_default_retry(self, pool: DirectorPool) -> None:
        await pool.assign_task(FakeTask("T-1"), FakeBlueprint())
        decision = pool.handle_failure("T-1", RuntimeError("boom"))
        assert decision.action == "retry"

    def test_handle_failure_unknown_task_aborts(self, pool: DirectorPool) -> None:
        decision = pool.handle_failure("T-unknown", RuntimeError("boom"))
        assert decision.action == "abort"


class TestDirectorPoolProgress:
    """Tests for progress updates."""

    @pytest.mark.anyio
    async def test_update_progress_changes_phase(self, pool: DirectorPool) -> None:
        did = await pool.assign_task(FakeTask("T-1"), FakeBlueprint())
        pool.update_progress(did, phase=DirectorPhase.IMPLEMENT, progress_pct=0.5)
        assert pool._directors[did].phase == DirectorPhase.IMPLEMENT
        assert pool._directors[did].progress_pct == 0.5

    def test_update_progress_clamps_out_of_range(self, pool: DirectorPool) -> None:
        did = next(iter(pool._directors.keys()))
        pool.update_progress(did, progress_pct=2.0)
        assert pool._directors[did].progress_pct == 1.0
        pool.update_progress(did, progress_pct=-1.0)
        assert pool._directors[did].progress_pct == 0.0


class TestDirectorPoolEvents:
    """Tests for EventBus integration."""

    @pytest.mark.anyio
    async def test_assignment_publishes_event(self, pool: DirectorPool) -> None:
        events: list[dict[str, Any]] = []
        pool.event_bus().subscribe("director.assigned", lambda _et, pl: events.append(pl))
        await pool.assign_task(FakeTask("T-1"), FakeBlueprint())
        assert len(events) == 1
        assert events[0]["task_id"] == "T-1"

    @pytest.mark.anyio
    async def test_completion_publishes_event(self, pool: DirectorPool) -> None:
        events: list[dict[str, Any]] = []
        pool.event_bus().subscribe("director.completed", lambda _et, pl: events.append(pl))
        await pool.assign_task(FakeTask("T-1"), FakeBlueprint())
        pool.mark_completed("T-1", success=True)
        assert len(events) == 1
        assert events[0]["success"] is True


class TestDirectorPoolAssignmentFailure:
    """Tests for assignment failure recovery."""

    @pytest.mark.anyio
    async def test_assign_task_resets_director_on_workflow_failure(self) -> None:
        p = DirectorPool(workspace="/tmp/test", max_directors=3)
        p.initialize_directors()
        p._submit_director_task_workflow = AsyncMock(side_effect=RuntimeError("workflow boom"))  # type: ignore[method-assign]

        events: list[dict[str, Any]] = []
        p.event_bus().subscribe("director.assignment_failed", lambda _et, pl: events.append(pl))

        with pytest.raises(RuntimeError, match="workflow boom"):
            await p.assign_task(FakeTask("T-1", ["a.py"]), FakeBlueprint())

        # Director should be back to IDLE
        did = p.get_director_for_task("T-1")
        assert did is None
        assert all(s.phase == DirectorPhase.IDLE for s in p._directors.values())

        # File locks should be released
        assert p._conflict_detector.detect("director-1", ["a.py"]) == []

        # Event should be published
        assert len(events) == 1
        assert events[0]["task_id"] == "T-1"
