"""KernelOne logical storage path helpers for roles.session.

This module enforces a strict "no direct path" policy:
1) Prefer canonical `runtime/*` logical paths.
2) If runtime roots are unavailable in the current execution mode,
   transparently fall back to `workspace/runtime/*` logical paths.

Both branches remain inside KernelOne storage policy boundaries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.db import KernelDatabase
    from polaris.kernelone.fs import KernelFileSystem

logger = logging.getLogger(__name__)


def resolve_preferred_logical_prefix(
    kernel_fs: KernelFileSystem,
    *,
    runtime_prefix: str,
    workspace_fallback_prefix: str,
) -> str:
    """Resolve a writable logical prefix without direct filesystem paths."""
    try:
        kernel_fs.resolve_path(runtime_prefix)
        return runtime_prefix
    except (RuntimeError, ValueError) as runtime_exc:
        logger.warning(
            "roles.session runtime prefix unavailable (%s), fallback to %s",
            runtime_exc,
            workspace_fallback_prefix,
        )
        kernel_fs.resolve_path(workspace_fallback_prefix)
        return workspace_fallback_prefix


def resolve_preferred_sqlite_path(
    kernel_db: KernelDatabase,
    *,
    runtime_logical_path: str,
    workspace_fallback_logical_path: str,
) -> str:
    """Resolve SQLite path through KernelOne DB policy with runtime->workspace fallback."""
    try:
        return kernel_db.resolve_sqlite_path(runtime_logical_path, ensure_parent=True)
    except (RuntimeError, ValueError) as runtime_exc:
        logger.warning(
            "roles.session runtime sqlite path unavailable (%s), fallback to %s",
            runtime_exc,
            workspace_fallback_logical_path,
        )
        return kernel_db.resolve_sqlite_path(
            workspace_fallback_logical_path,
            ensure_parent=True,
        )
