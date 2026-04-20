"""Polaris engine for PM -> Director orchestration.

This module is a facade that re-exports from the modular engine package.
The actual implementation has been refactored into focused modules:

    - engine/core.py: PolarisEngine class and EngineRuntimeConfig
    - engine/scheduler.py: SchedulerProtocol and SingleWorkerScheduler
    - engine/taskboard.py: Taskboard integration functions
    - engine/tri_council.py: Tri-Council coordination functions
    - engine/delivery_floor.py: Delivery floor validation
    - engine/completion_lock.py: Completion lock state management
    - engine/helpers.py: Pure utility functions

This file preserves backward compatibility for all existing imports.
"""

from __future__ import annotations

# Re-export all public APIs from the engine package
from polaris.delivery.cli.pm.engine import (
    # Constants
    _ALLOWED_EXECUTION_MODES,
    _ALLOWED_SCHEDULING_POLICIES,
    _CODE_FILE_EXTENSIONS,
    _DIRECTOR_RESULT_STATUSES,
    _PHASE_ORDER,
    _TERMINAL_TASK_STATUSES,
    DirectorRunner,
    # Core classes
    EngineRuntimeConfig,
    PolarisEngine,
    # Scheduler
    SchedulerProtocol,
    SingleWorkerScheduler,
    _build_single_task_payload,
    # Functions
    _collect_active_director_tasks,
    _resolve_preflight_paths,
    _select_plan_bootstrap_target,
)
from polaris.delivery.cli.pm.engine._dispatch import (
    normalize_director_result_status,
)
from polaris.delivery.cli.pm.engine.completion_lock import (
    _apply_task_stability_filters,
    _completion_lock_state_path,
    _load_completion_lock_state,
    _save_completion_lock_state,
    _select_dependency_closed_tasks,
    _update_completion_lock_state,
)
from polaris.delivery.cli.pm.engine.delivery_floor import (
    _DELIVERY_FLOOR_DEFAULTS,
    _detect_project_scale,
    _evaluate_delivery_floor,
    _looks_like_stress_workspace,
    _resolve_delivery_floor_thresholds,
)

# Re-export from submodules for backward compatibility
from polaris.delivery.cli.pm.engine.helpers import (
    _build_batches,
    _collect_completed_task_ids,
    _count_utf8_lines,
    _dedupe_paths,
    _env_float,
    _env_non_negative_int,
    _env_positive_int,
    _estimate_code_lines_from_workspace,
    _first_existing_file,
    _is_running_status,
    _is_truthy_env,
    _join_non_empty,
    _looks_like_code_file,
    _looks_like_test_file,
    _normalize_bool,
    _normalize_failure_detail,
    _now_timestamp,
    _order_tasks,
    _phase_rank,
    _resolve_workspace_candidate_path,
    _safe_int,
    _task_dependency_ids,
    _task_identity_key,
)
from polaris.delivery.cli.pm.engine.taskboard import (
    _build_taskboard_runtime,
    _load_role_taskboard_module,
    _select_taskboard_ready_batch,
    _taskboard_mainline_enabled,
    _taskboard_priority_enum,
)
from polaris.delivery.cli.pm.engine.tri_council import (
    _COORDINATION_ESCALATION_CHAIN,
    _DEFAULT_TRI_COUNCIL_MAX_ROUNDS,
    _DEFAULT_TRI_COUNCIL_START_RETRY,
    _clamp_coordination_stage,
    _coordination_participants_for_role,
    _coordination_role_for_stage,
    _looks_complex_for_council,
    _meeting_improvement_hint,
    _persist_meeting_learning_records,
    _resolve_tri_council_policy,
    _run_tri_council_round,
    _runtime_learning_paths,
    _tri_council_action_for_failure,
)

# Constants from submodules
_DEFAULT_ROLE_CONTEXT_HISTORY_LIMIT = 24
_DEFAULT_MAX_DIRECTOR_RETRIES = 5
_DEFAULT_COORDINATION_STAGE_RETRY_BUDGET = 2
_TASKBOARD_PRIORITY_LEVELS = {
    0: "CRITICAL",
    1: "HIGH",
    2: "HIGH",
    3: "MEDIUM",
    4: "MEDIUM",
}

__all__ = [
    # Constants
    "_ALLOWED_EXECUTION_MODES",
    "_ALLOWED_SCHEDULING_POLICIES",
    "_CODE_FILE_EXTENSIONS",
    "_COORDINATION_ESCALATION_CHAIN",
    "_DEFAULT_COORDINATION_STAGE_RETRY_BUDGET",
    "_DEFAULT_MAX_DIRECTOR_RETRIES",
    "_DEFAULT_ROLE_CONTEXT_HISTORY_LIMIT",
    "_DEFAULT_TRI_COUNCIL_MAX_ROUNDS",
    "_DEFAULT_TRI_COUNCIL_START_RETRY",
    "_DELIVERY_FLOOR_DEFAULTS",
    "_DIRECTOR_RESULT_STATUSES",
    "_PHASE_ORDER",
    "_TASKBOARD_PRIORITY_LEVELS",
    "_TERMINAL_TASK_STATUSES",
    "DirectorRunner",
    # Core classes
    "EngineRuntimeConfig",
    "PolarisEngine",
    # Scheduler
    "SchedulerProtocol",
    "SingleWorkerScheduler",
    "_apply_task_stability_filters",
    "_build_batches",
    "_build_single_task_payload",
    "_build_taskboard_runtime",
    "_clamp_coordination_stage",
    # Core functions
    "_collect_active_director_tasks",
    "_collect_completed_task_ids",
    # Completion lock functions
    "_completion_lock_state_path",
    "_coordination_participants_for_role",
    "_coordination_role_for_stage",
    "_count_utf8_lines",
    "_dedupe_paths",
    "_detect_project_scale",
    "_env_float",
    "_env_non_negative_int",
    "_env_positive_int",
    "_estimate_code_lines_from_workspace",
    "_evaluate_delivery_floor",
    "_first_existing_file",
    "_is_running_status",
    "_is_truthy_env",
    "_join_non_empty",
    "_load_completion_lock_state",
    "_load_role_taskboard_module",
    "_looks_complex_for_council",
    "_looks_like_code_file",
    # Delivery floor functions
    "_looks_like_stress_workspace",
    "_looks_like_test_file",
    "_meeting_improvement_hint",
    "_normalize_bool",
    "_normalize_failure_detail",
    # Helper functions
    "_now_timestamp",
    "_order_tasks",
    "_persist_meeting_learning_records",
    "_phase_rank",
    "_resolve_delivery_floor_thresholds",
    "_resolve_preflight_paths",
    # Tri-council functions
    "_resolve_tri_council_policy",
    "_resolve_workspace_candidate_path",
    "_run_tri_council_round",
    "_runtime_learning_paths",
    "_safe_int",
    "_save_completion_lock_state",
    "_select_dependency_closed_tasks",
    "_select_plan_bootstrap_target",
    "_select_taskboard_ready_batch",
    "_task_dependency_ids",
    "_task_identity_key",
    # Taskboard functions
    "_taskboard_mainline_enabled",
    "_taskboard_priority_enum",
    "_tri_council_action_for_failure",
    "_update_completion_lock_state",
    # Other
    "normalize_director_result_status",
]
