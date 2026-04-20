"""loop-pm package - PM orchestration for Polaris."""

from typing import Any

# Version and metadata (eagerly loaded)
__version__ = "1.0.0"

# Placeholder module-level attributes (will be set via lazy import)
# These are referenced in __all__ but need special handling
state_manager = None
requirements_tracker = None
document_manager = None
task_orchestrator = None
execution_tracker = None
pm_integration = None

# Symbol map: symbol name -> (module_path, import_name)
# If import_name is None, the symbol name is used
_SYMBOL_MAP = {
    # Config exports
    "enforce_utf8": ("pm.config", None),
    "build_utf8_env": ("pm.config", None),
    "SCRIPT_DIR": ("pm.config", None),
    "PROJECT_ROOT": ("pm.config", None),
    "PROMPT_PROFILE_ENV": ("pm.config", None),
    "AGENTS_APPROVAL_MODE_ENV": ("pm.config", None),
    "AGENTS_APPROVAL_TIMEOUT_ENV": ("pm.config", None),
    "DEFAULT_DIRECTOR_SUBPROCESS_LOG": ("pm.config", None),
    "DEFAULT_DIRECTOR_STATUS": ("pm.config", None),
    "AGENTS_DRAFT_REL": ("pm.config", None),
    "AGENTS_FEEDBACK_REL": ("pm.config", None),
    "CANONICAL_PM_TASKS_REL": ("pm.config", None),
    "DEFAULT_ASSIGNEE_EXECUTION": ("pm.config", None),
    "AGENTS_APPROVAL_MODES": ("pm.config", None),
    "DEFAULT_AGENTS_APPROVAL_MODE": ("pm.config", None),
    "DEFAULT_AGENTS_APPROVAL_TIMEOUT": ("pm.config", None),
    "REQUIRED_MODULE_FILES": ("pm.config", None),
    "SUPPORTED_ASSIGNEES": ("pm.config", None),
    "ACTIVE_TASK_STATUSES": ("pm.config", None),
    "TERMINAL_TASK_STATUSES": ("pm.config", None),
    "DEFAULT_PM_SCHEMA_REQUIRED_FIELDS": ("pm.config", None),
    "DEFAULT_DEFECT_TICKET_FIELDS": ("pm.config", None),
    "MANUAL_INTERVENTION_STATUS": ("pm.config", None),
    "MANUAL_INTERVENTION_RESUME_NOTE": ("pm.config", None),
    "PM_SPIN_GUARD_STATUS": ("pm.config", None),
    "PRIORITY_ALIASES": ("pm.config", None),
    "ARCHITECT_KEYWORDS": ("pm.config", None),
    "CHIEF_ENGINEER_KEYWORDS": ("pm.config", None),
    "POLICY_KEYWORDS": ("pm.config", None),
    "FINOPS_KEYWORDS": ("pm.config", None),
    "AUDIT_KEYWORDS": ("pm.config", None),
    "MODULE_DIR": ("pm.config", None),
    "PmRoleState": ("pm.config", None),
    "load_pm_model_config": ("pm.config", None),
    "_PM_PROVIDER_ID": ("pm.config", None),
    "_PM_MODEL": ("pm.config", None),
    # Utils exports
    "truncate_text_block": ("pm.utils", None),
    "_is_interactive_session": ("pm.utils", None),
    "read_json_file": ("pm.utils", None),
    "read_tail_lines": ("pm.utils", None),
    "append_text": ("pm.utils", None),
    "format_json_for_prompt": ("pm.utils", None),
    "compact_text": ("pm.utils", None),
    "_slug_token": ("pm.utils", None),
    "_use_context_engine_v2": ("pm.utils", None),
    "auto_plan_enabled": ("pm.utils", None),
    "is_qa_enabled": ("pm.utils", None),
    "normalize_str_list": ("pm.utils", None),
    "_is_docs_path": ("pm.utils", None),
    "_normalize_scope_list": ("pm.utils", None),
    "_normalize_policy_decision": ("pm.utils", None),
    "_normalize_audit_result": ("pm.utils", None),
    "should_pause_for_manual_intervention": ("pm.utils", None),
    "requires_manual_intervention_for_error": ("pm.utils", None),
    # Backend exports
    "_extract_json_from_llm_output": ("pm.backend", None),
    "resolve_pm_backend_kind": ("pm.backend", None),
    "ensure_pm_backend_available": ("pm.backend", None),
    "invoke_pm_backend": ("pm.backend", None),
    "build_pm_prompt": ("pm.backend", None),
    # Director management exports
    "append_director_log": ("pm.director_mgmt", None),
    "write_director_status": ("pm.director_mgmt", None),
    "run_director_once": ("pm.director_mgmt", None),
    "detect_plan_missing": ("pm.director_mgmt", None),
    "preflight_director_plan": ("pm.director_mgmt", None),
    "archive_if_exists": ("pm.director_mgmt", None),
    "build_run_dir": ("pm.director_mgmt", None),
    # Agents exports
    "resolve_agents_approval_mode": ("pm.agents", None),
    "resolve_agents_approval_timeout": ("pm.agents", None),
    "maybe_generate_agents_draft": ("pm.agents", None),
    "wait_for_agents_confirmation": ("pm.agents", None),
    # Tasks exports
    "normalize_engine_config": ("pm.tasks", None),
    "normalize_task_status": ("pm.tasks", None),
    "normalize_director_result_status": ("pm.tasks", None),
    "normalize_priority": ("pm.tasks", None),
    "normalize_assigned_to": ("pm.tasks", None),
    "normalize_required_evidence": ("pm.tasks", None),
    "normalize_tasks": ("pm.tasks", None),
    "normalize_pm_payload": ("pm.tasks", None),
    "_migrate_tasks_in_place": ("pm.tasks", None),
    "apply_task_status_updates": ("pm.tasks", None),
    "collect_schema_warnings": ("pm.tasks", None),
    "build_pm_spin_fingerprint": ("pm.tasks", None),
    # Task helpers exports
    "_auto_assign_role": ("pm.task_helpers", None),
    # Task splitting exports
    "split_director_tasks": ("pm.task_splitting", None),
    "merge_director_tasks": ("pm.task_splitting", None),
    "persist_pm_payloads": ("pm.task_splitting", None),
    # Task special exports
    "consume_interrupt_task": ("pm.task_special", None),
    "extract_defect_ticket": ("pm.task_helpers", None),
    "validate_ticket_fields": ("pm.task_helpers", None),
    "build_defect_followup_task": ("pm.task_special", None),
    # Execution exports
    "execute_non_director_tasks": ("pm.execution", None),
    "run_chief_engineer_analysis": ("pm.chief_engineer", None),
    "run_chief_engineer_task": ("pm.chief_engineer", None),
    # Engine exports
    "SchedulerProtocol": ("pm.polaris_engine", None),
    "SingleWorkerScheduler": ("pm.polaris_engine", None),
    "EngineRuntimeConfig": ("pm.polaris_engine", None),
    "PolarisEngine": ("pm.polaris_engine", None),
    # Results exports
    "wait_for_director_result": ("pm.results", None),
    "match_director_result": ("pm.results", None),
    "match_director_result_any": ("pm.results", None),
    "result_timestamp_epoch": ("pm.results", None),
    "normalize_match_mode": ("pm.results", None),
    "match_director_result_mode": ("pm.results", None),
    "wait_for_director_result_mode": ("pm.results", None),
    "is_director_done": ("pm.results", None),
    "build_director_fallback_result": ("pm.results", None),
    "read_director_lifecycle_for_run": ("pm.results", None),
    "classify_director_start_state": ("pm.results", None),
    "build_director_response": ("pm.results", None),
    "build_pm_review": ("pm.results", None),
    "emit_pm_director_conversation": ("pm.results", None),
    # Memo exports
    "build_pm_memo": ("pm.memo", None),
    "write_pm_memo": ("pm.memo", None),
    "write_pm_memo_index": ("pm.memo", None),
    "write_pm_memo_summary": ("pm.memo", None),
    # Orchestration exports
    "run_once": ("pm.orchestration_engine", None),
    "ensure_docs_ready": ("pm.orchestration_engine", None),
    "archive_task_history": ("pm.orchestration_engine", None),
    # CLI exports
    "main": ("pm.cli", None),
}

