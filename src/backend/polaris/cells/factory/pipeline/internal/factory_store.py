"""Factory Store - Durable storage for factory runs"""

import asyncio
import json
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from polaris.kernelone.fs.text_ops import open_text_log_append, write_text_atomic
from polaris.kernelone.runtime import BoundedCache

logger = logging.getLogger(__name__)

# Cross-loop safe file locks.
# Do not use process-global asyncio.Lock here, because pytest/TestClient can
# create multiple event loops and a loop-bound lock may deadlock on reuse.
#
# Use BoundedCache for automatic LRU eviction to prevent unbounded memory growth.
_MAX_LOCK_ENTRIES: int = 1000
_RUN_FILE_LOCKS: BoundedCache[str, threading.Lock] = BoundedCache(max_size=_MAX_LOCK_ENTRIES)
_RUN_FILE_LOCKS_GUARD = threading.Lock()


def _get_run_file_lock(file_path: Path) -> threading.Lock:
    """Return a process-local threading.Lock keyed by the resolved lower-case path."""
    key = str(Path(file_path).resolve()).lower()
    with _RUN_FILE_LOCKS_GUARD:
        lock = _RUN_FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _RUN_FILE_LOCKS.set(key, lock)
        return lock


class FileLockTimeoutError(TimeoutError):
    """Raised when file lock acquisition times out."""

    def __init__(self, file_path: Path, timeout: float, *args: object) -> None:
        self.file_path = file_path
        self.timeout = timeout
        super().__init__(f"Failed to acquire file lock for {file_path} within {timeout}s", *args)


def _acquire_lock_with_timeout(lock: threading.Lock, timeout: float) -> bool:
    """Acquire a threading.Lock with timeout.

    Args:
        lock: The threading.Lock to acquire.
        timeout: Maximum time in seconds to wait for the lock.

    Returns:
        True if the lock was acquired, False if timeout occurred.

    Raises:
        FileLockTimeoutError: When lock acquisition exceeds the timeout.
    """
    result = lock.acquire(timeout=timeout)
    if not result:
        raise FileLockTimeoutError(Path("<unknown>"), timeout)
    return result


@asynccontextmanager
async def _acquire_file_lock(file_path: Path, timeout: float = 5.0):
    """Acquire/release a cross-loop lock without blocking the event loop.

    Uses asyncio.wait_for() to implement timeout protection, preventing
    indefinite waiting when the lock is held by another thread.

    Args:
        file_path: Path to the file being locked.
        timeout: Maximum time in seconds to wait for the lock.
                 Defaults to 5.0 seconds.

    Raises:
        FileLockTimeoutError: When lock acquisition exceeds the timeout.
            Subclass of TimeoutError for explicit handling.

    Yields:
        None
    """
    lock = _get_run_file_lock(file_path)

    try:
        # Wrap in wait_for to apply timeout protection
        await asyncio.wait_for(
            asyncio.to_thread(_acquire_lock_with_timeout, lock, timeout),
            timeout=timeout + 1.0,  # Slightly longer than lock timeout for safety margin
        )
    except asyncio.TimeoutError:
        raise FileLockTimeoutError(file_path, timeout) from None

    try:
        yield
    finally:
        lock.release()


