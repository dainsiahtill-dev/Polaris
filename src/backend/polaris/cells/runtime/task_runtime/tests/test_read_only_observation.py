"""Historical observation stays in the task-runtime owner without disk effects."""

from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.runtime.task_runtime.internal.task_board import TaskBoard
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import set_default_adapter
from polaris.kernelone.storage import clear_storage_roots_cache, resolve_runtime_path


@pytest.fixture
def isolated_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Path]]:
    workspace = tmp_path / "workspace"
    private_home = tmp_path / "private-khome"
    workspace.mkdir()
    private_home.mkdir()
    for key in ("KERNELONE_RUNTIME_ROOT", "KERNELONE_RUNTIME_CACHE_ROOT", "KERNELONE_RAMDISK_ROOT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {
        "KERNELONE_HOME": private_home,
        "POLARIS_HOME": private_home,
        "KERNELONE_WORKSPACE": workspace,
        "KERNELONE_CONTEXT_ROOT": workspace,
        "KERNELONE_STATE_TO_RAMDISK": "0",
        "KERNELONE_SELF_UPGRADE_MODE": "0",
        "KERNELONE_NATS_ENABLED": "false",
        "KERNELONE_NATS_REQUIRED": "false",
    }.items():
        monkeypatch.setenv(key, str(value))
    clear_storage_roots_cache()
    set_default_adapter(LocalFileSystemAdapter())
    yield workspace, private_home
    clear_storage_roots_cache()


def _snapshot(*roots: Path) -> dict[str, tuple[int, str]]:
    return {
        f"{index}:{path.relative_to(root)}": (
            path.stat().st_mtime_ns,
            sha256(path.read_bytes()).hexdigest() if path.is_file() else "directory",
        )
        for index, root in enumerate(roots)
        for path in (root, *root.rglob("*"))
    }


def _writer(workspace: Path) -> TaskRuntimeService:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace), streams=fact_stream_bootstrap_streams(), maintenance_reason="readonly-history-fixture"
        )
    )
    return TaskRuntimeService(str(workspace))


def test_cold_observer_creates_no_directories_files_or_home_state(isolated_history: tuple[Path, Path]) -> None:
    workspace, private_home = isolated_history
    before = _snapshot(workspace, private_home)
    observer = TaskRuntimeService(str(workspace), read_only=True)
    assert observer.list_observable_task_rows() == []
    projection = observer.query_observable_task_rows_projection()
    assert projection.workspace == str(workspace)
    assert projection.rows == ()
    assert observer.get_task("missing") is None
    assert _snapshot(workspace, private_home) == before


def test_populated_history_observer_preserves_rows_sessions_facts_and_locks(
    isolated_history: tuple[Path, Path],
) -> None:
    workspace, private_home = isolated_history
    writer = _writer(workspace)
    row = writer.create_task_row(subject="Historical task", metadata={"history_marker": "preserve"})
    identity = row["id"]
    claimed = writer.claim_execution(identity, worker_id="history-fixture", role_id="director", run_id="history-run")
    assert claimed["success"] is True
    lock = Path(resolve_runtime_path(str(workspace), f"runtime/tasks/.task_{identity}.session.json.lock"))
    lock.unlink(missing_ok=True)
    clear_storage_roots_cache()
    before = _snapshot(workspace, private_home)
    observer = TaskRuntimeService(str(workspace), read_only=True)
    rows = observer.list_observable_task_rows()
    selected = next(item for item in rows if str(item["id"]) == str(identity))
    assert selected["metadata"]["history_marker"] == "preserve"
    assert selected["execution_state"] == "in_progress"
    projection = observer.query_observable_task_rows_projection()
    assert next(item for item in projection.rows if str(item["id"]) == str(identity))["execution_state"] == "in_progress"
    assert observer.get_task(identity)["execution_state"] == "in_progress"
    assert _snapshot(workspace, private_home) == before
    assert not lock.exists()


def test_normal_owner_writes_and_fresh_readonly_observation_advances(isolated_history: tuple[Path, Path]) -> None:
    workspace, private_home = isolated_history
    writer = _writer(workspace)
    row = writer.create_task_row(subject="Writable owner", metadata={"revision": 1})
    first = TaskRuntimeService(str(workspace), read_only=True)
    assert first.get_task(row["id"])["metadata"]["revision"] == 1
    writer.update_task_row(row["id"], metadata={"revision": 2})
    before = _snapshot(workspace, private_home)
    assert first.get_task(row["id"])["metadata"]["revision"] == 2
    current = TaskRuntimeService(str(workspace), read_only=True)
    assert current.get_task(row["id"])["metadata"]["revision"] == 2
    assert _snapshot(workspace, private_home) == before


@pytest.mark.parametrize("operation", ["create", "update", "claim", "reset", "refresh_dependencies"])
def test_readonly_mutation_rejects_before_any_file_or_lock_effect(
    isolated_history: tuple[Path, Path], operation: str
) -> None:
    workspace, private_home = isolated_history
    observer = TaskRuntimeService(str(workspace), read_only=True)
    before = _snapshot(workspace, private_home)
    with pytest.raises(PermissionError, match="read-only"):
        if operation == "create":
            observer.create_task_row(subject="must not exist")
        elif operation == "update":
            observer.update_task_row(1, metadata={"must_not": "persist"})
        elif operation == "claim":
            observer.claim_execution(1, worker_id="forbidden", role_id="director")
        elif operation == "reset":
            observer.reset_records()
        else:
            observer.refresh_dependency_unblocks()
    assert _snapshot(workspace, private_home) == before


def test_readonly_board_cannot_create_rows_and_writable_board_cannot_be_injected(
    isolated_history: tuple[Path, Path],
) -> None:
    workspace, private_home = isolated_history
    before = _snapshot(workspace, private_home)
    board = TaskBoard(str(workspace), read_only=True)
    with pytest.raises(PermissionError, match="read-only"):
        board.create(subject="forbidden")
    assert _snapshot(workspace, private_home) == before
    writable = TaskBoard(str(workspace))
    with pytest.raises(ValueError, match="read-only"):
        TaskRuntimeService(str(workspace), board=writable, read_only=True)


@pytest.mark.parametrize("corrupt", ["row", "session"])
def test_readonly_history_surfaces_corrupt_data_without_repair_or_drop(
    isolated_history: tuple[Path, Path], corrupt: str
) -> None:
    workspace, private_home = isolated_history
    writer = _writer(workspace)
    row = writer.create_task_row(subject="Corrupt history must remain visible as failure")
    if corrupt == "row":
        path = Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{row['id']}.json"))
    else:
        claimed = writer.claim_execution(row["id"], worker_id="history-fixture", role_id="director")
        assert claimed["success"] is True
        path = Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{row['id']}.session.json"))
    path.write_text("{corrupt historical data", encoding="utf-8")
    before = _snapshot(workspace, private_home)
    with pytest.raises(ValueError):
        TaskRuntimeService(str(workspace), read_only=True).list_observable_task_rows()
    assert _snapshot(workspace, private_home) == before
