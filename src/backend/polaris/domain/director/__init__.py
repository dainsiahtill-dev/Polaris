"""Domain director module.

This module contains Director-specific business logic and constants that were
migrated from the KernelOne layer.

Modules:
    constants: Director business constants (paths, phases)
    lifecycle: Director lifecycle management

迁移历史:
    - 2026-03-27: 从 polaris.kernelone.runtime 迁移
"""

from __future__ import annotations

from polaris.domain.director.constants import (
    AGENTS_DRAFT_REL,
    AGENTS_FEEDBACK_REL,
    CHANNEL_FILES,
    DEFAULT_DIALOGUE,
    DEFAULT_DIRECTOR_LIFECYCLE,
    DEFAULT_DIRECTOR_LLM_EVENTS,
    DEFAULT_DIRECTOR_STATUS,
    DEFAULT_DIRECTOR_SUBPROCESS_LOG,
    DEFAULT_ENGINE_STATUS,
    DEFAULT_GAP,
    DEFAULT_OLLAMA,
    DEFAULT_PLAN,
    DEFAULT_PLANNER,
    DEFAULT_PM_LLM_EVENTS,
    DEFAULT_PM_LOG,
    DEFAULT_PM_OUT,
    DEFAULT_PM_REPORT,
    DEFAULT_PM_SUBPROCESS_LOG,
    DEFAULT_QA,
    DEFAULT_REQUIREMENTS,
    DEFAULT_RUNLOG,
    DEFAULT_RUNTIME_EVENTS,
    DIRECTOR_CONTRACTS_DIR,
    DIRECTOR_EVENTS_DIR,
    DIRECTOR_LOGS_DIR,
    DIRECTOR_OUTPUT_DIR,
    DIRECTOR_RESULTS_DIR,
    DIRECTOR_RUNTIME_DIR,
    DIRECTOR_STATUS_DIR,
    NEW_CHANNEL_METADATA,
    WORKSPACE_STATUS_REL,
    DirectorPhase,
)
from polaris.domain.director.lifecycle import (
    DirectorLifecycleManager,
    LifecycleEvent,
    LifecycleState,
    read,
    update,
)

__all__ = [
    # Constants
    "AGENTS_DRAFT_REL",
    "AGENTS_FEEDBACK_REL",
    "CHANNEL_FILES",
    "DEFAULT_DIALOGUE",
    "DEFAULT_DIRECTOR_LIFECYCLE",
    "DEFAULT_DIRECTOR_LLM_EVENTS",
    "DEFAULT_DIRECTOR_STATUS",
    "DEFAULT_DIRECTOR_SUBPROCESS_LOG",
    "DEFAULT_ENGINE_STATUS",
    "DEFAULT_GAP",
    "DEFAULT_OLLAMA",
    "DEFAULT_PLAN",
    "DEFAULT_PLANNER",
    "DEFAULT_PM_LLM_EVENTS",
    "DEFAULT_PM_LOG",
    "DEFAULT_PM_OUT",
    "DEFAULT_PM_REPORT",
    "DEFAULT_PM_SUBPROCESS_LOG",
    "DEFAULT_QA",
    "DEFAULT_REQUIREMENTS",
    "DEFAULT_RUNLOG",
    "DEFAULT_RUNTIME_EVENTS",
    "DIRECTOR_CONTRACTS_DIR",
    "DIRECTOR_EVENTS_DIR",
    "DIRECTOR_LOGS_DIR",
    "DIRECTOR_OUTPUT_DIR",
    "DIRECTOR_RESULTS_DIR",
    "DIRECTOR_RUNTIME_DIR",
    "DIRECTOR_STATUS_DIR",
    "NEW_CHANNEL_METADATA",
    "WORKSPACE_STATUS_REL",
    # Lifecycle
    "DirectorLifecycleManager",
    "DirectorPhase",
    "LifecycleEvent",
    "LifecycleState",
    "read",
    "update",
]
