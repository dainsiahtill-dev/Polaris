"""Session Artifact Store - 增量 Patch 版 Artifact 存储与 ContextOS 去重压缩。

解决 ContextOS 运行产物中反复压缩相同上下文导致的重复噪音问题。
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any


class SessionArtifactStore:
    """会话级 Artifact 存储，支持增量 Patch 和 ContextOS 去重压缩。

    Args:
        workspace: 工作区根目录。
        session_id: 会话唯一标识。
    """

    def __init__(self, workspace: str, session_id: str) -> None:
        self.session_id = session_id
        self._cache_dir = Path(workspace) / ".polaris" / "artifacts" / session_id
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._hash_to_content: dict[str, str] = {}
        self._artifact_map: dict[str, Any] = {}

    async def persist(self, artifacts: list[dict[str, Any]]) -> dict[str, int]:
        """持久化 Artifact 列表，自动做增量 diff 和去重压缩触发。"""
        persisted_count = 0
        compressed_count = 0
        skipped_count = 0
        for artifact in artifacts:
            content = artifact.get("content", "")
            artifact_id = str(artifact.get("artifact_id") or artifact.get("path") or artifact.get("name") or "").strip()

            if not artifact_id or content == "":
                skipped_count += 1
                continue

            persisted_count += 1
            orig_hash = str(artifact.get("original_hash") or "").strip()

            self._artifact_map[artifact_id] = {
                "original_hash": orig_hash,
                "timestamp": artifact.get("timestamp"),
                "type": artifact.get("type"),
            }

            is_new_hash = orig_hash not in self._hash_to_content
            if not is_new_hash and isinstance(content, str):
                old_content = self._hash_to_content[orig_hash]
                if isinstance(old_content, str):
                    delta = "\n".join(
                        difflib.unified_diff(
                            old_content.splitlines(),
                            content.splitlines(),
                            lineterm="",
                        )
                    )
                    await self._save_delta(orig_hash, delta)
            else:
                if isinstance(content, str):
                    self._hash_to_content[orig_hash] = content
                await self._save_full(orig_hash, artifact)

            needs_recompress = bool(artifact.get("needs_recompress", False))
            if needs_recompress or is_new_hash:
                compressed_count += 1
                await self._trigger_incremental_compress(orig_hash, artifact)
            else:
                skipped_count += 1

        return {
            "persisted_count": persisted_count,
            "compressed_count": compressed_count,
            "skipped_count": skipped_count,
        }

    async def _save_full(self, orig_hash: str, artifact: dict[str, Any]) -> None:
        path = self._cache_dir / f"{orig_hash}_full.json"
        await _async_write_text(path, json.dumps(artifact, ensure_ascii=False, default=str))

    async def _save_delta(self, orig_hash: str, delta: str) -> None:
        path = self._cache_dir / f"{orig_hash}_delta.patch"
        await _async_write_text(path, delta + "\n---END_DELTA---\n", mode="a")

    async def _trigger_incremental_compress(self, orig_hash: str, artifact: dict[str, Any]) -> None:
        try:
            from polaris.kernelone.context.context_os import compress_if_changed  # type: ignore[attr-defined]

            await compress_if_changed(
                session_id=self.session_id,
                original_hash=orig_hash,
                artifact=artifact,
            )
        except ImportError:
            pass

    def get_artifact_map(self) -> dict[str, Any]:
        """返回当前已聚合的 artifact 元数据映射。"""
        return dict(self._artifact_map)

    async def store_structured_findings(
        self,
        findings: dict[str, Any],
        *,
        source_turn_id: str = "",
        schema_version: str = "1.0",
    ) -> dict[str, Any]:
        """存储结构化发现物作为派生记忆（derived memory）。

        Phase 1.5: 将 structured_findings 持久化到 artifact store，
        标记为 derived_memory（非独立 truth source，可从 truthlog 重建）。

        Args:
            findings: 结构化发现物字典（confirmed_facts, rejected_hypotheses 等）
            source_turn_id: 来源 turn ID，用于追溯
            schema_version: 发现物 schema 版本

        Returns:
            存储结果元数据 {"stored": bool, "path": str, "finding_count": int}
        """
        if not findings:
            return {"stored": False, "path": "", "finding_count": 0}

        # 构建派生记忆记录
        derived_memory = {
            "derived": True,
            "rebuildable": True,
            "schema_version": schema_version,
            "source_turn_id": source_turn_id,
            "stored_at": _iso_timestamp(),
            "findings": dict(findings),
        }

        # 写入派生记忆文件
        memory_id = f"structured_findings_{source_turn_id or 'latest'}"
        memory_path = self._cache_dir / f"{memory_id}.json"

        await _async_write_text(
            memory_path,
            json.dumps(derived_memory, ensure_ascii=False, default=str),
        )

        # 更新 artifact 映射
        self._artifact_map[memory_id] = {
            "type": "derived_memory",
            "rebuildable": True,
            "path": str(memory_path),
            "finding_count": len(findings),
        }

        return {
            "stored": True,
            "path": str(memory_path),
            "finding_count": len(findings),
        }


async def _async_write_text(path: Path, content: str, mode: str = "w") -> None:
    try:
        import aiofiles

        async with aiofiles.open(path, mode, encoding="utf-8") as handle:
            await handle.write(content)
    except ImportError:
        with open(path, mode, encoding="utf-8") as handle:
            handle.write(content)


def _iso_timestamp() -> str:
    """返回当前 UTC 时间的 ISO 8601 格式字符串。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
