"""tests.agent_stress 使用的官方 backend 自举器。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.agent_stress.paths import (
    BACKEND_ROOT,
    REPO_ROOT,
    ensure_backend_root_on_syspath,
)

ensure_backend_root_on_syspath()

PROJECT_ROOT = BACKEND_ROOT

from tests.agent_stress.backend_context import (
    BackendContext,
    get_desktop_backend_info_path,
    resolve_backend_context,
)
from tests.agent_stress.preflight import BackendPreflightProbe, BackendPreflightStatus
from tests.agent_stress.stress_path_policy import (
    default_stress_runtime_root,
    default_stress_workspace_base,
    ensure_stress_runtime_root,
    ensure_stress_workspace_path,
)

logger = logging.getLogger(__name__)

BOOTSTRAP_CONTEXT_SOURCE = "terminal-auto-bootstrap"
DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS = 30.0
DEFAULT_BOOTSTRAP_WORKSPACE_PREFIX = "tests-agent-stress-backend"
DEFAULT_BOOTSTRAP_RAMDISK = ensure_stress_runtime_root(
    default_stress_runtime_root("tests-agent-stress-runtime")
)
BACKEND_SERVER_SCRIPT = BACKEND_ROOT / "server.py"


def _fresh_bootstrap_workspace() -> Path:
    """Allocate a unique stress bootstrap workspace to avoid cross-run pollution."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    suffix = f"{timestamp}-{secrets.token_hex(4)}"
    return ensure_stress_workspace_path(
        default_stress_workspace_base(f"{DEFAULT_BOOTSTRAP_WORKSPACE_PREFIX}-{suffix}")
    )


def _resolve_bootstrap_workspace(startup_workspace: Path | None) -> Path:
    if startup_workspace is not None:
        return ensure_stress_workspace_path(startup_workspace)
    return _fresh_bootstrap_workspace()


def _resolve_bootstrap_ramdisk_root(ramdisk_root: Path | None) -> Path:
    return ensure_stress_runtime_root(ramdisk_root or DEFAULT_BOOTSTRAP_RAMDISK)


def _resolve_python_executable() -> str:
    configured = str(os.environ.get("POLARIS_PYTHON") or "").strip()
    if configured and Path(configured).exists():
        candidate = str(Path(configured).resolve())
        _validate_python_environment(candidate)
        return candidate

    venv_python = (
        REPO_ROOT
        / ".venv"
        / ("Scripts" if os.name == "nt" else "bin")
        / ("python.exe" if os.name == "nt" else "python")
    )
    if venv_python.exists():
        candidate = str(venv_python.resolve())
        _validate_python_environment(candidate)
        return candidate

    # No POLARIS_PYTHON, no .venv: validate sys.executable before using it.
    _validate_python_environment(str(Path(sys.executable).resolve()))
    return str(Path(sys.executable).resolve())


