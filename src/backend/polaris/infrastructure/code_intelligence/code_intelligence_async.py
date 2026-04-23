"""异步代码智能服务 - 支持后台索引和多级缓存."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.infrastructure.accel.config import resolve_effective_config
from polaris.infrastructure.accel.indexers import build_or_update_indexes
from polaris.infrastructure.accel.query.context_compiler import compile_context_pack
from polaris.infrastructure.accel.verify.orchestrator import run_verify
from polaris.infrastructure.db.adapters import SqliteAdapter
from polaris.kernelone.db import KernelDatabase

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目."""

    data: Any
    timestamp: float
    ttl: int = 3600  # 默认1小时
    hits: int = 0

    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl


class MultiLevelCache:
    """多级缓存系统：内存 L1 -> SQLite L2 -> 文件 L3."""

    def __init__(self, cache_dir: Path, max_memory_items: int = 100) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._kernel_db = KernelDatabase(
            str(self.cache_dir.parent.resolve()),
            sqlite_adapter=SqliteAdapter(),
            allow_unmanaged_absolute=True,
        )

        # L1: 内存缓存
        self._memory_cache: dict[str, CacheEntry] = {}
        self._memory_lock = threading.RLock()
        self._max_memory_items = max_memory_items

        # L2: SQLite 缓存
        self._sqlite_path = self.cache_dir / "cache.db"
        self._init_sqlite()

        # 统计
        self._stats = {"l1_hits": 0, "l2_hits": 0, "l3_hits": 0, "misses": 0}

    def _init_sqlite(self) -> None:
        """初始化 SQLite 缓存表."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    data BLOB,
                    timestamp REAL,
                    ttl INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp ON cache(timestamp)
            """)
            conn.commit()

    def _connect(self):
        return self._kernel_db.sqlite(
            str(self._sqlite_path),
            timeout_seconds=5.0,
            check_same_thread=False,
            pragmas={
                "busy_timeout": 5000,
                "journal_mode": "WAL",
                "synchronous": "NORMAL",
            },
            ensure_parent=True,
        )

    def _get_key(self, prefix: str, *args) -> str:
        """生成缓存键."""
        content = json.dumps(args, sort_keys=True, default=str)
        return f"{prefix}:{hashlib.sha256(content.encode()).hexdigest()[:16]}"

    def get(self, key: str) -> Any | None:
        """获取缓存，按 L1 -> L2 -> L3 顺序查找."""
        # L1: 内存缓存
        with self._memory_lock:
            if key in self._memory_cache:
                entry = self._memory_cache[key]
                if not entry.is_expired():
                    entry.hits += 1
                    self._stats["l1_hits"] += 1
                    return entry.data
                else:
                    del self._memory_cache[key]

        # L2: SQLite 缓存
        try:
            with self._connect() as conn:
                cursor = conn.execute("SELECT data, timestamp, ttl FROM cache WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    data_blob, timestamp, ttl = row
                    if time.time() - timestamp <= ttl:
                        data = json.loads(data_blob)
                        # 提升到 L1
                        self._set_memory(key, data, ttl)
                        self._stats["l2_hits"] += 1
                        return data
                    else:
                        conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                        conn.commit()
        except (RuntimeError, ValueError) as e:
            logger.error("MultiLevelCache.get failed for key=%s: %s", key, e, exc_info=True)

        self._stats["misses"] += 1
        return None

    def set(self, key: str, data: Any, ttl: int = 3600) -> None:
        """设置缓存（写入 L1 和 L2）."""
        # L1
        self._set_memory(key, data, ttl)

        # L2
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cache (key, data, timestamp, ttl)
                    VALUES (?, ?, ?, ?)
                    """,
                    (key, json.dumps(data, default=str), time.time(), ttl),
                )
                conn.commit()
        except (RuntimeError, ValueError) as e:
            logger.error("MultiLevelCache.set failed for key=%s: %s", key, e, exc_info=True)

    def _set_memory(self, key: str, data: Any, ttl: int) -> None:
        """设置内存缓存（带 LRU 淘汰）."""
        with self._memory_lock:
            # 淘汰过期或最旧的条目
            if len(self._memory_cache) >= self._max_memory_items:
                sorted_items = sorted(self._memory_cache.items(), key=lambda x: (x[1].hits, x[1].timestamp))
                # 删除最旧的1/4
                for k, _ in sorted_items[: len(sorted_items) // 4]:
                    del self._memory_cache[k]

            self._memory_cache[key] = CacheEntry(data=data, timestamp=time.time(), ttl=ttl)

    def invalidate(self, pattern: str = "") -> int:
        """使缓存失效."""
        count = 0

        # L1
        with self._memory_lock:
            keys = [k for k in self._memory_cache if pattern in k] if pattern else list(self._memory_cache.keys())
            for k in keys:
                del self._memory_cache[k]
                count += 1

        # L2
        try:
            with self._connect() as conn:
                if pattern:
                    cursor = conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"%{pattern}%",))
                else:
                    cursor = conn.execute("DELETE FROM cache")
                conn.commit()
                count += cursor.rowcount
        except (RuntimeError, ValueError) as e:
            logger.error("MultiLevelCache.invalidate failed for pattern=%s: %s", pattern, e, exc_info=True)

        return count

    def get_stats(self) -> dict[str, int]:
        """获取缓存统计."""
        with self._memory_lock:
            return {
                **self._stats,
                "l1_size": len(self._memory_cache),
            }


class AsyncIndexManager:
    """异步索引管理器 - 支持后台构建和共享索引."""

    _instances: dict[str, AsyncIndexManager] = {}
    _locks: dict[str, asyncio.Lock] = {}
    _init_attempted: dict[str, bool] = {}
    _global_lock: threading.Lock = threading.Lock()
    _initialized: bool = False
    _workspace_key: str

    def __new__(cls, workspace: str | Path, *args, **kwargs) -> AsyncIndexManager:
        """工作区隔离的单例模式 - 每个 workspace 独立一个管理器实例.

        NOTE: This method creates asyncio.Lock objects inside threading.Lock
        protected sections. This is safe here because:
        1. asyncio.Lock() constructor does NOT require a running event loop
        2. The lock is only acquired (async with) in async contexts where a
           running loop is guaranteed to exist
        3. We only modify dict structures, no async operations during lock
        """
        workspace_key = str(Path(workspace).resolve())

        # 获取或创建该 workspace 的锁
        with cls._global_lock:
            if workspace_key not in cls._locks:
                cls._locks[workspace_key] = asyncio.Lock()

        # 获取或创建该 workspace 的实例
        with cls._global_lock:
            if workspace_key not in cls._instances:
                cls._instances[workspace_key] = super().__new__(cls)
                cls._instances[workspace_key]._initialized = False
                cls._instances[workspace_key]._workspace_key = workspace_key
            return cls._instances[workspace_key]

    def __init__(self, workspace: str | Path, accel_home: str | Path | None = None) -> None:
        # Use stored workspace_key if available (re-entered via __new__ returning existing instance)
        workspace_key = getattr(self, "_workspace_key", None)
        if workspace_key is not None:
            actual_workspace = Path(workspace).resolve()
            # If workspace mismatch, this instance belongs to a different workspace
            if str(actual_workspace) != workspace_key:
                raise ValueError(
                    f"AsyncIndexManager already exists for workspace '{workspace_key}', "
                    f"cannot reinitialize for different workspace '{actual_workspace}'"
                )

        if self._initialized:
            return

        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        self.workspace = Path(workspace).resolve()
        metadata_dir = get_workspace_metadata_dir_name()
        self.accel_home = Path(accel_home) if accel_home else self.workspace / metadata_dir
        self._config: dict[str, Any] | None = None
        self._cache = MultiLevelCache(self.accel_home / "cache")

        # 索引状态
        self._index_status: dict[str, Any] = {
            "building": False,
            "last_build": 0,
            "manifest": None,
            "error": None,
        }
        self._status_lock = threading.RLock()

        # 后台任务
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="index_worker")
        self._loop: asyncio.AbstractEventLoop | None = None

        self._initialized = True

    @property
    def config(self) -> dict[str, Any]:
        """懒加载配置."""
        if self._config is None:
            self._config = resolve_effective_config(self.workspace)
            if "runtime" not in self._config:
                self._config["runtime"] = {}
            self._config["runtime"]["accel_home"] = str(self.accel_home)
        return self._config

    def get_index_status(self) -> dict[str, Any]:
        """获取索引状态."""
        with self._status_lock:
            return dict(self._index_status)

    async def ensure_index_async(
        self,
        force_full: bool = False,
        background: bool = True,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """确保索引已构建（异步版本）.

        Args:
            force_full: 是否强制完整重建
            background: 是否在后台构建
            progress_callback: 进度回调函数

        Returns:
            索引 manifest 或状态信息
        """
        cache_key = f"index_manifest:{self.workspace}:{force_full}"

        # 检查内存中的状态
        with self._status_lock:
            if self._index_status["building"]:
                return {"status": "building", "manifest": self._index_status["manifest"]}

            # 检查缓存
            if not force_full:
                cached = self._cache.get(cache_key)
                if cached:
                    return cached

                if self._index_status["manifest"]:
                    return self._index_status["manifest"]

        if background:
            # 在后台启动索引构建
            asyncio.create_task(self._build_index_async(force_full, progress_callback))
            return {"status": "background_started"}
        else:
            # 同步等待构建完成
            return await self._build_index_async(force_full, progress_callback)

    async def _build_index_async(
        self,
        force_full: bool = False,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """异步构建索引."""
        with self._status_lock:
            if self._index_status["building"]:
                return {"status": "already_building"}
            self._index_status["building"] = True
            self._index_status["error"] = None

        def _build():
            try:
                return build_or_update_indexes(
                    project_dir=self.workspace,
                    config=self.config,
                    mode="build" if force_full else "update",
                    full=force_full,
                    progress_callback=progress_callback,
                )
            except (RuntimeError, ValueError) as e:
                return {"error": str(e), "exit_code": 1}

        try:
            # 在线程池中运行索引构建
            manifest = await asyncio.get_running_loop().run_in_executor(self._executor, _build)

            with self._status_lock:
                self._index_status["manifest"] = manifest
                self._index_status["last_build"] = time.time()
                self._index_status["error"] = manifest.get("error")

            # 缓存结果
            cache_key = f"index_manifest:{self.workspace}:{force_full}"
            self._cache.set(cache_key, manifest, ttl=1800)  # 30分钟

            return manifest

        except (RuntimeError, ValueError) as e:
            with self._status_lock:
                self._index_status["error"] = str(e)
            return {"error": str(e), "exit_code": 1}

        finally:
            with self._status_lock:
                self._index_status["building"] = False

    def shutdown(self) -> None:
        """关闭索引管理器."""
        self._executor.shutdown(wait=False)

    @classmethod
    def reset_for_testing(cls, workspace: str | Path | None = None) -> None:
        """Reset singleton state for test isolation.

        This method should only be called in test teardown to prevent
        state leakage between tests.

        Args:
            workspace: Specific workspace to reset, or None to reset all.
        """
        with cls._global_lock:
            if workspace is not None:
                # Reset specific workspace
                workspace_key = str(Path(workspace).resolve())
                if workspace_key in cls._instances:
                    cls._instances[workspace_key]._executor.shutdown(wait=False)
                    del cls._instances[workspace_key]
                if workspace_key in cls._locks:
                    del cls._locks[workspace_key]
                if workspace_key in cls._init_attempted:
                    del cls._init_attempted[workspace_key]
            else:
                # Reset all workspaces
                for instance in cls._instances.values():
                    instance._executor.shutdown(wait=False)
                cls._instances.clear()
                cls._locks.clear()
                cls._init_attempted.clear()


class AsyncCodeIntelligenceService:
    """异步代码智能服务 - 支持后台索引、共享索引和多级缓存."""

    def __init__(
        self,
        workspace: str | Path,
        accel_home: str | Path | None = None,
        enable_cache: bool = True,
    ) -> None:
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        self.workspace = Path(workspace).resolve()
        metadata_dir = get_workspace_metadata_dir_name()
        self.accel_home = Path(accel_home) if accel_home else self.workspace / metadata_dir
        self._index_manager = AsyncIndexManager(self.workspace, self.accel_home)
        self._cache = MultiLevelCache(self.accel_home / "cache") if enable_cache else None

    @property
    def config(self) -> dict[str, Any]:
        return self._index_manager.config

    async def ensure_index(
        self,
        force_full: bool = False,
        background: bool = True,
    ) -> dict[str, Any]:
        """确保索引已构建（异步）."""
        return await self._index_manager.ensure_index_async(
            force_full=force_full,
            background=background,
        )

    async def get_context_for_task(
        self,
        task_description: str,
        changed_files: list[str] | None = None,
        hints: list[str] | None = None,
        budget_override: dict[str, int] | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """为任务生成代码上下文（异步，带缓存）."""
        cache_key = None
        if use_cache and self._cache:
            cache_key = self._cache._get_key(
                "context",
                task_description,
                changed_files,
                hints,
                budget_override,
            )
            cached = self._cache.get(cache_key)
            if cached:
                return cached

        # 确保索引存在
        await self.ensure_index(background=False)

        # 编译上下文
        def _compile():
            return compile_context_pack(
                project_dir=self.workspace,
                config=self.config,
                task=task_description,
                changed_files=changed_files or [],
                hints=hints or [],
                budget_override=budget_override,
            )

        pack = await asyncio.to_thread(_compile)

        if cache_key and self._cache:
            self._cache.set(cache_key, pack, ttl=600)  # 10分钟

        return pack

    async def verify_changes(
        self,
        changed_files: list[str],
        mode: str = "evidence_run",
    ) -> dict[str, Any]:
        """运行增量验证（异步）."""

        def _verify():
            return run_verify(
                project_dir=self.workspace,
                config=self.config,
                changed_files=changed_files,
                evidence_run=(mode == "evidence_run"),
            )

        return await asyncio.to_thread(_verify)

    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计."""
        if self._cache:
            return self._cache.get_stats()
        return {}

    def invalidate_cache(self, pattern: str = "") -> int:
        """使缓存失效."""
        if self._cache:
            return self._cache.invalidate(pattern)
        return 0

    async def close(self) -> None:
        """释放服务级缓存引用，避免长生命周期对象堆积."""
        if self._cache:
            self._cache.invalidate()
        self._cache = None
