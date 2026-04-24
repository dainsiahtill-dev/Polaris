"""Tests for SessionArtifactStore."""

import json
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.internal.session_artifact_store import SessionArtifactStore


class TestSessionArtifactStore:
    """测试 SessionArtifactStore 的增量持久化和 diff 行为。"""

    @pytest.fixture
    def tmp_workspace(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return str(workspace)

    @pytest.fixture
    def store(self, tmp_workspace):
        return SessionArtifactStore(workspace=tmp_workspace, session_id="sess-1")

    @pytest.mark.asyncio
    async def test_persist_single_artifact(self, store, tmp_workspace):
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
    async def test_persist_multiple_artifacts(self, store, tmp_workspace):
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
    async def test_persist_ignores_missing_content(self, store):
        artifacts = [
            {"name": "empty.txt"},
            {"name": "valid.txt", "content": "ok", "mime_type": "text/plain", "original_hash": "hash-v"},
        ]
        result = await store.persist(artifacts)
        assert result["persisted_count"] == 1
        assert result["skipped_count"] == 1

    @pytest.mark.asyncio
    async def test_persist_with_original_hash_and_compression(self, store, tmp_workspace):
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
    async def test_get_artifact_map_after_persist(self, store, tmp_workspace):
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
    async def test_delta_produced_for_same_hash(self, store, tmp_workspace):
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
