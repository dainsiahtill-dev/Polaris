"""Factory Store - Durable storage for factory runs"""

import asyncio
import contextlib
import hashlib
import json
import logging
import stat
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from polaris.kernelone.fs.guarded_regular_file_snapshot import (
    GuardedRegularFileSnapshotError,
    read_guarded_regular_file_snapshot,
)
from polaris.kernelone.fs.locked_regular_file import (
    LockedRegularFileError,
    LockedRegularFileSetV1,
)
from polaris.kernelone.fs.text_ops import open_text_log_append, write_text_atomic
from polaris.kernelone.runtime import BoundedCache

from .factory_event_chain import (
    FactoryEventChainError,
    build_next_factory_event_record,
    decode_factory_event_chain,
    encode_factory_event_record,
)

logger = logging.getLogger(__name__)

_FACTORY_EVENT_LOCK_TIMEOUT_SECONDS = 5.0
_FACTORY_RUN_SNAPSHOT_MAX_BYTES = 4 * 1024 * 1024

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


class FactoryArtifactSnapshotError(RuntimeError):
    """Typed fail-closed error for Factory-owned immutable artifact snapshots."""

    def __init__(self, code: str, message: str, *, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(f"{code}: {message}")


class FactoryRunSnapshotError(RuntimeError):
    """Typed failure for strict mutable-run and immutable-checkpoint reads."""

    def __init__(self, code: str, message: str, *, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class FactoryArtifactSnapshotV1:
    """One exact-byte content-addressed snapshot committed by FactoryStore."""

    logical_ref: str
    raw_sha256: str
    byte_count: int
    content: bytes


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


def _run_locked_file_operation(
    file_path: Path,
    operation: Callable[[], Any],
    timeout: float,
) -> Any:
    """Acquire, execute, and release one file operation in the same worker."""

    lock = _get_run_file_lock(file_path)
    try:
        _acquire_lock_with_timeout(lock, timeout)
    except FileLockTimeoutError:
        raise FileLockTimeoutError(file_path, timeout) from None
    try:
        return operation()
    finally:
        lock.release()


async def _run_file_operation(
    file_path: Path,
    operation: Callable[[], Any],
    timeout: float = 5.0,
) -> Any:
    """Run one complete lock-protected operation without executor inversion.

    Lock acquisition, synchronous I/O, and release stay inside one worker
    callback. Same-file waiters can occupy other executor workers without
    queueing the lock owner's I/O behind themselves.
    """

    worker = asyncio.create_task(
        asyncio.to_thread(
            _run_locked_file_operation,
            file_path,
            operation,
            timeout,
        )
    )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as cancellation:
        worker_failure: BaseException | None = None
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException as exc:  # noqa: BLE001 - cancellation remains authoritative
                worker_failure = exc
                break
        if worker_failure is None:
            try:
                worker.result()
            except BaseException as exc:  # noqa: BLE001 - consume terminal worker outcome
                worker_failure = exc
        if worker_failure is not None and not isinstance(worker_failure, asyncio.CancelledError):
            logger.debug(
                "factory file operation settled with worker failure after caller cancellation: %s",
                worker_failure,
            )
            raise cancellation from worker_failure
        raise cancellation


class FactoryStore:
    """Durable storage for factory runs with atomic writes"""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # A Factory store is itself the stable runtime root for its per-run
        # event streams.  This makes KernelOne's locked-regular-file authority
        # path-compatible without aliases or a second evidence location.
        self._event_lock_root = self.base_dir.parent / ".factory-event-lock-authority"
        self._event_storage_identity = hashlib.sha256(str(self.base_dir).encode("utf-8")).hexdigest()[:24]

    def get_run_dir(self, run_id: str) -> Path:
        """Get directory for a run"""
        return self.base_dir / run_id

    @classmethod
    def run_snapshot_ref(cls, run_id: str) -> str:
        safe_run_id = cls._validated_artifact_snapshot_run_id(run_id)
        return f"runtime/{safe_run_id}/run.json"

    @classmethod
    def checkpoint_ref(cls, run: object) -> str:
        raw_run_id = getattr(run, "id", None)
        if not isinstance(raw_run_id, str):
            raise FactoryRunSnapshotError(
                "factory_checkpoint_run_id_invalid",
                "Checkpoint run id must be an exact string",
            )
        run_id = cls._validated_artifact_snapshot_run_id(raw_run_id)
        status = getattr(getattr(run, "status", None), "value", None)
        if (
            type(status) is not str
            or not status
            or status != status.strip()
            or any(token in status for token in ("/", "\\", "\x00"))
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in status)
        ):
            raise FactoryRunSnapshotError(
                "factory_checkpoint_status_invalid",
                "Checkpoint status is not a safe filename identity",
            )
        timestamp = getattr(run, "updated_at", None)
        if type(timestamp) is not str or not timestamp or timestamp != timestamp.strip():
            raise FactoryRunSnapshotError(
                "factory_checkpoint_timestamp_invalid",
                "Checkpoint timestamp must be the exact non-empty updated_at string",
            )
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise FactoryRunSnapshotError(
                "factory_checkpoint_timestamp_invalid",
                "Checkpoint updated_at must be a bounded ISO-8601 instant",
            ) from exc
        if any(token in timestamp for token in ("/", "\\", "\x00")):
            raise FactoryRunSnapshotError(
                "factory_checkpoint_timestamp_invalid",
                "Checkpoint timestamp is not a safe filename identity",
            )
        return f"runtime/{run_id}/checkpoints/{status}_{timestamp.replace(':', '_')}.json"

    @staticmethod
    def _validated_artifact_snapshot_run_id(run_id: str) -> str:
        if not isinstance(run_id, str):
            raise FactoryArtifactSnapshotError(
                "factory_artifact_snapshot_run_id_invalid",
                "Factory run id must be a string",
            )
        normalized = unicodedata.normalize("NFC", run_id)
        encoded = normalized.encode("utf-8")
        if (
            normalized != run_id
            or not normalized
            or normalized != normalized.strip()
            or normalized in {".", ".."}
            or len(encoded) > 256
            or any(token in normalized for token in ("/", "\\", "\x00"))
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized)
        ):
            raise FactoryArtifactSnapshotError(
                "factory_artifact_snapshot_run_id_invalid",
                "Factory run id is not a safe bounded identity",
            )
        return normalized

    @staticmethod
    def _validated_artifact_snapshot_hash(raw_sha256: str) -> str:
        if (
            not isinstance(raw_sha256, str)
            or len(raw_sha256) != 64
            or any(character not in "0123456789abcdef" for character in raw_sha256)
        ):
            raise FactoryArtifactSnapshotError(
                "factory_artifact_snapshot_hash_invalid",
                "Artifact snapshot hash must be exactly 64 lowercase hexadecimal characters",
            )
        return raw_sha256

    @classmethod
    def stage_artifact_snapshot_ref(cls, run_id: str, raw_sha256: str) -> str:
        """Return the sole content-addressed logical ref for one run/hash pair."""

        safe_run_id = cls._validated_artifact_snapshot_run_id(run_id)
        safe_hash = cls._validated_artifact_snapshot_hash(raw_sha256)
        return f"runtime/{safe_run_id}/artifacts/stage-bindings/sha256/{safe_hash[:2]}/{safe_hash}.json"

    @staticmethod
    def _validated_artifact_snapshot_content(raw: bytes) -> bytes:
        if not isinstance(raw, bytes) or not raw:
            raise FactoryArtifactSnapshotError(
                "factory_artifact_snapshot_content_invalid",
                "Artifact snapshot content must be non-empty immutable bytes",
            )
        return raw

    @staticmethod
    def _validated_artifact_snapshot_byte_count(byte_count: int) -> int:
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count <= 0:
            raise FactoryArtifactSnapshotError(
                "factory_artifact_snapshot_byte_count_invalid",
                "Artifact snapshot byte count must be a positive integer",
            )
        return byte_count

    def persist_stage_artifact_snapshot(
        self,
        run_id: str,
        raw_sha256: str,
        raw: bytes,
    ) -> FactoryArtifactSnapshotV1:
        """Commit or exactly reuse one immutable content-addressed snapshot.

        The authoritative locked-regular-file capability is acquired for one
        logical ref only.  Existing bytes are compared exactly; a hash-path
        collision never overwrites or appends to the existing object.
        """

        content = self._validated_artifact_snapshot_content(raw)
        safe_hash = self._validated_artifact_snapshot_hash(raw_sha256)
        observed_hash = hashlib.sha256(content).hexdigest()
        if observed_hash != safe_hash:
            raise FactoryArtifactSnapshotError(
                "factory_artifact_snapshot_hash_mismatch",
                "Provided hash does not bind the exact snapshot bytes",
                details={"expected": safe_hash, "observed": observed_hash},
            )
        logical_ref = self.stage_artifact_snapshot_ref(run_id, safe_hash)

        try:
            with self._acquire_authoritative_event_lock(logical_ref) as locked:
                lease = locked.lease(logical_ref)
                if lease.open_existing(writable=False):
                    existing = lease.read_bytes()
                    if existing != content:
                        raise FactoryArtifactSnapshotError(
                            "factory_artifact_snapshot_hash_collision",
                            "Content-addressed snapshot ref already contains different bytes",
                            details={"logical_ref": logical_ref},
                        )
                else:
                    lease.append_bytes(content, fsync_file=True, fsync_parent_on_create=True)

                # Reopen and re-read while the same stable key remains locked.
                if not lease.open_existing(writable=False):
                    raise FactoryArtifactSnapshotError(
                        "factory_artifact_snapshot_post_commit_missing",
                        "Committed snapshot could not be reopened under its authoritative lock",
                        details={"logical_ref": logical_ref},
                    )
                committed = lease.read_bytes()
                if committed != content:
                    raise FactoryArtifactSnapshotError(
                        "factory_artifact_snapshot_post_commit_mismatch",
                        "Committed snapshot bytes differ from the exact source bytes",
                        details={"logical_ref": logical_ref},
                    )
        except FactoryArtifactSnapshotError:
            raise
        except LockedRegularFileError as exc:
            raise FactoryArtifactSnapshotError(
                "factory_artifact_snapshot_storage_failed",
                "Locked immutable snapshot persistence failed closed",
                details={"storage_error_code": exc.code, "logical_ref": logical_ref},
            ) from exc

        return FactoryArtifactSnapshotV1(
            logical_ref=logical_ref,
            raw_sha256=safe_hash,
            byte_count=len(content),
            content=content,
        )

    def read_stage_artifact_snapshot(
        self,
        run_id: str,
        logical_ref: str,
        raw_sha256: str,
        byte_count: int,
    ) -> FactoryArtifactSnapshotV1:
        """Strictly re-read one expected immutable snapshot under its sole key."""

        safe_hash = self._validated_artifact_snapshot_hash(raw_sha256)
        expected_ref = self.stage_artifact_snapshot_ref(run_id, safe_hash)
        if not isinstance(logical_ref, str) or logical_ref != expected_ref:
            raise FactoryArtifactSnapshotError(
                "factory_artifact_snapshot_ref_mismatch",
                "Snapshot ref does not match the exact run/hash content address",
                details={"expected": expected_ref, "observed": logical_ref},
            )
        expected_count = self._validated_artifact_snapshot_byte_count(byte_count)

        try:
            with self._acquire_authoritative_event_lock(expected_ref) as locked:
                lease = locked.lease(expected_ref)
                if not lease.open_existing(writable=False):
                    raise FactoryArtifactSnapshotError(
                        "factory_artifact_snapshot_missing",
                        "Expected immutable snapshot is absent",
                        details={"logical_ref": expected_ref},
                    )
                content = lease.read_bytes()
        except FactoryArtifactSnapshotError:
            raise
        except LockedRegularFileError as exc:
            raise FactoryArtifactSnapshotError(
                "factory_artifact_snapshot_storage_failed",
                "Locked immutable snapshot read failed closed",
                details={"storage_error_code": exc.code, "logical_ref": expected_ref},
            ) from exc

        observed_hash = hashlib.sha256(content).hexdigest()
        if observed_hash != safe_hash:
            raise FactoryArtifactSnapshotError(
                "factory_artifact_snapshot_hash_mismatch",
                "Stored snapshot bytes do not match the expected content hash",
                details={"expected": safe_hash, "observed": observed_hash},
            )
        if len(content) != expected_count:
            raise FactoryArtifactSnapshotError(
                "factory_artifact_snapshot_byte_count_mismatch",
                "Stored snapshot byte count does not match the expected count",
                details={"expected": expected_count, "observed": len(content)},
            )
        return FactoryArtifactSnapshotV1(
            logical_ref=expected_ref,
            raw_sha256=safe_hash,
            byte_count=expected_count,
            content=content,
        )

    async def save_run(self, run) -> str:
        """Save run to disk atomically"""
        run_dir = self.get_run_dir(run.id)
        run_dir.mkdir(parents=True, exist_ok=True)

        run_file = run_dir / "run.json"
        content = json.dumps(run.to_dict(), ensure_ascii=False, allow_nan=False, indent=2)

        # 完全异步的原子写入
        await self._write_file_atomic(run_file, content)
        return self.run_snapshot_ref(run.id)

    async def _write_file_atomic(self, run_file: Path, content: str) -> None:
        """异步文件写入 helper with Windows-safe replace retries."""
        temp_file = run_file.with_name(f"{run_file.name}.{uuid.uuid4().hex}.tmp")

        def write_and_replace() -> None:
            try:
                self._write_temp_file(temp_file, content)
                self._replace_with_retry_sync(temp_file, run_file)
            except BaseException:
                try:
                    temp_file.unlink(missing_ok=True)
                except OSError as cleanup_exc:
                    logger.warning(
                        "factory_store: failed to clean up temp file %s after locked write failure: %s",
                        temp_file,
                        cleanup_exc,
                    )
                raise

        await _run_file_operation(run_file, write_and_replace)

    def _write_temp_file(self, temp_file: Path, content: str) -> None:
        """同步文件写入（在线程池中执行）"""
        write_text_atomic(str(temp_file), content)

    async def _replace_with_retry(self, temp_file: Path, run_file: Path) -> None:
        """异步替换文件，带重试逻辑"""
        await asyncio.to_thread(self._replace_with_retry_sync, temp_file, run_file)

    def _replace_with_retry_sync(self, temp_file: Path, run_file: Path) -> None:
        """Synchronously replace a file inside one lock-owning worker."""
        retry_delays = (0.01, 0.02, 0.05, 0.1, 0.2)
        last_error: Exception | None = None
        for delay in (*retry_delays, 0.0):
            try:
                temp_file.replace(run_file)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                if delay <= 0:
                    break
                time.sleep(delay)

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

    async def get_run(self, run_id: str) -> Any | None:
        """Get run from disk"""
        from .factory_run_service import FactoryRun

        run_file = self.get_run_dir(run_id) / "run.json"
        if not run_file.exists():
            return None

        try:
            content = await self._read_file(run_file)
            data = json.loads(content)
            return FactoryRun.from_dict(data)
        except FileLockTimeoutError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
            logger.warning(
                "factory_store: invalid run record skipped run_id=%s path=%s error=%s",
                run_id,
                run_file,
                exc,
            )
            return None

    @staticmethod
    def _strict_json_object(raw: bytes, *, logical_ref: str) -> dict[str, Any]:
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise FactoryRunSnapshotError(
                "factory_run_snapshot_utf8_invalid",
                "Factory snapshot is not strict UTF-8",
                details={"logical_ref": logical_ref},
            ) from exc

        def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise FactoryRunSnapshotError(
                        "factory_run_snapshot_duplicate_key",
                        "Factory snapshot contains a duplicate JSON key",
                        details={"logical_ref": logical_ref, "key": key},
                    )
                value[key] = item
            return value

        def reject_constant(value: str) -> None:
            raise FactoryRunSnapshotError(
                "factory_run_snapshot_non_finite_number",
                "Factory snapshot contains a non-finite number",
                details={"logical_ref": logical_ref, "value": value},
            )

        try:
            document = json.loads(text, object_pairs_hook=reject_duplicate, parse_constant=reject_constant)
        except FactoryRunSnapshotError:
            raise
        except json.JSONDecodeError as exc:
            raise FactoryRunSnapshotError(
                "factory_run_snapshot_json_invalid",
                "Factory snapshot is not strict JSON",
                details={"logical_ref": logical_ref},
            ) from exc
        if not isinstance(document, dict):
            raise FactoryRunSnapshotError(
                "factory_run_snapshot_root_invalid",
                "Factory snapshot root must be an object",
                details={"logical_ref": logical_ref},
            )
        return document

    def _read_strict_snapshot_sync(self, logical_ref: str) -> dict[str, Any]:
        if not isinstance(logical_ref, str) or not logical_ref.startswith("runtime/"):
            raise FactoryRunSnapshotError(
                "factory_run_snapshot_ref_invalid",
                "Factory snapshot ref must be an exact runtime ref",
            )
        relative_path = logical_ref.removeprefix("runtime/")
        try:
            snapshot = read_guarded_regular_file_snapshot(
                str(self.base_dir),
                relative_path,
                _FACTORY_RUN_SNAPSHOT_MAX_BYTES,
            )
        except (GuardedRegularFileSnapshotError, ValueError) as exc:
            raise FactoryRunSnapshotError(
                "factory_run_snapshot_guard_failed",
                "Factory snapshot failed descriptor-safe bounded reread",
                details={"logical_ref": logical_ref, "guard_code": getattr(exc, "code", type(exc).__name__)},
            ) from exc
        return self._strict_json_object(snapshot.content, logical_ref=logical_ref)

    async def read_strict_run_snapshot(self, run_id: str) -> dict[str, Any]:
        """Strictly reread the current run snapshot under the 4 MiB bound."""

        return await asyncio.to_thread(self._read_strict_snapshot_sync, self.run_snapshot_ref(run_id))

    async def read_strict_checkpoint_snapshot(self, run_id: str, logical_ref: str) -> dict[str, Any]:
        """Strictly reread one exact immutable checkpoint ref."""

        safe_run_id = self._validated_artifact_snapshot_run_id(run_id)
        prefix = f"runtime/{safe_run_id}/checkpoints/"
        if (
            not isinstance(logical_ref, str)
            or not logical_ref.startswith(prefix)
            or not logical_ref.endswith(".json")
            or "/" in logical_ref[len(prefix) :]
        ):
            raise FactoryRunSnapshotError(
                "factory_checkpoint_ref_invalid",
                "Checkpoint ref does not belong to the exact Factory run",
            )
        return await asyncio.to_thread(self._read_strict_snapshot_sync, logical_ref)

    async def _read_file(self, file_path: Path) -> str:
        """异步文件读取 helper"""
        return await _run_file_operation(file_path, lambda: self._read_file_sync(file_path))

    def _read_file_sync(self, file_path: Path) -> str:
        """同步文件读取（在线程池中执行）"""
        with open(file_path, encoding="utf-8") as f:
            return f.read()

    async def checkpoint(self, run) -> str:
        """Create a checkpoint"""
        logical_ref = self.checkpoint_ref(run)
        content = json.dumps(run.to_dict(), ensure_ascii=False, allow_nan=False, indent=2)
        await asyncio.to_thread(self._persist_immutable_checkpoint_sync, logical_ref, content.encode("utf-8"))
        return logical_ref

    def _persist_immutable_checkpoint_sync(self, logical_ref: str, content: bytes) -> None:
        try:
            with self._acquire_authoritative_event_lock(logical_ref) as locked:
                lease = locked.lease(logical_ref)
                if lease.open_existing(writable=False):
                    if lease.read_bytes() != content:
                        raise FactoryRunSnapshotError(
                            "factory_checkpoint_immutable_collision",
                            "Immutable checkpoint ref already contains different bytes",
                            details={"logical_ref": logical_ref},
                        )
                    return
                lease.append_bytes(content, fsync_file=True, fsync_parent_on_create=True)
                if not lease.open_existing(writable=False) or lease.read_bytes() != content:
                    raise FactoryRunSnapshotError(
                        "factory_checkpoint_post_commit_mismatch",
                        "Immutable checkpoint did not reread as the exact committed bytes",
                        details={"logical_ref": logical_ref},
                    )
        except FactoryRunSnapshotError:
            raise
        except LockedRegularFileError as exc:
            raise FactoryRunSnapshotError(
                "factory_checkpoint_storage_failed",
                "Immutable checkpoint persistence failed closed",
                details={"logical_ref": logical_ref, "storage_error_code": exc.code},
            ) from exc

    def _write_file_sync(self, file_path: Path, content: str) -> None:
        """同步文件写入 helper（在线程池中执行）"""
        write_text_atomic(str(file_path), content)

    async def append_event(self, run_id: str, event: dict[str, Any]) -> None:
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

    @staticmethod
    def _authoritative_event_logical_path(run_id: str) -> str:
        if (
            not isinstance(run_id, str)
            or not run_id
            or run_id in {".", ".."}
            or any(token in run_id for token in ("/", "\\", "\x00"))
        ):
            raise FactoryEventChainError(
                "factory_event_chain_run_id_invalid",
                "Factory run id is not a safe event-stream identity",
            )
        # LockedRegularFileSetV1 accepts runtime-relative logical paths and
        # resolves the suffix below the exact ``base_dir`` runtime root.
        return f"runtime/{run_id}/events/events.jsonl"

    def _provision_authoritative_event_authority(
        self,
        *,
        timeout_seconds: float = _FACTORY_EVENT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        """Provision/revalidate the stable OS lock authority without enrolling a stream."""

        LockedRegularFileSetV1.provision_authority(
            platform_lock_root=str(self._event_lock_root),
            storage_identity_token=self._event_storage_identity,
            runtime_root=str(self.base_dir),
            timeout_seconds=timeout_seconds,
        )

    def _enroll_authoritative_event_lock(
        self,
        logical_path: str,
        *,
        timeout_seconds: float = _FACTORY_EVENT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        """Enroll one stable logical stream lock key in an existing authority."""

        LockedRegularFileSetV1.enroll_stream_lock_keys(
            platform_lock_root=str(self._event_lock_root),
            storage_identity_token=self._event_storage_identity,
            runtime_root=str(self.base_dir),
            logical_paths=(logical_path,),
            timeout_seconds=timeout_seconds,
        )

    def _provision_authoritative_event_lock(self, logical_path: str) -> None:
        """Provision/revalidate the stable OS lock authority for one stream.

        ``LockedRegularFileSetV1`` is reused because ``base_dir`` is the exact
        root that owns the Factory event file.  It holds one stable OS lock key
        across no-follow descriptor read, CAS validation, append, file fsync,
        and create-parent fsync; the process-local ``_acquire_file_lock`` is
        deliberately not part of this authority path.
        """

        deadline = time.monotonic() + _FACTORY_EVENT_LOCK_TIMEOUT_SECONDS
        self._provision_authoritative_event_authority(timeout_seconds=self._remaining_event_lock_budget(deadline))
        self._enroll_authoritative_event_lock(
            logical_path,
            timeout_seconds=self._remaining_event_lock_budget(deadline),
        )

    @staticmethod
    def _remaining_event_lock_budget(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LockedRegularFileError(
                "Factory event lock exceeded its monotonic deadline",
                code="lock_acquisition_timeout",
            )
        return remaining

    def _acquire_authoritative_event_lock(self, logical_path: str) -> LockedRegularFileSetV1:
        """Acquire an enrolled stream within five seconds, provisioning only when absent."""

        deadline = time.monotonic() + _FACTORY_EVENT_LOCK_TIMEOUT_SECONDS

        def acquire() -> LockedRegularFileSetV1:
            return LockedRegularFileSetV1.acquire(
                runtime_root=str(self.base_dir),
                storage_identity_token=self._event_storage_identity,
                logical_paths=(logical_path,),
                platform_lock_root=str(self._event_lock_root),
                timeout_seconds=self._remaining_event_lock_budget(deadline),
            )

        try:
            return acquire()
        except LockedRegularFileError as exc:
            missing_code = exc.code
            if missing_code not in {"lock_authority_missing", "stream_lock_missing"}:
                raise

        if missing_code == "lock_authority_missing":
            self._provision_authoritative_event_authority(timeout_seconds=self._remaining_event_lock_budget(deadline))
            try:
                return acquire()
            except LockedRegularFileError as exc:
                if exc.code != "stream_lock_missing":
                    raise

        self._enroll_authoritative_event_lock(
            logical_path,
            timeout_seconds=self._remaining_event_lock_budget(deadline),
        )
        return acquire()

    def _append_authoritative_event_sync(
        self,
        run_id: str,
        event: dict[str, Any],
        commit_permit: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> dict[str, Any]:
        logical_path = self._authoritative_event_logical_path(run_id)
        with self._acquire_authoritative_event_lock(logical_path) as locked:
            lease = locked.lease(logical_path)
            lease.open_existing(writable=True)
            raw = lease.read_bytes()
            prefix = decode_factory_event_chain(raw, run_id=run_id)
            record = build_next_factory_event_record(prefix, run_id=run_id, event=event)
            encoded = encode_factory_event_record(record)
            # ``build_next`` bounds canonical content; this exact-byte guard
            # also accounts for any permissible whitespace already on disk.
            from .factory_event_chain import FACTORY_EVENT_CHAIN_MAX_BYTES

            if len(raw) + len(encoded) > FACTORY_EVENT_CHAIN_MAX_BYTES:
                raise FactoryEventChainError(
                    "factory_event_chain_byte_limit_exceeded",
                    "Factory event chain exceeds the byte bound",
                    details={"limit": FACTORY_EVENT_CHAIN_MAX_BYTES},
                )
            permit = commit_permit() if commit_permit is not None else contextlib.nullcontext()
            with permit:
                lease.append_bytes(encoded, fsync_file=True, fsync_parent_on_create=True)
                committed = decode_factory_event_chain(lease.read_bytes(), run_id=run_id)
                if not committed or committed[-1] != record:
                    raise FactoryEventChainError(
                        "factory_event_chain_post_append_mismatch",
                        "Durable Factory event does not match the CAS candidate",
                    )
            return record

    def _preflight_authoritative_events_sync(
        self,
        run_id: str,
        events: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        """Prove the exact current chain has capacity for candidate records."""

        logical_path = self._authoritative_event_logical_path(run_id)
        with self._acquire_authoritative_event_lock(logical_path) as locked:
            lease = locked.lease(logical_path)
            lease.open_existing(writable=False)
            raw = lease.read_bytes()
            prefix = list(decode_factory_event_chain(raw, run_id=run_id))
            candidates: list[dict[str, Any]] = []
            encoded_bytes = len(raw)
            from .factory_event_chain import FACTORY_EVENT_CHAIN_MAX_BYTES

            for event in events:
                record = build_next_factory_event_record(tuple(prefix), run_id=run_id, event=event)
                encoded = encode_factory_event_record(record)
                encoded_bytes += len(encoded)
                if encoded_bytes > FACTORY_EVENT_CHAIN_MAX_BYTES:
                    raise FactoryEventChainError(
                        "factory_event_chain_byte_limit_exceeded",
                        "Factory event chain lacks capacity for the stage transaction",
                        details={"limit": FACTORY_EVENT_CHAIN_MAX_BYTES},
                    )
                prefix.append(record)
                candidates.append(record)
            return tuple(candidates)

    async def preflight_authoritative_events(
        self,
        run_id: str,
        events: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        """Read-only 8 MiB preflight for an ordered event transaction."""

        if not events:
            raise ValueError("events must contain at least one candidate")
        return await asyncio.to_thread(self._preflight_authoritative_events_sync, run_id, events)

    async def append_authoritative_event(
        self,
        run_id: str,
        event: dict[str, Any],
        *,
        commit_permit: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> dict[str, Any]:
        """Strictly append and fsync one authoritative hash-chain record."""

        if commit_permit is None:
            worker = asyncio.create_task(asyncio.to_thread(self._append_authoritative_event_sync, run_id, dict(event)))
        else:
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self._append_authoritative_event_sync,
                    run_id,
                    dict(event),
                    commit_permit,
                )
            )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError as cancellation:
            worker_failure: BaseException | None = None
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except BaseException as exc:  # noqa: BLE001 - cancellation must remain authoritative
                    worker_failure = exc
                    break
            if worker_failure is None:
                try:
                    worker.result()
                except BaseException as exc:  # noqa: BLE001 - consume terminal worker outcome
                    worker_failure = exc
            if worker_failure is not None and not isinstance(worker_failure, asyncio.CancelledError):
                logger.debug(
                    "factory authoritative append settled with worker failure after caller cancellation: %s",
                    worker_failure,
                )
                raise cancellation from worker_failure
            raise cancellation

    def _read_authoritative_events_sync(self, run_id: str) -> tuple[dict[str, Any], ...]:
        logical_path = self._authoritative_event_logical_path(run_id)
        with self._acquire_authoritative_event_lock(logical_path) as locked:
            lease = locked.lease(logical_path)
            if not lease.open_existing(writable=False):
                return ()
            return decode_factory_event_chain(lease.read_bytes(), run_id=run_id)

    @staticmethod
    def _is_regular_snapshot(path: Path) -> bool:
        try:
            info = path.stat(follow_symlinks=False)
        except OSError:
            return False
        return stat.S_ISREG(info.st_mode) and info.st_nlink == 1

    async def get_authoritative_events(
        self,
        run_id: str,
        *,
        require_run_snapshot: bool = True,
    ) -> list[dict[str, Any]]:
        """Strict reader that never skips corruption or upgrades legacy JSONL."""

        if require_run_snapshot and not self._is_regular_snapshot(self.get_run_dir(run_id) / "run.json"):
            raise FactoryEventChainError(
                "factory_event_chain_run_snapshot_missing",
                "Authoritative Factory chain has no current regular run snapshot",
            )
        return list(await asyncio.to_thread(self._read_authoritative_events_sync, run_id))

    async def _append_file(self, file_path: Path, content: str) -> None:
        """异步文件追加 helper"""
        await _run_file_operation(file_path, lambda: self._append_file_sync(file_path, content))

    def _append_file_sync(self, file_path: Path, content: str) -> None:
        """同步文件追加（在线程池中执行）"""
        handle = open_text_log_append(str(file_path))
        try:
            handle.write(content)
        finally:
            handle.close()

    async def get_events(self, run_id: str) -> list[dict[str, Any]]:
        """Get all events for a run"""
        event_file = self.get_run_dir(run_id) / "events" / "events.jsonl"
        if not event_file.exists():
            return []

        lines = await self._read_lines(event_file)

        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "factory_store: invalid event record skipped run_id=%s path=%s line=%s error=%s",
                    run_id,
                    event_file,
                    line_number,
                    exc,
                )
                continue
            if not isinstance(payload, dict):
                logger.warning(
                    "factory_store: non-object event record skipped run_id=%s path=%s line=%s",
                    run_id,
                    event_file,
                    line_number,
                )
                continue
            events.append(payload)

        return events

    async def _read_lines(self, file_path: Path) -> list[str]:
        """异步文件读取行 helper"""
        return await _run_file_operation(file_path, lambda: self._read_lines_sync(file_path))

    def _read_lines_sync(self, file_path: Path) -> list[str]:
        """同步文件读取行（在线程池中执行）"""
        with open(file_path, encoding="utf-8") as f:
            return f.readlines()

    def list_runs(self) -> list[str]:
        """List all run IDs"""
        if not self.base_dir.exists():
            return []

        # Admission-first creation can leave an authoritative half-run when the
        # later mutable snapshot fails.  Keep its bytes for audit, but quarantine
        # it from ordinary discovery until a regular ``run.json`` exists.
        return [
            directory.name
            for directory in self.base_dir.iterdir()
            if directory.is_dir() and self._is_regular_snapshot(directory / "run.json")
        ]
