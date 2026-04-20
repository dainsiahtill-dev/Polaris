"""Tests for SessionArtifactStore.store_structured_findings() (Phase 1.5 #4).

验证：
1. Happy Path: 正常存储 findings
2. Empty findings: 空字典返回 stored=False
3. Source turn ID: 文件名包含 turn_id
4. Artifact map: get_artifact_map() 返回正确
5. Multiple stores: 不同 turn_id 的文件共存
6. Overwrite: 同一 turn_id 存储两次，文件被覆盖
7. Schema version: 版本号正确写入 JSON
8. Derived memory: JSON 中包含 derived=True, rebuildable=True
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.session_artifact_store import (
    SessionArtifactStore,
)


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Provide a temporary workspace directory."""
    return tmp_path


class TestStoreStructuredFindings:
    """store_structured_findings() 测试。"""

    @pytest.mark.asyncio
    async def test_happy_path(self, tmp_workspace: Path) -> None:
        """正常存储 findings，验证返回值、文件存在和内容。"""
        store = SessionArtifactStore(workspace=str(tmp_workspace), session_id="s1")
        findings: dict[str, Any] = {
            "confirmed_facts": ["fact1"],
            "suspected_files": ["src/main.py"],
        }

        result = await store.store_structured_findings(
            findings=findings,
            source_turn_id="t1",
            schema_version="1.0",
        )

        assert result["stored"] is True
        assert result["finding_count"] == 2
        assert result["path"].endswith("structured_findings_t1.json")

        # 验证文件存在且 JSON 结构正确
        memory_path = Path(result["path"])
        assert memory_path.exists()
        data = json.loads(memory_path.read_text(encoding="utf-8"))
        assert data["derived"] is True
        assert data["rebuildable"] is True
        assert data["schema_version"] == "1.0"
        assert data["source_turn_id"] == "t1"
        assert data["findings"] == findings
        assert "stored_at" in data

    @pytest.mark.asyncio
    async def test_empty_findings_returns_not_stored(self, tmp_workspace: Path) -> None:
        """空 findings 返回 stored=False，不创建文件。"""
        store = SessionArtifactStore(workspace=str(tmp_workspace), session_id="s1")

        result = await store.store_structured_findings(findings={})

        assert result == {"stored": False, "path": "", "finding_count": 0}

    @pytest.mark.asyncio
    async def test_default_source_turn_id(self, tmp_workspace: Path) -> None:
        """未提供 source_turn_id 时使用 'latest'。"""
        store = SessionArtifactStore(workspace=str(tmp_workspace), session_id="s1")

        result = await store.store_structured_findings(findings={"a": "b"})

        assert "structured_findings_latest.json" in result["path"]

    @pytest.mark.asyncio
    async def test_artifact_map_updated(self, tmp_workspace: Path) -> None:
        """存储后 artifact_map 包含 derived_memory 条目。"""
        store = SessionArtifactStore(workspace=str(tmp_workspace), session_id="s1")
        findings = {"confirmed_facts": ["fact1"]}

        await store.store_structured_findings(
            findings=findings,
            source_turn_id="t1",
        )

        artifact_map = store.get_artifact_map()
        assert "structured_findings_t1" in artifact_map
        assert artifact_map["structured_findings_t1"]["type"] == "derived_memory"
        assert artifact_map["structured_findings_t1"]["rebuildable"] is True
        assert artifact_map["structured_findings_t1"]["finding_count"] == 1

    @pytest.mark.asyncio
    async def test_multiple_different_turn_ids(self, tmp_workspace: Path) -> None:
        """不同 turn_id 的存储互不覆盖，文件共存。"""
        store = SessionArtifactStore(workspace=str(tmp_workspace), session_id="s1")

        await store.store_structured_findings(
            findings={"turn": "1"},
            source_turn_id="t1",
        )
        await store.store_structured_findings(
            findings={"turn": "2"},
            source_turn_id="t2",
        )

        cache_dir = tmp_workspace / ".polaris" / "artifacts" / "s1"
        assert (cache_dir / "structured_findings_t1.json").exists()
        assert (cache_dir / "structured_findings_t2.json").exists()

        artifact_map = store.get_artifact_map()
        assert "structured_findings_t1" in artifact_map
        assert "structured_findings_t2" in artifact_map

    @pytest.mark.asyncio
    async def test_overwrite_same_turn_id(self, tmp_workspace: Path) -> None:
        """同一 turn_id 存储两次，第二次覆盖第一次。"""
        store = SessionArtifactStore(workspace=str(tmp_workspace), session_id="s1")

        await store.store_structured_findings(
            findings={"version": "1"},
            source_turn_id="t1",
        )
        await store.store_structured_findings(
            findings={"version": "2"},
            source_turn_id="t1",
        )

        # 文件应该只存在一个
        cache_dir = tmp_workspace / ".polaris" / "artifacts" / "s1"
        json_files = list(cache_dir.glob("structured_findings_t1.json"))
        assert len(json_files) == 1

        # 内容应该是第二次的
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        assert data["findings"]["version"] == "2"

        # artifact_map 只保留最新的（因为同一 key 覆盖）
        artifact_map = store.get_artifact_map()
        assert len([k for k in artifact_map if k.startswith("structured_findings_t1")]) == 1

    @pytest.mark.asyncio
    async def test_schema_version_passthrough(self, tmp_workspace: Path) -> None:
        """schema_version 正确写入 JSON。"""
        store = SessionArtifactStore(workspace=str(tmp_workspace), session_id="s1")

        await store.store_structured_findings(
            findings={"a": "b"},
            source_turn_id="t1",
            schema_version="2.0",
        )

        cache_dir = tmp_workspace / ".polaris" / "artifacts" / "s1"
        data = json.loads((cache_dir / "structured_findings_t1.json").read_text(encoding="utf-8"))
        assert data["schema_version"] == "2.0"

    @pytest.mark.asyncio
    async def test_derived_memory_flags(self, tmp_workspace: Path) -> None:
        """JSON 中必须包含 derived=True 和 rebuildable=True。"""
        store = SessionArtifactStore(workspace=str(tmp_workspace), session_id="s1")

        await store.store_structured_findings(
            findings={"fact": "x"},
            source_turn_id="t1",
        )

        cache_dir = tmp_workspace / ".polaris" / "artifacts" / "s1"
        data = json.loads((cache_dir / "structured_findings_t1.json").read_text(encoding="utf-8"))
        assert data["derived"] is True
        assert data["rebuildable"] is True

    @pytest.mark.asyncio
    async def test_findings_deep_copy(self, tmp_workspace: Path) -> None:
        """原始 findings 字典不应被修改。"""
        store = SessionArtifactStore(workspace=str(tmp_workspace), session_id="s1")
        findings = {"list": ["a", "b"]}

        await store.store_structured_findings(findings=findings, source_turn_id="t1")

        # 原始数据不应被修改
        assert findings == {"list": ["a", "b"]}
