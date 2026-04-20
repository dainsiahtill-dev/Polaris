from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from polaris.cells.storage.layout.public.service import load_persisted_settings
from polaris.kernelone.storage import resolve_storage_roots


@dataclass(frozen=True)
class WorkspaceRuntimeContext:
    workspace: str
    workspace_key: str
    runtime_root: str
    runtime_base: str
    source: str
    configured_workspace: str
    persisted_workspace: str
    fallback_workspace: str


def _normalize_existing_dir(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(candidate):
        return ""
    return candidate


def resolve_workspace_runtime_context(
    *,
    configured_workspace: Any,
    default_workspace: Any,
    ramdisk_root: str = "",
) -> WorkspaceRuntimeContext:
    configured = _normalize_existing_dir(configured_workspace)
    persisted = ""
    try:
        persisted_payload = load_persisted_settings(configured)
        if isinstance(persisted_payload, dict):
            persisted = _normalize_existing_dir(persisted_payload.get("workspace"))
    except (RuntimeError, ValueError):
        persisted = ""

    fallback = _normalize_existing_dir(default_workspace)
    if not fallback:
        fallback = os.path.abspath(os.path.expanduser(str(default_workspace or os.getcwd())))

    selected = configured
    source = "settings"
    if not selected:
        if persisted:
            selected = persisted
            source = "persisted"
        else:
            selected = fallback
            source = "default"

    roots = resolve_storage_roots(
        selected,
        ramdisk_root=ramdisk_root or None,
    )
    return WorkspaceRuntimeContext(
        workspace=str(roots.workspace_abs),
        workspace_key=str(roots.workspace_key),
        runtime_root=str(roots.runtime_root),
        runtime_base=str(roots.runtime_base),
        source=source,
        configured_workspace=configured,
        persisted_workspace=persisted,
        fallback_workspace=fallback,
    )
