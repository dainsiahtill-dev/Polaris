"""Tests for RollbackGuard and GitStashRollbackGuard."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from polaris.cells.chief_engineer.blueprint.internal import rollback_guard as rollback_guard_module
from polaris.cells.chief_engineer.blueprint.internal.rollback_guard import (
    GitStashRollbackGuard,
    RollbackGuard,
    create_rollback_guard,
)


@pytest.mark.anyio
async def test_snapshot_and_rollback_restores_content(tmp_path: Path) -> None:
    guard = RollbackGuard(str(tmp_path))
    file_path = "test_file.txt"
    original_content = "original content"
    (tmp_path / file_path).write_text(original_content, encoding="utf-8")

    await guard.snapshot_for_director("d1", [file_path])
    (tmp_path / file_path).write_text("modified content", encoding="utf-8")

    success = await guard.rollback_director("d1")
    assert success is True
    restored = (tmp_path / file_path).read_text(encoding="utf-8")
    assert restored == original_content
    assert guard.has_snapshot("d1") is False


@pytest.mark.anyio
async def test_snapshot_and_rollback_deletes_new_file(tmp_path: Path) -> None:
    guard = RollbackGuard(str(tmp_path))
    file_path = "new_file.txt"

    await guard.snapshot_for_director("d1", [file_path])
    (tmp_path / file_path).write_text("new content", encoding="utf-8")

    success = await guard.rollback_director("d1")
    assert success is True
    assert (tmp_path / file_path).exists() is False
    assert guard.has_snapshot("d1") is False


@pytest.mark.anyio
async def test_discard_snapshot_prevents_rollback(tmp_path: Path) -> None:
    guard = RollbackGuard(str(tmp_path))
    file_path = "test_file.txt"
    original_content = "original content"
    (tmp_path / file_path).write_text(original_content, encoding="utf-8")

    await guard.snapshot_for_director("d1", [file_path])
    guard.discard_snapshot("d1")

    (tmp_path / file_path).write_text("modified content", encoding="utf-8")
    success = await guard.rollback_director("d1")
    assert success is False
    assert (tmp_path / file_path).read_text(encoding="utf-8") == "modified content"


def test_git_stash_guard_requires_git_repo(tmp_path: Path) -> None:
    guard = GitStashRollbackGuard(str(tmp_path))
    result = guard.create_snapshot("task-1")
    assert result is None


def test_create_rollback_guard_factory() -> None:
    guard_memory = create_rollback_guard("/tmp", use_memory_snapshots=True)
    guard_serial = create_rollback_guard("/tmp", use_memory_snapshots=False)
    assert isinstance(guard_memory, RollbackGuard)
    assert isinstance(guard_serial, GitStashRollbackGuard)


def test_git_stash_guard_full_cycle(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    file_path = tmp_path / "tracked.txt"
    file_path.write_text("original", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True, check=True)

    guard = GitStashRollbackGuard(str(tmp_path))
    file_path.write_text("modified", encoding="utf-8")

    snapshot_name = guard.create_snapshot("task-123")
    assert snapshot_name is not None
    assert file_path.read_text(encoding="utf-8") == "original"

    success = guard.rollback("task-123")
    assert success is True
    assert file_path.read_text(encoding="utf-8") == "modified"

    success_drop = guard.discard_snapshot("task-123")
    assert success_drop is False


def test_git_stash_guard_discard_missing(tmp_path: Path) -> None:
    guard = GitStashRollbackGuard(str(tmp_path))
    assert guard.discard_snapshot("nonexistent") is False
    assert guard.rollback("nonexistent") is False


@pytest.mark.anyio
async def test_rollback_guard_missing_file_during_snapshot(tmp_path: Path) -> None:
    guard = RollbackGuard(str(tmp_path))
    file_path = "missing.txt"
    await guard.snapshot_for_director("d1", [file_path])
    assert guard.has_snapshot("d1") is True
    success = await guard.rollback_director("d1")
    assert success is True
    assert (tmp_path / file_path).exists() is False


@pytest.mark.anyio
async def test_rollback_guard_creates_parent_dirs(tmp_path: Path) -> None:
    guard = RollbackGuard(str(tmp_path))
    file_path = "nested/dir/file.txt"
    nested_file = tmp_path / file_path
    nested_file.parent.mkdir(parents=True, exist_ok=True)
    nested_file.write_text("original", encoding="utf-8")

    await guard.snapshot_for_director("d1", [file_path])
    nested_file.unlink()
    nested_file.parent.rmdir()

    success = await guard.rollback_director("d1")
    assert success is True
    assert nested_file.read_text(encoding="utf-8") == "original"


@pytest.mark.anyio
async def test_path_traversal_blocked(tmp_path: Path) -> None:
    guard = RollbackGuard(str(tmp_path))
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    await guard.snapshot_for_director("d1", ["../outside.txt"])
    assert guard.has_snapshot("d1") is True
    assert (tmp_path / "../outside.txt").resolve().read_text(encoding="utf-8") == "secret"

    await guard.rollback_director("d1")
    assert outside.read_text(encoding="utf-8") == "secret"
    outside.unlink()


@pytest.mark.anyio
async def test_git_stash_includes_untracked_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("original tracked", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True, check=True)

    untracked = tmp_path / "untracked.txt"
    untracked.write_text("original untracked", encoding="utf-8")

    guard = GitStashRollbackGuard(str(tmp_path))
    untracked.write_text("modified untracked", encoding="utf-8")

    snapshot_name = guard.create_snapshot("task-untracked")
    assert snapshot_name is not None
    assert not untracked.exists()

    success = guard.rollback("task-untracked")
    assert success is True
    assert untracked.read_text(encoding="utf-8") == "modified untracked"


@pytest.mark.anyio
async def test_snapshot_offloads_read_via_to_thread(tmp_path: Path) -> None:
    # Arrange
    guard = RollbackGuard(str(tmp_path))
    file_path = "offloaded.txt"
    (tmp_path / file_path).write_text("payload", encoding="utf-8")

    # Act
    with patch.object(
        rollback_guard_module.asyncio,
        "to_thread",
        wraps=rollback_guard_module.asyncio.to_thread,
    ) as spy:
        await guard.snapshot_for_director("d1", [file_path])

    # Assert
    assert spy.await_count == 1
    assert spy.await_args is not None
    assert spy.await_args.args[0] is rollback_guard_module._read_file_text
    assert guard.has_snapshot("d1") is True


@pytest.mark.anyio
async def test_rollback_offloads_restore_via_to_thread(tmp_path: Path) -> None:
    # Arrange
    guard = RollbackGuard(str(tmp_path))
    file_path = "restore_me.txt"
    (tmp_path / file_path).write_text("original", encoding="utf-8")
    await guard.snapshot_for_director("d1", [file_path])
    (tmp_path / file_path).write_text("changed", encoding="utf-8")

    # Act
    with patch.object(
        rollback_guard_module.asyncio,
        "to_thread",
        wraps=rollback_guard_module.asyncio.to_thread,
    ) as spy:
        success = await guard.rollback_director("d1")

    # Assert
    assert success is True
    assert spy.await_count == 1
    assert spy.await_args is not None
    assert spy.await_args.args[0] is rollback_guard_module._restore_file
    assert (tmp_path / file_path).read_text(encoding="utf-8") == "original"


@pytest.mark.anyio
async def test_rollback_swallows_offloaded_restore_error(tmp_path: Path) -> None:
    # Arrange
    guard = RollbackGuard(str(tmp_path))
    file_path = "boom.txt"
    (tmp_path / file_path).write_text("original", encoding="utf-8")
    await guard.snapshot_for_director("d1", [file_path])

    # Act
    with patch.object(
        rollback_guard_module,
        "_restore_file",
        side_effect=OSError("disk full"),
    ):
        success = await guard.rollback_director("d1")

    # Assert
    assert success is True
    assert guard.has_snapshot("d1") is False


def test_restore_file_removes_missing_snapshot(tmp_path: Path) -> None:
    # Arrange
    target = tmp_path / "to_delete.txt"
    target.write_text("present", encoding="utf-8")

    # Act
    rollback_guard_module._restore_file(target, None)

    # Assert
    assert target.exists() is False


def test_read_file_text_uses_utf8(tmp_path: Path) -> None:
    # Arrange
    target = tmp_path / "unicode.txt"
    target.write_text("汉字", encoding="utf-8")

    # Act
    result = rollback_guard_module._read_file_text(target)

    # Assert
    assert result == "汉字"
