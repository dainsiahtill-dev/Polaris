"""Projection-local compatibility facade for runtime artifact payload builders.

This module keeps the historical import path stable:
``polaris.cells.runtime.projection.internal.artifacts``.
The canonical implementation lives in
``polaris.cells.runtime.artifact_store.internal.artifacts``.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.runtime.artifact_store.public.service import (
    build_memory_payload as _build_memory_payload,
    build_success_stats_payload as _build_success_stats_payload,
)


def build_memory_payload(workspace: str, cache_root: str) -> dict[str, Any] | None:
    """Build memory payload from canonical artifact store implementation."""
    return _build_memory_payload(workspace, cache_root)


def build_success_stats_payload(workspace: str, cache_root: str) -> dict[str, Any]:
    """Build success statistics payload from canonical artifact store implementation."""
    return _build_success_stats_payload(workspace, cache_root)


__all__ = ["build_memory_payload", "build_success_stats_payload"]
