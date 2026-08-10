#!/usr/bin/env python3
# The direct-execution bootstrap must add this module's source tree before
# importing the Polaris packages below.
# ruff: noqa: E402

"""Factory-bench runner — drive the FULL Polaris role chain per project.

For each project in ``projects_v2.json`` (L1→L12, sequential — the local vLLM
is a shared single GPU, so this runner IS the load mutex):

1. create a fresh workspace directory;
2. hand the project brief to the Polaris role chain (PM→Chief Engineer→
   Director→QA) headlessly;
3. collect generated artifacts (plan/blueprint docs, QA verdicts, code);
4. run the project's deterministic checks (``factory_audit``) and append a
   schema-stamped audit record.

Benchmark discipline (memory: benchmark-run-discipline): one project at a
time, ``--max-failed`` early stop, audit + root-cause before continuing.

Implementation helpers live in the private package ``_bench_lib``;
this module remains the CLI entry point and public symbol surface.
"""

from __future__ import annotations

# mypy: disable-error-code=attr-defined
import sys
from pathlib import Path

_MODULE_BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_MODULE_BACKEND_ROOT))

from scripts.factory_bench._bench_lib import impl as _impl

# ---------------------------------------------------------------------------
# Public symbol surface (explicit Assign so surface oracle remains stable)
# ---------------------------------------------------------------------------
FACTORY_BENCH_REQUIRED_LLM_ROLES = _impl.FACTORY_BENCH_REQUIRED_LLM_ROLES
apply_factory_bench_gates = _impl.apply_factory_bench_gates
brief_goal_overlap = _impl.brief_goal_overlap
build_bench_backend_audit_context = _impl.build_bench_backend_audit_context
build_director_repair_coverage_gap_summary = _impl.build_director_repair_coverage_gap_summary
build_factory_bench_gates = _impl.build_factory_bench_gates
build_requirements_doc = _impl.build_requirements_doc
configure_bench_backend = _impl.configure_bench_backend
discover_artifacts = _impl.discover_artifacts
grade_chain_state = _impl.grade_chain_state
load_projects = _impl.load_projects
load_workspace_validation_repair_coverage = _impl.load_workspace_validation_repair_coverage
main = _impl.main
map_factory_run_to_chain_results = _impl.map_factory_run_to_chain_results
project_final_request_refs = _impl.project_final_request_refs
read_chain_results = _impl.read_chain_results
read_chain_results_from_runtime_dirs = _impl.read_chain_results_from_runtime_dirs
read_factory_qa_invocation_status = _impl.read_factory_qa_invocation_status
required_llm_roles_for_factory_record = _impl.required_llm_roles_for_factory_record
resolve_runtime_dir_for_workspace = _impl.resolve_runtime_dir_for_workspace
resolve_runtime_dirs_for_workspace = _impl.resolve_runtime_dirs_for_workspace
run_chain = _impl.run_chain
run_factory_chain = _impl.run_factory_chain

