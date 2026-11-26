"""Resolve a credential-free research peer from the local instance registry."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psutil
from polaris.cells.instances.public.service import query_instance_records
from polaris.cells.storage.layout.public import canonical_project_runtime_root
from polaris.kernelone.storage import resolve_storage_roots_read_only


def _loopback_origin(raw: object, port: object) -> str:
    if not isinstance(raw, str) or type(port) is not int or not (1 <= port <= 65535):
        raise PermissionError("research instance has no bound local endpoint")
    if any(ch in raw for ch in ("@", "\\", "%", "#", "?")) or any(not 32 < ord(ch) < 127 for ch in raw):
        raise PermissionError("invalid registered research endpoint")
    try:
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or parsed.path not in {"", "/"} or parsed.port != port:
            raise ValueError("invalid origin")
        host = ipaddress.ip_address("127.0.0.1" if parsed.hostname == "localhost" else str(parsed.hostname))
        if not host.is_loopback:
            raise ValueError("non-loopback address")
    except ValueError:
        raise PermissionError("research instance must be bound to a loopback origin") from None
    host_text = f"[{host}]" if host.version == 6 else str(host)
    return f"{parsed.scheme}://{host_text}:{port}"


def _owns_listener(pid: object, port: int, host: str) -> bool:
    """Avoid sending an operator credential to another process reusing an old registry port."""
    if type(pid) is not int or pid <= 0:
        return False
    try:
        process = psutil.Process(pid)
        # psutil 5.x uses connections; the supported 6.x/7.x spelling is net_connections.
        sockets = (getattr(process, "net_connections", None) or process.connections)(kind="tcp")
        address = ipaddress.ip_address(host)
        for sock in sockets:
            if sock.status != psutil.CONN_LISTEN or sock.laddr.port != port:
                continue
            bound = ipaddress.ip_address(sock.laddr.ip)
            if bound.version == address.version and (bound == address or bound.is_unspecified):
                return True
        return False
    except (psutil.Error, OSError):
        return False


def registered_research_instance(instance_id: str, *, home: Path | None = None) -> dict[str, Any]:
    """Resolve one independent local workspace and origin through the read-only registry."""
    records = query_instance_records(home=home)
    if not isinstance(records, list):
        raise RuntimeError("research instance registry is unavailable")
    matches = [r for r in records if r.get("instance_id") == instance_id]
    if not matches:
        raise KeyError("research instance is not registered")
    if len(matches) != 1:
        raise PermissionError("research instance registration is ambiguous")
    record = matches[0]
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        raise PermissionError("research instance has no workspace binding")
    binding = metadata.get("backend_binding")
    if binding not in (None, "isolated_backend_instance") or (
        record.get("kind") == "bench_project" and binding != "isolated_backend_instance"
    ):
        raise PermissionError("research exchange requires an independently bound instance")
    workspace = record.get("workspace")
    if not isinstance(workspace, str) or not Path(workspace).is_absolute():
        raise PermissionError("research instance workspace is not bound")
    try:
        canonical = Path(workspace).resolve(strict=True)
    except (OSError, RuntimeError):
        raise PermissionError("registered research workspace cannot be resolved") from None
    if str(canonical) != workspace or not canonical.is_dir():
        raise PermissionError("research instance workspace is not canonical")
    runtime_root = record.get("runtime_root")
    if (
        runtime_root != canonical_project_runtime_root(workspace)
        and runtime_root != resolve_storage_roots_read_only(workspace).runtime_root
    ):
        raise PermissionError("research instance runtime does not match its workspace")
    port = record.get("backend_port")
    if type(port) is not int:
        raise PermissionError("research instance has no bound backend port")
    origin = _loopback_origin(record.get("backend_url"), port)
    identity = metadata.get("backend_process_identity")
    if identity is not None and (
        not isinstance(identity, dict)
        or identity.get("instance_id") != instance_id
        or identity.get("workspace") != workspace
        or identity.get("pid") != record.get("backend_pid")
    ):
        raise PermissionError("research instance process binding disagrees with its registration")
    if record.get("status") not in {"running", "starting"} or not _owns_listener(
        record.get("backend_pid"), port, urlsplit(origin).hostname or ""
    ):
        raise PermissionError("registered research backend does not own its listener")
    return {"instance_id": instance_id, "workspace": workspace, "backend_url": origin}
