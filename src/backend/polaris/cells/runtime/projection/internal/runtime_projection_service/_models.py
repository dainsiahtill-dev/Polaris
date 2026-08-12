"""Core models for runtime projection service package."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("polaris.cells.runtime.projection.internal.runtime_projection_service")


class TaskSource(Enum):
    """Task list source selection."""

    TASK_RUNTIME = "runtime.task_runtime"  # TaskRuntimeService row projection
    LOCAL_LIVE = "local_live"  # Legacy local live rows when task-runtime projection is unavailable
    NONE = "none"  # No tasks available


@dataclass
class RuntimeProjection:
    """Unified runtime projection container."""

    # Core state sources
    pm_local: dict[str, Any] = field(default_factory=dict)
    director_local: dict[str, Any] = field(default_factory=dict)
    director_merged: dict[str, Any] = field(default_factory=dict)
    workflow_archive: dict[str, Any] | None = None
    engine_fallback: dict[str, Any] | None = None

    # Derived states
    court_state: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] | None = None
    success_stats: dict[str, Any] = field(default_factory=dict)
    anthro_state: dict[str, Any] | None = None
    lancedb: dict[str, Any] = field(default_factory=dict)
    resident: dict[str, Any] | None = None
    task_source: TaskSource = TaskSource.NONE
    task_rows: list[dict[str, Any]] = field(default_factory=list)