def _validate_python_environment(python_path: str) -> None:
    """Verify that the Python at *python_path* can import key Polaris modules.

    All checks are executed inside the target interpreter via subprocess so the
    result reflects the actual environment the backend will run in (important
    for mixed CI / Docker scenarios where the host and container interpreters
    differ).

    The critical-module list intentionally omits Polaris-internal packages
    (e.g.  "app.main") because their import path varies with deployment
    structure (e.g. polaris/ vs. app/ after the ACGA 2.0 migration).
    If those are missing the backend will fail loudly later with a clear
    ImportError — the silent wrong-interpreter problem we prevent here is
    specifically caused by missing third-party *foundation* packages.

    Raises
    ------
    BackendBootstrapError
        If the interpreter is inaccessible or lacks required third-party
        packages.  The error details include the Python executable path and
        the list of modules that were found vs.  missing so operators can
        diagnose mixed-environment problems.
    """
    import subprocess

    # Foundation packages required by the backend bootstrap path.
    # These are consistent across all deployment layouts.
    critical_modules: tuple[str, ...] = (
        "fastapi",
        "uvicorn",
        "pydantic",
    )

    # 1. Quick existence / version smoke-test using the interpreter itself.
    try:
        version_result = subprocess.run(
            [python_path, "-c", "import sys; print(sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise BackendBootstrapError(
            f"Python executable is not accessible: {python_path}",
            details={
                "python_path": python_path,
                "reason": f"OSError: {exc}",
            },
        ) from exc
    except subprocess.TimeoutExpired:
        raise BackendBootstrapError(
            f"Python executable is unresponsive (timeout): {python_path}",
            details={"python_path": python_path},
        )

    if version_result.returncode != 0:
        raise BackendBootstrapError(
            f"Python executable failed version check: {python_path}",
            details={
                "python_path": python_path,
                "returncode": version_result.returncode,
                "stderr": version_result.stderr,
            },
        )

    # 2. Probe site-packages presence — an empty interpreter with no packages
    #    will cause immediate import failures in mixed CI/Docker scenarios.
    site_packages_check = subprocess.run(
        [python_path, "-c", "import site; print(site.getsitepackages())"],
        capture_output=True,
        text=True,
        timeout=10,
        encoding="utf-8",
        errors="replace",
    )
    has_site_packages = (
        site_packages_check.returncode == 0
        and bool(site_packages_check.stdout.strip())
    )

    # 3. Import check inside the target interpreter.  Use a compact
    #    single-line script to avoid Windows -c length limits.
    #    Each find_spec call is guarded so a failing module does not
    #    corrupt the output for subsequent modules.
    import_probe_code = (
        "import sys, importlib.util, json; "
        + "; ".join(
            f"print(('OK' if importlib.util.find_spec('{m}') else 'MISSING'))"
            for m in critical_modules
        )
        + "; print('__SEP__'); "
        + "print(json.dumps(sys.path[:6], ensure_ascii=False))"
    )
    probe_result = subprocess.run(
        [python_path, "-c", import_probe_code],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
        encoding="utf-8",
        errors="replace",
    )

    found: list[str] = []
    missing: list[str] = []
    sys_path: list[str] = []

    if probe_result.returncode == 0:
        stdout = probe_result.stdout.strip()
        sep_idx = stdout.rfind("__SEP__")
        if sep_idx >= 0:
            spec_part = stdout[:sep_idx].strip()
            path_part = stdout[sep_idx + len("__SEP__") :].strip()
            for module_name, line in zip(critical_modules, spec_part.splitlines()):
                stripped = line.strip()
                if stripped == "OK":
                    found.append(module_name)
                else:
                    missing.append(module_name)
            try:
                sys_path = json.loads(path_part)
            except json.JSONDecodeError:
                pass
        else:
            # No separator: treat all as potentially missing.
            missing = list(critical_modules)
    else:
        # Probe crashed: treat as all missing, include stderr for diagnostics.
        missing = list(critical_modules)

    if missing or not has_site_packages:
        raise BackendBootstrapError(
            f"Python environment is unsuitable for backend bootstrap: "
            f"missing={missing}, has_site_packages={has_site_packages}",
            details={
                "python_path": python_path,
                "has_site_packages": has_site_packages,
                "sys_path": sys_path,
                "found_modules": found,
                "missing_modules": missing,
                "probe_stderr": probe_result.stderr[:500] if probe_result.returncode != 0 else "",
            },
        )


def _build_desktop_backend_payload(
    *,
    state: str,
    ready: bool,
    source: str,
    backend_url: str = "",
    token: str = "",
    port: int | None = None,
    pid: int | None = None,
    last_error: str = "",
    last_exit_code: int | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "state": str(state or "stopped"),
        "ready": bool(ready),
        "restarts": 0,
        "lastError": str(last_error or ""),
        "lastExitCode": last_exit_code if isinstance(last_exit_code, int) else None,
        "backend": {
            "port": port if isinstance(port, int) and port > 0 else None,
            "token": str(token or "").strip() or None,
            "baseUrl": str(backend_url or "").strip() or None,
            "pid": pid if isinstance(pid, int) and pid > 0 else None,
        },
    }


def _write_desktop_backend_info(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )


def _parse_backend_event(line: str) -> dict[str, Any]:
    text = str(line or "").strip()
    if not text.startswith("{") or not text.endswith("}"):
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        # JSON 解析失败：记录错误并返回空字典
        logger.debug(f"JSON parse error: {e}")
        return {}
    return payload if isinstance(payload, dict) else {}


async def _probe_preflight_status(context: BackendContext) -> BackendPreflightStatus:
    async with BackendPreflightProbe(
        backend_url=context.backend_url,
        token=context.token,
        timeout=5.0,
    ) as probe:
        report = await probe.run()
    return report.status


class BackendBootstrapError(RuntimeError):
    """Raised when tests.agent_stress cannot auto-bootstrap backend."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass
class ManagedBackendSession:
    """Managed backend context for tests.agent_stress."""

    context: BackendContext
    auto_bootstrapped: bool = False
    startup_workspace: str = ""
    ramdisk_root: str = ""
    desktop_info_path: str = ""

    _process: asyncio.subprocess.Process | None = None
    _stdout_task: asyncio.Task[None] | None = None
    _stderr_task: asyncio.Task[None] | None = None
    _watch_task: asyncio.Task[None] | None = None
    _recent_stdout: deque[str] | None = None
    _recent_stderr: deque[str] | None = None
    _startup_port: int | None = None
    _startup_error: str = ""

    async def __aenter__(self) -> ManagedBackendSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if not self.auto_bootstrapped:
            return
        await self._terminate_backend()

    async def _terminate_backend(self) -> None:
        process = self._process
        if process is None:
            await self._drain_background_tasks(timeout=1.0)
            return

        pid = process.pid
        if process.returncode is None:
            if os.name == "nt" and pid:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            else:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                if os.name == "nt" and pid:
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill",
                        "/PID",
                        str(pid),
                        "/T",
                        "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await killer.wait()
                else:
                    process.kill()
                with contextlib.suppress(Exception):
                    await process.wait()

        cleanup_errors = await self._drain_background_tasks(timeout=5.0)
        last_error = ""
        if cleanup_errors:
            summarized = "; ".join(cleanup_errors[:3])
            if len(cleanup_errors) > 3:
                summarized = f"{summarized}; (+{len(cleanup_errors) - 3} more)"
            last_error = f"background_task_cleanup: {summarized}"
        self._publish_stopped_state(last_exit_code=process.returncode, last_error=last_error)
        self._process = None

    async def _drain_background_tasks(self, *, timeout: float) -> list[str]:
        tasks = [
            task
            for task in (self._stdout_task, self._stderr_task, self._watch_task)
            if task is not None
        ]
        if not tasks:
            return []

        for task in tasks:
            if not task.done():
                task.cancel()

        errors: list[str] = []
        done, pending = await asyncio.wait(tasks, timeout=max(float(timeout or 0.0), 0.1))
        if pending:
            for task in pending:
                task.cancel()
            done_after_cancel, still_pending = await asyncio.wait(pending, timeout=1.0)
            done = done.union(done_after_cancel)
            pending = still_pending

        for task in done:
            if task.cancelled():
                continue
            with contextlib.suppress(asyncio.CancelledError):
                exc = task.exception()
                if exc is not None:
                    errors.append(str(exc))

        if pending:
            errors.append(f"{len(pending)} background task(s) did not terminate")

        self._stdout_task = None
        self._stderr_task = None
        self._watch_task = None
        return errors

    def _publish_stopped_state(self, *, last_exit_code: int | None, last_error: str = "") -> None:
        info_path = Path(self.desktop_info_path)
        _write_desktop_backend_info(
            info_path,
            _build_desktop_backend_payload(
                state="stopped",
                ready=False,
                source=BOOTSTRAP_CONTEXT_SOURCE,
                last_exit_code=last_exit_code,
                last_error=last_error,
            ),
        )

    async def _watch_process(self) -> None:
        process = self._process
        if process is None:
            return
        return_code = await process.wait()
        if self._process is process:
            state = "stopped" if return_code == 0 else "errored"
            _write_desktop_backend_info(
                Path(self.desktop_info_path),
                _build_desktop_backend_payload(
                    state=state,
                    ready=False,
                    source=BOOTSTRAP_CONTEXT_SOURCE,
                    last_exit_code=return_code,
                    last_error=self._startup_error if return_code else "",
                ),
            )


async def _collect_stream(
    stream: asyncio.StreamReader | None,
    sink: deque[str],
    *,
    on_event,
) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.readline()
        if not chunk:
            break
        decoded = chunk.decode("utf-8", errors="replace").rstrip("\r\n")
        sink.append(decoded)
        event = _parse_backend_event(decoded)
        if event:
            on_event(event)


async def _wait_for_backend_ready(
    *,
    process: asyncio.subprocess.Process,
    startup_port_getter,
    token: str,
    timeout_seconds: float,
) -> tuple[str, int]:
    deadline = time.monotonic() + max(float(timeout_seconds or 0.0), 1.0)
    while time.monotonic() < deadline:
        if process.returncode is not None:
            break
        port = startup_port_getter()
        if isinstance(port, int) and port > 0:
            base_url = f"http://127.0.0.1:{port}"
            async with BackendPreflightProbe(
                backend_url=base_url,
                token=token,
                timeout=2.0,
            ) as probe:
                report = await probe.run()
            if report.status == BackendPreflightStatus.HEALTHY:
                return base_url, port
        await asyncio.sleep(0.25)
    raise TimeoutError("backend did not become healthy before startup timeout")


async def _auto_bootstrap_backend(
    *,
    startup_workspace: Path,
    ramdisk_root: Path,
    timeout_seconds: float,
    desktop_info_path: Path,
) -> ManagedBackendSession:
    workspace = ensure_stress_workspace_path(startup_workspace)
    runtime_root = ensure_stress_runtime_root(ramdisk_root)
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)

    token = secrets.token_hex(16)
    python_executable = _resolve_python_executable()
    command = [
        python_executable,
        "-B",
        str(BACKEND_SERVER_SCRIPT),
        "--host",
        "127.0.0.1",
        "--port",
        "0",
        "--token",
        token,
        "--workspace",
        str(workspace),
        "--ramdisk-root",
        str(runtime_root),
        "--log-level",
        "warning",
    ]
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # Stress runner currently targets the stable Director adapter flow.
    # Keep Sequential opt-in for this bootstrap path unless caller overrides.
    env.setdefault("POLARIS_SEQ_ENABLED", "0")
    env.setdefault("POLARIS_SEQ_USE_HYBRID", "0")

    _write_desktop_backend_info(
        desktop_info_path,
        _build_desktop_backend_payload(
            state="starting",
            ready=False,
            source=BOOTSTRAP_CONTEXT_SOURCE,
        ),
    )

    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(BACKEND_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    session = ManagedBackendSession(
        context=BackendContext(
            backend_url="",
            token=token,
            source=BOOTSTRAP_CONTEXT_SOURCE,
            desktop_info_path=str(desktop_info_path),
        ),
        auto_bootstrapped=True,
        startup_workspace=str(workspace),
        ramdisk_root=str(runtime_root),
        desktop_info_path=str(desktop_info_path),
        _process=process,
        _recent_stdout=deque(maxlen=80),
        _recent_stderr=deque(maxlen=80),
    )

    def _handle_event(event: dict[str, Any]) -> None:
        if event.get("event") == "backend_started":
            try:
                session._startup_port = int(event.get("port") or 0)
            except (TypeError, ValueError):
                session._startup_port = None
        elif event.get("event") == "backend_failed":
            session._startup_error = str(event.get("error") or "backend_failed")

    session._stdout_task = asyncio.create_task(
        _collect_stream(process.stdout, session._recent_stdout, on_event=_handle_event)
    )
    session._stderr_task = asyncio.create_task(
        _collect_stream(process.stderr, session._recent_stderr, on_event=_handle_event)
    )

    try:
        backend_url, port = await _wait_for_backend_ready(
            process=process,
            startup_port_getter=lambda: session._startup_port,
            token=token,
            timeout_seconds=timeout_seconds,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        session._startup_error = str(exc)
        error_details = {
            "workspace": str(workspace),
            "ramdisk_root": str(runtime_root),
            "desktop_info_path": str(desktop_info_path),
            "stdout_tail": list(session._recent_stdout),
            "stderr_tail": list(session._recent_stderr),
        }
        await session.aclose()
        raise BackendBootstrapError(
            f"failed to auto-bootstrap Polaris backend: {exc}",
            details=error_details,
        ) from exc

    session.context = BackendContext(
        backend_url=backend_url,
        token=token,
        source=BOOTSTRAP_CONTEXT_SOURCE,
        desktop_info_path=str(desktop_info_path),
    )
    session._startup_port = port
    _write_desktop_backend_info(
        desktop_info_path,
        _build_desktop_backend_payload(
            state="running",
            ready=True,
            source=BOOTSTRAP_CONTEXT_SOURCE,
            backend_url=backend_url,
            token=token,
            port=port,
            pid=process.pid,
        ),
    )
    session._watch_task = asyncio.create_task(session._watch_process())
    return session


async def ensure_backend_session(
    *,
    backend_url: str = "",
    token: str = "",
    auto_bootstrap: bool = True,
    startup_workspace: Path | None = None,
    ramdisk_root: Path | None = None,
    startup_timeout_seconds: float = DEFAULT_BOOTSTRAP_TIMEOUT_SECONDS,
) -> ManagedBackendSession:
    explicit_cli_values = bool(str(backend_url or "").strip() or str(token or "").strip())
    context = resolve_backend_context(backend_url=backend_url, token=token)
    session = ManagedBackendSession(
        context=context,
        auto_bootstrapped=False,
        desktop_info_path=context.desktop_info_path,
    )
    if explicit_cli_values:
        return session

    allow_desktop_context = str(
        os.environ.get("POLARIS_STRESS_ALLOW_DESKTOP_CONTEXT") or ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    if (
        auto_bootstrap
        and context.source == "desktop-backend-info"
        and not allow_desktop_context
    ):
        return await _auto_bootstrap_backend(
            startup_workspace=_resolve_bootstrap_workspace(startup_workspace),
            ramdisk_root=_resolve_bootstrap_ramdisk_root(ramdisk_root),
            timeout_seconds=startup_timeout_seconds,
            desktop_info_path=get_desktop_backend_info_path(),
        )

    if context.backend_url:
        status = await _probe_preflight_status(context)
        if status == BackendPreflightStatus.HEALTHY:
            return session
        if status == BackendPreflightStatus.SETTINGS_UNAVAILABLE:
            return session
        if context.source not in {"desktop-backend-info", "unresolved"}:
            return session
        if status not in {
            BackendPreflightStatus.BACKEND_CONTEXT_MISSING,
            BackendPreflightStatus.BACKEND_UNAVAILABLE,
            BackendPreflightStatus.AUTH_INVALID,
        }:
            return session

    if not auto_bootstrap:
        return session

    return await _auto_bootstrap_backend(
        startup_workspace=_resolve_bootstrap_workspace(startup_workspace),
        ramdisk_root=_resolve_bootstrap_ramdisk_root(ramdisk_root),
        timeout_seconds=startup_timeout_seconds,
        desktop_info_path=get_desktop_backend_info_path(),
    )
