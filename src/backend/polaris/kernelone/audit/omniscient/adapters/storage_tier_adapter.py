"""Storage Tier Adapter — Cold/Hot分层存储适配器.

热存储: 近期事件(≤hot_ttl_days) → 标准JSONL分区
冷存储: 过期事件(>hot_ttl_days) → archive/ 子目录+gzip压缩

Features:
- TTL-based自动分层
- 后台Finalize任务自动压缩并移动过期分区
- 无缝封装KernelRuntimeAdapter

Usage:
    adapter = StorageTierAdapter(
        runtime_root=Path("/tmp/audit"),
        hot_ttl_days=7,
        cold_ttl_days=90,
    )
    await adapter.start()
    await adapter.emit({"event_type": "llm_interaction", ...})
    await adapter.stop()

    # 手动触发Finalize
    await adapter.archive_old_partitions()
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import logging
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 默认TTL配置
DEFAULT_HOT_TTL_DAYS = 7
DEFAULT_COLD_TTL_DAYS = 90


class StorageTierAdapter:
    """Cold/Hot分层存储适配器.

    封装KernelRuntimeAdapter，在其基础上增加冷热分层能力:
    - 热存储: 最近hot_ttl_days的事件保留在原始JSONL分区
    - 冷存储: 超过hot_ttl_days的事件Finalize到archive/目录并gzip压缩

    Attributes:
        hot_ttl_days: 热存储保留天数 (默认7天)
        cold_ttl_days: 冷存储保留天数 (默认90天，超过则删除)
        archive_on_rotation: Finalize时是否压缩 (默认True)
    """

    def __init__(
        self,
        runtime_root: Path,
        hot_ttl_days: int = DEFAULT_HOT_TTL_DAYS,
        cold_ttl_days: int = DEFAULT_COLD_TTL_DAYS,
        archive_on_rotation: bool = True,
    ) -> None:
        """Initialize the storage tier adapter.

        Args:
            runtime_root: Root path for audit files.
            hot_ttl_days: Days to keep events in hot storage (default 7).
            cold_ttl_days: Days to keep events in cold storage (default 90).
            archive_on_rotation: Whether to gzip compress archived partitions.
        """
        self._runtime_root = Path(runtime_root)
        self._hot_ttl_days = hot_ttl_days
        self._cold_ttl_days = cold_ttl_days
        self._archive_on_rotation = archive_on_rotation

        # 内嵌的RuntimeAdapter用于热路径写入
        from polaris.kernelone.audit.omniscient.adapters.kernel_runtime_adapter import (
            KernelRuntimeAdapter,
            KernelRuntimeAdapterConfig,
        )

        self._adapter = KernelRuntimeAdapter(
            runtime_root=runtime_root,
            config=KernelRuntimeAdapterConfig(),
        )

        # 后台Finalize任务
        self._rotation_task: asyncio.Task[None] | None = None
        self._rotation_interval_hours = 6  # 每6小时检查一次
        self._running = False
        self._lock = threading.Lock()

        # 统计
        self._hot_events = 0
        self._cold_events = 0
        self._archived_partitions = 0
        self._deleted_partitions = 0

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the adapter and background rotation task."""
        await self._adapter.start()
        self._running = True
        self._rotation_task = asyncio.create_task(self._rotation_loop())
        logger.info(
            "[storage_tier] Started: hot_ttl=%dd, cold_ttl=%dd",
            self._hot_ttl_days,
            self._cold_ttl_days,
        )

    async def stop(self, timeout: float = 10.0) -> None:
        """Stop the adapter and flush remaining events.

        Args:
            timeout: Maximum seconds to wait for flush.
        """
        self._running = False

        # 先Finalize所有热分区再停止
        await self.archive_old_partitions()

        if self._rotation_task and not self._rotation_task.done():
            self._rotation_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(self._rotation_task, timeout=3.0)

        await self._adapter.stop(timeout=timeout)
        logger.info(
            "[storage_tier] Stopped: hot=%d, cold=%d, archived=%d, deleted=%d",
            self._hot_events,
            self._cold_events,
            self._archived_partitions,
            self._deleted_partitions,
        )

    # -------------------------------------------------------------------------
    # Event Emission (热路径)
    # -------------------------------------------------------------------------

    async def emit(self, event: dict[str, Any]) -> str:
        """Emit an event to hot storage (via KernelRuntimeAdapter).

        Args:
            event: Audit event dict.

        Returns:
            Event ID.
        """
        return await self._adapter.emit(event)

    # -------------------------------------------------------------------------
    # Tier Classification
    # -------------------------------------------------------------------------

    def is_hot(self, event_or_date: dict[str, Any] | datetime | str | None) -> bool:
        """判断事件或日期是否属于热存储.

        Args:
            event_or_date: 事件dict(含timestamp)或datetime或ISO日期字符串.

        Returns:
            True if 属于热存储, False if 属于冷存储.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._hot_ttl_days)
        event_date = self._extract_date(event_or_date)
        if event_date is None:
            return True  # 无日期的事件默认为热存储
        return event_date >= cutoff

    def _extract_date(self, event_or_date: dict[str, Any] | datetime | str | None) -> datetime | None:
        """从事件dict或日期字符串提取datetime.

        Args:
            event_or_date: 事件dict或datetime或字符串.

        Returns:
            datetime或None.
        """
        if event_or_date is None:
            return None
        if isinstance(event_or_date, datetime):
            return event_or_date
        if isinstance(event_or_date, str):
            try:
                return datetime.fromisoformat(event_or_date.replace("Z", "+00:00"))
            except ValueError:
                return None
        if isinstance(event_or_date, dict):
            ts = event_or_date.get("timestamp", "")
            if isinstance(ts, str):
                try:
                    return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    return None
            elif isinstance(ts, datetime):
                return ts
        return None

    def get_tier(self, event_or_date: dict[str, Any] | datetime | str | None) -> str:
        """Get storage tier for an event or date.

        Args:
            event_or_date: Event dict or date.

        Returns:
            "hot", "cold", or "expired".
        """
        cutoff_hot = datetime.now(timezone.utc) - timedelta(days=self._hot_ttl_days)
        cutoff_cold = datetime.now(timezone.utc) - timedelta(days=self._cold_ttl_days)

        event_date = self._extract_date(event_or_date)
        if event_date is None:
            return "hot"

        if event_date >= cutoff_hot:
            return "hot"
        elif event_date >= cutoff_cold:
            return "cold"
        else:
            return "expired"

    # -------------------------------------------------------------------------
    # Archive Operations
    # -------------------------------------------------------------------------

    async def archive_old_partitions(self) -> dict[str, int]:
        """Archive partitions older than hot_ttl_days.

        Moves hot partitions older than hot_ttl_days to archive/ and optionally
        compresses them with gzip.

        Returns:
            Dict with archived and deleted partition counts.
        """
        audit_root = self._runtime_root / "audit"
        if not audit_root.exists():
            return {"archived": 0, "deleted": 0, "errors": 0}

        cutoff = datetime.now(timezone.utc) - timedelta(days=self._hot_ttl_days)
        archived = 0
        deleted = 0
        errors = 0

        try:
            for ws_dir in audit_root.iterdir():
                if not ws_dir.is_dir():
                    continue
                for date_dir in ws_dir.iterdir():
                    if not date_dir.is_dir():
                        continue
                    # 解析日期目录
                    try:
                        partition_date = datetime.strptime(date_dir.name, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue

                    # 检查是否需要Finalize
                    if partition_date < cutoff:
                        # 需要Finalize或删除
                        if self._archive_on_rotation:
                            success = await self._archive_partition(date_dir, ws_dir.name)
                            if success:
                                archived += 1
                            else:
                                errors += 1
                        else:
                            # 直接删除(不Finalize)
                            await self._delete_partition(date_dir)
                            deleted += 1

        except (RuntimeError, ValueError) as exc:
            logger.error("[storage_tier] Error during partition rotation: %s", exc)
            errors += 1

        with self._lock:
            self._archived_partitions += archived
            self._deleted_partitions += deleted

        logger.info(
            "[storage_tier] Rotation complete: archived=%d, deleted=%d, errors=%d",
            archived,
            deleted,
            errors,
        )
        return {"archived": archived, "deleted": deleted, "errors": errors}

    async def _archive_partition(self, partition_path: Path, workspace: str) -> bool:
        """Archive a partition by gzip-compressing and moving to archive/.

        Args:
            partition_path: Path to the date partition directory.
            workspace: Workspace name.

        Returns:
            True if successful.
        """
        archive_root = self._runtime_root / "audit" / "archive" / workspace
        archive_date_dir = archive_root / partition_path.name

        try:
            archive_date_dir.mkdir(parents=True, exist_ok=True)

            for jsonl_file in partition_path.glob("*.jsonl"):
                archive_file = archive_date_dir / f"{jsonl_file.stem}.jsonl.gz"
                await self._compress_file(jsonl_file, archive_file)
                # 原始文件删除
                jsonl_file.unlink()

            # 如果分区目录为空，删除它
            if not any(partition_path.iterdir()):
                partition_path.rmdir()

            logger.debug(
                "[storage_tier] Archived partition: %s/%s",
                workspace,
                partition_path.name,
            )
            return True

        except (RuntimeError, ValueError) as exc:
            logger.error("[storage_tier] Failed to archive %s: %s", partition_path, exc)
            return False

    async def _compress_file(self, source: Path, dest: Path) -> None:
        """Gzip-compress a file asynchronously.

        Args:
            source: Source file path.
            dest: Destination .gz file path.
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._compress_file_sync, source, dest)

    def _compress_file_sync(self, source: Path, dest: Path) -> None:
        """Sync gzip compression.

        Args:
            source: Source file path.
            dest: Destination .gz file path.
        """
        with open(source, "rb") as f_in, gzip.open(dest, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)

    async def _delete_partition(self, partition_path: Path) -> None:
        """Delete a partition directory.

        Args:
            partition_path: Path to the date partition directory.
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._delete_partition_sync, partition_path)

    def _delete_partition_sync(self, partition_path: Path) -> None:
        """Sync delete partition directory.

        Args:
            partition_path: Path to the date partition directory.
        """
        try:
            shutil.rmtree(partition_path)
            logger.debug("[storage_tier] Deleted partition: %s", partition_path)
        except OSError as exc:
            logger.warning("[storage_tier] Failed to delete %s: %s", partition_path, exc)

    # -------------------------------------------------------------------------
    # Background Rotation Loop
    # -------------------------------------------------------------------------

    async def _rotation_loop(self) -> None:
        """Background loop that periodically archives old partitions."""
        interval_seconds = self._rotation_interval_hours * 3600

        while self._running:
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
            except (RuntimeError, ValueError) as exc:
                logger.error("[storage_tier] Rotation loop error: %s", exc)
                continue

            if self._running:
                await self.archive_old_partitions()

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Get adapter statistics.

        Returns:
            Dictionary with hot/cold event counts and storage stats.
        """
        adapter_stats = self._adapter.get_stats()
        return {
            **adapter_stats,
            "hot_events": self._hot_events,
            "cold_events": self._cold_events,
            "archived_partitions": self._archived_partitions,
            "deleted_partitions": self._deleted_partitions,
            "hot_ttl_days": self._hot_ttl_days,
            "cold_ttl_days": self._cold_ttl_days,
        }

    def get_archive_stats(self) -> dict[str, Any]:
        """Get statistics about archived (cold) partitions.

        Returns:
            Dictionary with archive partition counts and sizes.
        """
        archive_root = self._runtime_root / "audit" / "archive"
        if not archive_root.exists():
            return {"total_gb": 0.0, "partition_count": 0}

        total_size = 0
        partition_count = 0

        try:
            for gz_file in archive_root.rglob("*.gz"):
                try:
                    total_size += gz_file.stat().st_size
                    partition_count += 1
                except OSError:
                    continue
        except OSError:
            pass

        return {
            "total_bytes": total_size,
            "total_gb": round(total_size / (1024**3), 4),
            "partition_count": partition_count,
        }


__all__ = [
    "DEFAULT_COLD_TTL_DAYS",
    "DEFAULT_HOT_TTL_DAYS",
    "StorageTierAdapter",
]
