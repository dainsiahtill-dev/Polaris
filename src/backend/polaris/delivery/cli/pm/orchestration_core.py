"""Orchestration helpers for loop-pm.

This module is now a facade that redirects to the refactored orchestration package.
All functionality has been moved to polaris.delivery.cli.pm.orchestration submodules.

See orchestration/__init__.py for the full public API.
"""

# Facade imports - redirect all public symbols to the new package
from polaris.delivery.cli.pm.orchestration import (
    archive_task_history,
    build_enhanced_task_with_tech_stack,
    check_spin_guard,
    check_stop_conditions,
    detect_tech_stack,
    ensure_docs_ready,
    ensure_shangshuling_pm_initialized,
    get_shangshuling_ready_tasks,
    load_cli_directive,
    load_state_and_context,
    record_shangshuling_task_completion,
    run_architect_docs_stage,
    sync_tasks_to_shangshuling,
    update_consecutive_counters,
)

__all__ = [
    "archive_task_history",
    "build_enhanced_task_with_tech_stack",
    "check_spin_guard",
    "check_stop_conditions",
    "detect_tech_stack",
    "ensure_docs_ready",
    # Shangshuling PM integration
    "ensure_shangshuling_pm_initialized",
    "get_shangshuling_ready_tasks",
    "load_cli_directive",
    "load_state_and_context",
    "record_shangshuling_task_completion",
    "run_architect_docs_stage",
    "sync_tasks_to_shangshuling",
    "update_consecutive_counters",
]
