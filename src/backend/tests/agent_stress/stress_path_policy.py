"""Stress-run path policy helpers.

These helpers are used by the stress harness to keep workspace and runtime
paths inside explicit, deterministic sandboxes.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polaris.kernelone.storage import resolve_storage_roots

_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
_DEFAULT_STRESS_ROOT_DIR = ".tmp_agent_stress"
_WORKSPACES_DIR = "workspaces"
_RUNTIME_DIR = "runtime"
_WINDOWS_WORKSPACE_ROOT = Path("C:/Temp")
_WINDOWS_RUNTIME_ROOT = Path("X:/")


def _sanitize_segment(value: str, *, fallback: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    sanitized = _SAFE_SEGMENT_RE.sub("-", raw).strip(".-")
    return sanitized or fallback


def _base_root_from_env(
    env_name: str,
    default_leaf: str,
    *,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    cwd: str | Path | None = None,
    windows_default_root: str | Path | None = None,
) -> Path:
    active_env = os.environ if env is None else env
    configured = str(active_env.get(env_name) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    normalized_platform = str(platform_name or os.name).strip().lower()
    if normalized_platform in {"nt", "win32"} and windows_default_root is not None:
        return Path(windows_default_root).expanduser().resolve()
    current_dir = Path(cwd).expanduser().resolve() if cwd is not None else Path.cwd().resolve()
    return (current_dir / _DEFAULT_STRESS_ROOT_DIR / default_leaf).resolve()


def default_stress_workspace_base(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    cwd: str | Path | None = None,
) -> Path:
    """Return the default stress workspace path for a named scenario.

    Official Windows runs must keep workspaces under `C:/Temp/`.
    """
    base_root = _base_root_from_env(
        "KERNELONE_STRESS_WORKSPACE_ROOT",
        _WORKSPACES_DIR,
        env=env,
        platform_name=platform_name,
        cwd=cwd,
        windows_default_root=_WINDOWS_WORKSPACE_ROOT,
    )
    return (base_root / _sanitize_segment(name, fallback="stress-workspace")).resolve()


def default_stress_runtime_root(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    cwd: str | Path | None = None,
) -> Path:
    """Return the default stress runtime root for a named scenario.

    Official Windows runs must keep runtime/cache roots on `X:/`.
    """
    base_root = _base_root_from_env(
        "KERNELONE_STRESS_RUNTIME_ROOT_BASE",
        _RUNTIME_DIR,
        env=env,
        platform_name=platform_name,
        cwd=cwd,
        windows_default_root=_WINDOWS_RUNTIME_ROOT,
    )
    return (base_root / _sanitize_segment(name, fallback="stress-runtime")).resolve()


def ensure_stress_workspace_path(path: str | Path) -> Path:
    """Resolve and create a stress workspace path."""
    candidate = Path(path).expanduser().resolve()
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def ensure_stress_runtime_root(path: str | Path) -> Path:
    """Resolve and create a stress runtime root path."""
    candidate = Path(path).expanduser().resolve()
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def runtime_layout_policy_violations(layout: Mapping[str, Any]) -> list[str]:
    """Return policy violations for a runtime/storage-layout payload."""
    if not isinstance(layout, Mapping):
        return ["layout_payload_not_mapping"]

    runtime_root_raw = str(layout.get("runtime_root") or "").strip()
    if not runtime_root_raw:
        return ["runtime_root_missing"]

    runtime_root = Path(runtime_root_raw).expanduser()
    if not runtime_root.is_absolute():
        return [f"runtime_root_not_absolute:{runtime_root_raw}"]

    workspace_raw = str(
        layout.get("workspace_abs")
        or layout.get("workspace")
        or ""
    ).strip()
    if not workspace_raw:
        return []

    ramdisk_root_raw = str(layout.get("ramdisk_root") or "").strip() or None
    try:
        expected = Path(
            resolve_storage_roots(workspace_raw, ramdisk_root_raw).runtime_root
        ).resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return [f"storage_roots_resolution_failed:{exc}"]

    actual = runtime_root.resolve()
    if actual != expected:
        return [f"runtime_root_mismatch:expected={expected},actual={actual}"]
    return []


__all__ = [
    "default_stress_runtime_root",
    "default_stress_workspace_base",
    "ensure_stress_runtime_root",
    "ensure_stress_workspace_path",
    "runtime_layout_policy_violations",
]
