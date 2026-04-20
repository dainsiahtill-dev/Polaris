"""Kernel-level runtime for all audit write/read orchestration."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import stat
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from polaris.kernelone._runtime_config import resolve_env_str
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter

from .contracts import (
    GENESIS_HASH,
    KernelAuditEvent,
    KernelAuditEventType,
    KernelAuditStorePort,
    KernelAuditWriteResult,
    KernelChainVerificationResult,
)
from .validators import (
    derive_task_id,
    derive_trace_id,
    normalize_event_type,
    normalize_optional_mapping,
    normalize_role,
    normalize_workspace_path,
    require_valid_run_id,
)

logger = logging.getLogger(__name__)


class AuditIndex:
    """Lightweight in-memory index for O(1) event lookups.

    Maintains three indexes (task_id, trace_id, event_type) plus a
    time-ordered sliding window. Thread-safe via RLock.
    """

    def __init__(
        self,
        max_entries: int = 50_000,
        window_hours: int = 24,
    ) -> None:
        self._task_index: dict[str, list[str]] = {}
        self._trace_index: dict[str, list[str]] = {}
        self._type_index: dict[KernelAuditEventType, list[str]] = {}
        self._id_index: dict[str, KernelAuditEvent] = {}
        self._time_index: list[tuple[datetime, str]] = []
        self._max_entries = max_entries
        self._window_hours = window_hours
        self._lock = threading.RLock()

    def index_event(self, event: KernelAuditEvent) -> None:
        with self._lock:
            self._id_index[event.event_id] = event
            task_id = str(event.task.get("task_id") or "")
            if task_id:
                self._task_index.setdefault(task_id, []).append(event.event_id)
            trace_id = str(event.context.get("trace_id") or "")
            if trace_id:
                self._trace_index.setdefault(trace_id, []).append(event.event_id)
            self._type_index.setdefault(event.event_type, []).append(event.event_id)
            self._time_index.append((event.timestamp, event.event_id))
            self._evict_if_needed_locked()

    def _evict_if_needed_locked(self) -> None:
        if len(self._id_index) <= self._max_entries:
            return
        self._time_index.sort(key=lambda x: x[0])
        to_remove = len(self._id_index) - self._max_entries
        for _, event_id in self._time_index[:to_remove]:
            self._remove_by_id_locked(event_id)
        self._time_index = self._time_index[to_remove:]

    def _remove_by_id_locked(self, event_id: str) -> None:
        event = self._id_index.pop(event_id, None)
        if event is None:
            return
        task_id = str(event.task.get("task_id") or "")
        if task_id and self._task_index.get(task_id):
            self._task_index[task_id] = [e for e in self._task_index[task_id] if e != event_id]
        trace_id = str(event.context.get("trace_id") or "")
        if trace_id and self._trace_index.get(trace_id):
            self._trace_index[trace_id] = [e for e in self._trace_index[trace_id] if e != event_id]
        type_list = self._type_index.get(event.event_type, [])
        self._type_index[event.event_type] = [e for e in type_list if e != event_id]

    def query_by_task(self, task_id: str) -> list[KernelAuditEvent]:
        with self._lock:
            ids = list(self._task_index.get(task_id, []))
            return [self._id_index[i] for i in ids if i in self._id_index]

    def query_by_trace(self, trace_id: str) -> list[KernelAuditEvent]:
        with self._lock:
            ids = list(self._trace_index.get(trace_id, []))
            return [self._id_index[i] for i in ids if i in self._id_index]

    def query_by_type(self, event_type: KernelAuditEventType, *, limit: int = 100) -> list[KernelAuditEvent]:
        with self._lock:
            ids = list(self._type_index.get(event_type, []))
            return [self._id_index[i] for i in ids if i in self._id_index][:limit]

    def query_recent(self, hours: int = 1) -> list[KernelAuditEvent]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self._lock:
            return [
                self._id_index[event_id]
                for ts, event_id in self._time_index
                if ts >= cutoff and event_id in self._id_index
            ]

    def evict_old(self, before: datetime) -> int:
        """Remove all events older than `before`. Returns count evicted."""
        with self._lock:
            before_aware = before.astimezone(timezone.utc)
            to_remove_ids = [eid for ts, eid in self._time_index if ts < before_aware]
            for event_id in to_remove_ids:
                self._remove_by_id_locked(event_id)
            self._time_index = [(ts, eid) for ts, eid in self._time_index if ts >= before_aware]
            return len(to_remove_ids)

    def __len__(self) -> int:
        with self._lock:
            return len(self._id_index)


class KernelAuditWriteError(RuntimeError):
    """Raised when a mandatory audit write cannot be persisted."""


class KernelAuditRuntime:
    """Single runtime entrypoint for runtime audit operations."""

    _instances: dict[str, KernelAuditRuntime] = {}
    _instances_lock = threading.RLock()

    def __new__(cls, runtime_root: Path, store: KernelAuditStorePort):
        key = str(Path(runtime_root).resolve())
        with cls._instances_lock:
            instance = cls._instances.get(key)
            if instance is None:
                instance = super().__new__(cls)
                cls._instances[key] = instance
                instance._initialized = False
            return instance

    def __init__(self, runtime_root: Path, store: KernelAuditStorePort) -> None:
        if getattr(self, "_initialized", False):
            return
        self._runtime_root = Path(runtime_root).resolve()
        self._store = store
        self._index = AuditIndex(max_entries=50_000, window_hours=24)
        # HMAC-SHA256 signing key — read from env or generate secure random key
        env_key = resolve_env_str("audit_hmac_key")
        if env_key:
            self._hmac_key = env_key.encode("utf-8")
        else:
            # Try to load existing generated key from runtime directory
            key_file = self._runtime_root / ".polaris_audit_key"
            if key_file.exists():
                # Check file permissions before loading
                self._check_key_file_permissions(key_file)
                self._hmac_key = key_file.read_bytes()
            else:
                # Generate secure random key and persist it
                self._hmac_key = secrets.token_bytes(32)
                key_file.parent.mkdir(parents=True, exist_ok=True)
                key_file.write_bytes(self._hmac_key)
                # Set 0o600 permissions (owner read/write only) — skip on Windows
                if sys.platform != "win32":
                    os.chmod(key_file, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
                    self._verify_key_file_permissions(key_file)
        self._initialized = True

    def _check_key_file_permissions(self, key_file: Path) -> None:
        """Warn if key file has loose permissions (group/others have access)."""
        try:
            file_mode = stat.S_IMODE(key_file.stat().st_mode)
            if file_mode & (stat.S_IRWXG | stat.S_IRWXO):  # group or others have any permissions
                logger.warning(
                    "[audit-runtime] HMAC key file has loose permissions %o, recommended 0o600",
                    file_mode,
                )
        except OSError as exc:
            logger.warning("[audit-runtime] Failed to check key file permissions: %s", exc)

    def _verify_key_file_permissions(self, key_file: Path) -> None:
        """Verify that key file was created with correct 0o600 permissions."""
        try:
            actual_mode = stat.S_IMODE(key_file.stat().st_mode)
            expected_mode = stat.S_IRUSR | stat.S_IWUSR  # 0o600
            if actual_mode != expected_mode:
                logger.warning(
                    "[audit-runtime] HMAC key file permissions %o differ from expected 0o600",
                    actual_mode,
                )
        except OSError as exc:
            logger.warning("[audit-runtime] Failed to verify key file permissions: %s", exc)

    @classmethod
    def get_instance(cls, runtime_root: Path) -> KernelAuditRuntime:
        """Get per-runtime-root runtime singleton."""
        normalized = Path(runtime_root).resolve()
        key = str(normalized)
        with cls._instances_lock:
            existing = cls._instances.get(key)
            if existing is not None:
                return existing
            from .registry import create_audit_store

            return cls(normalized, create_audit_store(normalized))

    @classmethod
    def shutdown_all(cls) -> None:
        """Clear runtime singletons."""
        with cls._instances_lock:
            cls._instances.clear()

    @property
    def runtime_root(self) -> Path:
        """Resolved runtime root."""
        return self._runtime_root

    @property
    def raw_store(self) -> Any:
        """Expose wrapped store for compatibility calls."""
        return getattr(self._store, "raw_store", None)

    def emit_event(
        self,
        *,
        event_type: KernelAuditEventType | str,
        role: str,
        workspace: str,
        task_id: str = "",
        run_id: str = "",
        trace_id: str = "",
        resource: dict[str, Any] | None = None,
        action: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> KernelAuditWriteResult:
        """Write one normalized audit event."""
        normalized_workspace = normalize_workspace_path(workspace)
        run_token = require_valid_run_id(run_id)

        event_token = normalize_event_type(event_type)
        role_token = normalize_role(role)
        warnings: list[str] = []

        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            normalized_task_id = derive_task_id(run_token)
            warnings.append("task_id_missing:derived")

        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            normalized_trace_id = derive_trace_id()
            warnings.append("trace_id_missing:derived")

        normalized_context = normalize_optional_mapping(context)
        if warnings:
            normalized_context.setdefault("normalization_warnings", list(warnings))
        normalized_context["trace_id"] = normalized_trace_id

        event = KernelAuditEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            event_type=event_token,
            version="2.0",
            source={
                "role": role_token,
                "workspace": normalized_workspace,
            },
            task={
                "task_id": normalized_task_id,
                "run_id": run_token,
            },
            resource=normalize_optional_mapping(resource),
            action=normalize_optional_mapping(action),
            data=normalize_optional_mapping(data),
            context=normalized_context,
            prev_hash=self._resolve_previous_hash(),
            signature="",  # placeholder — filled below after prev_hash is set
        )
        # HMAC-SHA256 signature over the chain link
        event.signature = self._compute_signature(event)

        try:
            persisted = self._store.append(event)
            self._index.index_event(persisted)
        except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover - depends on backend failures
            logger.error("Audit append failed: %s", exc)
            self._record_corruption(
                workspace=normalized_workspace,
                file_path=str(self._runtime_root / "audit" / "audit-write"),
                error_type=type(exc).__name__,
                error_message=str(exc),
                source_op="emit_event",
            )
            raise KernelAuditWriteError(f"Mandatory audit write failed for {event_token.value}: {exc}") from exc

        evidence_paths: list[str] = []
        canonical_path = self._write_canonical_log(
            workspace=normalized_workspace,
            event=persisted,
        )
        if canonical_path:
            evidence_paths.append(canonical_path)

        return KernelAuditWriteResult(
            success=True,
            event_id=persisted.event_id,
            warnings=warnings,
            evidence_paths=evidence_paths,
        )

    def _resolve_previous_hash(self) -> str:
        """Resolve the previous event hash for the next append."""
        try:
            latest_events = self._store.query(limit=1, offset=0)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("Failed to resolve previous audit hash, falling back to GENESIS_HASH: %s", exc)
            return GENESIS_HASH

        if not latest_events:
            return GENESIS_HASH
        return self._hash_event(latest_events[0])

    @staticmethod
    def _hash_event(event: KernelAuditEvent) -> str:
        """Compute the canonical SHA-256 hash for one audit event."""
        payload = json.dumps(
            event.to_dict(),
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _compute_signature(self, event: KernelAuditEvent) -> str:
        """Compute HMAC-SHA256 signature for an audit event.

        Signs the chain-link (prev_hash + event_id + timestamp + event_type)
        to enable tamper detection without modifying the event's prev_hash chain.
        """
        link = f"{event.prev_hash}{event.event_id}{event.timestamp.isoformat()}{event.event_type.value}"
        return hmac.new(
            self._hmac_key,
            link.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def emit_llm_event(
        self,
        *,
        role: str,
        workspace: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_ms: float = 0.0,
        task_id: str = "",
        run_id: str = "",
        trace_id: str = "",
        success: bool = True,
        error: str | None = None,
    ) -> KernelAuditWriteResult:
        """Write standardized LLM call event."""
        payload = {
            "model": str(model or "").strip(),
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": int(prompt_tokens or 0) + int(completion_tokens or 0),
            "duration_ms": float(duration_ms or 0.0),
        }
        action = {
            "name": "llm_call",
            "result": "success" if success else "failure",
        }
        if error:
            payload["error"] = str(error)
            action["error"] = str(error)
        return self.emit_event(
            event_type=KernelAuditEventType.LLM_CALL,
            role=role,
            workspace=workspace,
            task_id=task_id,
            run_id=run_id,
            trace_id=trace_id,
            resource={"type": "llm", "path": str(model or "").strip()},
            action=action,
            data=payload,
        )

    def emit_dialogue(
        self,
        *,
        role: str,
        workspace: str,
        dialogue_type: str,
        message_summary: str,
        task_id: str = "",
        run_id: str = "",
        trace_id: str = "",
    ) -> KernelAuditWriteResult:
        """Write standardized dialogue audit event."""
        return self.emit_event(
            event_type=KernelAuditEventType.DIALOGUE,
            role=role,
            workspace=workspace,
            task_id=task_id,
            run_id=run_id,
            trace_id=trace_id,
            action={"name": str(dialogue_type or "").strip(), "result": "success"},
            data={"message_summary": str(message_summary or "")[:500]},
        )

    def query_events(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_type: KernelAuditEventType | str | None = None,
        role: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KernelAuditEvent]:
        """Query events from store."""
        normalized_event_type: KernelAuditEventType | None = None
        if event_type is not None:
            normalized_event_type = normalize_event_type(event_type)
        return self._store.query(
            start_time=start_time,
            end_time=end_time,
            event_type=normalized_event_type,
            role=role,
            task_id=task_id,
            limit=max(1, int(limit or 100)),
            offset=max(0, int(offset or 0)),
        )

    def query_by_run_id(self, run_id: str, *, limit: int = 1000) -> list[KernelAuditEvent]:
        """Filter events by run id."""
        run_token = require_valid_run_id(run_id)
        events = self._store.query(limit=max(10, int(limit or 1000) * 3))
        result: list[KernelAuditEvent] = []
        for item in events:
            if str(item.task.get("run_id") or "") != run_token:
                continue
            result.append(item)
            if len(result) >= int(limit):
                break
        return result

    def query_by_task_id(self, task_id: str, *, limit: int = 1000) -> list[KernelAuditEvent]:
        """Filter events by task id using index."""
        token = str(task_id or "").strip()
        if not token:
            return []
        indexed = self._index.query_by_task(token)
        if indexed:
            return indexed[: max(1, int(limit))]
        # Fallback to store
        return self._store.query(task_id=token, limit=max(10, int(limit or 1000) * 3))

    def query_by_trace_id(self, trace_id: str, *, limit: int = 1000) -> list[KernelAuditEvent]:
        """Filter events by trace id using index."""
        token = str(trace_id or "").strip()
        if not token:
            return []
        indexed = self._index.query_by_trace(token)
        if indexed:
            return indexed[: max(1, int(limit))]
        # Fallback to store
        events = self._store.query(limit=max(10, int(limit or 1000) * 3))
        return [e for e in events if str(e.context.get("trace_id") or "") == token][: max(1, int(limit))]

    def export_json(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_types: list[KernelAuditEventType | str] | None = None,
        include_data: bool = True,
    ) -> dict[str, Any]:
        """Export events in JSON payload."""
        normalized_types: list[KernelAuditEventType] | None = None
        if event_types is not None:
            normalized_types = [normalize_event_type(item) for item in event_types]
        return self._store.export_json(
            start_time=start_time,
            end_time=end_time,
            event_types=normalized_types,
            include_data=bool(include_data),
        )

    def export_csv(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> str:
        """Export events as CSV text."""
        return self._store.export_csv(start_time=start_time, end_time=end_time)

    def verify_chain(self) -> KernelChainVerificationResult:
        """Verify chain integrity."""
        return self._store.verify_chain()

    def get_stats(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Read aggregate stats."""
        return self._store.get_stats(start_time=start_time, end_time=end_time)

    def cleanup_old_logs(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Cleanup old logs via backend retention policy."""
        return self._store.cleanup_old_logs(dry_run=bool(dry_run))

    def get_corruption_log(self, *, workspace: str, limit: int = 100) -> list[dict[str, Any]]:
        """Read corruption records."""
        del workspace
        path = self._runtime_root / "audit" / "corruption.events.jsonl"
        adapter = get_default_adapter()
        try:
            if not adapter.exists(str(path)):
                return []
            raw = adapter.read_text(str(path), encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            logger.warning(
                "KernelAuditRuntime: failed to read corruption log from %s: %s",
                path,
                exc,
            )
            return []
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "KernelAuditRuntime: failed to read corruption log from %s (unexpected): %s",
                path,
                exc,
            )
            return []
        rows: list[dict[str, Any]] = []
        for line in raw.splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        limit_value = max(1, int(limit or 100))
        return rows[-limit_value:]

    def _emit_internal_event(
        self,
        event_type: KernelAuditEventType,
        error_details: dict,
        source_event_id: str | None = None,
    ) -> None:
        """将审计系统自身失败写入审计链，而非 stdlib logging。"""
        try:
            internal_event = KernelAuditEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(timezone.utc),
                event_type=event_type,
                version="2.0",
                source={"role": "kernel:audit", "workspace": str(self._runtime_root)},
                task={},
                resource={"type": "audit_internal"},
                action={"name": event_type.value, "result": "internal_failure"},
                data=error_details,
                context={"trace_id": None, "source_event_id": source_event_id},
                prev_hash=self._resolve_previous_hash(),
                signature="",  # filled below
            )
            internal_event.signature = self._compute_signature(internal_event)
            self._store.append(internal_event)
        except (OSError, RuntimeError, ValueError) as exc:
            # Last resort fallback to prevent recursion
            logger.warning(
                "Internal audit event write failed: %s (cause: %s)",
                error_details,
                exc,
            )

    def _write_canonical_log(self, *, workspace: str, event: KernelAuditEvent) -> str:
        channel = self._determine_channel(event)
        severity = self._determine_severity(event)
        kind = self._determine_kind(event)
        action = dict(event.action or {})
        canonical = {
            "event_id": event.event_id,
            "run_id": str(event.task.get("run_id") or ""),
            "ts": event.timestamp.isoformat().replace("+00:00", "Z"),
            "ts_epoch": event.timestamp.timestamp(),
            "channel": channel,
            "severity": severity,
            "kind": kind,
            "actor": str(event.source.get("role") or "system"),
            "message": f"{event.event_type.value}: {action.get('name', '')}",
            "result": action.get("result", ""),
            "error": action.get("error", ""),
            "refs": {
                "task_id": str(event.task.get("task_id") or ""),
                "run_id": str(event.task.get("run_id") or ""),
                "trace_id": str(event.context.get("trace_id") or ""),
                "prev_hash": event.prev_hash,
            },
            "raw": event.to_dict(),
        }
        path = self._runtime_root / "audit" / f"canonical.{channel}.jsonl"
        try:
            return self._append_jsonl(workspace=workspace, absolute_path=path, payload=canonical)
        except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover - fallback path
            self._emit_internal_event(
                KernelAuditEventType.INTERNAL_AUDIT_FAILURE,
                {"error": str(exc), "path": str(path), "source_op": "_write_canonical_log"},
            )
            return ""

    def _record_corruption(
        self,
        *,
        workspace: str,
        file_path: str,
        error_type: str,
        error_message: str,
        source_op: str,
    ) -> None:
        record = {
            "schema_version": "2.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "file_path": str(file_path or ""),
            "offset": -1,
            "error_type": str(error_type or "unknown"),
            "error_message": str(error_message or "")[:500],
            "line_preview": "",
            "recovered": False,
            "source_op": str(source_op or "unknown"),
        }
        path = self._runtime_root / "audit" / "corruption.events.jsonl"
        try:
            self._append_jsonl(workspace=workspace, absolute_path=path, payload=record)
        except (OSError, RuntimeError, ValueError) as exc:
            self._emit_internal_event(
                KernelAuditEventType.INTERNAL_AUDIT_FAILURE,
                {"error": str(exc), "file_path": file_path, "source_op": "_record_corruption"},
            )

    def _append_jsonl(
        self,
        *,
        workspace: str,
        absolute_path: Path,
        payload: dict[str, Any],
    ) -> str:
        fs = self._fs_for_workspace(workspace)
        try:
            logical = fs.to_logical_path(str(absolute_path))
        except (AttributeError, TypeError, ValueError):
            logical = f"runtime/audit/{absolute_path.name}"
        receipt = fs.append_jsonl(logical, payload)
        return receipt.absolute_path

    def _fs_for_workspace(self, workspace: str) -> KernelFileSystem:
        return KernelFileSystem(
            normalize_workspace_path(workspace),
            get_default_adapter(),
        )

    def _determine_channel(self, event: KernelAuditEvent) -> str:
        if event.event_type is KernelAuditEventType.LLM_CALL:
            return "llm"
        if event.event_type is KernelAuditEventType.DIALOGUE:
            return "dialogue"
        if event.event_type is KernelAuditEventType.TOOL_EXECUTION:
            return "process"
        return "system"

    def _determine_severity(self, event: KernelAuditEvent) -> str:
        result = str((event.action or {}).get("result") or "").strip().lower()
        if result == "failure":
            return "error"
        if event.event_type is KernelAuditEventType.SECURITY_VIOLATION:
            return "critical"
        return "info"

    def _determine_kind(self, event: KernelAuditEvent) -> str:
        if event.event_type in (
            KernelAuditEventType.TASK_START,
            KernelAuditEventType.TASK_COMPLETE,
        ):
            return "state"
        if event.event_type is KernelAuditEventType.TOOL_EXECUTION:
            return "action"
        return "observation"

    # =========================================================================
    # Health Check (P1-AUDIT-005)
    # =========================================================================

    def health_check(self) -> dict[str, Any]:
        """Perform health check on the audit runtime.

        [P1-AUDIT-005] Added health_check() method for monitoring and diagnostics.

        Returns:
            Dictionary with health status and metrics including:
            - status: "healthy", "degraded", or "unhealthy"
            - runtime_root: Path to runtime root
            - store_path: Current audit log file path
            - recent_event_count: Events in last 24 hours
            - chain_valid: Whether hash chain verification passed
            - index_size: Number of events in memory index
            - last_error: Most recent error if any
        """
        from dataclasses import dataclass

        @dataclass
        class HealthResult:
            status: str
            runtime_root: str
            store_path: str
            recent_event_count: int
            chain_valid: bool
            index_size: int
            last_error: str | None
            details: dict[str, Any]

        health: HealthResult = HealthResult(
            status="healthy",
            runtime_root=str(self._runtime_root),
            store_path=str(self._store.runtime_root / "audit"),
            recent_event_count=0,
            chain_valid=True,
            index_size=len(self._index),
            last_error=None,
            details={},
        )

        # Check recent events
        try:
            recent_events = self._index.query_recent(hours=24)
            health.recent_event_count = len(recent_events)
            health.details["recent_events_24h"] = health.recent_event_count
        except (RuntimeError, OSError, ValueError) as exc:
            health.status = "degraded"
            health.last_error = f"Failed to query recent events: {exc}"
            health.details["query_error"] = str(exc)

        # Verify chain integrity
        try:
            chain_result = self._store.verify_chain()
            health.chain_valid = chain_result.is_valid
            health.details["chain_total_events"] = chain_result.total_events
            health.details["chain_gap_count"] = chain_result.gap_count
            if not chain_result.is_valid:
                health.status = "unhealthy"
                health.last_error = "Chain integrity verification failed"
        except (RuntimeError, OSError, ValueError) as exc:
            health.status = "unhealthy"
            health.last_error = f"Chain verification failed: {exc}"
            health.details["verification_error"] = str(exc)

        # Check store accessibility
        try:
            audit_dir = self._store.runtime_root / "audit"
            if not audit_dir.exists():
                health.status = "degraded"
                health.details["log_file_missing"] = True
        except (RuntimeError, OSError, ValueError) as exc:
            health.status = "degraded"
            health.details["store_access_error"] = str(exc)

        return {
            "status": health.status,
            "runtime_root": health.runtime_root,
            "store_path": health.store_path,
            "recent_event_count": health.recent_event_count,
            "chain_valid": health.chain_valid,
            "index_size": health.index_size,
            "last_error": health.last_error,
            "details": health.details,
        }
