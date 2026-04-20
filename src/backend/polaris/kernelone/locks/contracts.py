"""KernelOne distributed lock subsystem.

Provides file-based distributed locking that implements the LockPort contract.
Suitable for single-machine multi-process locking. For multi-machine scenarios,
replace with RedisLockAdapter or SQLiteLockAdapter.

No Polaris business semantics. Purely technical resource serialization.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from functools import partial
from typing import Any

from polaris.kernelone.constants import LOCK_STALE_THRESHOLD_SECONDS
from polaris.kernelone.contracts.technical import (
    LockAcquireResult,
    LockOptions,
    LockPort,
    LockReleaseResult,
)
from polaris.kernelone.utils.time_utils import utc_now as _utc_now

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import msvcrt

    def _flock(fh: int, op: int) -> None:
        msvcrt.locking(fh, op, 1)

    _LOCK_EX = msvcrt.LK_LOCK
    _LOCK_UN = msvcrt.LK_UNLCK
else:
    import fcntl

    def _flock(fh: int, op: int) -> None:
        fcntl.flock(fh, op)

    _LOCK_EX = fcntl.LOCK_EX
    _LOCK_UN = fcntl.LOCK_UN

# How often to retry acquisition when lock is held
_RETRY_INTERVAL = 0.1


class _LockEntry:
    """In-memory view of a lock state file."""

    __slots__ = ("acquired_at", "expires_at", "holder_id")

    def __init__(
        self,
        holder_id: str,
        acquired_at: float,
        expires_at: float,
    ) -> None:
        self.holder_id = holder_id
        self.acquired_at = acquired_at
        self.expires_at = expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "holder_id": self.holder_id,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _LockEntry:
        return cls(
            holder_id=str(data.get("holder_id") or ""),
            acquired_at=float(data.get("acquired_at") or 0.0),
            expires_at=float(data.get("expires_at") or 0.0),
        )


class FileLockAdapter(LockPort):
    """File-based distributed lock implementing LockPort.

    Uses advisory locking via a JSON state file per resource. Suitable for
    single-host multi-process coordination.

    Design constraints:
    - KernelOne-only: no Polaris business logic
    - No bare except: all I/O errors are caught with specific types
    - Explicit UTF-8: all file I/O uses encoding="utf-8"
    - No blocking I/O on the asyncio event loop in acquire(): uses
      asyncio.sleep() for retry, not time.sleep()
    """

    def __init__(
        self,
        lock_dir: str,
        *,
        ensure_dir: bool = True,
    ) -> None:
        self._lock_dir = os.path.abspath(lock_dir)
        if ensure_dir:
            os.makedirs(self._lock_dir, exist_ok=True)
        self._holder_locks: dict[str, str] = {}  # resource -> lock_path we hold
        self._closed = False

    def _lock_path(self, resource: str) -> str:
        safe = resource.lstrip("/").replace("/", "_").replace("\\", "_")
        if not safe:
            safe = "default"
        return os.path.join(self._lock_dir, f"{safe}.lock")

    def _lock_file(self, path: str, op: int) -> int | None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fh = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
            _flock(fh, op)
            return fh
        except OSError as exc:
            logger.warning("Lock: could not acquire lock on %s: %s", path, exc)
            return None

    def _unlock_file(self, fh: int) -> None:
        try:
            _flock(fh, _LOCK_UN)
            os.close(fh)
        except OSError as exc:
            logger.warning("Lock: could not release lock %s: %s", fh, exc)

    def _read_entry(self, fh: int) -> _LockEntry | None:
        try:
            os.lseek(fh, 0, os.SEEK_SET)
            data = os.read(fh, 65536)
            if not data:
                return None
            parsed = json.loads(data.decode("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Lock: could not read state file: %s (%s)", type(exc).__name__, exc)
            return None
        if not isinstance(parsed, dict):
            return None
        return _LockEntry.from_dict(parsed)

    def _write_entry_atomic(self, fh: int, entry: _LockEntry) -> bool:
        try:
            os.lseek(fh, 0, os.SEEK_SET)
            os.ftruncate(fh, 0)
            data = json.dumps(entry.to_dict(), ensure_ascii=False)
            os.write(fh, data.encode("utf-8"))
            os.fsync(fh)
            return True
        except OSError as exc:
            logger.warning("Lock: could not write state file: %s", exc)
            return False

    def _delete_entry(self, path: str) -> bool:
        try:
            if os.path.isfile(path):
                os.unlink(path)
            return True
        except OSError as exc:
            logger.warning("Lock: could not remove state file %s: %s", path, exc)
            return False

    async def acquire(
        self,
        resource: str,
        holder_id: str,
        options: LockOptions | None = None,
    ) -> LockAcquireResult:
        if self._closed:
            return LockAcquireResult(acquired=False)
        opts = options or LockOptions()
        timeout = opts.timeout_seconds
        ttl = opts.ttl_seconds
        retry_interval = max(0.01, opts.retry_interval_seconds)

        if opts.non_blocking:
            result = self._try_acquire_sync(resource, holder_id, ttl)
            return result

        path = self._lock_path(resource)
        waited_ms = 0
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            result = self._try_acquire_sync(resource, holder_id, ttl)
            if result.acquired:
                self._holder_locks[resource] = path
                return result

            slept = retry_interval
            if time.monotonic() + slept > deadline:
                slept = max(0.0, deadline - time.monotonic())
            if slept > 0:
                await asyncio.sleep(slept)
            waited_ms += int(slept * 1000)

        return LockAcquireResult(acquired=False, waited_ms=waited_ms)

    def _try_acquire_sync(
        self,
        resource: str,
        holder_id: str,
        ttl_seconds: float,
    ) -> LockAcquireResult:
        path = self._lock_path(resource)
        now = time.monotonic()

        fh = self._lock_file(path, _LOCK_EX)
        if fh is None:
            return LockAcquireResult(acquired=False)

        try:
            entry = self._read_entry(fh)

            if entry is not None:
                expired = now > entry.expires_at
                stale = (now - entry.acquired_at) > LOCK_STALE_THRESHOLD_SECONDS
                if not expired and not stale:
                    if entry.holder_id == holder_id:
                        new_expires = now + ttl_seconds
                        new_entry = _LockEntry(holder_id, entry.acquired_at, new_expires)
                        self._write_entry_atomic(fh, new_entry)
                        return LockAcquireResult(
                            acquired=True,
                            lock_id=path,
                            holder_id=holder_id,
                            expires_at=datetime.fromtimestamp(new_expires, tz=timezone.utc),
                        )
                    return LockAcquireResult(acquired=False)

            acquired_at = now
            expires_at = now + ttl_seconds
            new_entry = _LockEntry(holder_id, acquired_at, expires_at)
            if not self._write_entry_atomic(fh, new_entry):
                return LockAcquireResult(acquired=False)

            self._holder_locks[resource] = path
            return LockAcquireResult(
                acquired=True,
                lock_id=path,
                holder_id=holder_id,
                expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
            )
        finally:
            self._unlock_file(fh)

    async def release(self, resource: str, holder_id: str) -> LockReleaseResult:
        if self._closed:
            return LockReleaseResult(released=False)
        path = self._lock_path(resource)

        fh = self._lock_file(path, _LOCK_EX)
        if fh is None:
            return LockReleaseResult(released=False)

        try:
            entry = self._read_entry(fh)

            if entry is None:
                self._holder_locks.pop(resource, None)
                return LockReleaseResult(released=True, lock_id=path)

            if entry.holder_id == holder_id:
                self._unlock_file(fh)
                self._delete_entry(path)
                self._holder_locks.pop(resource, None)
                return LockReleaseResult(released=True, lock_id=path)

            return LockReleaseResult(released=False, force_released=False)
        finally:
            self._unlock_file(fh)

    async def extend(
        self,
        resource: str,
        holder_id: str,
        additional_seconds: float,
    ) -> bool:
        if self._closed:
            return False
        path = self._lock_path(resource)

        fh = self._lock_file(path, _LOCK_EX)
        if fh is None:
            return False

        try:
            entry = self._read_entry(fh)

            if entry is None or entry.holder_id != holder_id:
                return False

            now = time.monotonic()
            new_expires = max(entry.expires_at, now) + additional_seconds
            updated = _LockEntry(holder_id, entry.acquired_at, new_expires)
            return self._write_entry_atomic(fh, updated)
        finally:
            self._unlock_file(fh)

    async def is_held(self, resource: str) -> bool:
        if self._closed:
            return False
        path = self._lock_path(resource)

        fh = self._lock_file(path, _LOCK_EX)
        if fh is None:
            return False

        try:
            entry = self._read_entry(fh)
            if entry is None:
                return False
            return time.monotonic() <= entry.expires_at
        finally:
            self._unlock_file(fh)

    async def close(self) -> None:
        self._closed = True
        for resource in list(self._holder_locks.keys()):
            await self.release(resource, holder_id="__adapter__")


__all__ = ["FileLockAdapter"]
