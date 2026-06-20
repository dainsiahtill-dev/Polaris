"""Tests for SessionArtifactStore."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from polaris.cells.roles.runtime.internal.session_artifact_errors import ArtifactPersistError
from polaris.cells.roles.runtime.internal.session_artifact_store import (
    SessionArtifactStore,
    _async_write_text,
)


async def _run_func_to_thread(func: Callable[..., Any], *args: Any) -> Any:
    """Synchronous stand-in for ``asyncio.to_thread`` that runs ``func`` inline.

    Lets a test assert the helper *awaited* the offload boundary without
    actually spawning a worker thread.
    """
    return func(*args)


class TestSessionArtifactStore:
    """测试 SessionArtifactStore 的增量持久化和 diff 行为。"""

    @pytest.fixture
    def tmp_workspace(self, tmp_path: Path) -> str:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return str(workspace)

    @pytest.fixture
    def store(self, tmp_workspace: str) -> SessionArtifactStore:
        return SessionArtifactStore(workspace=tmp_workspace, session_id="sess-1")

    @pytest.mark.asyncio
    async def test_persist_single_artifact(self, store: SessionArtifactStore, tmp_workspace: str) -> None:
        artifact = {
            "name": "test.txt",
            "content": "hello world",
            "mime_type": "text/plain",
            "original_hash": "hash-1",
        }
        result = await store.persist([artifact])
        assert result["persisted_count"] == 1
        assert result["compressed_count"] == 1

        artifact_dir = Path(tmp_workspace) / ".polaris" / "artifacts" / "sess-1"
        assert artifact_dir.exists()
        saved = artifact_dir / "hash-1_full.json"
        assert saved.exists()
        data = json.loads(saved.read_text(encoding="utf-8"))
        assert data["content"] == "hello world"

    @pytest.mark.asyncio
    async def test_persist_multiple_artifacts(self, store: SessionArtifactStore, tmp_workspace: str) -> None:
        artifacts = [
            {"name": "a.txt", "content": "aaa", "mime_type": "text/plain", "original_hash": "hash-a"},
            {"name": "b.json", "content": '{"key": "val"}', "mime_type": "application/json", "original_hash": "hash-b"},
        ]
        result = await store.persist(artifacts)
        assert result["persisted_count"] == 2
        assert result["compressed_count"] == 2

        artifact_dir = Path(tmp_workspace) / ".polaris" / "artifacts" / "sess-1"
        assert (artifact_dir / "hash-a_full.json").exists()
        assert (artifact_dir / "hash-b_full.json").exists()

    @pytest.mark.asyncio
    async def test_persist_ignores_missing_content(self, store: SessionArtifactStore) -> None:
        artifacts = [
            {"name": "empty.txt"},
            {"name": "valid.txt", "content": "ok", "mime_type": "text/plain", "original_hash": "hash-v"},
        ]
        result = await store.persist(artifacts)
        assert result["persisted_count"] == 1
        assert result["skipped_count"] == 1

    @pytest.mark.asyncio
    async def test_persist_with_original_hash_and_compression(
        self, store: SessionArtifactStore, tmp_workspace: str
    ) -> None:
        artifact = {
            "name": "compress.txt",
            "content": "line1\nline2\nline3\n",
            "mime_type": "text/plain",
            "original_hash": "hash-abc",
            "needs_recompress": True,
        }
        result = await store.persist([artifact])
        assert result["persisted_count"] == 1
        assert result["compressed_count"] == 1
        assert result["skipped_count"] == 0

        # 第二次持久化相同 hash 且 needs_recompress=False 应该跳过压缩
        artifact2 = {
            "name": "compress.txt",
            "content": "line1\nline2\nline3\n",
            "mime_type": "text/plain",
            "original_hash": "hash-abc",
            "needs_recompress": False,
        }
        result2 = await store.persist([artifact2])
        assert result2["skipped_count"] == 1
        assert result2["compressed_count"] == 0
        assert result2["persisted_count"] == 1

    @pytest.mark.asyncio
    async def test_get_artifact_map_after_persist(self, store: SessionArtifactStore, tmp_workspace: str) -> None:
        await store.persist(
            [
                {"name": "x.txt", "content": "x", "mime_type": "text/plain", "original_hash": "hash-x"},
                {"name": "y.txt", "content": "y", "mime_type": "text/plain", "original_hash": "hash-y"},
            ]
        )
        mapping = store.get_artifact_map()
        assert "x.txt" in mapping
        assert "y.txt" in mapping
        assert mapping["x.txt"]["original_hash"] == "hash-x"
        assert mapping["y.txt"]["original_hash"] == "hash-y"

    @pytest.mark.asyncio
    async def test_delta_produced_for_same_hash(self, store: SessionArtifactStore, tmp_workspace: str) -> None:
        artifact_dir = Path(tmp_workspace) / ".polaris" / "artifacts" / "sess-1"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        artifact1 = {
            "name": "file.txt",
            "content": "old line\n",
            "mime_type": "text/plain",
            "original_hash": "hash-diff",
        }
        await store.persist([artifact1])

        artifact2 = {
            "name": "file.txt",
            "content": "new line\n",
            "mime_type": "text/plain",
            "original_hash": "hash-diff",
        }
        result = await store.persist([artifact2])
        # same hash = not compressed (skipped), but delta saved
        assert result["persisted_count"] == 1
        assert result["compressed_count"] == 0

        delta_file = artifact_dir / "hash-diff_delta.patch"
        assert delta_file.exists()
        diff_text = delta_file.read_text(encoding="utf-8")
        assert "---" in diff_text
        assert "+++" in diff_text
        assert "---END_DELTA---" in diff_text


class TestAsyncWriteText:
    """Tests for the persistence helper `_async_write_text` (async-correctness fix)."""

    @pytest.mark.asyncio
    async def test_full_write_creates_file_with_exact_content(self, tmp_path: Path) -> None:
        # Arrange
        target = tmp_path / "nested" / "artifact_full.json"
        content = '{"k": "v", "unicode": "中文"}'

        # Act
        await _async_write_text(target, content, mode="w")

        # Assert: atomic write created the file with byte-exact content (no temp left behind)
        assert target.read_text(encoding="utf-8") == content
        assert list(target.parent.glob("*.tmp")) == []

    @pytest.mark.asyncio
    async def test_full_write_replaces_existing_content(self, tmp_path: Path) -> None:
        # Arrange
        target = tmp_path / "artifact_full.json"
        await _async_write_text(target, "old", mode="w")

        # Act
        await _async_write_text(target, "new", mode="w")

        # Assert
        assert target.read_text(encoding="utf-8") == "new"

    @pytest.mark.asyncio
    async def test_delta_append_preserves_prior_content_and_marker(self, tmp_path: Path) -> None:
        # Arrange
        target = tmp_path / "artifact_delta.patch"
        first = "diff-one\n---END_DELTA---\n"
        second = "diff-two\n---END_DELTA---\n"

        # Act: two appends in "a" mode
        await _async_write_text(target, first, mode="a")
        await _async_write_text(target, second, mode="a")

        # Assert: prior content preserved, both markers present, order kept
        text = target.read_text(encoding="utf-8")
        assert text == first + second
        assert text.count("---END_DELTA---") == 2

    @pytest.mark.asyncio
    async def test_full_write_failure_raises_artifact_persist_error(self, tmp_path: Path) -> None:
        # Arrange
        target = tmp_path / "artifact_full.json"

        # Act / Assert: an OSError from the atomic primitive surfaces as ArtifactPersistError
        with (
            patch(
                "polaris.cells.roles.runtime.internal.session_artifact_store.write_text_atomic",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(ArtifactPersistError) as exc_info,
        ):
            await _async_write_text(target, "data", mode="w")

        err = exc_info.value
        assert err.code == "ARTIFACT_PERSIST_FAILED"
        assert err.details["path"] == str(target)
        assert err.details["mode"] == "w"
        assert isinstance(err.__cause__, OSError)

    @pytest.mark.asyncio
    async def test_delta_append_failure_raises_artifact_persist_error(self, tmp_path: Path) -> None:
        # Arrange
        target = tmp_path / "artifact_delta.patch"

        # Act / Assert: an OSError during the locked append surfaces as ArtifactPersistError
        with (
            patch(
                "polaris.cells.roles.runtime.internal.session_artifact_store.os.fsync",
                side_effect=OSError("fsync failed"),
            ),
            pytest.raises(ArtifactPersistError) as exc_info,
        ):
            await _async_write_text(target, "x\n---END_DELTA---\n", mode="a")

        assert exc_info.value.details["mode"] == "a"

    @pytest.mark.asyncio
    async def test_unsupported_mode_raises_artifact_persist_error(self, tmp_path: Path) -> None:
        # Arrange
        target = tmp_path / "artifact.bin"

        # Act / Assert
        with pytest.raises(ArtifactPersistError):
            await _async_write_text(target, "x", mode="r+")

    @pytest.mark.asyncio
    async def test_full_write_offloads_to_thread(self, tmp_path: Path) -> None:
        # Arrange: prove the blocking work is offloaded off the event loop via to_thread
        target = tmp_path / "artifact_full.json"

        # Act
        with patch(
            "polaris.cells.roles.runtime.internal.session_artifact_store.asyncio.to_thread",
            side_effect=_run_func_to_thread,
        ) as to_thread_mock:
            await _async_write_text(target, "data", mode="w")

        # Assert: to_thread was awaited (event loop not blocked by the sync write)
        assert to_thread_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_delta_append_offloads_to_thread(self, tmp_path: Path) -> None:
        # Arrange
        target = tmp_path / "artifact_delta.patch"

        # Act
        with patch(
            "polaris.cells.roles.runtime.internal.session_artifact_store.asyncio.to_thread",
            side_effect=_run_func_to_thread,
        ) as to_thread_mock:
            await _async_write_text(target, "x\n---END_DELTA---\n", mode="a")

        # Assert
        assert to_thread_mock.await_count == 1
