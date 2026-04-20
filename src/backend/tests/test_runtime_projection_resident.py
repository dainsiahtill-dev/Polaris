from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_runtime_projection_includes_resident_state() -> None:
    from polaris.cells.runtime.projection.internal.runtime_projection_service import build_runtime_projection

    state = MagicMock()
    state.settings.workspace = "/tmp/ws"
    state.settings.ramdisk_root = "/tmp/cache"
    state.settings.json_log_path = "/tmp/logs/app.json"

    resident_payload = {
        "identity": {"name": "Resident Engineer"},
        "runtime": {"active": True, "mode": "observe"},
        "agenda": {"pending_goal_ids": []},
    }

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
                                        "polaris.cells.runtime.projection.internal.runtime_projection_service.build_resident_state",
                                        return_value=resident_payload,
                                    ):
                                        with patch(
                                            "polaris.cells.runtime.projection.internal.runtime_projection_service.map_engine_to_court_state",
                                            return_value={},
                                        ):
                                            projection = await build_runtime_projection(
                                                state, "/tmp/ws", "/tmp/cache"
                                            )

    assert projection.resident == resident_payload
    assert projection.snapshot["resident"] == resident_payload


def test_runtime_ws_status_payload_includes_resident_state() -> None:
    from polaris.cells.runtime.projection.internal.status_snapshot_builder import build_status_payload_sync

    state = MagicMock()
    state.settings.workspace = "/tmp/ws"
    state.settings.ramdisk_root = "/tmp/cache"
    state.settings.json_log_path = "/tmp/logs/app.json"

    resident_payload = {
        "identity": {"name": "Resident Engineer"},
        "runtime": {"active": True, "mode": "observe"},
    }

    with patch(
        "polaris.cells.runtime.projection.internal.status_snapshot_builder.build_snapshot",
        return_value={"ok": True},
    ):
        with patch(
            "polaris.cells.runtime.projection.internal.status_snapshot_builder.build_resident_state",
            return_value=resident_payload,
        ):
            with patch(
                "polaris.cells.runtime.projection.internal.status_snapshot_builder.get_lancedb_status",
                return_value={},
            ):
                with patch(
                    "polaris.cells.runtime.projection.internal.status_snapshot_builder.build_memory_payload",
                    return_value=None,
                ):
                    with patch(
                        "polaris.cells.runtime.projection.internal.status_snapshot_builder.build_success_stats_payload",
                        return_value={},
                    ):
                        with patch(
                            "polaris.cells.runtime.projection.internal.status_snapshot_builder._build_anthro_state",
                            return_value=None,
                        ):
                            with patch(
                                "polaris.cells.runtime.projection.internal.status_snapshot_builder.map_engine_to_court_state",
                                return_value={},
                            ):
                                payload = build_status_payload_sync(
                                    state,
                                    workspace="/tmp/ws",
                                    cache_root="/tmp/cache",
                                    pm_status={"running": False},
                                    director_status={"running": False},
                                )

    assert payload["resident"] == resident_payload