# ---------------------------------------------------------------------------
# Private helpers re-exported for tests and historical importers (static names)
# ---------------------------------------------------------------------------
_logger = _impl._logger
_FACTORY_BENCH_DIR = _impl._FACTORY_BENCH_DIR
_MODULE_BACKEND_ROOT = _impl._MODULE_BACKEND_ROOT
_FIXTURE = _impl._FIXTURE
_BACKEND_ROOT = _impl._BACKEND_ROOT
_REPO_ROOT = _impl._REPO_ROOT
_LAUNCHER_INSTANCE_MODES = _impl._LAUNCHER_INSTANCE_MODES
_BENCH_SESSION_REPORTING_MODES = _impl._BENCH_SESSION_REPORTING_MODES
_sanitize_run_id = _impl._sanitize_run_id
_resolve_bench_work_dir = _impl._resolve_bench_work_dir
_bench_workspace_component = _impl._bench_workspace_component
_identity_workspace_component = _impl._identity_workspace_component
_workspace_physical_identity = _impl._workspace_physical_identity
_workspace_relative_components = _impl._workspace_relative_components
_workspace_catalog_hash = _impl._workspace_catalog_hash
_write_workspace_catalog_meta_exclusive = _impl._write_workspace_catalog_meta_exclusive
_read_workspace_catalog_meta_bound = _impl._read_workspace_catalog_meta_bound
_workspace_catalog_meta_matches = _impl._workspace_catalog_meta_matches
_require_workspace_catalog_meta = _impl._require_workspace_catalog_meta
_open_bench_directory_hierarchy = _impl._open_bench_directory_hierarchy
_allocate_fresh_project_workspace = _impl._allocate_fresh_project_workspace
_project_workspace_for_run = _impl._project_workspace_for_run
_load_json_object = _impl._load_json_object
_director_resume_plan_tasks = _impl._director_resume_plan_tasks
_director_resume_task_files = _impl._director_resume_task_files
_director_resume_task_payloads = _impl._director_resume_task_payloads
_director_resume_task_rows_mtime = _impl._director_resume_task_rows_mtime
_director_resume_has_taskboard = _impl._director_resume_has_taskboard
_director_resume_workspace_slug = _impl._director_resume_workspace_slug
_director_resume_source_task_dirs = _impl._director_resume_source_task_dirs
_director_resume_taskboard_score = _impl._director_resume_taskboard_score
_raise_director_resume_task_runtime_failure = _impl._raise_director_resume_task_runtime_failure
_rehydrate_director_resume_taskboard = _impl._rehydrate_director_resume_taskboard
_reset_current_director_resume_taskboard = _impl._reset_current_director_resume_taskboard
_director_resume_has_ce_blueprint = _impl._director_resume_has_ce_blueprint
_director_resume_snapshot_manifest = _impl._director_resume_snapshot_manifest
_director_resume_snapshot_ready = _impl._director_resume_snapshot_ready
_director_resume_declared_delivery_paths = _impl._director_resume_declared_delivery_paths
_director_resume_delivery_files = _impl._director_resume_delivery_files
_prepare_director_resume_workspace = _impl._prepare_director_resume_workspace
_attach_platform_residual_attribution = _impl._attach_platform_residual_attribution
_next_immutable_json_path = _impl._next_immutable_json_path
_write_immutable_json = _impl._write_immutable_json
_RUNTIME_PROJECT_BASES = _impl._RUNTIME_PROJECT_BASES
_RUNTIME_ARTIFACT_GLOBS = _impl._RUNTIME_ARTIFACT_GLOBS
_WORKSPACE_ARTIFACT_GLOBS = _impl._WORKSPACE_ARTIFACT_GLOBS
_resolve_catalog_path = _impl._resolve_catalog_path
_load_project_catalog = _impl._load_project_catalog
_level_local_project_aliases = _impl._level_local_project_aliases
_resolve_explicit_project_selection = _impl._resolve_explicit_project_selection
_safe_mtime = _impl._safe_mtime
_RUNTIME_WORKSPACE_EVIDENCE_RELATIVE_PATHS = _impl._RUNTIME_WORKSPACE_EVIDENCE_RELATIVE_PATHS
_file_mentions_workspace = _impl._file_mentions_workspace
_runtime_dir_matches_workspace = _impl._runtime_dir_matches_workspace
_is_valid_artifact_match = _impl._is_valid_artifact_match
_NON_TERMINAL_CHAIN_ERRORS = _impl._NON_TERMINAL_CHAIN_ERRORS
_chain_reached_terminal = _impl._chain_reached_terminal
_build_non_terminal_real_run_gate = _impl._build_non_terminal_real_run_gate
_non_terminal_chain_diagnostics = _impl._non_terminal_chain_diagnostics
_resolve_bench_cache_root = _impl._resolve_bench_cache_root
_BENCH_BACKEND = _impl._BENCH_BACKEND
_BENCH_OBSERVATION_CIRCUIT = _impl._BENCH_OBSERVATION_CIRCUIT
_bench_observation_disabled = _impl._bench_observation_disabled
_disable_bench_observation = _impl._disable_bench_observation
_emit_bench_event = _impl._emit_bench_event
_factory_role_from_phase = _impl._factory_role_from_phase
_emit_factory_phase_event = _impl._emit_factory_phase_event
_emit_factory_task_runtime_event = _impl._emit_factory_task_runtime_event
_DEFAULT_BACKEND_URL = _impl._DEFAULT_BACKEND_URL
_DEFAULT_LOCAL_BACKEND_TOKEN = _impl._DEFAULT_LOCAL_BACKEND_TOKEN
_BENCH_HTTP_TIMEOUT_S = _impl._BENCH_HTTP_TIMEOUT_S
_BENCH_OBSERVATION_HTTP_TIMEOUT_S = _impl._BENCH_OBSERVATION_HTTP_TIMEOUT_S
_resolve_polaris_home = _impl._resolve_polaris_home
_desktop_backend_info_path = _impl._desktop_backend_info_path
_read_desktop_backend_info = _impl._read_desktop_backend_info
_resolve_backend_url = _impl._resolve_backend_url
_desktop_backend_url_from_info = _impl._desktop_backend_url_from_info
_is_local_backend_url = _impl._is_local_backend_url
_resolve_backend_token = _impl._resolve_backend_token
_desktop_backend_token_from_info = _impl._desktop_backend_token_from_info
_http_post_json = _impl._http_post_json
_push_bench_session_to_backend = _impl._push_bench_session_to_backend
_ensure_bench_session = _impl._ensure_bench_session
_bench_record_counts = _impl._bench_record_counts
_push_bench_event_to_backend = _impl._push_bench_event_to_backend
_push_bench_complete_to_backend = _impl._push_bench_complete_to_backend
_push_bench_progress_to_backend = _impl._push_bench_progress_to_backend
_push_bench_workspace_to_backend = _impl._push_bench_workspace_to_backend
_register_bench_project_instance = _impl._register_bench_project_instance
_default_launcher_instance_mode = _impl._default_launcher_instance_mode
_default_bench_session_reporting_mode = _impl._default_bench_session_reporting_mode
_bench_session_backend_url = _impl._bench_session_backend_url
_bench_project_instance_id = _impl._bench_project_instance_id
_new_isolated_bench_launch_receipt = _impl._new_isolated_bench_launch_receipt
_validate_isolated_bench_launch = _impl._validate_isolated_bench_launch
_wait_backend_health = _impl._wait_backend_health
_start_isolated_bench_project_instance = _impl._start_isolated_bench_project_instance
_runtime_project_contamination = _impl._runtime_project_contamination
_bench_gate = _impl._bench_gate
_workspace_validation_artifact_candidates = _impl._workspace_validation_artifact_candidates
_workspace_validation_repair_coverage_reports = _impl._workspace_validation_repair_coverage_reports
_looks_like_repair_coverage_report = _impl._looks_like_repair_coverage_report
_collect_repair_coverage_reports = _impl._collect_repair_coverage_reports
_bench_repair_coverage_gap_payload = _impl._bench_repair_coverage_gap_payload
_url_port = _impl._url_port
_read_task_boundary_verdict_from_run_ledger_projection = _impl._read_task_boundary_verdict_from_run_ledger_projection
_build_language_runnable_contract = _impl._build_language_runnable_contract
_build_source_tree_contract = _impl._build_source_tree_contract
_build_feature_keywords_contract = _impl._build_feature_keywords_contract
_extract_feature_keywords = _impl._extract_feature_keywords
_fallback_audit_bundle_from_workspace = _impl._fallback_audit_bundle_from_workspace


if __name__ == "__main__":
    raise SystemExit(main())
