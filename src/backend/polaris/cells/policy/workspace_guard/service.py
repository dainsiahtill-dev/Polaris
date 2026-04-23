from __future__ import annotations

import os
from pathlib import Path
from typing import Any

SELF_UPGRADE_MODE_ENV = "KERNELONE_SELF_UPGRADE_MODE"


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_meta_project_root() -> Path:
    # Adjusted depth because it's now in polaris/cells/policy/workspace_guard/role_agent_service.py
    # Old: polaris/application/policy/workspace_policy.py (depth 3 to backend root)
    # New: polaris/cells/policy/workspace_guard/role_agent_service.py (depth 4 to backend root)
    return Path(__file__).resolve().parents[4]


def self_upgrade_mode_enabled(value: Any | None = None) -> bool:
    if value is not None:
        return _coerce_bool(value)
    return _coerce_bool(os.environ.get(SELF_UPGRADE_MODE_ENV))


def resolve_workspace_target(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def is_meta_project_target(path: str | Path) -> bool:
    resolved = resolve_workspace_target(path)
    project_root = get_meta_project_root().resolve()
    return resolved == project_root or project_root in resolved.parents


def build_workspace_guard_message(path: str | Path) -> str:
    resolved = resolve_workspace_target(path)
    project_root = get_meta_project_root().resolve()
    return (
        "target workspace "
        f"'{resolved}' is inside the Polaris meta-project root '{project_root}'. "
        "Enable self_upgrade_mode=true or set "
        f"{SELF_UPGRADE_MODE_ENV}=1 only for intentional Polaris self-upgrade runs."
    )


def ensure_workspace_target_allowed(
    path: str | Path,
    *,
    self_upgrade_mode: Any | None = None,
) -> Path:
    resolved = resolve_workspace_target(path)
    if is_meta_project_target(resolved) and not self_upgrade_mode_enabled(self_upgrade_mode):
        raise ValueError(build_workspace_guard_message(resolved))
    return resolved