# Cache for imported symbols
_import_cache: dict[str, Any] = {}


def __getattr__(name: str):
    """Lazy import of symbols to avoid circular imports."""
    global state_manager, requirements_tracker, document_manager
    global task_orchestrator, execution_tracker, pm_integration

    # Handle special module-level attributes that need to be set globally
    if name in (
        "state_manager",
        "requirements_tracker",
        "document_manager",
        "task_orchestrator",
        "execution_tracker",
        "pm_integration",
    ):
        # Try to import from polaris.delivery.cli.pm.orchestration_core if available
        try:
            import importlib

            core = importlib.import_module("pm.orchestration_core")
            value = getattr(core, name, None)
            globals()[name] = value
            return value
        except ImportError:
            globals()[name] = None
            return None

    # Check symbol map
    if name in _SYMBOL_MAP:
        # Check cache first
        if name in _import_cache:
            return _import_cache[name]

        module_path, import_name = _SYMBOL_MAP[name]
        actual_name = import_name if import_name else name

        try:
            import importlib

            module = importlib.import_module(module_path)
            value = getattr(module, actual_name)
            _import_cache[name] = value
            return value
        except (ImportError, AttributeError) as e:
            raise ImportError(f"cannot import name '{name}' from '{module_path}'") from e

    # Handle Director Interface Integration exports with fallback
    if name in (
        "create_director_for_pm",
        "run_director_via_interface",
        "should_use_director_interface",
        "is_standalone_mode",
        "get_director_type",
        "DIRECTOR_INTERFACE_AVAILABLE",
    ):
        try:
            import importlib

            module = importlib.import_module("pm.director_interface_integration")
            value = getattr(module, name)
            globals()[name] = value
            return value
        except ImportError:
            # Fallback for DIRECTOR_INTERFACE_AVAILABLE
            if name == "DIRECTOR_INTERFACE_AVAILABLE":
                globals()[name] = False
                return False
            # Fallback for other functions
            globals()[name] = None
            return None

    raise AttributeError(f"module 'pm' has no attribute '{name}'")


