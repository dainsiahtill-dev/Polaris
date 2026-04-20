"""Polaris engine package.

This package contains the modular implementation of PolarisEngine,
split into focused modules for maintainability.

Modules:
    core: PolarisEngine class and EngineRuntimeConfig
    scheduler: SchedulerProtocol and SingleWorkerScheduler
    taskboard: Taskboard integration functions
    tri_council: Tri-Council coordination functions
    delivery_floor: Delivery floor validation functions
    completion_lock: Completion lock state management
    helpers: Pure utility functions
"""

from polaris.delivery.cli.pm.engine.core import (
    DirectorRunner,
    EngineRuntimeConfig,
    PolarisEngine,
    _build_single_task_payload,
    _collect_active_director_tasks,
    _resolve_preflight_paths,
    _select_plan_bootstrap_target,
)
from polaris.delivery.cli.pm.engine.helpers import (
    _ALLOWED_EXECUTION_MODES,
    _ALLOWED_SCHEDULING_POLICIES,
    _CODE_FILE_EXTENSIONS,
    _DIRECTOR_RESULT_STATUSES,
    _PHASE_ORDER,
    _TERMINAL_TASK_STATUSES,
)
from polaris.delivery.cli.pm.engine.scheduler import (
    SchedulerProtocol,
    SingleWorkerScheduler,
)

__all__ = [
    # Helpers
    "_ALLOWED_EXECUTION_MODES",
    "_ALLOWED_SCHEDULING_POLICIES",
    "_CODE_FILE_EXTENSIONS",
    "_DIRECTOR_RESULT_STATUSES",
    "_PHASE_ORDER",
    "_TERMINAL_TASK_STATUSES",
    "DirectorRunner",
    # Core
    "EngineRuntimeConfig",
    "PolarisEngine",
    # Scheduler
    "SchedulerProtocol",
    "SingleWorkerScheduler",
    "_build_single_task_payload",
    # Functions
    "_collect_active_director_tasks",
    "_resolve_preflight_paths",
    "_select_plan_bootstrap_target",
]