class FactoryStore:
    """Durable storage for factory runs with atomic writes"""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_run_dir(self, run_id: str) -> Path:
        """Get directory for a run"""
        return self.base_dir / run_id

    async def save_run(self, run) -> None:
        """Save run to disk atomically"""
        run_dir = self.get_run_dir(run.id)
        run_dir.mkdir(parents=True, exist_ok=True)

        run_file = run_dir / "run.json"
        content = json.dumps(run.to_dict(), ensure_ascii=False, indent=2)

        # 完全异步的原子写入
        await self._write_file_atomic(run_file, content)

    async def _write_file_atomic(self, run_file: Path, content: str) -> None:
        """异步文件写入 helper with Windows-safe replace retries."""
        async with _acquire_file_lock(run_file):
            temp_file = run_file.with_name(f"{run_file.name}.{uuid.uuid4().hex}.tmp")
            # 文件写入在线程池中执行，但锁保护是异步的
            await asyncio.to_thread(self._write_temp_file, temp_file, content)
            await self._replace_with_retry(temp_file, run_file)

    def _write_temp_file(self, temp_file: Path, content: str) -> None:
        """同步文件写入（在线程池中执行）"""
        write_text_atomic(str(temp_file), content)

    async def _replace_with_retry(self, temp_file: Path, run_file: Path) -> None:
        """异步替换文件，带重试逻辑"""
        retry_delays = (0.01, 0.02, 0.05, 0.1, 0.2)
        last_error: Exception | None = None
        for delay in (*retry_delays, 0.0):
            try:
                await asyncio.to_thread(temp_file.replace, run_file)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                if delay <= 0:
                    break
                # 使用 asyncio.sleep 替代 time.sleep
                await asyncio.sleep(delay)

        if last_error is not None:
            try:
                temp_file.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(
                    "factory_store: failed to clean up temp file %s after atomic-replace retries: %s",
                    temp_file,
                    exc,
                )
            raise last_error

    async def get_run(self, run_id: str):
        """Get run from disk"""
        from .factory_run_service import FactoryRun

        run_file = self.get_run_dir(run_id) / "run.json"
        if not run_file.exists():
            return None

        content = await self._read_file(run_file)

        data = json.loads(content)
        return FactoryRun.from_dict(data)

    async def _read_file(self, file_path: Path) -> str:
        """异步文件读取 helper"""
        async with _acquire_file_lock(file_path):
            # 读取操作在线程池中执行
            return await asyncio.to_thread(self._read_file_sync, file_path)

    def _read_file_sync(self, file_path: Path) -> str:
        """同步文件读取（在线程池中执行）"""
        with open(file_path, encoding="utf-8") as f:
            return f.read()

    async def checkpoint(self, run) -> None:
        """Create a checkpoint"""
        checkpoint_dir = self.get_run_dir(run.id) / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        timestamp = run.updated_at or run.created_at
        safe_timestamp = timestamp.replace(":", "_") if timestamp else "unknown"
        checkpoint_file = checkpoint_dir / f"{run.status.value}_{safe_timestamp}.json"

        content = json.dumps(run.to_dict(), ensure_ascii=False, indent=2)

        await asyncio.to_thread(self._write_file_sync, checkpoint_file, content)

    def _write_file_sync(self, file_path: Path, content: str) -> None:
        """同步文件写入 helper（在线程池中执行）"""
        write_text_atomic(str(file_path), content)

    async def append_event(self, run_id: str, event: dict) -> None:
        """Append event to audit log (JSONL format)"""
        event_file = self.get_run_dir(run_id) / "events" / "events.jsonl"
        event_file.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(event, ensure_ascii=False) + "\n"

        try:
            await self._append_file(event_file, line)
        except OSError as exc:
            logger.error(
                "factory_store: append_event failed run_id=%s path=%s: %s",
                run_id,
                event_file,
                exc,
                exc_info=True,
            )
            raise

    async def _append_file(self, file_path: Path, content: str) -> None:
        """异步文件追加 helper"""
        async with _acquire_file_lock(file_path):
            await asyncio.to_thread(self._append_file_sync, file_path, content)

    def _append_file_sync(self, file_path: Path, content: str) -> None:
        """同步文件追加（在线程池中执行）"""
        handle = open_text_log_append(str(file_path))
        try:
            handle.write(content)
        finally:
            handle.close()

    async def get_events(self, run_id: str) -> list[dict]:
        """Get all events for a run"""
        event_file = self.get_run_dir(run_id) / "events" / "events.jsonl"
        if not event_file.exists():
            return []

        lines = await self._read_lines(event_file)

        events = []
        for line in lines:
            line = line.strip()
            if line:
                events.append(json.loads(line))

        return events

    async def _read_lines(self, file_path: Path) -> list[str]:
        """异步文件读取行 helper"""
        async with _acquire_file_lock(file_path):
            return await asyncio.to_thread(self._read_lines_sync, file_path)

    def _read_lines_sync(self, file_path: Path) -> list[str]:
        """同步文件读取行（在线程池中执行）"""
        with open(file_path, encoding="utf-8") as f:
            return f.readlines()

    def list_runs(self) -> list[str]:
        """List all run IDs"""
        if not self.base_dir.exists():
            return []

        return [d.name for d in self.base_dir.iterdir() if d.is_dir()]
