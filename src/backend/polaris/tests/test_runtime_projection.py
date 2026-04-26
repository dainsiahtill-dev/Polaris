"""Tests for RuntimeProjectionService.

Covers all four scenarios:
1. workflow 有效 (workflow valid)
2. workflow 缺失 (workflow missing)
3. workflow stale but local live
4. all unavailable fallback
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestMergeDirectorStatus:
    """Tests for merge_director_status function."""

    def test_merge_uses_workflow_when_available(self) -> None:
        """Scenario 1: workflow 有效 - Use workflow as source of truth."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import merge_director_status

        local = {"running": False, "state": "IDLE", "status": {"tasks": {"total": 0}}}
        workflow = {
            "running": True,
            "state": "RUNNING",
            "workflow_id": "wf-001",
            "status": {"state": "RUNNING", "tasks": {"total": 5}},
        }

        result = merge_director_status(local, workflow)

        assert result["running"] is True
        assert result["workflow_id"] == "wf-001"
        assert result["source"] == "workflow"

    def test_merge_uses_local_when_workflow_missing(self) -> None:
        """Scenario 2: workflow 缺失 - Use local when workflow unavailable."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import merge_director_status

        local = {"running": True, "state": "RUNNING", "status": {"tasks": {"total": 3}}}
        workflow = None

        result = merge_director_status(local, workflow)

        assert result["running"] is True
        assert result["source"] == "v2_service"

    def test_merge_uses_local_when_workflow_stale_but_local_live(self) -> None:
        """Scenario 3: workflow stale but local live - Keep local as authoritative."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import merge_director_status

        local = {
            "running": True,
            "state": "RUNNING",
            "source": "v2_service",
            "mode": "v2_service",
            "status": {
                "state": "RUNNING",
                "tasks": {
                    "total": 1,
                    "by_status": {"IN_PROGRESS": 1},
                    "task_rows": [{"id": "local-1", "status": "RUNNING", "title": "live task"}],
                },
            },
        }
        # Workflow has NO tasks (empty) - this triggers local selection
        workflow = {
            "running": False,
            "state": "PENDING",
            "workflow_id": "wf-stale",
            "status": {
                "state": "PENDING",
                "tasks": {"total": 0, "task_rows": []},
            },
        }

        result = merge_director_status(local, workflow)

        assert result["source"] == "v2_service"
        assert result["running"] is True
        assert result["status"]["tasks"]["task_rows"][0]["id"] == "local-1"

    def test_merge_fallback_when_all_unavailable(self) -> None:
        """Scenario 4: all unavailable fallback - Return local (even if empty)."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import merge_director_status

        local = {"running": False, "source": "none", "status": {}}
        workflow = None

        result = merge_director_status(local, workflow)

        assert result["running"] is False
        assert result["source"] == "none"


class TestSelectTaskRows:
    """Tests for select_task_rows function."""

    def test_select_workflow_when_available(self) -> None:
        """Rule 1: Prefer workflow tasks when available."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import TaskSource, select_task_rows

        workflow_tasks = [
            {"id": "wf-1", "status": "PENDING"},
            {"id": "wf-2", "status": "RUNNING"},
        ]
        local_status = {"running": True, "status": {"tasks": {"task_rows": []}}}

        tasks, source = select_task_rows(workflow_tasks, local_status)

        assert source == TaskSource.WORKFLOW
        assert len(tasks) == 2
        assert tasks[0]["id"] == "wf-1"

    def test_select_local_live_when_workflow_missing(self) -> None:
        """Rule 2: Fall back to local live tasks when workflow unavailable."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import TaskSource, select_task_rows

        workflow_tasks: list = []
        local_status = {
            "running": True,
            "state": "RUNNING",
            "status": {
                "tasks": {
                    "task_rows": [
                        {"id": "local-1", "status": "RUNNING"},
                    ]
                }
            },
        }

        tasks, source = select_task_rows(workflow_tasks, local_status)

        assert source == TaskSource.LOCAL_LIVE
        assert len(tasks) == 1
        assert tasks[0]["id"] == "local-1"

    def test_select_empty_when_both_unavailable(self) -> None:
        """Rule 3: Return empty when no tasks available."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import TaskSource, select_task_rows

        workflow_tasks: list = []
        local_status = {"running": False, "status": {}}

        tasks, source = select_task_rows(workflow_tasks, local_status)

        assert source == TaskSource.NONE
        assert len(tasks) == 0

    def test_select_from_projection_keeps_terminal_local_rows(self) -> None:
        """When workflow rows are absent, keep local rows even after Director stopped."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import (
            RuntimeProjection,
            select_task_rows_from_projection,
        )

        projection = RuntimeProjection(
            pm_local={},
            director_local={
                "running": False,
                "state": "COMPLETED",
                "status": {
                    "state": "COMPLETED",
                    "tasks": {
                        "active": 0,
                        "task_rows": [{"id": "director-1", "status": "COMPLETED", "metadata": {"pm_task_id": "PM-1"}}],
                    },
                },
            },
            workflow_archive={"tasks": []},
            engine_fallback=None,
        )

        rows = select_task_rows_from_projection(projection)

        assert len(rows) == 1
        assert rows[0]["id"] == "director-1"

    def test_select_from_projection_prefers_workflow_rows(self) -> None:
        """Workflow rows still have precedence over local terminal snapshots."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import (
            RuntimeProjection,
            select_task_rows_from_projection,
        )

        projection = RuntimeProjection(
            pm_local={},
            director_local={
                "running": False,
                "state": "COMPLETED",
                "status": {
                    "state": "COMPLETED",
                    "tasks": {"task_rows": [{"id": "local-1", "status": "COMPLETED"}]},
                },
            },
            workflow_archive={"tasks": [{"id": "wf-1", "status": "COMPLETED"}]},
            engine_fallback=None,
        )

        rows = select_task_rows_from_projection(projection)

        assert len(rows) == 1
        assert rows[0]["id"] == "wf-1"


