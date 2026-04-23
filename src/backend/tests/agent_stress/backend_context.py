"""解析 tests.agent_stress 使用的 backend 上下文。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _expand_path(raw_path: str) -> str:
    value = str(raw_path or "").strip()
    if not value:
        return ""
    return str(Path(value).expanduser().resolve())


def resolve_polaris_root(
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> str:
    active_env = env or os.environ
    active_platform = platform or os.name

    root_override = str(active_env.get("KERNELONE_ROOT") or "").strip()
    if root_override:
        return _expand_path(root_override)

    home_override = str(active_env.get("KERNELONE_HOME") or "").strip()
    if home_override:
        expanded = _expand_path(home_override)
        trimmed = expanded.rstrip("\\/")
        if Path(trimmed).name.lower() == ".polaris":
            return str(Path(trimmed).parent)
        return expanded

    if active_platform in {"nt", "win32"}:
        app_data = str(active_env.get("APPDATA") or "").strip()
        if app_data:
            return _expand_path(app_data)

    xdg = str(active_env.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return _expand_path(xdg)

    return _expand_path(str(Path.home()))


def resolve_polaris_home(
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> str:
    return str(Path(resolve_polaris_root(env, platform)) / ".polaris")


def get_desktop_backend_info_path(
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    return Path(resolve_polaris_home(env, platform)) / "runtime" / "desktop-backend.json"


def _read_json_utf8(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@dataclass(frozen=True)
class BackendContext:
    backend_url: str
    token: str
    source: str
    desktop_info_path: str = ""


def resolve_backend_context(
    *,
    backend_url: str = "",
    token: str = "",
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> BackendContext:
    active_env = env or os.environ
    explicit_url = str(backend_url or "").strip()
    explicit_token = str(token or "").strip()
    if explicit_url or explicit_token:
        return BackendContext(
            backend_url=explicit_url,
            token=explicit_token,
            source="explicit",
        )

    env_url = str(active_env.get("KERNELONE_BASE_URL") or "").strip()
    env_token = str(active_env.get("KERNELONE_TOKEN") or "").strip()
    if env_url or env_token:
        return BackendContext(
            backend_url=env_url,
            token=env_token,
            source="env",
        )

    info_path = get_desktop_backend_info_path(active_env, platform)
    payload = _read_json_utf8(info_path)
    backend = payload.get("backend")
    backend_dict = backend if isinstance(backend, dict) else {}
    desktop_url = str(backend_dict.get("baseUrl") or "").strip()
    desktop_token = str(backend_dict.get("token") or "").strip()
    if desktop_url or desktop_token:
        return BackendContext(
            backend_url=desktop_url,
            token=desktop_token,
            source="desktop-backend-info",
            desktop_info_path=str(info_path),
        )

    return BackendContext(
        backend_url="",
        token="",
        source="unresolved",
        desktop_info_path=str(info_path),
    )