def __dir__():
    """Return list of available attributes."""
    return sorted(
        set(
            [
                "__version__",
                *list(_SYMBOL_MAP.keys()),
                "state_manager",
                "requirements_tracker",
                "document_manager",
                "task_orchestrator",
                "execution_tracker",
                "pm_integration",
                "create_director_for_pm",
                "run_director_via_interface",
                "should_use_director_interface",
                "is_standalone_mode",
                "get_director_type",
                "DIRECTOR_INTERFACE_AVAILABLE",
            ]
        )
    )


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "AGENTS_APPROVAL_MODES",
    "AGENTS_APPROVAL_MODE_ENV",
    "AGENTS_APPROVAL_TIMEOUT_ENV",
    "AGENTS_DRAFT_REL",
    "AGENTS_FEEDBACK_REL",
    "ARCHITECT_KEYWORDS",
    "AUDIT_KEYWORDS",
    "CANONICAL_PM_TASKS_REL",
    "CHIEF_ENGINEER_KEYWORDS",
    "DEFAULT_AGENTS_APPROVAL_MODE",
    "DEFAULT_AGENTS_APPROVAL_TIMEOUT",
    "DEFAULT_ASSIGNEE_EXECUTION",
    "DEFAULT_DEFECT_TICKET_FIELDS",
    "DEFAULT_DIRECTOR_STATUS",
    "DEFAULT_DIRECTOR_SUBPROCESS_LOG",
    "DEFAULT_PM_SCHEMA_REQUIRED_FIELDS",
    "DIRECTOR_INTERFACE_AVAILABLE",
    "FINOPS_KEYWORDS",
    "MANUAL_INTERVENTION_RESUME_NOTE",
    "MANUAL_INTERVENTION_STATUS",
    "MODULE_DIR",
    "PM_SPIN_GUARD_STATUS",
    "POLICY_KEYWORDS",
    "PRIORITY_ALIASES",
    "PROJECT_ROOT",
    "PROMPT_PROFILE_ENV",
    "REQUIRED_MODULE_FILES",
    "SCRIPT_DIR",
    "SUPPORTED_ASSIGNEES",
    "TERMINAL_TASK_STATUSES",
    "_PM_MODEL",
    "_PM_PROVIDER_ID",
    "EngineRuntimeConfig",
    "PolarisEngine",
    "PmRoleState",
    # Engine
    "SchedulerProtocol",
    "SingleWorkerScheduler",
    "__version__",
    "_auto_assign_role",
    # Backend
    "_extract_json_from_llm_output",
    "_is_docs_path",
    "_is_interactive_session",
    "_migrate_tasks_in_place",
    "_normalize_audit_result",
    "_normalize_policy_decision",
    "_normalize_scope_list",
    "_slug_token",
    "_use_context_engine_v2",
    # Director
    "append_director_log",
    "append_text",
    "apply_task_status_updates",
    "archive_if_exists",
    "archive_task_history",
    "auto_plan_enabled",
    "build_defect_followup_task",
    "build_director_fallback_result",
    "build_director_response",
    # Memo
    "build_pm_memo",
    "build_pm_prompt",
    "build_pm_review",
    "build_pm_spin_fingerprint",
    "build_run_dir",
    "build_utf8_env",
    "classify_director_start_state",
    "collect_schema_warnings",
    "compact_text",
    "consume_interrupt_task",
    # Director Interface Integration
    "create_director_for_pm",
    "detect_plan_missing",
    "document_manager",
    "emit_pm_director_conversation",
    # Config
    "enforce_utf8",
    "ensure_docs_ready",
    "ensure_pm_backend_available",
    # Execution
    "execute_non_director_tasks",
    "execution_tracker",
    "extract_defect_ticket",
    "format_json_for_prompt",
    "get_director_type",
    "invoke_pm_backend",
    "is_director_done",
    "is_qa_enabled",
    "is_standalone_mode",
    "load_pm_model_config",
    # CLI
    "main",
    "match_director_result",
    "match_director_result_any",
    "match_director_result_mode",
    "maybe_generate_agents_draft",
    "merge_director_tasks",
    "normalize_assigned_to",
    "normalize_director_result_status",
    "normalize_engine_config",
    "normalize_match_mode",
    "normalize_pm_payload",
    "normalize_priority",
    "normalize_required_evidence",
    "normalize_str_list",
    # Tasks
    "normalize_task_status",
    "normalize_tasks",
    "persist_pm_payloads",
    "pm_integration",
    "preflight_director_plan",
    "read_director_lifecycle_for_run",
    "read_json_file",
    "read_tail_lines",
    "requirements_tracker",
    "requires_manual_intervention_for_error",
    # Agents
    "resolve_agents_approval_mode",
    "resolve_agents_approval_timeout",
    "resolve_pm_backend_kind",
    "result_timestamp_epoch",
    "run_chief_engineer_analysis",
    "run_chief_engineer_task",
    "run_director_once",
    "run_director_via_interface",
    # Orchestration
    "run_once",
    "should_pause_for_manual_intervention",
    "should_use_director_interface",
    "split_director_tasks",
    # PM Core
    "state_manager",
    "task_orchestrator",
    # Utils
    "truncate_text_block",
    "validate_ticket_fields",
    "wait_for_agents_confirmation",
    # Results
    "wait_for_director_result",
    "wait_for_director_result_mode",
    "write_director_status",
    "write_pm_memo",
    "write_pm_memo_index",
    "write_pm_memo_summary",
]
