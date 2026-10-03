"""Managed local NATS/JetStream runtime for Polaris backend."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

from polaris.kernelone.fs.text_ops import open_text_log_append
from polaris.kernelone.storage.layout import kernelone_home

logger = logging.getLogger(__name__)

_STARTUP_TIMEOUT_SECONDS = 60.0
_managed_server: ManagedNATSServer | None = None
_managed_server_lock = asyncio.Lock()
_shared_attachment: dict[str, Any] | None = None


def _first_nats_server_url(raw: str) -> str:
    token = str(raw or "").strip()
    if not token:
        return "nats://127.0.0.1:4222"
    return token.split(",", 1)[0].strip() or "nats://127.0.0.1:4222"


def _parse_local_nats_endpoint(url: str) -> tuple[str, int] | None:
    parsed = urlparse(_first_nats_server_url(url))
    if parsed.scheme != "nats":
        return None
    host = str(parsed.hostname or "").strip().lower()
    port = int(parsed.port or 4222)
    if host in {"127.0.0.1", "localhost", "::1"}:
        return host, port
    return None


def _sanitize_nats_url(raw: str) -> str:
    parsed = urlparse(_first_nats_server_url(raw))
    if not parsed.scheme or not parsed.netloc:
        return _first_nats_server_url(raw)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunparse(parsed._replace(netloc=netloc))


def should_manage_local_nats(url: str) -> bool:
    return _parse_local_nats_endpoint(url) is not None


def resolve_managed_nats_storage_root() -> Path:
    return (Path(kernelone_home()) / "runtime" / "nats" / "jetstream").resolve()


def resolve_nats_server_executable() -> Path | None:
    explicit = str(os.environ.get("KERNELONE_NATS_SERVER_BIN") or "").strip()
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


def _startup_budget(value: float) -> float:
    budget = float(value)
    if not math.isfinite(budget) or not 0.0 < budget <= 90.0:
        raise ValueError("Managed NATS startup budget must be finite and in (0, 90] seconds")
    return budget


async def _probe_nats_endpoint(
    host: str,
    port: int,
    timeout: float,
) -> Literal["ready", "absent", "pending", "incompatible"]:
    """Distinguish absent service, pending readiness and explicit wrong protocol."""

    async def read_greeting() -> Literal["ready", "absent", "pending", "incompatible"]:
        # Resolve explicitly rather than parsing multi-address exception text
        # or introducing Python-3.12-only all_errors/ExceptionGroup behavior.
        addresses = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        for _family, _kind, _protocol, _name, address in addresses:
            writer: asyncio.StreamWriter | None = None
            try:
                reader, writer = await asyncio.open_connection(address[0], address[1], limit=8192)
                greeting = await reader.readuntil(b"\r\n")
                if not greeting.startswith(b"INFO "):
                    return "incompatible"
                info = json.loads(greeting[5:].decode("utf-8"))
                return "ready" if isinstance(info, dict) and info.get("jetstream") is True else "incompatible"
            except ConnectionRefusedError:
                continue
            finally:
                if writer is not None:
                    writer.close()
        return "absent"

    try:
        return await asyncio.wait_for(read_greeting(), timeout=timeout)
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        return "pending"
    except (ValueError, asyncio.LimitOverrunError):
        return "incompatible"


async def _nats_protocol_ready(host: str, port: int, timeout: float) -> bool:
    return await _probe_nats_endpoint(host, port, timeout) == "ready"


async def _observe_existing_endpoint(
    host: str,
    port: int,
    deadline: float,
) -> Literal["ready", "absent", "pending", "incompatible"]:
    loop = asyncio.get_running_loop()
    while (remaining := deadline - loop.time()) > 0.0:
        state = await _probe_nats_endpoint(host, port, remaining)
        if state != "pending":
            return state
        await asyncio.sleep(min(0.1, max(0.0, deadline - loop.time())))
    return "pending"


async def _wait_until_nats_accepts(
    host: str,
    port: int,
    timeout: float,
    *,
    process: subprocess.Popen[bytes] | None = None,
) -> bool:
    """Race native readiness against exact owned exit within one startup budget."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _startup_budget(timeout)

    async def wait_for_readiness() -> bool:
        while (remaining := deadline - loop.time()) > 0.0:
            state = await _probe_nats_endpoint(host, port, remaining)
            if state == "ready":
                return True
            if state == "incompatible":
                raise RuntimeError(f"Managed NATS endpoint is not a NATS JetStream service: host={host} port={port}")
            await asyncio.sleep(min(0.1, max(0.0, deadline - loop.time())))
        return False

    async def wait_for_owned_exit(owned: subprocess.Popen[bytes]) -> None:
        # Do not use a blocking wait thread: cancelling its Task would leave
        # an uninterruptible waiter behind after successful readiness.
        while (returncode := owned.poll()) is None:
            await asyncio.sleep(0.05)
        raise RuntimeError(f"Managed NATS startup failed: process_exited returncode={returncode}")

    readiness_task = asyncio.create_task(wait_for_readiness())
    monitors: list[asyncio.Task[bool] | asyncio.Task[None]] = [readiness_task]
    if process is not None:
        monitors.append(asyncio.create_task(wait_for_owned_exit(process)))
    try:
        done, _pending = await asyncio.wait(
            monitors,
            timeout=max(0.0, deadline - loop.time()),
            return_when=asyncio.FIRST_COMPLETED,
        )
        # Exit wins a simultaneous ready/exit observation, even if the async
        # exit monitor has not yet received its next turn.
        if process is not None and (returncode := process.poll()) is not None:
            raise RuntimeError(f"Managed NATS startup failed: process_exited returncode={returncode}")
        return readiness_task.result() if readiness_task in done else False
    finally:
        for monitor in monitors:
            if not monitor.done():
                monitor.cancel()
        cleanup = asyncio.gather(*monitors, return_exceptions=True)
        cancellation: asyncio.CancelledError | None = None
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as exc:
                cancellation = exc
        cleanup.result()
        if cancellation is not None:
            raise cancellation


