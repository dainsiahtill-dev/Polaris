"""Orchestration module for PM workflow.

This module provides the orchestration logic for the PM (尚书令) workflow,
including document generation, blueprint analysis, and core execution control.

Module Structure:
- architect_stage.py: Architect docs stage execution
- blueprint_analysis.py: Technology stack detection and task enhancement
- blueprint_pipeline.py: Blueprint pipeline utilities
- core.py: Core orchestration logic (load_state_and_context, check_spin_guard, etc.)
- directive_processing.py: Directive text processing utilities
- doc_rendering.py: Document rendering and quality validation
- docs_pipeline.py: Document generation pipeline utilities
- helpers.py: Helper utilities for environment and I/O
- module_evolution.py: Shangshuling PM integration functions

Public API:
- ensure_docs_ready: Ensure docs directory exists
- archive_task_history: Archive task history to runtime
- load_state_and_context: Load state and context for iteration
- load_cli_directive: Load CLI directive from args
- run_architect_docs_stage: Run architect docs generation stage
- check_spin_guard: Check if spin guard is active
- check_stop_conditions: Check stop conditions
- update_consecutive_counters: Update consecutive counters
- detect_tech_stack: Detect technology stack
- build_enhanced_task_with_tech_stack: Enhance task with tech stack
- ensure_shangshuling_pm_initialized: Initialize Shangshuling PM
- sync_tasks_to_shangshuling: Sync tasks to Shangshuling PM
- get_shangshuling_ready_tasks: Get ready tasks from Shangshuling PM
- record_shangshuling_task_completion: Record task completion
"""

from .architect_stage import (
    ensure_docs_ready,
    run_architect_docs_stage,
)
from .blueprint_analysis import (
    build_enhanced_task_with_tech_stack,
    detect_tech_stack,
)
from .core import (
    archive_task_history,
    check_spin_guard,
    check_stop_conditions,
    load_state_and_context,
    update_consecutive_counters,
)
from .helpers import (
    _load_cli_directive as load_cli_directive,
)
from .module_evolution import (
    ensure_shangshuling_pm_initialized,
    get_shangshuling_ready_tasks,
    record_shangshuling_task_completion,
    sync_tasks_to_shangshuling,
)

__all__ = [
    "archive_task_history",
    "build_enhanced_task_with_tech_stack",
    "check_spin_guard",
    "check_stop_conditions",
    # Blueprint analysis
    "detect_tech_stack",
    # Core orchestration
    "ensure_docs_ready",
    # Shangshuling PM integration
    "ensure_shangshuling_pm_initialized",
    "get_shangshuling_ready_tasks",
    # Directive loading
    "load_cli_directive",
    "load_state_and_context",
    "record_shangshuling_task_completion",
    # Document generation
    "run_architect_docs_stage",
    "sync_tasks_to_shangshuling",
    "update_consecutive_counters",
]
