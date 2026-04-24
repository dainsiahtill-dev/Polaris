#!/usr/bin/env python3
"""PM wrapper for Ollama loop - CLI entry point.

This file delegates to the canonical implementation in the pm/ package.
"""

import sys
from pathlib import Path

# Import from pm package (renamed from loop_pm)
import polaris.delivery.cli.pm.agents as _pm_agents_module
import polaris.delivery.cli.pm.backend as _pm_backend_module
import polaris.delivery.cli.pm.orchestration_engine as _pm_orchestration_engine_module

# Import main entry point
# Import from utils
# Import from results
# Import from tasks
# Import from task_splitting
# Import from task_special
# Import from execution
# Import from polaris_engine
# Import from director_mgmt
# Import from agents
# Import from memo
# Import from orchestration
from polaris.delivery.cli.pm import (
    EngineRuntimeConfig,
    PolarisEngine,
    SchedulerProtocol,
    SingleWorkerScheduler,
    _is_interactive_session,
    _migrate_tasks_in_place,
    _use_context_engine_v2,
    apply_task_status_updates,
    build_pm_spin_fingerprint,
    build_resume_payload_from_last_tasks,
    classify_director_start_state,
    collect_schema_warnings,
    consume_interrupt_task,
    execute_non_director_tasks,
    main,
    match_director_result,
    match_director_result_any,
    match_director_result_mode,
    maybe_generate_agents_draft,
    merge_director_tasks,
    normalize_engine_config,
    normalize_match_mode,
    normalize_pm_payload,
    normalize_str_list,
    normalize_tasks,
    persist_pm_payloads,
    requires_manual_intervention_for_error,
    run_chief_engineer_analysis,
    run_chief_engineer_task,
    run_director_once,
    run_once as _run_once_impl,
    should_pause_for_manual_intervention,
    split_director_tasks,
    wait_for_director_result,
)

# Import from config
from polaris.delivery.cli.pm.config import (
    AGENTS_APPROVAL_MODE_ENV,
    AGENTS_APPROVAL_MODES,
    AGENTS_APPROVAL_TIMEOUT_ENV,
    AGENTS_DRAFT_REL,
    AGENTS_FEEDBACK_REL,
    DEFAULT_AGENTS_APPROVAL_MODE,
    DEFAULT_AGENTS_APPROVAL_TIMEOUT,
    DEFAULT_DIRECTOR_STATUS,
    DEFAULT_DIRECTOR_SUBPROCESS_LOG,
    PROJECT_ROOT,
    PROMPT_PROFILE_ENV,
    REQUIRED_MODULE_FILES,
    SCRIPT_DIR,
    PmRoleState,
    build_utf8_env,
    enforce_utf8,
)
from polaris.delivery.cli.pm.task_helpers import (
    _auto_assign_role,
    compute_task_fingerprint,
)
from polaris.kernelone.process.ollama_utils import invoke_ollama
from polaris.kernelone.tool_execution.io_tools import ensure_ollama_available

# Import DirectorInterface integration
try:
    from polaris.delivery.cli.pm import (
        DIRECTOR_INTERFACE_AVAILABLE,
        create_director_for_pm,
        get_director_type,
        is_standalone_mode,
        run_director_via_interface,
        should_use_director_interface,
    )
except ImportError:
    DIRECTOR_INTERFACE_AVAILABLE = False
    create_director_for_pm = None
    run_director_via_interface = None
    should_use_director_interface = None
    is_standalone_mode = None
    get_director_type = None

# Import from backend
from polaris.delivery.cli.pm import (
    invoke_pm_backend,
)


def _bootstrap_backend_import_path() -> None:
    """Ensure backend package path when running file directly."""
    if __package__:
        return
    backend_root = Path(__file__).resolve().parents[3]
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)


# Delegation bridges for test monkeypatching on the wrapper module.
get_anthropomorphic_context = _pm_backend_module.get_anthropomorphic_context
get_anthropomorphic_context_v2 = _pm_backend_module.get_anthropomorphic_context_v2


def build_pm_prompt(*args, **kwargs):
    _pm_backend_module.get_anthropomorphic_context = get_anthropomorphic_context
    _pm_backend_module.get_anthropomorphic_context_v2 = get_anthropomorphic_context_v2
    _pm_backend_module._use_context_engine_v2 = _use_context_engine_v2
    return _pm_backend_module.build_pm_prompt(*args, **kwargs)


