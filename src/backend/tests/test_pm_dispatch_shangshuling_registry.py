"""Regression tests for the cell-local Shangshuling registry port."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry import (
    LocalShangshulingPort,
    get_shangshuling_port,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_get_shangshuling_port_returns_local_port() -> None:
    port = get_shangshuling_port()
    assert isinstance(port, LocalShangshulingPort)


def test_local_shangshuling_registry_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    port = LocalShangshulingPort()
    registry_root = tmp_path / "registry"
    registry_root.mkdir()

    def _resolve_runtime_path(workspace_full: str, rel_path: str) -> str:
        _ = workspace_full
        return str(registry_root / rel_path)

    def _write_json_atomic(path: str, data: object) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_jsonl(path: str, record: object) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry.resolve_runtime_path",
        _resolve_runtime_path,
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry.write_json_atomic",
        _write_json_atomic,
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry.append_jsonl",
        _append_jsonl,
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry.read_file_safe",
        lambda path: Path(path).read_text(encoding="utf-8") if Path(path).is_file() else "",
    )

    tasks = [
        {"id": "T-2", "status": "done", "priority": 9, "metadata": {"legacy_id": "L-2"}},
        {"id": "T-1", "status": "todo", "priority": 1, "metadata": {"legacy_id": "L-1"}},
        {"id": "T-3", "status": "failed", "priority": "high"},
    ]

    synced = port.sync_tasks_to_shangshuling(str(workspace), tasks)
    assert synced == 3

    registry_path = registry_root / "runtime" / "state" / "dispatch" / "shangshuling.registry.json"
    assert registry_path.is_file()
    registry = _read_json(registry_path)
    assert registry["workspace"] == str(workspace)
    assert len(registry["tasks"]) == 3

    ready = port.get_shangshuling_ready_tasks(str(workspace))
    assert [item["id"] for item in ready] == ["T-1"]

    assert port.record_shangshuling_task_completion(
        str(workspace),
        "T-1",
        True,
        {"note": "completed"},
    ) is True

    updated = _read_json(registry_path)
    task_1 = next(item for item in updated["tasks"] if item["id"] == "T-1")
    assert task_1["status"] == "done"
    assert task_1["metadata"]["note"] == "completed"

    history_path = registry_root / "runtime" / "state" / "dispatch" / "shangshuling.history.jsonl"
    port.archive_task_history(
        str(workspace),
        str(workspace / "cache"),
        "run-1",
        1,
        {"tasks": tasks},
        {"status": "ok"},
        "2026-03-22T12:00:00Z",
    )
    assert history_path.is_file()
    history_line = history_path.read_text(encoding="utf-8").strip()
    assert history_line
    history_record = json.loads(history_line)
    assert history_record["run_id"] == "run-1"
    assert history_record["director_result"]["status"] == "ok"
