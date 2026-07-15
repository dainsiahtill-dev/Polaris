"""Local Polaris instance registry and supervisor.

An instance is a single-workspace Polaris backend/frontend pair.  This cell is
intentionally platform-level infrastructure: internal stress tools such as
factory_bench may create instances, but Bench is not a production concept here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.fs.jsonl.locking import file_lock

SCHEMA_VERSION = 1
DEFAULT_BACKEND_PORT = 49977
DEFAULT_FRONTEND_PORT = 5173
DEFAULT_HOST = "127.0.0.1"
INSTANCE_HOME_ENV = "KERNELONE_INSTANCE_HOME"
INSTANCE_WATCHDOG_ENABLED_ENV = "KERNELONE_INSTANCE_WATCHDOG_ENABLED"
INSTANCE_WATCHDOG_INTERVAL_ENV = "KERNELONE_INSTANCE_WATCHDOG_INTERVAL_SECONDS"
DEFAULT_WATCHDOG_INTERVAL_SECONDS = 2.0
PROCESS_TERMINATE_TIMEOUT_SECONDS = 5.0
PORT_RELEASE_TIMEOUT_SECONDS = 8.0
BACKEND_IDENTITY_TIMEOUT_SECONDS = 75.0
FRONTEND_IDENTITY_TIMEOUT_SECONDS = 10.0
PARTIAL_STARTUP_GRACE_SECONDS = 120.0
REGISTRY_LOCK_TIMEOUT_SECONDS = 30.0
RESERVATION_LEASE_TTL_SECONDS = 180.0
BACKEND_PROCESS_IDENTITY_ENDPOINT = "/v2/runtime/fingerprint"
BACKEND_PROCESS_IDENTITY_SOURCE = "runtime/fingerprint:process_startup"
BACKEND_PROCESS_IDENTITY_METADATA_KEY = "backend_process_identity"
RESERVATION_LEASE_METADATA_KEY = "reservation_lease"
START_FAILURES_METADATA_KEY = "start_failures"

logger = logging.getLogger(__name__)

_REGISTRY_LOCKS: dict[str, threading.RLock] = {}
_REGISTRY_LOCKS_GUARD = threading.Lock()
_REGISTRY_LOCK_STATE = threading.local()


class InstanceRegistryError(RuntimeError):
    """Base failure for an unavailable authoritative instance registry."""

    code = "instance_registry_error"

    def __init__(self, registry_path: Path, *, reason: str, detail: str = "") -> None:
        self.registry_path = registry_path
        self.reason = reason
        self.detail = detail
        message = f"{self.code}: path={registry_path} reason={reason}"
        if detail:
            message = f"{message} detail={detail}"
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "degraded": True,
            "registry_path": str(self.registry_path),
            "reason": self.reason,
            "detail": self.detail,
        }


class RegistryReadError(InstanceRegistryError):
    code = "instance_registry_read_error"


class RegistryCorruptionError(InstanceRegistryError):
    code = "instance_registry_corrupt"


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_utc_timestamp(value: str) -> float | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        normalized = token.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.strptime(token, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _reservation_lease(record: InstanceRecord) -> dict[str, Any]:
    raw = record.metadata.get(RESERVATION_LEASE_METADATA_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _reservation_lease_is_active(record: InstanceRecord, *, now: float | None = None) -> bool:
    if record.status != "starting":
        return False
    lease = _reservation_lease(record)
    if str(lease.get("state") or "") != "active" or not str(lease.get("lease_id") or ""):
        return False
    try:
        expires_at = float(lease.get("expires_at_epoch") or 0.0)
    except (TypeError, ValueError):
        return False
    return expires_at > (time.time() if now is None else now)


def _record_reserves_ports(record: InstanceRecord) -> bool:
    return record.status == "running" or _reservation_lease_is_active(record)


def instance_start_age_seconds(record: InstanceRecord) -> float:
    candidates = (
        record.last_started_at,
        record.updated_at,
        record.created_at,
    )
    started_at = next((parsed for value in candidates if (parsed := parse_utc_timestamp(value)) is not None), None)
    if started_at is None:
        return 0.0
    return max(0.0, time.time() - started_at)


def normalize_instance_id(value: str) -> str:
    """Return the canonical instance identifier without inventing a fallback."""

    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    token = "-".join(part for part in token.split("-") if part)
    return token[:80]


def sanitize_instance_id(value: str) -> str:
    return normalize_instance_id(value) or f"instance-{secrets.token_hex(4)}"


def default_polaris_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "package.json").is_file() and (parent / "src/backend/polaris").is_dir():
            return parent
    raise RuntimeError(f"unable to resolve Polaris root from instance service module: {current}")


def default_instance_home() -> Path:
    raw = os.environ.get(INSTANCE_HOME_ENV, "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".polaris" / "instances"


def ensure_absolute_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def is_process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_port_free(port: int, host: str = DEFAULT_HOST) -> bool:
    if port <= 0:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
        return True


def allocate_port(start: int, *, excluded_ports: set[int] | None = None) -> int:
    excluded = excluded_ports or set()
    port = max(1024, int(start))
    while port < 65535:
        if port not in excluded and is_port_free(port):
            return port
        port += 1
    raise RuntimeError("no free local port available")


def _coerce_requested_port(value: Any) -> int | None:
    if value is None:
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if port > 0 else None


def _coerce_excluded_ports(value: Any) -> set[int]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    ports: set[int] = set()
    for item in value:
        port = _coerce_requested_port(item)
        if port is not None:
            ports.add(port)
    return ports


def _request_uses_auto_port(requested_port: Any, *, reserved_max: int) -> bool:
    port = _coerce_requested_port(requested_port)
    return port is None or port <= reserved_max


def _is_retryable_auto_port_start_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "backend identity mismatch",
            "backend identity check timed out",
            "backend process exited before identity check",
            "frontend identity mismatch",
            "frontend identity check timed out",
            "frontend process exited before identity check",
            "address already in use",
            "port is already in use",
        )
    )


def _choose_instance_port(
    requested_port: Any,
    *,
    start: int,
    reserved_max: int,
    excluded_ports: set[int] | None = None,
) -> tuple[int, bool]:
    """Choose an instance port, ignoring requests that collide with reserved ports."""
    port = _coerce_requested_port(requested_port)
    if port is None or port <= reserved_max:
        if excluded_ports:
            return allocate_port(start, excluded_ports=excluded_ports), False
        return allocate_port(start), False
    return port, True


def validate_polaris_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if not (root / "package.json").is_file():
        raise ValueError(f"Polaris root missing package.json: {root}")
    if not (root / "src/backend/polaris").is_dir():
        raise ValueError(f"Polaris root missing src/backend/polaris: {root}")
    return root


def tail_text(path: Path, lines: int) -> str:
    if not path.is_file():
        return ""
    line_count = max(1, min(int(lines or 200), 5000))
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        content = handle.readlines()
    return "".join(content[-line_count:])


def _read_linux_process_text(pid: int) -> str:
    chunks: list[str] = []
    for name in ("cmdline", "environ"):
        path = Path("/proc") / str(pid) / name
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        chunks.append(raw.replace(b"\x00", b" ").decode("utf-8", errors="replace"))
    return " ".join(chunks)


@dataclass(slots=True)
class InstanceRecord:
    instance_id: str
    name: str
    kind: str
    polaris_root: str
    workspace: str
    runtime_root: str
    backend_port: int
    frontend_port: int
    backend_url: str
    frontend_url: str
    token: str
    backend_reload: bool = True
    frontend_vite: bool = True
    start_frontend: bool = True
    status: str = "stopped"
    backend_pid: int | None = None
    frontend_pid: int | None = None
    created_at: str = field(default_factory=utc_timestamp)
    updated_at: str = field(default_factory=utc_timestamp)
    last_started_at: str = ""
    last_stopped_at: str = ""
    bench: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["backend_alive"] = is_process_alive(self.backend_pid)
        data["frontend_alive"] = is_process_alive(self.frontend_pid) or self.metadata.get("frontend_health") == "ok"
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstanceRecord:
        payload = dict(data)
        payload.pop("backend_alive", None)
        payload.pop("frontend_alive", None)
        payload.setdefault("schema_version", SCHEMA_VERSION)
        payload.setdefault("bench", {})
        payload.setdefault("metadata", {})
        payload.setdefault("created_at", utc_timestamp())
        payload.setdefault("updated_at", utc_timestamp())
        payload.setdefault("last_started_at", "")
        payload.setdefault("last_stopped_at", "")
        payload.setdefault("backend_reload", True)
        payload.setdefault("frontend_vite", True)
        payload.setdefault("start_frontend", True)
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class InstanceRegistrySnapshot:
    schema_version: int
    records: tuple[InstanceRecord, ...]


@dataclass(frozen=True, slots=True)
class BackendProcessIdentity:
    pid: int
    instance_id: str
    workspace: str
    backend_root: str
    fingerprint: str
    current_source_fingerprint: str
    source: str
    verified_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "instance.backend_process_identity.v1",
            "pid": self.pid,
            "instance_id": self.instance_id,
            "workspace": self.workspace,
            "backend_root": self.backend_root,
            "fingerprint": self.fingerprint,
            "current_source_fingerprint": self.current_source_fingerprint,
            "source": self.source,
            "verified_at": self.verified_at,
        }


class InstanceRegistry:
    def __init__(self, home: Path | None = None, *, publish_events: bool = True) -> None:
        self.home = ensure_absolute_dir(home or default_instance_home())
        self.registry_path = self.home / "registry.json"
        self._fs = KernelFileSystem(str(self.home), get_default_adapter())
        self.publish_events = publish_events

    def list_records(self) -> list[InstanceRecord]:
        return list(self._read_raw().records)

    def assert_healthy(self) -> None:
        self._read_raw()

    def get(self, instance_id: str) -> InstanceRecord | None:
        wanted = sanitize_instance_id(instance_id)
        for record in self.list_records():
            if record.instance_id == wanted:
                return record
        return None

    @contextmanager
    def mutation_lock(self) -> Iterator[None]:
        """Serialize registry mutations across runner processes.

        A bench launch reserves its ports in the registry before spawning. The
        lock keeps two isolated runners from choosing the same free port in the
        gap between probing and binding it.
        """
        key = str(self.registry_path.resolve())
        with _REGISTRY_LOCKS_GUARD:
            lock = _REGISTRY_LOCKS.setdefault(key, threading.RLock())
        with lock:
            held: dict[str, tuple[int, Any]] = getattr(_REGISTRY_LOCK_STATE, "held", {})
            depth_and_handle = held.get(key)
            if depth_and_handle is not None:
                held[key] = (depth_and_handle[0] + 1, depth_and_handle[1])
                _REGISTRY_LOCK_STATE.held = held
                try:
                    yield
                finally:
                    held[key] = (held[key][0] - 1, held[key][1])
                    if held[key][0] == 0:
                        held.pop(key, None)
                return

            self.home.mkdir(parents=True, exist_ok=True)
            lock_path = self.home / "registry.lock"
            with file_lock(str(lock_path), timeout_sec=REGISTRY_LOCK_TIMEOUT_SECONDS) as acquired:
                if not acquired:
                    raise RuntimeError(f"instance registry lock acquisition timed out: {lock_path}")
                held[key] = (1, lock_path)
                _REGISTRY_LOCK_STATE.held = held
                try:
                    yield
                finally:
                    held.pop(key, None)

    def save(self, record: InstanceRecord) -> InstanceRecord:
        with self.mutation_lock():
            records = [item for item in self.list_records() if item.instance_id != record.instance_id]
            record.updated_at = utc_timestamp()
            records.append(record)
            records.sort(key=lambda item: item.instance_id)
            self._write_raw({"schema_version": SCHEMA_VERSION, "instances": [item.to_dict() for item in records]})
            if self.publish_events:
                publish_instances_update(action="saved", record=record, records=records)
            return record

    def replace_records(
        self,
        records: list[InstanceRecord],
        *,
        action: str,
        record: InstanceRecord | None = None,
    ) -> None:
        with self.mutation_lock():
            self.assert_healthy()
            records.sort(key=lambda item: item.instance_id)
            self._write_raw({"schema_version": SCHEMA_VERSION, "instances": [item.to_dict() for item in records]})
            if self.publish_events:
                publish_instances_update(action=action, record=record, records=records)

    def delete(self, instance_id: str) -> bool:
        with self.mutation_lock():
            wanted = sanitize_instance_id(instance_id)
            records = self.list_records()
            next_records = [item for item in records if item.instance_id != wanted]
            if len(next_records) == len(records):
                return False
            self._write_raw({"schema_version": SCHEMA_VERSION, "instances": [item.to_dict() for item in next_records]})
            if self.publish_events:
                publish_instances_update(action="deleted", record=None, records=next_records)
            return True

    def _read_raw(self) -> InstanceRegistrySnapshot:
        try:
            raw_text = self.registry_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            if self.registry_path.is_symlink():
                raise RegistryReadError(
                    self.registry_path,
                    reason="dangling_registry_path",
                    detail=type(exc).__name__,
                ) from exc
            return InstanceRegistrySnapshot(schema_version=SCHEMA_VERSION, records=())
        except UnicodeDecodeError as exc:
            raise RegistryCorruptionError(
                self.registry_path,
                reason="invalid_utf8",
                detail=f"byte_offset={exc.start}",
            ) from exc
        except OSError as exc:
            raise RegistryReadError(
                self.registry_path,
                reason="read_failed",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise RegistryCorruptionError(
                self.registry_path,
                reason="invalid_json",
                detail=f"line={exc.lineno} column={exc.colno}",
            ) from exc
        if not isinstance(data, dict):
            raise RegistryCorruptionError(self.registry_path, reason="root_not_object")

        schema_version = data.get("schema_version")
        if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
            raise RegistryCorruptionError(
                self.registry_path,
                reason="unsupported_schema_version",
                detail=f"expected={SCHEMA_VERSION} observed={schema_version!r}",
            )
        raw_instances = data.get("instances")
        if not isinstance(raw_instances, list):
            raise RegistryCorruptionError(self.registry_path, reason="instances_not_array")

        records: list[InstanceRecord] = []
        seen_instance_ids: set[str] = set()
        for index, raw_record in enumerate(raw_instances):
            if not isinstance(raw_record, dict):
                raise RegistryCorruptionError(
                    self.registry_path,
                    reason="record_not_object",
                    detail=f"index={index}",
                )
            try:
                record = InstanceRecord.from_dict(raw_record)
            except (TypeError, ValueError) as exc:
                raise RegistryCorruptionError(
                    self.registry_path,
                    reason="invalid_record_shape",
                    detail=f"index={index} error={type(exc).__name__}: {exc}",
                ) from exc
            self._validate_record(record, index=index)
            if record.instance_id in seen_instance_ids:
                raise RegistryCorruptionError(
                    self.registry_path,
                    reason="duplicate_instance_id",
                    detail=f"index={index} instance_id={record.instance_id!r}",
                )
            seen_instance_ids.add(record.instance_id)
            records.append(record)
        return InstanceRegistrySnapshot(schema_version=schema_version, records=tuple(records))

    def _validate_record(self, record: InstanceRecord, *, index: int) -> None:
        string_fields = (
            "instance_id",
            "name",
            "kind",
            "polaris_root",
            "workspace",
            "runtime_root",
            "backend_url",
            "frontend_url",
            "token",
            "status",
            "created_at",
            "updated_at",
            "last_started_at",
            "last_stopped_at",
        )
        invalid_string_field = next(
            (field_name for field_name in string_fields if not isinstance(getattr(record, field_name), str)),
            "",
        )
        if invalid_string_field:
            raise RegistryCorruptionError(
                self.registry_path,
                reason="invalid_record_field_type",
                detail=f"index={index} field={invalid_string_field} expected=string",
            )
        if not record.instance_id or normalize_instance_id(record.instance_id) != record.instance_id:
            raise RegistryCorruptionError(
                self.registry_path,
                reason="invalid_instance_id",
                detail=f"index={index} instance_id={record.instance_id!r}",
            )
        if type(record.schema_version) is not int or record.schema_version != SCHEMA_VERSION:
            raise RegistryCorruptionError(
                self.registry_path,
                reason="invalid_record_schema_version",
                detail=f"index={index} observed={record.schema_version!r}",
            )
        for field_name in ("backend_port", "frontend_port"):
            value = getattr(record, field_name)
            if type(value) is not int or value < 0 or value > 65535:
                raise RegistryCorruptionError(
                    self.registry_path,
                    reason="invalid_record_port",
                    detail=f"index={index} field={field_name} observed={value!r}",
                )
        for field_name in ("backend_pid", "frontend_pid"):
            value = getattr(record, field_name)
            if value is not None and (type(value) is not int or value <= 0):
                raise RegistryCorruptionError(
                    self.registry_path,
                    reason="invalid_record_pid",
                    detail=f"index={index} field={field_name} observed={value!r}",
                )
        for field_name in ("backend_reload", "frontend_vite", "start_frontend"):
            if type(getattr(record, field_name)) is not bool:
                raise RegistryCorruptionError(
                    self.registry_path,
                    reason="invalid_record_field_type",
                    detail=f"index={index} field={field_name} expected=boolean",
                )
        if not isinstance(record.bench, dict) or not isinstance(record.metadata, dict):
            raise RegistryCorruptionError(
                self.registry_path,
                reason="invalid_record_mapping",
                detail=f"index={index}",
            )

    def _write_raw(self, data: dict[str, Any]) -> None:
        self.assert_healthy()
        self.home.mkdir(parents=True, exist_ok=True)
        self._fs.workspace_write_text_atomic(
            "registry.json",
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        )


def public_instance_summary(record: InstanceRecord) -> dict[str, Any]:
    payload = record.to_dict()
    payload.pop("token", None)
    return payload


def publish_instances_update(
    *,
    action: str,
    record: InstanceRecord | None,
    records: list[InstanceRecord],
) -> bool:
    """Publish a runtime.v2 instance-registry update without leaking tokens."""
    try:
        from polaris.infrastructure.log_pipeline.jetstream_publisher import get_log_jetstream_publisher
    except (ImportError, RuntimeError):
        return False

    now = datetime.now(timezone.utc)
    event_id = f"instances-{int(now.timestamp() * 1000)}-{secrets.token_hex(3)}"
    envelope = {
        "schema_version": "runtime.v2",
        "event_id": event_id,
        "workspace_key": "instances",
        "run_id": "",
        "channel": "status.instances",
        "kind": "instance_registry_update",
        "ts": now.isoformat(),
        "cursor": 0,
        "trace_id": event_id,
        "payload": {
            "action": action,
            "instance": public_instance_summary(record) if record else None,
            "instances": [public_instance_summary(item) for item in records],
            "total": len(records),
        },
        "meta": {"source": "polaris.instances"},
    }
    try:
        return get_log_jetstream_publisher().publish(
            subject="hp.runtime.instances.status.instances",
            payload=envelope,
        )
    except (OSError, RuntimeError, ValueError):
        return False


class InstanceSupervisor:
    def __init__(self, registry: InstanceRegistry | None = None) -> None:
        self.registry = registry or InstanceRegistry()

    def list_instances(self) -> list[dict[str, Any]]:
        records = []
        for record in self.registry.list_records():
            records.append(self._with_health(record, probe_http=False).to_dict())
        return records

    def refresh_instance_states(self) -> list[dict[str, Any]]:
        """Persist process-state changes and publish one runtime update when needed."""
        records = self.registry.list_records()
        changed: list[InstanceRecord] = []
        next_records: list[InstanceRecord] = []
        for record in records:
            before = _instance_state_signature(record)
            projected = self._with_health(record, probe_http=False)
            after = _instance_state_signature(projected)
            if after != before:
                projected.updated_at = utc_timestamp()
                changed.append(projected)
            next_records.append(projected)
        if changed:
            self.registry.replace_records(next_records, action="health_changed", record=changed[0])
        return [item.to_dict() for item in changed]

    def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = str(request.get("kind") or "project")
        is_bench_project = kind == "bench_project"
        backend_reserved_max = DEFAULT_BACKEND_PORT if is_bench_project else 0
        frontend_reserved_max = DEFAULT_FRONTEND_PORT if is_bench_project else 0
        auto_backend_port = _request_uses_auto_port(request.get("backend_port"), reserved_max=backend_reserved_max)
        auto_frontend_port = _request_uses_auto_port(request.get("frontend_port"), reserved_max=frontend_reserved_max)
        max_attempts = 3 if auto_backend_port else 1
        excluded_backend_ports: set[int] = set()
        excluded_frontend_ports: set[int] = set()
        last_error: BaseException | None = None
        launch_request_id = secrets.token_hex(16)

        for attempt in range(max_attempts):
            with self.registry.mutation_lock():
                # Corruption must be detected before workspace creation, port
                # probing/allocation, process inspection, or spawn.
                self.registry.assert_healthy()
                request_for_attempt = dict(request)
                if excluded_backend_ports:
                    request_for_attempt["_excluded_backend_ports"] = sorted(excluded_backend_ports)
                if excluded_frontend_ports:
                    request_for_attempt["_excluded_frontend_ports"] = sorted(excluded_frontend_ports)
                record = self._build_record(request_for_attempt)
                existing = self.registry.get(record.instance_id)
                owned_retry = bool(existing and self._reservation_owned_by(existing, launch_request_id))
                if existing and bool(request.get("require_fresh_instance")) and not owned_retry:
                    raise RuntimeError(f"fresh instance identity collision: {record.instance_id}")
                if existing and _reservation_lease_is_active(existing) and not owned_retry:
                    raise RuntimeError(f"instance start already reserved: {record.instance_id}")
                if existing and owned_retry:
                    record.created_at = existing.created_at
                    failures = existing.metadata.get(START_FAILURES_METADATA_KEY)
                    if isinstance(failures, list):
                        record.metadata[START_FAILURES_METADATA_KEY] = [
                            dict(item) for item in failures if isinstance(item, dict)
                        ]
                # Reuse an existing record ONLY when its recorded backend PID is still
                # *this instance's* backend process. A bare ``is_process_alive`` check is
                # PID-recycling-unsafe: ``pid_max`` is small (often <100k) and bench runs
                # churn many short-lived backends, so a long-dead instance's PID is
                # routinely recycled to an unrelated live process. Trusting that made the
                # supervisor hand back a stale record pointing at a dead port (the
                # backend was never re-spawned), and the bench chain then connection-
                # refused on that port. ``_pid_looks_like_instance_process`` verifies via
                # /proc/<pid>/cmdline that the PID really is the polaris backend bound to
                # this workspace+port, so a recycled PID falls through to a fresh start.
                if (
                    existing
                    and not owned_retry
                    and self._pid_looks_like_instance_process(existing, existing.backend_pid, process_kind="backend")
                ):
                    if self._record_matches_start_request(existing, record):
                        return self._with_health(existing, probe_http=True).to_dict()
                    self._terminate_record_processes_for_replacement(existing)
                    self._wait_for_record_ports_free(existing)
                    existing.frontend_pid = None
                    existing.backend_pid = None
                    existing.status = "stopped"
                    existing.last_stopped_at = utc_timestamp()
                    self.registry.save(existing)
                elif existing and not owned_retry and is_process_alive(existing.backend_pid):
                    # Recorded PID is alive but is NOT our backend (recycled) — clear the
                    # stale liveness so downstream spawn/identity logic starts clean and
                    # never targets the unrelated process.
                    existing.frontend_pid = None
                    existing.backend_pid = None
                    existing.status = "stopped"
                    existing.last_stopped_at = utc_timestamp()
                    self.registry.save(existing)

                # Persist a port reservation before spawning. Other processes consult
                # every registry record during allocation, so they cannot select these
                # ports while this process is between bind probing and subprocess start.
                self._activate_start_reservation(
                    record,
                    launch_request_id=launch_request_id,
                    attempt=attempt + 1,
                )
                self.registry.save(record)

            instance_dir = self._instance_dir(record.instance_id)
            log_dir = ensure_absolute_dir(instance_dir / "logs")
            try:
                backend_pid = self._start_backend(record, log_dir / "backend.log")
                record.backend_pid = backend_pid
                self._wait_for_backend_identity(record)
                if record.start_frontend:
                    record.frontend_pid = self._start_frontend(record, log_dir / "frontend.log")
                    self._wait_for_frontend_identity(record)
                self._commit_start_reservation(record)
            except Exception as exc:
                self._terminate_pid(record.frontend_pid)
                self._terminate_pid(record.backend_pid)
                last_error = exc
                if record.backend_port:
                    excluded_backend_ports.add(int(record.backend_port))
                if auto_frontend_port and record.frontend_port:
                    excluded_frontend_ports.add(int(record.frontend_port))
                final_failure = attempt + 1 >= max_attempts or not _is_retryable_auto_port_start_error(exc)
                self._record_start_failure(
                    record,
                    exc=exc,
                    attempt=attempt + 1,
                    final=final_failure,
                )
                if final_failure:
                    raise
                logger.warning(
                    "instance auto-port start retry: instance=%s backend_port=%s frontend_port=%s error=%s",
                    record.instance_id,
                    record.backend_port,
                    record.frontend_port,
                    exc,
                )
                continue
            return self._with_health(record, probe_http=True).to_dict()
        if last_error is not None:
            raise RuntimeError(str(last_error)) from last_error
        raise RuntimeError("instance start failed")

    @staticmethod
    def _record_matches_start_request(existing: InstanceRecord, requested: InstanceRecord) -> bool:
        existing_binding = str(existing.metadata.get("backend_binding") or "")
        requested_binding = str(requested.metadata.get("backend_binding") or "")
        return (
            existing.kind == requested.kind
            and Path(existing.polaris_root).resolve() == Path(requested.polaris_root).resolve()
            and Path(existing.workspace).resolve() == Path(requested.workspace).resolve()
            and Path(existing.runtime_root).resolve() == Path(requested.runtime_root).resolve()
            and existing_binding == requested_binding
        )

    @staticmethod
    def _reservation_owned_by(record: InstanceRecord, launch_request_id: str) -> bool:
        lease = _reservation_lease(record)
        return (
            record.status == "retrying"
            and str(lease.get("launch_request_id") or "") == launch_request_id
            and str(lease.get("state") or "") == "released"
        )

    @staticmethod
    def _activate_start_reservation(
        record: InstanceRecord,
        *,
        launch_request_id: str,
        attempt: int,
    ) -> None:
        now = time.time()
        metadata = dict(record.metadata)
        metadata[RESERVATION_LEASE_METADATA_KEY] = {
            "schema_version": "instance.reservation_lease.v1",
            "lease_id": f"{launch_request_id}:{attempt}",
            "launch_request_id": launch_request_id,
            "attempt": attempt,
            "state": "active",
            "reserved_at": utc_timestamp(),
            "expires_at_epoch": now + RESERVATION_LEASE_TTL_SECONDS,
            "backend_port": record.backend_port,
            "frontend_port": record.frontend_port,
        }
        record.metadata = metadata
        record.status = "starting"

    def _record_start_failure(
        self,
        record: InstanceRecord,
        *,
        exc: BaseException,
        attempt: int,
        final: bool,
    ) -> None:
        with self.registry.mutation_lock():
            current = self.registry.get(record.instance_id)
            current_lease = _reservation_lease(current) if current is not None else {}
            record_lease = _reservation_lease(record)
            if current is None or current_lease.get("lease_id") != record_lease.get("lease_id"):
                raise RuntimeError(f"instance reservation ownership lost: {record.instance_id}") from exc

            failed_at = utc_timestamp()
            failure = {
                "attempt": attempt,
                "final": final,
                "failed_at": failed_at,
                "error_type": type(exc).__name__,
                "error_detail": str(exc),
                "backend_port": record.backend_port,
                "frontend_port": record.frontend_port,
            }
            failures_raw = record.metadata.get(START_FAILURES_METADATA_KEY)
            failures = (
                [dict(item) for item in failures_raw if isinstance(item, dict)]
                if isinstance(failures_raw, list)
                else []
            )
            failures.append(failure)
            lease = dict(record_lease)
            lease.update(
                {
                    "state": "released",
                    "released_at": failed_at,
                    "release_reason": "start_failed" if final else "retry",
                }
            )
            metadata = dict(record.metadata)
            metadata[RESERVATION_LEASE_METADATA_KEY] = lease
            metadata[START_FAILURES_METADATA_KEY] = failures
            metadata["last_start_failure"] = failure
            record.metadata = metadata
            record.backend_pid = None
            record.frontend_pid = None
            record.status = "failed" if final else "retrying"
            record.last_stopped_at = failed_at
            self.registry.save(record)

    def _commit_start_reservation(self, record: InstanceRecord) -> None:
        with self.registry.mutation_lock():
            current = self.registry.get(record.instance_id)
            current_lease = _reservation_lease(current) if current is not None else {}
            record_lease = _reservation_lease(record)
            if current is None or current_lease.get("lease_id") != record_lease.get("lease_id"):
                raise RuntimeError(f"instance reservation ownership lost: {record.instance_id}")
            committed_at = utc_timestamp()
            lease = dict(record_lease)
            lease.update({"state": "committed", "committed_at": committed_at})
            metadata = dict(record.metadata)
            metadata[RESERVATION_LEASE_METADATA_KEY] = lease
            record.metadata = metadata
            record.status = "running"
            record.last_started_at = committed_at
            self.registry.save(record)

    def _registered_ports(self, exclude_instance_id: str, *, port_kind: str) -> set[int]:
        ports: set[int] = set()
        for record in self.registry.list_records():
            if record.instance_id == exclude_instance_id:
                continue
            if not _record_reserves_ports(record):
                continue
            port = record.backend_port if port_kind == "backend" else record.frontend_port
            if port > 0:
                ports.add(int(port))
        return ports

    def _terminate_record_processes_for_replacement(self, record: InstanceRecord) -> None:
        if self._pid_looks_like_instance_process(record, record.frontend_pid, process_kind="frontend"):
            self._terminate_pid(record.frontend_pid)
        if self._pid_looks_like_instance_process(record, record.backend_pid, process_kind="backend"):
            self._terminate_pid(record.backend_pid)

    @staticmethod
    def _pid_looks_like_instance_process(
        record: InstanceRecord,
        pid: int | None,
        *,
        process_kind: str,
        allow_unknown: bool = False,
    ) -> bool:
        if not pid or pid <= 0:
            return False
        if os.name != "posix":
            return True
        process_text = _read_linux_process_text(pid)
        if not process_text:
            return bool(allow_unknown)
        if process_kind == "backend":
            return (
                "polaris.delivery.cli.backend" in process_text
                and record.workspace in process_text
                and str(record.backend_port) in process_text
            )
        if process_kind == "frontend":
            return (
                record.instance_id in process_text
                and record.workspace in process_text
                and str(record.frontend_port) in process_text
            )
        return False

    def stop_instance(self, instance_id: str) -> dict[str, Any]:
        record = self._require_record(instance_id)
        if self._record_is_current_backend(record):
            raise RuntimeError("current backend instance cannot stop itself")
        frontend_owned = self._pid_looks_like_instance_process(record, record.frontend_pid, process_kind="frontend")
        backend_owned = self._pid_looks_like_instance_process(record, record.backend_pid, process_kind="backend")
        if frontend_owned:
            self._terminate_pid(record.frontend_pid)
        if backend_owned:
            self._terminate_pid(record.backend_pid)
        if backend_owned or frontend_owned:
            self._wait_for_record_ports_free(record, wait_backend=backend_owned, wait_frontend=frontend_owned)
        record.frontend_pid = None
        record.backend_pid = None
        record.status = "stopped"
        record.last_stopped_at = utc_timestamp()
        self.registry.save(record)
        return record.to_dict()

    def restart_instance(self, instance_id: str) -> dict[str, Any]:
        record = self._require_record(instance_id)
        if self._record_is_current_backend(record):
            raise RuntimeError("current backend instance cannot restart itself")
        payload = self._restart_payload(record)
        self.stop_instance(instance_id)
        return self.start_instance(payload)

    def delete_instance(self, instance_id: str) -> bool:
        record = self.registry.get(instance_id)
        if record:
            if self._record_is_current_backend(record):
                raise RuntimeError("current backend instance cannot delete itself")
            self._terminate_pid(record.frontend_pid)
            self._terminate_pid(record.backend_pid)
        return self.registry.delete(instance_id)

    def get_logs(self, instance_id: str, stream: str, tail_lines: int = 200) -> str:
        record = self._require_record(instance_id)
        stream_name = "frontend" if stream == "frontend" else "backend"
        log_path = self._instance_dir(record.instance_id) / "logs" / f"{stream_name}.log"
        return tail_text(log_path, tail_lines)

    def health(self, instance_id: str) -> dict[str, Any]:
        return self._with_health(self._require_record(instance_id), probe_http=True).to_dict()

    def _build_record(self, request: dict[str, Any]) -> InstanceRecord:
        polaris_root = validate_polaris_root(Path(str(request.get("polaris_root") or default_polaris_root())))
        workspace_raw = str(request.get("workspace") or "").strip()
        if not workspace_raw:
            raise ValueError("workspace is required")
        workspace = ensure_absolute_dir(Path(workspace_raw))

        raw_id = str(request.get("instance_id") or request.get("name") or workspace.name)
        instance_id = sanitize_instance_id(raw_id)
        instance_dir = self._instance_dir(instance_id)
        runtime_root = ensure_absolute_dir(Path(str(request.get("runtime_root") or instance_dir / "runtime")))
        kind = str(request.get("kind") or "project")
        metadata = request.get("metadata")
        metadata_payload: dict[str, Any] = dict(metadata) if isinstance(metadata, dict) else {}
        requested_backend_port = request.get("backend_port")
        requested_frontend_port = request.get("frontend_port")
        is_bench_project = kind == "bench_project"
        excluded_backend_ports = self._registered_ports(instance_id, port_kind="backend") | _coerce_excluded_ports(
            request.get("_excluded_backend_ports")
        )
        excluded_frontend_ports = self._registered_ports(instance_id, port_kind="frontend") | _coerce_excluded_ports(
            request.get("_excluded_frontend_ports")
        )
        backend_port_start = DEFAULT_BACKEND_PORT + 1 if is_bench_project else DEFAULT_BACKEND_PORT
        frontend_port_start = DEFAULT_FRONTEND_PORT + 1 if is_bench_project else DEFAULT_FRONTEND_PORT
        backend_port, backend_requested = _choose_instance_port(
            requested_backend_port,
            start=backend_port_start,
            reserved_max=DEFAULT_BACKEND_PORT if is_bench_project else 0,
            excluded_ports=excluded_backend_ports,
        )
        frontend_port, frontend_requested = _choose_instance_port(
            requested_frontend_port,
            start=frontend_port_start,
            reserved_max=DEFAULT_FRONTEND_PORT if is_bench_project else 0,
            excluded_ports=excluded_frontend_ports,
        )
        if backend_requested and (backend_port in excluded_backend_ports or not is_port_free(backend_port)):
            raise RuntimeError(f"backend port is already in use: {backend_port}")
        if (
            request.get("start_frontend", True)
            and frontend_requested
            and (frontend_port in excluded_frontend_ports or not is_port_free(frontend_port))
        ):
            raise RuntimeError(f"frontend port is already in use: {frontend_port}")
        token = str(request.get("token") or f"polaris-{secrets.token_urlsafe(18)}")
        bench = request.get("bench")
        bench_payload: dict[str, Any] = dict(bench) if isinstance(bench, dict) else {}

        return InstanceRecord(
            instance_id=instance_id,
            name=str(request.get("name") or workspace.name or instance_id),
            kind=kind,
            polaris_root=str(polaris_root),
            workspace=str(workspace),
            runtime_root=str(runtime_root),
            backend_port=backend_port,
            frontend_port=frontend_port,
            backend_url=f"http://{DEFAULT_HOST}:{backend_port}",
            frontend_url=f"http://{DEFAULT_HOST}:{frontend_port}",
            token=token,
            backend_reload=bool(request.get("backend_reload", True)),
            frontend_vite=bool(request.get("frontend_vite", True)),
            start_frontend=bool(request.get("start_frontend", True)),
            bench=bench_payload,
            metadata=metadata_payload,
        )

    @staticmethod
    def _restart_payload(record: InstanceRecord) -> dict[str, Any]:
        payload = record.to_dict()
        backend_binding = record.metadata.get("backend_binding")
        if record.kind == "bench_project" and backend_binding == "isolated_backend_instance":
            payload["backend_reload"] = False
        if backend_binding == "shared_backend_workspace_switch":
            metadata = dict(record.metadata)
            metadata["promoted_from_backend_binding"] = "shared_backend_workspace_switch"
            metadata["backend_binding"] = "isolated_backend_instance"
            payload.update(
                {
                    "backend_port": None,
                    "frontend_port": None,
                    "backend_reload": False,
                    "frontend_vite": True,
                    "start_frontend": True,
                    "metadata": metadata,
                }
            )
        return payload

    def _start_backend(self, record: InstanceRecord, log_path: Path) -> int:
        backend_root = Path(record.polaris_root) / "src" / "backend"
        command = [
            sys.executable,
            "-m",
            "polaris.delivery.cli.backend",
            "serve",
            "--workspace",
            record.workspace,
            "--port",
            str(record.backend_port),
            "--runtime-root",
            record.runtime_root,
            "--token",
            record.token,
        ]
        if record.backend_reload:
            command.append("--reload")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(backend_root) + os.pathsep + env.get("PYTHONPATH", "")
        env["KERNELONE_INSTANCE_ID"] = record.instance_id
        env["KERNELONE_INSTANCE_KIND"] = record.kind
        if record.instance_id != "main":
            env.setdefault(INSTANCE_WATCHDOG_ENABLED_ENV, "0")
        env["KERNELONE_CORS_ORIGINS"] = ",".join(
            [
                record.frontend_url,
                f"http://{DEFAULT_HOST}:{DEFAULT_FRONTEND_PORT}",
                "http://localhost:5173",
            ]
        )
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=str(backend_root),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        return int(process.pid)

    def _start_frontend(self, record: InstanceRecord, log_path: Path) -> int:
        command = [
            "npm",
            "run",
            "dev:renderer",
            "--",
            "--host",
            DEFAULT_HOST,
            "--port",
            str(record.frontend_port),
        ]
        env = os.environ.copy()
        env["VITE_BACKEND_URL"] = record.backend_url
        env["VITE_BACKEND_TOKEN"] = record.token
        env["VITE_POLARIS_BACKEND_URL"] = record.backend_url
        env["VITE_POLARIS_BACKEND_TOKEN"] = record.token
        env["VITE_POLARIS_INSTANCE_ID"] = record.instance_id
        env["VITE_POLARIS_WORKSPACE"] = record.workspace
        env["VITE_WORKSPACE"] = record.workspace
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=record.polaris_root,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        return int(process.pid)

    def _with_health(self, record: InstanceRecord, *, probe_http: bool) -> InstanceRecord:
        backend_raw_alive = is_process_alive(record.backend_pid)
        frontend_raw_alive = is_process_alive(record.frontend_pid)
        backend_alive = backend_raw_alive and self._pid_looks_like_instance_process(
            record,
            record.backend_pid,
            process_kind="backend",
            allow_unknown=True,
        )
        frontend_pid_alive = frontend_raw_alive and self._pid_looks_like_instance_process(
            record,
            record.frontend_pid,
            process_kind="frontend",
            allow_unknown=True,
        )
        backend_foreign_pid = backend_raw_alive and not backend_alive
        frontend_foreign_pid = frontend_raw_alive and not frontend_pid_alive
        partial_startup_timed_out = (
            (backend_alive != frontend_pid_alive)
            and record.start_frontend
            and instance_start_age_seconds(record) > PARTIAL_STARTUP_GRACE_SECONDS
        )
        if not partial_startup_timed_out:
            record.metadata.pop("status_reason", None)
        if not probe_http:
            if backend_alive and (not record.start_frontend or frontend_pid_alive):
                record.status = "running"
            elif partial_startup_timed_out:
                record.status = "failed"
            elif backend_alive or frontend_pid_alive:
                record.status = "starting"
            else:
                record.status = "stopped"
            record.metadata["backend_health"] = "process" if backend_alive else "stopped"
            if partial_startup_timed_out and not backend_alive:
                record.metadata["backend_health"] = "failed"
                record.metadata["status_reason"] = "backend process did not survive startup"
            elif backend_foreign_pid:
                record.metadata["backend_health"] = "foreign_process"
            if frontend_pid_alive:
                record.metadata["frontend_health"] = "process"
            elif frontend_foreign_pid:
                record.metadata["frontend_health"] = "foreign_process"
            elif partial_startup_timed_out and record.start_frontend:
                record.metadata["frontend_health"] = "failed"
                record.metadata["status_reason"] = "frontend process did not survive startup"
            elif record.start_frontend:
                record.metadata["frontend_health"] = "stopped"
            else:
                record.metadata["frontend_health"] = "disabled"
            return record

        if backend_alive and record.backend_pid in {os.getpid(), os.getppid()}:
            backend_http_ok = True
        else:
            backend_http_ok = (
                self._http_ok(f"{record.backend_url}/health", record.token)
                if probe_http and record.backend_url and (backend_alive or not record.backend_pid)
                else False
            )
        frontend_http_ok = False
        if probe_http and record.frontend_url and (frontend_pid_alive or not record.start_frontend):
            frontend_http_ok = self._http_ok(record.frontend_url, record.token)
        if record.start_frontend:
            frontend_alive = frontend_pid_alive or frontend_http_ok
        else:
            frontend_alive = frontend_http_ok if record.frontend_url else True
        if backend_http_ok and not record.backend_pid:
            record.status = "observed"
        elif backend_http_ok and frontend_alive:
            record.status = "running"
        elif partial_startup_timed_out:
            record.status = "failed"
        elif backend_alive or frontend_pid_alive:
            record.status = "starting"
        else:
            record.status = "stopped"
        if backend_http_ok:
            record.metadata["backend_health"] = "ok"
        elif backend_alive:
            record.metadata["backend_health"] = "starting"
        elif backend_foreign_pid:
            record.metadata["backend_health"] = "foreign_process"
        elif partial_startup_timed_out and record.start_frontend:
            record.metadata["backend_health"] = "failed"
            record.metadata["status_reason"] = "backend did not become healthy during startup"
        else:
            record.metadata["backend_health"] = "stopped"
        if frontend_http_ok:
            record.metadata["frontend_health"] = "ok"
        elif frontend_pid_alive:
            record.metadata["frontend_health"] = "starting"
        elif frontend_foreign_pid:
            record.metadata["frontend_health"] = "foreign_process"
        elif partial_startup_timed_out and record.start_frontend:
            record.metadata["frontend_health"] = "failed"
            record.metadata["status_reason"] = "frontend did not become healthy during startup"
        elif record.frontend_url or record.start_frontend:
            record.metadata["frontend_health"] = "stopped"
        else:
            record.metadata["frontend_health"] = "disabled"
        return record

    def _require_record(self, instance_id: str) -> InstanceRecord:
        record = self.registry.get(instance_id)
        if not record:
            raise KeyError(f"instance not found: {instance_id}")
        return record

    @staticmethod
    def _record_is_current_backend(record: InstanceRecord) -> bool:
        current_pid = os.getpid()
        if record.backend_pid and int(record.backend_pid) == current_pid:
            return True
        env_instance_id = str(os.environ.get("KERNELONE_INSTANCE_ID", "") or "").strip()
        return bool(env_instance_id and record.instance_id == env_instance_id)

    def _instance_dir(self, instance_id: str) -> Path:
        return ensure_absolute_dir(self.registry.home / sanitize_instance_id(instance_id))

    @staticmethod
    def _wait_for_port_free(port: int, *, timeout_seconds: float = PORT_RELEASE_TIMEOUT_SECONDS) -> None:
        if port <= 0:
            return
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if is_port_free(port):
                return
            time.sleep(0.1)
        raise RuntimeError(f"backend/frontend port did not become free after stop: {port}")

    def _wait_for_record_ports_free(
        self,
        record: InstanceRecord,
        *,
        wait_backend: bool | None = None,
        wait_frontend: bool | None = None,
    ) -> None:
        should_wait_backend = bool(record.backend_pid) if wait_backend is None else bool(wait_backend)
        should_wait_frontend = (
            bool(record.start_frontend and record.frontend_pid and record.frontend_port > 0)
            if wait_frontend is None
            else bool(wait_frontend)
        )
        if should_wait_backend:
            self._wait_for_port_free(record.backend_port)
        if should_wait_frontend:
            self._wait_for_port_free(record.frontend_port)

    @staticmethod
    def _read_backend_identity_payload(record: InstanceRecord) -> dict[str, Any] | None:
        request = urllib.request.Request(
            f"{record.backend_url}{BACKEND_PROCESS_IDENTITY_ENDPOINT}",
            headers={"Authorization": f"Bearer {record.token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                body = response.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            return None
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("backend identity endpoint returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("backend identity endpoint returned a non-object payload")
        return payload

    @staticmethod
    def _canonical_identity_path(value: Any, *, field_name: str) -> str:
        raw = str(value or "").strip()
        candidate = Path(raw)
        if not raw or not candidate.is_absolute():
            raise RuntimeError(f"backend identity mismatch: {field_name} is not an absolute path")
        try:
            return str(candidate.resolve())
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"backend identity mismatch: invalid {field_name}") from exc

    @classmethod
    def _validate_backend_identity_payload(
        cls,
        record: InstanceRecord,
        payload: dict[str, Any],
    ) -> BackendProcessIdentity:
        expected_pid = record.backend_pid
        observed_pid = payload.get("pid")
        if not isinstance(expected_pid, int) or isinstance(expected_pid, bool) or expected_pid <= 0:
            raise RuntimeError("backend identity mismatch: launched backend PID is unavailable")
        if not isinstance(observed_pid, int) or isinstance(observed_pid, bool) or observed_pid != expected_pid:
            raise RuntimeError(f"backend identity mismatch: pid observed={observed_pid!r} expected={expected_pid}")

        observed_instance_id = str(payload.get("instance_id") or "")
        if (
            not observed_instance_id
            or observed_instance_id != normalize_instance_id(observed_instance_id)
            or observed_instance_id != record.instance_id
        ):
            raise RuntimeError(
                "backend identity mismatch: "
                f"instance_id observed={observed_instance_id!r} expected={record.instance_id!r}"
            )

        observed_workspace = cls._canonical_identity_path(payload.get("workspace"), field_name="workspace")
        expected_workspace = str(Path(record.workspace).resolve())
        if observed_workspace != expected_workspace:
            raise RuntimeError(
                f"backend identity mismatch: workspace observed={observed_workspace!r} expected={expected_workspace!r}"
            )

        observed_backend_root = cls._canonical_identity_path(
            payload.get("backend_root"),
            field_name="backend_root",
        )
        expected_backend_root = str((Path(record.polaris_root) / "src" / "backend").resolve())
        if observed_backend_root != expected_backend_root:
            raise RuntimeError(
                "backend identity mismatch: "
                f"backend_root observed={observed_backend_root!r} expected={expected_backend_root!r}"
            )

        observed_source = str(payload.get("source") or "")
        if observed_source != BACKEND_PROCESS_IDENTITY_SOURCE:
            raise RuntimeError(
                "backend identity mismatch: "
                f"source observed={observed_source!r} expected={BACKEND_PROCESS_IDENTITY_SOURCE!r}"
            )

        return BackendProcessIdentity(
            pid=observed_pid,
            instance_id=observed_instance_id,
            workspace=observed_workspace,
            backend_root=observed_backend_root,
            fingerprint=str(payload.get("fingerprint") or ""),
            current_source_fingerprint=str(payload.get("current_source_fingerprint") or ""),
            source=observed_source,
            verified_at=utc_timestamp(),
        )

    def _wait_for_backend_identity(
        self,
        record: InstanceRecord,
        *,
        timeout_seconds: float = BACKEND_IDENTITY_TIMEOUT_SECONDS,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            payload = self._read_backend_identity_payload(record)
            if payload is not None:
                identity = self._validate_backend_identity_payload(record, payload)
                metadata = dict(record.metadata)
                metadata[BACKEND_PROCESS_IDENTITY_METADATA_KEY] = identity.to_dict()
                record.metadata = metadata
                return
            if record.backend_pid and not is_process_alive(record.backend_pid):
                raise RuntimeError(f"backend process exited before identity check: {record.backend_pid}")
            time.sleep(0.2)
        raise RuntimeError(f"backend identity check timed out for port {record.backend_port}")

    def _wait_for_frontend_identity(
        self,
        record: InstanceRecord,
        *,
        timeout_seconds: float = FRONTEND_IDENTITY_TIMEOUT_SECONDS,
    ) -> None:
        if not record.start_frontend or not record.frontend_pid:
            return
        deadline = time.monotonic() + timeout_seconds
        last_process_alive = True
        while time.monotonic() < deadline:
            last_process_alive = is_process_alive(record.frontend_pid)
            if not last_process_alive:
                raise RuntimeError("frontend process exited before identity check")
            if self._pid_looks_like_instance_process(record, record.frontend_pid, process_kind="frontend"):
                return
            time.sleep(0.1)
        if not last_process_alive:
            raise RuntimeError("frontend process exited before identity check")
        detail = f"expected instance={record.instance_id!r} workspace={record.workspace!r} port={record.frontend_port}"
        raise RuntimeError(f"frontend identity check timed out: {detail}")

    @staticmethod
    def _signal_pid(pid: int, sig: signal.Signals) -> None:
        if os.name == "posix":
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)

    @staticmethod
    def _wait_for_pid_exit(pid: int, *, timeout_seconds: float = PROCESS_TERMINATE_TIMEOUT_SECONDS) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not is_process_alive(pid):
                return True
            time.sleep(0.1)
        return not is_process_alive(pid)

    @staticmethod
    def _terminate_pid(pid: int | None) -> None:
        if not pid or pid <= 0:
            return
        try:
            InstanceSupervisor._signal_pid(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        if InstanceSupervisor._wait_for_pid_exit(pid):
            return
        with suppress(ProcessLookupError):
            InstanceSupervisor._signal_pid(pid, signal.SIGKILL)
        InstanceSupervisor._wait_for_pid_exit(pid, timeout_seconds=1.0)

    @staticmethod
    def _http_ok(url: str, token: str) -> bool:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                return 200 <= int(response.status) < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False


def _instance_state_signature(record: InstanceRecord) -> tuple[Any, ...]:
    return (
        record.status,
        bool(is_process_alive(record.backend_pid)),
        bool(is_process_alive(record.frontend_pid)),
        str(record.metadata.get("backend_health") or ""),
        str(record.metadata.get("frontend_health") or ""),
    )


def _env_flag_enabled(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def _watchdog_interval_seconds() -> float:
    raw = str(os.environ.get(INSTANCE_WATCHDOG_INTERVAL_ENV, "") or "").strip()
    if not raw:
        return DEFAULT_WATCHDOG_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_WATCHDOG_INTERVAL_SECONDS
    return max(0.5, min(value, 60.0))


def _instance_watchdog_default_enabled() -> bool:
    instance_id = str(os.environ.get("KERNELONE_INSTANCE_ID", "") or "").strip()
    instance_kind = str(os.environ.get("KERNELONE_INSTANCE_KIND", "") or "").strip()
    return instance_id == "main" or instance_kind == "development"


async def _instance_watchdog_loop(supervisor: InstanceSupervisor, interval_seconds: float) -> None:
    while True:
        try:
            supervisor.refresh_instance_states()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("instance watchdog refresh failed: %s", exc)
        await asyncio.sleep(interval_seconds)


def maybe_start_instance_watchdog() -> asyncio.Task[None] | None:
    if not _env_flag_enabled(INSTANCE_WATCHDOG_ENABLED_ENV, default=_instance_watchdog_default_enabled()):
        return None
    interval_seconds = _watchdog_interval_seconds()
    return asyncio.create_task(
        _instance_watchdog_loop(InstanceSupervisor(), interval_seconds),
        name="polaris-instance-watchdog",
    )
