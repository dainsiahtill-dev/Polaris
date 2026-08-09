"""Authoritative registry of solidifiable platform modules.

Each module is a freeze boundary: when ``status=sealed``, its invariants and
pytest nodeids form a hard gate. Agents must not "soft-fix" sealed modules
without re-running the module gate and updating this registry.

Test pyramid for this registry:

1. **module** — single module full functional suite (no live LLM)
2. **cascade** — all sealed + hardening modules in dependency order
3. **bench** — isolated factory_bench true-run (four pillars)

Reference agent CLIs (Codex / long-horizon runners): observation and execution
are separate modules; observation failure must not kill execution before a
true wall-clock deadline. That invariant is sealed under ``M01_event_wait``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class PlatformModuleStatus(str, Enum):
    """Lifecycle of a solidifiable platform module."""

    SEALED = "sealed"
    HARDENING = "hardening"
    OPEN = "open"


@dataclass(frozen=True, slots=True)
class PlatformModuleRecord:
    """One freezeable platform module with gate metadata."""

    module_id: str
    name: str
    status: PlatformModuleStatus
    summary: str
    owner_paths: tuple[str, ...]
    pytest_targets: tuple[str, ...]
    invariants: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    sealed_by_defect: str = ""
    bench_required: bool = False
    notes: str = ""
    markers: tuple[str, ...] = field(default_factory=tuple)

    def marker_expression(self) -> str:
        """Return a pytest ``-m`` expression for this module, if markers exist."""

        if not self.markers:
            return ""
        if len(self.markers) == 1:
            return self.markers[0]
        return " or ".join(self.markers)


# Dependency order for cascade: observation → authority → tools → context → chain → bench.
MODULE_CASCADE_ORDER: tuple[str, ...] = (
    "M01_event_wait",
    "M02_physical_attempt_authority",
    "M03_tool_batch_deo",
    "M04_final_request_context",
    "M05_stage_lease_heartbeat",
    "M06_director_multi_task",
    "M07_factory_stage_chain",
    "M08_run_ledger_tool_lifecycle",
    "M10_materialization_semantic_quality",
    "M09_four_pillars_gates",
)


PLATFORM_MODULES: Mapping[str, PlatformModuleRecord] = {
    "M01_event_wait": PlatformModuleRecord(
        module_id="M01_event_wait",
        name="Event Wait / Runtime Observation",
        status=PlatformModuleStatus.HARDENING,
        summary=(
            "Factory-bench observation of factory runs via runtime.v2. "
            "Reconnect until wall-clock deadline; never cancel a healthy run "
            "on a single WS keepalive drop. Under Director load, HTTP GET must "
            "retry transport/timeouts and audit-bundle uses long observation budget."
        ),
        owner_paths=(
            "src/backend/scripts/factory_bench/factory_http_client.py",
            "src/backend/scripts/factory_bench/run_factory_bench.py",
        ),
        pytest_targets=(
            "src/backend/scripts/factory_bench/test_factory_http_client.py::TestEventWaitUntilTerminal",
            "src/backend/scripts/factory_bench/test_bench_to_backend_integration.py::"
            "TestFactoryRunsIntegration::test_run_factory_chain_event_wait_timeout_non_terminal",
        ),
        invariants=(
            "ws_keepalive_failure_reconnects_within_deadline",
            "connection_failed_surfaces_only_after_deadline",
            "cancel_reason_distinguishes_timeout_vs_connection_failed",
            "observation_failure_does_not_kill_execution_before_deadline",
            "http_get_retries_transport_timeout_under_director_load",
            "audit_bundle_uses_long_observation_timeout",
            "non_terminal_http_progress_updates_observation_cursor",
        ),
        sealed_by_defect="R153-EVENT-WAIT-RECONNECT-UNTIL-DEADLINE",
        markers=("module_event_wait",),
        notes=(
            "Sealed after r152 residual: keepalive cancel → authority_closed → TASK-3 no tools. "
            "R163: reopened to hardening — isolated backend GET timed out under Director load; "
            "transport retries + long audit-bundle timeout + non-terminal progress merge."
        ),
    ),
    "M02_physical_attempt_authority": PlatformModuleRecord(
        module_id="M02_physical_attempt_authority",
        name="Physical Attempt Authority",
        status=PlatformModuleStatus.HARDENING,
        summary=(
            "Factory-owned grant/reserve/start/terminal for provider attempts. "
            "Grant close only on stage drain or true cancel, never on observation glitch alone."
        ),
        owner_paths=(
            "src/backend/polaris/cells/factory/pipeline/internal/factory_physical_attempt_coordinator.py",
            "src/backend/polaris/cells/factory/pipeline/internal/factory_role_evidence_authority.py",
        ),
        pytest_targets=(
            "src/backend/polaris/cells/factory/pipeline/tests/test_factory_physical_attempt_control.py",
            "src/backend/polaris/cells/factory/pipeline/tests/test_factory_role_evidence_authority.py",
        ),
        invariants=(
            "unknown_source_tool_fail_closed",
            "closed_grant_rejects_reserve",
            "revoke_vs_closed_error_codes_distinct",
            "multi_task_director_shares_stage_budget_not_premature_close",
        ),
        depends_on=("M01_event_wait",),
        markers=("module_physical_attempt",),
    ),
    "M03_tool_batch_deo": PlatformModuleRecord(
        module_id="M03_tool_batch_deo",
        name="Tool Batch / DEO Serial Mutation",
        status=PlatformModuleStatus.SEALED,
        summary=(
            "Directed effect serial write batching. On serial mutation failure, "
            "abort remaining unclaimed siblings and emit ABORTED tool_result rows "
            "so lifecycle accounting is complete; prepare_batch seal/ready "
            "idempotent replay must not drop write batches. Path-scope evidence "
            "must canonicalize leading ``./`` so model-emitted ``./package.json`` "
            "matches CE/PM capability scope ``package.json``."
        ),
        owner_paths=(
            "src/backend/polaris/cells/roles/kernel/internal/tool_batch_runtime.py",
            "src/backend/polaris/cells/roles/kernel/internal/directed_effect_lifecycle.py",
            "src/backend/polaris/cells/control_plane/run_ledger/public/tool_lifecycle.py",
            "src/backend/polaris/cells/roles/adapters/internal/director/directed_effect_policy_snapshot.py",
            "src/backend/polaris/cells/roles/adapters/internal/director/execution_tools.py",
        ),
        pytest_targets=(
            "src/backend/polaris/cells/roles/kernel/tests/test_tool_batch_runtime.py::"
            "test_r151_plain_serial_write_failure_aborts_remaining_unclaimed_siblings",
            "src/backend/polaris/cells/roles/kernel/tests/test_directed_effect_lifecycle.py::"
            "test_r155_prepare_batch_idempotent_replay_after_ready_does_not_drop",
            "src/backend/polaris/cells/roles/kernel/tests/test_directed_effect_lifecycle.py::"
            "test_lifecycle_admits_second_turn_after_first_batch_receipts_close",
            "src/backend/polaris/cells/control_plane/run_ledger/tests/test_tool_lifecycle.py::"
            "test_r156_lifecycle_failure_reason_not_bare_dispatched_status",
            "src/backend/polaris/cells/roles/adapters/tests/"
            "test_director_directed_effect_policy_snapshot.py::"
            "test_r158_dot_slash_package_json_write_is_in_scope",
            "src/backend/polaris/cells/roles/adapters/tests/"
            "test_director_directed_effect_policy_snapshot.py::"
            "test_current_policy_capture_survives_unrelated_registry_alias_growth",
            "src/backend/polaris/cells/roles/adapters/tests/"
            "test_director_directed_effect_policy_snapshot.py::"
            "test_current_policy_capture_tolerates_agents_mtime_noise",
            "src/backend/polaris/cells/roles/adapters/tests/"
            "test_director_directed_effect_policy_snapshot.py::"
            "test_current_policy_capture_after_sibling_greenfield_creates",
            "src/backend/polaris/cells/roles/adapters/tests/"
            "test_director_directed_effect_policy_snapshot.py::"
            "test_r179_edit_blocks_is_allowed_write_tool_not_policy_denied",
            "src/backend/polaris/cells/roles/adapters/tests/"
            "test_director_directed_effect_policy_snapshot.py::"
            "test_r179_edit_blocks_unknown_write_tool_no_longer_hard_denied",
            "src/backend/polaris/cells/roles/adapters/tests/"
            "test_director_execution_tools.py::"
            "test_r179_edit_blocks_is_available_and_applies_search_replace",
        ),
        invariants=(
            "serial_write_failure_aborts_unclaimed_siblings",
            "sibling_abort_reason_stable",
            "no_silent_partial_batch_success",
            "prepare_batch_seal_ready_idempotent_replay_not_drop",
            "sibling_abort_emits_aborted_tool_results",
            "lifecycle_failure_reason_not_bare_dispatch_status",
            "dot_slash_relative_path_in_capability_scope",
            "post_claim_tool_spec_definition_stable_not_full_alias_map",
            "agents_policy_hash_content_stable_not_mtime",
            "post_claim_capture_survives_sibling_creates",
            "edit_blocks_is_deo_write_tool",
            "edit_blocks_physical_executor_applies_search_replace",
        ),
        sealed_by_defect="R151+R155+R156+R158+R174+R179-DEO-EDIT-BLOCKS",
        markers=("module_tool_batch_deo",),
        notes=(
            "R156: TASK-3 TOOL_RESULT_FAILED with partial effect receipts and "
            "failure_evidence.reason=dispatched; sibling abort now emits ABORTED results. "
            "R158 unfreeze: write_file path ./package.json false-denied as "
            "deo_path_scope_denied (raw vs resolve() relative mismatch); "
            "canonicalize leading ./ in policy snapshot path compare + capability scope. "
            "R174: post-claim capture false-denied deo_current_policy_evidence_unavailable "
            "on Nth serial write — tool_spec re-check required full alias-map "
            "snapshot_hash; agents_policy_hash embedded mtime. Fix: definition-only "
            "tool_spec re-verify + content-stable agents hash + named source logging. "
            "R179 unfreeze: preferred edit_blocks was absent from DEO private _WRITE_TOOLS "
            "and DirectorToolExecutor.available_tools → deo_director_policy_denied dropped "
            "entire batch (TOOL_RESULT_FAILED). Fix: admit edit_blocks/search_replace as "
            "write tools + physical SEARCH/REPLACE apply path."
        ),
    ),
    "M04_final_request_context": PlatformModuleRecord(
        module_id="M04_final_request_context",
        name="Final Request Context / current_user_final",
        status=PlatformModuleStatus.SEALED,
        summary=(
            "Provider final request must keep current_user as final non-system role. "
            "Sibling-export pins insert among leading systems, never trail as final system."
        ),
        owner_paths=(
            "src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py",
            "src/backend/polaris/cells/roles/kernel/internal/llm_caller/context_audit.py",
            "src/backend/polaris/kernelone/audit/context_os_prompt.py",
        ),
        pytest_targets=(
            "src/backend/polaris/cells/roles/kernel/tests/test_llm_caller_components.py::"
            "test_r152_sibling_export_pin_preserves_current_user_final_role",
            "src/backend/polaris/cells/roles/kernel/internal/llm_caller/tests/test_context_audit.py::"
            "test_r152_context_os_audit_finding_surfaces_current_user_final_failure",
        ),
        invariants=(
            "current_user_final_true_after_sibling_export_pin",
            "final_role_user_not_system",
            "context_os_audit_fails_closed_on_broken_final_role",
        ),
        sealed_by_defect="R152-SIBLING-EXPORT-PIN-CURRENT-USER-FINAL",
        depends_on=("M03_tool_batch_deo",),
        markers=("module_final_request_context",),
    ),
    "M05_stage_lease_heartbeat": PlatformModuleRecord(
        module_id="M05_stage_lease_heartbeat",
        name="Stage Lease Heartbeat",
        status=PlatformModuleStatus.HARDENING,
        summary="Stage heartbeats renew workspace run lease so long Director stages do not fence mid-task.",
        owner_paths=(
            "src/backend/polaris/cells/factory/pipeline/internal/factory_run_service.py",
            "src/backend/polaris/cells/factory/pipeline/internal/factory_run_admission.py",
        ),
        pytest_targets=("src/backend/polaris/cells/factory/pipeline/tests/test_factory_workspace_admission.py",),
        invariants=(
            "heartbeat_renews_active_stage_lease",
            "revalidate_without_renew_must_not_fence_live_stage",
        ),
        depends_on=("M02_physical_attempt_authority",),
        markers=("module_stage_lease",),
    ),
    "M06_director_multi_task": PlatformModuleRecord(
        module_id="M06_director_multi_task",
        name="Director Multi-Task Fanout",
        status=PlatformModuleStatus.HARDENING,
        summary=(
            "Director executes all CE tasks under one stage without premature authority "
            "close between tasks. On multi-task timeout/incomplete boundary, end-of-stage "
            "materialization quality settle still runs so smoke/tsc repairs land (R165/R174)."
        ),
        owner_paths=(
            "src/backend/polaris/cells/factory/pipeline/internal/factory_stage_executor.py",
            "src/backend/polaris/cells/runtime/task_runtime/internal/service.py",
            "src/backend/polaris/cells/roles/kernel/public/deferred_repair_commit_service.py",
        ),
        pytest_targets=(
            "src/backend/polaris/cells/factory/pipeline/tests/test_director_binding_fanout.py",
            "src/backend/polaris/cells/factory/pipeline/tests/test_director_fanout_evidence.py",
            "src/backend/polaris/cells/factory/pipeline/tests/test_director_stage_materialization_settle.py",
            "src/backend/polaris/cells/roles/kernel/tests/test_deferred_repair_commit_service.py",
        ),
        invariants=(
            "all_tasks_get_authority_binding_or_explicit_fail",
            "task_n_failure_does_not_erase_task_1_n_minus_1_receipts",
            "director_stage_materialization_settle_on_timeout_or_incomplete",
            "cancelled_director_stage_skips_materialization_settle",
            "deferred_settle_partitions_non_conflicting_target_paths",
        ),
        depends_on=("M02_physical_attempt_authority", "M04_final_request_context", "M03_tool_batch_deo"),
        markers=("module_director_fanout",),
        notes=(
            "R165: L1-01 multi-task timeout left package.json+src without tests/; "
            "quality_gate never ran. End-of-director_dispatch settle invokes "
            "run_director_materialization_quality_repair_schedule before stage exit. "
            "R174: settle planned 5 deferred repairs (tsc + json_as_source smoke) but "
            "synthesize_batch fail-all deo_deferred_repair_target_conflict on shared "
            "src/main.ts — committed=0, tests/ never wrote. Partition deferred commits "
            "into non-overlapping forward-path waves so smoke tests still land. "
            "R181: real_run green while director_dispatch failed on "
            "canonical_task_boundary_missing / task_runtime_not_converged with stale "
            "downstream_pending. Disk reconcile + completed_verified supersedes "
            "non-completed runtime rows; multi-pass post-settle recovery re-evaluates "
            "authority. Residual attribution maps this class to M06, not M10."
        ),
    ),
    "M07_factory_stage_chain": PlatformModuleRecord(
        module_id="M07_factory_stage_chain",
        name="Factory Stage Chain PM→CE→Director→QA",
        status=PlatformModuleStatus.HARDENING,
        summary="Canonical product chain is PM → Chief Engineer → Director → QA; no PM→Director shortcut.",
        owner_paths=(
            "src/backend/polaris/cells/factory/pipeline/internal/factory_stage_executor.py",
            "src/backend/polaris/cells/factory/pipeline/internal/factory_run_service.py",
            "src/backend/polaris/kernelone/platform_modules/residual_attribution.py",
        ),
        pytest_targets=(
            "src/backend/polaris/cells/factory/pipeline/tests/test_factory_execution_control_plane_ssot.py",
            "src/backend/polaris/kernelone/platform_modules/tests/test_residual_attribution.py",
            "src/backend/polaris/kernelone/platform_modules/tests/test_registry.py",
        ),
        invariants=(
            "chain_is_pm_ce_director_only",
            "missing_ce_blocks_director",
            "stage_persistence_commits_before_next_stage",
            "residual_maps_to_exactly_one_module_id",
            "residual_attribution_is_non_terminal_and_workflow_owner_schedules",
        ),
        depends_on=("M05_stage_lease_heartbeat", "M06_director_multi_task"),
        markers=("module_factory_chain",),
        notes=(
            "KernelOne owns generic one-residual-one-module attribution only; "
            "workflow_runtime owns project scheduling and terminal policy."
        ),
    ),
    "M08_run_ledger_tool_lifecycle": PlatformModuleRecord(
        module_id="M08_run_ledger_tool_lifecycle",
        name="Run Ledger / Tool Lifecycle",
        status=PlatformModuleStatus.HARDENING,
        summary="Tool lifecycle missing vs failed is distinct; ledger never projects failed as missing.",
        owner_paths=(
            "src/backend/polaris/cells/control_plane/run_ledger/public/tool_lifecycle.py",
            "src/backend/polaris/cells/events/fact_stream/public/service.py",
        ),
        pytest_targets=("src/backend/polaris/cells/control_plane/run_ledger/tests/test_tool_lifecycle.py",),
        invariants=(
            "missing_vs_failed_modalities_distinct",
            "tool_lifecycle_required_when_task_claims_tools",
        ),
        depends_on=("M03_tool_batch_deo", "M06_director_multi_task"),
        markers=("module_run_ledger",),
    ),
    "M10_materialization_semantic_quality": PlatformModuleRecord(
        module_id="M10_materialization_semantic_quality",
        name="Materialization Semantic Quality",
        status=PlatformModuleStatus.HARDENING,
        summary=(
            "Director post-materialization semantic quality. Comment-only placeholder "
            "prose must not fail; truncated HTML entrypoints and TypeScript module "
            "scripts must be deterministically closed/rewritten before LLM repair. "
            "Package-manifest JSON written into .ts paths (R159) is rewritten and "
            "missing package.json test targets get a minimal Node smoke test."
        ),
        owner_paths=(
            "src/backend/polaris/cells/roles/adapters/internal/director/helpers.py",
            "src/backend/polaris/cells/roles/adapters/internal/director/execution.py",
            "src/backend/polaris/cells/roles/adapters/internal/director/quality_gate.py",
            "src/backend/polaris/cells/director/runtime/internal/repair_kernel/typescript_syntax/",
            "src/backend/polaris/cells/director/runtime/internal/repair_kernel/generic_hygiene_syntax.py",
            "src/backend/polaris/cells/director/runtime/internal/repair_kernel/registry.py",
            "src/backend/polaris/cells/director/runtime/internal/repair_kernel/schedule_catalog.py",
        ),
        pytest_targets=(
            "src/backend/polaris/cells/roles/adapters/tests/test_director_helpers_pure.py::TestR154PlaceholderCommentGuard",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_html_module_script_rewrites_dot_slash_src_typescript_to_dist",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_html_truncated_entrypoint_closes_script_and_html_and_rewrites_ts_script",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_html_module_script_rewrites_typescript_source_entrypoint",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_typescript_duplicate_function_removes_later_stub",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_typescript_readonly_assignment_mutates_readonly_array_fields",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_typescript_json_as_source_rewrites_package_manifest_and_adds_smoke_test",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_typescript_json_as_source_seeds_vitest_smoke_for_bare_test_script",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_typescript_member_alias_rewrites_position_glow_and_garden_tick",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_typescript_literal_union_expand_adds_missing_literals",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_typescript_init_property_alias_renames_garden_init_keys",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_typescript_arg_type_function_alias_rewrites_humidity_to_hydration",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_typescript_import_type_value_conflict_drops_type_flower_keeps_value",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_typescript_import_type_value_conflict_promotes_type_only_import",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_runtime_dependency_repair_plans_node_types_dev_dependency",
            "src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract.py::"
            "test_public_runtime_dependency_repair_tsconfig_types_when_atypes_node_already_declared",
            "src/backend/polaris/cells/director/runtime/tests/test_typescript_m10_strict_compile_repairs.py",
        ),
        invariants=(
            "comment_only_placeholder_not_low_quality",
            "executable_placeholder_identifier_still_flags",
            "html_attribute_placeholder_allowed",
            "truncated_html_entrypoint_covered_by_deterministic_plan",
            "html_ts_module_script_src_maps_to_compiled_dist",
            "duplicate_function_declaration_removed_for_tsc",
            "readonly_array_index_assignment_mutability_repaired",
            "package_manifest_json_in_ts_path_rewritten",
            "missing_package_test_target_gets_smoke_test",
            "bare_vitest_run_seeds_tests_verify_smoke",
            "member_alias_position_glow_and_garden_tick",
            "ts7010_interface_method_void_return",
            "ts2322_object_freeze_named_type_assertion",
            "ts2339_readonly_array_push_binding",
            "ts2339_param_object_property_retype",
            "literal_union_expand_for_missing_string_literals",
            "init_property_alias_garden_init_keys",
            "arg_type_function_alias_humidity_to_hydration",
            "import_type_value_conflict_ts2300_ts1361",
            "ts2580_process_adds_atypes_node_and_tsconfig_types",
            "ts2580_existing_atypes_node_still_sets_tsconfig_types",
        ),
        depends_on=("M03_tool_batch_deo", "M04_final_request_context", "M06_director_multi_task"),
        sealed_by_defect="",
        markers=("module_materialization_semantic_quality",),
        notes=(
            "R154 placeholder comment. R155 truncated HTML. R157: verify.ts import.meta + "
            "duplicate runVerification + ReadonlyArray index writes blocked four-pillar build. "
            "R161: bare vitest run smoke + FlowerState↔HumidityState arg-type function alias. "
            "R164: type+value import conflict (TS2300/TS1361) + unused import + missing export + smoke. "
            "R178: TS2580 process when @types/node already declared but tsconfig lacks types:node."
        ),
    ),
    "M09_four_pillars_gates": PlatformModuleRecord(
        module_id="M09_four_pillars_gates",
        name="Four Pillars Bench Gates",
        status=PlatformModuleStatus.OPEN,
        summary=(
            "Measure-only gates: code on disk, env/deps, real build/test/lint, real CLI/Web/API. "
            "Never repair target projects from gates."
        ),
        owner_paths=(
            "src/backend/polaris/cells/factory/pipeline/internal/bench_gates.py",
            "src/backend/scripts/factory_bench/run_factory_bench.py",
        ),
        pytest_targets=("src/backend/polaris/cells/factory/pipeline/tests/test_bench_gates.py",),
        invariants=(
            "gates_are_measure_only_no_workspace_repair",
            "four_pillars_all_required_for_pass",
            "event_wait_taxonomy_prefers_runtime_environment_when_causal",
        ),
        depends_on=("M07_factory_stage_chain", "M08_run_ledger_tool_lifecycle", "M01_event_wait"),
        bench_required=True,
        markers=("module_four_pillars",),
    ),
}


def get_module(module_id: str) -> PlatformModuleRecord:
    """Return one module or raise KeyError."""

    key = str(module_id or "").strip()
    if key not in PLATFORM_MODULES:
        raise KeyError(f"unknown_platform_module:{key}")
    return PLATFORM_MODULES[key]


def list_modules() -> tuple[PlatformModuleRecord, ...]:
    """Return modules in cascade order (unknowns appended)."""

    ordered: list[PlatformModuleRecord] = []
    seen: set[str] = set()
    for module_id in MODULE_CASCADE_ORDER:
        if module_id in PLATFORM_MODULES:
            ordered.append(PLATFORM_MODULES[module_id])
            seen.add(module_id)
    for module_id, record in PLATFORM_MODULES.items():
        if module_id not in seen:
            ordered.append(record)
    return tuple(ordered)


def modules_by_status(status: PlatformModuleStatus) -> tuple[PlatformModuleRecord, ...]:
    """Filter modules by lifecycle status, preserving cascade order."""

    return tuple(module for module in list_modules() if module.status is status)