def resolve_agents_approval_mode(args):  # type: ignore[no-redef]
    _pm_agents_module._is_interactive_session = _is_interactive_session
    return _pm_agents_module.resolve_agents_approval_mode(args)


def wait_for_agents_confirmation(*args, **kwargs):  # type: ignore[no-redef]
    _pm_agents_module._is_interactive_session = _is_interactive_session
    _pm_agents_module.maybe_generate_agents_draft = maybe_generate_agents_draft
    return _pm_agents_module.wait_for_agents_confirmation(*args, **kwargs)


def _invoke_pm_backend(*args, **kwargs):
    return invoke_pm_backend(*args, **kwargs)


def run_once(args, iteration=1):
    # Keep wrapper-level monkeypatch behavior for tests that patch this module.
    prev_invoke_ollama = _pm_backend_module.invoke_ollama
    prev_ensure_ollama_available = _pm_backend_module.ensure_ollama_available
    prev_backend_invoke = _pm_backend_module.invoke_pm_backend
    prev_engine_invoke = _pm_orchestration_engine_module.invoke_pm_backend
    try:
        _pm_backend_module.invoke_ollama = invoke_ollama
        _pm_backend_module.ensure_ollama_available = ensure_ollama_available
        _pm_backend_module.invoke_pm_backend = _invoke_pm_backend
        _pm_orchestration_engine_module.invoke_pm_backend = _invoke_pm_backend
        return _run_once_impl(args, iteration=iteration)
    finally:
        _pm_backend_module.invoke_ollama = prev_invoke_ollama
        _pm_backend_module.ensure_ollama_available = prev_ensure_ollama_available
        _pm_backend_module.invoke_pm_backend = prev_backend_invoke
        _pm_orchestration_engine_module.invoke_pm_backend = prev_engine_invoke


__all__ = [
    "AGENTS_APPROVAL_MODES",
    "AGENTS_APPROVAL_MODE_ENV",
    "AGENTS_APPROVAL_TIMEOUT_ENV",
    "AGENTS_DRAFT_REL",
    "AGENTS_FEEDBACK_REL",
    "DEFAULT_AGENTS_APPROVAL_MODE",
    "DEFAULT_AGENTS_APPROVAL_TIMEOUT",
    "DEFAULT_DIRECTOR_STATUS",
    "DEFAULT_DIRECTOR_SUBPROCESS_LOG",
    "DIRECTOR_INTERFACE_AVAILABLE",
    "PROJECT_ROOT",
    "PROMPT_PROFILE_ENV",
    "REQUIRED_MODULE_FILES",
    "SCRIPT_DIR",
    "EngineRuntimeConfig",
    "PmRoleState",
    "PolarisEngine",
    "SchedulerProtocol",
    "SingleWorkerScheduler",
    "_auto_assign_role",
    "_is_interactive_session",
    "_migrate_tasks_in_place",
    "_use_context_engine_v2",
    "apply_task_status_updates",
    "build_pm_prompt",
    "build_pm_spin_fingerprint",
    "build_resume_payload_from_last_tasks",
    "build_utf8_env",
    "classify_director_start_state",
    "collect_schema_warnings",
    "compute_task_fingerprint",
    "consume_interrupt_task",
    "create_director_for_pm",
    "enforce_utf8",
    "execute_non_director_tasks",
    "get_director_type",
    "is_standalone_mode",
    "main",
    "match_director_result",
    "match_director_result_any",
    "match_director_result_mode",
    "merge_director_tasks",
    "normalize_engine_config",
    "normalize_match_mode",
    "normalize_pm_payload",
    "normalize_str_list",
    "normalize_tasks",
    "persist_pm_payloads",
    "requires_manual_intervention_for_error",
    "resolve_agents_approval_mode",
    "run_chief_engineer_analysis",
    "run_chief_engineer_task",
    "run_director_once",
    "run_director_via_interface",
    "run_once",
    "should_pause_for_manual_intervention",
    "should_use_director_interface",
    "split_director_tasks",
    "wait_for_agents_confirmation",
    "wait_for_director_result",
]

if __name__ == "__main__":
    _bootstrap_backend_import_path()
    raise SystemExit(main())
