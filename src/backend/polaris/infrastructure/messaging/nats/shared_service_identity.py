"""Linux native shared-service identity. Records never authorize PID-only signals."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import stat
import sys
import time
import uuid
from pathlib import Path
from typing import Any


class SharedNATSError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def require_capability() -> None:
    if sys.platform != "linux" or not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise SharedNATSError("shared_nats_capability_unavailable", "Linux proc/pidfd descriptor fencing required")
    try:
        descriptor = os.pidfd_open(os.getpid())
        try:
            signal.pidfd_send_signal(descriptor, 0)
        finally:
            os.close(descriptor)
    except (OSError, NotImplementedError) as exc:
        raise SharedNATSError("shared_nats_capability_unavailable", "pidfd syscalls unavailable or denied") from exc


def directory_identity(path: Path) -> list[int]:
    """Reject symlink aliases throughout the directory path, not only the leaf."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise SharedNATSError("shared_nats_identity_conflict", "unsafe directory path")
    info = path.stat()
    return [info.st_dev, info.st_ino]


def process_identity(pid: int) -> dict[str, Any]:
    proc = Path(f"/proc/{pid}")
    status = (proc / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    if status[0] == "Z":
        raise ProcessLookupError(pid)
    executable = (proc / "exe").resolve(strict=True)
    info = executable.stat()
    return {
        "pid": pid,
        "start_ticks": int(status[19]),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip(),
        "uid": proc.stat().st_uid,
        "executable": str(executable),
        "executable_identity": [info.st_dev, info.st_ino],
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "pgid": os.getpgid(pid),
        "sid": os.getsid(pid),
    }


def capture_identity(
    pid: int,
    *,
    generation: str,
    executable: Path,
    store: Path,
    host: str,
    port: int,
    descriptor_identities: tuple[tuple[int, int, int], ...],
) -> dict[str, Any]:
    identity = process_identity(pid)
    if identity["executable"] != str(executable.resolve()) or identity["pgid"] != pid or identity["sid"] != pid:
        raise SharedNATSError("shared_nats_identity_conflict", "spawned executable/group mismatch")
    identity.update(
        {
            "generation": generation,
            "store": str(store),
            "store_identity": directory_identity(store),
            "host": host,
            "port": port,
            "lock_descriptors": [list(value) for value in descriptor_identities],
        }
    )
    validate_identity(identity)
    return identity


def validate_identity_record(identity: dict[str, Any]) -> None:
    """Validate persisted proof before using even a dead PID for store recovery."""
    try:
        for key in ("pid", "start_ticks", "uid", "pgid", "sid"):
            value = identity[key]
            if type(value) is not int or value < (0 if key == "uid" else 1):
                raise ValueError("invalid process field")
        uuid.UUID(identity["boot_id"])
        generation = identity["generation"]
        if not isinstance(generation, str) or not generation.startswith("polaris-shared-"):
            raise ValueError("invalid generation")
        uuid.UUID(hex=generation.removeprefix("polaris-shared-"))
        for key in ("executable", "store"):
            if not isinstance(identity[key], str) or not Path(identity[key]).is_absolute():
                raise ValueError("invalid path binding")
        for key in ("executable_identity", "store_identity"):
            value = identity[key]
            if (
                not isinstance(value, list)
                or len(value) != 2
                or any(type(item) is not int or item < 0 for item in value)
            ):
                raise ValueError("invalid inode binding")
        digest = identity["executable_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid executable digest")
        if (
            identity["host"] not in {"127.0.0.1", "localhost", "::1"}
            or type(identity["port"]) is not int
            or not 0 < identity["port"] < 65536
        ):
            raise ValueError("invalid endpoint")
        locks = identity["lock_descriptors"]
        if (
            not isinstance(locks, list)
            or len(locks) != 2
            or any(
                not isinstance(row, list) or len(row) != 3 or any(type(item) is not int or item < 0 for item in row)
                for row in locks
            )
        ):
            raise ValueError("invalid fence descriptors")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SharedNATSError("shared_nats_identity_conflict", "incomplete or malformed identity record") from exc


def validate_identity(identity: dict[str, Any]) -> None:
    validate_identity_record(identity)
    try:
        pid = int(identity["pid"])
        current = process_identity(pid)
        if current["uid"] != os.getuid() or any(identity.get(key) != value for key, value in current.items()):
            raise ValueError("process incarnation mismatch")
        if directory_identity(Path(identity["store"])) != identity["store_identity"]:
            raise ValueError("store incarnation mismatch")
        descriptors = identity["lock_descriptors"]
        if not isinstance(descriptors, list) or len(descriptors) < 2:
            raise ValueError("missing inherited fences")
        for fd, device, inode in descriptors:
            info = os.stat(f"/proc/{pid}/fd/{int(fd)}")
            if not stat.S_ISREG(info.st_mode) or [info.st_dev, info.st_ino] != [device, inode]:
                raise ValueError("inherited fence mismatch")
        expected = [
            identity["executable"],
            "-js",
            "-a",
            identity["host"],
            "-p",
            str(identity["port"]),
            "-sd",
            identity["store"],
            "--name",
            identity["generation"],
        ]
        actual = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8").rstrip("\0").split("\0")
        if actual != expected:
            raise ValueError("native invocation binding mismatch")
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise SharedNATSError("shared_nats_identity_conflict", "live identity proof unavailable or mismatched") from exc


def incarnation_dead(identity: dict[str, Any]) -> bool:
    """PID reuse is not permission to signal; absence alone permits lock acquisition."""
    try:
        current = process_identity(int(identity["pid"]))
    except (FileNotFoundError, ProcessLookupError):
        return True
    except (OSError, KeyError, TypeError, ValueError):
        return False
    return any(current.get(key) != identity.get(key) for key in ("boot_id", "start_ticks"))


def probe_info(host: str, port: int, timeout: float, *, ping: bool = False) -> dict[str, Any] | None:
    """Native bounded INFO; optional authenticated-free PONG only for owned servers."""
    deadline = time.monotonic() + max(0.001, timeout)
    try:
        with socket.create_connection((host, port), timeout=max(0.001, timeout)) as connection:
            data = b""
            while b"\r\n" not in data and len(data) < 8192:
                connection.settimeout(max(0.001, deadline - time.monotonic()))
                chunk = connection.recv(8192 - len(data))
                if not chunk:
                    return None
                data += chunk
            line = data.split(b"\r\n", 1)[0]
            if not line.startswith(b"INFO "):
                raise SharedNATSError("shared_nats_incompatible_service")
            info = json.loads(line[5:].decode("utf-8"))
            if not isinstance(info, dict) or info.get("jetstream") is not True:
                raise SharedNATSError("shared_nats_incompatible_service")
            if ping:
                if info.get("auth_required") or info.get("tls_required"):
                    raise SharedNATSError(
                        "shared_nats_identity_conflict", "owned service unexpectedly requires auth/TLS"
                    )
                connection.sendall(b'CONNECT {"verbose":false}\r\nPING\r\n')
                data = b""
                while b"PONG\r\n" not in data and len(data) < 8192:
                    connection.settimeout(max(0.001, deadline - time.monotonic()))
                    chunk = connection.recv(8192 - len(data))
                    if not chunk:
                        return None
                    data += chunk
                if b"PONG\r\n" not in data:
                    return None
            return info
    except (ConnectionRefusedError, TimeoutError):
        return None
    except (UnicodeError, ValueError) as exc:
        raise SharedNATSError("shared_nats_incompatible_service") from exc
