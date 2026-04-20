"""Backward compatibility tests for runtime_ws_status.

These tests verify that the legacy API still works with the new unified projection.
"""

from __future__ import annotations


def test_websocket_director_status_prefers_workflow_when_available() -> None:
    """Test that workflow status is preferred when available."""
    from polaris.cells.runtime.projection.internal.runtime_projection_service import merge_director_status

    # Test the merge logic directly
    local = {"running": True, "source": "v2_service", "status": {"state": "RUNNING"}}
    workflow = {
        "running": True,
        "source": "workflow",
        "workflow_id": "wf-001",
        "status": {"state": "RUNNING", "tasks": {"total": 3}},
    }

    result = merge_director_status(local, workflow)

    assert result["running"] is True
    assert result["source"] == "workflow"
    assert result["mode"] == "workflow"


def test_websocket_director_status_uses_local_runtime_when_workflow_unavailable() -> None:
    """Test that local runtime is used when workflow is unavailable."""
    from polaris.cells.runtime.projection.internal.runtime_projection_service import merge_director_status

    local = {"running": False, "source": "v2_service", "status": {"state": "IDLE"}}

    result = merge_director_status(local, None)

    assert result["running"] is False
    assert result["source"] == "v2_service"


def test_websocket_director_status_falls_back_to_legacy_when_all_unavailable() -> None:
    """Test fallback when both sources unavailable."""
    from polaris.cells.runtime.projection.internal.runtime_projection_service import merge_director_status

    result = merge_director_status(None, None)

    # Should return dict with source:none when both are None
    assert result == {"source": "none"}


def test_websocket_director_status_keeps_local_runtime_when_workflow_snapshot_is_stale() -> None:
    """Test that local runtime is kept when workflow is stale but local is live."""
    from polaris.cells.runtime.projection.internal.runtime_projection_service import merge_director_status

    local = {
        "running": True,
        "source": "v2_service",
        "mode": "v2_service",
        "status": {
            "state": "RUNNING",
            "metrics": {"tasks_completed": 0},
            "tasks": {
                "total": 1,
                "by_status": {"IN_PROGRESS": 1},
                "task_rows": [
                    {"id": "local-1", "status": "RUNNING", "title": "live local task"},
                ],
            },
        },
    }
    stale_workflow = {
        "running": False,
        "source": "workflow",
        "workflow_id": "wf-stale-001",
        "status": {
            "state": "PENDING",
            "metrics": {"workflow_id": "wf-stale-001"},
            "tasks": {"total": 1, "task_rows": []},
        },
    }

    result = merge_director_status(local, stale_workflow)

    assert result["source"] == "v2_service"
    assert result["status"]["state"] == "RUNNING"
    assert result["status"]["tasks"]["task_rows"][0]["id"] == "local-1"
    assert result["workflow_id"] == "wf-stale-001"
