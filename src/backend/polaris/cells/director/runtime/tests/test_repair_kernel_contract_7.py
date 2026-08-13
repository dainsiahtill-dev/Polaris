"""Tests for the Director Runtime Repair Kernel contracts."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from polaris.cells.control_plane.run_ledger.public import TaskBoundaryFailureClassV1
from polaris.cells.director.runtime.internal.repair_kernel import (
    PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL,
    PatchComposer,
    RepairAdvisorNote,
    RepairArchetype,
    RepairConvergenceScheduler,
    RepairDiagnostic,
    RepairOperation,
    RepairPlan,
    RepairPolicyContext,
    RepairPolicyGate,
    RepairReceipt,
    RepairRevalidationEvidence,
    RepairRuleDefinition,
    RepairRuleRegistry,
    RepairVerifierSnapshot,
    TransactionalRepairExecutor,
    build_cpp_failing_smoke_translation_unit_plan,
    build_cpp_include_path_plan,
    build_cpp_missing_private_members_plan,
    build_cpp_placeholder_declaration_plan,
    build_cpp_post_plan,
    build_cpp_standard_include_plan,
    build_cpp_struct_getter_field_access_plan,
    build_go_bare_import_string_plan,
    build_go_bare_local_import_plan,
    build_go_error_string_helper_plan,
    build_go_nested_import_plan,
    build_go_subpath_import_plan,
    build_go_unused_import_plan,
    build_java_accessor_alias_plan,
    build_patch_residue_cleanup_plan,
    build_python_readme_required_token_plan,
    build_repair_coverage_report,
    build_repair_receipt_context,
    build_rust_dependency_plan,
    build_rust_missing_binary_entrypoint_plan,
    build_typescript_canvas_scale_return_type_plan,
    build_typescript_duplicate_object_property_plan,
    build_typescript_enum_member_separator_plan,
    build_typescript_hyphenated_identifier_plan,
    build_typescript_missing_closing_brace_plan,
    build_typescript_nullable_canvas_context_plan,
    build_typescript_number_property_call_plan,
    build_typescript_number_to_string_argument_plan,
    build_typescript_object_literal_comma_plan,
    build_typescript_readonly_assignment_plan,
    build_typescript_shorthand_property_scope_plan,
    build_typescript_string_literal_suggestion_plan,
    default_repair_rule_registry,
    deterministic_repair_source_tool_known,
    javascript_syntax as js_syntax,
    normalize_artifact_quality_errors,
    order_repair_plans,
    plan_runtime_repair,
    plan_typescript_canvas_scale_return_type_repair,
    plan_typescript_duplicate_object_property_repair,
    plan_typescript_enum_member_separator_repair,
    plan_typescript_nullable_canvas_context_repair,
    plan_typescript_object_literal_comma_repair,
    remove_patch_residue_lines,
    repair_cpp_failing_smoke_translation_unit_text,
    repair_cpp_include_paths_text,
    repair_cpp_invalid_placeholder_declarations_text,
    repair_cpp_missing_private_members_text,
    repair_cpp_missing_standard_includes_text,
    repair_cpp_struct_getter_field_access_text,
    repair_go_bare_import_strings_text,
    repair_go_nested_import_keywords_text,
    repair_java_common_accessor_aliases_text,
    repair_typescript_missing_closing_braces,
    repair_typescript_nullable_canvas_context_guards,
    repair_typescript_object_literal_commas,
    run_materialization_quality_repair_schedule_callbacks,
    run_post_execution_repair_schedule_callbacks,
    run_runtime_repair,
    runtime_dispatch as runtime_dispatch_module,
    runtime_repair_bindings,
    runtime_repair_source_tools,
    typescript_syntax as ts_syntax,
)
from polaris.cells.director.runtime.internal.repair_kernel.advisory_policy import (
    FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS,
    FORBIDDEN_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS,
)
from polaris.cells.director.runtime.internal.repair_kernel.contracts import (
    FILE_ABSENT_HASH,
    ComposedPatch,
    CompositionResult,
    sha256_text,
)
from polaris.cells.director.runtime.internal.repair_kernel.generic_hygiene_syntax import (
    SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL,
    build_scaffold_marker_cleanup_plan,
)
from polaris.cells.director.runtime.internal.repair_kernel.java_syntax import (
    build_java_eof_truncation_plan,
    build_java_test_dependency_plan,
    repair_java_eof_truncation_text,
)
from polaris.cells.director.runtime.internal.repair_kernel.rust_syntax import (
    build_rust_crate_import_rewrite_plan,
    build_rust_field_rename_suggestion_plan,
    build_rust_incompatible_copy_derive_plan,
    build_rust_line_suggestion_plan,
    build_rust_method_self_signature_plan,
    build_rust_missing_trait_derive_plan,
    build_rust_serde_derive_plan,
    build_rust_trait_import_plan,
    build_rust_unresolved_pub_use_plan,
    build_rust_unused_import_plan,
    build_rust_wrong_crate_path_plan,
)
from polaris.cells.director.runtime.public import (
    AttachDirectorRepairRevalidationEvidenceV1,
    CompareDirectorRepairShadowRunV1,
    DirectorRepairAdvisoryPolicyResultV1,
    DirectorRepairAdvisoryValidationResultV1,
    DirectorRepairCoverageReportV1,
    DirectorRepairEffectPlanV1,
    DirectorRepairEffectV1,
    DirectorRepairKernelSummaryProjectionResultV1,
    DirectorRepairLanguageSlotsResultV1,
    DirectorRepairLanguageSlotV1,
    DirectorRepairMaterializationAllowedPathsResultV1,
    DirectorRepairMaterializationPlanProbeResultV1,
    DirectorRepairMaterializationQualityFacadeResultV1,
    DirectorRepairMaterializationQualityScheduleResultV1,
    DirectorRepairMetricsResultV1,
    DirectorRepairPlanningResultV1,
    DirectorRepairPostExecutionScheduleResultV1,
    DirectorRepairRevalidationProjectionResultV1,
    DirectorRepairShadowComparisonResultV1,
    EvaluateDirectorRepairCutoverReadinessV1,
    PlanDirectorRepairCommandV1,
    ProjectDirectorRepairKernelSummaryV1,
    ProjectDirectorRepairMaterializationBridgeMetadataV1,
    ProjectDirectorRepairMetricsV1,
    QueryDirectorRepairAdvisoryPolicyV1,
    QueryDirectorRepairAdvisoryValidationV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairLanguageSlotsV1,
    QueryDirectorRepairMaterializationAllowedPathsV1,
    QueryDirectorRepairMaterializationPlanProbeV1,
    QueryDirectorRepairMaterializationQualityScheduleV1,
    QueryDirectorRepairPlanProbeV1,
    QueryDirectorRepairPostExecutionScheduleV1,
    QueryDirectorRepairStrategyCatalogV1,
    RepairAdvisoryV1,
    RepairDiagnosticV1,
    RepairReceiptV1,
    RunDirectorRepairCommandV1,
    attach_director_repair_revalidation_evidence,
    build_director_repair_kernel_summary,
    compare_director_repair_shadow_run,
    evaluate_director_repair_cutover_readiness,
    hash_director_repair_effect_plan,
    normalize_director_repair_issue_diagnostics,
    plan_director_repair,
    project_director_repair_kernel_summary,
    project_director_repair_materialization_bridge_metadata,
    project_director_repair_metrics,
    project_director_repair_revalidation_evidence,
    query_director_repair_advisory_policy,
    query_director_repair_coverage,
    query_director_repair_language_slots,
    query_director_repair_materialization_allowed_paths,
    query_director_repair_materialization_plan_probe,
    query_director_repair_materialization_quality_schedule,
    query_director_repair_plan_probe,
    query_director_repair_post_execution_schedule,
    query_director_repair_strategy_catalog,
    run_director_materialization_quality_repair_facade,
    run_director_repair,
    service as runtime_public_service,
    validate_director_repair_advisory,
)
from polaris.cells.director.runtime.public.directed_effect_contracts import hash_directed_effect_arguments
from polaris.cells.director.runtime.public.service import normalize_director_repair_diagnostics
from polaris.kernelone.quality import artifact_quality_issues_from_errors
from polaris.kernelone.tools.tool_kinds import DEPRECATED_WRITE_TOOLS




def test_runtime_materialization_quality_schedule_runs_callbacks_and_injects_step_metadata() -> None:
    observed_step_ids: list[str] = []
    expected_step_ids = [
        "materialization.hygiene_scaffold",
        "materialization.typescript_scaffold",
        "materialization.typescript_compiler",
        "materialization.html_entrypoint",
        "materialization.node_manifest",
        "materialization.rust_compiler",
        "materialization.target_runtime",
        "materialization.python_import",
        "materialization.go_import",
    ]

    def runner(step) -> list[dict[str, object]]:
        observed_step_ids.append(step.step_id)
        return [
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "source_tool": "deterministic_typescript_missing_export_repair",
                    "file": "src/main.ts",
                },
            }
        ]

    run = run_materialization_quality_repair_schedule_callbacks(
        runner_step_ids=tuple(expected_step_ids),
        runner=runner,
    )

    assert observed_step_ids == expected_step_ids
    assert [step.step_id for step in run.ordered_steps] == observed_step_ids
    assert len(run.tool_results) == len(expected_step_ids)
    payload = run.tool_results[0]["result"]
    assert payload["bridge_step_id"] == "materialization.hygiene_scaffold"
    assert payload["language"] == "multi"
    assert payload["phase"] == "hygiene"
    assert payload["priority"] == 0
    assert payload["scheduled_source_tool"] == "deterministic_materialization_hygiene_repair"
    assert payload["scheduled_source_tool_kind"] == "callback_schedule_label"
    assert payload["scheduled_source_tool_executable_runtime"] is False
    assert payload["schedule_source_tool_kind"] == "callback_schedule_label"
    assert payload["schedule_source_tool_is_runtime_executable"] is False
    assert payload["round_number"] == 1
    assert payload["scheduler_round_number"] == 1
    assert payload["convergence_status"] == "max_rounds_reached"
    assert run.tool_results[-1]["result"]["bridge_step_id"] == "materialization.go_import"
    assert run.tool_results[-1]["result"]["depends_on"] == ["materialization.python_import"]

    with pytest.raises(RuntimeError, match="runner is not declared"):
        run_materialization_quality_repair_schedule_callbacks(
            runner_step_ids=(*expected_step_ids, "python.unregistered"),
            runner=runner,
        )


def test_repair_language_slot_inference_avoids_common_reserved_language_false_positives() -> None:
    diagnostics = [
        RepairDiagnostic(
            source="artifact_quality",
            code="generic_error",
            message="A javascript transpiler message should remain unclassified without a known source.",
            raw="javascript transpiler message",
        ),
        RepairDiagnostic(
            source="artifact_quality",
            code="generic_error",
            message="Generic syntax error.",
            path="analysis/sim.m",
            raw="Generic syntax error in analysis/sim.m",
        ),
        RepairDiagnostic(
            source="artifact_quality",
            code="generic_error",
            message="MATLAB failed while loading helper code.",
            path="analysis/sim.m",
            raw="MATLAB failed while loading helper code.",
        ),
        RepairDiagnostic(
            source="artifact_quality",
            code="generic_error",
            message="clang reports an Objective-C import failure.",
            path="src/AppDelegate.m",
            raw="clang reports an Objective-C import failure.",
        ),
        RepairDiagnostic(
            source="artifact_quality",
            code="generic_error",
            message="svelte-check reported an unresolved component import.",
            path="src/App.svelte",
            raw="svelte-check reported an unresolved component import.",
        ),
    ]

    payload = default_repair_rule_registry().coverage(diagnostics).to_dict()

    assert payload["items"][0]["diagnostic_language"] == "unknown"
    assert payload["items"][1]["diagnostic_language"] == "unknown"
    assert payload["items"][2]["diagnostic_language"] == "matlab"
    assert payload["items"][3]["diagnostic_language"] == "objective_c"
    assert payload["items"][4]["diagnostic_language"] == "svelte"


def test_public_strategy_catalog_is_read_only_and_non_agi_authoritative() -> None:
    result = query_director_repair_strategy_catalog(QueryDirectorRepairStrategyCatalogV1(max_items=3))
    payload = result.to_dict()

    assert payload["source"] == "director.runtime.repair_kernel.strategy_catalog"
    assert payload["access"] == "read_only"
    assert payload["agi_execution_authority"] is False
    assert payload["director_tool_execution_required"] is True
    assert payload["owner_cell"] == "director.runtime"
    assert len(payload["items"]) == 3
    assert payload["items"][0]["implementation_status"] == "executable_runtime"
    assert payload["items"][0]["execution_owner"] == "director.runtime"
    assert payload["items"][0]["bench_driven_migration_required"] is False
    expected_runtime_source_tools = list(runtime_repair_source_tools())
    expected_runtime_by_language: dict[str, int] = {}
    for binding in runtime_repair_bindings():
        language = str(binding["language"])
        expected_runtime_by_language[language] = expected_runtime_by_language.get(language, 0) + 1
    baseline_source_tools = payload["summary"]["adapter_strategy_host_source_tools"]
    summary_failure_message = (
        "expected public strategy catalog ledger to have no adapter_strategy_host source_tools; "
        f"observed implementation_status_counts={payload['summary']['implementation_status_counts']}; "
        "adapter_strategy_host_source_tools:\n- "
        + "\n- ".join(str(source_tool) for source_tool in baseline_source_tools)
    )
    assert payload["summary"]["executable_runtime_binding_count"] == len(expected_runtime_source_tools)
    assert payload["summary"]["executable_runtime_source_tools"] == expected_runtime_source_tools
    assert "deterministic_rust_post_repair" not in payload["summary"]["executable_runtime_source_tools"]
    assert "deterministic_rust_derive_repair" in payload["summary"]["executable_runtime_source_tools"]
    assert payload["summary"]["executable_runtime_by_language"] == expected_runtime_by_language
    executable_status_count = payload["summary"]["implementation_status_counts"].get("executable_runtime", 0)
    metadata_status_count = payload["summary"]["implementation_status_counts"].get("metadata_rule_registered", 0)
    assert executable_status_count <= payload["summary"]["executable_runtime_binding_count"]
    assert payload["summary"]["implementation_status_counts"].get("adapter_strategy_host", 0) == 0, (
        summary_failure_message
    )
    assert payload["summary"]["adapter_strategy_host_count"] == 0, summary_failure_message
    assert baseline_source_tools == [], summary_failure_message
    assert payload["summary"]["adapter_strategy_host_count"] == (
        payload["summary"]["total"] - executable_status_count - metadata_status_count
    )
    assert payload["summary"]["bench_driven_migration_required"] is False, summary_failure_message
    assert payload["summary"]["adapter_strategy_host_owner"] == (
        "roles.adapters.internal.director.deterministic_repairs"
    )
    assert payload["summary"]["migration_target_owner"] == "director.runtime.repair_kernel"
    assert "deterministic_typescript_missing_export_repair" not in baseline_source_tools
    assert "deterministic_typescript_return_object_semicolon_repair" not in baseline_source_tools
    assert payload["summary"]["executable_runtime_bindings"][0] == {
        "source_tool": "deterministic_cpp_include_path_repair",
        "language": "cpp",
        "rule_id": "cpp.include_path",
    }


def test_public_strategy_catalog_and_language_slots_keep_status_ledger_counts_explicit() -> None:
    catalog_payload = query_director_repair_strategy_catalog(
        QueryDirectorRepairStrategyCatalogV1(include_items=True, max_items=10_000)
    ).to_dict()
    slots_payload = query_director_repair_language_slots(
        QueryDirectorRepairLanguageSlotsV1(include_items=True)
    ).to_dict()
    catalog_summary = catalog_payload["summary"]
    slot_summary = slots_payload["summary"]
    baseline_source_tools = [str(source_tool) for source_tool in catalog_summary["adapter_strategy_host_source_tools"]]
    legacy_typescript_source_tools = [
        source_tool
        for source_tool in baseline_source_tools
        if source_tool.startswith(("deterministic_typescript", "deterministic_html_typescript"))
        or source_tool.startswith("deterministic_typeorm")
        or source_tool == "deterministic_javascript_typescript_annotation_repair"
    ]
    legacy_typescript_failure_message = (
        "TypeScript migration source_tools must not be in adapter_strategy_host_source_tools:\n- "
        + "\n- ".join(legacy_typescript_source_tools)
    )
    expected_runtime_source_tools = list(runtime_repair_source_tools())
    expected_status_counts: dict[str, int] = {}
    for item in catalog_payload["items"]:
        status = str(item["implementation_status"])
        expected_status_counts[status] = expected_status_counts.get(status, 0) + 1
    catalog_failure_message = (
        "expected public strategy catalog ledger to match runtime binding facts "
        "and contain no adapter_strategy_host source_tools; "
        f"observed implementation_status_counts={catalog_summary['implementation_status_counts']}; "
        "adapter_strategy_host_source_tools:\n- " + "\n- ".join(baseline_source_tools)
    )

    assert catalog_summary["total"] == len(catalog_payload["items"])
    assert legacy_typescript_source_tools == [], legacy_typescript_failure_message
    assert baseline_source_tools == [], catalog_failure_message
    assert catalog_summary["implementation_status_counts"] == expected_status_counts, catalog_failure_message
    assert catalog_summary["implementation_status_counts"].get("executable_runtime", 0) == len(
        expected_runtime_source_tools
    ), catalog_failure_message
    assert catalog_summary["implementation_status_counts"].get("adapter_strategy_host", 0) == 0, catalog_failure_message
    assert catalog_summary["executable_runtime_binding_count"] == len(expected_runtime_source_tools), (
        catalog_failure_message
    )
    assert catalog_summary["adapter_strategy_host_count"] == 0, catalog_failure_message
    assert catalog_summary["executable_runtime_source_tools"] == expected_runtime_source_tools, catalog_failure_message
    assert set(catalog_summary["implementation_status_counts"]).issubset(
        {"executable_runtime", "metadata_rule_registered", "adapter_strategy_host"}
    )
    assert "reserved_only" not in catalog_summary["implementation_status_counts"]

    assert slot_summary["language_count"] == 54
    assert slot_summary["implementation_status_counts"] == {
        "executable_runtime": 8,
        "reserved_only": 46,
    }
    assert slot_summary["executable_runtime_language_count"] == 8
    assert slot_summary["reserved_only_language_count"] == 46
    assert "adapter_strategy_host" not in slot_summary["implementation_status_counts"]
    assert set(slot_summary["executable_runtime_languages"]) == {
        "cpp",
        "go",
        "html",
        "java",
        "javascript",
        "python",
        "rust",
        "typescript",
    }
    assert all(
        item["implementation_status"] in {"executable_runtime", "metadata_rule_registered", "reserved_only"}
        for item in slots_payload["items"]
    )
