"""PM orchestration engine - Facade layer.

This module serves as the facade layer for PM orchestration.
All actual implementations have been migrated to app.orchestration.* modules.

Delegates to:
- app.orchestration.planning_pipeline: PM planning iteration with quality retry
- app.orchestration.iteration_state: Iteration finalization and state management
- app.orchestration.dispatch_pipeline: Task dispatch to Director
- app.orchestration.chief_engineer_preflight: Chief Engineer preflight checks
- app.orchestration.pm_contract_store: Contract persistence
- pm.orchestration_core: Core orchestration utilities (spin guard, stop conditions, etc.)
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any

# ---------------------------------------------------------------------------
# Module re-export / monkeypatch surface.
#
# After the god-FUNCTION decomposition, the heavy bodies of ``run_once`` and
# ``_run_dispatch_pipeline_with_workflow`` live in sibling runner modules
# (``orchestration.run_once_runner`` / ``orchestration.dispatch_pipeline_runner``).
# Those runners resolve their collaborators through THIS module object at call
# time (``_oe.NAME``) so that ``monkeypatch.setattr(orchestration_engine, ...)``
# in the integration/regression tests still wins. Every name below is therefore
# part of this module's public/monkeypatch surface and is bound here as an
# explicit ``name as name`` re-export (lossless surface), even where it is no
# longer referenced directly inside this facade.
# ---------------------------------------------------------------------------
from polaris.cells.orchestration.pm_dispatch.public import (
    clear_manual_intervention as clear_manual_intervention,
    finalize_iteration as finalize_iteration,
    handle_spin_guard as handle_spin_guard,
    record_stop as record_stop,
    resolve_director_dispatch_tasks as resolve_director_dispatch_tasks,
    run_post_dispatch_integration_qa as run_post_dispatch_integration_qa,
)
from polaris.cells.orchestration.pm_planning.public.pipeline import (
    run_pm_planning_iteration as run_pm_planning_iteration,
)
from polaris.cells.orchestration.pm_planning.public.service import (
    autofix_pm_contract_for_quality as autofix_pm_contract_for_quality,
    evaluate_pm_task_quality as evaluate_pm_task_quality,
)

# Import from refactored app.orchestration modules (via public boundary)
from polaris.cells.orchestration.workflow_runtime.public import (
    SUPPORTED_ORCHESTRATION_RUNTIMES,
    resolve_orchestration_runtime as resolve_workflow_orchestration_runtime,
    submit_pm_workflow_sync as submit_pm_workflow_sync,
    wait_for_workflow_completion_sync as wait_for_workflow_completion_sync,
)
from polaris.cells.runtime.projection.public import (
    get_workflow_runtime_status as get_workflow_runtime_status,
    summarize_workflow_tasks as summarize_workflow_tasks,
)
from polaris.cells.runtime.state_owner.public import (
    ensure_engine_dispatch_contracts as ensure_engine_dispatch_contracts,
    persist_pm_payload as persist_pm_payload,
)
from polaris.delivery.cli.pm.agents import (
    wait_for_agents_confirmation as wait_for_agents_confirmation,
)

# Import from pm modules
from polaris.delivery.cli.pm.backend import (
    ensure_pm_backend_available as ensure_pm_backend_available,
    resolve_pm_backend_kind as resolve_pm_backend_kind,
)
from polaris.delivery.cli.pm.blocked_policy import (
    consume_degrade_settings as consume_degrade_settings,
    evaluate_blocked_policy as evaluate_blocked_policy,
    normalize_director_status as normalize_director_status,
    should_apply_degrade_settings as should_apply_degrade_settings,
)
from polaris.delivery.cli.pm.config import PmRoleState as PmRoleState
from polaris.delivery.cli.pm.engine.core import (
    EngineRuntimeConfig as EngineRuntimeConfig,
    PolarisEngine as PolarisEngine,
)
from polaris.delivery.cli.pm.orchestration.dispatch_pipeline_runner import (
    run_dispatch_pipeline_with_workflow,
)
from polaris.delivery.cli.pm.orchestration.docs_pipeline import (
    _sync_plan_to_runtime as _sync_plan_to_runtime,
)

# Re-export pure helper clusters that were extracted into in-cell submodules so
# callers/tests using ``orchestration_engine.<helper>`` keep resolving. The
# canonical definitions live in the submodules below; these names stay bound
# here for backward compatibility (lossless surface).
from polaris.delivery.cli.pm.orchestration.exit_grading import (
    build_chain_summary as build_chain_summary,
    grade_director_exit_code as grade_director_exit_code,
    grade_qa_exit_code as grade_qa_exit_code,
)
from polaris.delivery.cli.pm.orchestration.pm_failure_detail import (
    _append_unique_schema_warning as _append_unique_schema_warning,
    _build_pm_failure_detail as _build_pm_failure_detail,
    _compact_pm_failure_text as _compact_pm_failure_text,
    _downgrade_recovered_pm_invoke_error as _downgrade_recovered_pm_invoke_error,
    _first_schema_warning as _first_schema_warning,
    _mark_pm_invoke_terminal_failure as _mark_pm_invoke_terminal_failure,
    _pm_invoke_failed as _pm_invoke_failed,
)
from polaris.delivery.cli.pm.orchestration.run_once_runner import run_once_impl
from polaris.delivery.cli.pm.orchestration.workflow_timeout import (
    _DIRECTOR_WORKFLOW_TIMEOUT_MARGIN_SECONDS as _DIRECTOR_WORKFLOW_TIMEOUT_MARGIN_SECONDS,
    _director_workflow_timeout_floor_seconds as _director_workflow_timeout_floor_seconds,
    _pm_workflow_wait_timeout_seconds as _pm_workflow_wait_timeout_seconds,
    _positive_timeout_int as _positive_timeout_int,
)
from polaris.delivery.cli.pm.orchestration.zero_task_fallback import (
    _FALLBACK_WORKSPACE_FILE_EXTENSIONS as _FALLBACK_WORKSPACE_FILE_EXTENSIONS,
    _FALLBACK_WORKSPACE_SKIP_DIRS as _FALLBACK_WORKSPACE_SKIP_DIRS,
    _PM_TASK_QUALITY_DEFAULT_MODE as _PM_TASK_QUALITY_DEFAULT_MODE,
    _PM_TASK_QUALITY_MODE_ENV as _PM_TASK_QUALITY_MODE_ENV,
    _PM_TASK_QUALITY_MODES as _PM_TASK_QUALITY_MODES,
    _PM_TASK_QUALITY_RETRIES_ENV as _PM_TASK_QUALITY_RETRIES_ENV,
    _apply_quality_gate_to_requirements_fallback as _apply_quality_gate_to_requirements_fallback,
    _apply_requirements_fallback_for_empty_tasks as _apply_requirements_fallback_for_empty_tasks,
    _collect_workspace_file_candidates as _collect_workspace_file_candidates,
    _extract_normalized_tasks as _extract_normalized_tasks,
    _resolve_outer_pm_task_quality_mode as _resolve_outer_pm_task_quality_mode,
)
from polaris.delivery.cli.pm.orchestration_core import (
    archive_task_history as archive_task_history,
    check_spin_guard as check_spin_guard,
    check_stop_conditions as check_stop_conditions,
    ensure_docs_ready as ensure_docs_ready,
    load_state_and_context as load_state_and_context,
    update_consecutive_counters as update_consecutive_counters,
)
from polaris.delivery.cli.pm.report_utils import (
    append_pm_report as append_pm_report,
    format_chief_engineer_for_report as format_chief_engineer_for_report,
    format_director_summary_for_report as format_director_summary_for_report,
    format_integration_qa_for_report as format_integration_qa_for_report,
)
from polaris.delivery.cli.pm.tasks import (
    build_resume_payload_from_last_tasks as build_resume_payload_from_last_tasks,
)
from polaris.delivery.cli.pm.tasks_utils import (
    build_requirements_fallback_payload as build_requirements_fallback_payload,
)
from polaris.kernelone.events import (
    emit_event as emit_event,
    emit_llm_event as emit_llm_event,
    set_dialogue_seq as set_dialogue_seq,
)

# Import io utilities
from polaris.kernelone.fs.jsonl.ops import scan_last_seq as scan_last_seq
from polaris.kernelone.storage import (
    resolve_ramdisk_root as resolve_ramdisk_root,
    state_to_ramdisk_enabled as state_to_ramdisk_enabled,
)
from polaris.kernelone.storage.io_paths import (
    build_cache_root as build_cache_root,
    resolve_artifact_path as resolve_artifact_path,
    resolve_run_dir as resolve_run_dir,
    resolve_workspace_path as resolve_workspace_path,
    update_latest_pointer as update_latest_pointer,
)
from polaris.kernelone.traceability.internal.safety import (
    safe_find_node as safe_find_node,
    safe_link as safe_link,
    safe_persist_matrix as safe_persist_matrix,
    safe_register_node as safe_register_node,
    safe_reset as safe_reset,
)
from polaris.kernelone.traceability.public.service import (
    create_traceability_service as create_traceability_service,
)

logger = logging.getLogger(__name__)

# Constants
_ORCHESTRATION_RUNTIME_OPTIONS = set(SUPPORTED_ORCHESTRATION_RUNTIMES)
_ORCHESTRATION_RUNTIME_DEFAULT = "workflow"


def _resolve_orchestration_runtime(args: argparse.Namespace) -> str:
    """Resolve orchestration runtime from args and environment."""
    runtime = resolve_workflow_orchestration_runtime(
        getattr(args, "orchestration_runtime", ""),
        environ=os.environ,
    )
    return runtime if runtime in _ORCHESTRATION_RUNTIME_OPTIONS else _ORCHESTRATION_RUNTIME_DEFAULT


def run_once(args: argparse.Namespace, iteration: int = 1) -> int:
    """Run PM iteration once - Main entry point.

    This is the facade function that orchestrates the entire PM iteration:
    1. Load state and context
    2. Run planning iteration (with quality retry)
    3. Persist contracts
    4. Run dispatch pipeline (if enabled)
    5. Finalize iteration

    Thin delegating shim. The heavy body lives in
    ``orchestration.run_once_runner.run_once_impl``; this canonical name stays
    defined here so callers/tests resolving ``orchestration_engine.run_once``
    keep working and so the runner can resolve monkeypatchable globals through
    this module.

    Args:
        args: Command line arguments namespace
        iteration: Current iteration number (default: 1)

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    return run_once_impl(args, iteration=iteration)


