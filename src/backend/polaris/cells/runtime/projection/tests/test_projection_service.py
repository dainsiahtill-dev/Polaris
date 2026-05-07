"""Unit tests for `runtime.projection` cell - RuntimeProjectionService and helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.cells.runtime.projection.internal.runtime_projection_service import (
    ProjectionCache,
    RuntimeProjection,
    RuntimeProjectionService,
    TaskSource,
    _parse_engine_updated_at,
    _safe_int,
    _state_token,
    _task_totals,
    _workflow_has_live_rows,
    load_runtime_task_rows,
    merge_director_status,
    select_task_rows,
    select_task_rows_from_projection,
)
from polaris.cells.runtime.projection.internal.status_snapshot_builder import (
    _parse_engine_updated_at as _parse_status_snapshot_updated_at,
)

if TYPE_CHECKING:
    from pathlib import Path

# =============================================================================
# Helper function unit tests
# =============================================================================


class TestSafeInt:
    def test_positive_integer(self) -> None:
        assert _safe_int(42) == 42

    def test_negative_integer_becomes_zero(self) -> None:
        assert _safe_int(-5) == 0

    def test_float_rounds_down(self) -> None:
        assert _safe_int(3.9) == 3

    def test_string_int(self) -> None:
        assert _safe_int("123") == 123

    def test_non_numeric_returns_zero(self) -> None:
        assert _safe_int("abc") == 0

    def test_none_returns_zero(self) -> None:
        assert _safe_int(None) == 0


class TestStateToken:
    def test_top_level_state(self) -> None:
        payload = {"state": "running"}
        assert _state_token(payload) == "RUNNING"

    def test_nested_status_state(self) -> None:
        payload = {"status": {"state": "idle"}}
        assert _state_token(payload) == "IDLE"

    def test_empty_payload(self) -> None:
        assert _state_token({}) == ""
        assert _state_token(None) == ""

    def test_top_level_takes_precedence(self) -> None:
        payload = {"state": "running", "status": {"state": "idle"}}
        assert _state_token(payload) == "RUNNING"

    def test_whitespace_normalized(self) -> None:
        payload = {"state": "  RUNNING  "}
        assert _state_token(payload) == "RUNNING"


class TestTaskTotals:
    def test_direct_tasks(self) -> None:
        payload = {"tasks": {"total": 10, "by_status": {"IN_PROGRESS": 3}}}
        total, active = _task_totals(payload)
        assert total == 10
        assert active == 3

    def test_nested_status_tasks(self) -> None:
        payload = {"status": {"tasks": {"total": 5, "by_status": {"RUNNING": 2, "CLAIMED": 1}}}}
        total, active = _task_totals(payload)
        assert total == 5
        assert active == 3

    def test_inactive_tasks_not_counted(self) -> None:
        payload = {"tasks": {"total": 8, "by_status": {"PENDING": 5, "COMPLETED": 3}}}
        total, active = _task_totals(payload)
        assert total == 8
        assert active == 0

    def test_missing_tasks_returns_zero(self) -> None:
        assert _task_totals({}) == (0, 0)
        assert _task_totals(None) == (0, 0)


class TestWorkflowHasLiveRows:
    def test_has_live_rows(self) -> None:
        payload = {"tasks": {"task_rows": [{"status": "RUNNING"}, {"status": "COMPLETED"}]}}
        assert _workflow_has_live_rows(payload) is True

    def test_no_live_rows(self) -> None:
        payload = {"tasks": {"task_rows": [{"status": "PENDING"}, {"status": "PENDING"}]}}
        assert _workflow_has_live_rows(payload) is False

    def test_empty_rows(self) -> None:
        assert _workflow_has_live_rows({"tasks": {"task_rows": []}}) is False
        assert _workflow_has_live_rows(None) is False


class TestParseEngineUpdatedAt:
    def test_accepts_legacy_timestamp(self) -> None:
        assert _parse_engine_updated_at("2026-05-07 15:36:07") is not None

    def test_accepts_iso_z_timestamp(self) -> None:
        assert _parse_engine_updated_at("2026-05-07T15:36:07Z") is not None

    def test_status_snapshot_builder_accepts_iso_z_timestamp(self) -> None:
        assert _parse_status_snapshot_updated_at("2026-05-07T15:36:07Z") is not None


# =============================================================================
# merge_director_status tests
# =============================================================================


class TestMergeDirectorStatus:
    def test_local_takes_precedence_when_running_with_tasks(self) -> None:
        local = {"running": True, "mode": "v2_service", "metrics": {"workflow_id": "wf-1"}, "tasks": {"total": 5}}
        workflow = {"running": False, "state": "queued", "workflow_id": "wf-1"}
        result = merge_director_status(local, workflow)
        assert result["source"] == "v2_service"
        assert result["running"] is True

    def test_workflow_source_when_local_not_running(self) -> None:
        local = {"running": False}
        workflow = {"running": True, "state": "RUNNING", "workflow_id": "wf-2"}
        result = merge_director_status(local, workflow)
        assert result["source"] == "workflow"
        assert result["workflow_id"] == "wf-2"

    def test_empty_workflow_uses_local(self) -> None:
        local = {"running": True, "source": "v2_service"}
        result = merge_director_status(local, None)
        assert result["source"] == "v2_service"

    def test_both_empty_returns_source_none(self) -> None:
        result = merge_director_status(None, None)
        # Returns a dict with 'source: none', not empty dict
        assert result == {"source": "none"}

    def test_token_budget_merged(self) -> None:
        local = {"running": False, "token_budget": {"used": 100}}
        workflow = {"running": False, "token_budget": {"limit": 1000}}
        result = merge_director_status(local, workflow)
        assert result["token_budget"]["used"] == 100
        assert result["token_budget"]["limit"] == 1000

    def test_local_running_overrides_workflow_state(self) -> None:
        local = {"running": True, "state": "RUNNING"}
        workflow = {"running": False, "state": "queued"}
        result = merge_director_status(local, workflow)
        assert result["state"] == "RUNNING"


# =============================================================================
# select_task_rows tests
# =============================================================================


class TestSelectTaskRows:
    def test_workflow_rows_preferred(self) -> None:
        workflow_rows = [{"id": "task-1", "status": "RUNNING"}]
        local = {"running": True}
        rows, source = select_task_rows(workflow_rows, local)
        assert rows == workflow_rows
        assert source == TaskSource.WORKFLOW

    def test_local_live_when_workflow_empty(self) -> None:
        local = {
            "running": True,
            "state": "RUNNING",
            "status": {"tasks": {"task_rows": [{"id": "local-1", "status": "RUNNING"}]}},
        }
        rows, source = select_task_rows([], local)
        assert rows == [{"id": "local-1", "status": "RUNNING"}]
        assert source == TaskSource.LOCAL_LIVE

    def test_empty_when_nothing_available(self) -> None:
        rows, source = select_task_rows(None, None)
        assert rows == []
        assert source == TaskSource.NONE

    def test_workflow_none_vs_empty_list(self) -> None:
        """None should be treated as empty, not as having content."""
        local = {"running": True, "state": "RUNNING", "status": {"tasks": {"task_rows": [{"id": "x"}]}}}
        rows, source = select_task_rows(None, local)
        assert rows == [{"id": "x"}]
        assert source == TaskSource.LOCAL_LIVE


# =============================================================================
# ProjectionCache tests
# =============================================================================


class TestProjectionCache:
    def test_cache_set_and_get(self) -> None:
        cache = ProjectionCache(ttl_seconds=60.0)
        proj = RuntimeProjection()
        cache.set("test-workspace", proj)
        retrieved = cache.get("test-workspace")
        assert retrieved is proj

    def test_cache_miss_for_unknown_workspace(self) -> None:
        cache = ProjectionCache(ttl_seconds=60.0)
        assert cache.get("unknown") is None

    def test_cache_expires_after_ttl(self) -> None:
        cache = ProjectionCache(ttl_seconds=0.0)  # Immediate expiry
        proj = RuntimeProjection()
        cache.set("test-workspace", proj)
        # Immediately expired
        assert cache.get("test-workspace") is None

    def test_cache_invalidate(self) -> None:
        cache = ProjectionCache(ttl_seconds=60.0)
        proj = RuntimeProjection()
        cache.set("test-workspace", proj)
        cache.invalidate("test-workspace")
        assert cache.get("test-workspace") is None

    def test_cache_clear(self) -> None:
        cache = ProjectionCache(ttl_seconds=60.0)
        cache.set("ws1", RuntimeProjection())
        cache.set("ws2", RuntimeProjection())
        cache.clear()
        assert cache.get("ws1") is None
        assert cache.get("ws2") is None

    def test_cache_empty_workspace_rejected(self) -> None:
        cache = ProjectionCache(ttl_seconds=60.0)
        cache.set("", RuntimeProjection())
        assert cache.get("") is None


# =============================================================================
# select_task_rows_from_projection tests
# =============================================================================


class TestSelectTaskRowsFromProjection:
    def test_workflow_tasks_returned_when_available(self) -> None:
        proj = RuntimeProjection(
            workflow_archive={"tasks": [{"id": "wf-task-1"}]},
            director_local={"running": True},
        )
        rows = select_task_rows_from_projection(proj)
        assert rows == [{"id": "wf-task-1"}]

    def test_local_rows_when_workflow_empty(self) -> None:
        proj = RuntimeProjection(
            workflow_archive={},
            director_local={
                "running": True,
                "state": "RUNNING",
                "status": {"tasks": {"task_rows": [{"id": "local-task-1"}]}},
            },
        )
        rows = select_task_rows_from_projection(proj)
        assert rows == [{"id": "local-task-1"}]

    def test_empty_when_nothing_available(self) -> None:
        proj = RuntimeProjection()
        rows = select_task_rows_from_projection(proj)
        assert rows == []


# =============================================================================
# RuntimeProjectionService.build() tests (sync, no I/O)
# =============================================================================


class TestRuntimeProjectionServiceBuild:
    def test_build_returns_runtime_projection(self, tmp_path: Path) -> None:
        """Sanity: build() returns a RuntimeProjection with expected fields."""
        ProjectionCache(ttl_seconds=60.0)
        proj = RuntimeProjectionService.build(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=False,
        )
        assert isinstance(proj, RuntimeProjection)
        # Fields should be present (even if empty)
        assert isinstance(proj.pm_local, dict)
        assert isinstance(proj.director_local, dict)

    def test_build_with_custom_cache(self, tmp_path: Path) -> None:
        ProjectionCache(ttl_seconds=60.0)
        proj1 = RuntimeProjectionService.build(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=True,
        )
        # Second call with same cache should hit cache
        proj2 = RuntimeProjectionService.build(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=True,
        )
        # Same instance returned from cache
        assert proj1 is proj2

    def test_build_cache_disabled(self, tmp_path: Path) -> None:
        ProjectionCache(ttl_seconds=60.0)
        proj1 = RuntimeProjectionService.build(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=False,
        )
        proj2 = RuntimeProjectionService.build(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=False,
        )
        # Different instances when cache disabled
        assert proj1 is not proj2


# =============================================================================
# RuntimeProjection dataclass tests
# =============================================================================


class TestRuntimeProjectionDataclass:
    def test_default_fields(self) -> None:
        proj = RuntimeProjection()
        assert proj.pm_local == {}
        assert proj.director_local == {}
        assert proj.workflow_archive is None
        assert proj.engine_fallback is None
        assert proj.court_state == {}
        assert proj.snapshot == {}
        assert proj.memory is None
        assert proj.success_stats == {}
        assert proj.anthro_state is None
        assert proj.lancedb == {}
        assert proj.resident is None
        assert proj.task_source == TaskSource.NONE
        assert proj.task_rows == []

    def test_custom_fields(self) -> None:
        custom = {"running": True, "pid": 12345}
        proj = RuntimeProjection(
            pm_local=custom,
            task_source=TaskSource.WORKFLOW,
            task_rows=[{"id": "task-1"}],
        )
        assert proj.pm_local == custom
        assert proj.task_source == TaskSource.WORKFLOW
        assert proj.task_rows == [{"id": "task-1"}]


# =============================================================================
# load_runtime_task_rows tests
# =============================================================================


class TestLoadRuntimeTaskRows:
    def test_empty_workspace_returns_empty(self) -> None:
        rows = load_runtime_task_rows("")
        assert rows == []

    def test_workspace_with_no_state_owner_returns_empty(self, tmp_path: Path) -> None:
        # No task_runtime state files exist in this tmp_path
        rows = load_runtime_task_rows(str(tmp_path))
        # Should return empty list (no exception)
        assert rows == []


# =============================================================================
# Async build tests (sanity - no I/O)
# =============================================================================


@pytest.mark.asyncio
class TestRuntimeProjectionServiceBuildAsync:
    async def test_build_async_returns_projection(self, tmp_path: Path) -> None:
        proj = await RuntimeProjectionService.build_async(
            workspace=str(tmp_path),
            cache_root=tmp_path,
            use_cache=False,
        )
        assert isinstance(proj, RuntimeProjection)
