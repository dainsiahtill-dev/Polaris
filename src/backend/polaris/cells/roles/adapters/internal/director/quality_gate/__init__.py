"""Director artifact-quality collection + quality-repair flow.

Artifact-quality collection, error-path parsing, and the LLM-driven quality
repair flow (including ``scan_workspace_artifact_quality`` orchestration),
extracted verbatim from ``execute_method.py`` during the lossless
decomposition of that god-module.

This package is the lossless successor of the former ``quality_gate`` module.
It re-exports every previously-public symbol from the same import path so
``import ...director.quality_gate`` and ``from ...director.quality_gate import X``
keep resolving identically for all external importers.

The ``scan_workspace_artifact_quality`` reference is resolved through
``execute_method`` (aliased ``_em``) at call time so a test
``monkeypatch`` on the ``execute_method`` module namespace still takes effect.
The canonical import path remains ``execute_method`` (which re-exports every
symbol here).
"""

from __future__ import annotations

# Backward-compatible re-export of stdlib / typing names that were module-level
# attributes of the former single-file module (preserves dir() oracle COUNT/HASH).
import ast
import contextlib
import hashlib
import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

from polaris.cells.director.runtime.public.contracts import DirectorInterfaceDiscrepancyReceiptV1
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.quality import (
    artifact_quality_issue_raw,
    artifact_quality_issues_for_errors,
    artifact_quality_issues_from_errors,
    build_scope_authority_decision,
    partition_paths_by_declared_scope,
    scope_authority_decision_summary,
)

from .. import execute_method as _em
from ..artifact_quality_diagnostics import (
    _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE,
    _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE,
    _build_unresolved_import_symbol_repair_block,
    _missing_unresolved_relative_import_target_files,
    _parse_missing_declared_target_files,
    _relative_import_repair_target_candidates,
)
from ..contract_verify import resolve_contract_step_verify
from ..helpers import has_successful_write_tool
from ..materialization_quality_boundary import run_materialization_quality_public_boundary
from ..materialization_quality_runtime_ports import has_materialization_quality_runtime_repair_coverage
from ..repair_profile_projection import project_repair_kernel_summary
from ..runtime_repair_tool_adapter import (
    defer_director_command_with_director_tools,
    run_runtime_repair_with_director_tools,
)
from ..task_scope_paths import (
    _dedupe_preserve_order,
    _extract_project_declared_target_path_candidates,
    _extract_task_path_candidates,
    _extract_task_target_path_candidates,
    _filter_diff_to_task_declared_paths,
    _normalize_declared_task_path,
    _path_candidate_exists_in_file_set,
    _task_has_declared_target_files,
    _task_text_blob,
    _workspace_path_exists_case_insensitive,
)