@dataclass
class ManagedNATSServer:
    executable: Path
    host: str
    port: int
    storage_root: Path
    stdout_log_path: Path
    stderr_log_path: Path

    process: subprocess.Popen[bytes] | None = None
    _stdout_handle: Any | None = None
    _stderr_handle: Any | None = None
    _drain_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    async def ensure_running(self, *, startup_timeout_seconds: float = _STARTUP_TIMEOUT_SECONDS) -> None:
        budget = _startup_budget(startup_timeout_seconds)
        started = asyncio.get_running_loop().time()
        try:
            if self.process is None or self.process.poll() is not None:
                await self.stop()
                self.storage_root.mkdir(parents=True, exist_ok=True)
                self.stdout_log_path.parent.mkdir(parents=True, exist_ok=True)
                self.stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
                self._stdout_handle = open_text_log_append(str(self.stdout_log_path), newline="\n")
                self._stderr_handle = open_text_log_append(str(self.stderr_log_path), newline="\n")
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                self.process = subprocess.Popen(
                    [str(self.executable), "-js", "-a", self.host, "-p", str(self.port), "-sd", str(self.storage_root)],
                    stdout=self._stdout_handle,
                    stderr=self._stderr_handle,
                    creationflags=creationflags,
                )
            remaining = budget - (asyncio.get_running_loop().time() - started)
            ready = remaining > 0.0 and await _wait_until_nats_accepts(
                self.host,
                self.port,
                remaining,
                process=self.process,
            )
            if not ready:
                raise RuntimeError(
                    "Managed NATS startup_deadline_exceeded: "
                    f"host={self.host} port={self.port} budget_seconds={budget} stderr={self.stderr_log_path}"
                )
            logger.info(
                "Managed NATS server ready: pid=%s host=%s port=%s storage=%s",
                self.process.pid if self.process else None,
                self.host,
                self.port,
                self.storage_root,
            )
        except (OSError, RuntimeError, ValueError, asyncio.CancelledError):
            await self.stop()
            raise

    async def stop(self) -> None:
        process = self.process
        if self._drain_task is None:
            self._drain_task = asyncio.create_task(self._drain_owned_process(process))
        drain_task = self._drain_task
        cancellation: asyncio.CancelledError | None = None
        try:
            while not drain_task.done():
                try:
                    await asyncio.shield(drain_task)
                except asyncio.CancelledError as exc:
                    cancellation = exc
            drain_task.result()
        finally:
            # Never forget a live child, even if termination itself fails.
            if (process is None or process.poll() is not None) and self.process is process:
                self.process = None
            self._drain_task = None
            for handle_name in ("_stdout_handle", "_stderr_handle"):
                handle = getattr(self, handle_name, None)
                setattr(self, handle_name, None)
                with contextlib.suppress(Exception):
                    if handle is not None:
                        handle.close()
        if cancellation is not None:
            raise cancellation

    @staticmethod
    async def _drain_owned_process(process: subprocess.Popen[bytes] | None) -> None:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await asyncio.to_thread(process.wait)


