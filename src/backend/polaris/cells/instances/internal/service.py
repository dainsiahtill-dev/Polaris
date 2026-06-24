"""Local Polaris instance registry and supervisor.

An instance is a single-workspace Polaris backend/frontend pair.  This cell is
intentionally platform-level infrastructure: internal stress tools such as
factory_bench may create instances, but Bench is not a production concept here.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_BACKEND_PORT = 49977
DEFAULT_FRONTEND_PORT = 5173
DEFAULT_HOST = "127.0.0.1"
INSTANCE_HOME_ENV = "POLARIS_INSTANCE_HOME"


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sanitize_instance_id(value: str) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    token = "-".join(part for part in token.split("-") if part)
    return token[:80] or f"instance-{secrets.token_hex(4)}"


def default_polaris_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "package.json").is_file() and (parent / "src/backend/polaris").is_dir():
            return parent
    return Path.cwd()


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
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) != 0


def allocate_port(start: int) -> int:
    port = max(1024, int(start))
    while port < 65535:
        if is_port_free(port):
            return port
        port += 1
    raise RuntimeError("no free local port available")


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


class InstanceRegistry:
    def __init__(self, home: Path | None = None, *, publish_events: bool = True) -> None:
        self.home = ensure_absolute_dir(home or default_instance_home())
        self.registry_path = self.home / "registry.json"
        self.publish_events = publish_events

    def list_records(self) -> list[InstanceRecord]:
        raw = self._read_raw()
        records = raw.get("instances", [])
        if not isinstance(records, list):
            return []
        return [InstanceRecord.from_dict(item) for item in records if isinstance(item, dict)]

    def get(self, instance_id: str) -> InstanceRecord | None:
        wanted = sanitize_instance_id(instance_id)
        for record in self.list_records():
            if record.instance_id == wanted:
                return record
        return None

    def save(self, record: InstanceRecord) -> InstanceRecord:
        records = [item for item in self.list_records() if item.instance_id != record.instance_id]
        record.updated_at = utc_timestamp()
        records.append(record)
        records.sort(key=lambda item: item.instance_id)
        self._write_raw({"schema_version": SCHEMA_VERSION, "instances": [item.to_dict() for item in records]})
        if self.publish_events:
            publish_instances_update(action="saved", record=record, records=records)
        return record

    def delete(self, instance_id: str) -> bool:
        wanted = sanitize_instance_id(instance_id)
        records = self.list_records()
        next_records = [item for item in records if item.instance_id != wanted]
        if len(next_records) == len(records):
            return False
        self._write_raw({"schema_version": SCHEMA_VERSION, "instances": [item.to_dict() for item in next_records]})
        if self.publish_events:
            publish_instances_update(action="deleted", record=None, records=next_records)
        return True

    def _read_raw(self) -> dict[str, Any]:
        if not self.registry_path.is_file():
            return {"schema_version": SCHEMA_VERSION, "instances": []}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": SCHEMA_VERSION, "instances": []}
        return data if isinstance(data, dict) else {"schema_version": SCHEMA_VERSION, "instances": []}

    def _write_raw(self, data: dict[str, Any]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.registry_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.registry_path)


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
            records.append(self._with_health(record).to_dict())
        return records

    def start_instance(self, request: dict[str, Any]) -> dict[str, Any]:
        record = self._build_record(request)
        existing = self.registry.get(record.instance_id)
        if existing and is_process_alive(existing.backend_pid):
            if self._record_matches_start_request(existing, record):
                return self._with_health(existing).to_dict()
            self._terminate_pid(existing.frontend_pid)
            self._terminate_pid(existing.backend_pid)
            existing.frontend_pid = None
            existing.backend_pid = None
            existing.status = "stopped"
            existing.last_stopped_at = utc_timestamp()
            self.registry.save(existing)

        instance_dir = self._instance_dir(record.instance_id)
        log_dir = ensure_absolute_dir(instance_dir / "logs")
        backend_pid = self._start_backend(record, log_dir / "backend.log")
        record.backend_pid = backend_pid
        if record.start_frontend:
            record.frontend_pid = self._start_frontend(record, log_dir / "frontend.log")
        record.status = "running"
        record.last_started_at = utc_timestamp()
        self.registry.save(record)
        return self._with_health(record).to_dict()

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

    def stop_instance(self, instance_id: str) -> dict[str, Any]:
        record = self._require_record(instance_id)
        self._terminate_pid(record.frontend_pid)
        self._terminate_pid(record.backend_pid)
        record.frontend_pid = None
        record.backend_pid = None
        record.status = "stopped"
        record.last_stopped_at = utc_timestamp()
        self.registry.save(record)
        return record.to_dict()

    def restart_instance(self, instance_id: str) -> dict[str, Any]:
        record = self._require_record(instance_id)
        payload = self._restart_payload(record)
        self.stop_instance(instance_id)
        return self.start_instance(payload)

    def delete_instance(self, instance_id: str) -> bool:
        record = self.registry.get(instance_id)
        if record:
            self._terminate_pid(record.frontend_pid)
            self._terminate_pid(record.backend_pid)
        return self.registry.delete(instance_id)

    def get_logs(self, instance_id: str, stream: str, tail_lines: int = 200) -> str:
        record = self._require_record(instance_id)
        stream_name = "frontend" if stream == "frontend" else "backend"
        log_path = self._instance_dir(record.instance_id) / "logs" / f"{stream_name}.log"
        return tail_text(log_path, tail_lines)

    def health(self, instance_id: str) -> dict[str, Any]:
        return self._with_health(self._require_record(instance_id)).to_dict()

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
        requested_backend_port = request.get("backend_port")
        requested_frontend_port = request.get("frontend_port")
        backend_port = int(requested_backend_port or allocate_port(DEFAULT_BACKEND_PORT))
        frontend_port = int(requested_frontend_port or allocate_port(DEFAULT_FRONTEND_PORT))
        if requested_backend_port and not is_port_free(backend_port):
            raise RuntimeError(f"backend port is already in use: {backend_port}")
        if request.get("start_frontend", True) and requested_frontend_port and not is_port_free(frontend_port):
            raise RuntimeError(f"frontend port is already in use: {frontend_port}")
        token = str(request.get("token") or f"polaris-{secrets.token_urlsafe(18)}")
        bench = request.get("bench")
        metadata = request.get("metadata")
        bench_payload: dict[str, Any] = bench if isinstance(bench, dict) else {}
        metadata_payload: dict[str, Any] = metadata if isinstance(metadata, dict) else {}

        return InstanceRecord(
            instance_id=instance_id,
            name=str(request.get("name") or workspace.name or instance_id),
            kind=str(request.get("kind") or "project"),
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

    def _with_health(self, record: InstanceRecord) -> InstanceRecord:
        backend_alive = is_process_alive(record.backend_pid)
        if backend_alive and record.backend_pid in {os.getpid(), os.getppid()}:
            backend_http_ok = True
        else:
            backend_http_ok = (
                self._http_ok(f"{record.backend_url}/health", record.token) if record.backend_url else False
            )
        frontend_pid_alive = is_process_alive(record.frontend_pid)
        frontend_http_ok = self._http_ok(record.frontend_url, record.token) if record.frontend_url else False
        if record.start_frontend:
            frontend_alive = frontend_pid_alive or frontend_http_ok
        else:
            frontend_alive = frontend_http_ok if record.frontend_url else True
        if backend_http_ok and not record.backend_pid:
            record.status = "observed"
        elif backend_http_ok and frontend_alive:
            record.status = "running"
        elif backend_alive or frontend_pid_alive:
            record.status = "starting"
        else:
            record.status = "stopped"
        if backend_http_ok:
            record.metadata["backend_health"] = "ok"
        elif backend_alive:
            record.metadata["backend_health"] = "starting"
        else:
            record.metadata["backend_health"] = "stopped"
        if frontend_http_ok:
            record.metadata["frontend_health"] = "ok"
        elif frontend_pid_alive:
            record.metadata["frontend_health"] = "starting"
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

    def _instance_dir(self, instance_id: str) -> Path:
        return ensure_absolute_dir(self.registry.home / sanitize_instance_id(instance_id))

    @staticmethod
    def _terminate_pid(pid: int | None) -> None:
        if not pid or pid <= 0:
            return
        with suppress(ProcessLookupError):
            if os.name == "posix":
                os.killpg(pid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)

    @staticmethod
    def _http_ok(url: str, token: str) -> bool:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                return 200 <= int(response.status) < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False
