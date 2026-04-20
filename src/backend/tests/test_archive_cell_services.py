from __future__ import annotations

import json
from pathlib import Path

from polaris.cells.archive.factory_archive.public.service import (
    archive_factory_run,
    get_factory_manifest,
    list_factory_runs,
)
from polaris.cells.archive.run_archive.public.service import (
    archive_run,
    get_run_manifest,
    list_history_runs,
)
from polaris.cells.archive.task_snapshot_archive.public.service import (
    archive_task_snapshot,
    get_task_snapshot_manifest,
    list_task_snapshots,
)
from polaris.cells.storage.layout.public.service import resolve_polaris_roots as resolve_storage_roots


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_run_archive_public_service_roundtrip(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    roots = resolve_storage_roots(workspace)
    run_id = "run-archive-001"
    _write_json(
        Path(roots.runtime_root) / "runs" / run_id / "results" / "director.result.json",
        {"status": "completed"},
    )

    manifest = archive_run(workspace, run_id, reason="completed", status="completed")
    assert manifest["scope"] == "run"
    assert manifest["id"] == run_id

    runs = list_history_runs(workspace, limit=20, offset=0)
    assert any(str(item.get("run_id") or "") == run_id for item in runs)

    loaded = get_run_manifest(workspace, run_id)
    assert loaded is not None
    assert loaded["id"] == run_id


def test_task_snapshot_archive_public_service_roundtrip(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    roots = resolve_storage_roots(workspace)
    snapshot_id = "pm-00001-20260322010101"
    tasks_dir = Path(roots.runtime_root) / "tasks"
    _write_json(tasks_dir / "task_1.json", {"id": "task-1", "title": "demo"})
    _write_json(tasks_dir / "plan.json", {"tasks": [{"id": "task-1"}]})

    manifest = archive_task_snapshot(
        workspace=workspace,
        snapshot_id=snapshot_id,
        source_tasks_dir=str(tasks_dir),
        source_plan_path=str(tasks_dir / "plan.json"),
        reason="completed",
    )
    assert manifest["scope"] == "task_snapshot"
    assert manifest["id"] == snapshot_id

    snapshots = list_task_snapshots(workspace, limit=20, offset=0)
    assert any(str(item.get("snapshot_id") or "") == snapshot_id for item in snapshots)

    loaded = get_task_snapshot_manifest(workspace, snapshot_id)
    assert loaded is not None
    assert loaded["id"] == snapshot_id


def test_factory_archive_public_service_roundtrip(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    roots = resolve_storage_roots(workspace)
    factory_run_id = "factory-001"
    source_dir = Path(roots.workspace_persistent_root) / "factory" / factory_run_id
    _write_json(source_dir / "config.json", {"name": "factory"})

    manifest = archive_factory_run(
        workspace=workspace,
        factory_run_id=factory_run_id,
        source_factory_dir=str(source_dir),
        reason="completed",
    )
    assert manifest["scope"] == "factory_run"
    assert manifest["id"] == factory_run_id

    factory_runs = list_factory_runs(workspace, limit=20, offset=0)
    assert any(str(item.get("factory_run_id") or "") == factory_run_id for item in factory_runs)

    loaded = get_factory_manifest(workspace, factory_run_id)
    assert loaded is not None
    assert loaded["id"] == factory_run_id