def _run_dispatch_pipeline_with_workflow(
    *,
    args: argparse.Namespace,
    engine: PolarisEngine,
    workspace_full: str,
    cache_root_full: str,
    run_dir: str,
    run_id: str,
    iteration: int,
    normalized: dict[str, Any],
    run_events: str,
    dialogue_full: str,
    runtime_pm_tasks_full: str,
    pm_out_full: str,
    run_pm_tasks: str,
    run_director_result: str,
    docs_stage: dict[str, Any] | None,
    pm_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run dispatch pipeline with workflow orchestration.

    Thin delegating shim. The heavy body lives in
    ``orchestration.dispatch_pipeline_runner.run_dispatch_pipeline_with_workflow``;
    this canonical name stays defined here so callers/tests resolving
    ``orchestration_engine._run_dispatch_pipeline_with_workflow`` keep working and
    so the runner can resolve monkeypatchable globals through this module.
    """
    return run_dispatch_pipeline_with_workflow(
        args=args,
        engine=engine,
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        run_dir=run_dir,
        run_id=run_id,
        iteration=iteration,
        normalized=normalized,
        run_events=run_events,
        dialogue_full=dialogue_full,
        runtime_pm_tasks_full=runtime_pm_tasks_full,
        pm_out_full=pm_out_full,
        run_pm_tasks=run_pm_tasks,
        run_director_result=run_director_result,
        docs_stage=docs_stage,
        pm_state=pm_state,
    )


def _merge_engine_config(payload_engine: Any, args: argparse.Namespace) -> dict[str, Any]:
    """Merge PM payload engine hints with CLI defaults."""
    from polaris.delivery.cli.pm.tasks import normalize_engine_config

    normalized_payload = normalize_engine_config(payload_engine)
    cfg = EngineRuntimeConfig.from_sources(args, normalized_payload)
    return cfg.to_payload()


# Re-export from orchestration_core for backward compatibility

# For backward compatibility - these are now in iteration_state

__all__ = [
    "archive_task_history",
    "check_spin_guard",
    "check_stop_conditions",
    # Core orchestration functions (re-exported from orchestration_core)
    "ensure_docs_ready",
    "load_state_and_context",
    # Main entry point
    "run_once",
    "update_consecutive_counters",
]