# Implementation submodules (domain split of former single-file module).
from . import (
    _boundary_and_verify as _boundary_and_verify,
    _language_targets as _language_targets,
    _prompt_and_targets as _prompt_and_targets,
    _repair_loop as _repair_loop,
    _scope_scan as _scope_scan,
)
from ._boundary_and_verify import (
    _ACCEPTANCE_TEST_FILE_FLAGS,
    _ACCEPTANCE_VERIFY_EXISTS_RE,
    _JS_MISSING_NAMED_EXPORT_RE,
    _JS_MODULE_SYSTEM_REPAIR_MARKERS,
    _JS_NAMED_IMPORT_RE,
    _NPM_SCRIPT_GENERATED_OUTPUT_PREFIXES,
    _NPM_SCRIPT_MISSING_LOCAL_ENTRYPOINT_RE,
    _NPM_SCRIPT_MISSING_LOCAL_MODULE_RE,
    _NPM_SCRIPT_REPAIRABLE_SOURCE_PREFIXES,
    _QUALITY_SYNTAX_ERROR_PATH_RE,
    _RAW_SINGLE_TARGET_CODE_FENCE_RE,
    _STEP_VERIFY_NODE_ENV_COMMAND_RE,
    _TOOL_RECEIPT_CONTAMINATION_TOKENS,
    _TSC_PROJECT_DIAGNOSTIC_RE,
    _VERIFY_GREP_FILE_RE,
    _VERIFY_TEST_FILE_RE,
    _VERIFY_WC_PATH_RE,
    _build_javascript_module_system_repair_block,
    _build_javascript_named_export_repair_block,
    _build_materialization_quality_failure_evidence_context,
    _build_materialization_quality_workspace_evidence_context,
    _clean_verify_path_token,
    _collect_step_verify_errors,
    _collect_workspace_code_diff,
    _collect_workspace_out_of_scope_diff,
    _deterministic_single_missing_python_module_alias_to_write_file,
    _deterministic_single_missing_quality_repair_to_write_file,
    _director_repair_force_existing_write_enabled,
    _evaluate_acceptance_verify_exists,
    _evaluate_machine_checkable_acceptance_criterion,
    _evaluate_safe_acceptance_clause,
    _format_quality_error_for_repair_prompt,
    _format_tool_receipt_contamination_error_for_repair_prompt,
    _format_typescript_project_typecheck_error_for_repair_prompt,
    _format_unresolved_relative_import_error_for_repair_prompt,
    _is_recoverable_no_write_mutation_contract_error_text,
    _is_recoverable_no_write_mutation_contract_exception,
    _iter_stage_summary_error_texts,
    _js_default_imports_for_module,
    _js_imported_symbols_for_module,
    _looks_like_tool_receipt_contamination_text,
    _near_miss_verify_target_paths,
    _node_environment_has_missing_declared_packages,
    _normalize_raw_single_target_write_content,
    _parse_js_named_import_symbols,
    _partition_paths_by_task_write_scope,
    _path_stem_identity,
    _path_within_task_write_scope,
    _quality_error_path_safe_for_repair_prompt,
    _quality_repair_base_files,
    _quality_repair_edit_file_tool_definition,
    _quality_repair_execute_command_tool_definition,
    _quality_repair_execution_attempt,
    _quality_repair_existing_target_tool_definitions,
    _quality_repair_write_file_tool_definition,
    _record_deferred_step_verify_obligation,
    _record_deferred_task_boundary_quality_errors,
    _reject_raw_single_target_repair_body,
    _relative_import_specifier_safe_for_repair_prompt,
    _resolve_workspace_path_case_insensitive,
    _run_materialization_quality_public_boundary,
    _safe_int,
    _single_file_step_target,
    _stage_summary_has_recoverable_no_write_mutation_contract_exception,
    _step_verify_environment_prep_plans,
    _step_verify_target_mismatch_error,
    _summarize_llm_stage_result,
    _task_write_scope_candidates,
    _tool_receipt_safe_quality_errors,
    _verify_referenced_file_paths,
    _workspace_path_satisfies_flag,
)
from ._language_targets import (
    _CLI_ENTRYPOINT_REPAIR_CANDIDATES,
    _EXPLICIT_ARTIFACT_QUALITY_TARGET_HINTS,
    _GO_COMPILE_PATH_RE,
    _GO_IMPORT_SPEC_RE,
    _GO_MISSING_MEMBER_TYPE_RE,
    _GO_RUN_COMMAND_TARGET_RE,
    _GO_TEST_FAILURE_TITLE_RE,
    _NODE_COMMAND_JS_TARGET_RE,
    _NODE_STACK_JS_PATH_RE,
    _PYTHON_TEST_HARNESS_PATH_RE,
    _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES,
    _SEMANTIC_QUALITY_SINGLE_TARGET_HINTS,
    _SOURCE_REPAIR_EXTENSIONS,
    _artifact_quality_failed_test_count,
    _changed_source_repair_target_files,
    _compiled_javascript_stack_source_candidates,
    _embedded_rust_compile_repair_target_files,
    _explicit_artifact_quality_repair_target_files,
    _failed_test_title_target_files,
    _go_compile_error_target_files,
    _go_files_in_directory,
    _go_import_path_workspace_directories,
    _go_missing_member_type_definition_target_files,
    _go_missing_member_type_refs,
    _go_package_qualifier_target_directories,
    _go_production_files_matching_tokens,
    _go_runtime_smoke_command_target_files,
    _go_runtime_smoke_repair_target_files,
    _go_test_behavior_repair_target_files,
    _go_test_title_tokens,
    _has_non_test_python_traceback_source,
    _is_test_like_javascript_path,
    _is_test_like_python_path,
    _javascript_facade_related_source_target_files,
    _javascript_relative_import_refs,
    _javascript_relative_reexport_refs,
    _javascript_runtime_smoke_path_candidates,
    _javascript_runtime_smoke_repair_target_files,
    _javascript_test_imported_source_target_files,
    _looks_like_cli_subcommand_quality_failure,
    _looks_like_embedded_rust_compile_failure,
    _looks_like_go_workspace_quality_error,
    _looks_like_javascript_module_system_failure,
    _looks_like_javascript_runtime_smoke_quality_error,
    _looks_like_javascript_test_behavior_failure,
    _looks_like_python_missing_module_failure,
    _looks_like_python_module_coupling_failure,
    _looks_like_python_regex_source_quality_failure,
    _looks_like_python_runtime_smoke_quality_error,
    _looks_like_python_test_behavior_failure,
    _looks_like_python_test_harness_quality_failure,
    _map_quality_error_path_to_changed_file,
    _python_runtime_smoke_imported_source_target_files,
    _python_runtime_smoke_missing_module_source_targets,
    _python_runtime_smoke_repair_target_files,
    _python_runtime_smoke_traceback_repair_target_files,
    _python_test_harness_changed_source_target_files,
    _python_unittest_failure_test_target_files,
    _python_unittest_module_candidate_paths,
    _resolve_javascript_relative_import_target,
    _typescript_source_repair_target_for_javascript_output,
    _workspace_cli_entrypoint_repair_target_files,
    _workspace_go_entrypoint_repair_target_files,
    _workspace_relative_go_repair_target,
    _workspace_relative_javascript_repair_target,
    _workspace_relative_rust_repair_target,
    _workspace_rust_source_repair_target_files,
)
from ._prompt_and_targets import (
    _DECLARED_TARGET_MISSING_ISSUE_CODES,
    _MISSING_WORKSPACE_FILE_ISSUE_CODES,
    _PYTHON_MODULE_ALIAS_ISSUE_CODES,
    _QUALITY_REPAIR_CONTROL_LINE_RE,
    _QUALITY_REPAIR_FILEISH_RE,
    _bounded_interface_discrepancy_prompt_payload,
    _build_existing_workspace_task_evidence,
    _build_full_verifier_diagnostics_block,
    _build_materialization_quality_repair_message,
    _can_accept_existing_workspace_scope,
    _coerce_artifact_quality_issue_module,
    _coerce_artifact_quality_issue_path,
    _compact_original_message_for_quality_repair,
    _concrete_npm_test_glob_repair_target,
    _director_direct_text_patch_only_enabled,
    _director_existing_scope_preflight_enabled,
    _find_python_module_alias_source,
    _find_python_module_alias_sources,
    _is_generated_quality_repair_target,
    _is_typescript_command_config_path,
    _iter_artifact_quality_issue_payloads,
    _missing_declared_target_files,
    _missing_materialization_quality_repair_target_files,
    _missing_npm_script_entrypoint_repair_target_files,
    _missing_python_module_alias_repair_target_files,
    _missing_workspace_file_path_to_relative,
    _missing_workspace_file_quality_repair_target_files,
    _missing_workspace_file_target_allowed,
    _npm_script_entrypoint_repair_target_allowed,
    _npm_script_entrypoint_repair_target_candidates,
    _python_missing_module_target,
    _repair_target_context_block,
    _requirements_txt_declared_dependencies,
    _resolve_quality_error_module_target,
    _semantic_quality_exporting_module_targets,
    _semantic_quality_repair_target_files,
    _task_requires_fresh_materialization,
    _typescript_diagnostic_target_files,
    _typescript_type_only_usage_files,
    _typescript_unknown_exporter_target_files,
    _workspace_file_contract_assertion_allows_existing_target,
)
from ._repair_loop import (
    _DEFAULT_QUALITY_REPAIR_TIMEOUT_SECONDS,
    _FAILED_TEST_TITLE_RE,
    _MISSING_WORKSPACE_DIRECTORY_ALLOWLIST,
    _MISSING_WORKSPACE_FILE_ALLOWED_PREFIXES,
    _MISSING_WORKSPACE_FILE_PATTERNS,
    _MISSING_WORKSPACE_ROOT_FILE_ALLOWLIST,
    _PYTHON_MODULE_NOT_FOUND_RE,
    _PYTHON_RUNTIME_SMOKE_TARGET_PATTERNS,
    _PYTHON_TRACEBACK_FILE_RE,
    _PYTHON_UNITTEST_RESULT_LINE_RE,
    _QUALITY_REPAIR_ATTEMPT_HARD_CAP,
    _QUALITY_REPAIR_BASE_ATTEMPTS,
    _QUALITY_REPAIR_DEADLINE_DEFAULT_SAFETY_SECONDS,
    _QUALITY_REPAIR_DEADLINE_MIN_LLM_SECONDS,
    _QUALITY_REPAIR_TARGET_BATCH_LIMIT,
    _REQUIREMENTS_TXT_ASSERT_IN_DEP_RE,
    _REQUIREMENTS_TXT_MUST_DECLARE_DEP_RE,
    _REQUIREMENTS_TXT_NON_PACKAGE_WORDS,
    _RUST_COMPILE_PATH_RE,
    _SEMANTIC_QUALITY_EXPLICIT_PATH_RE,
    _TAP_FAILED_TEST_RE,
    _TEST_SUMMARY_FAIL_RE,
    _TS_DIAGNOSTIC_PATH_RE,
    _TS_EXPORTED_DECLARATION_TEMPLATE,
    _TS_NO_EXPORTED_MEMBER_QUALITY_RE,
    _TS_TYPE_ONLY_VALUE_QUALITY_RE,
    _TS_UNKNOWN_VALUE_QUALITY_RE,
    _annotate_current_task_missing_target_continuation,
    _context_float_value,
    _extract_task_interface_contract,
    _filter_materialization_quality_errors_for_repair_targets,
    _has_scaffold_marker_quality_error,
    _materialization_interface_discrepancy_evidence,
    _materialization_interface_discrepancy_retry_authorized,
    _materialization_plan_probe_requires_task_boundary_triage,
    _ordered_materialization_quality_repair_target_candidates,
    _quality_repair_deadline_decision,
    _resolve_quality_repair_timeout_seconds,
    _run_materialization_quality_repair_retry,
    _runtime_quality_targets_should_precede_missing,
    _select_materialization_quality_repair_target_batch,
    _should_preserve_materialization_quality_repair_batch,
    _should_preserve_python_cross_language_harness_repair_batch,
    _should_rotate_materialization_quality_repair_targets,
)
from ._scope_scan import (
    _artifact_quality_issue_paths_by_raw,
    _cargo_manifest_should_be_rescanned_for_rust_files,
    _case_insensitive_file_match,
    _collect_materialization_quality_errors,
    _collect_materialization_quality_findings,
    _declared_target_file_quality_errors,
    _declared_target_file_quality_findings,
    _dict_items,
    _execute_method_artifact_quality_scanner_is_default,
    _extract_successful_write_paths,
    _filter_missing_workspace_file_errors_to_task_write_scope,
    _filter_npm_script_entrypoint_errors_to_task_write_scope,
    _filter_project_completion_errors_to_task_boundary,
    _has_materialization_quality_runtime_repair_coverage,
    _is_node_runtime_source_path,
    _is_rust_missing_binary_quality_error,
    _is_rust_source_path,
    _materialization_quality_scan_paths,
    _materialization_quality_scan_paths_with_package_manifest,
    _materialization_quality_task_id,
    _merge_successful_write_paths,
    _node_package_manifest_should_be_rescanned_for_test_files,
    _quality_repair_cache_root,
    _run_post_llm_materialization_runtime_guard,
    _scan_workspace_artifact_quality_findings,
    _semantic_exporter_scope_discrepancy_evidence,
    _should_defer_missing_workspace_target,
    _task_boundary_requesting_task_id,
    _task_boundary_scope_filter_evidence,
    _task_write_scope_touches_rust,
)


def _wire_cross_module_namespace() -> None:
    """Inject sibling symbols into each submodule globals for free-name lookup.

    Functions defined in submodules resolve free names via their module
    ``__dict__``. After the package re-exports every symbol, copy non-owned
    names into each submodule so cross-module calls remain lossless without
    rewriting call sites. Ownership is each submodule's ``__all__``.
    """
    import sys

    pkg = sys.modules[__name__]
    shared = {key: value for key, value in pkg.__dict__.items() if not key.startswith("__")}
    for mod in (
        _boundary_and_verify,
        _scope_scan,
        _repair_loop,
        _language_targets,
        _prompt_and_targets,
    ):
        owned = set(getattr(mod, "__all__", ()) or ())
        for key, value in shared.items():
            if key not in owned:
                mod.__dict__[key] = value


_wire_cross_module_namespace()
