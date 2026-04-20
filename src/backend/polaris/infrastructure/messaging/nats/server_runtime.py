"""Managed local NATS/JetStream runtime for Polaris backend."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from polaris.kernelone.fs.text_ops import open_text_log_append
from polaris.kernelone.storage.layout import kernelone_home

logger = logging.getLogger(__name__)

_STARTUP_TIMEOUT_SECONDS = 8.0
_managed_server: ManagedNATSServer | None = None
_managed_server_lock = asyncio.Lock()


def _first_nats_server_url(raw: str) -> str:
    token = str(raw or "").strip()
    if not token:
        return "nats://127.0.0.1:4222"
    return token.split(",", 1)[0].strip() or "nats://127.0.0.1:4222"


def _parse_local_nats_endpoint(url: str) -> tuple[str, int] | None:
    parsed = urlparse(_first_nats_server_url(url))
    host = str(parsed.hostname or "").strip().lower()
    port = int(parsed.port or 4222)
    if host in {"127.0.0.1", "localhost", "::1"}:
        return host, port
    return None


def should_manage_local_nats(url: str) -> bool:
    return _parse_local_nats_endpoint(url) is not None


def resolve_managed_nats_storage_root() -> Path:
    return (Path(kernelone_home()) / "runtime" / "nats" / "jetstream").resolve()


def resolve_nats_server_executable() -> Path | None:
    explicit = str(
        os.environ.get("KERNELONE_NATS_SERVER_BIN") or os.environ.get("POLARIS_NATS_SERVER_BIN") or ""
    ).strip()
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        return candidate if candidate.exists() else None

    discovered = shutil.which("nats-server")
    if discovered:
        return Path(discovered).resolve()

    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if not local_app_data:
        return None

    packages_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if not packages_root.exists():
        return None

    candidates = sorted(packages_root.glob("NATSAuthors.NATSServer*/*/nats-server.exe"))
    if candidates:
        return candidates[-1].resolve()
    return None


def _can_accept_tcp(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


async def _wait_until_nats_accepts(host: str, port: int, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + max(0.5, float(timeout or 0.0))
    while asyncio.get_running_loop().time() < deadline:
        if await asyncio.to_thread(_can_accept_tcp, host, port):
            return True
        await asyncio.sleep(0.1)
    return False


@dataclass
class ManagedNATSServer:
    executable: Path
    host: str
    port: int
    storage_root: Path
    stdout_log_path: Path
    stderr_log_path: Path

    process: subprocess.Popen[bytes] | None = None
    _stdout_handle: object | None = None
    _stderr_handle: object | None = None

    async def ensure_running(self) -> None:
        if self.process and self.process.poll() is None:
            return

        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.stdout_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.stderr_log_path.parent.mkdir(parents=True, exist_ok=True)

        self._stdout_handle = open_text_log_append(str(self.stdout_log_path), newline="\n")
        self._stderr_handle = open_text_log_append(str(self.stderr_log_path), newline="\n")

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        command = [
            str(self.executable),
            "-js",
            "-a",
            self.host,
            "-p",
            str(self.port),
            "-sd",
            str(self.storage_root),
        ]
        self.process = subprocess.Popen(
            command,
            stdout=self._stdout_handle,
            stderr=self._stderr_handle,
            creationflags=creationflags,
        )

        ready = await _wait_until_nats_accepts(self.host, self.port, _STARTUP_TIMEOUT_SECONDS)
        if ready:
            logger.info(
                "Managed NATS server ready: pid=%s host=%s port=%s storage=%s",
                self.process.pid if self.process else None,
                self.host,
                self.port,
                self.storage_root,
            )
            return

        await self.stop()
        raise RuntimeError(
            "Managed NATS server failed to become reachable: "
            f"host={self.host} port={self.port} stderr={self.stderr_log_path}"
        )

    async def stop(self) -> None:
        process = self.process
        self.process = None

        try:
            if process and process.poll() is None:
                process.terminate()
                try:
                    await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=5.0)
                except asyncio.TimeoutError:
                    process.kill()
                    await asyncio.to_thread(process.wait)
        finally:
            for handle_name in ("_stdout_handle", "_stderr_handle"):
                handle = getattr(self, handle_name, None)
                setattr(self, handle_name, None)
                with contextlib.suppress(Exception):
                    if handle is not None:
                        handle.close()


async def ensure_local_nats_runtime(nats_url: str) -> None:
    endpoint = _parse_local_nats_endpoint(nats_url)
    if endpoint is None:
        return

    host, port = endpoint
    if await asyncio.to_thread(_can_accept_tcp, host, port):
        return

    executable = resolve_nats_server_executable()
    if executable is None:
        raise RuntimeError("nats-server executable not found for managed local runtime")

    storage_root = resolve_managed_nats_storage_root()
    logs_root = storage_root.parent

    global _managed_server
    async with _managed_server_lock:
        if await asyncio.to_thread(_can_accept_tcp, host, port):
            return
        if _managed_server is None:
            _managed_server = ManagedNATSServer(
                executable=executable,
                host=host,
                port=port,
                storage_root=storage_root,
                stdout_log_path=logs_root / "nats-server.stdout.log",
                stderr_log_path=logs_root / "nats-server.stderr.log",
            )
        await _managed_server.ensure_running()


async def shutdown_local_nats_runtime() -> None:
    global _managed_server
    async with _managed_server_lock:
        server = _managed_server
        _managed_server = None
    if server is not None:
        await server.stop()


__all__ = [
    "ensure_local_nats_runtime",
    "resolve_managed_nats_storage_root",
    "resolve_nats_server_executable",
    "should_manage_local_nats",
    "shutdown_local_nats_runtime",
]