async def ensure_local_nats_runtime(
    nats_url: str,
    *,
    startup_timeout_seconds: float = _STARTUP_TIMEOUT_SECONDS,
    allow_autostart: bool = True,
) -> None:
    endpoint = _parse_local_nats_endpoint(nats_url)
    if endpoint is None:
        return

    host, port = endpoint
    budget = _startup_budget(startup_timeout_seconds)
    deadline = asyncio.get_running_loop().time() + budget
    state = await _observe_existing_endpoint(host, port, deadline)
    from .shared_service_runtime import ensure_shared_nats_runtime, ready_endpoint_is_external

    if state == "ready" and await ready_endpoint_is_external(host, port, deadline):
        return
    if state == "incompatible":
        raise RuntimeError(f"Managed NATS endpoint is not a NATS JetStream service: host={host} port={port}")
    if state == "pending":
        raise RuntimeError("Managed NATS startup_deadline_exceeded while awaiting existing service readiness")

    parsed = urlparse(_first_nats_server_url(nats_url))
    if (
        not allow_autostart
        or parsed.username is not None
        or parsed.password is not None
        or os.environ.get("KERNELONE_NATS_USER")
        or os.environ.get("KERNELONE_NATS_PASSWORD")
    ):
        if state == "ready":
            return
        raise RuntimeError("shared_nats_external_service_unavailable: credentialed endpoint cannot autostart")

    executable = resolve_nats_server_executable()
    if executable is None:
        raise RuntimeError("nats-server executable not found for managed local runtime")

    storage_root = resolve_managed_nats_storage_root()
    global _shared_attachment
    try:
        await asyncio.wait_for(
            _managed_server_lock.acquire(), timeout=max(0.0, deadline - asyncio.get_running_loop().time())
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Managed NATS startup_deadline_exceeded while awaiting local startup ownership") from exc
    try:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0.0:
            raise RuntimeError("Managed NATS startup_deadline_exceeded while awaiting local startup ownership")
        _shared_attachment = await ensure_shared_nats_runtime(host, port, executable, storage_root, deadline)
    finally:
        _managed_server_lock.release()


async def shutdown_local_nats_runtime() -> None:
    """Detach this backend; only explicit maintenance may stop the shared writer."""
    global _shared_attachment
    async with _managed_server_lock:
        _shared_attachment = None


def get_managed_nats_runtime_snapshot(nats_url: str) -> dict[str, Any]:
    """Return a side-effect-free snapshot of managed local NATS runtime state."""

    configured_url = _sanitize_nats_url(nats_url)
    endpoint = _parse_local_nats_endpoint(nats_url)
    executable = resolve_nats_server_executable()
    storage_root = resolve_managed_nats_storage_root()
    logs_root = storage_root.parent
    server = _managed_server
    process = server.process if server is not None else None
    process_running = bool(process is not None and process.poll() is None)
    host = endpoint[0] if endpoint else ""
    port = endpoint[1] if endpoint else 0
    tcp_reachable = _can_accept_tcp(host, port) if endpoint else False
    attachment = _shared_attachment
    if attachment is not None and (attachment.get("host"), attachment.get("port")) != endpoint:
        attachment = None
    if attachment is not None and (attachment.get("host"), attachment.get("port")) == endpoint:
        from .shared_service_identity import SharedNATSError, validate_identity

        try:
            validate_identity(attachment)
            process_running = True
        except SharedNATSError:
            process_running = False

    return {
        "configured_url": configured_url,
        "managed": endpoint is not None,
        "host": host,
        "port": port,
        "tcp_reachable": tcp_reachable,
        "executable_found": executable is not None,
        "executable_path": str(executable) if executable is not None else None,
        "storage_root": str(storage_root),
        "stdout_log_path": str(server.stdout_log_path if server else logs_root / "nats-server.stdout.log"),
        "stderr_log_path": str(server.stderr_log_path if server else logs_root / "nats-server.stderr.log"),
        "process_pid": attachment.get("pid") if attachment else process.pid if process is not None else None,
        "process_running": process_running,
        "ownership_mode": "shared_service" if attachment else "external_or_legacy" if tcp_reachable else "unattached",
        "service_generation": attachment.get("generation") if attachment else None,
        "identity_status": "verified" if attachment and process_running else "unverified",
    }


__all__ = [
    "ensure_local_nats_runtime",
    "get_managed_nats_runtime_snapshot",
    "resolve_managed_nats_storage_root",
    "resolve_nats_server_executable",
    "should_manage_local_nats",
    "shutdown_local_nats_runtime",
]
