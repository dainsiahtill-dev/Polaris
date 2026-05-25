from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from polaris.bootstrap.config import Settings
from polaris.cells.runtime.projection.internal.status_snapshot_builder import build_status_payload_sync
from polaris.kernelone.runtime.defaults import DEFAULT_PM_OUT
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path


def _write_pm_contract(
    workspace: Path,
    cache_root: str,
    *,
    run_id: str,
    task_id: str,
) -> None:
    contract_path = Path(resolve_artifact_path(str(workspace), cache_root, DEFAULT_PM_OUT))
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tasks": [
                    {
                        "id": task_id,
                        "title": task_id,
                        "status": "pending",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_status_payload_snapshot_uses_ws_workspace_context(tmp_path: Path) -> None:
    target_workspace = tmp_path / "target-workspace"
    stale_workspace = tmp_path / "stale-workspace"
    target_workspace.mkdir(parents=True, exist_ok=True)
    stale_workspace.mkdir(parents=True, exist_ok=True)

    ramdisk_root = tmp_path / "runtime-root"
    ramdisk_root.mkdir(parents=True, exist_ok=True)

    target_cache_root = build_cache_root(str(ramdisk_root), str(target_workspace))
    stale_cache_root = build_cache_root(str(ramdisk_root), str(stale_workspace))

    _write_pm_contract(
        target_workspace,
        target_cache_root,
        run_id="pm-target",
        task_id="task-target",
    )
    _write_pm_contract(
        stale_workspace,
        stale_cache_root,
        run_id="pm-stale",
        task_id="task-stale",
    )

    # Simulate stale in-memory settings workspace while websocket context has target workspace.
    settings = Settings(
        workspace=str(stale_workspace),
        ramdisk_root=str(ramdisk_root),
        json_log_path="runtime/events/pm.events.jsonl",
    )
    state = SimpleNamespace(settings=settings, last_pm_payload=None)

    payload = build_status_payload_sync(
        state,
        workspace=str(target_workspace),
        cache_root=target_cache_root,
        pm_status={"running": True},
        director_status={"running": False},
    )

    snapshot = payload.get("snapshot")
    assert isinstance(snapshot, dict)
    assert snapshot.get("run_id") == "pm-target"
    tasks = snapshot.get("tasks")
    assert isinstance(tasks, list) and tasks
    assert str(tasks[0].get("id") or "").strip() == "task-target"


def test_status_payload_llm_status_uses_ws_workspace_context(tmp_path: Path) -> None:
    target_workspace = tmp_path / "target-workspace"
    stale_workspace = tmp_path / "stale-workspace"
    target_workspace.mkdir(parents=True, exist_ok=True)
    stale_workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        workspace=str(stale_workspace),
        ramdisk_root=str(tmp_path / "runtime-root"),
        json_log_path="runtime/events/pm.events.jsonl",
    )
    state = SimpleNamespace(settings=settings, last_pm_payload=None)

    with (
        patch(
            "polaris.cells.runtime.projection.internal.status_snapshot_builder.build_llm_status",
            return_value={"state": "READY", "blocked_roles": []},
        ) as mock_llm_status,
        patch(
            "polaris.cells.runtime.projection.internal.status_snapshot_builder.build_snapshot",
            return_value={"ok": True},
        ),
        patch(
            "polaris.cells.runtime.projection.internal.status_snapshot_builder.build_resident_state",
            return_value={},
        ),
        patch(
            "polaris.cells.runtime.projection.internal.status_snapshot_builder.get_lancedb_status",
            return_value={},
        ),
        patch(
            "polaris.cells.runtime.projection.internal.status_snapshot_builder.build_memory_payload",
            return_value=None,
        ),
        patch(
            "polaris.cells.runtime.projection.internal.status_snapshot_builder.build_success_stats_payload",
            return_value={},
        ),
        patch(
            "polaris.cells.runtime.projection.internal.status_snapshot_builder._build_anthro_state",
            return_value=None,
        ),
        patch(
            "polaris.cells.runtime.projection.internal.status_snapshot_builder.map_engine_to_court_state",
            return_value={},
        ),
    ):
        payload = build_status_payload_sync(
            state,
            workspace=str(target_workspace),
            cache_root=str(tmp_path / "cache-root"),
            pm_status={"running": False},
            director_status={"running": False},
        )

    called_settings = mock_llm_status.call_args.args[0]
    assert str(called_settings.workspace) == str(target_workspace)
    assert str(settings.workspace) == str(stale_workspace)
    assert payload["llm_status"] == {"state": "READY", "blocked_roles": []}
