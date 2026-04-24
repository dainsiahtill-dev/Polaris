"""Ollama CLI provider operations.

This module provides low-level Ollama CLI interaction.
No HTTP semantics — callers are responsible for mapping domain exceptions
to HTTP responses at the delivery boundary.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from polaris.domain.exceptions import ExternalServiceError, ServiceUnavailableError
from polaris.kernelone.errors import TimeoutError as DomainTimeoutError
from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest


def _project_root(project_root: str | None = None) -> str:
    if project_root:
        return project_root
    # Deferred import to avoid config下沉 in this module.
    # Callers should pass project_root explicitly.
    from polaris.bootstrap.config import get_settings

    return str(get_settings().project_root)


def list_ollama_models(project_root: str | None = None) -> list[str]:
    """List available Ollama models via `ollama ps`.

    Raises:
        ServiceUnavailableError: Ollama binary not found in PATH.
        ExternalServiceError: `ollama ps` command failed.
        DomainTimeoutError: `ollama ps` command timed out.
    """
    if not shutil.which("ollama"):
        raise ServiceUnavailableError(
            service="ollama",
            message="ollama command not found in PATH.",
        )

    timeout_sec = 0
    try:
        timeout_sec = int(str(os.environ.get("KERNELONE_OLLAMA_CLI_TIMEOUT", "15")).strip())
    except (ValueError, TypeError):
        timeout_sec = 15
    timeout_val = timeout_sec if timeout_sec and timeout_sec > 0 else 15

    root = _project_root(project_root)
    try:
        cmd_svc = CommandExecutionService(
            root,
            allowed_executables={"ollama", "ollama.exe"},
        )
        request = CommandRequest(
            executable="ollama",
            args=["ps"],
            cwd=root,
            timeout_seconds=timeout_val,
        )
        result = cmd_svc.run(request)
    except (RuntimeError, ValueError) as exc:
        raise ExternalServiceError(
            service="ollama",
            message=f"ollama ps failed: {exc}",
        ) from exc
    if result.get("timed_out", False):
        raise DomainTimeoutError(
            message="ollama ps timed out",
            timeout_seconds=timeout_val,
        )
    if not result.get("ok", False):
        msg = (result.get("stderr", "") or result.get("stdout", "") or "ollama ps failed").strip()
        raise ExternalServiceError(service="ollama", message=msg)

    lines = result.get("stdout", "").splitlines()
    models: list[str] = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        name = line.split()[0].strip()
        if name and name.lower() != "name":
            models.append(name)
    return models


def ollama_stop(project_root: str | None = None) -> dict[str, Any]:
    """Stop all running Ollama models via `ollama stop`."""
    try:
        models = list_ollama_models(project_root)
    except (ServiceUnavailableError, ExternalServiceError, DomainTimeoutError):
        return {"ok": True, "stopped": [], "failed": [], "models": []}

    if not models:
        return {"ok": True, "stopped": [], "failed": [], "models": []}

    timeout_sec = 0
    try:
        timeout_sec = int(str(os.environ.get("KERNELONE_OLLAMA_CLI_TIMEOUT", "15")).strip())
    except (ValueError, TypeError):
        timeout_sec = 15
    timeout_val = timeout_sec if timeout_sec and timeout_sec > 0 else 15

    stopped: list[str] = []
    failed: list[dict[str, str]] = []
    root = _project_root(project_root)
    cmd_svc = CommandExecutionService(
        root,
        allowed_executables={"ollama", "ollama.exe"},
    )
    for name in models:
        try:
            request = CommandRequest(
                executable="ollama",
                args=["stop", name],
                cwd=root,
                timeout_seconds=timeout_val,
            )
            result = cmd_svc.run(request)
        except (RuntimeError, ValueError) as exc:
            failed.append({"model": name, "error": str(exc)})
            continue
        if result.get("timed_out", False):
            failed.append({"model": name, "error": "timeout"})
            continue
        if result.get("ok", False):
            stopped.append(name)
        else:
            msg = (result.get("stderr", "") or result.get("stdout", "") or "ollama stop failed").strip()
            failed.append({"model": name, "error": msg})
    return {"ok": True, "stopped": stopped, "failed": failed, "models": models}