class TestRuntimeProjectionService:
    """Integration tests for build_runtime_projection."""

    @pytest.mark.asyncio
    async def test_projection_workflow_valid(self) -> None:
        """Scenario 1: workflow 有效 - Full projection with workflow."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import (
            TaskSource,
            build_runtime_projection,
        )

        state = MagicMock()
        state.settings.workspace = "/tmp/ws"
        state.settings.ramdisk_root = "/tmp/cache"
        state.settings.json_log_path = "/tmp/logs/app.json"

        with patch(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_pm_local_status",
            new_callable=AsyncMock,
            return_value={"running": False},
        ):
            with patch(
                "polaris.cells.runtime.projection.internal.runtime_projection_service.get_director_local_status",
                new_callable=AsyncMock,
                return_value={"running": False},
            ):
                with patch(
                    "polaris.cells.runtime.projection.internal.runtime_projection_service.get_workflow_director_status",
                    new_callable=AsyncMock,
                    return_value={
                        "running": True,
                        "workflow_id": "wf-001",
                        "status": {"state": "RUNNING"},
                    },
                ):
                    with patch(
                        "polaris.cells.runtime.projection.internal.runtime_projection_service.build_workflow_task_rows",
                        return_value=[
                            {"id": "task-1", "status": "PENDING"},
                        ],
                    ):
                        with patch(
                            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_lancedb_status",
                            return_value={},
                        ):
                            with patch(
                                "polaris.cells.runtime.projection.internal.artifacts.build_memory_payload",
                                return_value=None,
                            ):
                                with patch(
                                    "polaris.cells.runtime.projection.internal.artifacts.build_success_stats_payload",
                                    return_value={},
                                ):
                                    with patch(
                                        "polaris.cells.runtime.projection.internal.runtime_projection_service.build_anthro_state",
                                        return_value=None,
                                    ):
                                        with patch(
                                            "polaris.cells.runtime.projection.internal.runtime_projection_service.map_engine_to_court_state",
                                            return_value={},
                                        ):
                                            projection = await build_runtime_projection(
                                                state,
                                                "/tmp/ws",
                                                "/tmp/cache",
                                                use_cache=False,
                                            )

        assert projection.pm_local["running"] is False
        assert projection.workflow_archive is not None
        assert projection.workflow_archive["workflow_id"] == "wf-001"
        assert projection.task_source == TaskSource.WORKFLOW

    @pytest.mark.asyncio
    async def test_projection_workflow_missing(self) -> None:
        """Scenario 2: workflow 缺失 - Use local status."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import build_runtime_projection

        state = MagicMock()
        state.settings.workspace = "/tmp/ws"
        state.settings.ramdisk_root = "/tmp/cache"
        state.settings.json_log_path = "/tmp/logs/app.json"

        with patch(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_pm_local_status",
            new_callable=AsyncMock,
            return_value={"running": False},
        ):
            with patch(
                "polaris.cells.runtime.projection.internal.runtime_projection_service.get_director_local_status",
                new_callable=AsyncMock,
                return_value={"running": True, "status": {"state": "RUNNING"}},
            ):
                with patch(
                    "polaris.cells.runtime.projection.internal.runtime_projection_service.get_workflow_director_status",
                    new_callable=AsyncMock,
                    return_value=None,
                ):
                    with patch(
                        "polaris.cells.runtime.projection.internal.runtime_projection_service.build_workflow_task_rows",
                        return_value=[],
                    ):
                        with patch(
                            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_lancedb_status",
                            return_value={},
                        ):
                            with patch(
                                "polaris.cells.runtime.projection.internal.artifacts.build_memory_payload",
                                return_value=None,
                            ):
                                with patch(
                                    "polaris.cells.runtime.projection.internal.artifacts.build_success_stats_payload",
                                    return_value={},
                                ):
                                    with patch(
                                        "polaris.cells.runtime.projection.internal.runtime_projection_service.build_anthro_state",
                                        return_value=None,
                                    ):
                                        with patch(
                                            "polaris.cells.runtime.projection.internal.runtime_projection_service.map_engine_to_court_state",
                                            return_value={},
                                        ):
                                            projection = await build_runtime_projection(
                                                state,
                                                "/tmp/ws",
                                                "/tmp/cache",
                                                use_cache=False,
                                            )

        assert projection.director_local["running"] is True
        assert projection.workflow_archive is None

    @pytest.mark.asyncio
    async def test_projection_workflow_stale_local_live(self) -> None:
        """Scenario 3: workflow stale but local live - Use local tasks."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import (
            TaskSource,
            build_runtime_projection,
        )

        state = MagicMock()
        state.settings.workspace = "/tmp/ws"
        state.settings.ramdisk_root = "/tmp/cache"
        state.settings.json_log_path = "/tmp/logs/app.json"

        local_director = {
            "running": True,
            "source": "v2_service",
            "status": {
                "state": "RUNNING",
                "tasks": {
                    "total": 1,
                    "task_rows": [{"id": "local-task", "status": "RUNNING"}],
                },
            },
        }

        with patch(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_pm_local_status",
            new_callable=AsyncMock,
            return_value={"running": False},
        ):
            with patch(
                "polaris.cells.runtime.projection.internal.runtime_projection_service.get_director_local_status",
                new_callable=AsyncMock,
                return_value=local_director,
            ):
                with patch(
                    "polaris.cells.runtime.projection.internal.runtime_projection_service.get_workflow_director_status",
                    new_callable=AsyncMock,
                    return_value={
                        "running": False,
                        "workflow_id": "wf-stale",
                        "status": {"state": "PENDING", "tasks": {}},
                    },
                ):
                    # Return empty workflow tasks to trigger local fallback
                    with patch(
                        "polaris.cells.runtime.projection.internal.runtime_projection_service.build_workflow_task_rows",
                        return_value=[],
                    ):
                        with patch(
                            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_lancedb_status",
                            return_value={},
                        ):
                            with patch(
                                "polaris.cells.runtime.projection.internal.artifacts.build_memory_payload",
                                return_value=None,
                            ):
                                with patch(
                                    "polaris.cells.runtime.projection.internal.artifacts.build_success_stats_payload",
                                    return_value={},
                                ):
                                    with patch(
                                        "polaris.cells.runtime.projection.internal.runtime_projection_service.build_anthro_state",
                                        return_value=None,
                                    ):
                                        with patch(
                                            "polaris.cells.runtime.projection.internal.runtime_projection_service.map_engine_to_court_state",
                                            return_value={},
                                        ):
                                            projection = await build_runtime_projection(
                                                state,
                                                "/tmp/ws",
                                                "/tmp/cache",
                                                use_cache=False,
                                            )

        # Local should be authoritative due to live tasks
        assert projection.director_local["source"] == "v2_service"
        # With empty workflow tasks, should fall back to local
        assert projection.task_source == TaskSource.LOCAL_LIVE

    @pytest.mark.asyncio
    async def test_projection_all_unavailable_fallback(self) -> None:
        """Scenario 4: all unavailable fallback - Minimal projection."""
        from polaris.cells.runtime.projection.internal.runtime_projection_service import (
            TaskSource,
            build_runtime_projection,
        )

        state = MagicMock()
        state.settings.workspace = "/tmp/ws"
        state.settings.ramdisk_root = "/tmp/cache"
        state.settings.json_log_path = "/tmp/logs/app.json"

        with patch(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_pm_local_status",
            new_callable=AsyncMock,
            return_value={"running": False},
        ):
            with patch(
                "polaris.cells.runtime.projection.internal.runtime_projection_service.get_director_local_status",
                new_callable=AsyncMock,
                return_value={"running": False, "source": "none"},
            ):
                with patch(
                    "polaris.cells.runtime.projection.internal.runtime_projection_service.get_workflow_director_status",
                    new_callable=AsyncMock,
                    return_value=None,
                ):
                    with patch(
                        "polaris.cells.runtime.projection.internal.runtime_projection_service.build_workflow_task_rows",
                        return_value=[],
                    ):
                        with patch(
                            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_lancedb_status",
                            return_value={},
                        ):
                            with patch(
                                "polaris.cells.runtime.projection.internal.artifacts.build_memory_payload",
                                return_value=None,
                            ):
                                with patch(
                                    "polaris.cells.runtime.projection.internal.artifacts.build_success_stats_payload",
                                    return_value={},
                                ):
                                    with patch(
                                        "polaris.cells.runtime.projection.internal.runtime_projection_service.build_anthro_state",
                                        return_value=None,
                                    ):
                                        with patch(
                                            "polaris.cells.runtime.projection.internal.runtime_projection_service.map_engine_to_court_state",
                                            return_value={},
                                        ):
                                            projection = await build_runtime_projection(
                                                state,
                                                "/tmp/ws",
                                                "/tmp/cache",
                                                use_cache=False,
                                            )

        assert projection.director_local["source"] == "none"
        assert projection.task_source == TaskSource.NONE
        assert len(projection.task_rows) == 0

    @pytest.mark.asyncio
    async def test_sync_bridge_does_not_deadlock_when_called_inside_running_loop(self) -> None:
        from polaris.cells.runtime.projection.internal import runtime_projection_service as rp

        state = MagicMock()
        state.settings.workspace = "/tmp/ws"
        state.settings.ramdisk_root = "/tmp/cache"
        state.settings.json_log_path = "/tmp/logs/app.json"

        async def _fake_projection(state_arg, workspace_arg, cache_root_arg, *, use_cache=True):
            del state_arg, workspace_arg, cache_root_arg, use_cache
            return rp.RuntimeProjection(
                pm_local={"running": False},
                director_local={"running": False},
                workflow_archive=None,
                engine_fallback=None,
            )

        with patch(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.build_runtime_projection",
            new=_fake_projection,
        ):
            started = time.perf_counter()
            projection = rp.build_runtime_projection_sync(state, "/tmp/ws", "/tmp/cache")
            elapsed = time.perf_counter() - started

        assert projection.pm_local["running"] is False
        assert elapsed < 2.0


class TestRuntimeWsStatusCanonical:
    """Verify runtime_ws_status uses the canonical director sources."""

    @pytest.mark.asyncio
    async def test_build_director_status_uses_current_sources(self) -> None:
        from polaris.cells.runtime.projection.internal.status_snapshot_builder import build_director_status

        local = {
            "running": True,
            "state": "RUNNING",
            "source": "v2_service",
            "status": {
                "tasks": {
                    "total": 1,
                    "task_rows": [{"id": "local-1", "status": "RUNNING"}],
                }
            },
        }
        workflow = {
            "running": False,
            "state": "IDLE",
            "status": {
                "tasks": {"total": 0, "task_rows": []},
            },
        }

        with (
            patch(
                "polaris.cells.runtime.projection.internal.status_snapshot_builder.get_director_local_status",
                new_callable=AsyncMock,
                return_value=local,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.status_snapshot_builder.get_workflow_director_status",
                new_callable=AsyncMock,
                return_value=workflow,
            ),
        ):
            result = await build_director_status(
                MagicMock(),
                "/tmp/ws",
                "/tmp/cache",
            )

        assert result["running"] is True
        assert result["source"] == "v2_service"
