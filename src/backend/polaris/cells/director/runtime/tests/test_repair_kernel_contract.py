"""Tests for the Director Runtime Repair Kernel contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
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
from polaris.cells.director.runtime.internal.repair_kernel.contracts import FILE_ABSENT_HASH, sha256_text
from polaris.cells.director.runtime.internal.repair_kernel.generic_hygiene_syntax import (
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
    RepairReceiptV1,
    RunDirectorRepairCommandV1,
    attach_director_repair_revalidation_evidence,
    build_director_repair_kernel_summary,
    compare_director_repair_shadow_run,
    evaluate_director_repair_cutover_readiness,
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
    validate_director_repair_advisory,
)


def _install_delete_file_test_runtime_binding(monkeypatch: pytest.MonkeyPatch, source_tool: str) -> None:
    def planner(
        base_files: dict[str, str],
        artifact_quality_errors: tuple[str, ...],
        advisor_notes: tuple[RepairAdvisorNote, ...] | None,
        mode: str,
    ) -> runtime_dispatch_module.RuntimeRepairPlanning:
        diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
        relative_path, original = next(iter(base_files.items()))
        plan = RepairPlan(
            rule_id="test.delete_file",
            source_tool=source_tool,
            operations=(
                RepairOperation(
                    kind="delete_file",
                    path=relative_path,
                    before_hash=sha256_text(original),
                ),
            ),
            diagnostics=diagnostics,
            mode=mode,
            advisor_notes=tuple(advisor_notes or ()),
        )
        composition = PatchComposer().compose(base_files, plan.operations)
        return runtime_dispatch_module.RuntimeRepairPlanning(
            source_tool=source_tool,
            diagnostics=diagnostics,
            plan=plan,
            composition=composition,
            advisor_notes=tuple(advisor_notes or ()),
        )

    def runner(
        workspace: str | Path,
        base_files: dict[str, str],
        artifact_quality_errors: tuple[str, ...],
        writer: object,
        editor: object | None,
        deleter: object | None,
        allowed_paths: tuple[str, ...] | None,
        advisor_notes: tuple[RepairAdvisorNote, ...] | None,
        mode: str,
    ) -> runtime_dispatch_module.RuntimeRepairRun:
        del editor
        planning = planner(base_files, artifact_quality_errors, advisor_notes, mode)
        assert planning.plan is not None
        assert planning.composition is not None
        policy = RepairPolicyGate()
        policy_context = RepairPolicyContext(allowed_paths=tuple(allowed_paths or base_files.keys()))
        plan_decision = policy.evaluate_plan(planning.plan, policy_context)
        composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
        execution_result = TransactionalRepairExecutor().execute(
            workspace=Path(workspace),
            plan=planning.plan,
            composition=planning.composition,
            writer=writer,  # type: ignore[arg-type]
            deleter=deleter,  # type: ignore[arg-type]
        )
        return runtime_dispatch_module.RuntimeRepairRun(
            planning=planning,
            ok=execution_result.ok,
            execution_result=execution_result,
            plan_decision=plan_decision,
            composition_decision=composition_decision,
            error_code=None if execution_result.ok else "repair_execution_failed",
            error_message=None if execution_result.ok else execution_result.error,
        )

    bindings = dict(runtime_dispatch_module._RUNTIME_REPAIR_BINDINGS)
    bindings[source_tool] = runtime_dispatch_module.RuntimeRepairBinding(
        source_tool=source_tool,
        language="test",
        rule_id="test.delete_file",
        planner=planner,  # type: ignore[arg-type]
        runner=runner,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(runtime_dispatch_module, "_RUNTIME_REPAIR_BINDINGS", bindings)


def test_normalizer_builds_typed_typescript_diagnostic() -> None:
    diagnostics = normalize_artifact_quality_errors(["src/app.ts(3,14): error TS2304: Cannot find name 'Widget'."])

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.source == "artifact_quality"
    assert diagnostic.code == "typescript_ts2304"
    assert diagnostic.path == "src/app.ts"
    assert diagnostic.line == 3
    assert diagnostic.column == 14


def test_normalizer_builds_typed_typescript_return_object_semicolon_diagnostic() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "Artifact quality scan failed: TypeScript return object contains "
            "semicolon-terminated property in src/models/task.ts"
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.source == "artifact_quality"
    assert diagnostic.code == "typescript_return_object_property_semicolon"
    assert diagnostic.path == "src/models/task.ts"
    coverage = default_repair_rule_registry().coverage(diagnostics)

    assert coverage.covered_diagnostic_count == 1
    assert coverage.executable_runtime_plan_diagnostic_count == 1
    assert coverage.items[0].known_rule_matched is True
    assert coverage.items[0].matched_rules[0].rule_id == "typescript.object_literal_property_semicolon"


def test_normalizer_builds_cross_artifact_unresolved_symbol_diagnostic() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "Artifact quality scan failed: unresolved import symbol 'WeatherReport' "
            "from './weather' in src/forecast.ts (sibling module does not define it)"
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "cross_artifact_unresolved_import_symbol"
    assert diagnostic.path == "src/forecast.ts"
    assert diagnostic.metadata == {
        "symbol": "WeatherReport",
        "module": "./weather",
        "contract_plane": "cross_artifact_interface",
    }

    coverage = default_repair_rule_registry().coverage(diagnostics)
    assert coverage.covered_diagnostic_count == 1
    assert coverage.executable_runtime_plan_diagnostic_count == 1
    assert coverage.items[0].matched_rules[0].rule_id == "typescript.unresolved_import_symbol_missing_export"


def test_cross_artifact_unresolved_symbol_routes_to_python_rule_for_python_paths() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "Artifact quality scan failed: unresolved import symbol 'WeatherReport' "
            "from 'src.models.weather' in src/engine/forecast.py (sibling module does not define it)"
        ]
    )

    coverage = default_repair_rule_registry().coverage(diagnostics)

    assert coverage.covered_diagnostic_count == 1
    assert coverage.items[0].matched_rules[0].rule_id == "python.unresolved_import_symbol"


def test_repair_rule_registry_reports_known_and_unknown_diagnostic_coverage() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "TypeScript syntax check failed: src/models/Flight.ts(6,5): error TS1005: ',' expected.",
            "src/app.ts(3,14): error TS9999: Unknown future compiler error.",
        ]
    )

    report = default_repair_rule_registry().coverage(diagnostics)
    payload = report.to_dict()

    assert payload["total_diagnostics"] == 2
    assert payload["covered_diagnostic_count"] == 1
    assert payload["uncovered_diagnostic_count"] == 1
    assert payload["coverage_gap_count"] == 1
    assert payload["rule_discovery_required"] is True
    assert payload["coverage_gap_languages"] == ["typescript"]
    assert payload["executable_runtime_plan_diagnostic_count"] == 1
    assert payload["metadata_only_diagnostic_count"] == 0
    assert payload["items"][0]["known_rule_matched"] is True
    assert payload["items"][0]["executable_runtime_plan_matched"] is True
    assert payload["items"][0]["metadata_only_match"] is False
    assert payload["items"][0]["matched_rule_ids"] == [
        "typescript.hyphenated_identifier",
        "typescript.object_literal_missing_comma",
    ]
    assert payload["items"][0]["runtime_plan_rule_ids"] == [
        "typescript.hyphenated_identifier",
        "typescript.object_literal_missing_comma",
    ]
    assert payload["items"][0]["archetypes"] == ["invalid_identifier", "object_literal_syntax"]
    assert payload["items"][0]["phases"] == ["quality_repair"]
    assert payload["items"][1]["known_rule_matched"] is False
    assert payload["items"][1]["matched_rule_ids"] == []
    assert payload["items"][1]["diagnostic_archetype"] == "object_literal_syntax"
    assert payload["items"][1]["diagnostic_phase"] == "quality_repair"
    assert payload["items"][1]["diagnostic_language"] == "typescript"
    assert payload["coverage_gaps"][0]["known_rule_matched"] is False
    assert payload["coverage_gaps"][0]["audit_reason"] == "known_rule_matched=false"
    assert payload["coverage_gaps"][0]["missing_capability"] == "deterministic_repair_rule"


def test_repair_rule_registry_matches_language_specific_go_and_rust_rules() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "main.go:8:2: import path must be string",
            "error[E0433]: failed to resolve: use of unresolved module or unlinked crate `tokio`\n"
            "  --> src/main.rs:3:5",
            "error[E0277]: the trait bound `Widget: Copy` is not satisfied\n  --> src/lib.rs:12:10",
        ]
    )

    payload = default_repair_rule_registry().coverage(diagnostics).to_dict()

    assert payload["covered_diagnostic_count"] == 3
    assert payload["executable_runtime_plan_diagnostic_count"] == 3
    assert payload["metadata_only_diagnostic_count"] == 0
    assert payload["items"][0]["matched_rule_ids"] == ["go.bare_import_string"]
    assert payload["items"][0]["runtime_plan_rule_ids"] == ["go.bare_import_string"]
    assert payload["items"][0]["metadata_only_match"] is False
    assert payload["items"][0]["diagnostic_language"] == "go"
    assert payload["items"][1]["matched_rule_ids"] == ["rust.unlinked_crate_dependency"]
    assert payload["items"][1]["runtime_plan_rule_ids"] == ["rust.unlinked_crate_dependency"]
    assert payload["items"][1]["metadata_only_match"] is False
    assert payload["items"][1]["diagnostic_phase"] == "dependency_resolution"
    assert payload["items"][2]["matched_rule_ids"] == ["rust.missing_trait_derive"]
    assert payload["items"][2]["runtime_plan_rule_ids"] == ["rust.missing_trait_derive"]
    assert payload["items"][2]["diagnostic_archetype"] == "incompatible_derive"


def test_repair_rule_registry_matches_existing_multilanguage_legacy_strategy_metadata() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "src/main.cpp:3:10: fatal error: 'engine.hpp' file not found",
            "src/Main.java:7: error: cannot find symbol",
            'Traceback (most recent call last):\n  File "tests/test_app.py", line 2, in <module>\n'
            "ModuleNotFoundError: No module named 'app'",
            "Error: Cannot find module './src/index.js'",
            "SyntaxError: The requested module './app.js' does not provide an export named 'run'",
            "TypeScript project typecheck failed: src/app.ts(1,10): error TS2305: "
            "Module '\"./model\"' has no exported member 'Widget'.",
            "src/spec.test.ts(1,1): error TS2582: Cannot find name 'describe'.",
        ]
    )

    payload = default_repair_rule_registry().coverage(diagnostics).to_dict()
    matched_source_tools = [item["matched_source_tools"] for item in payload["items"]]

    assert payload["covered_diagnostic_count"] == 7
    assert payload["metadata_only_diagnostic_count"] == 0
    assert payload["executable_runtime_plan_diagnostic_count"] == 7
    assert matched_source_tools[0] == ["deterministic_cpp_include_path_repair"]
    assert payload["items"][0]["runtime_plan_rule_ids"] == ["cpp.include_path"]
    assert payload["items"][0]["diagnostic_language"] == "cpp"
    assert "deterministic_java_post_repair" in matched_source_tools[1]
    assert payload["items"][1]["diagnostic_language"] == "java"
    assert matched_source_tools[2] == ["deterministic_python_package_shadow_bridge_repair"]
    assert payload["items"][2]["diagnostic_language"] == "python"
    assert matched_source_tools[3] == [
        "deterministic_node_test_script_contract_repair",
        "deterministic_npm_script_contract_repair",
        "deterministic_typescript_local_js_import_repair",
    ]
    assert payload["items"][3]["runtime_plan_rule_ids"] == [
        "javascript.cannot_find_module",
        "javascript.npm_script_typescript_source_require_contract",
        "typescript.local_js_import_extension",
    ]
    assert payload["items"][3]["diagnostic_language"] == "javascript"
    assert matched_source_tools[4] == ["deterministic_javascript_missing_export_repair"]
    assert "deterministic_typescript_missing_export_repair" in matched_source_tools[5]
    assert matched_source_tools[6] == ["deterministic_typescript_vitest_globals_repair"]


def test_javascript_module_error_with_unquoted_export_name_stays_javascript() -> None:
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module ./engine/AlchemyEngine.js "
        "does not provide an export named default"
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            error,
        ]
    )

    payload = default_repair_rule_registry().coverage(diagnostics).to_dict()
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=(error,),
            base_files={
                "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
                "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
                "src/engine/AlchemyEngine.js": (
                    "class AlchemyEngine {}\n"
                    "function buildDefaultEngine() { return {}; }\n"
                    "module.exports = AlchemyEngine;\n"
                    "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
                    'module.exports.VERSION = "1.0.0";\n'
                ),
            },
        )
    )
    no_stack_probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=(
                "SyntaxError: The requested module ./engine/AlchemyEngine.js does not provide an export named default",
            ),
            base_files={
                "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
                "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
                "src/engine/AlchemyEngine.js": "class AlchemyEngine {}\nmodule.exports = AlchemyEngine;\n",
            },
        )
    )

    assert diagnostics[0].code == "javascript_module_error"
    assert "The requested module ./engine/AlchemyEngine.js" in diagnostics[0].message
    assert payload["items"][0]["diagnostic_language"] == "javascript"
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in payload["items"][0]["matched_source_tools"]
    assert probe.status == "covered_plannable"
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in probe.plannable_source_tools
    assert no_stack_probe.status == "covered_plannable"


def test_materialization_allowed_paths_query_merges_base_and_runtime_changed_paths() -> None:
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module ./engine/AlchemyEngine.js "
        "does not provide an export named default"
    )
    base_files = {
        "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
        "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
        "src/engine/AlchemyEngine.js": (
            "class AlchemyEngine {}\n"
            "function buildDefaultEngine() { return {}; }\n"
            "module.exports = AlchemyEngine;\n"
            "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
        ),
    }

    result = query_director_repair_materialization_allowed_paths(
        QueryDirectorRepairMaterializationAllowedPathsV1(
            source_tool="deterministic_javascript_esm_commonjs_entrypoint_repair",
            base_files=base_files,
            artifact_quality_errors=(error,),
        )
    )

    assert isinstance(result, DirectorRepairMaterializationAllowedPathsResultV1)
    assert result.owner_cell == "director.runtime"
    assert result.execution_boundary == "read_only_materialization_allowed_paths_no_writes"
    assert result.director_tool_execution_required is False
    assert result.planning_result.ok is True
    assert "package.json" in result.allowed_paths
    assert result.changed_paths
    assert set(result.changed_paths).issubset(set(result.allowed_paths))
    assert result.to_dict()["metadata"]["read_only_allowed_paths_plan"] is True


def test_materialization_plan_probe_query_owns_candidate_and_plannable_source_tools() -> None:
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module ./engine/AlchemyEngine.js "
        "does not provide an export named default"
    )
    base_files = {
        "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
        "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
        "src/engine/AlchemyEngine.js": (
            "class AlchemyEngine {}\n"
            "function buildDefaultEngine() { return {}; }\n"
            "module.exports = AlchemyEngine;\n"
            "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
        ),
    }

    result = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=(error,),
            base_files=base_files,
            source_tools=(
                "deterministic_javascript_esm_commonjs_entrypoint_repair",
                "deterministic_python_package_shadow_bridge_repair",
            ),
        )
    )

    assert isinstance(result, DirectorRepairMaterializationPlanProbeResultV1)
    assert result.owner_cell == "director.runtime"
    assert result.execution_boundary == "read_only_materialization_plan_probe_no_writes"
    assert result.candidate_source_tools == ("deterministic_javascript_esm_commonjs_entrypoint_repair",)
    assert result.plannable_source_tools == ("deterministic_javascript_esm_commonjs_entrypoint_repair",)
    assert result.plan_probe_result is not None
    assert result.to_dict()["metadata"]["coverage_is_not_planning"] is True


def test_materialization_plan_probe_query_uses_runtime_schedule_source_tools() -> None:
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module ./engine/AlchemyEngine.js "
        "does not provide an export named default"
    )
    base_files = {
        "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
        "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
        "src/engine/AlchemyEngine.js": (
            "class AlchemyEngine {}\n"
            "function buildDefaultEngine() { return {}; }\n"
            "module.exports = AlchemyEngine;\n"
            "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
        ),
    }

    result = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=(error,),
            base_files=base_files,
            step_id="materialization.typescript_compiler",
        )
    )

    assert isinstance(result, DirectorRepairMaterializationPlanProbeResultV1)
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in result.requested_source_tools
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in result.candidate_source_tools
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in result.plannable_source_tools
    payload = result.to_dict()
    assert payload["metadata"]["materialization_step_id"] == "materialization.typescript_compiler"
    assert (
        "deterministic_javascript_esm_commonjs_entrypoint_repair"
        in payload["metadata"]["materialization_schedule_source_tools"]
    )


def test_javascript_missing_export_without_declaration_is_covered_unplannable() -> None:
    diagnostics = (
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module './engine/weather.js' does not provide an export named WeatherBalloon",
    )
    base_files = {
        "src/index.js": "import { WeatherBalloon } from './engine/weather.js';\nnew WeatherBalloon().report();\n",
        "src/engine/weather.js": "export function forecast() {\n  return 'cloud';\n}\n",
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_javascript_missing_export_repair",
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            source_tools=("deterministic_javascript_missing_export_repair",),
        )
    )

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["patch_count"] == 0
    assert probe.status == "coverage_matched_but_unplannable"
    assert probe.plannable_source_tools == ()
    assert probe.covered_unplannable_source_tools == ("deterministic_javascript_missing_export_repair",)


def test_javascript_missing_run_export_with_entrypoint_contract_is_plannable() -> None:
    diagnostics = (
        "Artifact quality scan failed: unresolved import symbol 'run' from '../src/index.js' in tests/test_basic.js",
    )
    base_files = {
        "src/index.js": "console.log('dream note app');\n",
        "tests/test_basic.js": (
            'import { run } from "../src/index.js";\n'
            "const output = run();\n"
            "assert.equal(output.ok, true);\n"
            "assert.match(output.entrypoint, /src[\\\\/]+index\\.js$/);\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_javascript_missing_export_repair",
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["composition_summary"]["changed_paths"] == ["src/index.js"]
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "export function run(...args)" in repaired
    assert "return { ok: true, entrypoint };" in repaired


def _plan_javascript_missing_export(
    *,
    base_files: dict[str, str],
    diagnostics: tuple[str, ...],
) -> dict[str, object]:
    return plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=js_syntax.JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()


def _javascript_missing_export_after(
    *,
    base_files: dict[str, str],
    diagnostics: tuple[str, ...],
    path: str = "src/index.js",
) -> str:
    payload = _plan_javascript_missing_export(base_files=base_files, diagnostics=diagnostics)
    assert payload["ok"] is True
    assert payload["planned"] is True
    summary = payload["composition_summary"]
    assert isinstance(summary, dict)
    patches = summary["patches"]
    assert isinstance(patches, list)
    for patch in patches:
        assert isinstance(patch, dict)
        if patch["path"] == path:
            return str(patch["content_after"])
    raise AssertionError(f"missing patch for {path}: {patches!r}")


def _javascript_esm_commonjs_after(
    *,
    base_files: dict[str, str],
    diagnostics: tuple[str, ...],
    path: str = "src/index.js",
) -> str:
    payload = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=js_syntax.JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()
    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["plan_summary"]["rule_id"] == "javascript.commonjs_esm_entrypoint"
    summary = payload["composition_summary"]
    assert isinstance(summary, dict)
    patches = summary["patches"]
    assert isinstance(patches, list)
    for patch in patches:
        assert isinstance(patch, dict)
        if patch["path"] == path:
            return str(patch["content_after"])
    raise AssertionError(f"missing patch for {path}: {patches!r}")


def test_javascript_esm_commonjs_entrypoint_repair_rewrites_multiline_blocks() -> None:
    repaired = _javascript_esm_commonjs_after(
        base_files={
            "package.json": '{"type":"module","main":"src/index.js","scripts":{"start":"node src/index.js"}}',
            "src/models/Note.js": "export class Note {}\nexport default Note;\n",
            "src/index.js": (
                '"use strict";\n\n'
                'const Note = require("./models/Note");\n\n'
                "function main() {\n"
                "  return new Note();\n"
                "}\n\n"
                "if (require.main === module) {\n"
                "  main();\n"
                "}\n\n"
                "module.exports = {\n"
                "  main,\n"
                "  Note,\n"
                "};\n"
                ".exports;\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: workspace validation command failed (npm run start): "
            "file:///tmp/project/src/index.js:3\n"
            "ReferenceError: require is not defined in ES module scope. "
            'package.json contains "type": "module".',
        ),
    )

    assert 'import { Note } from "./models/Note.js";' in repaired
    assert "if (import.meta.url === `file://${process.argv[1]}`)" in repaired
    assert "export { main, Note };" in repaired
    assert "export default { main, Note };" in repaired
    assert "require(" not in repaired
    assert "require.main" not in repaired
    assert "module.exports" not in repaired
    assert ".exports" not in repaired


def test_javascript_esm_commonjs_entrypoint_repair_converts_default_imported_module() -> None:
    repaired = _javascript_esm_commonjs_after(
        path="src/engine/AlchemyEngine.js",
        base_files={
            "package.json": '{"type":"module","main":"src/index.js","scripts":{"start":"node src/index.js"}}',
            "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
            "src/models/Note.js": "export class Note {}\nexport default Note;\n",
            "src/engine/AlchemyEngine.js": (
                '"use strict";\n\n'
                'const Note = require("../models/Note");\n\n'
                "class AlchemyEngine {\n"
                "  constructor() {\n"
                "    this.notes = [new Note()];\n"
                "  }\n"
                "}\n\n"
                "function buildDefaultEngine() {\n"
                "  return { notes: [] };\n"
                "}\n\n"
                "module.exports = AlchemyEngine;\n"
                "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
                'module.exports.VERSION = "1.0.0";\n'
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: workspace validation command failed (npm run start): "
            "file:///tmp/project/src/index.js:1\n"
            "SyntaxError: The requested module './engine/AlchemyEngine.js' "
            "does not provide an export named 'default'",
        ),
    )

    assert 'import { Note } from "../models/Note.js";' in repaired
    assert "export default AlchemyEngine;" in repaired
    assert "export { buildDefaultEngine };" in repaired
    assert 'export const VERSION = "1.0.0";' in repaired
    assert "module.exports" not in repaired
    assert "require(" not in repaired


def test_javascript_esm_commonjs_entrypoint_repair_preserves_namespace_require_binding() -> None:
    repaired = _javascript_esm_commonjs_after(
        base_files={
            "package.json": '{"type":"module","main":"src/index.js","scripts":{"start":"node src/index.js"}}',
            "src/engine/AlchemyEngine.js": (
                "export class AlchemyEngine {}\n"
                "export class Recipe {}\n"
                "export class Note {}\n"
                "export class DreamCard {}\n"
            ),
            "src/index.js": (
                'const AlchemyEngine = require("./engine/AlchemyEngine");\n'
                "const { Note, DreamCard, Recipe } = AlchemyEngine;\n"
                "function buildDemoEngine() {\n"
                "  const engine = new AlchemyEngine();\n"
                "  return { engine, Note, DreamCard, Recipe };\n"
                "}\n"
                "module.exports = { buildDemoEngine };\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: workspace validation command failed (npm run start): "
            "file:///tmp/project/src/index.js:1\n"
            "ReferenceError: require is not defined in ES module scope\n"
            'package.json contains "type": "module"',
        ),
    )

    assert 'import * as AlchemyEngine from "./engine/AlchemyEngine.js";' in repaired
    assert "const engine = new AlchemyEngine.AlchemyEngine();" in repaired
    assert "const { Note, DreamCard, Recipe } = AlchemyEngine;" in repaired
    assert "export { buildDemoEngine };" in repaired
    assert "require(" not in repaired
    assert "module.exports" not in repaired


def test_javascript_missing_export_repair_uses_js_contracts() -> None:
    repaired = _javascript_missing_export_after(
        base_files={
            "src/index.js": "console.log('dream note app');\n",
            "tests/test_basic.js": (
                'import { run, refineDreamNotes } from "../src/index.js";\n'
                "const result = refineDreamNotes({ notes: ['有效便签', '', null] });\n"
                "assert.equal(result.count, 1);\n"
                "assert.equal(result.distilled[0], '[提炼] 有效便签');\n"
                "const output = run();\n"
                "assert.equal(output.ok, true);\n"
                "assert.match(output.entrypoint, /src[\\\\/]+index\\.js$/);\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: unresolved import symbol 'refineDreamNotes' "
            "from '../src/index.js' in tests/test_basic.js",
            "Artifact quality scan failed: unresolved import symbol 'run' "
            "from '../src/index.js' in tests/test_basic.js",
        ),
    )

    assert "export function refineDreamNotes(...args)" in repaired
    assert '"[提炼] " + note.trim()' in repaired
    assert "return { count: distilled.length, distilled };" in repaired
    assert "export function run(...args)" in repaired
    assert "return { ok: true, entrypoint };" in repaired
    assert ": unknown" not in repaired
    assert "): any" not in repaired


def test_javascript_missing_export_repair_turns_iterable_method_into_constant() -> None:
    repaired = _javascript_missing_export_after(
        path="src/engine/AlchemyEngine.js",
        base_files={
            "src/engine/AlchemyEngine.js": (
                "export class AlchemyEngine {\n  defaultRecipes() {\n    return [{ name: 'starter' }];\n  }\n}\n"
            ),
            "tests/alchemyEngine.test.js": (
                'import { AlchemyEngine, defaultRecipes } from "../src/engine/AlchemyEngine.js";\n'
                "const engine = new AlchemyEngine();\n"
                "for (const recipe of defaultRecipes) engine.addRecipe(recipe);\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: unresolved import symbol 'defaultRecipes' "
            "from '../src/engine/AlchemyEngine.js' in tests/alchemyEngine.test.js",
        ),
    )

    assert "export const defaultRecipes = new AlchemyEngine().defaultRecipes();" in repaired
    assert "export function defaultRecipes" not in repaired
    assert "export class AlchemyEngine" in repaired


def test_javascript_export_contract_repair_replaces_wrong_existing_function() -> None:
    repaired = _javascript_missing_export_after(
        base_files={
            "src/index.js": (
                "export function refineDreamNotes(cards) {\n"
                "  if (!Array.isArray(cards)) return [];\n"
                "  return cards;\n"
                "}\n"
            ),
            "tests/smoke.test.js": (
                'import assert from "node:assert/strict";\n'
                'import { refineDreamNotes } from "../src/index.js";\n'
                "const result = refineDreamNotes('a glowing key', 'silent bell', 'paper moon');\n"
                "assert.equal(result.count, 3);\n"
                "assert.equal(result.summary, 'a glowing key | silent bell | paper moon');\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: workspace validation command failed (npm test): "
            "file:///tmp/project/tests/smoke.test.js:5\n"
            "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:\n\n"
            "undefined !== 3",
        ),
    )

    assert "export function refineDreamNotes(...args)" in repaired
    assert "const values = args" in repaired
    assert 'summary: values.join(" | ")' in repaired
    assert "if (!Array.isArray(cards)) return [];" not in repaired


def test_javascript_export_contract_repair_supports_prefixed_text_and_semver() -> None:
    repaired = _javascript_missing_export_after(
        base_files={
            "package.json": '{"version":"0.2.0"}\n',
            "src/index.js": (
                "function refineDreamNotes(notes) {\n"
                "  return [];\n"
                "}\n\n"
                "export function getVersion(...args) {\n"
                "  return { ok: true };\n"
                "}\n\n"
                "export { refineDreamNotes };\n"
            ),
            "tests/smoke.test.js": (
                'import assert from "node:assert/strict";\n'
                'import { refineDreamNotes, getVersion, VERSION } from "../src/index.js";\n'
                "const result = refineDreamNotes('  first dream  \\n\\n second dream ');\n"
                'assert.equal(result, "[dream] first dream\\n[dream] second dream");\n'
                "assert.throws(() => refineDreamNotes(null), TypeError);\n"
                "const v = getVersion();\n"
                "assert.equal(typeof v, 'string');\n"
                "assert.ok(/^\\d+\\.\\d+\\.\\d+/.test(v));\n"
                "assert.equal(typeof VERSION, 'string');\n"
                "assert.equal(VERSION, getVersion());\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: workspace validation command failed (npm test): "
            "file:///tmp/project/tests/smoke.test.js:4\n"
            "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal",
        ),
    )

    assert "function refineDreamNotes(...args)" in repaired
    assert 'throw new TypeError("Expected a string input");' in repaired
    assert '"[dream] " + line' in repaired
    assert "return VERSION;" in repaired
    assert 'export const VERSION = "0.2.0";' in repaired


def test_javascript_export_contract_repair_supports_app_metadata() -> None:
    repaired = _javascript_missing_export_after(
        base_files={
            "package.json": (
                '{"name":"dream-note-alchemy-furnace","version":"0.1.0","description":"Dream note alchemy CLI"}\n'
            ),
            "src/index.js": "export function getAppInfo() {\n  return { ok: true };\n}\n",
            "tests/version.test.js": (
                'import assert from "node:assert/strict";\n'
                'import { APP_NAME, APP_VERSION, APP_DESCRIPTION, getAppInfo } from "../src/index.js";\n'
                "assert.equal(typeof APP_NAME, 'string');\n"
                "assert.ok(APP_NAME.length > 0);\n"
                "assert.match(APP_VERSION, /^\\d+\\.\\d+\\.\\d+/);\n"
                "assert.equal(typeof APP_DESCRIPTION, 'string');\n"
                "const info = getAppInfo();\n"
                "assert.equal(info.name, APP_NAME);\n"
                "assert.equal(info.version, APP_VERSION);\n"
                "assert.equal(info.description, APP_DESCRIPTION);\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: workspace validation command failed (npm test): "
            "file:///tmp/project/tests/version.test.js:8\n"
            "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal",
            "Artifact quality scan failed: unresolved import symbol 'APP_DESCRIPTION' "
            "from '../src/index.js' in tests/version.test.js (sibling module does not define it)",
            "Artifact quality scan failed: unresolved import symbol 'APP_NAME' "
            "from '../src/index.js' in tests/version.test.js (sibling module does not define it)",
            "Artifact quality scan failed: unresolved import symbol 'APP_VERSION' "
            "from '../src/index.js' in tests/version.test.js (sibling module does not define it)",
        ),
    )

    assert 'export const APP_NAME = "dream-note-alchemy-furnace";' in repaired
    assert 'export const APP_VERSION = "0.1.0";' in repaired
    assert 'export const APP_DESCRIPTION = "Dream note alchemy CLI";' in repaired
    assert "name: APP_NAME" in repaired
    assert "version: APP_VERSION" in repaired
    assert "description: APP_DESCRIPTION" in repaired


def test_javascript_export_contract_repair_uses_asserted_literal_and_note_shape() -> None:
    repaired = _javascript_missing_export_after(
        base_files={
            "package.json": '{"name":"dream-note-alchemy-furnace","version":"0.1.0"}\n',
            "src/index.js": "export function main() {\n  return true;\n}\n",
            "tests/test_index.js": (
                'import assert from "node:assert/strict";\n'
                'import { ALCHEMY_FURNACE, refineDreamNote } from "../src/index.js";\n'
                'assert.equal(typeof ALCHEMY_FURNACE, "string");\n'
                'assert.equal(ALCHEMY_FURNACE, "dream-note-alchemy-furnace");\n'
                'const result = refineDreamNote("  flying over paper lanterns  ");\n'
                "assert.deepEqual(result, {\n"
                '  source: "  flying over paper lanterns  ",\n'
                '  refined: "flying over paper lanterns",\n'
                '  tag: "dream-fragment",\n'
                "});\n"
                'const empty = refineDreamNote("   ");\n'
                'assert.equal(empty.source, "   ");\n'
                'assert.equal(empty.refined, "");\n'
                'assert.equal(empty.tag, "empty");\n'
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: unresolved import symbol 'ALCHEMY_FURNACE' "
            "from '../src/index.js' in tests/test_index.js (sibling module does not define it)",
            "Artifact quality scan failed: unresolved import symbol 'refineDreamNote' "
            "from '../src/index.js' in tests/test_index.js (sibling module does not define it)",
        ),
    )

    assert 'export const ALCHEMY_FURNACE = "dream-note-alchemy-furnace";' in repaired
    assert "export function refineDreamNote(...args)" in repaired
    assert 'const source = typeof args[0] === "string" ? args[0] : "";' in repaired
    assert "const refined = source.trim();" in repaired
    assert 'tag: refined.length > 0 ? "dream-fragment" : "empty"' in repaired


def test_repair_rule_registry_rejects_duplicate_rule_ids_and_unknown_source_tool() -> None:
    rule = RepairRuleDefinition(
        rule_id="typescript.object_literal_missing_comma",
        source_tool="deterministic_typescript_return_object_semicolon_repair",
        language="typescript",
        phase="quality_repair",
        archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
        diagnostic_codes=("typescript_ts1005",),
        message_terms=(",", "expected"),
    )

    with pytest.raises(ValueError, match="duplicate repair rule_id"):
        RepairRuleRegistry((rule, rule))

    with pytest.raises(ValueError, match="unregistered repair source_tool"):
        RepairRuleDefinition(
            rule_id="typescript.future_rule",
            source_tool="deterministic_future_repair",
            language="typescript",
            phase="quality_repair",
            archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
            diagnostic_codes=("typescript_ts9999",),
        )


def test_repair_rule_registry_does_not_overmatch_ts1005_without_comma_expected_message() -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts1005",
        message="';' expected.",
        path="src/app.ts",
        raw="src/app.ts(1,1): error TS1005: ';' expected.",
    )

    matches = default_repair_rule_registry().match_diagnostic(diagnostic)

    assert matches == ()


def test_repair_rule_registry_falls_back_to_raw_when_message_is_empty() -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts1005",
        message="",
        path="src/app.ts",
        raw="src/app.ts(1,1): error TS1005: ',' expected.",
    )

    matches = default_repair_rule_registry().match_diagnostic(diagnostic)

    assert [match.rule_id for match in matches] == [
        "typescript.hyphenated_identifier",
        "typescript.object_literal_missing_comma",
    ]


def test_repair_plan_scheduler_orders_dependencies_and_fails_closed_on_cycles() -> None:
    first = RepairPlan(
        rule_id="rule.first",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/first.ts", content="export const first = true;\n"),),
        priority=10,
    )
    second = RepairPlan(
        rule_id="rule.second",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/second.ts", content="export const second = true;\n"),),
        priority=1,
        depends_on=("rule.first",),
    )

    schedule = order_repair_plans((second, first))

    assert schedule.cycle_detected is False
    assert [plan.rule_id for plan in schedule.ordered_plans] == ["rule.first", "rule.second"]

    cyclic_first = RepairPlan(
        rule_id="rule.cyclic_first",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/a.ts", content="a\n"),),
        depends_on=("rule.cyclic_second",),
    )
    cyclic_second = RepairPlan(
        rule_id="rule.cyclic_second",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/b.ts", content="b\n"),),
        depends_on=("rule.cyclic_first",),
    )

    cyclic_schedule = order_repair_plans((cyclic_first, cyclic_second))

    assert cyclic_schedule.cycle_detected is True
    assert cyclic_schedule.ordered_plans == ()
    assert set(cyclic_schedule.blocked_rule_ids) == {"rule.cyclic_first", "rule.cyclic_second"}


def test_repair_convergence_scheduler_records_revalidation_receipt_evidence(tmp_path: Path) -> None:
    relative_path = "src/app.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text("export const pending = true;\n", encoding="utf-8")
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts2304",
        message="Cannot find name 'done'.",
        path=relative_path,
        raw="src/app.ts(1,14): error TS2304: Cannot find name 'done'.",
    )

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del receipts
        current = target.read_text(encoding="utf-8")
        diagnostics = () if "export const done = true;" in current else (diagnostic,)
        return RepairVerifierSnapshot(
            diagnostics=diagnostics,
            command=("npm", "test"),
            exit_code=0 if not diagnostics else 1,
            raw_output_ref=f"runtime/verifier/round-{round_number}.log",
        )

    def planner(diagnostics: tuple[RepairDiagnostic, ...], round_number: int) -> tuple[RepairPlan, ...]:
        if not diagnostics:
            return ()
        return (
            RepairPlan(
                rule_id="typescript.missing_done_export",
                source_tool="deterministic_typescript_missing_export_repair",
                diagnostics=diagnostics,
                operations=(
                    RepairOperation(
                        kind="write_file",
                        path=relative_path,
                        content="export const done = true;\n",
                    ),
                ),
                priority=round_number,
            ),
        )

    def base_files_provider(plan: RepairPlan) -> dict[str, str]:
        del plan
        return {relative_path: target.read_text(encoding="utf-8")}

    def writer(path: str, content: str) -> dict[str, object]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "modify"}

    result = RepairConvergenceScheduler(max_rounds=2).run(
        workspace=tmp_path,
        verifier=verifier,
        planner=planner,
        base_files_provider=base_files_provider,
        writer=writer,
        allowed_paths=(relative_path,),
    )
    payload = result.to_dict()
    receipt = result.receipts[0]

    assert result.status == "converged"
    assert result.converged is True
    assert result.final_diagnostics == ()
    assert result.rounds[0].status == "converged"
    assert receipt.status == "applied"
    assert receipt.round_number == 1
    assert receipt.errors_before == 1
    assert receipt.errors_after == 0
    assert receipt.net_error_reduction == 1
    assert receipt.evidence_status == "resolved_evidence"
    assert receipt.revalidation_evidence is not None
    assert receipt.revalidation_evidence.evidence_status == "resolved_evidence"
    assert receipt.revalidation_evidence.resolved_diagnostic_ids == (diagnostic.diagnostic_id,)
    assert payload["receipts"][0]["evidence_status"] == "resolved_evidence"
    assert payload["rounds"][0]["revalidation_evidence"]["evidence_status"] == "resolved_evidence"
    assert payload["rounds"][0]["revalidation_evidence"]["raw_output_ref"] == "runtime/verifier/round-1.log"
    assert payload["receipts"][0]["revalidation_evidence"]["net_error_reduction"] == 1
    assert target.read_text(encoding="utf-8") == "export const done = true;\n"


def test_runtime_convergence_metadata_uses_current_single_repair_entrypoint_key() -> None:
    metadata = runtime_dispatch_module._runtime_convergence_metadata(
        status="converged",
        source_tools=("deterministic_typescript_missing_export_repair",),
        planner_override=False,
    )

    assert metadata["current_single_repair_entrypoint"] == "run_runtime_repair"
    single_entrypoint_keys = sorted(key for key in metadata if key.endswith("_single_repair_entrypoint"))
    assert single_entrypoint_keys == ["current_single_repair_entrypoint"]
    assert metadata["preferred_entrypoint"] == "run_runtime_repair_convergence"


def test_repair_convergence_scheduler_can_use_policy_gated_editor(tmp_path: Path) -> None:
    relative_path = "src/app.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    original = "export const pending = true;\n"
    target.write_text(original, encoding="utf-8")
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts2304",
        message="Cannot find name 'done'.",
        path=relative_path,
        raw="src/app.ts(1,14): error TS2304: Cannot find name 'done'.",
    )
    edit_calls: list[tuple[str, str, str]] = []
    write_calls: list[tuple[str, str]] = []

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del receipts
        current = target.read_text(encoding="utf-8")
        diagnostics = () if "export const done = true;" in current else (diagnostic,)
        return RepairVerifierSnapshot(
            diagnostics=diagnostics,
            command=("npm", "test"),
            exit_code=0 if not diagnostics else 1,
            raw_output_ref=f"runtime/verifier/editor-round-{round_number}.log",
        )

    def planner(diagnostics: tuple[RepairDiagnostic, ...], round_number: int) -> tuple[RepairPlan, ...]:
        del round_number
        if not diagnostics:
            return ()
        start = original.index("pending")
        return (
            RepairPlan(
                rule_id="typescript.precise_pending_export",
                source_tool="deterministic_typescript_missing_export_repair",
                diagnostics=diagnostics,
                operations=(
                    RepairOperation(
                        kind="text_replace",
                        path=relative_path,
                        span_start=start,
                        span_end=start + len("pending"),
                        expected="pending",
                        replacement="done",
                    ),
                ),
            ),
        )

    def base_files_provider(plan: RepairPlan) -> dict[str, str]:
        del plan
        return {relative_path: target.read_text(encoding="utf-8")}

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append((path, content))
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "modify"}

    def editor(operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert operation.expected is not None
        assert operation.replacement is not None
        assert operation.span_start is not None
        assert operation.span_end is not None
        assert current[operation.span_start : operation.span_end] == operation.expected
        updated = current[: operation.span_start] + operation.replacement + current[operation.span_end :]
        target.write_text(updated, encoding="utf-8")
        edit_calls.append((operation.path, operation.expected, operation.replacement))
        return {"ok": True, "file": operation.path, "operation": "edit"}

    result = RepairConvergenceScheduler(max_rounds=2).run(
        workspace=tmp_path,
        verifier=verifier,
        planner=planner,
        base_files_provider=base_files_provider,
        writer=writer,
        editor=editor,
        allowed_paths=(relative_path,),
    )
    receipt = result.receipts[0]

    assert result.status == "converged"
    assert edit_calls == [(relative_path, "pending", "done")]
    assert write_calls == []
    assert target.read_text(encoding="utf-8") == "export const done = true;\n"
    assert receipt.status == "applied"
    assert receipt.authoritative is True
    assert receipt.revalidation_evidence is not None
    assert receipt.revalidation_evidence.raw_output_ref == "runtime/verifier/editor-round-1.log"
    assert receipt.errors_before == 1
    assert receipt.errors_after == 0


def test_repair_convergence_scheduler_downgrades_failed_revalidation_receipts(tmp_path: Path) -> None:
    relative_path = "src/app.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text("export const pending = true;\n", encoding="utf-8")
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts2304",
        message="Cannot find name 'done'.",
        path=relative_path,
        raw="src/app.ts(1,14): error TS2304: Cannot find name 'done'.",
    )

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del round_number, receipts
        return RepairVerifierSnapshot(
            diagnostics=(diagnostic,),
            command=("npm", "test"),
            exit_code=1,
            raw_output_ref="runtime/verifier/failed.log",
        )

    def planner(diagnostics: tuple[RepairDiagnostic, ...], round_number: int) -> tuple[RepairPlan, ...]:
        if round_number > 1:
            return ()
        return (
            RepairPlan(
                rule_id="typescript.incomplete_fix",
                source_tool="deterministic_typescript_missing_export_repair",
                diagnostics=diagnostics,
                operations=(
                    RepairOperation(
                        kind="write_file",
                        path=relative_path,
                        content="export const still_pending = true;\n",
                    ),
                ),
            ),
        )

    def base_files_provider(plan: RepairPlan) -> dict[str, str]:
        del plan
        return {relative_path: target.read_text(encoding="utf-8")}

    def writer(path: str, content: str) -> dict[str, object]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "modify"}

    result = RepairConvergenceScheduler(max_rounds=2).run(
        workspace=tmp_path,
        verifier=verifier,
        planner=planner,
        base_files_provider=base_files_provider,
        writer=writer,
        allowed_paths=(relative_path,),
    )
    receipt = result.receipts[0]

    assert result.status == "cycle_detected"
    assert result.metadata["post_check_evidence_complete"] is True
    assert result.metadata["evidence_status_counts"]["failed_evidence"] == 1
    assert result.metadata["evidence_status_counts"]["missing_evidence"] == 0
    assert result.metadata["failed_evidence_receipt_ids"] == [receipt.receipt_id]
    assert result.metadata["failed_evidence_source_tools"] == ["deterministic_typescript_missing_export_repair"]
    assert result.metadata["missing_evidence_receipt_ids"] == []
    assert result.metadata["revalidation_coverage"]["failed_evidence_receipt_ids"] == [receipt.receipt_id]
    assert receipt.status == "failed_revalidation"
    assert receipt.authoritative is False
    assert receipt.evidence_status == "failed_evidence"
    assert receipt.metadata["requires_revalidation"] is False
    assert receipt.revalidation_evidence is not None
    assert receipt.revalidation_evidence.evidence_status == "failed_evidence"
    assert receipt.revalidation_evidence.exit_code == 1
    assert receipt.revalidation_evidence.residual_diagnostic_ids == (diagnostic.diagnostic_id,)
    assert receipt.errors_before == 1
    assert receipt.errors_after == 1
    assert receipt.net_error_reduction == 0


def test_repair_convergence_scheduler_projects_missing_previous_receipt_evidence() -> None:
    pending_receipt = RepairReceipt(
        receipt_id="repair_receipt.pending_missing_evidence",
        plan_id="plan.pending_missing_evidence",
        rule_id="typescript.pending",
        source_tool="deterministic_typescript_missing_export_repair",
        status="pending_revalidation",
        mode="commit",
        authoritative=False,
        files_changed=("src/app.ts",),
        metadata={"requires_revalidation": True},
    )

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del round_number, receipts
        return RepairVerifierSnapshot(diagnostics=(), command=("npm", "test"), exit_code=0)

    result = RepairConvergenceScheduler(max_rounds=1).run(
        workspace=Path("."),
        verifier=verifier,
        planner=lambda _diagnostics, _round_number: (),
        base_files_provider=lambda _plan: {},
        previous_receipts=(pending_receipt,),
    )

    assert result.status == "already_clean"
    assert result.metadata["post_check_evidence_complete"] is False
    assert result.metadata["evidence_status_counts"]["missing_evidence"] == 1
    assert result.metadata["evidence_status_counts"]["failed_evidence"] == 0
    assert result.metadata["missing_evidence_receipt_ids"] == [pending_receipt.receipt_id]
    assert result.metadata["missing_evidence_source_tools"] == ["deterministic_typescript_missing_export_repair"]
    assert result.metadata["failed_evidence_receipt_ids"] == []
    assert result.metadata["revalidation_coverage"]["requires_revalidation"] is True


def test_repair_receipt_revalidation_evidence_is_authoritative_hash_material() -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts2304",
        message="Cannot find name 'done'.",
        path="src/app.ts",
        raw="src/app.ts(1,14): error TS2304: Cannot find name 'done'.",
    )

    def receipt_with(evidence: RepairRevalidationEvidence | None) -> RepairReceipt:
        return RepairReceipt(
            receipt_id="repair_receipt.test",
            plan_id="plan.test",
            rule_id="typescript.missing_done_export",
            source_tool="deterministic_typescript_missing_export_repair",
            status="applied",
            mode="commit",
            authoritative=evidence is not None and evidence.exit_code == 0,
            files_changed=("src/app.ts",),
            operation_ids=("operation.test",),
            diagnostics=(diagnostic,),
            before_hashes={"src/app.ts": "before"},
            after_hashes={"src/app.ts": "after"},
            round_number=1,
            revalidation_evidence=evidence,
        )

    resolved_evidence = RepairRevalidationEvidence(
        command=("npm", "test"),
        exit_code=0,
        diagnostics_before=(diagnostic,),
        diagnostics_after=(),
        errors_before_count=1,
        errors_after_count=0,
        resolved_diagnostic_ids=(diagnostic.diagnostic_id,),
        round_number=1,
        raw_output_ref="runtime/verifier/round-1.log",
    )
    residual_evidence = RepairRevalidationEvidence(
        command=("npm", "test"),
        exit_code=1,
        diagnostics_before=(diagnostic,),
        diagnostics_after=(diagnostic,),
        errors_before_count=1,
        errors_after_count=1,
        residual_diagnostic_ids=(diagnostic.diagnostic_id,),
        round_number=1,
        raw_output_ref="runtime/verifier/round-1.log",
    )

    pending_receipt = receipt_with(None)
    resolved_receipt = receipt_with(resolved_evidence)
    residual_receipt = receipt_with(residual_evidence)
    payload = resolved_receipt.to_dict()

    assert resolved_receipt.authority_hash() != pending_receipt.authority_hash()
    assert resolved_receipt.projection_hash() != pending_receipt.projection_hash()
    assert resolved_receipt.authority_hash() != residual_receipt.authority_hash()
    assert resolved_receipt.projection_hash() != residual_receipt.projection_hash()
    assert resolved_receipt.errors_before == 1
    assert resolved_receipt.errors_after == 0
    assert resolved_receipt.net_error_reduction == 1
    assert pending_receipt.evidence_status == "missing_evidence"
    assert resolved_receipt.evidence_status == "resolved_evidence"
    assert residual_receipt.evidence_status == "failed_evidence"
    assert payload["evidence_status"] == "resolved_evidence"
    assert payload["authority_hash"] == resolved_receipt.authority_hash()
    assert payload["projection_hash"] == resolved_receipt.projection_hash()
    assert payload["revalidation_evidence"]["evidence_status"] == "resolved_evidence"
    assert payload["revalidation_evidence"]["net_error_reduction"] == 1


def test_public_repair_receipt_native_revalidation_fields_round_trip() -> None:
    diagnostic_payload = {
        "source": "artifact_quality",
        "code": "typescript_ts2304",
        "message": "Cannot find name 'done'.",
        "path": "src/app.ts",
        "diagnostic_id": "diag_ts2304_done",
    }
    evidence_payload = {
        "command": ["npm", "test"],
        "exit_code": 0,
        "round_number": 1,
        "evidence_status": "resolved_evidence",
        "errors_before": 1,
        "errors_after": 0,
        "net_error_reduction": 1,
        "resolved_diagnostic_ids": ["diag_ts2304_done"],
        "residual_diagnostic_ids": [],
        "diagnostics_before": [diagnostic_payload],
        "diagnostics_after": [],
        "raw_output_ref": "runtime/verifier/round-1.log",
        "metadata": {"verifier": "npm_test"},
    }

    receipt = RepairReceiptV1(
        receipt_id="repair_receipt.public.native",
        plan_id="plan.public.native",
        rule_id="typescript.missing_done_export",
        source_tool="deterministic_typescript_missing_export_repair",
        status="applied",
        authoritative=True,
        files_changed=("src/app.ts",),
        revalidation_evidence=evidence_payload,
    )
    payload = receipt.to_dict()

    assert receipt.evidence_status == "resolved_evidence"
    assert receipt.verifier_command == ("npm", "test")
    assert receipt.verifier_exit_code == 0
    assert receipt.diagnostics_before == (diagnostic_payload,)
    assert receipt.diagnostics_after == ()
    assert receipt.resolved_diagnostic_ids == ("diag_ts2304_done",)
    assert receipt.residual_diagnostic_ids == ()
    assert payload["verifier_command"] == ["npm", "test"]
    assert payload["verifier_exit_code"] == 0
    assert payload["diagnostics_before"] == [diagnostic_payload]
    assert payload["resolved_diagnostic_ids"] == ["diag_ts2304_done"]

    native_only_receipt = RepairReceiptV1(
        receipt_id="repair_receipt.public.native_only",
        plan_id="plan.public.native_only",
        rule_id="typescript.missing_done_export",
        source_tool="deterministic_typescript_missing_export_repair",
        status="applied",
        authoritative=True,
        files_changed=("src/app.ts",),
        evidence_status="resolved_evidence",
        errors_before=1,
        errors_after=0,
        net_error_reduction=1,
        verifier_command=("npm", "test"),
        verifier_exit_code=0,
        diagnostics_before=(diagnostic_payload,),
        diagnostics_after=(),
        resolved_diagnostic_ids=("diag_ts2304_done",),
        residual_diagnostic_ids=(),
    )

    assert native_only_receipt.revalidation_evidence["command"] == ["npm", "test"]
    assert native_only_receipt.revalidation_evidence["exit_code"] == 0
    assert native_only_receipt.revalidation_evidence["diagnostics_before"] == [diagnostic_payload]
    assert native_only_receipt.revalidation_evidence["resolved_diagnostic_ids"] == ["diag_ts2304_done"]


def test_repair_receipt_authority_hash_excludes_agi_advisory_projection_material() -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts2304",
        message="Cannot find name 'done'.",
        path="src/app.ts",
        raw="src/app.ts(1,14): error TS2304: Cannot find name 'done'.",
    )
    evidence = RepairRevalidationEvidence(
        command=("npm", "test"),
        exit_code=0,
        diagnostics_before=(diagnostic,),
        diagnostics_after=(),
        resolved_diagnostic_ids=(diagnostic.diagnostic_id,),
    )
    base_receipt = RepairReceipt(
        receipt_id="repair_receipt.advisory",
        plan_id="plan.advisory",
        rule_id="typescript.missing_done_export",
        source_tool="deterministic_typescript_missing_export_repair",
        status="applied",
        mode="commit",
        authoritative=True,
        files_changed=("src/app.ts",),
        operation_ids=("operation.advisory",),
        diagnostics=(diagnostic,),
        before_hashes={"src/app.ts": "before"},
        after_hashes={"src/app.ts": "after"},
        revalidation_evidence=evidence,
    )
    advisory_receipt = RepairReceipt(
        receipt_id=base_receipt.receipt_id,
        plan_id=base_receipt.plan_id,
        rule_id=base_receipt.rule_id,
        source_tool=base_receipt.source_tool,
        status=base_receipt.status,
        mode=base_receipt.mode,
        authoritative=base_receipt.authoritative,
        files_changed=base_receipt.files_changed,
        operation_ids=base_receipt.operation_ids,
        diagnostics=base_receipt.diagnostics,
        before_hashes=base_receipt.before_hashes,
        after_hashes=base_receipt.after_hashes,
        revalidation_evidence=base_receipt.revalidation_evidence,
        advisor_notes=(
            RepairAdvisorNote(
                source="agi",
                message="Advisory only.",
                confidence=0.4,
                suggested_rules=(
                    {
                        "pattern": "missing done export",
                        "fix_template": "export const done = true;",
                        "confidence": 0.4,
                    },
                ),
            ),
        ),
    )

    assert base_receipt.authority_hash() == advisory_receipt.authority_hash()
    assert base_receipt.projection_hash() != advisory_receipt.projection_hash()


def _assert_direct_runtime_receipt_pending_revalidation(receipt: RepairReceipt | RepairReceiptV1) -> None:
    assert receipt.status == "applied"
    assert receipt.authoritative is False
    assert receipt.evidence_status == "missing_evidence"
    assert receipt.metadata["requires_revalidation"] is True
    if isinstance(receipt, RepairReceiptV1):
        assert receipt.authority_hash
        assert receipt.projection_hash
        assert receipt.revalidation_evidence == {}
    else:
        assert receipt.authority_hash()
        assert receipt.projection_hash()
        assert receipt.revalidation_evidence is None


def test_patch_composer_applies_text_spans_descending() -> None:
    base = {"src/app.ts": "alpha beta gamma"}
    operations = [
        RepairOperation(
            kind="text_replace",
            path="src/app.ts",
            span_start=0,
            span_end=5,
            expected="alpha",
            replacement="one",
        ),
        RepairOperation(
            kind="text_replace",
            path="src/app.ts",
            span_start=11,
            span_end=16,
            expected="gamma",
            replacement="three",
        ),
    ]

    result = PatchComposer().compose(base, operations)

    assert result.ok
    assert len(result.patches) == 1
    assert result.patches[0].content_after == "one beta three"


def test_patch_composer_fails_closed_on_overlapping_text_spans() -> None:
    base = {"src/app.ts": "abcdef"}
    operations = [
        RepairOperation(kind="text_replace", path="src/app.ts", span_start=1, span_end=4, replacement="X"),
        RepairOperation(kind="text_replace", path="src/app.ts", span_start=3, span_end=5, replacement="Y"),
    ]

    result = PatchComposer().compose(base, operations)

    assert not result.ok
    assert result.issues[0].code == "overlapping_text_spans"


def test_patch_composer_merges_json_operations() -> None:
    base = {"package.json": '{"scripts":{"test":"echo fail"}}\n'}
    operations = [
        RepairOperation(kind="json_set", path="package.json", json_path=("scripts", "test"), value="npm run build"),
        RepairOperation(kind="json_set", path="package.json", json_path=("scripts", "build"), value="tsc"),
    ]

    result = PatchComposer().compose(base, operations)

    assert result.ok
    assert '"build": "tsc"' in result.patches[0].content_after
    assert '"test": "npm run build"' in result.patches[0].content_after


def test_typescript_object_literal_comma_rule_builds_canonical_plan() -> None:
    content = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        ["TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected."]
    )

    plan = build_typescript_object_literal_comma_plan(
        base_files={"src/models/Flight.ts": content},
        diagnostics=diagnostics,
    )

    assert plan is not None
    assert plan.rule_id == "typescript.object_literal_missing_comma"
    assert plan.source_tool == "deterministic_typescript_return_object_semicolon_repair"
    assert plan.operations[0].kind == "write_file"
    assert "flightTime, landed:" in str(plan.operations[0].content)
    composition = PatchComposer().compose({"src/models/Flight.ts": content}, plan.operations)
    assert composition.ok
    assert "flightTime, landed:" in composition.patches[0].content_after


def test_typescript_object_literal_comma_runtime_plans_composition_inside_kernel() -> None:
    content = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )

    planning = plan_typescript_object_literal_comma_repair(
        base_files={"src/models/Flight.ts": content},
        artifact_quality_errors=[
            "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected."
        ],
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.plan.rule_id == "typescript.object_literal_missing_comma"
    assert planning.plan.mode == "shadow"
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert "flightTime, landed:" in planning.composition.patches[0].content_after


def test_typescript_hyphenated_identifier_rule_repairs_declaration_and_uses() -> None:
    content = (
        "export function checkScripts(scripts: Record<string, string>) {\n"
        "  const hasSample-check = Object.values(scripts).some((value) => /DONE/.test(value));\n"
        "  return !hasSample-check;\n"
        "}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        ["TypeScript syntax check failed: src/verify.ts(2,18): error TS1005: ',' expected."]
    )

    plan = build_typescript_hyphenated_identifier_plan(
        base_files={"src/verify.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "typescript.hyphenated_identifier"
    assert plan.source_tool == ts_syntax.TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == "src/verify.ts"
    assert "const hasSampleCheck =" in str(operation.replacement)
    assert "return !hasSampleCheck;" in str(operation.replacement)
    assert "hasSample-check" in str(operation.expected)
    assert "hasSample-check" not in str(operation.replacement)


def test_typescript_import_specifier_keyword_rule_repairs_named_import_clause() -> None:
    content = (
        "export type LocalOnly = { id: string };\n"
        "import {\n"
        "  Reputation,\n"
        "  type ReputationSnapshot,\n"
        "  export type ReputationTier,\n"
        "  import type ReputationRecord,\n"
        "  tierForScore,\n"
        '} from "./Reputation";\n'
    )
    diagnostic = "src/models/Market.ts(5,3): error TS1003: Identifier expected."
    source_tool = ts_syntax.TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    plan = ts_syntax.build_typescript_runtime_plan_for_source_tool(
        source_tool=source_tool,
        base_files={"src/models/Market.ts": content},
        diagnostics=normalize_artifact_quality_errors([diagnostic]),
        mode="shadow",
    )

    coverage_payload = coverage.to_dict()
    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["uncovered_diagnostic_count"] == 0
    assert coverage_payload["items"][0]["matched_source_tools"] == [source_tool]
    assert plan is not None
    assert plan.rule_id == "typescript.import_specifier_keyword"
    assert plan.source_tool == source_tool
    assert {operation.kind for operation in plan.operations} == {"text_replace"}
    composition = PatchComposer().compose({"src/models/Market.ts": content}, plan.operations)
    assert composition.ok
    repaired = composition.patches[0].content_after
    assert "export type LocalOnly" in repaired
    assert "  type ReputationTier,\n" in repaired
    assert "  type ReputationRecord,\n" in repaired
    assert "export type ReputationTier" not in repaired
    assert "import type ReputationRecord" not in repaired


def test_typescript_import_specifier_keyword_rule_repairs_embedded_import_type_block() -> None:
    source_tool = ts_syntax.TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL
    content = (
        'import { test } from "node:test";\n'
        'import assert from "node:assert/strict";\n'
        "\n"
        "import {\n"
        'import type { StallId } from "../src/types";\n'
        "  DomainError,\n"
        "  Inventory,\n"
        "  Market,\n"
        '} from "../src/index";\n'
        "\n"
        'const stallId = "stall-src" as StallId;\n'
        'assert.equal(stallId, "stall-src");\n'
    )
    diagnostics = (
        "tests/behavior.test.ts(5,1): error TS1003: Identifier expected.",
        "tests/behavior.test.ts(5,13): error TS1005: 'from' expected.",
        "tests/behavior.test.ts(10,1): error TS1109: Declaration or statement expected.",
        "tests/behavior.test.ts(10,3): error TS1434: Unexpected keyword or identifier.",
    )

    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=diagnostics,
            base_files={"tests/behavior.test.ts": content},
        )
    )
    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={"tests/behavior.test.ts": content},
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    )
    payload = planning_result.to_dict()

    assert result.status == "covered_plannable"
    assert result.uncovered_diagnostics == ()
    assert result.plannable_source_tools == (source_tool,)
    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["composition_summary"]["patch_count"] == 1
    repaired = payload["composition_summary"]["patches"][0]["content_after"]
    assert 'import type { StallId } from "../src/types";\nimport {\n' in repaired
    assert "import {\nimport type" not in repaired
    assert 'StallId } from "../src/types";\n  DomainError' not in repaired


def test_public_typescript_import_specifier_keyword_repair_plans_precise_text_replace() -> None:
    source_tool = ts_syntax.TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL
    content = 'import {\n  Reputation,\n  export type ReputationTier,\n} from "./Reputation";\n'

    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={"src/models/Market.ts": content},
            artifact_quality_errors=("src/models/Market.ts(3,3): error TS1003: Identifier expected.",),
            mode="shadow",
        )
    )
    payload = planning_result.to_dict()

    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["source_tool"] == source_tool
    assert payload["plan_summary"]["rule_id"] == "typescript.import_specifier_keyword"
    assert payload["plan_summary"]["operation_count"] == 1
    assert payload["composition_summary"]["ok"] is True
    assert payload["composition_summary"]["changed_paths"] == ["src/models/Market.ts"]
    assert "  type ReputationTier," in payload["composition_summary"]["patches"][0]["content_after"]


def test_typescript_import_specifier_keyword_rule_fails_closed_without_named_import_clause() -> None:
    source_tool = ts_syntax.TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL
    content = "export type ReputationTier = 'rising' | 'trusted';\n"
    diagnostic = "src/models/Reputation.ts(1,8): error TS1003: Identifier expected."

    plan = ts_syntax.build_typescript_runtime_plan_for_source_tool(
        source_tool=source_tool,
        base_files={"src/models/Reputation.ts": content},
        diagnostics=normalize_artifact_quality_errors([diagnostic]),
        mode="shadow",
    )

    assert plan is None


def test_typescript_nullable_canvas_context_rule_plans_precise_text_replacements() -> None:
    content = (
        "const canvas = document.querySelector('canvas') as HTMLCanvasElement;\n"
        "const ctx = canvas.getContext('2d');\n"
        "ctx.fillStyle = '#fff';\n"
    )
    repaired_text, guarded_symbols = repair_typescript_nullable_canvas_context_guards(content, {"ctx"})
    assert guarded_symbols == ["ctx"]
    assert "const ctx = canvas.getContext('2d')!;" in repaired_text
    diagnostics = normalize_artifact_quality_errors(
        [
            "Artifact quality scan failed: TypeScript project typecheck failed: "
            "src/index.ts(3,1): error TS18047: 'ctx' is possibly 'null'."
        ]
    )

    plan = build_typescript_nullable_canvas_context_plan(
        base_files={"src/index.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "typescript.nullable_canvas_context"
    assert plan.source_tool == "deterministic_typescript_nullable_canvas_context_repair"
    assert {operation.kind for operation in plan.operations} == {"text_replace"}
    assert any("if (!ctx) {" in str(operation.replacement) for operation in plan.operations)
    composition = PatchComposer().compose({"src/index.ts": content}, plan.operations)
    assert composition.ok
    assert "const ctx = canvas.getContext('2d')!;" in composition.patches[0].content_after
    assert "if (!ctx) {" in composition.patches[0].content_after


def test_typescript_nullable_property_chain_rule_adds_targeted_non_null_assertion() -> None:
    diagnostic = "src/main.ts(2,19): error TS18047: 'opened.stall' is possibly 'null'."
    content = "const opened = market.openStall('north', 8);\nconst stallId = opened.stall.id;\n"

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL,
            base_files={"src/main.ts": content},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "const stallId = opened.stall!.id;" in content_after


def test_public_typescript_nullable_dom_global_covers_window_possibly_undefined() -> None:
    diagnostic = "src/web.ts(5,3): error TS18048: 'window' is possibly 'undefined'."
    content = (
        "declare const window: Window | undefined;\n\n"
        "export function boot(): void {\n"
        "  const detail = { ok: true };\n"
        "  window.dispatchEvent(new CustomEvent('boot', { detail }));\n"
        "}\n"
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL,
            base_files={"src/web.ts": content},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    coverage_payload = coverage.to_dict()

    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["uncovered_diagnostic_count"] == 0
    assert (
        ts_syntax.TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL in coverage_payload["items"][0]["matched_source_tools"]
    )
    assert planning["ok"] is True
    assert planning["planned"] is True
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert 'if (typeof window === "undefined") {' in content_after
    assert "window.dispatchEvent" in content_after


def test_typescript_nullable_canvas_context_runtime_plans_composition_inside_kernel() -> None:
    content = (
        "const canvas = document.querySelector('canvas') as HTMLCanvasElement;\n"
        "const ctx = canvas.getContext('2d');\n"
        "ctx.fillStyle = '#fff';\n"
    )

    planning = plan_typescript_nullable_canvas_context_repair(
        base_files={"src/index.ts": content},
        artifact_quality_errors=[
            "Artifact quality scan failed: TypeScript project typecheck failed: "
            "src/index.ts(3,1): error TS18047: 'ctx' is possibly 'null'."
        ],
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.plan.rule_id == "typescript.nullable_canvas_context"
    assert planning.plan.mode == "shadow"
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert "if (!ctx) {" in planning.composition.patches[0].content_after


def test_typescript_duplicate_object_property_rule_plans_precise_line_delete() -> None:
    content = (
        "enum Phase { Quarter = 'quarter', Full = 'full' }\n"
        "const adjacent = {\n"
        "  [Phase.Quarter]: [Phase.Full],\n"
        "  [Phase.Full]: [Phase.Quarter],\n"
        "  [Phase.Quarter]: [Phase.Quarter],\n"
        "};\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        ["src/flower.ts(5,3): error TS1117: An object literal cannot have multiple properties with the same name."]
    )

    plan = build_typescript_duplicate_object_property_plan(
        base_files={"src/flower.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "typescript.duplicate_object_property"
    assert plan.source_tool == "deterministic_typescript_duplicate_object_property_repair"
    assert plan.operations[0].kind == "text_replace"
    assert plan.operations[0].expected == "  [Phase.Quarter]: [Phase.Quarter],\n"
    assert plan.operations[0].replacement == ""
    composition = PatchComposer().compose({"src/flower.ts": content}, plan.operations)
    assert composition.ok
    assert composition.patches[0].content_after.count("[Phase.Quarter]:") == 1


def test_typescript_duplicate_object_property_runtime_plans_composition_inside_kernel() -> None:
    content = "const config = {\n  name: 'first',\n  mode: 'fast',\n  name: 'second',\n};\n"

    planning = plan_typescript_duplicate_object_property_repair(
        base_files={"src/config.ts": content},
        artifact_quality_errors=[
            "src/config.ts(4,3): error TS1117: An object literal cannot have multiple properties with the same name."
        ],
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.plan.rule_id == "typescript.duplicate_object_property"
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert "  name: 'second'" not in planning.composition.patches[0].content_after


def test_typescript_enum_member_separator_rule_plans_precise_line_replace() -> None:
    content = (
        "export enum MoonPhase {\n"
        "  New,\n"
        "  Full,\n"
        "  WaningCrescent;\n"
        "}\n"
        "export interface MoonState {\n"
        "  phase: MoonPhase;\n"
        "}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        ["src/models/moonphase.ts(4,18): error TS1357: An enum member name must be followed by a ',', '=', or '}'."]
    )

    plan = build_typescript_enum_member_separator_plan(
        base_files={"src/models/moonphase.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "typescript.enum_member_separator"
    assert plan.source_tool == "deterministic_typescript_enum_member_separator_repair"
    assert plan.operations[0].kind == "text_replace"
    assert plan.operations[0].expected == "  WaningCrescent;\n"
    assert plan.operations[0].replacement == "  WaningCrescent,\n"
    composition = PatchComposer().compose({"src/models/moonphase.ts": content}, plan.operations)
    assert composition.ok
    assert "  WaningCrescent," in composition.patches[0].content_after
    assert "  phase: MoonPhase;" in composition.patches[0].content_after


def test_typescript_enum_member_separator_runtime_plans_composition_inside_kernel() -> None:
    content = "export enum MoonPhase {\n  New,\n  Full;\n}\n"

    planning = plan_typescript_enum_member_separator_repair(
        base_files={"src/models/moonphase.ts": content},
        artifact_quality_errors=[
            "src/models/moonphase.ts(3,7): error TS1357: An enum member name must be followed by a ',', '=', or '}'."
        ],
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.plan.rule_id == "typescript.enum_member_separator"
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert "  Full," in planning.composition.patches[0].content_after


def test_typescript_missing_closing_brace_rule_plans_precise_eof_insert() -> None:
    content = "export function run(): number {\n  return 1;\n"
    diagnostics = normalize_artifact_quality_errors(["src/app.ts(2,12): error TS1005: '}' expected."])

    repaired = repair_typescript_missing_closing_braces(content)
    plan = build_typescript_missing_closing_brace_plan(
        base_files={"src/app.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert repaired == "export function run(): number {\n  return 1;\n}\n"
    assert plan is not None
    assert plan.rule_id == "typescript.missing_closing_brace"
    assert plan.source_tool == "deterministic_typescript_missing_closing_brace_repair"
    assert {operation.kind for operation in plan.operations} == {"text_replace"}
    assert plan.operations[0].expected == "\n"
    assert plan.operations[0].replacement == "\n}\n"
    composition = PatchComposer().compose({"src/app.ts": content}, plan.operations)
    assert composition.ok
    assert composition.patches[0].content_after == repaired


def test_typescript_missing_closing_brace_rule_fails_closed_for_unbalanced_overflow() -> None:
    content = "\n".join(f"export function f{index}() {{" for index in range(9)) + "\n"
    diagnostics = normalize_artifact_quality_errors(["src/app.ts(9,1): error TS1005: '}' expected."])

    plan = build_typescript_missing_closing_brace_plan(
        base_files={"src/app.ts": content},
        diagnostics=diagnostics,
    )

    assert plan is None


def test_typescript_number_to_string_argument_rule_plans_precise_line_replace() -> None:
    content = "const label = makeLabel(42, width);\n"
    column = content.index("42") + 1
    diagnostics = normalize_artifact_quality_errors(
        [
            "src/garden.ts"
            f"(1,{column}): error TS2345: Argument of type 'number' is not assignable "
            "to parameter of type 'string'."
        ]
    )

    plan = build_typescript_number_to_string_argument_plan(
        base_files={"src/garden.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "typescript.number_to_string_argument"
    assert plan.source_tool == "deterministic_typescript_number_to_string_argument_repair"
    assert {operation.kind for operation in plan.operations} == {"text_replace"}
    assert plan.operations[0].expected == content
    assert plan.operations[0].replacement == "const label = makeLabel(String(42), width);\n"
    composition = PatchComposer().compose({"src/garden.ts": content}, plan.operations)
    assert composition.ok
    assert "makeLabel(String(42), width)" in composition.patches[0].content_after


def test_typescript_number_to_string_argument_runtime_uses_editor_without_write_file(tmp_path: Path) -> None:
    relative_path = "src/garden.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = "const label = makeLabel(42, width);\n"
    target.write_text(content, encoding="utf-8")
    column = content.index("42") + 1
    writes: list[str] = []
    edits: list[str] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append(path)
        raise AssertionError("number-to-string repair must prefer edit_file over write_file")

    def editor(operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert operation.span_start is not None
        assert operation.span_end is not None
        target.write_text(
            current[: operation.span_start] + str(operation.replacement or "") + current[operation.span_end :],
            encoding="utf-8",
        )
        edits.append(operation.operation_id)
        return {"ok": True}

    result = run_runtime_repair(
        source_tool="deterministic_typescript_number_to_string_argument_repair",
        workspace=tmp_path,
        base_files={relative_path: content},
        artifact_quality_errors=(
            "src/garden.ts"
            f"(1,{column}): error TS2345: Argument of type 'number' is not assignable "
            "to parameter of type 'string'.",
        ),
        writer=writer,
        editor=editor,
        allowed_paths=(relative_path,),
    )

    assert result.ok is True
    assert writes == []
    assert edits
    assert target.read_text(encoding="utf-8") == "const label = makeLabel(String(42), width);\n"
    assert result.execution_result is not None
    record = result.execution_result.receipt.metadata["execution_records"][0]
    assert record["operation"] == "edit_file"
    assert result.execution_result.receipt.metadata["write_file_reasons_by_path"] == {}


def test_typescript_too_few_arguments_rule_adds_trailing_declaration_defaults() -> None:
    source_tool = ts_syntax.TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL
    base_files = {
        "src/firefly.ts": (
            "export class Firefly {\n  update(deltaTime: number, moonPhase: number, temperature: number): void {}\n}\n"
        ),
        "src/garden.ts": (
            "import { Firefly } from './firefly.js';\nconst firefly = new Firefly();\nfirefly.update(0.5, 0.8);\n"
        ),
    }
    diagnostic = (
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/garden.ts(3,9): error TS2554: Expected 3 arguments, but got 2."
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )
    payload = planning.to_dict()

    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["plan_summary"]["rule_id"] == "typescript.too_few_arguments"
    assert payload["composition_summary"]["changed_paths"] == ["src/firefly.ts"]
    repaired = payload["composition_summary"]["patches"][0]["content_after"]
    assert "temperature: number = 0" in repaired


def test_typescript_too_few_arguments_rule_repairs_two_arg_clamp_callsite() -> None:
    source_tool = ts_syntax.TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL
    base_files = {
        "src/engine.ts": (
            "function clamp(value: number, min: number, max: number): number {\n"
            "  return Math.max(min, Math.min(max, value));\n"
            "}\n"
            "let y = clamp(42, 600);\n"
        )
    }
    diagnostic = (
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/engine.ts(4,9): error TS2554: Expected 3 arguments, but got 2."
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )
    payload = planning.to_dict()

    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["plan_summary"]["rule_id"] == "typescript.too_few_arguments"
    assert payload["composition_summary"]["changed_paths"] == ["src/engine.ts"]
    repaired = payload["composition_summary"]["patches"][0]["content_after"]
    assert "let y = clamp(42, 0, 600);" in repaired


def test_typescript_uninitialized_property_rule_adds_default_value() -> None:
    source_tool = ts_syntax.TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL
    base_files = {"src/flower.ts": "export class Flower {\n  public happiness: number;\n  constructor() {}\n}\n"}
    diagnostic = (
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/flower.ts(2,10): error TS2564: Property 'happiness' has no initializer "
        "and is not definitely assigned in the constructor."
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )
    payload = planning.to_dict()

    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["plan_summary"]["rule_id"] == "typescript.uninitialized_property"
    assert payload["composition_summary"]["changed_paths"] == ["src/flower.ts"]
    repaired = payload["composition_summary"]["patches"][0]["content_after"]
    assert "public happiness: number = 0;" in repaired


def test_typescript_sourcefile_diagnostics_rule_replaces_bad_scaffold() -> None:
    source_tool = ts_syntax.TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL
    base_files = {
        "src/verify.ts": (
            'import * as ts from "typescript";\n'
            "function check(file: string, text: string): string[] {\n"
            "  const sourceFile = ts.createSourceFile(file, text, ts.ScriptTarget.ES2020, true);\n"
            "  const diagnostics = undefined as unknown as unknown ?? [];\n"
            "  if (0 > 0) {\n"
            "    return diagnostics.map((d) => String(d.messageText));\n"
            "  }\n"
            "  return [];\n"
            "}\n"
        )
    }
    diagnostics = (
        "src/verify.ts(4,23): error TS2871: This expression is always nullish.",
        "src/verify.ts(6,24): error TS2339: Property 'map' does not exist on type '{}'.",
        "src/verify.ts(6,29): error TS7006: Parameter 'd' implicitly has an 'any' type.",
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    )
    payload = planning.to_dict()

    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["plan_summary"]["rule_id"] == "typescript.sourcefile_diagnostics"
    assert payload["composition_summary"]["changed_paths"] == ["src/verify.ts"]
    repaired = payload["composition_summary"]["patches"][0]["content_after"]
    assert "const diagnostics: readonly ts.Diagnostic[]" in repaired
    assert "ts.transpileModule(text" in repaired
    assert "if (diagnostics.length > 0)" in repaired
    assert "undefined as unknown" not in repaired


def test_typescript_escaped_newline_rule_repairs_line_comment_code() -> None:
    source_tool = ts_syntax.TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL
    base_files = {
        "src/middleware/auth.ts": (
            "import { AsyncLocalStorage } from 'async_hooks';\n\n"
            "// Context for tenant lifecycle\\n"
            "export const tenantContext = new AsyncLocalStorage<{ tenantId: string }>();\n"
        )
    }
    diagnostic = (
        "Artifact quality scan failed: TypeScript escaped newline in line comment before code in src/middleware/auth.ts"
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )
    payload = planning.to_dict()

    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["plan_summary"]["rule_id"] == "typescript.escaped_newline"
    assert payload["composition_summary"]["changed_paths"] == ["src/middleware/auth.ts"]
    patch = payload["composition_summary"]["patches"][0]
    assert "lifecycle\\nexport const tenantContext" not in patch["content_after"]
    assert "\nexport const tenantContext" in patch["content_after"]


def test_typescript_readonly_assignment_rule_covers_ts2540_and_removes_single_modifier() -> None:
    content = (
        "export interface InventoryItem {\n"
        "  readonly id: string;\n"
        "  readonly quantity: number;\n"
        "  readonly unitPrice: number;\n"
        "}\n"
        "\n"
        "export function add(existing: InventoryItem, delta: InventoryItem): void {\n"
        "  existing.quantity += delta.quantity;\n"
        "}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        ["src/models/Inventory.ts(8,12): error TS2540: Cannot assign to 'quantity' because it is a read-only property."]
    )

    coverage = build_repair_coverage_report(diagnostics)
    assert coverage.covered_diagnostic_count == 1
    assert coverage.executable_runtime_plan_diagnostic_count == 1
    assert coverage.items[0].matched_rules[0].rule_id == "typescript.readonly_assignment"
    assert coverage.items[0].matched_rules[0].source_tool == "deterministic_typescript_readonly_assignment_repair"

    plan = build_typescript_readonly_assignment_plan(
        base_files={"src/models/Inventory.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "typescript.readonly_assignment"
    assert plan.source_tool == "deterministic_typescript_readonly_assignment_repair"
    assert {operation.kind for operation in plan.operations} == {"text_replace"}
    assert plan.operations[0].expected == "readonly "
    assert plan.operations[0].replacement == ""
    composition = PatchComposer().compose({"src/models/Inventory.ts": content}, plan.operations)
    assert composition.ok
    assert "  quantity: number;" in composition.patches[0].content_after
    assert "readonly id: string" in composition.patches[0].content_after
    assert "readonly unitPrice: number" in composition.patches[0].content_after


def test_typescript_readonly_assignment_rule_fails_closed_for_ambiguous_declarations() -> None:
    content = (
        "export interface A {\n"
        "  readonly quantity: number;\n"
        "}\n"
        "export interface B {\n"
        "  readonly quantity: number;\n"
        "}\n"
        "declare const existing: A;\n"
        "existing.quantity += 1;\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        ["src/models/Inventory.ts(8,10): error TS2540: Cannot assign to 'quantity' because it is a read-only property."]
    )

    plan = build_typescript_readonly_assignment_plan(
        base_files={"src/models/Inventory.ts": content},
        diagnostics=diagnostics,
    )

    assert plan is None


def test_typescript_readonly_assignment_rule_requires_assignment_line_context() -> None:
    content = (
        "export interface InventoryItem {\n"
        "  readonly quantity: number;\n"
        "}\n"
        "declare const existing: InventoryItem;\n"
        "console.log(existing.quantity);\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        ["src/models/Inventory.ts(5,22): error TS2540: Cannot assign to 'quantity' because it is a read-only property."]
    )

    plan = build_typescript_readonly_assignment_plan(
        base_files={"src/models/Inventory.ts": content},
        diagnostics=diagnostics,
    )

    assert plan is None


def test_typescript_string_literal_suggestion_rule_covers_ts2820_and_replaces_same_line_literal() -> None:
    content = (
        'export type MarketPhase = "pre-open" | "open" | "closed";\n'
        "export function initialPhase(): MarketPhase {\n"
        '  return "pre_open";\n'
        "}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            "src/models/Market.ts(3,5): error TS2820: Type '\"pre_open\"' is not assignable to type "
            "'MarketPhase'. Did you mean '\"pre-open\"'?"
        ]
    )

    coverage = build_repair_coverage_report(diagnostics)
    assert coverage.covered_diagnostic_count == 1
    assert coverage.executable_runtime_plan_diagnostic_count == 1
    assert coverage.items[0].matched_rules[0].rule_id == "typescript.string_literal_suggestion"
    assert coverage.items[0].matched_rules[0].source_tool == "deterministic_typescript_string_literal_suggestion_repair"

    plan = build_typescript_string_literal_suggestion_plan(
        base_files={"src/models/Market.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "typescript.string_literal_suggestion"
    assert plan.source_tool == "deterministic_typescript_string_literal_suggestion_repair"
    assert len(plan.operations) == 1
    assert plan.operations[0].expected == '"pre_open"'
    assert plan.operations[0].replacement == '"pre-open"'
    composition = PatchComposer().compose({"src/models/Market.ts": content}, plan.operations)
    assert composition.ok
    assert 'return "pre-open";' in composition.patches[0].content_after
    assert '"pre_open"' not in composition.patches[0].content_after


def test_typescript_string_literal_suggestion_runtime_uses_editor_without_write_file(tmp_path: Path) -> None:
    relative_path = "src/models/Market.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = (
        'export type MarketPhase = "pre-open" | "open" | "closed";\n'
        "export function initialPhase(): MarketPhase {\n"
        '  return "pre_open";\n'
        "}\n"
    )
    target.write_text(content, encoding="utf-8")
    writes: list[str] = []
    edits: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append(path)
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    def editor(operation: RepairOperation) -> dict[str, object]:
        file_path = tmp_path / operation.path
        original = file_path.read_text(encoding="utf-8")
        assert operation.span_start is not None
        assert operation.span_end is not None
        assert original[operation.span_start : operation.span_end] == operation.expected
        updated = original[: operation.span_start] + operation.replacement + original[operation.span_end :]
        file_path.write_text(updated, encoding="utf-8")
        edits.append(operation.operation_id)
        return {"ok": True}

    result = run_runtime_repair(
        source_tool="deterministic_typescript_string_literal_suggestion_repair",
        workspace=tmp_path,
        base_files={relative_path: content},
        artifact_quality_errors=(
            "src/models/Market.ts(3,5): error TS2820: Type '\"pre_open\"' is not assignable to type "
            "'MarketPhase'. Did you mean '\"pre-open\"'?",
        ),
        writer=writer,
        editor=editor,
        allowed_paths=(relative_path,),
    )

    assert result.ok is True
    assert writes == []
    assert edits
    assert target.read_text(encoding="utf-8") == content.replace('"pre_open"', '"pre-open"')


def test_typescript_number_property_call_and_shorthand_scope_rules_cover_r36_diagnostics() -> None:
    index_content = (
        "export function openStall(inventory: { size(): number }, reputation: { score: number }) {\n"
        "  return {\n"
        "    inventorySize: inventory.size(),\n"
        "    reputationScore: reputation.score(),\n"
        "  };\n"
        "}\n"
    )
    inventory_content = (
        "function createInventory() {\n"
        "  const snapshot = () => ({ sku: 'tea' });\n"
        "  return { snapshot };\n"
        "}\n"
        "export const __inventoryInternals = { snapshot };\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            "src/index.ts(4,33): error TS2349: This expression is not callable.",
            "src/models/Inventory.ts(5,39): error TS18004: No value exists in scope for the shorthand property "
            "'snapshot'. Either declare one or provide an initializer.",
        ]
    )

    coverage = build_repair_coverage_report(diagnostics)
    assert coverage.covered_diagnostic_count == 2
    assert coverage.executable_runtime_plan_diagnostic_count == 2
    assert coverage.items[0].matched_rules[0].rule_id == "typescript.number_property_call"
    assert coverage.items[1].matched_rules[0].rule_id == "typescript.shorthand_property_scope"

    number_plan = build_typescript_number_property_call_plan(
        base_files={"src/index.ts": index_content},
        diagnostics=diagnostics,
        mode="shadow",
    )
    shorthand_plan = build_typescript_shorthand_property_scope_plan(
        base_files={"src/models/Inventory.ts": inventory_content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert number_plan is not None
    assert number_plan.rule_id == "typescript.number_property_call"
    assert number_plan.operations[0].expected == "()"
    assert number_plan.operations[0].replacement == ""
    number_composition = PatchComposer().compose({"src/index.ts": index_content}, number_plan.operations)
    assert number_composition.ok
    assert "reputationScore: reputation.score," in number_composition.patches[0].content_after
    assert "inventory.size()" in number_composition.patches[0].content_after

    assert shorthand_plan is not None
    assert shorthand_plan.rule_id == "typescript.shorthand_property_scope"
    shorthand_composition = PatchComposer().compose(
        {"src/models/Inventory.ts": inventory_content},
        shorthand_plan.operations,
    )
    assert shorthand_composition.ok
    assert "export const __inventoryInternals = {};" in shorthand_composition.patches[0].content_after


def test_typescript_number_property_call_and_shorthand_scope_runtime_use_editor_without_write_file(
    tmp_path: Path,
) -> None:
    index_path = "src/index.ts"
    inventory_path = "src/models/Inventory.ts"
    index_target = tmp_path / index_path
    inventory_target = tmp_path / inventory_path
    index_target.parent.mkdir(parents=True)
    inventory_target.parent.mkdir(parents=True)
    index_content = (
        "export function openStall(inventory: { size(): number }, reputation: { score: number }) {\n"
        "  return {\n"
        "    inventorySize: inventory.size(),\n"
        "    reputationScore: reputation.score(),\n"
        "  };\n"
        "}\n"
    )
    inventory_content = (
        "function createInventory() {\n"
        "  const snapshot = () => ({ sku: 'tea' });\n"
        "  return { snapshot };\n"
        "}\n"
        "export const __inventoryInternals = { snapshot };\n"
    )
    index_target.write_text(index_content, encoding="utf-8")
    inventory_target.write_text(inventory_content, encoding="utf-8")
    writes: list[str] = []
    edits: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append(path)
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    def editor(operation: RepairOperation) -> dict[str, object]:
        file_path = tmp_path / operation.path
        original = file_path.read_text(encoding="utf-8")
        assert operation.span_start is not None
        assert operation.span_end is not None
        assert original[operation.span_start : operation.span_end] == operation.expected
        updated = original[: operation.span_start] + operation.replacement + original[operation.span_end :]
        file_path.write_text(updated, encoding="utf-8")
        edits.append(operation.operation_id)
        return {"ok": True}

    number_result = run_runtime_repair(
        source_tool="deterministic_typescript_number_property_call_repair",
        workspace=tmp_path,
        base_files={index_path: index_content},
        artifact_quality_errors=("src/index.ts(4,33): error TS2349: This expression is not callable.",),
        writer=writer,
        editor=editor,
        allowed_paths=(index_path,),
    )
    shorthand_result = run_runtime_repair(
        source_tool="deterministic_typescript_shorthand_property_scope_repair",
        workspace=tmp_path,
        base_files={inventory_path: inventory_content},
        artifact_quality_errors=(
            "src/models/Inventory.ts(5,39): error TS18004: No value exists in scope for the shorthand property "
            "'snapshot'. Either declare one or provide an initializer.",
        ),
        writer=writer,
        editor=editor,
        allowed_paths=(inventory_path,),
    )

    assert number_result.ok is True
    assert shorthand_result.ok is True
    assert writes == []
    assert len(edits) == 2
    assert "reputationScore: reputation.score," in index_target.read_text(encoding="utf-8")
    assert "export const __inventoryInternals = {};" in inventory_target.read_text(encoding="utf-8")


def test_typescript_readonly_assignment_runtime_uses_editor_without_write_file(tmp_path: Path) -> None:
    relative_path = "src/models/Inventory.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = (
        "export interface InventoryItem {\n"
        "  readonly id: string;\n"
        "  readonly quantity: number;\n"
        "}\n"
        "declare const existing: InventoryItem;\n"
        "existing.quantity -= 1;\n"
    )
    target.write_text(content, encoding="utf-8")
    writes: list[str] = []
    edits: list[str] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append(path)
        raise AssertionError("readonly assignment repair must prefer edit_file over write_file")

    def editor(operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert operation.span_start is not None
        assert operation.span_end is not None
        target.write_text(
            current[: operation.span_start] + str(operation.replacement or "") + current[operation.span_end :],
            encoding="utf-8",
        )
        edits.append(operation.operation_id)
        return {"ok": True}

    result = run_runtime_repair(
        source_tool="deterministic_typescript_readonly_assignment_repair",
        workspace=tmp_path,
        base_files={relative_path: content},
        artifact_quality_errors=(
            "src/models/Inventory.ts(6,10): error TS2540: Cannot assign to 'quantity' "
            "because it is a read-only property.",
        ),
        writer=writer,
        editor=editor,
        allowed_paths=(relative_path,),
    )

    assert result.ok is True
    assert writes == []
    assert edits
    assert "  quantity: number;" in target.read_text(encoding="utf-8")


def test_typescript_canvas_scale_return_type_rule_plans_precise_type_replace_and_coverage() -> None:
    content = (
        "export function scaleToCanvas(state: unknown, width: number, height: number): "
        "{ sx: number; sy: number; scale: number } {\n"
        "  const scale = Math.min(width, height);\n"
        "  return { sx: (x: number) => x * scale, sy: (y: number) => y * scale, scale };\n"
        "}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            "src/engine/renderer.ts(178,37): error TS2345: Argument of type 'number' is not assignable "
            "to parameter of type '(n: number) => number'."
        ]
    )

    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    plan = build_typescript_canvas_scale_return_type_plan(
        base_files={"src/engine/simulation.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )
    planning = plan_typescript_canvas_scale_return_type_repair(
        base_files={"src/engine/simulation.ts": content},
        artifact_quality_errors=(diagnostics[0].raw,),
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "typescript.canvas_scale_return_type"
    assert plan.source_tool == "deterministic_typescript_canvas_scale_return_type_repair"
    assert {operation.kind for operation in plan.operations} == {"text_replace"}
    assert plan.operations[0].expected == "{ sx: number; sy: number; scale: number }"
    assert plan.operations[0].replacement == ("{ sx: (n: number) => number; sy: (n: number) => number; scale: number }")
    composition = PatchComposer().compose({"src/engine/simulation.ts": content}, plan.operations)
    assert composition.ok
    assert "{ sx: (n: number) => number; sy: (n: number) => number; scale: number }" in (
        composition.patches[0].content_after
    )
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert coverage["items"][0]["runtime_plan_rule_ids"] == ["typescript.canvas_scale_return_type"]
    assert planning.plan is not None
    assert planning.composition is not None
    assert planning.composition.ok is True


def _ts_diag(raw: str, *, path: str = "", code: str = "artifact_quality") -> RepairDiagnostic:
    return RepairDiagnostic(source="artifact_quality", code=code, message=raw, raw=raw, path=path)


def _typescript_conservative_planner_safe_cases() -> dict[str, tuple[dict[str, str], tuple[RepairDiagnostic, ...]]]:
    return {
        ts_syntax.HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL: (
            {"index.html": '<script type="module" src="src/main.ts"></script>\n'},
            (
                _ts_diag(
                    "HTML module script references TypeScript source 'src/main.ts' in index.html; "
                    "static entrypoints must load JavaScript"
                ),
            ),
        ),
        ts_syntax.JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL: (
            {"dist/app.js": "function greet(name: string): void {\n  return name;\n}\n"},
            (_ts_diag("dist/app.js: SyntaxError: Unexpected token ':'"),),
        ),
        ts_syntax.TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL: (
            {
                "src/user.ts": (
                    "import { Entity } from 'typeorm';\n\n@Entity()\nexport class User {\n  id: number;\n}\n"
                )
            },
            (_ts_diag("undeclared runtime import 'typeorm' in src/user.ts"),),
        ),
        ts_syntax.TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL: (
            {
                "package.json": '{"type":"module"}\n',
                "tsconfig.json": '{"compilerOptions":{"module":"CommonJS"}}\n',
            },
            (_ts_diag("TypeScript CommonJS module output requires package type commonjs, not module."),),
        ),
        ts_syntax.TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL: (
            {
                "package.json": '{"main":"dist/index.js"}\n',
                "src/feature.ts": "export const feature = true;\n",
            },
            (_ts_diag("TypeScript entrypoint missing for dist/index.js."),),
        ),
        ts_syntax.TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL: (
            {"src/app.ts": "// generated\\nexport const value = 1;\n"},
            (_ts_diag("TypeScript escaped newline in line comment before code in src/app.ts"),),
        ),
        ts_syntax.TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL: (
            {
                "src/verify.ts": (
                    "export function checkScripts(scripts: Record<string, string>) {\n"
                    "  const hasSample-check = Object.values(scripts).some((v) => /DONE/.test(v));\n"
                    "  return !hasSample-check;\n"
                    "}\n"
                )
            },
            (
                _ts_diag(
                    "src/verify.ts(2,18): error TS1005: ',' expected.",
                    path="src/verify.ts",
                    code="typescript_ts1005",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL: (
            {
                "src/app.ts": (
                    "interface Sprite { position: { x: number; y: number } }\n"
                    "function draw(sprite: Sprite) {\n"
                    "  console.log(sprite.x);\n"
                    "}\n"
                )
            },
            (
                _ts_diag(
                    "src/app.ts(3,22): error TS2339: Property 'x' does not exist on type 'Sprite'.",
                    path="src/app.ts",
                    code="typescript_ts2339",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL: (
            {
                "src/app.ts": "import { Widget } from './model';\nconsole.log(Widget);\n",
                "src/model.ts": "class Widget {}\n",
            },
            (
                _ts_diag(
                    "src/app.ts(1,10): error TS2305: Module '\"./model\"' has no exported member 'Widget'.",
                    path="src/app.ts",
                    code="typescript_ts2305",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL: (
            {
                "src/app.ts": "interface Sprite {\n}\nfunction draw(sprite: Sprite) {\n  sprite.pixels['main'];\n}\n",
            },
            (
                _ts_diag(
                    "src/app.ts(4,10): error TS2339: Property 'pixels' does not exist on type 'Sprite'.",
                    path="src/app.ts",
                    code="typescript_ts2339",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_REEXPORT_SOURCE_TOOL: (
            {
                "src/app.ts": "import { Glow } from './barrel';\nconsole.log(Glow);\n",
                "src/barrel.ts": "export interface GlowOptions { level: number }\n",
                "src/glow.ts": "export class Glow {}\n",
            },
            (_ts_diag("TypeScript runtime re-export missing export: Glow is undefined in src/app.ts."),),
        ),
        ts_syntax.TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL: (
            {
                "src/barrel.ts": "export { Widget } from './types';\nconst item: Widget = {} as Widget;\n",
            },
            (
                _ts_diag(
                    "src/barrel.ts(2,13): error TS2304: Cannot find name 'Widget'.",
                    path="src/barrel.ts",
                    code="typescript_ts2304",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL: (
            {
                "src/Garden.ts": "import { Moon } from './Moon';\nexport class Garden { moon = new Moon(); }\n",
                "src/moon.ts": "export class Moon {}\n",
            },
            (_ts_diag("unresolved relative import './Moon' in src/Garden.ts"),),
        ),
        ts_syntax.TYPESCRIPT_SCAFFOLD_SOURCE_TOOL: (
            {},
            (_ts_diag("TypeScript scaffold missing package.json and tsconfig.json."),),
        ),
        ts_syntax.TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL: (
            {
                "src/parser.ts": (
                    "import ts from 'typescript';\n"
                    "const sourceFile = ts.createSourceFile('x.ts', '', ts.ScriptTarget.Latest);\n"
                    "const diagnostics = sourceFile.parseDiagnostics;\n"
                )
            },
            (
                _ts_diag(
                    "src/parser.ts(3,32): error TS2339: Property 'parseDiagnostics' does not exist.",
                    path="src/parser.ts",
                    code="typescript_ts2339",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL: (
            {
                "src/engine.ts": (
                    "function clamp(value: number, min: number, max: number): number {\n"
                    "  return Math.max(min, Math.min(max, value));\n"
                    "}\n"
                    "let y = clamp(42, 600);\n"
                )
            },
            (
                _ts_diag(
                    "src/engine.ts(4,9): error TS2554: Expected 3 arguments, but got 2.",
                    path="src/engine.ts",
                    code="typescript_ts2554",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL: (
            {"tsconfig.json": '{"compilerOptions":{"target":"ES2020"}}\n'},
            (
                _ts_diag(
                    "src/app.ts(1,1): error TS2584: Cannot find name 'document'. "
                    "Try changing the 'lib' compiler option to include 'dom'.",
                    path="src/app.ts",
                    code="typescript_ts2584",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL: (
            {"src/user.ts": "class User {\n  name: string;\n}\n"},
            (
                _ts_diag(
                    "src/user.ts(2,3): error TS2564: Property 'name' has no initializer.",
                    path="src/user.ts",
                    code="typescript_ts2564",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL: (
            {
                "src/main.ts": "import { Garden } from './missing';\nconst garden = new Garden();\n",
                "src/garden.ts": "export class Garden {}\n",
            },
            (_ts_diag("unresolved relative import './missing' in src/main.ts"),),
        ),
        ts_syntax.TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL: (
            {
                "src/fairy.ts": (
                    'import { Reputation } from "./reputation";\n'
                    "export interface FairyInit {\n"
                    "  readonly reputation: Reputation;\n"
                    "}\n"
                ),
                "src/reputation.ts": (
                    "class ReputationImpl {\n  readonly score = 0;\n}\nexport const Reputation = ReputationImpl;\n"
                ),
            },
            (
                _ts_diag(
                    "src/fairy.ts(3,24): error TS2749: 'Reputation' refers to a value, "
                    "but is being used as a type here. Did you mean 'typeof Reputation'?",
                    path="src/fairy.ts",
                    code="typescript_ts2749",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL: (
            {
                "src/model.ts": 'export type WidgetKind = "Primary" | "Secondary";\n',
                "src/app.ts": ('import { WidgetKind } from "./model";\nconst kind = WidgetKind.Primary;\n'),
            },
            (
                _ts_diag(
                    "src/app.ts(2,14): error TS2693: 'WidgetKind' only refers to a type, "
                    "but is being used as a value here.",
                    path="src/app.ts",
                    code="typescript_ts2693",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL: (
            {"src/app.ts": "function setLabel(label: string) {\n  return newLabel.trim();\n}\n"},
            (
                _ts_diag(
                    "src/app.ts(2,10): error TS2304: Cannot find name 'newLabel'.",
                    path="src/app.ts",
                    code="typescript_ts2304",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL: (
            {"src/main.ts": "import { Garden } from './missing';\nconsole.log('ready');\n"},
            (_ts_diag("unresolved relative import './missing' in src/main.ts"),),
        ),
        ts_syntax.TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL: (
            {
                "src/app.test.ts": "describe('app', () => {\n  it('works', () => expect(true).toBe(true));\n});\n",
                "package.json": '{"scripts":{"test":"node --test"},"devDependencies":{}}\n',
            },
            (
                _ts_diag(
                    "src/app.test.ts(1,1): error TS2582: Cannot find name 'describe'.",
                    path="src/app.test.ts",
                    code="typescript_ts2582",
                ),
            ),
        ),
        ts_syntax.TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL: (
            {
                "src/user.ts": (
                    "import { z } from 'zod';\nexport class User {}\nexport type User = z.infer<typeof UserSchema>;\n"
                )
            },
            (_ts_diag("TypeScript zod inferred type collides with class User in src/user.ts"),),
        ),
    }


def test_typescript_scaffold_public_planner_generates_package_and_tsconfig() -> None:
    diagnostics = (
        "package.json not found in workspace",
        "tsconfig.json missing in workspace root",
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_SCAFFOLD_SOURCE_TOOL,
            base_files={},
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.scaffold"
    assert planning["composition_summary"]["changed_paths"] == ["package.json", "tsconfig.json"]

    patches = {patch["path"]: patch["content_after"] for patch in planning["composition_summary"]["patches"]}
    package_payload = json.loads(patches["package.json"])
    tsconfig_payload = json.loads(patches["tsconfig.json"])
    assert package_payload["main"] == "dist/index.js"
    assert package_payload["scripts"]["build"] == "tsc"
    assert package_payload["scripts"]["test"] == "npm run build"
    assert package_payload["scripts"]["start"] == "node dist/index.js"
    assert package_payload["devDependencies"]["typescript"] == "^5.0.0"
    assert tsconfig_payload["compilerOptions"]["target"] == "ES2020"
    assert tsconfig_payload["compilerOptions"]["module"] == "ESNext"
    assert tsconfig_payload["compilerOptions"]["rootDir"] == "src"
    assert tsconfig_payload["include"] == ["src/**/*.ts"]


def test_typescript_entrypoint_public_planner_generates_safe_aggregator() -> None:
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL,
            base_files={
                "package.json": '{"main":"dist/index.js","scripts":{"start":"node dist/index.js"}}\n',
                "src/flower.ts": "export const hello = 'world';\n",
                "src/moon.ts": "export const night = true;\n",
                "src/types.d.ts": "export interface Ignored {}\n",
                "src/flower.test.ts": "export const ignored = true;\n",
            },
            artifact_quality_errors=("TypeScript entrypoint missing for dist/index.js.",),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.entrypoint"
    assert planning["composition_summary"]["changed_paths"] == ["src/index.ts"]
    patch = planning["composition_summary"]["patches"][0]
    assert patch["path"] == "src/index.ts"
    content = patch["content_after"]
    assert "import * as flower from './flower';" in content
    assert "import * as moon from './moon';" in content
    assert "export { flower };" in content
    assert "export { moon };" in content
    for forbidden in ("console", "window", "document", "process", "global", "Buffer"):
        assert forbidden not in content


def test_typescript_entrypoint_public_planner_does_not_overwrite_existing_entrypoint() -> None:
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL,
            base_files={
                "package.json": '{"main":"dist/index.js"}\n',
                "src/index.ts": "export const existing = true;\n",
                "src/feature.ts": "export const feature = true;\n",
            },
            artifact_quality_errors=("TypeScript entrypoint missing for dist/index.js.",),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["source_tool"] == ts_syntax.TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL
    assert planning["composition_summary"]["changed_paths"] == []
    assert planning["composition_summary"]["patch_count"] == 0


def test_typescript_relative_import_case_public_planner_rewrites_specifier_only() -> None:
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL,
            base_files={
                "src/Garden.ts": "import { Moon } from './Moon';\nexport class Garden { moon = new Moon(); }\n",
                "src/moon.ts": "export class Moon {}\n",
            },
            artifact_quality_errors=(
                "Artifact quality scan failed: unresolved relative import './Moon' in src/Garden.ts",
            ),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.relative_import_case"
    assert planning["composition_summary"]["changed_paths"] == ["src/Garden.ts"]
    content = planning["composition_summary"]["patches"][0]["content_after"]
    assert "from './moon'" in content
    assert "new Moon()" in content


def test_typescript_unused_import_public_planner_removes_unresolved_unused_import() -> None:
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
            base_files={
                "src/main.ts": 'import { Garden } from "./engine/garden";\nconsole.log("ready");\n',
            },
            artifact_quality_errors=(
                "Artifact quality scan failed: unresolved relative import './engine/garden' in src/main.ts",
            ),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.unused_import"
    assert planning["composition_summary"]["changed_paths"] == ["src/main.ts"]
    assert planning["composition_summary"]["patches"][0]["content_after"] == 'console.log("ready");\n'


def test_typescript_unique_export_import_public_planner_repoints_to_unique_export_owner() -> None:
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
            base_files={
                "src/Garden.ts": "import { Moon } from './models';\nexport class Garden { moon = new Moon(); }\n",
                "src/entities/moon.ts": "export class Moon {}\n",
            },
            artifact_quality_errors=(
                "Artifact quality scan failed: unresolved relative import './models' in src/Garden.ts",
            ),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.unique_export_import"
    assert planning["composition_summary"]["changed_paths"] == ["src/Garden.ts"]
    content = planning["composition_summary"]["patches"][0]["content_after"]
    assert "from './entities/moon'" in content
    assert "new Moon()" in content


def test_typescript_conservative_planner_recognizes_all_legacy_ts_html_source_tools() -> None:
    cases = _typescript_conservative_planner_safe_cases()
    expected_source_tools = {
        ts_syntax.HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
        ts_syntax.JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL,
        ts_syntax.TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_REEXPORT_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_SCAFFOLD_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL,
        ts_syntax.TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL,
    }

    assert set(cases) == expected_source_tools
    assert len(cases) == 25

    plans = []
    for source_tool, (base_files, diagnostics) in cases.items():
        plan = ts_syntax.build_typescript_runtime_plan_for_source_tool(
            source_tool=source_tool,
            base_files=base_files,
            diagnostics=diagnostics,
            mode="shadow",
        )

        assert plan is not None, source_tool
        assert plan.source_tool == source_tool
        assert plan.operations, source_tool
        plans.append(plan)

    operation_kinds = {operation.kind for plan in plans for operation in plan.operations}
    assert "text_replace" in operation_kinds
    assert "json_set" in operation_kinds


def test_typescript_zod_type_class_collision_rewrites_inferred_data_type() -> None:
    diagnostics = ("TypeScript zod inferred type collides with class TaskDefinition in src/models/task_definition.ts",)
    base_files = {
        "src/models/task_definition.ts": (
            "import { z } from 'zod';\n"
            "export const TaskDefinitionSchema = z.object({ title: z.string() });\n"
            "export type TaskDefinition = z.infer<typeof TaskDefinitionSchema>;\n"
            "export class TaskDefinition {\n"
            "  constructor(public data: TaskDefinition) {}\n"
            "  public payload: TaskDefinition;\n"
            "}\n"
        )
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.zod_type_class_collision"
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "export type TaskDefinitionData = z.infer<typeof TaskDefinitionSchema>;" in repaired
    assert "constructor(public data: TaskDefinitionData)" in repaired
    assert "export type TaskDefinition = z.infer<typeof TaskDefinitionSchema>;" not in repaired


def test_typescript_reexport_runtime_repairs_missing_runtime_barrel_symbol(tmp_path: Path) -> None:
    type_dir = tmp_path / "src" / "types"
    test_dir = type_dir / "__tests__"
    test_dir.mkdir(parents=True)
    (type_dir / "asset.ts").write_text(
        "export enum AssetType {\n  garment = 'garment',\n}\n",
        encoding="utf-8",
    )
    generation_path = type_dir / "generation.ts"
    generation_text = (
        "import type { Asset } from './asset';\n"
        "export enum TaskType {\n  garment_to_model = 'garment_to_model',\n}\n"
        "export interface GenerationSpec {\n  input_assets: Asset[];\n}\n"
    )
    generation_path.write_text(generation_text, encoding="utf-8")
    (test_dir / "spec.test.ts").write_text(
        "import { GenerationSpec, TaskType, AssetType } from '../generation';\nconst type = AssetType.garment;\n",
        encoding="utf-8",
    )
    writes: list[str] = []
    edits: list[str] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append(path)
        raise AssertionError("typescript re-export repair must prefer edit_file over write_file")

    def editor(operation: RepairOperation) -> dict[str, object]:
        assert operation.expected is not None
        assert operation.replacement is not None
        assert operation.span_start is not None
        assert operation.span_end is not None
        target = tmp_path / operation.path
        current = target.read_text(encoding="utf-8")
        if current[operation.span_start : operation.span_end] != operation.expected:
            return {"ok": False, "error": "expected text not found"}
        target.write_text(
            current[: operation.span_start] + operation.replacement + current[operation.span_end :],
            encoding="utf-8",
        )
        edits.append(operation.operation_id)
        return {"ok": True}

    result = run_runtime_repair(
        source_tool="deterministic_typescript_reexport_repair",
        workspace=tmp_path,
        base_files={
            "src/types/asset.ts": (type_dir / "asset.ts").read_text(encoding="utf-8"),
            "src/types/generation.ts": generation_text,
            "src/types/__tests__/spec.test.ts": (test_dir / "spec.test.ts").read_text(encoding="utf-8"),
        },
        artifact_quality_errors=(
            "TypeScript runtime re-export missing export: AssetType is undefined in src/types/__tests__/spec.test.ts.",
        ),
        writer=writer,
        editor=editor,
        allowed_paths=("src/types/generation.ts",),
    )

    assert result.ok is True
    assert writes == []
    assert edits
    repaired = generation_path.read_text(encoding="utf-8")
    assert "export { AssetType } from './asset';" in repaired
    assert repaired.count("export { AssetType } from './asset';") == 1
    assert result.execution_result is not None
    receipt = result.execution_result.receipt
    assert receipt.source_tool == "deterministic_typescript_reexport_repair"
    assert receipt.files_changed == ("src/types/generation.ts",)
    record = receipt.metadata["execution_records"][0]
    assert record["operation"] == "edit_file"
    assert record["span_based"] is True
    assert record["precise_edit_strategy"]["editor_used"] is True


def test_typescript_conservative_planner_fails_closed_for_unknown_source_tool_and_unknown_inputs() -> None:
    unknown_diagnostics = (
        _ts_diag(
            "src/app.ts(1,1): error TS9999: Unknown future compiler error.",
            path="src/app.ts",
            code="typescript_ts9999",
        ),
    )
    base_files = {
        "package.json": '{"main":"dist/index.js","type":"module","scripts":{"test":"node --test"}}\n',
        "tsconfig.json": '{"compilerOptions":{"module":"CommonJS"}}\n',
        "src/app.ts": "export const value = 1;\n",
        "src/feature.ts": "export const feature = true;\n",
        "src/app.test.ts": "describe('app', () => expect(true).toBe(true));\n",
        "index.html": '<script type="module" src="src/main.ts"></script>\n',
    }

    assert (
        ts_syntax.build_typescript_runtime_plan_for_source_tool(
            source_tool="deterministic_typescript_future_repair",
            base_files=base_files,
            diagnostics=unknown_diagnostics,
            mode="shadow",
        )
        is None
    )
    for source_tool in _typescript_conservative_planner_safe_cases():
        assert (
            ts_syntax.build_typescript_runtime_plan_for_source_tool(
                source_tool=source_tool,
                base_files=base_files,
                diagnostics=unknown_diagnostics,
                mode="shadow",
            )
            is None
        ), source_tool


def test_typescript_runtime_migrated_rules_fail_closed_for_unknown_inputs() -> None:
    base_files = {"src/app.ts": "export const value = 1;\n"}
    unknown_errors = ("src/app.ts(1,1): error TS9999: Unknown future compiler error.",)

    for source_tool in (
        "deterministic_typescript_missing_closing_brace_repair",
        "deterministic_typescript_number_to_string_argument_repair",
        "deterministic_typescript_canvas_scale_return_type_repair",
    ):
        planning = plan_runtime_repair(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=unknown_errors,
            mode="shadow",
        )

        assert planning.source_tool == source_tool
        assert planning.plan is None
        assert planning.composition is None


def test_patch_residue_cleanup_rule_plans_precise_text_replacements() -> None:
    content = (
        "export const cardAssetsReady = true;\n>>>> REPLACE src/assets/card-assets.ts\nexport const assetCount = 52;\n"
    )
    plan = build_patch_residue_cleanup_plan(
        base_files={"src/assets/card-assets.ts": content},
        diagnostics=(),
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "generic.patch_residue_cleanup"
    assert plan.source_tool == "deterministic_patch_residue_cleanup"
    assert plan.operations[0].kind == "text_replace"
    assert plan.operations[0].expected == ">>>> REPLACE src/assets/card-assets.ts\n"
    composition = PatchComposer().compose({"src/assets/card-assets.ts": content}, plan.operations)
    assert composition.ok
    assert ">>>> REPLACE" not in composition.patches[0].content_after
    assert composition.patches[0].content_after == remove_patch_residue_lines(composition.patches[0].content_after)


def test_scaffold_marker_cleanup_prefers_longest_non_overlapping_marker() -> None:
    content = 'console.log("Hello from Polaris TypeScript scaffold.");\n'
    diagnostics = normalize_artifact_quality_errors(
        ["Artifact quality scan failed: deterministic scaffold marker 'Polaris TypeScript scaffold' in src/main.ts"]
    )

    plan = build_scaffold_marker_cleanup_plan(
        base_files={"src/main.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
        source_tool="deterministic_scaffold_marker_quality_cleanup",
        rule_id="generic.scaffold_marker_quality_cleanup",
        diagnostic_paths_only=True,
    )

    assert plan is not None
    assert len(plan.operations) == 1
    assert plan.operations[0].expected == "Polaris TypeScript scaffold"
    composition = PatchComposer().compose({"src/main.ts": content}, plan.operations)
    assert composition.ok
    assert "Polaris TypeScript scaffold" not in composition.patches[0].content_after
    assert "TypeScript application" in composition.patches[0].content_after


def test_scaffold_placeholder_quality_diagnostic_is_covered_plannable() -> None:
    diagnostic = (
        "Director output quality gate failed: generic/placeholder content detected: "
        "main.go:(?<![.:'\"-])\\bplaceholder\\b(?!\\s*[=:])(?![-'\"])"
    )
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=("deterministic_scaffold_marker_quality_cleanup",),
            artifact_quality_errors=(diagnostic,),
            base_files={
                "main.go": (
                    "package main\n\n// output reflects real state rather than a static placeholder.\nfunc main() {}\n"
                )
            },
        )
    )

    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == ("deterministic_scaffold_marker_quality_cleanup",)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 1


def test_missing_declared_target_runtime_declines_file_fabrication(tmp_path: Path) -> None:
    source_path = "src/user.ts"
    missing_path = "src/user.model.ts"
    source_content = "export interface User { id: string; }\n"
    diagnostic = f"Artifact quality scan failed: declared target file missing '{missing_path}'"
    writes: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        writes.append((path, content))
        return {
            "ok": True,
            "file": path,
            "operation": "create",
            "bytes_written": len(content.encode("utf-8")),
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-missing-declared-target",
            workspace=str(tmp_path),
            source_tool="deterministic_missing_declared_target_repair",
            base_files={source_path: source_content},
            artifact_quality_errors=(diagnostic,),
            allowed_paths=(source_path, missing_path),
        ),
        writer=writer,
    )

    assert result.ok is False
    assert result.error_code == "unsupported_repair_source_tool"
    assert writes == []
    assert result.receipts == ()
    assert not (tmp_path / missing_path).exists()

    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,))
    ).to_dict()
    item = coverage["items"][0]
    assert item["known_rule_matched"] is True
    assert item["executable_runtime_plan_matched"] is False
    assert item["metadata_only_match"] is True
    assert item["coverage_status"] == "metadata_only_not_executable"
    assert item["recommended_route"] == "task_boundary"
    assert item["runtime_blocker_reasons"] == ["task_boundary_required"]
    assert "deterministic_missing_declared_target_repair" in item["matched_source_tools"]

    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=(diagnostic,),
            base_files={source_path: source_content},
            source_tools=("deterministic_missing_declared_target_repair",),
        )
    )
    assert probe.status == "coverage_matched_but_unplannable"
    assert probe.plannable_source_tools == ()
    assert probe.covered_unplannable_source_tools == ("deterministic_missing_declared_target_repair",)
    assert probe.items[0].status == "unsupported_repair_source_tool"
    assert probe.items[0].patch_count == 0


def test_pre_materialization_declared_target_runtime_filters_unsafe_paths() -> None:
    source_path = "src/app.ts"
    diagnostic = "Artifact quality scan failed: declared target file missing 'docs/app.model.ts'"

    planning = plan_runtime_repair(
        source_tool="deterministic_pre_materialization_declared_target_repair",
        base_files={source_path: "export const app = true;\n"},
        artifact_quality_errors=(diagnostic,),
        mode="shadow",
    )

    assert planning.plan is None
    assert planning.composition is None


def test_typescript_ts2584_dom_lib_diagnostic_is_covered_plannable() -> None:
    source_tool = ts_syntax.TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL
    diagnostic = (
        "src/web.ts(1,17): error TS2584: Cannot find name 'document'. "
        "Do you need to change your target library? Try changing the 'lib' "
        "compiler option to include 'dom'."
    )
    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,))
    ).to_dict()
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=(diagnostic,),
            base_files={"tsconfig.json": '{"compilerOptions":{"target":"ES2020"}}\n'},
        )
    )

    assert coverage["covered_diagnostic_count"] == 1
    assert coverage["uncovered_diagnostic_count"] == 0
    assert source_tool in coverage["items"][0]["matched_source_tools"]
    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (source_tool,)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 1


def test_typescript_config_key_split_diagnostic_is_covered_plannable() -> None:
    source_tool = ts_syntax.TYPESCRIPT_CONFIG_KEY_SPLIT_SOURCE_TOOL
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed (npm run build):\n"
        '✘ [ERROR] Expected "}" but found "Dir"\n\n'
        "    vite.config.ts:5:9:\n"
        "      5 │   public Dir: false,\n"
        "        │          ~~~\n"
        "        ╵          }\n"
    )
    base_files = {
        "vite.config.ts": (
            "import { defineConfig } from 'vite';\n\n"
            "export default defineConfig({\n"
            '  root: ".",\n'
            "  public Dir: false,\n"
            "});\n"
        )
    }
    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,))
    ).to_dict()
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=(diagnostic,),
            base_files=base_files,
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            artifact_quality_errors=(diagnostic,),
            base_files=base_files,
            mode="shadow",
        )
    ).to_dict()

    assert coverage["covered_diagnostic_count"] == 1
    assert coverage["uncovered_diagnostic_count"] == 0
    assert source_tool in coverage["items"][0]["matched_source_tools"]
    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (source_tool,)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 1
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["composition_summary"]["patch_count"] == 1
    assert "publicDir: false" in planning["composition_summary"]["patches"][0]["content_after"]


def test_typescript_ts1128_test_block_residue_is_covered_plannable() -> None:
    source_tool = ts_syntax.TYPESCRIPT_TEST_BLOCK_RESIDUE_SOURCE_TOOL
    diagnostic = "src/models/__tests__/market.sim.test.ts(18,3): error TS1128: Declaration or statement expected."
    base_files = {
        "src/models/__tests__/market.sim.test.ts": (
            "import assert from 'node:assert/strict';\n"
            "import { isFairySpecies, isItemCategory } from '../market.js';\n"
            "\n"
            "describe('type guards', () => {\n"
            "  it('isItemCategory accepts only known categories', () => {\n"
            "    assert.equal(isItemCategory('herb'), true);\n"
            "    assert.equal(isItemCategory('potion'), true);\n"
            "    assert.equal(isItemCategory('not-a-category'), false);\n"
            "  });\n"
            "\n"
            "  it('isFairySpecies accepts only known species', () => {\n"
            "    assert.equal(isFairySpecies('moon'), true);\n"
            "    assert.equal(isFairySpecies('not-a-species'), false);\n"
            "  });\n"
            "});\n"
            "    assert.equal(isItemCategory('herb'), true);\n"
            "    assert.equal(isItemCategory('rock'), false);\n"
            "  });\n"
            "  it('isFairySpecies accepts only known species', () => {\n"
            "    assert.equal(isFairySpecies('moon'), true);\n"
            "    assert.equal(isFairySpecies('dragon'), false);\n"
            "  });\n"
            "});\n"
        )
    }
    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,))
    ).to_dict()
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=(diagnostic,),
            base_files=base_files,
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            artifact_quality_errors=(diagnostic,),
            base_files=base_files,
            mode="shadow",
        )
    ).to_dict()

    assert coverage["covered_diagnostic_count"] == 1
    assert coverage["uncovered_diagnostic_count"] == 0
    assert source_tool in coverage["items"][0]["matched_source_tools"]
    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (source_tool,)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 1
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["composition_summary"]["patch_count"] == 1
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "assert.equal(isItemCategory('not-a-category'), false);" in content_after
    assert "assert.equal(isItemCategory('rock'), false);" not in content_after
    assert content_after.rstrip().endswith("});")


def test_typescript_ts6133_unused_import_and_local_declaration_are_covered_plannable() -> None:
    source_tool = ts_syntax.TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL
    diagnostics = (
        "src/main.ts(2,1): error TS6133: 'Reputation' is declared but its value is never read.",
        "src/models/Inventory.test.ts(4,9): error TS6133: 'inv' is declared but its value is never read.",
    )
    base_files = {
        "src/main.ts": (
            'import { Market } from "./models/Market.js";\n'
            'import { Reputation } from "./models/Reputation.js";\n'
            "const market = new Market('night');\n"
            "console.log(market);\n"
        ),
        "src/models/Inventory.test.ts": (
            "import { Inventory } from './Inventory.js';\n"
            "test('quantity guard', () => {\n"
            "  const before = 1;\n"
            "  const inv = new Inventory();\n"
            "  assert.equal(before, 1);\n"
            "});\n"
        ),
    }
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=diagnostics,
            base_files=base_files,
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            mode="shadow",
        )
    ).to_dict()

    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (source_tool,)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 2
    assert planning["ok"] is True
    assert planning["planned"] is True
    patches = {patch["path"]: patch["content_after"] for patch in planning["composition_summary"]["patches"]}
    assert "Reputation" not in patches["src/main.ts"]
    assert "new Inventory();" in patches["src/models/Inventory.test.ts"]
    assert "const inv" not in patches["src/models/Inventory.test.ts"]


def test_typescript_ts6133_unused_function_declaration_is_covered_plannable() -> None:
    source_tool = ts_syntax.TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL
    diagnostic = "src/models/Market.ts(7,10): error TS6133: 'findFairy' is declared but its value is never read."
    source = (
        "export class MarketError extends Error {\n"
        "  constructor(message: string) {\n"
        "    super(message);\n"
        "  }\n"
        "}\n"
        "\n"
        "function findFairy(market: Market, fairyId: string): Fairy {\n"
        "  const found = market.fairies.find((f) => f.id === fairyId);\n"
        "  if (!found) {\n"
        "    throw new MarketError(`Fairy ${fairyId} is not registered in market ${market.id}`);\n"
        "  }\n"
        "  return found;\n"
        "}\n"
        "\n"
        "export function createMarket(): Market {\n"
        '  return { id: "night", fairies: [] } as Market;\n'
        "}\n"
    )

    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=(diagnostic,),
            base_files={"src/models/Market.ts": source},
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            artifact_quality_errors=(diagnostic,),
            base_files={"src/models/Market.ts": source},
            mode="shadow",
        )
    ).to_dict()

    assert result.status == "covered_plannable"
    assert result.items[0].patch_count == 1
    assert planning["ok"] is True
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "function findFairy" not in repaired
    assert "export function createMarket" in repaired
    assert "export class MarketError" in repaired


def test_typescript_ts6133_underscore_unused_local_declaration_is_covered_plannable() -> None:
    source_tool = ts_syntax.TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL
    diagnostic = "src/main.ts(5,9): error TS6133: '_unusedTreasury' is declared but its value is never read."
    source = (
        "function renderTreasury(value: { coins: number }): string {\n"
        "  return String(value.coins);\n"
        "}\n"
        "export function runDemo(): void {\n"
        "  const _unusedTreasury = renderTreasury({ coins: 3 });\n"
        '  console.log("demo");\n'
        "}\n"
    )

    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=(diagnostic,),
            base_files={"src/main.ts": source},
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            artifact_quality_errors=(diagnostic,),
            base_files={"src/main.ts": source},
            mode="shadow",
        )
    ).to_dict()

    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (source_tool,)
    assert planning["ok"] is True
    assert planning["composition_summary"]["patch_count"] == 1
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "const _unusedTreasury" not in repaired
    assert "  renderTreasury({ coins: 3 });\n" in repaired
    assert 'console.log("demo");' in repaired


def test_typescript_ts6133_multispecifier_import_bindings_are_covered_plannable() -> None:
    source_tool = ts_syntax.TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL
    diagnostics = (
        "src/main.ts(3,3): error TS6133: 'MarketError' is declared but its value is never read.",
        "src/main.ts(4,3): error TS6133: 'StallState' is declared but its value is never read.",
        "src/main.ts(7,17): error TS6133: 'FairyError' is declared but its value is never read.",
    )
    base_files = {
        "src/main.ts": (
            "import {\n"
            "  Market,\n"
            "  MarketError,\n"
            "  StallState,\n"
            "  type StallId,\n"
            "} from './models/Market';\n"
            "import { Fairy, FairyError, FairyRole } from './models/Fairy';\n"
            "export { MarketError, StallState } from './models/Market';\n"
            "export { FairyError } from './models/Fairy';\n"
            "const market = new Market();\n"
            "const fairy = new Fairy();\n"
            "console.log(market, fairy, FairyRole.Worker);\n"
        ),
    }
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=diagnostics,
            base_files=base_files,
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            mode="shadow",
        )
    ).to_dict()

    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (source_tool,)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 1
    assert planning["ok"] is True
    assert planning["planned"] is True
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "  MarketError,\n" not in content_after
    assert "  StallState,\n" not in content_after
    assert "import { Fairy, FairyRole } from './models/Fairy';" in content_after
    assert "export { MarketError, StallState } from './models/Market';" in content_after
    assert "export { FairyError } from './models/Fairy';" in content_after


def test_typescript_ts6133_duplicate_diagnostics_do_not_overlap_same_span() -> None:
    source_tool = ts_syntax.TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL
    diagnostics = (
        "Artifact quality scan failed: workspace validation command failed (npm run build): "
        "src/main.ts(2,1): error TS6133: 'Inventory' is declared but its value is never read.\n"
        "src/main.ts(3,1): error TS6133: 'Reputation' is declared but its value is never read.",
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "src/main.ts(2,1): error TS6133: 'Inventory' is declared but its value is never read.\n"
        "src/main.ts(3,1): error TS6133: 'Reputation' is declared but its value is never read.",
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/main.ts(2,1): error TS6133: 'Inventory' is declared but its value is never read.",
    )
    base_files = {
        "src/main.ts": (
            'import { Market } from "./models/Market.js";\n'
            'import { Inventory } from "./models/Inventory.js";\n'
            'import { Reputation } from "./models/Reputation.js";\n'
            "const market = new Market('night');\n"
            "console.log(market);\n"
        ),
    }
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=diagnostics,
            base_files=base_files,
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            mode="shadow",
        )
    ).to_dict()

    assert result.status == "covered_plannable"
    assert result.items[0].status == "covered_plannable"
    assert planning["ok"] is True
    assert planning["composition_summary"]["issue_count"] == 0
    assert planning["composition_summary"]["patch_count"] == 1
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "Inventory" not in repaired
    assert "Reputation" not in repaired
    assert "Market" in repaired


def test_typescript_ts2304_dom_html_type_diagnostic_is_covered_plannable() -> None:
    source_tool = ts_syntax.TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL
    diagnostic = "src/web.ts(18,18): error TS2304: Cannot find name 'HTMLElementTagNameMap'."
    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,))
    ).to_dict()
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=(diagnostic,),
            base_files={"tsconfig.json": '{"compilerOptions":{"target":"ES2020","lib":["ES2020"]}}\n'},
        )
    )

    assert coverage["covered_diagnostic_count"] == 1
    assert coverage["uncovered_diagnostic_count"] == 0
    assert source_tool in coverage["items"][0]["matched_source_tools"]
    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (source_tool,)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 1


def test_java_test_dependency_rule_builds_whole_file_fallback_runtime_plan() -> None:
    relative_path = "src/test/java/AppTest.java"
    content = (
        "import org.junit.jupiter.api.Test;\n"
        "import static org.junit.jupiter.api.Assertions.assertEquals;\n\n"
        "public class AppTest {\n"
        "    @Test\n"
        "    public void addsNumbers() {\n"
        "        assertEquals(4, 2 + 2);\n"
        "    }\n"
        "}\n"
    )
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="java_compile_error",
        message="package org.junit.jupiter.api does not exist",
        path=relative_path,
        raw=f"{relative_path}:1: error: package org.junit.jupiter.api does not exist",
    )

    plan = build_java_test_dependency_plan(
        base_files={relative_path: content},
        diagnostics=(diagnostic,),
        mode="shadow",
    )
    coverage = default_repair_rule_registry().coverage((diagnostic,)).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_java_test_dependency_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(diagnostic.raw,),
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "java.junit_test_dependency"
    assert plan.source_tool == "deterministic_java_test_dependency_repair"
    assert plan.metadata["edit_strategy"] == "whole_file_fallback"
    assert plan.metadata["adapter_transform_migrated"] is True
    assert plan.operations[0].kind == "write_file"
    assert plan.operations[0].metadata["edit_strategy"] == "whole_file_fallback"
    assert plan.operations[0].metadata["adapter_transform_migrated"] is True
    assert "org.junit" not in plan.operations[0].content
    assert "public static void main" in plan.operations[0].content
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "java.junit_test_dependency" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_java_test_dependency_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_java_test_dependency_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_java_eof_truncation_rule_plans_precise_tail_repair() -> None:
    relative_path = "src/test/java/polaris/factory/RhythmEngineTest.java"
    absolute_path = f"/tmp/factory-bench-L2-06-r09/L2-06/{relative_path}"
    content = (
        "package polaris.factory;\n\n"
        "public final class RhythmEngineTest {\n"
        "    public static void main(String[] args) {\n"
        "        int defaultRc = Main.run(new String[]{});\n"
        '        check("cli",\n'
    )
    raw = (
        f"{absolute_path}:6: error: reached end of file while parsing\n"
        '        check("cli",\n'
        "                    ^\n"
        "1 error"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_java_eof_truncation_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_java_post_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "java.eof_truncation_closure"
    assert plan.source_tool == "deterministic_java_post_repair"
    assert plan.operations[0].kind == "text_replace"
    assert plan.operations[0].path == relative_path
    assert plan.operations[0].expected == '        check("cli",\n'
    assert plan.operations[0].metadata["dropped_incomplete_tail"] is True
    assert plan.operations[0].metadata["missing_closing_braces"] == 2
    composition = PatchComposer().compose({relative_path: content}, plan.operations)
    assert composition.ok is True
    repaired = composition.patches[0].content_after
    assert repaired == repair_java_eof_truncation_text(content)
    assert 'check("cli",' not in repaired
    assert repaired.endswith("}\n}\n")
    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "java.eof_truncation_closure" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_java_post_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.rule_id == "java.post_execution_conservative"
    assert "java.eof_truncation_closure" in planning.plan.depends_on
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_java_post_rule_repairs_numeric_constant_and_missing_score_aliases() -> None:
    relative_path = "src/main/java/polaris/factory/engine/RhythmEngine.java"
    test_path = "src/test/java/polaris/factory/RhythmEngineTest.java"
    content = (
        "package polaris.factory.engine;\n\n"
        "public final class RhythmEngine {\n"
        "    public static final int HARMONISE_THRESHOLD = 80;\n"
        "    public static final int SULK_THRESHOLD = 40;\n"
        "    private static final int MAX_DENSITY_DELTA = 1.0;\n\n"
        "    public int scoreAgainst(RhythmMonster monster, BeatPattern pattern) {\n"
        "        return 90;\n"
        "    }\n"
        "}\n"
    )
    raw = (
        f"{relative_path}:6: error: incompatible types: possible lossy conversion from double to int\n"
        "    private static final int MAX_DENSITY_DELTA = 1.0;\n"
        "                                                 ^\n"
        f"{test_path}:60: error: cannot find symbol\n"
        "                scoreBad < RhythmEngine.TOLERATE_THRESHOLD,\n"
        "                                       ^\n"
        "  symbol:   variable TOLERATE_THRESHOLD\n"
        "  location: class RhythmEngine\n"
        f"{test_path}:65: error: cannot find symbol\n"
        "                engine.willHarmonise(inSeason, good));\n"
        "                      ^\n"
        "  symbol:   method willHarmonise(RhythmMonster,BeatPattern)\n"
        "  location: variable engine of type RhythmEngine\n"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_java_post_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "java.numeric_constant_literal_type" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert planning.plan is not None
    assert planning.plan.rule_id == "java.post_execution_conservative"
    assert "java.numeric_constant_literal_type" in planning.plan.depends_on
    assert "java.missing_symbol_compatibility" in planning.plan.depends_on
    assert planning.composition is not None
    assert planning.composition.ok is True
    repaired = planning.composition.patches[0].content_after
    assert "private static final double MAX_DENSITY_DELTA = 1.0;" in repaired
    assert "public static final int TOLERATE_THRESHOLD = SULK_THRESHOLD;" in repaired
    assert "public boolean willHarmonise(RhythmMonster rhythmMonster, BeatPattern beatPattern)" in repaired
    assert "return scoreAgainst(rhythmMonster, beatPattern) >= HARMONISE_THRESHOLD;" in repaired


def test_runtime_dispatcher_exposes_executable_source_tool_bindings() -> None:
    bindings = runtime_repair_bindings()

    assert "deterministic_rust_post_repair" not in runtime_repair_source_tools()
    assert "deterministic_rust_derive_repair" in runtime_repair_source_tools()
    assert len(runtime_repair_source_tools()) == len(bindings)
    assert sum(1 for binding in bindings if binding["language"] == "rust") == 20
    assert runtime_repair_source_tools() == tuple(binding["source_tool"] for binding in bindings)
    assert all(set(binding) == {"source_tool", "language", "rule_id"} for binding in bindings)
    bindings_by_tool = {binding["source_tool"]: binding for binding in bindings}
    assert {
        "deterministic_javascript_esm_commonjs_entrypoint_repair",
        "deterministic_javascript_dom_global_runtime_guard_repair",
        "deterministic_javascript_missing_export_repair",
        "deterministic_javascript_missing_method_runtime_repair",
        "deterministic_javascript_test_missing_target_repair",
        "deterministic_go_error_string_helper_repair",
        "deterministic_python_package_child_reexport_repair",
        "deterministic_python_package_shadow_bridge_repair",
        "deterministic_python_readme_required_token_repair",
        "deterministic_python_unittest_runtime_failure_repair",
        "deterministic_unresolved_import_symbol_repair",
    } <= set(bindings_by_tool)
    assert bindings_by_tool["deterministic_javascript_missing_export_repair"] == {
        "source_tool": "deterministic_javascript_missing_export_repair",
        "language": "javascript",
        "rule_id": "javascript.missing_named_export",
    }
    assert bindings_by_tool["deterministic_unresolved_import_symbol_repair"] == {
        "source_tool": "deterministic_unresolved_import_symbol_repair",
        "language": "python",
        "rule_id": "python.unresolved_import_symbol",
    }
    assert bindings_by_tool[PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL] == {
        "source_tool": PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL,
        "language": "python",
        "rule_id": "python.readme_required_token",
    }
    assert bindings_by_tool["deterministic_go_unused_import_repair"] == {
        "source_tool": "deterministic_go_unused_import_repair",
        "language": "go",
        "rule_id": "go.unused_import",
    }
    assert bindings_by_tool["deterministic_go_error_string_helper_repair"] == {
        "source_tool": "deterministic_go_error_string_helper_repair",
        "language": "go",
        "rule_id": "go.error_string_helper",
    }
    assert bindings_by_tool[ts_syntax.TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL] == {
        "source_tool": ts_syntax.TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL,
        "language": "typescript",
        "rule_id": "typescript.import_specifier_keyword",
    }
    assert bindings_by_tool[ts_syntax.TYPESCRIPT_BRANDED_LITERAL_CAST_SOURCE_TOOL] == {
        "source_tool": ts_syntax.TYPESCRIPT_BRANDED_LITERAL_CAST_SOURCE_TOOL,
        "language": "typescript",
        "rule_id": "typescript.branded_literal_cast",
    }
    assert bindings_by_tool[ts_syntax.TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL] == {
        "source_tool": ts_syntax.TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL,
        "language": "typescript",
        "rule_id": "typescript.literal_union_value_facade",
    }
    assert bindings_by_tool[ts_syntax.TYPESCRIPT_TEST_BLOCK_RESIDUE_SOURCE_TOOL] == {
        "source_tool": ts_syntax.TYPESCRIPT_TEST_BLOCK_RESIDUE_SOURCE_TOOL,
        "language": "typescript",
        "rule_id": "typescript.test_block_residue",
    }
    assert bindings_by_tool[ts_syntax.TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL] == {
        "source_tool": ts_syntax.TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL,
        "language": "typescript",
        "rule_id": "typescript.dom_local_shim_cleanup",
    }
    assert bindings_by_tool[ts_syntax.TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL] == {
        "source_tool": ts_syntax.TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL,
        "language": "typescript",
        "rule_id": "typescript.expect_error_placement",
    }


def test_runtime_executable_source_tools_are_registered_in_strategy_catalog() -> None:
    unregistered = tuple(
        source_tool
        for source_tool in runtime_repair_source_tools()
        if not deterministic_repair_source_tool_known(source_tool)
    )

    assert unregistered == ()


def test_runtime_dispatcher_unknown_source_tool_fails_closed_without_writes(tmp_path: Path) -> None:
    planning = plan_runtime_repair(
        source_tool="deterministic_future_language_repair",
        base_files={"src/main.future": "broken\n"},
        artifact_quality_errors=("future-lang compiler: unknown diagnostic",),
        mode="shadow",
    )

    assert planning.source_tool == "deterministic_future_language_repair"
    assert planning.plan is None
    assert planning.composition is None
    assert planning.error_code == "unsupported_repair_source_tool"
    assert planning.diagnostics[0].raw == "future-lang compiler: unknown diagnostic"

    writes: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append((path, content))
        raise AssertionError("unknown runtime source_tool must not write files")

    run = run_runtime_repair(
        source_tool="deterministic_future_language_repair",
        workspace=tmp_path,
        base_files={"src/main.future": "broken\n"},
        artifact_quality_errors=("future-lang compiler: unknown diagnostic",),
        writer=writer,
        allowed_paths=("src/main.future",),
    )

    assert run.ok is False
    assert run.execution_result is None
    assert run.plan_decision is None
    assert run.composition_decision is None
    assert run.error_code == "unsupported_repair_source_tool"
    assert run.planning.error_code == "unsupported_repair_source_tool"
    assert writes == []


def test_public_repair_unknown_source_tool_exposes_fail_closed_error_without_writes(tmp_path: Path) -> None:
    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_future_language_repair",
            base_files={"src/main.future": "broken\n"},
            artifact_quality_errors=("future-lang compiler: unknown diagnostic",),
            mode="shadow",
        )
    )
    planning_payload = planning_result.to_dict()

    assert planning_payload["ok"] is False
    assert planning_payload["planned"] is False
    assert planning_payload["source_tool"] == "deterministic_future_language_repair"
    assert planning_payload["error_code"] == "unsupported_repair_source_tool"
    assert "No runtime planner is registered" in planning_payload["error_message"]
    assert planning_payload["plan_summary"] is None
    assert planning_payload["composition_summary"]["ok"] is False

    writes: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append((path, content))
        raise AssertionError("unknown public source_tool must not write files")

    run_result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-unknown-source-tool",
            workspace=str(tmp_path),
            source_tool="deterministic_future_language_repair",
            base_files={"src/main.future": "broken\n"},
            artifact_quality_errors=("future-lang compiler: unknown diagnostic",),
            allowed_paths=("src/main.future",),
        ),
        writer=writer,
    )

    assert run_result.ok is False
    assert run_result.error_code == "unsupported_repair_source_tool"
    assert run_result.receipts == ()
    assert run_result.metadata["planning"]["error_code"] == "unsupported_repair_source_tool"
    assert run_result.metadata["planning_error"]["error_code"] == "unsupported_repair_source_tool"
    assert writes == []


def test_public_repair_migrated_typescript_source_tool_uses_runtime_binding_without_legacy_write(
    tmp_path: Path,
) -> None:
    source_tool = "deterministic_typescript_missing_export_repair"
    assert source_tool in runtime_repair_source_tools()

    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={"src/model.ts": "export const value = 1;\n"},
            artifact_quality_errors=("TypeScript project typecheck failed without a matching export diagnostic",),
            mode="shadow",
        )
    )
    planning_payload = planning_result.to_dict()

    assert planning_payload["ok"] is False
    assert planning_payload["planned"] is False
    assert planning_payload["source_tool"] == source_tool
    assert planning_payload["error_code"] is None
    assert planning_payload["error_message"] is None
    assert planning_payload["composition_summary"]["ok"] is False

    writes: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append((path, content))
        raise AssertionError("migrated TypeScript source_tool must fail closed without legacy writes")

    run_result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-migrated-ts-runtime-binding",
            workspace=str(tmp_path),
            source_tool=source_tool,
            base_files={"src/model.ts": "export const value = 1;\n"},
            artifact_quality_errors=("TypeScript project typecheck failed without a matching export diagnostic",),
            allowed_paths=("src/model.ts",),
        ),
        writer=writer,
    )

    assert run_result.ok is False
    assert run_result.error_code == "repair_not_planned"
    assert run_result.receipts == ()
    assert run_result.metadata["planning"]["source_tool"] == source_tool
    assert run_result.metadata["planning"]["planned"] is False
    assert writes == []


def test_public_runtime_dependency_repair_plans_node_types_dev_dependency() -> None:
    source_tool = "deterministic_runtime_dependency_repair"

    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "package.json": ('{"name":"node-ts-app","private":true,"devDependencies":{"typescript":"5.4.5"}}\n')
            },
            artifact_quality_errors=(
                "src/main.ts(43,5): error TS2580: Cannot find name 'process'. "
                "Do you need to install type definitions for node? Try npm i --save-dev @types/node.",
            ),
            mode="shadow",
        )
    )
    payload = planning_result.to_dict()

    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["source_tool"] == source_tool
    assert payload["plan_summary"]["rule_id"] == "generic.runtime_dependency"
    assert payload["plan_summary"]["operation_count"] == 1
    assert payload["composition_summary"]["ok"] is True
    assert payload["composition_summary"]["changed_paths"] == ["package.json"]
    assert '"@types/node"' in payload["composition_summary"]["patches"][0]["content_after"]


def test_public_runtime_dependency_repair_covers_node_scheme_ts2307() -> None:
    source_tool = "deterministic_runtime_dependency_repair"
    diagnostic = (
        "src/main.ts(7,31): error TS2307: Cannot find module 'node:url' or its corresponding type declarations."
    )

    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(diagnostic,),
        )
    )
    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={"package.json": '{"name":"node-ts-app","private":true,"devDependencies":{}}\n'},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )
    coverage_payload = coverage.to_dict()
    planning_payload = planning_result.to_dict()

    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["uncovered_diagnostic_count"] == 0
    assert coverage_payload["items"][0]["matched_source_tools"] == [source_tool]
    assert planning_payload["ok"] is True
    assert planning_payload["planned"] is True
    assert planning_payload["plan_summary"]["rule_id"] == "generic.runtime_dependency"
    assert planning_payload["plan_summary"]["operation_count"] == 1
    assert '"@types/node"' in planning_payload["composition_summary"]["patches"][0]["content_after"]


def test_public_javascript_missing_test_target_covers_npm_module_not_found() -> None:
    source_tool = js_syntax.JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL
    diagnostic = (
        "step verify failed (exit 1): npm run test :: :diagnostics_channel:328:14)\n"
        "    at wrapModuleLoad (node:internal/modules/cjs/loader:237:24)\n"
        "    at Function.executeUserEntryPoint [as runMain] (node:internal/modules/run_main:171:5)\n"
        "    at node:internal/main/run_main_module:36:49 {\n"
        "  code: 'MODULE_NOT_FOUND',\n"
        "  requireStack: []\n"
        "}\n"
    )
    package_json = (
        '{"name":"node-ts-app","private":true,"main":"dist/main.js",'
        '"scripts":{"build":"tsc -p tsconfig.json","test":"npm run build && node tests/smoke.js"},'
        '"devDependencies":{"typescript":"5.4.5"}}\n'
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "package.json": package_json,
                "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n',
                "src/main.ts": "console.log('ok');\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )
    coverage_payload = coverage.to_dict()
    planning_payload = planning_result.to_dict()

    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["uncovered_diagnostic_count"] == 0
    assert coverage_payload["items"][0]["matched_source_tools"] == [source_tool]
    assert planning_payload["ok"] is True
    assert planning_payload["planned"] is True
    assert planning_payload["plan_summary"]["rule_id"] == "javascript.test_missing_target"
    assert planning_payload["plan_summary"]["operation_count"] == 1
    assert planning_payload["composition_summary"]["changed_paths"] == ["tests/smoke.js"]
    smoke_content = planning_payload["composition_summary"]["patches"][0]["content_after"]
    assert "childProcess.execFileSync" in smoke_content
    assert '"dist/main.js"' in smoke_content
    assert "assert.ok(packageJson.name" in smoke_content


def test_public_javascript_typescript_annotation_repair_updates_placeholder_contracts() -> None:
    source_tool = ts_syntax.JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed: file:///tmp/demo/src/index.js:1\n"
        "export function refineDreamNotes(..._args: unknown[]): any {\n"
        "                                         ^\n\n"
        "SyntaxError: Unexpected token ':'"
    )
    base_files = {
        "src/index.js": (
            "export function refineDreamNotes(..._args: unknown[]): any {\n"
            "  return undefined;\n"
            "}\n\n"
            "export function run(..._args: unknown[]): any {\n"
            "  return undefined;\n"
            "}\n"
        ),
        "tests/test_basic.js": (
            'import { run, refineDreamNotes } from "../src/index.js";\n'
            "const result = refineDreamNotes({ notes: ['有效便签'] });\n"
            "assert.equal(result.count, 1);\n"
            "assert.equal(result.distilled[0], '[提炼] 有效便签');\n"
            "const output = run();\n"
            "assert.equal(output.ok, true);\n"
            "assert.match(output.entrypoint, /src[\\\\/]+index\\.js$/);\n"
        ),
    }

    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )
    planning_payload = planning_result.to_dict()

    assert planning_payload["ok"] is True
    assert planning_payload["planned"] is True
    assert planning_payload["plan_summary"]["rule_id"] == "typescript.javascript_annotation_cleanup"
    assert planning_payload["plan_summary"]["operation_count"] >= 1
    assert planning_payload["composition_summary"]["changed_paths"] == ["src/index.js"]
    repaired = planning_payload["composition_summary"]["patches"][0]["content_after"]
    assert "export function refineDreamNotes(...args)" in repaired
    assert "export function run(..._args)" in repaired
    assert ": unknown" not in repaired
    assert "): any" not in repaired
    assert "return undefined" not in repaired
    assert '"[提炼] " + note.trim()' in repaired


def test_public_javascript_missing_test_directory_target_covers_node_test_import_tsx() -> None:
    source_tool = js_syntax.JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL
    diagnostic = "step verify failed (exit 1): npm run test :: node --test --import tsx tests Could not find 'tests'"
    package_json = (
        '{"name":"node-ts-app","private":true,"main":"dist/main.js",'
        '"scripts":{"build":"tsc -p tsconfig.json","test":"node --test --import tsx tests"},'
        '"devDependencies":{"typescript":"5.4.5","tsx":"4.7.2"}}\n'
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "package.json": package_json,
                "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n',
                "src/main.ts": "console.log('ok');\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )
    coverage_payload = coverage.to_dict()
    planning_payload = planning_result.to_dict()

    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["uncovered_diagnostic_count"] == 0
    assert coverage_payload["items"][0]["matched_source_tools"] == [source_tool]
    assert planning_payload["ok"] is True
    assert planning_payload["planned"] is True
    assert planning_payload["plan_summary"]["rule_id"] == "javascript.test_missing_target"
    assert planning_payload["plan_summary"]["operation_count"] == 1
    assert planning_payload["composition_summary"]["changed_paths"] == ["tests/smoke.test.ts"]
    smoke_content = planning_payload["composition_summary"]["patches"][0]["content_after"]
    assert "childProcess.execFileSync" in smoke_content
    assert '"dist/main.js"' in smoke_content
    assert "assert.ok(packageJson.name" in smoke_content


def test_public_javascript_missing_test_directory_target_covers_npm_test_shorthand() -> None:
    source_tool = js_syntax.JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed (npm test): "
        "> sample@0.1.0 test\n> node --test --import tsx ./test\nCould not find './test'"
    )
    package_json = (
        '{"name":"node-ts-app","private":true,"main":"dist/main.js",'
        '"scripts":{"build":"tsc -p tsconfig.json","test":"node --test --import tsx ./test"},'
        '"devDependencies":{"typescript":"5.4.5","tsx":"4.7.2"}}\n'
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "package.json": package_json,
                "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n',
                "src/main.ts": "console.log('ok');\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )
    coverage_payload = coverage.to_dict()
    planning_payload = planning_result.to_dict()

    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["uncovered_diagnostic_count"] == 0
    assert coverage_payload["items"][0]["matched_source_tools"] == [source_tool]
    assert planning_payload["ok"] is True
    assert planning_payload["planned"] is True
    assert planning_payload["composition_summary"]["changed_paths"] == ["package.json", "tests/smoke.test.ts"]
    patches = {patch["path"]: patch["content_after"] for patch in planning_payload["composition_summary"]["patches"]}
    assert '"test": "node --test --import tsx ./tests/smoke.test.ts"' in patches["package.json"]
    assert "node:assert" in patches["tests/smoke.test.ts"]


def test_public_typescript_missing_dist_tests_directory_creates_source_test_target() -> None:
    source_tool = js_syntax.JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed (npm test): "
        "> fairy-market-stall@0.1.0 test\n> node --test dist/__tests__\n"
        "Could not find 'dist/__tests__'"
    )
    package_json = (
        '{"name":"fairy-market-stall","private":true,"main":"dist/main.js",'
        '"scripts":{"build":"tsc -p tsconfig.json","test":"node --test dist/__tests__"},'
        '"devDependencies":{"typescript":"5.4.5"}}\n'
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "package.json": package_json,
                "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"},"include":["src/**/*"]}\n',
                "src/main.ts": "console.log('ok');\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )
    coverage_payload = coverage.to_dict()
    planning_payload = planning_result.to_dict()

    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["uncovered_diagnostic_count"] == 0
    assert coverage_payload["items"][0]["matched_source_tools"] == [source_tool]
    assert planning_payload["ok"] is True
    assert planning_payload["planned"] is True
    assert planning_payload["plan_summary"]["rule_id"] == "javascript.test_missing_target"
    assert planning_payload["composition_summary"]["changed_paths"] == [
        "package.json",
        "src/__tests__/smoke.test.ts",
    ]
    patches = {patch["path"]: patch["content_after"] for patch in planning_payload["composition_summary"]["patches"]}
    assert '"test": "node --test dist/__tests__/smoke.test.js"' in patches["package.json"]
    smoke_content = patches["src/__tests__/smoke.test.ts"]
    assert "const assert = require('assert');" in smoke_content
    assert "import.meta.url" not in smoke_content
    assert '"dist/main.js"' in smoke_content


def test_public_npm_script_contract_uses_tsconfig_rootdir_for_compiled_entrypoint() -> None:
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed (npm test): "
        "npm run build && node dist/verify.js\n"
        "Error: Cannot find module '/tmp/workspace/dist/verify.js'\n"
        "code: 'MODULE_NOT_FOUND'"
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=js_syntax.NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
            base_files={
                "package.json": (
                    '{"type":"module","scripts":{'
                    '"build":"tsc -p tsconfig.json",'
                    '"test":"npm run build && node dist/verify.js",'
                    '"start":"npm run build && node dist/verify.js"'
                    '},"devDependencies":{"typescript":"5.5.4"}}\n'
                ),
                "tsconfig.json": (
                    '{"compilerOptions":{"outDir":"dist","rootDir":"."},"include":["src/**/*.ts","tests/**/*.ts"]}\n'
                ),
                "src/verify.ts": "export function runVerification(): void {}\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "javascript.npm_script_contract"
    assert planning["composition_summary"]["patch_count"] == 1
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert '"test": "npm run build && node dist/src/verify.js"' in content_after
    assert '"start": "npm run build && node dist/src/verify.js"' in content_after


def test_public_typescript_local_js_import_repair_plans_ts_node_commonjs_runtime_miss() -> None:
    diagnostic = (
        "Runtime entrypoint failed:\n"
        "Error: Cannot find module './models/Fairy.js'\n"
        "Require stack:\n"
        "- /workspace/src/main.ts"
    )

    base_files = {
        "package.json": (
            '{"scripts":{"start":"ts-node --transpile-only src/main.ts",'
            '"test":"ts-node --transpile-only tests/behavior.test.ts"}}\n'
        ),
        "tsconfig.json": '{"compilerOptions":{"module":"CommonJS","moduleResolution":"Node"}}\n',
        "src/main.ts": (
            'import { Fairy } from "./models/Fairy.js";\n'
            'import { Market } from "./models/Market.js";\n'
            "console.log(Fairy, Market);\n"
        ),
        "src/models/Fairy.ts": "export class Fairy {}\n",
        "src/models/Market.ts": "export class Market {}\n",
    }

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=js_syntax.TYPESCRIPT_LOCAL_JS_IMPORT_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    coverage_payload = coverage.to_dict()
    assert js_syntax.TYPESCRIPT_LOCAL_JS_IMPORT_SOURCE_TOOL in coverage_payload["items"][0]["matched_source_tools"]
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.local_js_import_extension"
    assert planning["composition_summary"]["patch_count"] == 1
    patched_main = planning["composition_summary"]["patches"][0]["content_after"]
    assert 'from "./models/Fairy"' in patched_main
    assert 'from "./models/Market"' in patched_main
    assert ".js" not in patched_main


def test_public_typescript_local_js_import_repair_fails_closed_for_node_next_module() -> None:
    diagnostic = "Error: Cannot find module './models/Fairy.js'\nRequire stack:\n- /workspace/src/main.ts"

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=js_syntax.TYPESCRIPT_LOCAL_JS_IMPORT_SOURCE_TOOL,
            base_files={
                "package.json": '{"type":"module","scripts":{"start":"tsx src/main.ts"}}\n',
                "tsconfig.json": '{"compilerOptions":{"module":"NodeNext","moduleResolution":"NodeNext"}}\n',
                "src/main.ts": 'import { Fairy } from "./models/Fairy.js";\n',
                "src/models/Fairy.ts": "export class Fairy {}\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is False
    assert planning["planned"] is False


def test_public_npm_script_contract_covers_bench_missing_local_entrypoint_shape() -> None:
    source_tool = js_syntax.NPM_SCRIPT_CONTRACT_SOURCE_TOOL
    diagnostic = "script 'test' references missing local entrypoint: ./tests/register-ts.js"

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "package.json": (
                    '{"type":"commonjs","scripts":{'
                    '"build":"tsc -p tsconfig.json",'
                    '"test":"npm run build && node --test --require ./tests/register-ts.js tests/behavior.test.ts",'
                    '"start":"npm run build && node dist/main.js"'
                    '},"devDependencies":{"typescript":"5.5.4"}}\n'
                ),
                "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n',
                "tests/behavior.test.ts": "import test from 'node:test';\ntest('ok', () => {});\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    coverage_payload = coverage.to_dict()
    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["items"][0]["matched_source_tools"] == [source_tool]
    assert planning["ok"] is True
    assert planning["planned"] is True
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "register-ts.js" not in content_after
    assert '"test": "npm run build"' in content_after


def test_public_npm_script_source_require_module_error_is_covered_plannable() -> None:
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed (npm test): "
        "node -e \"require('./src/models/firefly')\"\n"
        "Error: Cannot find module './src/models/firefly'"
    )
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(js_syntax.NPM_SCRIPT_CONTRACT_SOURCE_TOOL,),
            artifact_quality_errors=(diagnostic,),
            base_files={
                "package.json": (
                    '{"scripts":{"build":"tsc","test":"node -e \\"require('
                    "'./src/models/firefly'"
                    ')\\""},"devDependencies":{"typescript":"^5.0.0"}}\n'
                ),
                "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n',
                "src/models/firefly.ts": "export class Firefly {}\n",
            },
        )
    )

    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (js_syntax.NPM_SCRIPT_CONTRACT_SOURCE_TOOL,)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 1


def test_public_npm_start_typescript_source_loader_require_cycle_is_covered_plannable() -> None:
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed (npm run start):\n"
        "> node --loader ts-node/esm src/main.ts || node -r ts-node/register src/main.ts\n"
        "Error [ERR_REQUIRE_CYCLE_MODULE]: Cannot require() ES Module /tmp/demo/src/main.ts in a cycle.\n"
        "src/main.ts(2,1): error TS6133: 'Unused' is declared but its value is never read."
    )
    base_files = {
        "package.json": (
            '{"scripts":{"build":"tsc -p tsconfig.json","start":"node --loader ts-node/esm src/main.ts || '
            'node -r ts-node/register src/main.ts"},"devDependencies":{"typescript":"^5.0.0","ts-node":"^10.9.2"}}\n'
        ),
        "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n',
        "src/main.ts": "export function main(): void {}\n",
    }

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(js_syntax.NPM_SCRIPT_CONTRACT_SOURCE_TOOL,),
            artifact_quality_errors=(diagnostic,),
            base_files=base_files,
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=js_syntax.NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
            artifact_quality_errors=(diagnostic,),
            base_files=base_files,
            mode="shadow",
        )
    ).to_dict()

    assert coverage.covered_diagnostic_count == 2
    assert js_syntax.NPM_SCRIPT_CONTRACT_SOURCE_TOOL in {
        source_tool for item in coverage.items for source_tool in item.matched_source_tools
    }
    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (js_syntax.NPM_SCRIPT_CONTRACT_SOURCE_TOOL,)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 1
    assert planning["ok"] is True
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert '"start": "npm run build && node dist/main.js"' in content_after


def test_public_typescript_tsconfig_rootdir_repair_covers_tests_outside_src(tmp_path: Path) -> None:
    source_tool = ts_syntax.TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL
    diagnostic = (
        "error TS6059: File '/tmp/workspace/tests/verify.test.ts' is not under 'rootDir' "
        "'/tmp/workspace/src'. 'rootDir' is expected to contain all source files."
    )
    base_files = {
        "tsconfig.json": (
            '{"compilerOptions":{"outDir":"dist","rootDir":"src","strict":true},'
            '"include":["src/**/*.ts","tests/**/*.ts"]}\n'
        ),
        "src/verify.ts": "export function runAllChecks(): void {}\n",
        "tests/verify.test.ts": "import { runAllChecks } from '../src/verify';\nrunAllChecks();\n",
    }

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert coverage.to_dict()["coverage_gap_count"] == 0
    assert coverage.to_dict()["items"][0]["matched_source_tools"] == [source_tool]
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.tsconfig_rootdir"
    assert planning["composition_summary"]["changed_paths"] == ["tsconfig.json"]
    assert '"rootDir": "."' in planning["composition_summary"]["patches"][0]["content_after"]

    def writer(path: str, content: str) -> dict[str, object]:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "file": path, "bytes_written": len(content.encode("utf-8"))}

    (tmp_path / "tsconfig.json").write_text(base_files["tsconfig.json"], encoding="utf-8")
    run_result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-tsconfig-rootdir",
            workspace=str(tmp_path),
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            allowed_paths=("tsconfig.json",),
        ),
        writer=writer,
    )

    assert run_result.ok is True
    assert run_result.receipts
    assert '"rootDir": "."' in (tmp_path / "tsconfig.json").read_text(encoding="utf-8")


def test_public_javascript_dom_global_runtime_guard_covers_browser_bundle_start() -> None:
    source_tool = js_syntax.JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "> sample@0.1.0 start\n> npm run build && node dist/web.js\n"
        "file:///tmp/factory-bench/sample/dist/web.js:362\n"
        '  if (document.readyState === "loading") {\n'
        "  ^\n\n"
        "ReferenceError: document is not defined\n"
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "package.json": (
                    '{"type":"module","main":"dist/web.js","scripts":{'
                    '"build":"esbuild src/web.ts --bundle --outfile=dist/web.js",'
                    '"start":"npm run build && node dist/web.js"}}\n'
                ),
                "src/web.ts": (
                    "function bootstrap(): void {\n"
                    '  document.getElementById("app");\n'
                    "}\n\n"
                    "function whenReady(): void {\n"
                    '  if (document.readyState === "loading") {\n'
                    '    document.addEventListener("DOMContentLoaded", bootstrap, { once: true });\n'
                    "  } else {\n"
                    "    bootstrap();\n"
                    "  }\n"
                    "}\n\n"
                    "whenReady();\n"
                ),
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )
    coverage_payload = coverage.to_dict()
    planning_payload = planning_result.to_dict()

    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["uncovered_diagnostic_count"] == 0
    assert coverage_payload["items"][0]["matched_source_tools"] == [source_tool]
    assert planning_payload["ok"] is True
    assert planning_payload["planned"] is True
    assert planning_payload["plan_summary"]["rule_id"] == "javascript.dom_global_runtime_guard"
    assert planning_payload["composition_summary"]["changed_paths"] == ["src/web.ts"]
    content_after = planning_payload["composition_summary"]["patches"][0]["content_after"]
    assert 'if (typeof document !== "undefined") {' in content_after
    assert "  whenReady();" in content_after


def test_public_npm_script_contract_repairs_http_server_fixed_port_conflict() -> None:
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "> sample@0.1.0 start\n> npx --yes http-server . -p 8080 -c-1\n"
        "Error: listen EADDRINUSE: address already in use 0.0.0.0:8080"
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=js_syntax.NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
            base_files={
                "package.json": (
                    '{"type":"module","scripts":{'
                    '"build":"tsc -p tsconfig.json",'
                    '"start":"npx --yes http-server . -p 8080 -c-1",'
                    '"serve":"npx --yes http-server . --port 8080 -c-1"'
                    '},"devDependencies":{"typescript":"5.5.4"}}\n'
                ),
                "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n',
                "src/web.ts": "export function mount(): void {}\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    coverage_payload = coverage.to_dict()

    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["items"][0]["matched_source_tools"] == [js_syntax.NPM_SCRIPT_CONTRACT_SOURCE_TOOL]
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "javascript.npm_script_contract"
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert '"start": "npx --yes http-server . -p ${PORT:-0} -c-1"' in content_after
    assert '"serve": "npx --yes http-server . --port ${PORT:-0} -c-1"' in content_after
    assert "8080" not in content_after


def test_public_typescript_commonjs_package_type_covers_node_esm_runtime_error() -> None:
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "> sample@1.0.0 start\n> node dist/web.js\n"
        "file:///tmp/workspace/dist/web.js:3\n"
        'Object.defineProperty(exports, "__esModule", { value: true });\n'
        "                      ^\n\n"
        "ReferenceError: exports is not defined in ES module scope\n"
        "This file is being treated as an ES module because it has a '.js' file extension and "
        '"/tmp/workspace/package.json" contains "type": "module". '
        "To treat it as a CommonJS script, rename it to use the '.cjs' file extension."
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL,
            base_files={
                "package.json": '{"type":"module","scripts":{"start":"node dist/web.js"}}\n',
                "tsconfig.json": '{"compilerOptions":{"module":"CommonJS","outDir":"dist","rootDir":"src"}}\n',
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    coverage_payload = coverage.to_dict()
    matched_source_tools = coverage_payload["items"][0]["matched_source_tools"]

    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["uncovered_diagnostic_count"] == 0
    assert ts_syntax.TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL in matched_source_tools
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.commonjs_package_type"
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert '"type": "commonjs"' in content_after


def test_public_html_module_script_uses_tsconfig_rootdir_for_compiled_entrypoint() -> None:
    diagnostic = (
        "HTML module script references missing compiled JavaScript './dist/web.js' in index.html; "
        "TypeScript build emitted './dist/src/web.js'"
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
            base_files={
                "index.html": '<script type="module" src="./dist/web.js"></script>\n',
                "tsconfig.json": (
                    '{"compilerOptions":{"outDir":"dist","rootDir":"."},"include":["src/**/*.ts","tests/**/*.ts"]}\n'
                ),
                "src/web.ts": "console.log('web');\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "html.typescript_module_script"
    assert planning["composition_summary"]["patch_count"] == 1
    assert 'src="./dist/src/web.js"' in planning["composition_summary"]["patches"][0]["content_after"]


def test_public_html_module_script_rewrites_typescript_source_entrypoint() -> None:
    diagnostic = (
        "Artifact quality scan failed: HTML module script references TypeScript source "
        "'/src/engine/renderer.ts' in index.html; static entrypoints must load JavaScript"
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
            base_files={
                "index.html": '<div id="garden"></div>\n<script type="module" src="/src/engine/renderer.ts"></script>\n',
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "html.typescript_module_script"
    assert planning["composition_summary"]["patch_count"] == 1
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert 'src="dist/engine/renderer.js"' in repaired
    assert "/src/engine/renderer.ts" not in repaired


def test_public_typescript_html_container_selector_covers_html5_verifier_contract_mismatch() -> None:
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed (npm test):\n"
        "[FAIL] html :: htmlTag=true canvas=true container=false"
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL,
            base_files={
                "index.html": '<main id="market-app"><canvas id="board"></canvas></main>\n',
                "src/verify.ts": (
                    "export function verify(text: string): boolean {\n"
                    "  return /<canvas\\b/i.test(text) && /id=[\"'](market|stall|sim|app)[\"']/i.test(text);\n"
                    "}\n"
                ),
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    coverage_payload = coverage.to_dict()

    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["uncovered_diagnostic_count"] == 0
    assert coverage_payload["items"][0]["matched_source_tools"] == [
        ts_syntax.TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL
    ]
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.html_container_selector"
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "/id=[\"'][^\"']*(market|stall|sim|app)[^\"']*[\"']/i" in content_after


def test_public_typescript_dom_local_shim_cleanup_removes_generated_dom_shims() -> None:
    diagnostic = (
        "src/web.ts(48,25): error TS2339: Property 'createElement' does not exist on type "
        "'{ getElementById(id: string): HTMLElement | null; addEventListener(type: string, listener: () => void): void; }'.\n"
        "src/web.ts(86,34): error TS2740: Type 'HTMLCanvasElement' is missing the following "
        "properties from type 'HTMLCanvasElement': captureStream, toBlob, toDataURL, transferControlToOffscreen.\n"
    )
    web_source = (
        "declare const document: {\n"
        "  getElementById(id: string): HTMLElement | null;\n"
        "  addEventListener(type: string, listener: () => void): void;\n"
        "};\n"
        "\n"
        "interface HTMLElement {\n"
        "  textContent: string;\n"
        "  appendChild(child: HTMLElement): void;\n"
        "  children: HTMLCollection;\n"
        "}\n"
        "\n"
        "interface HTMLCanvasElement extends HTMLElement {\n"
        "  width: number;\n"
        "  height: number;\n"
        "  getContext(type: '2d'): CanvasRenderingContext2D | null;\n"
        "}\n"
        "\n"
        "interface CanvasRenderingContext2D {\n"
        "  clearRect(x: number, y: number, width: number, height: number): void;\n"
        "}\n"
        "\n"
        "interface HTMLCollection {\n"
        "  length: number;\n"
        "}\n"
        "\n"
        "const root = document.createElement('section');\n"
        "const canvas = document.getElementById('board') as HTMLCanvasElement;\n"
        "const query = root.querySelector('canvas');\n"
        "console.log(canvas.width, query);\n"
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL,
            base_files={
                "src/web.ts": web_source,
                "tsconfig.web.json": '{"compilerOptions":{"lib":["ES2022","DOM"]}}\n',
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    coverage_payload = coverage.to_dict()
    ts2740_item = next(item for item in coverage_payload["items"] if item["diagnostic"]["code"] == "typescript_ts2740")

    assert ts_syntax.TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL in ts2740_item["matched_source_tools"]
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.dom_local_shim_cleanup"
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "declare const document" not in content_after
    assert "interface HTMLCanvasElement" not in content_after
    assert "interface CanvasRenderingContext2D" not in content_after
    assert "document.createElement('section')" in content_after


def test_public_typescript_unresolved_identifier_repairs_array_length_type_assertion() -> None:
    source_tool = ts_syntax.TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL
    diagnostic = "src/models/Market.test.ts(4,20): error TS2304: Cannot find name 'ReputationDeltaInternal'."
    content = (
        "import { Reputation } from './Reputation';\n"
        "const rep = new Reputation();\n"
        "const snap = rep.snapshot();\n"
        "(snap.history as ReputationDeltaInternal[]).length = 0;\n"
    )

    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=(diagnostic,),
            base_files={"src/models/Market.test.ts": content},
        )
    )
    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={"src/models/Market.test.ts": content},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (source_tool,)
    assert planning_result["ok"] is True
    assert planning_result["planned"] is True
    assert planning_result["composition_summary"]["patch_count"] == 1
    content_after = planning_result["composition_summary"]["patches"][0]["content_after"]
    assert "(snap.history as unknown[]).length = 0;" in content_after


def test_public_typescript_unresolved_identifier_repairs_parameter_alias() -> None:
    source_tool = ts_syntax.TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL
    diagnostic = "src/engine/simulation.ts(10,8): error TS2304: Cannot find name 'newState'."
    content = "\n".join(
        [
            "export interface GardenState { moonPhase: number; humidity: number; tick: number; }",
            "",
            "export function tickGarden(state: GardenState): GardenState {",
            "  const newState = { ...state, tick: state.tick + 1 };",
            "  return newState;",
            "}",
            "",
            "export function getGardenSummary(state: GardenState): string {",
            "  return [",
            "    `${newState.moonPhase}`;",
            "    `${newState.humidity}`;",
            "    `${newState.tick}`;",
            "  ].join('\\n');",
            "}",
            "",
        ]
    )

    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={"src/engine/simulation.ts": content},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning_result["ok"] is True
    assert planning_result["planned"] is True
    assert planning_result["composition_summary"]["patch_count"] == 1
    content_after = planning_result["composition_summary"]["patches"][0]["content_after"]
    assert "`${state.moonPhase}`;" in content_after
    assert "`${newState.humidity}`;" in content_after


def test_public_typescript_expect_error_placement_moves_comment_to_error_line() -> None:
    source_tool = ts_syntax.TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL
    diagnostics = (
        "tests/behavior.test.ts(2,3): error TS2578: Unused '@ts-expect-error' directive.",
        "tests/behavior.test.ts(4,31): error TS2345: Argument of type '\"dragon\"' "
        "is not assignable to parameter of type 'FairyRole'.",
    )
    content = (
        "test('invalid role', () => {\n"
        "  // @ts-expect-error -- exercising runtime guard against unknown role\n"
        "  assert.throws(\n"
        "    () => new Fairy('x', 'X', 'dragon', { charm: 50, reliability: 50 }),\n"
        "    /unknown role/,\n"
        "  );\n"
        "});\n"
    )

    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=diagnostics,
            base_files={"tests/behavior.test.ts": content},
        )
    )
    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={"tests/behavior.test.ts": content},
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (source_tool,)
    assert planning_result["ok"] is True
    assert planning_result["planned"] is True
    assert planning_result["composition_summary"]["patch_count"] == 1
    content_after = planning_result["composition_summary"]["patches"][0]["content_after"]
    assert (
        "  assert.throws(\n    // @ts-expect-error -- exercising runtime guard against unknown role\n" in content_after
    )
    assert (
        "  // @ts-expect-error -- exercising runtime guard against unknown role\n  assert.throws" not in content_after
    )


def test_typescript_aggregate_diagnostics_expand_each_compiler_line() -> None:
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed (npm run build):\n"
        "tests/verify.test.ts(13,42): error TS2554: Expected 0 arguments, but got 1.\n"
        "tests/verify.test.ts(17,49): error TS2339: Property 'failures' does not exist on type 'VerifyReport'.\n"
        "tests/verify.test.ts(33,29): error TS7006: Parameter 'c' implicitly has an 'any' type.\n"
    )

    diagnostics = normalize_artifact_quality_errors([diagnostic])
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    coverage_payload = coverage.to_dict()

    assert [item.code for item in diagnostics] == [
        "typescript_ts2554",
        "typescript_ts2339",
        "typescript_ts7006",
    ]
    assert coverage_payload["total_diagnostics"] == 3
    assert [item["diagnostic"]["code"] for item in coverage_payload["items"]] == [
        "typescript_ts2554",
        "typescript_ts2339",
        "typescript_ts7006",
    ]


def test_typescript_member_alias_maps_verify_report_derived_members_to_results() -> None:
    diagnostic = (
        "tests/verify.test.ts(4,39): error TS2339: "
        "Property 'failures' does not exist on type 'VerifyReport'.\n"
        "tests/verify.test.ts(5,17): error TS2339: "
        "Property 'checks' does not exist on type 'VerifyReport'.\n"
        "tests/verify.test.ts(6,17): error TS2339: "
        "Property 'failures' does not exist on type 'VerifyReport'.\n"
        "tests/verify.test.ts(6,17): error TS2339: "
        "Property 'failures' does not exist on type 'VerifyReport'.\n"
    )
    base_files = {
        "src/verify.ts": (
            "export interface CheckResult {\n"
            "  readonly id: string;\n"
            "  readonly ok: boolean;\n"
            "}\n"
            "export interface VerifyReport {\n"
            "  readonly ok: boolean;\n"
            "  readonly results: ReadonlyArray<CheckResult>;\n"
            "  readonly summary: string;\n"
            "}\n"
        ),
        "tests/verify.test.ts": (
            "import { runVerification } from '../src/verify.js';\n"
            "const report = runVerification();\n"
            "console.log(report.ok);\n"
            "console.log(JSON.stringify(report.failures));\n"
            "console.log(report.checks.length);\n"
            "console.log(report.failures.some((failure) => failure.id === 'content_any'));\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["operation_count"] == 3
    assert planning["composition_summary"]["patch_count"] == 1
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "JSON.stringify(report.results.filter((result) => !result.ok))" in content_after
    assert "report.results.length" in content_after
    assert "report.results.filter((result) => !result.ok).some" in content_after
    assert "report.failures" not in content_after
    assert "report.checks" not in content_after


def test_typescript_member_alias_maps_generated_id_suffix_to_existing_id_member() -> None:
    diagnostic = (
        "src/main.ts(2,37): error TS2339: Property 'stallId' does not exist on type 'Stall'.\n"
        "src/main.ts(3,38): error TS2339: Property 'stallId' does not exist on type 'Stall'.\n"
    )
    base_files = {
        "src/models/Market.ts": (
            "export interface Stall {\n"
            "  readonly id: StallId;\n"
            "  readonly name: string;\n"
            "}\n"
            "export type StallId = string & { readonly __brand: 'StallId' };\n"
        ),
        "src/main.ts": (
            "const opened = market.openStall('north', 8);\n"
            "const a = opened.stall.stallId;\n"
            "const b = opened.stall!.stallId;\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "const a = opened.stall.id;" in content_after
    assert "const b = opened.stall!.id;" in content_after


def test_typescript_member_alias_runtime_repairs_structural_drift(tmp_path: Path) -> None:
    engine_dir = tmp_path / "src" / "engine"
    engine_dir.mkdir(parents=True)
    simulation_path = engine_dir / "simulation.ts"
    renderer_path = engine_dir / "renderer.ts"
    simulation_text = (
        "export interface Vec2 { x: number; y: number; }\n"
        "export interface Firefly {\n"
        "  position: Vec2;\n"
        "  brightness: number;\n"
        "}\n"
        "export interface Flower {\n"
        "  position: Vec2;\n"
        "  petalRadius: number;\n"
        "  hue: number;\n"
        "  saturation: number;\n"
        "  lightness: number;\n"
        "}\n"
        "export interface Moon {\n"
        "  position: Vec2;\n"
        "  intensity: number;\n"
        "}\n"
    )
    renderer_text = (
        "import type { Firefly, Flower, Moon } from './simulation.js';\n"
        "export function render(moon: Moon, flower: Flower, firefly: Firefly): string {\n"
        "  const moonGlow = moon.brightness;\n"
        "  const flowerSize = flower.size;\n"
        "  const flowerX = flower.x;\n"
        "  const flowerY = flower.y;\n"
        "  const flowerColor = flower.color;\n"
        "  const fireflyGlow = firefly.glow;\n"
        "  const fireflyX = firefly.x;\n"
        "  const fireflyY = firefly.y;\n"
        "  return `${moonGlow}:${flowerSize}:${flowerX}:${flowerY}:${flowerColor}:${fireflyGlow}:${fireflyX}:${fireflyY}`;\n"
        "}\n"
    )
    diagnostics = (
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/engine/renderer.ts(3,25): error TS2339: Property 'brightness' does not exist on type 'Moon'.\n"
        "src/engine/renderer.ts(4,29): error TS2339: Property 'size' does not exist on type 'Flower'.\n"
        "src/engine/renderer.ts(5,27): error TS2339: Property 'x' does not exist on type 'Flower'.\n"
        "src/engine/renderer.ts(6,27): error TS2339: Property 'y' does not exist on type 'Flower'.\n"
        "src/engine/renderer.ts(7,31): error TS2339: Property 'color' does not exist on type 'Flower'.\n"
        "src/engine/renderer.ts(8,31): error TS2339: Property 'glow' does not exist on type 'Firefly'.\n"
        "src/engine/renderer.ts(9,29): error TS2339: Property 'x' does not exist on type 'Firefly'.\n"
        "src/engine/renderer.ts(10,29): error TS2339: Property 'y' does not exist on type 'Firefly'.",
    )
    simulation_path.write_text(simulation_text, encoding="utf-8")
    renderer_path.write_text(renderer_text, encoding="utf-8")
    writes: list[str] = []
    edits: list[str] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        target = tmp_path / path
        target.write_text(updated, encoding="utf-8")
        writes.append(path)
        return {"ok": True}

    def editor(operation: RepairOperation) -> dict[str, object]:
        assert operation.expected is not None
        assert operation.replacement is not None
        assert operation.span_start is not None
        assert operation.span_end is not None
        target = tmp_path / operation.path
        current = target.read_text(encoding="utf-8")
        if current[operation.span_start : operation.span_end] != operation.expected:
            return {"ok": False, "error": "expected text not found"}
        target.write_text(
            current[: operation.span_start] + operation.replacement + current[operation.span_end :],
            encoding="utf-8",
        )
        edits.append(operation.operation_id)
        return {"ok": True}

    result = run_runtime_repair(
        source_tool=ts_syntax.TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
        workspace=tmp_path,
        base_files={
            "src/engine/simulation.ts": simulation_text,
            "src/engine/renderer.ts": renderer_text,
        },
        artifact_quality_errors=diagnostics,
        writer=writer,
        editor=editor,
        allowed_paths=("src/engine/renderer.ts",),
    )

    assert result.ok is True
    assert writes == []
    assert edits
    repaired = renderer_path.read_text(encoding="utf-8")
    assert "moon.intensity" in repaired
    assert "flower.petalRadius" in repaired
    assert "flower.position.x" in repaired
    assert "flower.position.y" in repaired
    assert (
        "`hsl(${flower.hue}, ${Math.round(flower.saturation * 100)}%, ${Math.round(flower.lightness * 100)}%)`"
        in repaired
    )
    assert "firefly.brightness" in repaired
    assert "firefly.position.x" in repaired
    assert "firefly.position.y" in repaired
    assert "moon.brightness" not in repaired
    assert "flower.size" not in repaired
    assert "firefly.glow" not in repaired
    assert result.execution_result is not None
    receipt = result.execution_result.receipt
    assert receipt.source_tool == ts_syntax.TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL
    assert receipt.files_changed == ("src/engine/renderer.ts",)


def test_typescript_private_constructor_access_repairs_exported_class_factory_new_expression() -> None:
    diagnostics = (
        "src/models/Fairy.ts(14,10): error TS2673: Constructor of class 'Fairy' "
        "is private and only accessible within the class declaration.",
    )
    base_files = {
        "src/models/Fairy.ts": (
            "export interface FairyProfile { readonly name: string; }\n"
            "export class Fairy {\n"
            "  private readonly name: string;\n"
            "\n"
            "  private constructor(profile: FairyProfile) {\n"
            "    this.name = profile.name;\n"
            "  }\n"
            "\n"
            "  public label(): string {\n"
            "    return this.name;\n"
            "  }\n"
            "}\n"
            "export function createFairy(profile: FairyProfile): Fairy {\n"
            "  return new Fairy(profile);\n"
            "}\n"
        )
    }

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=diagnostics))
    assert coverage.items[0].known_rule_matched is True
    assert ts_syntax.TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL in coverage.items[0].matched_source_tools

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.private_constructor_access"
    assert planning["plan_summary"]["operation_count"] == 1
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "  constructor(profile: FairyProfile) {" in repaired
    assert "private constructor" not in repaired

    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            source_tools=(ts_syntax.TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL,),
        )
    )
    assert probe.status == "covered_plannable"
    assert probe.plannable_source_tools == (ts_syntax.TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL,)


def test_typescript_private_constructor_access_non_exported_class_is_covered_unplannable() -> None:
    diagnostics = (
        "src/models/Fairy.ts(5,10): error TS2673: Constructor of class 'Fairy' "
        "is private and only accessible within the class declaration.",
    )
    base_files = {
        "src/models/Fairy.ts": (
            "class Fairy {\n"
            "  private constructor() {}\n"
            "}\n"
            "export function createFairy(): Fairy {\n"
            "  return new Fairy();\n"
            "}\n"
        )
    }

    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            source_tools=(ts_syntax.TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL,),
        )
    )

    assert probe.status == "coverage_matched_but_unplannable"
    assert probe.plannable_source_tools == ()
    assert probe.covered_unplannable_source_tools == (ts_syntax.TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL,)


def test_typescript_duplicate_export_import_binding_repairs_barrel_ts2300() -> None:
    diagnostic = (
        "src/index.ts(9,3): error TS2300: Duplicate identifier Market.\n"
        "src/index.ts(14,10): error TS2300: Duplicate identifier Fairy.\n"
        "src/index.ts(16,3): error TS2300: Duplicate identifier Inventory.\n"
        "src/index.ts(21,3): error TS2300: Duplicate identifier Reputation.\n"
    )
    index_text = (
        "export {\n"
        "  Market,\n"
        "  createMarket,\n"
        "  type StallId,\n"
        '} from "./models/Market";\n'
        'export { Fairy, type FairyId } from "./models/Fairy";\n'
        "export {\n"
        "  Inventory,\n"
        "  type InventoryEntry,\n"
        '} from "./models/Inventory";\n'
        "export {\n"
        "  Reputation,\n"
        "  type ReputationTier,\n"
        '} from "./models/Reputation";\n'
        'import { Market } from "./models/Market";\n'
        'import { Fairy } from "./models/Fairy";\n'
        'import { Inventory } from "./models/Inventory";\n'
        'import { Reputation } from "./models/Reputation";\n'
        "export interface Snapshot {\n"
        '  readonly reputationTier: Reputation["tier"];\n'
        "}\n"
        "export { Market, Fairy, Inventory, Reputation };\n"
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
            base_files={"src/index.ts": index_text},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    coverage_payload = coverage.to_dict()

    assert coverage_payload["covered_diagnostic_count"] == 4
    assert "typescript.duplicate_export_import_binding" in coverage_payload["items"][0]["matched_rule_ids"]
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.duplicate_export_import_binding"
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "  Market,\n  createMarket" not in repaired
    assert "  createMarket,\n  type StallId," in repaired
    assert "export { Fairy, type FairyId } from" not in repaired
    assert "export { type FairyId } from" in repaired
    assert "export { Market, Fairy, Inventory, Reputation };" in repaired


def test_typescript_duplicate_export_import_binding_repairs_type_reexports_ts2300() -> None:
    diagnostic = (
        "src/index.ts(13,15): error TS2300: Duplicate identifier 'Market'.\n"
        "src/index.ts(14,15): error TS2300: Duplicate identifier 'Fairy'.\n"
        "src/index.ts(15,15): error TS2300: Duplicate identifier 'Inventory'.\n"
        "src/index.ts(16,15): error TS2300: Duplicate identifier 'Reputation'.\n"
        "src/index.ts(64,10): error TS2300: Duplicate identifier 'Market'.\n"
    )
    index_text = (
        'import { Market } from "./models/Market";\n'
        'import { Fairy } from "./models/Fairy";\n'
        'import { Inventory } from "./models/Inventory";\n'
        'import { Reputation } from "./models/Reputation";\n'
        "\n"
        'export type { Market, MarketSnapshot } from "./models/Market";\n'
        'export type { Fairy, FairyRole } from "./models/Fairy";\n'
        'export type { Inventory, InventoryItem, StockUnit } from "./models/Inventory";\n'
        'export type { Reputation, ReputationTier } from "./models/Reputation";\n'
        "\n"
        "export interface FairyMarketBundle {\n"
        "  readonly market: Market;\n"
        "  readonly inventory: Inventory;\n"
        "  readonly reputation: Reputation;\n"
        "  readonly fairys: ReadonlyArray<Fairy>;\n"
        "}\n"
        "\n"
        "export { Market, Fairy, Inventory, Reputation };\n"
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
            base_files={"src/index.ts": index_text},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.duplicate_export_import_binding"
    assert planning["composition_summary"]["changed_paths"] == ["src/index.ts"]
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert 'export type { Market, MarketSnapshot } from "./models/Market";' not in repaired
    assert 'export type { MarketSnapshot } from "./models/Market";' in repaired
    assert 'export type { FairyRole } from "./models/Fairy";' in repaired
    assert 'export type { InventoryItem, StockUnit } from "./models/Inventory";' in repaired
    assert 'export type { ReputationTier } from "./models/Reputation";' in repaired
    assert "export { Market, Fairy, Inventory, Reputation };" in repaired


def test_typescript_duplicate_export_import_binding_repairs_barrel_value_and_type_reexports() -> None:
    diagnostic = (
        "src/index.ts(17,10): error TS2300: Duplicate identifier 'Fairy'.\n"
        "src/index.ts(18,51): error TS2300: Duplicate identifier 'Fairy'."
    )
    index_text = (
        'export { Market } from "./models/Market";\n'
        'export type { MarketId } from "./models/Market";\n'
        'export { Fairy } from "./models/Fairy";\n'
        'export type { FairyId, FairySpecialty, FairyRole, Fairy } from "./models/Fairy";\n'
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
            base_files={"src/index.ts": index_text},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert 'export { Fairy } from "./models/Fairy";' in repaired
    assert 'export type { FairyId, FairySpecialty, FairyRole } from "./models/Fairy";' in repaired
    assert "FairyRole, Fairy }" not in repaired


def test_typescript_export_ambiguity_adds_explicit_reexport_for_ts2308() -> None:
    diagnostic = (
        'src/index.ts(2,1): error TS2308: Module "./models/A" has already exported '
        "a member named 'SharedId'. Consider explicitly re-exporting to resolve the ambiguity."
    )
    index_text = 'export * from "./models/A";\nexport * from "./models/B";\n'
    base_files = {
        "src/index.ts": index_text,
        "src/models/A.ts": 'export type SharedId = string & { readonly __brand: "A" };\nexport const makeA = () => 1;\n',
        "src/models/B.ts": 'export type SharedId = string & { readonly __brand: "B" };\nexport const makeB = () => 2;\n',
    }

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_EXPORT_AMBIGUITY_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    coverage_payload = coverage.to_dict()

    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["items"][0]["coverage_status"] == "executable_runtime"
    assert "typescript.export_ambiguity" in coverage_payload["items"][0]["matched_rule_ids"]
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.export_ambiguity"
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert 'export * from "./models/A";\nexport type { SharedId } from "./models/A";' in repaired
    assert 'export * from "./models/B";' in repaired


def test_typescript_duplicate_export_import_binding_repairs_local_type_export_duplicates() -> None:
    diagnostic = (
        "src/index.ts(19,8): error TS2300: Duplicate identifier 'FairyId'.\n"
        "src/index.ts(20,8): error TS2300: Duplicate identifier 'FairyRole'.\n"
        "src/index.ts(33,8): error TS2300: Duplicate identifier 'ReputationTier'.\n"
        "src/index.ts(183,15): error TS2300: Duplicate identifier 'FairyId'.\n"
        "src/index.ts(183,24): error TS2300: Duplicate identifier 'FairyRole'.\n"
        "src/index.ts(183,35): error TS2300: Duplicate identifier 'ReputationTier'.\n"
    )
    index_text = (
        "export {\n"
        "  Fairy,\n"
        "  createFairy,\n"
        "  type FairyId,\n"
        "  type FairyRole,\n"
        "  type FairyRosterEntry,\n"
        '} from "./models/Fairy";\n'
        "export {\n"
        "  Reputation,\n"
        "  createReputation,\n"
        "  type ReputationTier,\n"
        "  type ReputationEvent,\n"
        "  type ReputationReport,\n"
        '} from "./models/Reputation";\n'
        'import { Fairy, type FairyId, type FairyRole } from "./models/Fairy";\n'
        'import { Reputation, type ReputationTier } from "./models/Reputation";\n'
        "export function createDemoMarket(): void {}\n"
        "export type { FairyId, FairyRole, ReputationTier };\n"
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
            base_files={"src/index.ts": index_text},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.duplicate_export_import_binding"
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "type FairyId," in repaired
    assert "type FairyRole," in repaired
    assert "type ReputationTier," in repaired
    assert "export type { FairyId, FairyRole, ReputationTier };" not in repaired


def test_typescript_branded_literal_cast_repairs_string_literal_id_assignments() -> None:
    diagnostics = (
        "src/main.ts(4,29): error TS2345: Argument of type 'string' is not assignable to parameter of type 'MarketId'.\n"
        "  Type 'string' is not assignable to type '{ readonly __brand: \"MarketId\"; }'.",
        "src/main.ts(6,5): error TS2322: Type 'string' is not assignable to type 'FairyId'.\n"
        "  Type 'string' is not assignable to type '{ readonly __brand: \"FairyId\"; }'.",
    )
    base_files = {
        "src/models/Market.ts": 'export type MarketId = string & { readonly __brand: "MarketId" };\n',
        "src/models/Fairy.ts": (
            'export type FairyId = string & { readonly __brand: "FairyId" };\n'
            "export interface FairyRole { readonly id: FairyId; }\n"
        ),
        "src/main.ts": (
            'import { Market } from "./models/Market";\n'
            'import type { FairyRole } from "./models/Fairy";\n'
            "\n"
            'const market = new Market("street-corner-fairy-market-01", "街角妖精市集");\n'
            "const moonFairy: FairyRole = {\n"
            '  id: "fairy-001",\n'
            "};\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_BRANDED_LITERAL_CAST_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.branded_literal_cast"
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert 'import type { MarketId } from "./models/Market";' in repaired
    assert 'import type { FairyId } from "./models/Fairy";' in repaired
    assert '"street-corner-fairy-market-01" as MarketId' in repaired
    assert '"fairy-001" as FairyId' in repaired


def test_typescript_literal_union_value_facade_repairs_ts2693_enum_like_usage() -> None:
    diagnostics = (
        "src/main.ts(2,17): error TS2693: 'FairyArchetype' only refers to a type, but is being used as a value here.",
        "src/web.ts(2,17): error TS2693: 'FairyArchetype' only refers to a type, but is being used as a value here.",
    )
    base_files = {
        "src/models/Fairy.ts": (
            "export type FairyArchetype =\n"
            '  "MoonWeaver" | "SunSmith" | "MistRunner";\n'
            "export function createFairy(archetype: FairyArchetype) { return { archetype }; }\n"
        ),
        "src/main.ts": (
            'import { createFairy, FairyArchetype } from "./models/Fairy";\n'
            "const fairy = createFairy(FairyArchetype.MoonWeaver);\n"
        ),
        "src/web.ts": (
            'import { createFairy, FairyArchetype } from "./models/Fairy";\n'
            "const fairy = createFairy(FairyArchetype.MistRunner);\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.literal_union_value_facade"
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "export const FairyArchetype = {" in repaired
    assert '  MoonWeaver: "MoonWeaver",' in repaired
    assert '  MistRunner: "MistRunner",' in repaired
    assert "export type FairyArchetype = (typeof FairyArchetype)[keyof typeof FairyArchetype];" in repaired

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=diagnostics))
    assert coverage.items[0].known_rule_matched is True
    assert ts_syntax.TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL in coverage.items[0].matched_source_tools

    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            source_tools=(ts_syntax.TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL,),
        )
    )
    assert probe.status == "covered_plannable"
    assert probe.plannable_source_tools == (ts_syntax.TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL,)


def test_typescript_missing_export_without_declaration_is_covered_unplannable() -> None:
    diagnostics = ("src/main.ts(1,10): error TS2305: Module '\"./product\"' has no exported member 'GardenSimulator'.",)
    base_files = {
        "src/main.ts": "import { GardenSimulator } from './product';\nnew GardenSimulator().report();\n",
        "src/product.ts": "export function gardenReport(): string {\n  return 'ok';\n}\n",
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["patch_count"] == 0

    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            source_tools=(ts_syntax.TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,),
        )
    )

    assert probe.status == "coverage_matched_but_unplannable"
    assert probe.plannable_source_tools == ()
    assert probe.covered_unplannable_source_tools == (ts_syntax.TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,)


def test_typescript_missing_export_uses_ts2724_suggestion_alias() -> None:
    diagnostics = (
        "src/index.ts(1,10): error TS2724: '\"./models/humidity\"' has no exported member named "
        "'IHumidityState'. Did you mean 'HumidityState'?",
    )
    base_files = {
        "src/index.ts": (
            "import { IHumidityState } from './models/humidity';\n"
            "const state: IHumidityState = new IHumidityState();\n"
            "export { state };\n"
        ),
        "src/models/humidity.ts": "export class HumidityState {\n  public level = 60;\n}\n",
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.missing_export"
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "export { HumidityState as IHumidityState };" in repaired
    assert "export type IHumidityState = any;" not in repaired


def test_typescript_missing_export_aliases_similar_function_from_unresolved_symbol() -> None:
    diagnostics = (
        "Artifact quality scan failed: unresolved import symbol 'runAllChecks' "
        "from '../src/verify' in tests/verify.test.ts (sibling module does not define it)",
    )
    base_files = {
        "tests/verify.test.ts": 'import { runAllChecks } from "../src/verify";\nconst result = runAllChecks();\n',
        "src/verify.ts": "function runAll(): string[] {\n  return [];\n}\n",
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "export { runAll as runAllChecks };" in repaired


def test_typescript_missing_export_does_not_alias_constructed_symbol_to_enum() -> None:
    diagnostics = (
        "src/index.ts(1,10): error TS2305: Module '\"./models/moonphase\"' has no exported member 'MoonPhaseModel'.",
    )
    base_files = {
        "src/index.ts": (
            "import { MoonPhaseModel } from './models/moonphase';\n"
            "const moon = new MoonPhaseModel();\n"
            "moon.getState();\n"
        ),
        "src/models/moonphase.ts": "export enum MoonPhase {\n  New,\n  Full,\n}\n",
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["patch_count"] == 0


def test_typescript_ts2459_declares_locally_reexports_imported_type() -> None:
    diagnostics = (
        "src/index.ts(7,8): error TS2459: Module '\"./models/Fairy\"' declares "
        "'FairyMood' locally, but it is not exported.",
    )
    base_files = {
        "src/index.ts": (
            'export { createFairy, type FairyMood } from "./models/Fairy";\n'
            "export function boot(): string { return 'ok'; }\n"
        ),
        "src/models/Fairy.ts": (
            "import {\n"
            "  type Fairy,\n"
            "  type FairyMood,\n"
            '} from "./types";\n'
            "\n"
            "export function createFairy(mood: FairyMood): Fairy {\n"
            "  return { mood };\n"
            "}\n"
        ),
        "src/models/types.ts": (
            'export type FairyMood = "cheerful" | "neutral" | "grumpy";\n'
            "export interface Fairy { readonly mood: FairyMood; }\n"
        ),
    }

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=diagnostics))
    assert coverage.items[0].known_rule_matched is True
    assert ts_syntax.TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL in coverage.items[0].matched_source_tools

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.missing_export"
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert 'export type { FairyMood } from "./types";' in repaired

    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            source_tools=(ts_syntax.TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,),
        )
    )
    assert probe.status == "covered_plannable"
    assert probe.plannable_source_tools == (ts_syntax.TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,)


def test_typescript_reexported_type_binding_runtime_adds_local_import(tmp_path: Path) -> None:
    (tmp_path / "src" / "domain").mkdir(parents=True)
    (tmp_path / "src" / "domain" / "firefly.ts").write_text(
        "export interface Firefly { id: string; }\n",
        encoding="utf-8",
    )
    relative_path = "src/index.ts"
    original = (
        'export { Firefly } from "./domain/firefly";\nexport interface GardenSnapshot {\n  fireflies: Firefly[];\n}\n'
    )
    target = tmp_path / relative_path
    target.write_text(original, encoding="utf-8")
    writes: list[str] = []
    edits: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append(path)
        raise AssertionError("reexported type binding repair must prefer edit_file over write_file")

    def editor(operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert operation.span_start is not None
        assert operation.span_end is not None
        assert operation.expected is not None
        assert operation.replacement is not None
        assert current[operation.span_start : operation.span_end] == operation.expected
        target.write_text(
            current[: operation.span_start] + operation.replacement + current[operation.span_end :],
            encoding="utf-8",
        )
        edits.append(operation.path)
        return {"ok": True}

    result = run_runtime_repair(
        source_tool=ts_syntax.TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,
        workspace=tmp_path,
        base_files={relative_path: original},
        artifact_quality_errors=(
            "Artifact quality scan failed: TypeScript project typecheck failed: "
            "src/index.ts(3,14): error TS2304: Cannot find name 'Firefly'.",
        ),
        writer=writer,
        editor=editor,
        allowed_paths=(relative_path,),
    )

    assert result.ok is True
    assert writes == []
    assert edits == [relative_path]
    repaired = target.read_text(encoding="utf-8")
    assert 'import type { Firefly } from "./domain/firefly";' in repaired
    assert repaired.index("import type") < repaired.index("export { Firefly }")
    assert result.execution_result is not None
    record = result.execution_result.receipt.metadata["execution_records"][0]
    assert record["operation"] == "edit_file"


def test_typescript_value_used_as_type_repairs_exported_const_class_alias() -> None:
    diagnostics = (
        "src/models/Fairy.ts(4,24): error TS2749: 'Reputation' refers to a value, "
        "but is being used as a type here. Did you mean 'typeof Reputation'?",
        "src/models/Fairy.ts(8,32): error TS2749: 'Reputation' refers to a value, "
        "but is being used as a type here. Did you mean 'typeof Reputation'?",
    )
    base_files = {
        "src/models/Fairy.ts": (
            'import { Reputation } from "./Reputation";\n'
            "\n"
            "export interface FairyInit {\n"
            "  readonly reputation: Reputation;\n"
            "}\n"
            "\n"
            "class FairyImpl {\n"
            "  private readonly reputation: Reputation;\n"
            "}\n"
        ),
        "src/models/Reputation.ts": (
            "class ReputationImpl {\n  readonly score = 0;\n}\nexport const Reputation = ReputationImpl;\n"
        ),
    }

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=diagnostics))
    assert coverage.items[0].known_rule_matched is True
    assert ts_syntax.TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL in coverage.items[0].matched_source_tools

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.value_used_as_type"
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "readonly reputation: InstanceType<typeof Reputation>;" in repaired
    assert "private readonly reputation: InstanceType<typeof Reputation>;" in repaired

    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            source_tools=(ts_syntax.TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL,),
        )
    )
    assert probe.status == "covered_plannable"
    assert probe.plannable_source_tools == (ts_syntax.TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL,)


def test_typescript_value_used_as_type_without_class_alias_is_covered_unplannable() -> None:
    diagnostics = (
        "src/models/Fairy.ts(3,24): error TS2749: 'Reputation' refers to a value, "
        "but is being used as a type here. Did you mean 'typeof Reputation'?",
    )
    base_files = {
        "src/models/Fairy.ts": (
            'import { Reputation } from "./Reputation";\n'
            "export interface FairyInit {\n"
            "  readonly reputation: Reputation;\n"
            "}\n"
        ),
        "src/models/Reputation.ts": "export const Reputation = { score: 0 };\n",
    }

    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            source_tools=(ts_syntax.TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL,),
        )
    )

    assert probe.status == "coverage_matched_but_unplannable"
    assert probe.plannable_source_tools == ()
    assert probe.covered_unplannable_source_tools == (ts_syntax.TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL,)


def test_typescript_type_inference_required_diagnostics_are_metadata_only() -> None:
    diagnostics = (
        "src/factory.ts(10,5): error TS2353: Object literal may only specify known properties, "
        "and 'level' does not exist in type 'FairyRecord'.",
        "src/web.ts(119,24): error TS2344: Type 'string' does not satisfy the constraint '(...args: any) => any'.",
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=diagnostics))
    payload = coverage.to_dict()

    assert payload["covered_diagnostic_count"] == 2
    assert payload["executable_runtime_plan_diagnostic_count"] == 0
    assert payload["metadata_only_diagnostic_count"] == 2
    assert payload["uncovered_diagnostic_count"] == 0
    assert all(
        item["known_rule_matched"] is True
        and item["executable_runtime_plan_matched"] is False
        and item["metadata_only_match"] is True
        and item["coverage_status"] == "metadata_only_not_executable"
        and item["matched_source_tools"] == ["deterministic_typescript_type_inference_required_repair"]
        for item in payload["items"]
    )


def test_typescript_missing_member_without_type_evidence_is_unplannable() -> None:
    diagnostic = (
        "tests/usage.ts(3,8): error TS2339: Property 'label' does not exist on type 'Widget'.\n"
        "tests/usage.ts(4,8): error TS2339: Property 'render' does not exist on type 'Widget'.\n"
    )
    base_files = {
        "src/widget.ts": "export interface Widget {\n  id: string;\n}\n",
        "tests/usage.ts": (
            "import type { Widget } from '../src/widget.js';\n"
            "declare const widget: Widget;\n"
            "widget.label;\n"
            "widget.render();\n"
        ),
    }

    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,),
            artifact_quality_errors=(diagnostic,),
            base_files=base_files,
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert result.status == "coverage_matched_but_unplannable"
    assert result.items[0].status == "covered_unplannable"
    assert planning["ok"] is False
    assert planning["planned"] is False


def test_typescript_missing_member_infers_indexed_property_shape() -> None:
    diagnostic = "tests/usage.ts(3,19): error TS2339: Property 'items' does not exist on type 'Snapshot'."
    base_files = {
        "src/snapshot.ts": "export interface Snapshot {\n  id: string;\n}\n",
        "tests/usage.ts": (
            "import type { Snapshot } from '../src/snapshot.js';\n"
            "declare const snap: Snapshot;\n"
            "const entry = snap.items['first'];\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "items: Record<string, unknown>;" in content_after
    assert "items: unknown;" not in content_after


def test_typescript_missing_member_infers_numeric_class_property_from_arithmetic() -> None:
    diagnostic = "src/firefly.ts(3,31): error TS2339: Property 'brightness' does not exist on type 'Moon'."
    base_files = {
        "src/moon.ts": "export class Moon {\n  public getIllumination(): number {\n    return 1;\n  }\n}\n",
        "src/firefly.ts": (
            "import { Moon } from './moon.js';\n"
            "const moon = new Moon();\n"
            "const moonFactor = 0.4 + moon.brightness * 0.6;\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["composition_summary"]["changed_paths"] == ["src/moon.ts"]
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "public brightness: number = 0;" in content_after


def test_typescript_missing_member_infers_interface_string_and_number_properties() -> None:
    diagnostic = (
        "src/render.ts(4,23): error TS2339: Property 'size' does not exist on type 'Flower'.\n"
        "src/render.ts(5,21): error TS2339: Property 'color' does not exist on type 'Flower'.\n"
        "src/render.ts(6,19): error TS2339: Property 'baseX' does not exist on type 'Firefly'.\n"
        "src/render.ts(6,35): error TS2339: Property 'brightness' does not exist on type 'Firefly'."
    )
    base_files = {
        "src/simulation.ts": (
            "export interface Flower {\n  mood: number;\n}\n\nexport interface Firefly {\n  id: string;\n}\n"
        ),
        "src/render.ts": (
            "import type { Firefly, Flower } from './simulation.js';\n"
            "const flower = {} as Flower;\n"
            "const firefly = {} as Firefly;\n"
            "const radius = flower.size * 2;\n"
            "const fill = flower.color;\n"
            "const x = firefly.baseX + firefly.brightness;\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["composition_summary"]["changed_paths"] == ["src/simulation.ts"]
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "  size: number;" in content_after
    assert "  color: string;" in content_after
    assert "  baseX: number;" in content_after
    assert "  brightness: number;" in content_after


def test_typescript_missing_member_static_factory_without_return_evidence_is_unplannable() -> None:
    diagnostic = (
        "src/engine/simulation.ts(3,21): error TS2339: Property 'createRandom' does not exist on type 'typeof Flower'."
    )
    base_files = {
        "src/models/flower.ts": (
            "export class Flower {\n  constructor(name: string, color: string, isBlooming: boolean = true) {}\n}\n"
        ),
        "src/engine/simulation.ts": (
            "import { Flower } from '../models/flower';\n"
            "const flowers: Flower[] = [];\n"
            "flowers.push(Flower.createRandom(1));\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["patch_count"] == 0


def test_typescript_unknown_member_access_and_required_literal_cover_l2_07_residuals() -> None:
    diagnostics = (
        (
            "src/main.ts(3,19): error TS18046: 'snap.items' is of type 'unknown'.\n"
            "src/models/Market.ts(11,3): error TS2741: Property 'items' is missing in type "
            "'{ id: string; name: string; }' but required in type 'MarketSnapshot'.\n"
        ),
    )
    base_files = {
        "src/main.ts": (
            "import { snapshotMarket } from './models/Market.js';\n"
            "const snap = snapshotMarket(market);\n"
            "const entry = snap.items[inventoryId];\n"
        ),
        "src/models/Market.ts": (
            "export interface MarketSnapshot {\n"
            "  readonly id: string;\n"
            "  readonly name: string;\n"
            "  items: unknown;\n"
            "}\n\n"
            "interface MarketState {\n"
            "  readonly id: string;\n"
            "  readonly name: string;\n"
            "}\n\n"
            "export function snapshotMarket(market: MarketState): MarketSnapshot {\n"
            "  return {\n"
            "    id: market.id,\n"
            "    name: market.name,\n"
            "  };\n"
            "}\n"
        ),
    }

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=diagnostics))
    coverage_payload = coverage.to_dict()
    assert coverage_payload["covered_diagnostic_count"] == 2
    assert coverage_payload["uncovered_diagnostic_count"] == 0
    assert {
        "deterministic_typescript_unknown_member_access_repair",
        ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
    }.issubset({source_tool for item in coverage_payload["items"] for source_tool in item["matched_source_tools"]})

    unknown_planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()
    assert unknown_planning["ok"] is True
    assert unknown_planning["planned"] is True
    after_unknown = unknown_planning["composition_summary"]["patches"][0]["content_after"]
    assert "items: Record<string, unknown>;" in after_unknown

    literal_planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
            base_files={"src/main.ts": base_files["src/main.ts"], "src/models/Market.ts": after_unknown},
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()
    assert literal_planning["ok"] is True
    assert literal_planning["planned"] is True
    repaired = literal_planning["composition_summary"]["patches"][0]["content_after"]
    assert "    items: {}," in repaired
    assert "    items: {},\n  };" in repaired


def test_typescript_missing_member_repairs_inline_array_object_shape() -> None:
    diagnostic = (
        "src/main.ts(4,20): error TS2339: Property 'averageRating' does not exist on type "
        "'{ readonly tier: string; readonly count: number; }'."
    )
    main_text = (
        'import { summarizeMarket } from "./models/Market";\n'
        "\n"
        "const payload = summarizeMarket();\n"
        "console.log(payload.summary.reputationBreakdown[0].averageRating.toFixed(2));\n"
    )
    market_text = (
        "export interface MarketPayload {\n"
        "  readonly summary: {\n"
        "    readonly reputationBreakdown: ReadonlyArray<{\n"
        "      readonly tier: string;\n"
        "      readonly count: number;\n"
        "    }>;\n"
        "  };\n"
        "}\n"
        "\n"
        "const tierCounts = new Map<string, number>();\n"
        "\n"
        "export function summarizeMarket(): MarketPayload {\n"
        "  return {\n"
        "    summary: {\n"
        "      reputationBreakdown: [...tierCounts.entries()].map(([tier, count]) => ({\n"
        "        tier,\n"
        "        count,\n"
        "      })),\n"
        "    },\n"
        "  };\n"
        "}\n"
    )

    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,),
            artifact_quality_errors=(diagnostic,),
            base_files={"src/main.ts": main_text, "src/models/Market.ts": market_text},
        )
    )

    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 1
    planning = result.items[0].planning_result.to_dict()
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "      readonly averageRating: number;\n" in repaired
    assert "        averageRating: 0,\n" in repaired


def test_typescript_object_literal_missing_member_implementation_repairs_ts2739_ts2741() -> None:
    diagnostic = (
        "src/models/Inventory.ts(36,3): error TS2741: Property 'add' is missing in type "
        "'Readonly<{ stock: number; capacity: number; utilization: number; }>' but required in type 'Inventory'.\n"
        "src/models/Inventory.ts(37,3): error TS2739: Type "
        "'Readonly<{ stock: number; capacity: number; utilization: number; }>' is missing the following "
        "properties from type 'Inventory': add, take\n"
    )
    inventory_text = (
        "export interface Inventory {\n"
        "  readonly stock: number;\n"
        "  readonly capacity: number;\n"
        "  readonly utilization: number;\n"
        "  add(..._args: unknown[]): unknown;\n"
        "  take(..._args: unknown[]): unknown;\n"
        "}\n"
        "\n"
        "export function createInventory(input: { stock: number; capacity: number }): Inventory {\n"
        "  const stock = Math.max(0, input.stock);\n"
        "  const capacity = Math.max(stock, input.capacity);\n"
        "  const utilization = capacity === 0 ? 0 : stock / capacity;\n"
        "  return Object.freeze({\n"
        "    stock,\n"
        "    capacity,\n"
        "    utilization,\n"
        "  });\n"
        "}\n"
        "\n"
        "export function addStock(inventory: Inventory, amount: number): Inventory {\n"
        "  return createInventory({ stock: inventory.stock + amount, capacity: inventory.capacity });\n"
        "}\n"
        "\n"
        "export function takeStock(inventory: Inventory, amount: number): Inventory {\n"
        "  return createInventory({ stock: inventory.stock - amount, capacity: inventory.capacity });\n"
        "}\n"
    )
    base_files = {"src/models/Inventory.ts": inventory_text}

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    coverage_payload = coverage.to_dict()
    assert coverage_payload["covered_diagnostic_count"] == 2
    assert "typescript.object_literal_missing_member_implementation" in coverage_payload["items"][0]["matched_rule_ids"]
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.missing_member"
    assert planning["composition_summary"]["changed_paths"] == ["src/models/Inventory.ts"]
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "add(amount: number): Inventory;" in repaired
    assert "take(amount: number): Inventory;" in repaired
    assert "add(..._args: unknown[]): unknown;" not in repaired
    assert "take(..._args: unknown[]): unknown;" not in repaired
    assert "add(amount: number): Inventory {\n      return addStock(this, amount);\n    }," in repaired
    assert "take(amount: number): Inventory {\n      return takeStock(this, amount);\n    }," in repaired


def test_typescript_unused_parameter_repairs_ts6133_without_unused_import_misroute() -> None:
    diagnostic = "src/web.ts(36,33): error TS6133: 'rootId' is declared but its value is never read."
    source = (
        'export function mountMarketView(rootId: string = "app"): void {\n'
        '  const root = document.createElement("section");\n'
        "  document.body.appendChild(root);\n"
        "}\n"
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
            base_files={"src/web.ts": source},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    coverage_payload = coverage.to_dict()
    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["items"][0]["matched_rule_ids"] == ["typescript.unused_parameter"]
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.unused_parameter"
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert 'mountMarketView(_rootId: string = "app")' in repaired
    assert 'mountMarketView(rootId: string = "app")' not in repaired


def test_typescript_too_many_arguments_adds_rest_param_to_unique_zero_arg_function() -> None:
    diagnostic = (
        "tests/verify.test.ts(2,31): error TS2554: Expected 0 arguments, but got 2.\n"
        "tests/verify.test.ts(2,31): error TS2554: Expected 0 arguments, but got 2.\n"
    )
    base_files = {
        "src/verify.ts": "export function runVerification(): VerifyReport {\n  return verifyNow();\n}\n",
        "tests/verify.test.ts": (
            "import { runVerification } from '../src/verify.js';\n"
            "const report = runVerification(process.cwd(), { minFiles: 10 });\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["operation_count"] == 1
    assert planning["composition_summary"]["patch_count"] == 1
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "runVerification(..._args: unknown[]): VerifyReport" in content_after


def test_public_typescript_tsconfig_repair_plans_import_meta_module_option() -> None:
    source_tool = "deterministic_typescript_tsconfig_lib_repair"

    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "tsconfig.json": (
                    '{"compilerOptions":{"target":"ES2020","module":"commonjs",'
                    '"moduleResolution":"node","lib":["ES2020"]}}\n'
                )
            },
            artifact_quality_errors=(
                "src/main.ts(152,16): error TS1343: The 'import.meta' meta-property is only "
                "allowed when '--module' is es2020/es2022/esnext/system/node16/nodenext.",
            ),
            mode="shadow",
        )
    )
    payload = planning_result.to_dict()

    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["source_tool"] == source_tool
    assert payload["plan_summary"]["rule_id"] == "typescript.tsconfig_lib"
    assert payload["plan_summary"]["operation_count"] == 1
    assert payload["composition_summary"]["ok"] is True
    assert payload["composition_summary"]["changed_paths"] == ["tsconfig.json"]
    assert '"module": "ES2020"' in payload["composition_summary"]["patches"][0]["content_after"]


def test_public_typescript_tsconfig_repair_plans_dom_lib_for_console() -> None:
    source_tool = "deterministic_typescript_tsconfig_lib_repair"

    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "tsconfig.json": (
                    '{"compilerOptions":{"target":"ES2020","module":"ES2020",'
                    '"moduleResolution":"node","lib":["ES2020"]},"include":["src/**/*.ts"]}\n'
                ),
                "src/main.ts": "console.log('hello');\n",
            },
            artifact_quality_errors=(
                "Artifact quality scan failed: TypeScript project typecheck failed: "
                "src/main.ts(1,1): error TS2584: Cannot find name 'console'. "
                "Do you need to change your target library? Try changing the 'lib' compiler option to include 'dom'.",
            ),
            mode="shadow",
        )
    )
    payload = planning_result.to_dict()

    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["source_tool"] == source_tool
    assert payload["plan_summary"]["rule_id"] == "typescript.tsconfig_lib"
    assert payload["plan_summary"]["operation_count"] == 1
    assert payload["composition_summary"]["ok"] is True
    assert payload["composition_summary"]["changed_paths"] == ["tsconfig.json"]
    content_after = payload["composition_summary"]["patches"][0]["content_after"]
    assert '"lib": [' in content_after
    assert '"ES2020"' in content_after
    assert '"DOM"' in content_after


def test_public_typescript_tsconfig_repair_plans_es2021_lib_for_replace_all() -> None:
    source_tool = "deterministic_typescript_tsconfig_lib_repair"

    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "tsconfig.json": (
                    '{"compilerOptions":{"target":"ES2020","module":"ES2020",'
                    '"moduleResolution":"node","lib":["ES2020","DOM"]}}\n'
                ),
                "src/verify.ts": 'const x = "a\\\\b".replaceAll("\\\\", "/");\n',
            },
            artifact_quality_errors=(
                "src/verify.ts(201,40): error TS2550: Property 'replaceAll' does not exist "
                "on type 'string'. Do you need to change your target library? Try changing "
                "the 'lib' compiler option to 'es2021' or later.",
            ),
            mode="shadow",
        )
    )
    payload = planning_result.to_dict()

    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["source_tool"] == source_tool
    assert payload["plan_summary"]["rule_id"] == "typescript.tsconfig_lib"
    assert payload["plan_summary"]["operation_count"] == 2
    assert payload["composition_summary"]["ok"] is True
    assert payload["composition_summary"]["changed_paths"] == ["tsconfig.json"]
    content_after = payload["composition_summary"]["patches"][0]["content_after"]
    assert '"lib": [' in content_after
    assert '"ES2021"' in content_after
    assert '"DOM"' in content_after
    assert '"target": "ES2021"' in content_after


def test_public_repair_rust_aggregate_bindings_fail_closed_without_safe_plan(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "deterministic_rust_missing_fields_repair",
            "error[E0609]: no field `duration` on type `&Flight`\n"
            " --> src/lib.rs:8:22\n"
            "  |\n"
            '8 |     println!("{}", flight.duration);\n'
            "  |                      ^^^^^^^^ unknown field\n",
        ),
        (
            "deterministic_rust_post_repair",
            "error[E0433]: failed to resolve: use of unresolved module or unlinked crate `serde`\n",
        ),
    )
    writes: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append((path, content))
        raise AssertionError("unsafe rust missing-fields input must not write files")

    for source_tool, raw_error in cases:
        planning_result = plan_director_repair(
            PlanDirectorRepairCommandV1(
                source_tool=source_tool,
                base_files={"src/lib.rs": "pub struct Flight { pub name: String }\n"},
                artifact_quality_errors=(raw_error,),
                mode="shadow",
            )
        )
        planning_payload = planning_result.to_dict()

        assert planning_payload["ok"] is False
        assert planning_payload["planned"] is False
        assert planning_payload["source_tool"] == source_tool
        if source_tool == "deterministic_rust_post_repair":
            assert source_tool not in runtime_repair_source_tools()
            assert planning_payload["error_code"] == "unsupported_repair_source_tool"
            assert planning_payload["error_message"]
        else:
            assert source_tool in runtime_repair_source_tools()
            assert planning_payload["error_code"] is None
            assert planning_payload["error_message"] is None
        assert planning_payload["plan_summary"] is None
        if planning_payload["composition_summary"] is not None:
            assert planning_payload["composition_summary"]["ok"] is False

        run_result = run_director_repair(
            RunDirectorRepairCommandV1(
                task_id=f"task-{source_tool}",
                workspace=str(tmp_path),
                source_tool=source_tool,
                base_files={"src/lib.rs": "pub struct Flight { pub name: String }\n"},
                artifact_quality_errors=(raw_error,),
                allowed_paths=("src/lib.rs",),
            ),
            writer=writer,
        )

        assert run_result.ok is False
        assert run_result.error_code == (
            "unsupported_repair_source_tool"
            if source_tool == "deterministic_rust_post_repair"
            else "repair_not_planned"
        )
        assert run_result.receipts == ()
        assert run_result.metadata["planning"]["planned"] is False

        if source_tool == "deterministic_rust_missing_fields_repair":
            coverage_payload = (
                default_repair_rule_registry().coverage(normalize_artifact_quality_errors([raw_error])).to_dict()
            )
            coverage_item = coverage_payload["items"][0]
            assert coverage_item["metadata_only_match"] is False
            assert coverage_item["executable_runtime_plan_matched"] is True
            assert coverage_item["runtime_plan_rule_ids"] == ["rust.missing_struct_field_declaration"]
            assert coverage_item["coverage_status"] == "executable_runtime"

    assert writes == []


def test_public_rust_lib_root_facade_export_signal_runs_with_editor_only(tmp_path: Path) -> None:
    source_tool = "deterministic_rust_lib_root_facade_repair"
    raw_error = "AssertionError: lib.rs must expose generate_palette API for Rust lib root facade"
    base_files = {
        "src/lib.rs": "mod engine;\n",
        "src/engine.rs": "pub fn generate_palette() {}\n",
    }
    target = tmp_path / "src/lib.rs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(base_files["src/lib.rs"], encoding="utf-8")
    writes: list[tuple[str, str]] = []
    edits: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append((path, content))
        raise AssertionError("Rust lib-root facade export must not use write_file fallback")

    def editor(operation) -> dict[str, object]:
        edits.append(operation.operation_id)
        path = tmp_path / operation.path
        content = path.read_text(encoding="utf-8")
        start = int(operation.span_start)
        end = int(operation.span_end)
        assert content[start:end] == operation.expected
        path.write_text(content[:start] + str(operation.replacement) + content[end:], encoding="utf-8")
        return {"ok": True, "path": operation.path}

    assert source_tool in runtime_repair_source_tools()

    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=(raw_error,),
            mode="shadow",
        )
    )
    planning_payload = planning_result.to_dict()

    assert planning_payload["ok"] is True
    assert planning_payload["planned"] is True
    assert planning_payload["source_tool"] == source_tool
    assert planning_payload["error_code"] is None
    assert planning_payload["plan_summary"]["rule_id"] == "rust.lib_root_facade_export"
    assert planning_payload["composition_summary"]["ok"] is True

    run_result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-lib-root-facade-export",
            workspace=str(tmp_path),
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=(raw_error,),
            allowed_paths=("src/lib.rs", "src/engine.rs"),
        ),
        writer=writer,
        editor=editor,
    )

    assert run_result.ok is True
    assert run_result.error_code is None
    assert len(run_result.receipts) == 1
    assert writes == []
    assert len(edits) == 1
    assert target.read_text(encoding="utf-8") == "mod engine;\npub use crate::engine::generate_palette;\n"
    record = run_result.receipts[0].metadata["execution_records"][0]
    assert record["operation"] == "edit_file"
    assert record["span_based"] is True


def test_python_readme_required_token_rule_builds_append_plan() -> None:
    raw_error = "AssertionError: 'unittest' not found in README text : README missing required token: unittest"
    plan = build_python_readme_required_token_plan(
        base_files={
            "README.md": "# Demo\n\nExisting workflow notes.\n",
            "tests/test_product.py": "import unittest\n",
        },
        diagnostics=normalize_artifact_quality_errors([raw_error]),
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "python.readme_required_token"
    assert plan.source_tool == PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL
    assert plan.metadata["runtime_plan_scope"] == "existing_readme_append_only"
    assert plan.metadata["tokens"] == ["unittest"]
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == "README.md"
    assert operation.expected == ""
    assert "python -m unittest discover" in str(operation.replacement)
    assert operation.metadata["repair_kind"] == "python_readme_required_token"
    assert operation.metadata["edit_file_preferred"] is True
    assert operation.metadata["expected_context_before"] == "# Demo\n\nExisting workflow notes.\n"


def test_python_readme_required_token_rule_accepts_compact_missing_form() -> None:
    raw_error = "AssertionError: README missing: javac"
    plan = build_python_readme_required_token_plan(
        base_files={
            "README.md": "# Demo\n\nExisting workflow notes.\n",
            "src/Main.java": "public final class Main {}\n",
        },
        diagnostics=normalize_artifact_quality_errors([raw_error]),
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "python.readme_required_token"
    assert plan.source_tool == PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL
    assert plan.metadata["tokens"] == ["javac"]
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == "README.md"
    assert "javac" in str(operation.replacement)
    assert operation.metadata["repair_kind"] == "python_readme_required_token"


def test_public_python_readme_required_token_repair_runs_with_editor_only(tmp_path: Path) -> None:
    source_tool = PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL
    raw_error = (
        "FAIL: test_readme_documents_workflow (tests.test_product.ProductTests)\n"
        "AssertionError: 'unittest' not found in README text : README missing required token: unittest"
    )
    base_files = {
        "README.md": "# Demo\n\nUse the documented CLI workflow.\n",
        "tests/test_product.py": "import unittest\n",
    }
    readme = tmp_path / "README.md"
    readme.write_text(base_files["README.md"], encoding="utf-8")
    writes: list[tuple[str, str]] = []
    edits: list[str] = []

    coverage_payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(raw_error,))
    ).to_dict()
    coverage_item = coverage_payload["items"][0]
    assert coverage_item["known_rule_matched"] is True
    assert coverage_item["executable_runtime_plan_matched"] is True
    assert coverage_item["metadata_only_match"] is False
    assert coverage_item["matched_source_tools"] == [source_tool]
    assert coverage_item["runtime_plan_rule_ids"] == ["python.readme_required_token"]
    assert coverage_item["coverage_status"] == "executable_runtime"

    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=(raw_error,),
            mode="shadow",
        )
    )
    planning_payload = planning_result.to_dict()

    assert planning_payload["ok"] is True
    assert planning_payload["planned"] is True
    assert planning_payload["source_tool"] == source_tool
    assert planning_payload["plan_summary"]["rule_id"] == "python.readme_required_token"
    assert planning_payload["composition_summary"]["ok"] is True

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append((path, content))
        raise AssertionError("README required-token repair must prefer edit_file over write_file")

    def editor(operation: RepairOperation) -> dict[str, object]:
        edits.append(operation.operation_id)
        path = tmp_path / operation.path
        content = path.read_text(encoding="utf-8")
        start = int(operation.span_start or 0)
        end = int(operation.span_end or 0)
        assert content[start:end] == operation.expected
        path.write_text(content[:start] + str(operation.replacement) + content[end:], encoding="utf-8")
        return {"ok": True, "path": operation.path}

    run_result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-python-readme-required-token",
            workspace=str(tmp_path),
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=(raw_error,),
            allowed_paths=("README.md", "tests/test_product.py"),
        ),
        writer=writer,
        editor=editor,
    )

    assert run_result.ok is True
    assert run_result.error_code is None
    assert writes == []
    assert len(edits) == 1
    assert "python -m unittest discover" in readme.read_text(encoding="utf-8")
    receipt = run_result.receipts[0]
    assert receipt.rule_id == "python.readme_required_token"
    assert receipt.source_tool == source_tool
    assert receipt.files_changed == ("README.md",)
    record = receipt.metadata["execution_records"][0]
    assert record["operation"] == "edit_file"
    assert record["span_based"] is True


def test_public_repair_delete_file_without_deleter_fails_closed_with_receipt_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_tool = "deterministic_test_delete_file_repair"
    relative_path = "src/stale.ts"
    original = "export const stale = true;\n"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(original, encoding="utf-8")
    _install_delete_file_test_runtime_binding(monkeypatch, source_tool)

    def writer(path: str, content: str) -> dict[str, object]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-delete-no-deleter",
            workspace=str(tmp_path),
            source_tool=source_tool,
            base_files={relative_path: original},
            artifact_quality_errors=("test delete stale file",),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is False
    assert result.error_code == "repair_execution_failed"
    assert result.metadata["execution_error_code"] == "delete_file_requires_policy_gated_deleter"
    assert "policy-gated deleter" in str(result.metadata["execution_error"])
    assert target.read_text(encoding="utf-8") == original
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.status == "failed"
    assert receipt.metadata["error"].endswith(f"policy-gated deleter for {relative_path}")


def test_public_repair_delete_file_uses_policy_gated_deleter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_tool = "deterministic_test_delete_file_repair"
    relative_path = "src/stale.ts"
    original = "export const stale = true;\n"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(original, encoding="utf-8")
    _install_delete_file_test_runtime_binding(monkeypatch, source_tool)
    delete_calls: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    def deleter(path: str) -> dict[str, object]:
        delete_calls.append(path)
        (tmp_path / path).unlink()
        return {"ok": True, "file": path, "operation": "delete_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-delete-with-deleter",
            workspace=str(tmp_path),
            source_tool=source_tool,
            base_files={relative_path: original},
            artifact_quality_errors=("test delete stale file",),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        deleter=deleter,
    )

    assert result.ok is True
    assert delete_calls == [relative_path]
    assert not target.exists()
    receipt = result.receipts[0]
    assert receipt.before_hashes[relative_path] == sha256_text(original)
    assert receipt.after_hashes[relative_path] == FILE_ABSENT_HASH
    record = receipt.metadata["execution_records"][0]
    assert record["operation"] == "delete_file"
    assert record["rollback_strategy"] == "write_file_full_restore"


def test_runtime_dispatcher_bindings_match_registry_runtime_plan_flags() -> None:
    bindings_by_tool = {binding["source_tool"]: binding for binding in runtime_repair_bindings()}
    runtime_rules_by_tool: dict[str, list[RepairRuleDefinition]] = {}
    for rule in default_repair_rule_registry().rules():
        if rule.runtime_plan_available:
            runtime_rules_by_tool.setdefault(rule.source_tool, []).append(rule)

    assert set(bindings_by_tool) == set(runtime_rules_by_tool)
    for source_tool, binding in bindings_by_tool.items():
        rules = runtime_rules_by_tool[source_tool]
        assert binding["language"] in {rule.language for rule in rules}


def test_cpp_include_path_rule_builds_canonical_plan_without_diagnostics() -> None:
    header = "#pragma once\n"
    content = '#include "src/models/postcard.hpp"\n#include <string>\n'

    repaired = repair_cpp_include_paths_text(
        path="./src/engine/generator.cpp",
        text=content,
        header_paths=("src/models/postcard.hpp", "src/engine/generator.hpp"),
    )
    plan = build_cpp_include_path_plan(
        base_files={
            "./src/models/postcard.hpp": header,
            "./src/engine/generator.hpp": header,
            "./src/engine/generator.cpp": content,
        },
        diagnostics=(),
        mode="shadow",
    )

    assert '#include "../models/postcard.hpp"' in repaired
    assert plan is not None
    assert plan.rule_id == "cpp.include_path"
    assert plan.source_tool == "deterministic_cpp_include_path_repair"
    assert plan.priority == 0
    assert len(plan.operations) == 1
    assert plan.operations[0].path == "src/engine/generator.cpp"
    assert plan.operations[0].metadata["repair_kind"] == "cpp_include_path"


def test_cpp_standard_include_rule_builds_canonical_plan_without_diagnostics() -> None:
    content = "#pragma once\nnamespace demo { std::uint32_t seed(); }\n"

    repaired = repair_cpp_missing_standard_includes_text(content)
    plan = build_cpp_standard_include_plan(
        base_files={"./src/models/seed.hpp": content},
        diagnostics=(),
        mode="shadow",
    )

    assert "#include <cstdint>" in repaired
    assert plan is not None
    assert plan.rule_id == "cpp.standard_include"
    assert plan.source_tool == "deterministic_cpp_standard_include_repair"
    assert plan.priority == 0
    assert plan.depends_on == ("cpp.include_path",)
    assert len(plan.operations) == 1
    assert plan.operations[0].path == "src/models/seed.hpp"
    assert plan.operations[0].metadata["repair_kind"] == "cpp_standard_include"


def test_cpp_missing_private_members_rule_builds_canonical_plan_without_diagnostics() -> None:
    content = (
        "#pragma once\n"
        "#include <string>\n"
        "namespace demo {\n"
        "class Poem {\n"
        "public:\n"
        "    const std::string& title() const noexcept { return title_; }\n"
        "};\n"
        "}\n"
    )

    repaired = repair_cpp_missing_private_members_text(content)
    plan = build_cpp_missing_private_members_plan(
        base_files={"./src/models/poem.hpp": content},
        diagnostics=(),
        mode="shadow",
    )

    assert "private:\n    std::string title_;" in repaired
    assert plan is not None
    assert plan.rule_id == "cpp.missing_private_members"
    assert plan.source_tool == "deterministic_cpp_missing_private_members_repair"
    assert plan.priority == 1
    assert plan.depends_on == ("cpp.standard_include",)
    assert len(plan.operations) == 1
    assert plan.operations[0].path == "src/models/poem.hpp"
    assert plan.operations[0].metadata["repair_kind"] == "cpp_missing_private_members"


def test_cpp_placeholder_declaration_rule_builds_canonical_plan_without_diagnostics() -> None:
    content = (
        "#pragma once\n"
        "namespace demo {\n"
        "class Generator {\n"
        "public:\n"
        "    std::render_return_type /* placeholder */ render_html() const = delete;\n"
        "};\n"
        "}\n"
    )

    repaired = repair_cpp_invalid_placeholder_declarations_text(content)
    plan = build_cpp_placeholder_declaration_plan(
        base_files={"./src/engine/generator.hpp": content},
        diagnostics=(),
        mode="shadow",
    )

    assert "std::render_return_type" not in repaired
    assert plan is not None
    assert plan.rule_id == "cpp.placeholder_declaration"
    assert plan.source_tool == "deterministic_cpp_placeholder_declaration_repair"
    assert plan.priority == 1
    assert len(plan.operations) == 1
    assert plan.operations[0].path == "src/engine/generator.hpp"
    assert plan.operations[0].metadata["repair_kind"] == "cpp_placeholder_declaration"


def test_cpp_struct_getter_field_access_rule_builds_canonical_plan_without_diagnostics() -> None:
    header = "#pragma once\nnamespace demo {\nstruct Postcard {\n    int poem;\n    int stamp;\n};\n}\n"
    source = (
        '#include "models/postcard.hpp"\n'
        "int main() {\n"
        "    demo::Postcard card{};\n"
        "    return card.get_poem() + card.stamp();\n"
        "}\n"
    )

    repaired = repair_cpp_struct_getter_field_access_text(text=source, field_names=("poem", "stamp"))
    plan = build_cpp_struct_getter_field_access_plan(
        base_files={"./src/models/postcard.hpp": header, "./src/main.cpp": source},
        diagnostics=(),
        mode="shadow",
    )

    assert "card.poem" in repaired
    assert "card.stamp" in repaired
    assert plan is not None
    assert plan.rule_id == "cpp.struct_getter_field_access"
    assert plan.source_tool == "deterministic_cpp_struct_getter_field_access_repair"
    assert plan.priority == 1
    assert len(plan.operations) == 1
    assert plan.operations[0].path == "src/main.cpp"
    assert plan.operations[0].metadata["repair_kind"] == "cpp_struct_getter_field_access"


def test_cpp_post_rule_builds_compile_smoke_plan_without_legacy_helper() -> None:
    header = "#pragma once\nnamespace demo { struct Poem {}; }\n"
    source = '#include "generator.hpp"\nvoid render() {\n    missing::legacy::Api value;\n}\n'
    main_source = '#include "models/poem.hpp"\nint main() {\n    missing::legacy::Api value;\n    return 0;\n}\n'

    repaired = repair_cpp_failing_smoke_translation_unit_text(
        path="./src/engine/generator.cpp",
        text=source,
        header_paths=("src/engine/generator.hpp", "src/models/poem.hpp"),
    )
    smoke_plan = build_cpp_failing_smoke_translation_unit_plan(
        base_files={
            "src/engine/generator.hpp": header,
            "src/engine/generator.cpp": source,
            "src/main.cpp": main_source,
            "src/models/poem.hpp": header,
        },
        diagnostics=(),
        mode="shadow",
    )
    post_plan = build_cpp_post_plan(
        base_files={
            "src/engine/generator.hpp": header,
            "src/engine/generator.cpp": source,
            "src/main.cpp": main_source,
            "src/models/poem.hpp": header,
        },
        diagnostics=(),
        mode="shadow",
    )

    assert '#include "generator.hpp"' in repaired
    assert "polaris_cpp_smoke_src_engine_generator_cpp" in repaired
    assert smoke_plan is not None
    assert smoke_plan.source_tool == "deterministic_cpp_post_repair"
    assert smoke_plan.operations[0].metadata["repair_kind"] == "cpp_failing_smoke_translation_unit"
    assert post_plan is not None
    assert post_plan.source_tool == "deterministic_cpp_post_repair"
    assert post_plan.metadata["adapter_post_helper_used"] is False
    assert "cpp.failing_smoke_translation_unit" in post_plan.metadata["aggregate_runtime_child_rules"]
    assert {operation.path for operation in post_plan.operations} == {
        "src/engine/generator.cpp",
        "src/main.cpp",
    }


def test_go_bare_import_string_rule_builds_canonical_plan_without_diagnostics() -> None:
    content = 'package main\n\n"fmt"\n\nfunc main() {}\n'

    repaired = repair_go_bare_import_strings_text(content)
    plan = build_go_bare_import_string_plan(
        base_files={"./cmd/app/main.go": content, "cmd/app/main_test.go": content},
        diagnostics=(),
        mode="shadow",
    )

    assert 'import "fmt"' in repaired
    assert plan is not None
    assert plan.rule_id == "go.bare_import_string"
    assert plan.source_tool == "deterministic_go_bare_import_string_repair"
    assert plan.priority == 0
    assert len(plan.operations) == 1
    assert plan.operations[0].path == "cmd/app/main.go"
    assert plan.operations[0].metadata["repair_kind"] == "go_bare_import_string"


def test_go_import_followup_rules_build_precise_plans_with_ordering_and_coverage() -> None:
    content = (
        'package main\n\nimport (\n    import "fmt"\n    "example.com/demo/pet-ascii/src/engine"\n    "src/models"\n)\n'
    )
    base_files = {
        "go.mod": "module example.com/demo\n",
        "cmd/app/main.go": content,
        "src/engine/engine.go": "package engine\n",
        "src/models/model.go": "package models\n",
    }

    nested_repaired = repair_go_nested_import_keywords_text(content)
    nested_plan = build_go_nested_import_plan(base_files=base_files, diagnostics=(), mode="shadow")
    bare_local_plan = build_go_bare_local_import_plan(base_files=base_files, diagnostics=(), mode="shadow")
    subpath_plan = build_go_subpath_import_plan(base_files=base_files, diagnostics=(), mode="shadow")
    coverage = (
        default_repair_rule_registry()
        .coverage(
            (
                RepairDiagnostic(
                    source="go test",
                    code="go_compile_error",
                    message="package src/models is not in std",
                    path="cmd/app/main.go",
                    raw="cmd/app/main.go:5:5: package src/models is not in std (/usr/local/go/src/src/models)",
                ),
                RepairDiagnostic(
                    source="go test",
                    code="go_compile_error",
                    message="no required module provides package example.com/demo/pet-ascii/src/engine",
                    path="cmd/app/main.go",
                    raw=(
                        "cmd/app/main.go:4:5: no required module provides package example.com/demo/pet-ascii/src/engine"
                    ),
                ),
                RepairDiagnostic(
                    source="go test",
                    code="go_compile_error",
                    message='expected declaration, found "import"',
                    path="cmd/app/main.go",
                    raw='import (\n    import "fmt"\n)',
                ),
            )
        )
        .to_dict()
    )

    assert '    "fmt"\n' in nested_repaired
    assert nested_plan is not None
    assert nested_plan.source_tool == "deterministic_go_nested_import_repair"
    assert nested_plan.priority == 1
    assert nested_plan.depends_on == ("go.bare_import_string",)
    assert nested_plan.operations[0].kind == "text_replace"
    assert nested_plan.operations[0].metadata["precision_strategy"] == "span_context_text_patch"
    assert nested_plan.operations[0].expected == '    import "fmt"'
    assert nested_plan.operations[0].replacement == '    "fmt"'

    assert bare_local_plan is not None
    assert bare_local_plan.source_tool == "deterministic_go_bare_import_repair"
    assert bare_local_plan.priority == 2
    assert bare_local_plan.depends_on == ("go.nested_import_keyword",)
    assert bare_local_plan.operations[0].kind == "text_replace"
    assert bare_local_plan.operations[0].expected == '"src/models"'
    assert bare_local_plan.operations[0].replacement == '"example.com/demo/src/models"'

    assert subpath_plan is not None
    assert subpath_plan.source_tool == "deterministic_go_subpath_repair"
    assert subpath_plan.priority == 3
    assert subpath_plan.depends_on == ("go.bare_local_import",)
    assert subpath_plan.operations[0].kind == "text_replace"
    assert subpath_plan.operations[0].expected == '"example.com/demo/pet-ascii/src/engine"'
    assert subpath_plan.operations[0].replacement == '"example.com/demo/src/engine"'

    coverage_items = coverage["items"]
    assert "deterministic_go_bare_import_repair" in coverage_items[0]["matched_source_tools"]
    assert "deterministic_go_subpath_repair" in coverage_items[1]["matched_source_tools"]
    assert "deterministic_go_nested_import_repair" in coverage_items[2]["matched_source_tools"]
    assert all(item["executable_runtime_plan_matched"] is True for item in coverage_items)


def test_go_module_import_not_in_std_diagnostic_is_covered_plannable() -> None:
    diagnostic = (
        "Artifact quality scan failed: workspace validation command failed (go test ./...): "
        "engine/unlock.go:3:8: package example-app/models is not in std"
    )
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=("deterministic_go_module_import_repair",),
            artifact_quality_errors=(diagnostic,),
            base_files={
                "go.mod": "module example/app\n\ngo 1.21\n",
                "models/capsule.go": "package models\n\ntype Capsule struct{}\n",
                "engine/unlock.go": (
                    'package engine\n\nimport "example-app/models"\n\n'
                    "func Use() models.Capsule { return models.Capsule{} }\n"
                ),
            },
        )
    )

    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == ("deterministic_go_module_import_repair",)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 1


def test_go_unused_import_rule_removes_compiler_reported_line_with_text_replace() -> None:
    relative_path = "engine/riddle.go"
    content = (
        "package engine\n"
        "\n"
        "import (\n"
        '    "errors"\n'
        '    "timecapsulemuseum/models"\n'
        ")\n"
        "\n"
        'func Riddle() error { return errors.New("sealed") }\n'
    )
    raw = (
        "Artifact quality scan failed: workspace validation command failed (go test ./...): "
        'engine/riddle.go:5:5: "timecapsulemuseum/models" imported and not used'
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_go_unused_import_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "go.unused_import"
    assert plan.source_tool == "deterministic_go_unused_import_repair"
    assert plan.priority == 2
    assert plan.metadata["span_based"] is True
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == '    "timecapsulemuseum/models"\n'
    assert operation.replacement == ""
    assert operation.before_hash == sha256_text(content)
    assert operation.metadata["repair_kind"] == "go_unused_import"
    assert operation.metadata["import_path"] == "timecapsulemuseum/models"

    planning = plan_runtime_repair(
        source_tool="deterministic_go_unused_import_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert '"timecapsulemuseum/models"' not in planning.composition.patches[0].content_after
    assert '"errors"' in planning.composition.patches[0].content_after


def test_go_unused_import_coverage_matches_executable_runtime_plan() -> None:
    raw = 'engine/riddle.go:5:5: "timecapsulemuseum/models" imported and not used'
    diagnostics = normalize_artifact_quality_errors([raw])
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_go_unused_import_repair",
        base_files={
            "engine/riddle.go": (
                "package engine\n\nimport (\n"
                '    "errors"\n'
                '    "timecapsulemuseum/models"\n'
                ")\n\n"
                'func Riddle() error { return errors.New("sealed") }\n'
            )
        },
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "go.unused_import" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_go_unused_import_repair" in coverage["items"][0]["matched_source_tools"]
    assert coverage["items"][0]["archetypes"] == ["generated_residue"]
    assert coverage["items"][0]["phases"] == ["code_repair"]
    assert coverage["items"][0]["diagnostic_archetype"] == "generated_residue"
    assert coverage["items"][0]["diagnostic_phase"] == "code_repair"
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_go_unused_import_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_go_error_string_helper_rule_inserts_narrow_error_type() -> None:
    relative_path = "models/gallery.go"
    content = (
        "package models\n"
        "\n"
        'import "errors"\n'
        "\n"
        "var (\n"
        '    ErrDuplicateCapsule = errString("capsule id already exists")\n'
        '    ErrUnknownCapsule   = errString("capsule id not found")\n'
        ")\n"
        "\n"
        'func Existing() error { return errors.New("x") }\n'
    )
    raw = (
        "Artifact quality scan failed: workspace validation command failed (go test ./...): "
        "models/gallery.go:6:27: undefined: errString"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_go_error_string_helper_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "go.error_string_helper"
    assert plan.source_tool == "deterministic_go_error_string_helper_repair"
    assert plan.priority == 3
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == ""
    assert (
        operation.replacement == "type errString string\n\nfunc (e errString) Error() string { return string(e) }\n\n"
    )
    assert operation.before_hash == sha256_text(content)
    assert operation.metadata["repair_kind"] == "go_error_string_helper"
    assert operation.metadata["identifier"] == "errString"

    planning = plan_runtime_repair(
        source_tool="deterministic_go_error_string_helper_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.composition is not None
    assert planning.composition.ok is True
    repaired = planning.composition.patches[0].content_after
    assert "type errString string" in repaired
    assert "func (e errString) Error() string { return string(e) }" in repaired
    assert repaired.index("type errString string") < repaired.index("var (")


def test_go_error_string_helper_coverage_matches_executable_runtime_plan() -> None:
    raw = "models/gallery.go:52:24: undefined: errString"
    diagnostics = normalize_artifact_quality_errors([raw])
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_go_error_string_helper_repair",
        base_files={
            "models/gallery.go": (
                'package models\n\nvar (\n    ErrDuplicateCapsule = errString("capsule id already exists")\n)\n'
            )
        },
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "go.error_string_helper" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_go_error_string_helper_repair" in coverage["items"][0]["matched_source_tools"]
    assert coverage["items"][0]["archetypes"] == ["missing_dependency"]
    assert coverage["items"][0]["phases"] == ["code_repair"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_go_error_string_helper_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_go_error_string_helper_rule_rejects_non_error_undefined_symbols() -> None:
    raw = "models/gallery.go:52:24: undefined: buildCapsule"
    diagnostics = normalize_artifact_quality_errors([raw])
    plan = build_go_error_string_helper_plan(
        base_files={"models/gallery.go": ('package models\n\nvar CapsuleFactory = buildCapsule("demo")\n')},
        diagnostics=diagnostics,
        mode="shadow",
    )
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()

    assert plan is None
    assert coverage["items"][0]["known_rule_matched"] is False


def test_rust_dependency_rule_builds_canonical_plan_from_diagnostics() -> None:
    cargo = '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\n'
    source = "use serde::Serialize;\nfn main() { let _ = serde_json::json!({}); }\n"
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="rust_e0432",
        message="unresolved import `serde`",
        path="src/main.rs",
        raw="error[E0432]: unresolved import `serde`",
    )

    plan = build_rust_dependency_plan(
        base_files={"./Cargo.toml": cargo, "src/main.rs": source},
        diagnostics=(diagnostic,),
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.unlinked_crate_dependency"
    assert plan.source_tool == "deterministic_rust_dependency_repair"
    assert plan.priority == 0
    assert plan.operations[0].path == "Cargo.toml"
    assert plan.operations[0].metadata["repair_kind"] == "rust_dependency"
    assert plan.operations[0].metadata["packages"] == ("serde", "serde_json")
    assert 'serde = { version = "1.0", features = ["derive"] }' in str(plan.operations[0].content)
    assert 'serde_json = "1.0"' in str(plan.operations[0].content)


def test_rust_dependency_e0432_known_crate_import_is_covered_plannable() -> None:
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=("deterministic_rust_dependency_repair",),
            artifact_quality_errors=("error[E0432]: unresolved import `serde`",),
            base_files={
                "Cargo.toml": '[package]\nname = "kitchen-taste-palette"\n\n[dependencies]\n',
                "src/main.rs": ("use serde::{Deserialize, Serialize};\nfn main() { let _ = serde_json::json!({}); }\n"),
            },
        )
    )

    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == ("deterministic_rust_dependency_repair",)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 1


def test_rust_missing_binary_entrypoint_rule_builds_create_file_plan() -> None:
    cargo = '[package]\nname = "demo-app"\nversion = "0.1.0"\n\n[[bin]]\nname = "demo-cli"\npath = "src/bin/demo.rs"\n'

    plan = build_rust_missing_binary_entrypoint_plan(
        base_files={"./Cargo.toml": cargo},
        diagnostics=(),
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.missing_binary_entrypoint"
    assert plan.source_tool == "deterministic_rust_missing_binary_entrypoint_repair"
    assert plan.priority == 1
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "write_file"
    assert operation.path == "src/bin/demo.rs"
    assert operation.before_hash == sha256_text("")
    assert operation.metadata["write_file_reason"] == "new_file_or_empty_file"
    assert operation.metadata["create_file_rollback_strategy"] == (
        "restore_empty_before_content_via_policy_gated_writer"
    )
    assert "fn main()" in str(operation.content)
    assert "demo_app binary entry point" in str(operation.content)


def test_rust_method_self_signature_rule_builds_precise_plan_from_diagnostics() -> None:
    relative_path = "src/lib.rs"
    content = "pub struct Demo;\nimpl Demo {\n    pub fn foo(&) -> i32 { 1 }\n    pub fn bar(&mut) { }\n}\n"
    errors = (
        "error: expected parameter name, found `)`\n --> src/lib.rs:3:17\n  |\n3 |     pub fn foo(&) -> i32 { 1 }",
        "error: expected parameter name, found `)`\n --> src/lib.rs:4:20\n  |\n4 |     pub fn bar(&mut) { }",
    )
    diagnostics = normalize_artifact_quality_errors(list(errors))

    plan = build_rust_method_self_signature_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.method_self_signature"
    assert plan.source_tool == "deterministic_rust_method_self_signature_repair"
    assert plan.metadata["edit_strategy"] == "span_text_replace"
    assert len(plan.operations) == 2
    assert [operation.kind for operation in plan.operations] == ["text_replace", "text_replace"]
    assert [operation.expected for operation in plan.operations] == ["(&)", "(&mut)"]
    assert [operation.replacement for operation in plan.operations] == ["(&self)", "(&mut self)"]
    assert all(operation.metadata["edit_strategy"] == "span_text_replace" for operation in plan.operations)

    composition = PatchComposer().compose({relative_path: content}, plan.operations)
    assert composition.ok is True
    assert "pub fn foo(&self)" in composition.patches[0].content_after
    assert "pub fn bar(&mut self)" in composition.patches[0].content_after


@pytest.mark.parametrize("unsafe_path", ("../src/lib.rs", "/tmp/x/src/lib.rs"))
def test_rust_method_self_signature_rule_rejects_unsafe_diagnostic_paths(unsafe_path: str) -> None:
    content = "impl Demo {\n    pub fn foo(&) -> i32 { 1 }\n}\n"
    diagnostics = normalize_artifact_quality_errors(
        [
            f"error: expected parameter name, found `)`\n --> {unsafe_path}:2:17\n  |\n2 |     pub fn foo(&) -> i32 {{ 1 }}",
        ]
    )

    plan = build_rust_method_self_signature_plan(
        base_files={unsafe_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_method_self_signature_rule_normalizes_dot_relative_paths() -> None:
    content = "impl Demo {\n    pub fn foo(&) -> i32 { 1 }\n}\n"
    diagnostics = normalize_artifact_quality_errors(
        [
            "error: expected parameter name, found `)`\n --> ./src/lib.rs:2:17\n  |\n2 |     pub fn foo(&) -> i32 { 1 }",
        ]
    )

    plan = build_rust_method_self_signature_plan(
        base_files={"./src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.operations[0].path == "src/lib.rs"


def test_rust_method_self_signature_coverage_matches_executable_runtime_plan() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "error: expected parameter name, found `)`\n --> src/lib.rs:2:17\n  |\n2 |     pub fn foo(&) -> i32 { 1 }",
        ]
    )
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_method_self_signature_repair",
        base_files={"src/lib.rs": "impl Demo {\n    pub fn foo(&) -> i32 { 1 }\n}\n"},
        artifact_quality_errors=(diagnostics[0].raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.method_self_signature" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_method_self_signature_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_method_self_signature_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def _rust_serde_derive_error(
    *,
    module: str = "models",
    symbol: str = "Recipe",
    trait: str = "Serialize",
) -> str:
    return (
        (
            "error[E0277]: the trait bound `Recipe: serde::Serialize` is not satisfied\n"
            "help: consider adding `#[derive(serde::Serialize)]` to your `models::Recipe` type\n"
            f"note: requested trait marker {trait} for `{module}::{symbol}`"
        )
        .replace("serde::Serialize", f"serde::{trait}")
        .replace("models::Recipe", f"{module}::{symbol}")
    )


def test_rust_serde_derive_rule_extends_existing_derive_with_span_metadata() -> None:
    relative_path = "src/models.rs"
    content = "#[derive(Debug, Clone)]\npub struct Recipe { name: String }\n"
    raw = _rust_serde_derive_error()
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_serde_derive_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.serde_derive"
    assert plan.source_tool == "deterministic_rust_serde_derive_repair"
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "#[derive(Debug, Clone)]\n"
    assert operation.replacement == "#[derive(Debug, Clone, serde::Serialize)]\n"
    assert operation.metadata["repair_kind"] == "rust_serde_derive"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["unique_context"] == "#[derive(Debug, Clone)]\n"
    assert operation.metadata["traits_added"] == ("serde::Serialize",)

    runtime_planning = plan_runtime_repair(
        source_tool="deterministic_rust_serde_derive_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    composition = runtime_planning.composition
    assert composition is not None
    assert composition.ok is True
    assert "#[derive(Debug, Clone, serde::Serialize)]" in composition.patches[0].content_after


def test_rust_serde_derive_rule_inserts_derive_by_replacing_declaration_line() -> None:
    relative_path = "src/models.rs"
    content = "pub enum Recipe { Soup }\n"
    diagnostics = normalize_artifact_quality_errors([_rust_serde_derive_error(trait="Deserialize")])

    plan = build_rust_serde_derive_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.expected == "pub enum Recipe { Soup }\n"
    assert operation.replacement == "#[derive(serde::Deserialize)]\npub enum Recipe { Soup }\n"
    assert operation.metadata["derive_line_existing"] is False


def test_rust_serde_derive_coverage_matches_executable_runtime_plan() -> None:
    raw = _rust_serde_derive_error()
    diagnostics = normalize_artifact_quality_errors([raw])
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_serde_derive_repair",
        base_files={"src/models.rs": "pub struct Recipe { name: String }\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.serde_derive" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_serde_derive_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_serde_derive_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_serde_derive_runtime_binding_executes_public_edit(tmp_path: Path) -> None:
    relative_path = "src/models.rs"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = "#[derive(Debug)]\npub struct Recipe { name: String }\n"
    target.write_text(content, encoding="utf-8")
    edit_calls: list[tuple[str, str, str]] = []
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, replacement: str) -> dict[str, object]:
        write_calls.append((path, replacement))
        return {"ok": False, "file": path, "error": "write_file should not be used"}

    def editor(operation: RepairOperation) -> dict[str, object]:
        before = target.read_text(encoding="utf-8")
        assert operation.expected is not None
        assert operation.replacement is not None
        target.write_text(before.replace(operation.expected, operation.replacement, 1), encoding="utf-8")
        edit_calls.append((operation.path, operation.expected, operation.replacement))
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-serde-derive",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_serde_derive_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(_rust_serde_derive_error(),),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert [receipt.source_tool for receipt in result.receipts] == ["deterministic_rust_serde_derive_repair"]
    assert edit_calls == [
        (
            relative_path,
            "#[derive(Debug)]\n",
            "#[derive(Debug, serde::Serialize)]\n",
        )
    ]
    assert write_calls == []
    assert "#[derive(Debug, serde::Serialize)]" in target.read_text(encoding="utf-8")


def _rust_missing_trait_derive_error(
    *,
    symbol: str = "Widget",
    trait: str = "Eq",
    path: str = "src/lib.rs",
    line: int = 3,
) -> str:
    return (
        f"error[E0277]: the trait bound `{symbol}: {trait}` is not satisfied\n"
        f" --> {path}:{line}:10\n"
        "  |\n"
        f"{line} |     needs_eq(widget);\n"
        "  |     --------- ^^^^^^ the trait `Eq` is not implemented\n"
    )


def test_rust_missing_trait_derive_rule_extends_existing_derive_with_span_metadata() -> None:
    relative_path = "src/lib.rs"
    content = "#[derive(Debug, Clone)]\npub struct Widget { id: u64 }\n"
    raw = _rust_missing_trait_derive_error(trait="Hash")
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_missing_trait_derive_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.missing_trait_derive"
    assert plan.source_tool == "deterministic_rust_derive_repair"
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.span_start == 0
    assert operation.span_end == len("#[derive(Debug, Clone)]\n")
    assert operation.expected == "#[derive(Debug, Clone)]\n"
    assert operation.replacement == "#[derive(Debug, Clone, Hash)]\n"
    assert operation.metadata["repair_kind"] == "rust_missing_trait_derive"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["unique_context"] == "#[derive(Debug, Clone)]\npub struct Widget { id: u64 }\n"
    assert operation.metadata["traits_added"] == ("Hash",)

    runtime_planning = plan_runtime_repair(
        source_tool="deterministic_rust_derive_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    composition = runtime_planning.composition
    assert composition is not None
    assert composition.ok is True
    assert "#[derive(Debug, Clone, Hash)]" in composition.patches[0].content_after


def test_rust_missing_trait_derive_rule_does_not_take_serde_diagnostics() -> None:
    diagnostics = normalize_artifact_quality_errors([_rust_serde_derive_error()])

    plan = build_rust_missing_trait_derive_plan(
        base_files={"src/models.rs": "#[derive(Debug)]\npub struct Recipe { name: String }\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_missing_trait_derive_runtime_binding_executes_public_edit(tmp_path: Path) -> None:
    relative_path = "src/lib.rs"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = "#[derive(Debug)]\npub struct Widget { id: u64 }\n"
    target.write_text(content, encoding="utf-8")
    edit_calls: list[tuple[str, str, str]] = []
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, replacement: str) -> dict[str, object]:
        write_calls.append((path, replacement))
        return {"ok": False, "file": path, "error": "write_file should not be used"}

    def editor(operation: RepairOperation) -> dict[str, object]:
        before = target.read_text(encoding="utf-8")
        assert operation.expected is not None
        assert operation.replacement is not None
        target.write_text(before.replace(operation.expected, operation.replacement, 1), encoding="utf-8")
        edit_calls.append((operation.path, operation.expected, operation.replacement))
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-missing-trait-derive",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_derive_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(_rust_missing_trait_derive_error(),),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert [receipt.source_tool for receipt in result.receipts] == ["deterministic_rust_derive_repair"]
    assert edit_calls == [
        (
            relative_path,
            "#[derive(Debug)]\n",
            "#[derive(Debug, Eq)]\n",
        )
    ]
    assert write_calls == []
    assert "#[derive(Debug, Eq)]" in target.read_text(encoding="utf-8")


def _rust_copy_derive_error(*, path: str = "src/lib.rs", line: int = 2) -> str:
    return (
        "error[E0204]: the trait `Copy` cannot be implemented for this type\n"
        f" --> {path}:{line}:10\n"
        "  |\n"
        f"{line} | pub struct Demo {{ value: String }}\n"
        "  |          ^^^^"
    )


@pytest.mark.parametrize(
    ("derive_line", "replacement"),
    (
        ("#[derive(Debug, Clone, Copy)]\n", "#[derive(Debug, Clone)]\n"),
        ("#[derive(Copy, Clone)]\n", "#[derive(Clone)]\n"),
        ("#[derive(Copy)]\n", "#[derive()]\n"),
    ),
)
def test_rust_copy_derive_rule_removes_copy_with_text_replace(
    derive_line: str,
    replacement: str,
) -> None:
    relative_path = "src/lib.rs"
    content = f"{derive_line}pub struct Demo {{ value: String }}\n"
    raw = _rust_copy_derive_error()
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_incompatible_copy_derive_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.incompatible_copy_derive"
    assert plan.source_tool == "deterministic_rust_incompatible_copy_derive_repair"
    assert plan.priority == 1
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == derive_line
    assert operation.replacement == replacement
    assert operation.metadata["repair_kind"] == "rust_incompatible_copy_derive"
    assert operation.metadata["edit_strategy"] == "text_replace"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["line_number"] == 2
    assert operation.metadata["derive_line_number"] == 1
    assert operation.metadata["unique_context"] is True

    runtime_planning = plan_runtime_repair(
        source_tool="deterministic_rust_incompatible_copy_derive_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    composition = runtime_planning.composition
    assert composition is not None
    assert composition.ok is True
    assert replacement in composition.patches[0].content_after
    assert "Copy" not in composition.patches[0].content_after


@pytest.mark.parametrize(
    ("diagnostic_path", "base_files"),
    (
        ("", {"src/lib.rs": "#[derive(Copy)]\npub struct Demo { value: String }\n"}),
        (
            "/tmp/project/src/lib.rs",
            {"/tmp/project/src/lib.rs": "#[derive(Copy)]\npub struct Demo { value: String }\n"},
        ),
        ("../src/lib.rs", {"../src/lib.rs": "#[derive(Copy)]\npub struct Demo { value: String }\n"}),
        ("src/lib.txt", {"src/lib.txt": "#[derive(Copy)]\npub struct Demo { value: String }\n"}),
        ("src/lib.rs", {"src/other.rs": "#[derive(Copy)]\npub struct Demo { value: String }\n"}),
    ),
)
def test_rust_copy_derive_rule_rejects_unsafe_or_untracked_paths(
    diagnostic_path: str,
    base_files: dict[str, str],
) -> None:
    diagnostics = normalize_artifact_quality_errors([_rust_copy_derive_error(path=diagnostic_path, line=2)])

    plan = build_rust_incompatible_copy_derive_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_copy_derive_rule_does_not_plan_without_copy_token() -> None:
    content = "#[derive(Clone)]\npub struct Demo { value: String }\n"
    diagnostics = normalize_artifact_quality_errors([_rust_copy_derive_error()])

    plan = build_rust_incompatible_copy_derive_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_copy_derive_rule_rejects_non_unique_expected_line() -> None:
    content = (
        "#[derive(Clone, Copy)]\n"
        "pub struct Demo { value: String }\n"
        "#[derive(Clone, Copy)]\n"
        "pub struct Other { value: String }\n"
    )
    diagnostics = normalize_artifact_quality_errors([_rust_copy_derive_error()])

    plan = build_rust_incompatible_copy_derive_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_copy_derive_coverage_matches_executable_runtime_plan() -> None:
    raw = _rust_copy_derive_error()
    diagnostics = normalize_artifact_quality_errors([raw])
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_incompatible_copy_derive_repair",
        base_files={"src/lib.rs": "#[derive(Clone, Copy)]\npub struct Demo { value: String }\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert "deterministic_rust_post_repair" not in runtime_repair_source_tools()
    assert "deterministic_rust_derive_repair" in runtime_repair_source_tools()
    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.incompatible_copy_derive" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_incompatible_copy_derive_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_incompatible_copy_derive_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_copy_derive_runtime_binding_executes_public_edit(tmp_path: Path) -> None:
    relative_path = "src/lib.rs"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = "#[derive(Debug, Clone, Copy)]\npub struct Demo { value: String }\n"
    target.write_text(content, encoding="utf-8")
    edit_calls: list[tuple[str, str, str]] = []
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, replacement: str) -> dict[str, object]:
        write_calls.append((path, replacement))
        return {"ok": False, "file": path, "error": "write_file should not be used"}

    def editor(operation: RepairOperation) -> dict[str, object]:
        before = target.read_text(encoding="utf-8")
        assert operation.expected is not None
        assert operation.replacement is not None
        target.write_text(before.replace(operation.expected, operation.replacement, 1), encoding="utf-8")
        edit_calls.append((operation.path, operation.expected, operation.replacement))
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-copy-derive",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_incompatible_copy_derive_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(_rust_copy_derive_error(),),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert [receipt.source_tool for receipt in result.receipts] == [
        "deterministic_rust_incompatible_copy_derive_repair"
    ]
    assert edit_calls == [
        (
            relative_path,
            "#[derive(Debug, Clone, Copy)]\n",
            "#[derive(Debug, Clone)]\n",
        )
    ]
    assert write_calls == []
    assert "#[derive(Debug, Clone)]" in target.read_text(encoding="utf-8")


def _rust_wrong_crate_path_error(
    *,
    path: str = "src/lib.rs",
    line: int = 1,
    original: str = "use crate::recipe::Recipe;",
    suggestion: str = "use crate::models::recipe::Recipe;",
) -> str:
    return (
        "error[E0432]: unresolved import `crate::recipe`\n"
        f" --> {path}:{line}:12\n"
        "  |\n"
        f"{line} | {original}\n"
        "  |            ^^^^^^ help: a similar path exists: `models::recipe`\n"
        "help: a similar path exists\n"
        "  |\n"
        f"{line} | {suggestion}\n"
    )


def test_rust_wrong_crate_path_rule_replaces_use_line_with_span_metadata() -> None:
    relative_path = "src/lib.rs"
    content = "    use crate::recipe::Recipe;\nfn main() {}\n"
    raw = _rust_wrong_crate_path_error()
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_wrong_crate_path_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.wrong_crate_path"
    assert plan.source_tool == "deterministic_rust_wrong_crate_path_repair"
    assert plan.depends_on == ("rust.unlinked_crate_dependency",)
    assert plan.priority == 0
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "    use crate::recipe::Recipe;\n"
    assert operation.replacement == "    use crate::models::recipe::Recipe;\n"
    assert operation.metadata["repair_kind"] == "rust_wrong_crate_path"
    assert operation.metadata["edit_strategy"] == "text_replace"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["line_number"] == 1
    assert operation.metadata["suggestion"] == "use crate::models::recipe::Recipe;"
    assert operation.metadata["unique_context"] is True

    runtime_planning = plan_runtime_repair(
        source_tool="deterministic_rust_wrong_crate_path_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    composition = runtime_planning.composition
    assert composition is not None
    assert composition.ok is True
    assert "    use crate::models::recipe::Recipe;" in composition.patches[0].content_after


@pytest.mark.parametrize(
    ("diagnostic_path", "base_files"),
    (
        ("", {"src/lib.rs": "use crate::recipe::Recipe;\n"}),
        ("/tmp/project/src/lib.rs", {"/tmp/project/src/lib.rs": "use crate::recipe::Recipe;\n"}),
        ("../src/lib.rs", {"../src/lib.rs": "use crate::recipe::Recipe;\n"}),
        ("src/lib.txt", {"src/lib.txt": "use crate::recipe::Recipe;\n"}),
        ("src/lib.rs", {"src/other.rs": "use crate::recipe::Recipe;\n"}),
    ),
)
def test_rust_wrong_crate_path_rule_rejects_unsafe_or_untracked_paths(
    diagnostic_path: str,
    base_files: dict[str, str],
) -> None:
    path_for_raw = diagnostic_path or "/tmp/empty.rs"
    diagnostics = normalize_artifact_quality_errors([_rust_wrong_crate_path_error(path=path_for_raw)])

    plan = build_rust_wrong_crate_path_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_wrong_crate_path_rule_rejects_non_use_target_line() -> None:
    content = "let recipe = Recipe::new();\n"
    diagnostics = normalize_artifact_quality_errors([_rust_wrong_crate_path_error()])

    plan = build_rust_wrong_crate_path_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_wrong_crate_path_rule_rejects_non_unique_expected_line() -> None:
    content = "use crate::recipe::Recipe;\nuse crate::recipe::Recipe;\n"
    diagnostics = normalize_artifact_quality_errors([_rust_wrong_crate_path_error()])

    plan = build_rust_wrong_crate_path_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_wrong_crate_path_rule_rejects_invalid_suggestion() -> None:
    content = "use crate::recipe::Recipe;\n"
    diagnostics = normalize_artifact_quality_errors(
        [
            _rust_wrong_crate_path_error(
                suggestion="crate::models::recipe::Recipe",
            ),
            _rust_wrong_crate_path_error(
                suggestion="use crate::models::recipe::Recipe",
            ),
        ]
    )

    plan = build_rust_wrong_crate_path_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_wrong_crate_path_rule_composes_multiple_same_file_text_replacements() -> None:
    relative_path = "src/lib.rs"
    content = "use crate::recipe::Recipe;\nuse crate::pantry::Pantry;\nfn main() {}\n"
    raw = _rust_wrong_crate_path_error(
        path=relative_path,
        line=1,
        original="use crate::recipe::Recipe;",
        suggestion="use crate::models::recipe::Recipe;",
    ) + _rust_wrong_crate_path_error(
        path=relative_path,
        line=2,
        original="use crate::pantry::Pantry;",
        suggestion="use crate::models::pantry::Pantry;",
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_wrong_crate_path_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert [operation.kind for operation in plan.operations] == ["text_replace", "text_replace"]
    assert len(plan.operations) == 2
    runtime_planning = plan_runtime_repair(
        source_tool="deterministic_rust_wrong_crate_path_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    composition = runtime_planning.composition
    assert composition is not None
    assert composition.ok is True
    assert composition.patches[0].content_after == (
        "use crate::models::recipe::Recipe;\nuse crate::models::pantry::Pantry;\nfn main() {}\n"
    )


def test_rust_crate_import_rewrite_rule_uses_span_based_editor_operations(tmp_path: Path) -> None:
    cargo = '[package]\nname = "garden-kit"\nversion = "0.1.0"\n'
    main_path = "src/main.rs"
    lib_path = "src/lib.rs"
    main_content = "use garden_app_kit::models::Recipe;\nextern crate garden_app_kit;\nfn main() {}\n"
    raw = "error[E0433]: failed to resolve: cannot find crate `garden_app_kit`\n --> src/main.rs:1:5\n"
    base_files = {
        "Cargo.toml": cargo,
        main_path: main_content,
        lib_path: "pub mod models;\n",
    }
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_crate_import_rewrite_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.crate_import_rewrite"
    assert plan.source_tool == "deterministic_rust_crate_import_rewrite_repair"
    assert plan.depends_on == ("rust.unlinked_crate_dependency",)
    assert plan.metadata["span_based"] is True
    assert [operation.kind for operation in plan.operations] == ["text_replace", "text_replace"]
    assert {operation.metadata["match_kind"] for operation in plan.operations} == {
        "crate_prefix",
        "extern_crate",
    }
    assert all(operation.expected for operation in plan.operations)
    assert all(operation.metadata["expected_context_after"] for operation in plan.operations)

    runtime_planning = plan_runtime_repair(
        source_tool="deterministic_rust_crate_import_rewrite_repair",
        base_files=base_files,
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    composition = runtime_planning.composition
    assert composition is not None
    assert composition.ok is True
    assert "use garden_kit::models::Recipe;" in composition.patches[0].content_after
    assert "extern crate garden_kit;" in composition.patches[0].content_after

    target = tmp_path / main_path
    target.parent.mkdir(parents=True)
    target.write_text(main_content, encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text(cargo, encoding="utf-8")
    (tmp_path / lib_path).write_text("pub mod models;\n", encoding="utf-8")
    edited_operations: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        raise AssertionError("span-based Rust crate import rewrite must use edit_file")

    def editor(operation: RepairOperation) -> dict[str, object]:
        edit_target = tmp_path / operation.path
        current = edit_target.read_text(encoding="utf-8")
        start = int(operation.span_start or 0)
        end = int(operation.span_end or 0)
        assert current[start:end] == operation.expected
        edit_target.write_text(
            current[:start] + str(operation.replacement or "") + current[end:],
            encoding="utf-8",
        )
        edited_operations.append(operation.operation_id)
        return {"ok": True}

    run = run_runtime_repair(
        source_tool="deterministic_rust_crate_import_rewrite_repair",
        workspace=tmp_path,
        base_files=base_files,
        artifact_quality_errors=(raw,),
        writer=writer,
        editor=editor,
        allowed_paths=(main_path, lib_path, "Cargo.toml"),
    )

    assert run.ok is True
    assert len(edited_operations) == 2
    assert target.read_text(encoding="utf-8") == (
        "use garden_kit::models::Recipe;\nextern crate garden_kit;\nfn main() {}\n"
    )
    assert run.execution_result is not None
    records = run.execution_result.receipt.metadata["execution_records"]
    assert records[0]["operation"] == "edit_file"
    assert records[0]["span_based"] is True


def test_rust_crate_import_legacy_source_tool_executes_through_runtime_editor(tmp_path: Path) -> None:
    cargo = '[package]\nname = "kitchen-flavor-palette"\nversion = "0.1.0"\n'
    main_path = "src/main.rs"
    main_content = (
        "use kitchen_palette::engine::generate_palette;\n"
        "fn main() {\n"
        "    let _ = kitchen_palette::models::Recipe::default();\n"
        "    generate_palette();\n"
        "}\n"
    )
    raw = "cargo check failed: error[E0433]: cannot find module or crate `kitchen_palette` in this scope"
    base_files = {
        "Cargo.toml": cargo,
        main_path: main_content,
    }
    target = tmp_path / main_path
    target.parent.mkdir(parents=True)
    target.write_text(main_content, encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text(cargo, encoding="utf-8")
    edited_operations: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        raise AssertionError("span-based Rust crate import repair must use edit_file")

    def editor(operation: RepairOperation) -> dict[str, object]:
        edit_target = tmp_path / operation.path
        current = edit_target.read_text(encoding="utf-8")
        start = int(operation.span_start or 0)
        end = int(operation.span_end or 0)
        assert current[start:end] == operation.expected
        edit_target.write_text(
            current[:start] + str(operation.replacement or "") + current[end:],
            encoding="utf-8",
        )
        edited_operations.append(operation.operation_id)
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    run = run_runtime_repair(
        source_tool="deterministic_rust_crate_import_repair",
        workspace=tmp_path,
        base_files=base_files,
        artifact_quality_errors=(raw,),
        writer=writer,
        editor=editor,
        allowed_paths=(main_path, "Cargo.toml"),
    )

    repaired = target.read_text(encoding="utf-8")
    assert run.ok is True
    assert edited_operations
    assert "use kitchen_flavor_palette::engine::generate_palette;" in repaired
    assert "kitchen_flavor_palette::models::Recipe" in repaired
    assert "kitchen_palette::" not in repaired
    assert run.execution_result is not None
    assert {record["operation"] for record in run.execution_result.receipt.metadata["execution_records"]} == {
        "edit_file"
    }


def test_rust_crate_import_legacy_source_tool_declines_declared_dependencies() -> None:
    cargo = '[package]\nname = "kitchen-flavor-palette"\nversion = "0.1.0"\n\n[dependencies]\nkitchen_palette = "1"\n'
    main_content = "use kitchen_palette::engine::generate_palette;\n"
    raw = "cargo check failed: error[E0433]: cannot find module or crate `kitchen_palette` in this scope"

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_crate_import_repair",
        base_files={
            "Cargo.toml": cargo,
            "src/main.rs": main_content,
        },
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert planning.error_code is None
    assert planning.plan is None
    assert planning.composition is None


def test_rust_wrong_crate_path_runtime_binding_executes_public_edit(tmp_path: Path) -> None:
    relative_path = "src/lib.rs"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = "use crate::recipe::Recipe;\nfn main() {}\n"
    target.write_text(content, encoding="utf-8")
    edit_calls: list[tuple[str, str, str]] = []
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, replacement: str) -> dict[str, object]:
        write_calls.append((path, replacement))
        return {"ok": False, "file": path, "error": "write_file should not be used"}

    def editor(operation: RepairOperation) -> dict[str, object]:
        before = target.read_text(encoding="utf-8")
        assert operation.expected is not None
        assert operation.replacement is not None
        target.write_text(before.replace(operation.expected, operation.replacement, 1), encoding="utf-8")
        edit_calls.append((operation.path, operation.expected, operation.replacement))
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-wrong-crate-path",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_wrong_crate_path_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(_rust_wrong_crate_path_error(),),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert [receipt.source_tool for receipt in result.receipts] == ["deterministic_rust_wrong_crate_path_repair"]
    assert edit_calls == [
        (
            relative_path,
            "use crate::recipe::Recipe;\n",
            "use crate::models::recipe::Recipe;\n",
        )
    ]
    assert write_calls == []
    assert "use crate::models::recipe::Recipe;" in target.read_text(encoding="utf-8")


def test_rust_unresolved_pub_use_rule_deletes_flat_pub_use_with_span_metadata() -> None:
    relative_path = "src/lib.rs"
    content = "pub use foo::Missing;\npub fn keep() {}\n"
    raw = (
        "error[E0432]: unresolved import `foo::Missing`\n"
        " --> src/lib.rs:1:9\n"
        "  |\n"
        "1 | pub use foo::Missing;\n"
        "  |         ^^^^^^^^^^^^ no `Missing` in `foo`"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_unresolved_pub_use_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.unresolved_pub_use"
    assert plan.source_tool == "deterministic_rust_unresolved_pub_use_repair"
    assert plan.metadata["span_based"] is True
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "pub use foo::Missing;\n"
    assert operation.replacement == ""
    assert operation.before_hash == sha256_text(content)
    assert operation.metadata["span_based"] is True
    assert operation.metadata["symbols_removed"] == ("Missing",)
    assert operation.metadata["unique_context"] == "pub use foo::Missing;\n"

    composition = PatchComposer().compose({relative_path: content}, plan.operations)
    assert composition.ok is True
    assert composition.patches[0].content_after == "pub fn keep() {}\n"


def test_rust_unresolved_pub_use_rule_removes_only_missing_group_symbol() -> None:
    relative_path = "src/lib.rs"
    content = "pub use foo::{A, Missing, B};\npub fn keep() {}\n"
    raw = (
        "error[E0432]: unresolved import `foo::Missing`\n"
        " --> src/lib.rs:1:18\n"
        "  |\n"
        "1 | pub use foo::{A, Missing, B};\n"
        "  |                  ^^^^^^^ no `Missing` in `foo`"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_unresolved_pub_use_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.expected == "pub use foo::{A, Missing, B};\n"
    assert operation.replacement == "pub use foo::{A, B};\n"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["symbols_removed"] == ("Missing",)

    composition = PatchComposer().compose({relative_path: content}, plan.operations)
    assert composition.ok is True
    assert "pub use foo::{A, B};" in composition.patches[0].content_after
    assert "Missing" not in composition.patches[0].content_after


def test_rust_unresolved_pub_use_rule_does_not_plan_without_matching_symbol() -> None:
    content = "pub use foo::{A, Missing, B};\n"
    diagnostics = normalize_artifact_quality_errors(
        [
            "error[E0432]: unresolved import `foo::Other`\n"
            " --> src/lib.rs:1:18\n"
            "  |\n"
            "1 | pub use foo::{A, Missing, B};\n"
            "  |                  ^^^^^ no `Other` in `foo`",
        ]
    )

    plan = build_rust_unresolved_pub_use_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


@pytest.mark.parametrize("unsafe_path", ("../src/lib.rs", "/tmp/demo/src/lib.rs"))
def test_rust_unresolved_pub_use_rule_rejects_unsafe_paths(unsafe_path: str) -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "error[E0432]: unresolved import `foo::Missing`\n"
            f" --> {unsafe_path}:1:9\n"
            "  |\n"
            "1 | pub use foo::Missing;\n"
            "  |         ^^^^^^^^^^^^ no `Missing` in `foo`",
        ]
    )

    plan = build_rust_unresolved_pub_use_plan(
        base_files={unsafe_path: "pub use foo::Missing;\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_unresolved_pub_use_coverage_matches_executable_runtime_plan() -> None:
    raw = (
        "error[E0432]: unresolved import `foo::Missing`\n"
        " --> src/lib.rs:1:9\n"
        "  |\n"
        "1 | pub use foo::Missing;\n"
        "  |         ^^^^^^^^^^^^ no `Missing` in `foo`"
    )
    diagnostics = normalize_artifact_quality_errors([raw])
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_unresolved_pub_use_repair",
        base_files={"src/lib.rs": "pub use foo::Missing;\npub fn keep() {}\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.unresolved_pub_use" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_unresolved_pub_use_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_unresolved_pub_use_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_unused_import_rule_removes_group_symbol_with_text_replace() -> None:
    relative_path = "src/lib.rs"
    content = "use foo::{A, B, C};\npub fn keep() {}\n"
    raw = "warning: unused import: `B`\n --> src/lib.rs:1:14\n  |\n1 | use foo::{A, B, C};\n  |              ^\n"
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_unused_import_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.unused_import"
    assert plan.source_tool == "deterministic_rust_unused_import_repair"
    assert plan.priority == 2
    assert plan.metadata["span_based"] is True
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "use foo::{A, B, C};\n"
    assert operation.replacement == "use foo::{A, C};\n"
    assert operation.before_hash == sha256_text(content)
    assert operation.metadata["repair_kind"] == "rust_unused_import"
    assert operation.metadata["edit_strategy"] == "text_replace"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["line_number"] == 1
    assert operation.metadata["symbol"] == "B"
    assert operation.metadata["unique_context"] is True

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_unused_import_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_unused_import_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert "use foo::{A, C};\npub fn keep() {}" in planning.composition.patches[0].content_after


def test_rust_unused_import_rule_comments_single_import_preserving_newline() -> None:
    content = "use foo::A;\r\npub fn keep() {}\r\n"
    raw = "warning: unused import: `A`\n --> src/lib.rs:1:10\n  |\n1 | use foo::A;\n"

    plan = build_rust_unused_import_plan(
        base_files={"src/lib.rs": content},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )

    assert plan is not None
    assert plan.operations[0].kind == "text_replace"
    assert plan.operations[0].replacement == "// [repair-unused] use foo::A;\r\n"


@pytest.mark.parametrize(
    "unsafe_path",
    ("", "../src/lib.rs", "/tmp/demo/src/lib.rs", "src/lib.txt", "src/missing.rs"),
)
def test_rust_unused_import_rule_rejects_unsafe_or_untracked_paths(unsafe_path: str) -> None:
    location = "" if unsafe_path == "" else f" --> {unsafe_path}:1:10\n"
    raw = f"warning: unused import: `A`\n{location}  |\n1 | use foo::A;\n"

    plan = build_rust_unused_import_plan(
        base_files={"src/lib.rs": "use foo::A;\n", "src/lib.txt": "use foo::A;\n"},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )

    assert plan is None


def test_rust_unused_import_rule_skips_non_unique_expected_line() -> None:
    content = "use foo::A;\nuse foo::A;\n"
    raw = "warning: unused import: `A`\n --> src/lib.rs:1:10\n  |\n1 | use foo::A;\n"

    plan = build_rust_unused_import_plan(
        base_files={"src/lib.rs": content},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )

    assert plan is None


def test_rust_unused_import_rule_composes_multiple_patches_in_same_file() -> None:
    content = "use foo::{A, B};\nuse bar::C;\npub fn keep() {}\n"
    raw = (
        "warning: unused import: `B`\n"
        " --> src/lib.rs:1:14\n"
        "  |\n"
        "1 | use foo::{A, B};\n"
        "warning: unused import: `C`\n"
        " --> src/lib.rs:2:10\n"
        "  |\n"
        "2 | use bar::C;\n"
    )

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_unused_import_repair",
        base_files={"src/lib.rs": content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert len(planning.plan.operations) == 2
    assert all(operation.kind == "text_replace" for operation in planning.plan.operations)
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert planning.composition.patches[0].content_after == (
        "use foo::{A};\n// [repair-unused] use bar::C;\npub fn keep() {}\n"
    )


def test_rust_unused_import_coverage_matches_executable_runtime_plan() -> None:
    raw = "warning: unused import: `A`\n --> src/lib.rs:1:10\n  |\n1 | use foo::A;\n"
    coverage = default_repair_rule_registry().coverage(normalize_artifact_quality_errors([raw])).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_unused_import_repair",
        base_files={"src/lib.rs": "use foo::A;\npub fn keep() {}\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.unused_import" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_unused_import_repair" in coverage["items"][0]["matched_source_tools"]
    assert coverage["items"][0]["archetypes"] == ["generated_residue"]
    assert coverage["items"][0]["phases"] == ["code_repair"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_unused_import_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_trait_import_rule_builds_precise_text_replace_plan_and_runs_with_editor(
    tmp_path: Path,
) -> None:
    relative_path = "src/lib.rs"
    content = "\n//! crate docs\n#![allow(dead_code)]\nuse crate::bar::Bar;\n\npub fn run() {}\n"
    raw = (
        "error[E0599]: no method named `render` found for struct `Widget` in the current scope\n"
        " --> src/lib.rs:6:12\n"
        "  |\n"
        "help: trait `Renderable` which provides `render` is implemented but not in scope; "
        "perhaps add a use for it:\n"
        "  |\n"
        "1 + use crate::render::Renderable;\n"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_trait_import_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.trait_import"
    assert plan.source_tool == "deterministic_rust_trait_import_repair"
    assert plan.metadata["span_based"] is True
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "pub fn run() {}\n"
    assert operation.replacement == "use crate::render::Renderable;\npub fn run() {}\n"
    assert operation.before_hash == sha256_text(content)
    assert operation.metadata["repair_kind"] == "rust_trait_import"
    assert operation.metadata["edit_strategy"] == "text_replace"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["import_line"] == "use crate::render::Renderable;"
    assert operation.metadata["insert_index"] == 5
    assert operation.metadata["unique_context"] is True

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_trait_import_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    assert planning.plan is not None
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert "use crate::render::Renderable;\npub fn run() {}" in planning.composition.patches[0].content_after

    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    edits: list[tuple[str, str, str]] = []
    writes: list[tuple[str, str]] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append((path, updated))
        raise AssertionError("rust trait imports must execute through editor")

    def editor(edit_operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert edit_operation.span_start is not None
        assert edit_operation.span_end is not None
        assert edit_operation.expected is not None
        assert edit_operation.replacement is not None
        assert current[edit_operation.span_start : edit_operation.span_end] == edit_operation.expected
        updated = current[: edit_operation.span_start] + edit_operation.replacement + current[edit_operation.span_end :]
        target.write_text(updated, encoding="utf-8")
        edits.append((edit_operation.path, edit_operation.expected, edit_operation.replacement))
        return {
            "ok": True,
            "file": edit_operation.path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "edit_file",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-trait-import",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_trait_import_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(raw,),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert result.error_code is None
    assert writes == []
    assert edits == [(relative_path, "pub fn run() {}\n", "use crate::render::Renderable;\npub fn run() {}\n")]
    assert "use crate::render::Renderable;\npub fn run() {}" in target.read_text(encoding="utf-8")
    assert result.receipts[0].source_tool == "deterministic_rust_trait_import_repair"


def test_rust_trait_import_rule_skips_existing_import() -> None:
    content = "use crate::render::Renderable;\npub fn run() {}\n"
    raw = (
        "error[E0599]: no method named `render` found\n"
        " --> src/lib.rs:2:12\n"
        "help: trait `Renderable` which provides `render` is implemented but not in scope; "
        "perhaps add a use for it:\n"
        "1 + use crate::render::Renderable;\n"
    )

    plan = build_rust_trait_import_plan(
        base_files={"src/lib.rs": content},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )

    assert plan is None


@pytest.mark.parametrize(
    "unsafe_path",
    ("", "../src/lib.rs", "/tmp/demo/src/lib.rs", "src/lib.txt", "src/missing.rs"),
)
def test_rust_trait_import_rule_rejects_unsafe_or_untracked_paths(unsafe_path: str) -> None:
    location = "" if unsafe_path == "" else f" --> {unsafe_path}:1:12\n"
    raw = (
        "error[E0599]: no method named `render` found\n"
        f"{location}"
        "help: trait `Renderable` which provides `render` is implemented but not in scope; "
        "perhaps add a use for it:\n"
        "1 + use crate::render::Renderable;\n"
    )

    plan = build_rust_trait_import_plan(
        base_files={"src/lib.rs": "pub fn run() {}\n", "src/lib.txt": "pub fn run() {}\n"},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )

    assert plan is None


def test_rust_trait_import_rule_skips_non_unique_anchor() -> None:
    content = "pub fn run() {}\npub fn run() {}\n"
    raw = (
        "error[E0599]: no method named `render` found\n"
        " --> src/lib.rs:1:12\n"
        "help: trait `Renderable` which provides `render` is implemented but not in scope; "
        "perhaps add a use for it:\n"
        "1 + use crate::render::Renderable;\n"
    )

    plan = build_rust_trait_import_plan(
        base_files={"src/lib.rs": content},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )

    assert plan is None


def test_rust_trait_import_coverage_matches_executable_runtime_plan() -> None:
    raw = (
        "error[E0599]: no method named `render` found\n"
        " --> src/lib.rs:1:12\n"
        "help: trait `Renderable` which provides `render` is implemented but not in scope; "
        "perhaps add a use for it:\n"
        "1 + use crate::render::Renderable;\n"
    )
    coverage = default_repair_rule_registry().coverage(normalize_artifact_quality_errors([raw])).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_trait_import_repair",
        base_files={"src/lib.rs": "pub fn run() {}\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.trait_import" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_trait_import_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_trait_import_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_line_suggestion_rule_builds_precise_text_replace_plan_and_runs_with_editor(
    tmp_path: Path,
) -> None:
    relative_path = "src/lib.rs"
    content = "fn takes(_: &String) {}\nfn main() {\n    takes(value)\n}\n"
    raw = (
        "error[E0308]: mismatched types\n"
        " --> src/lib.rs:3:11\n"
        "  |\n"
        "3 |     takes(value)\n"
        "  |           ^^^^^ expected `&String`, found `String`\n"
        "help: consider borrowing here\n"
        "  |\n"
        "3 |     takes(&value)"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_line_suggestion_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.line_suggestion"
    assert plan.source_tool == "deterministic_rust_line_suggestion_repair"
    assert plan.metadata["span_based"] is True
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "    takes(value)\n"
    assert operation.replacement == "    takes(&value)\n"
    assert operation.before_hash == sha256_text(content)
    assert operation.metadata["edit_strategy"] == "text_replace"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["line_number"] == 3
    assert operation.metadata["unique_context"] is True

    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    edits: list[tuple[str, str, str]] = []
    writes: list[tuple[str, str]] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append((path, updated))
        raise AssertionError("rust line suggestions must execute through editor")

    def editor(edit_operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert edit_operation.span_start is not None
        assert edit_operation.span_end is not None
        assert edit_operation.expected is not None
        assert edit_operation.replacement is not None
        assert current[edit_operation.span_start : edit_operation.span_end] == edit_operation.expected
        updated = current[: edit_operation.span_start] + edit_operation.replacement + current[edit_operation.span_end :]
        target.write_text(updated, encoding="utf-8")
        edits.append((edit_operation.path, edit_operation.expected, edit_operation.replacement))
        return {
            "ok": True,
            "file": edit_operation.path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "edit_file",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-line-suggestion",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_line_suggestion_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(raw,),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert result.error_code is None
    assert writes == []
    assert edits == [(relative_path, "    takes(value)\n", "    takes(&value)\n")]
    assert "takes(&value)" in target.read_text(encoding="utf-8")
    assert result.receipts[0].source_tool == "deterministic_rust_line_suggestion_repair"


@pytest.mark.parametrize("unsafe_path", ("../src/lib.rs", "/tmp/demo/src/lib.rs"))
def test_rust_line_suggestion_rule_rejects_unsafe_paths(unsafe_path: str) -> None:
    content = "fn main() {\n    takes(value)\n}\n"
    raw = (
        "error[E0308]: mismatched types\n"
        f" --> {unsafe_path}:2:11\n"
        "  |\n"
        "2 |     takes(value)\n"
        "help: consider borrowing here\n"
        "  |\n"
        "2 |     takes(&value)"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_line_suggestion_plan(
        base_files={unsafe_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_line_suggestion_rule_skips_non_unique_expected_line() -> None:
    content = "fn main() {\n    takes(value)\n    takes(value)\n}\n"
    raw = (
        "error[E0308]: mismatched types\n"
        " --> src/lib.rs:2:11\n"
        "  |\n"
        "2 |     takes(value)\n"
        "help: consider borrowing here\n"
        "  |\n"
        "2 |     takes(&value)"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_line_suggestion_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_line_suggestion_rule_skips_already_equal_line() -> None:
    content = "fn main() {\n    takes(&value)\n}\n"
    raw = (
        "error[E0308]: mismatched types\n"
        " --> src/lib.rs:2:11\n"
        "  |\n"
        "2 |     takes(value)\n"
        "help: consider borrowing here\n"
        "  |\n"
        "2 |     takes(&value)"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_line_suggestion_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_line_suggestion_rule_requires_rs_path_in_base_files() -> None:
    raw = (
        "error[E0308]: mismatched types\n"
        " --> src/lib.rs:2:11\n"
        "  |\n"
        "2 |     takes(value)\n"
        "help: consider borrowing here\n"
        "  |\n"
        "2 |     takes(&value)"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    missing_base_plan = build_rust_line_suggestion_plan(
        base_files={"src/other.rs": "fn main() {\n    takes(value)\n}\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )
    non_rs_plan = build_rust_line_suggestion_plan(
        base_files={"src/lib.txt": "fn main() {\n    takes(value)\n}\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert missing_base_plan is None
    assert non_rs_plan is None


def test_rust_line_suggestion_coverage_matches_executable_runtime_plan() -> None:
    raw = (
        "error[E0308]: mismatched types\n"
        " --> src/lib.rs:3:11\n"
        "  |\n"
        "3 |     takes(value)\n"
        "  |           ^^^^^ expected `&String`, found `String`\n"
        "help: consider borrowing here\n"
        "  |\n"
        "3 |     takes(&value)"
    )
    coverage = default_repair_rule_registry().coverage(normalize_artifact_quality_errors([raw])).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_line_suggestion_repair",
        base_files={"src/lib.rs": "fn takes(_: &String) {}\nfn main() {\n    takes(value)\n}\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.line_suggestion" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_line_suggestion_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_line_suggestion_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_field_rename_suggestion_rule_builds_span_text_replace_plan_and_runs_with_editor(
    tmp_path: Path,
) -> None:
    relative_path = "src/lib.rs"
    content = (
        "pub struct Recipe {\n"
        "    pub ingredient: Vec<String>,\n"
        "}\n"
        "pub fn collect(recipe: &Recipe) {\n"
        "    for ingredient in &recipe.ingredients {\n"
        '        println!("{}", ingredient);\n'
        "    }\n"
        "}\n"
    )
    raw = (
        "error[E0609]: no field `ingredients` on type `&Recipe`\n"
        " --> src/lib.rs:5:31\n"
        "  |\n"
        "5 |     for ingredient in &recipe.ingredients {\n"
        "  |                               ^^^^^^^^^^^ unknown field\n"
        "  |\n"
        "help: a field with a similar name exists\n"
        "  |\n"
        "5 -     for ingredient in &recipe.ingredients {\n"
        "5 +     for ingredient in &recipe.ingredient {"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_field_rename_suggestion_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.field_rename_suggestion"
    assert plan.source_tool == "deterministic_rust_field_rename_suggestion_repair"
    assert plan.metadata["span_based"] is True
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "ingredients"
    assert operation.replacement == "ingredient"
    assert operation.before_hash == sha256_text(content)
    assert operation.span_start is not None
    assert operation.span_end is not None
    assert content[operation.span_start : operation.span_end] == "ingredients"
    assert operation.metadata["edit_strategy"] == "text_replace"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["line_number"] == 5
    assert operation.metadata["wrong_field"] == "ingredients"
    assert operation.metadata["correct_field"] == "ingredient"
    assert operation.metadata["unique_context"]

    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    edits: list[tuple[str, str, str]] = []
    writes: list[tuple[str, str]] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append((path, updated))
        raise AssertionError("rust field rename suggestions must execute through editor")

    def editor(edit_operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert edit_operation.span_start is not None
        assert edit_operation.span_end is not None
        assert edit_operation.expected is not None
        assert edit_operation.replacement is not None
        assert current[edit_operation.span_start : edit_operation.span_end] == edit_operation.expected
        updated = current[: edit_operation.span_start] + edit_operation.replacement + current[edit_operation.span_end :]
        target.write_text(updated, encoding="utf-8")
        edits.append((edit_operation.path, edit_operation.expected, edit_operation.replacement))
        return {
            "ok": True,
            "file": edit_operation.path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "edit_file",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-field-rename-suggestion",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_field_rename_suggestion_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(raw,),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert result.error_code is None
    assert writes == []
    assert edits == [(relative_path, "ingredients", "ingredient")]
    repaired = target.read_text(encoding="utf-8")
    assert "&recipe.ingredient" in repaired
    assert "&recipe.ingredients" not in repaired
    assert result.receipts[0].source_tool == "deterministic_rust_field_rename_suggestion_repair"


def test_rust_field_rename_suggestion_coverage_matches_executable_runtime_plan() -> None:
    content = (
        "pub struct Recipe {\n"
        "    pub ingredient: Vec<String>,\n"
        "}\n"
        "pub fn collect(recipe: &Recipe) {\n"
        "    for ingredient in &recipe.ingredients {\n"
        "    }\n"
        "}\n"
    )
    raw = (
        "error[E0609]: no field `ingredients` on type `&Recipe`\n"
        " --> src/lib.rs:5:31\n"
        "  |\n"
        "5 |     for ingredient in &recipe.ingredients {\n"
        "  |\n"
        "help: a field with a similar name exists\n"
        "  |\n"
        "5 -     for ingredient in &recipe.ingredients {\n"
        "5 +     for ingredient in &recipe.ingredient {"
    )
    coverage = default_repair_rule_registry().coverage(normalize_artifact_quality_errors([raw])).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_field_rename_suggestion_repair",
        base_files={"src/lib.rs": content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.field_rename_suggestion" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_field_rename_suggestion_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_field_rename_suggestion_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_java_accessor_alias_rule_builds_canonical_plan_without_diagnostics() -> None:
    content = (
        "package demo;\n"
        "public final class RhythmMonster {\n"
        "    public int getTemperament() {\n"
        "        return 4;\n"
        "    }\n"
        "}\n"
    )

    repaired = repair_java_common_accessor_aliases_text(content)
    plan = build_java_accessor_alias_plan(
        base_files={"./src/main/java/demo/RhythmMonster.java": content},
        diagnostics=(),
        mode="shadow",
    )

    assert "public int temperament()" in repaired
    assert plan is not None
    assert plan.rule_id == "java.common_accessor_aliases"
    assert plan.source_tool == "deterministic_java_accessor_alias_repair"
    assert plan.priority == 1
    assert len(plan.operations) == 1
    assert plan.operations[0].path == "src/main/java/demo/RhythmMonster.java"
    assert plan.operations[0].metadata["repair_kind"] == "java_common_accessor_aliases"


def test_typescript_object_literal_comma_plan_dedupes_same_file_diagnostics() -> None:
    content = (
        "export function summarizeFlight() {\n  return {\n    range\n    maxAltitude: 2\n    landed: true,\n  };\n}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            "TypeScript syntax check failed: src/models/Flight.ts(4,5): error TS1005: ',' expected.",
            "TypeScript syntax check failed: src/models/Flight.ts(5,5): error TS1005: ',' expected.",
        ]
    )

    plan = build_typescript_object_literal_comma_plan(
        base_files={"src/models/Flight.ts": content},
        diagnostics=diagnostics,
    )

    assert plan is not None
    assert len(plan.operations) == 1
    assert len(plan.diagnostics) == 2
    composition = PatchComposer().compose({"src/models/Flight.ts": content}, plan.operations)
    assert composition.ok
    assert "    range,\n" in composition.patches[0].content_after
    assert "    maxAltitude: 2,\n" in composition.patches[0].content_after


def test_typescript_object_literal_comma_rule_repairs_previous_property_line() -> None:
    content = "export function summarizeFlight() {\n  return {\n    range\n    maxAltitude: 2,\n  };\n}\n"

    repaired = repair_typescript_object_literal_commas(content)

    assert "    range,\n" in repaired
    assert "    maxAltitude: 2,\n" in repaired


def test_typescript_object_literal_comma_rule_repairs_property_semicolons() -> None:
    content = (
        "export function summarizeFlower() {\n"
        "  return {\n"
        "    color;\n"
        "    [FlowerType.Moonflower]: 0.9;\n"
        "    wilted: false;\n"
        "  };\n"
        "}\n"
    )

    repaired = repair_typescript_object_literal_commas(content)

    assert "    color,\n" in repaired
    assert "    [FlowerType.Moonflower]: 0.9,\n" in repaired
    assert "    wilted: false,\n" in repaired
    assert "color;\n" not in repaired
    assert "0.9;\n" not in repaired


def test_public_typescript_object_literal_comma_planning_surface_builds_summary() -> None:
    content = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )
    advisory = RepairAdvisoryV1(
        advisor_source="agi",
        message="check the receipt after deterministic planning",
        confidence=0.4,
        metadata={"evidence_ref": "runtime/receipts/latest.json"},
    )

    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_return_object_semicolon_repair",
            base_files={"src/models/Flight.ts": content},
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected.",
            ),
            advisor_notes=(advisory,),
            mode="shadow",
        )
    )
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairPlanningResultV1)
    assert payload["schema_version"] == "director.repair_planning_result.v1"
    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["agi_execution_authority"] is False
    assert payload["advisory_authoritative"] is False
    assert payload["director_tool_execution_required"] is True
    assert payload["plan_summary"]["rule_id"] == "typescript.object_literal_missing_comma"
    assert payload["plan_summary"]["source_tool"] == "deterministic_typescript_return_object_semicolon_repair"
    assert payload["plan_summary"]["mode"] == "shadow"
    assert payload["plan_summary"]["operation_count"] == 1
    assert payload["plan_summary"]["advisor_note_count"] == 1
    assert payload["plan_summary"]["agi_execution_authority"] is False
    assert payload["plan_summary"]["advisory_authoritative"] is False
    assert payload["composition_summary"]["ok"] is True
    assert payload["composition_summary"]["patch_count"] == 1
    assert payload["composition_summary"]["issue_count"] == 0
    patch = payload["composition_summary"]["patches"][0]
    assert patch["path"] == "src/models/Flight.ts"
    assert patch["changed"] is True
    assert "flightTime, landed:" in patch["content_after"]
    assert payload["advisor_notes"] == [
        {
            "advisor_source": "agi",
            "message": "check the receipt after deterministic planning",
            "confidence": 0.4,
            "authoritative": False,
            "suggested_rules": [],
            "metadata": {"evidence_ref": "runtime/receipts/latest.json"},
        }
    ]


def test_public_typescript_object_literal_comma_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "src/models/Flight.ts"
    target = tmp_path / relative_path
    content = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-ts1005",
            workspace=str(tmp_path),
            source_tool="deterministic_typescript_return_object_semicolon_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected.",
            ),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_typescript_return_object_semicolon_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "flightTime, landed:" in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_go_bare_import_string_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "cmd/app/main.go"
    target = tmp_path / relative_path
    content = 'package main\n\n"fmt"\n\nfunc main() {}\n'
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-go-import",
            workspace=str(tmp_path),
            source_tool="deterministic_go_bare_import_string_repair",
            base_files={relative_path: content},
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_go_bare_import_string_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert 'import "fmt"' in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_go_subpath_run_uses_precise_editor_and_receipt(tmp_path: Path) -> None:
    relative_path = "cmd/app/main.go"
    package_path = "src/engine/engine.go"
    go_mod_path = "go.mod"
    content = 'package main\n\nimport (\n    "example.com/demo/pet-ascii/src/engine"\n)\n\nfunc main() {}\n'
    base_files = {
        go_mod_path: "module example.com/demo\n",
        relative_path: content,
        package_path: "package engine\n",
    }
    for path, text in base_files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    writes: list[str] = []
    edits: list[RepairOperation] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append(path)
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    def editor(operation: RepairOperation) -> dict[str, object]:
        edit_target = tmp_path / operation.path
        current = edit_target.read_text(encoding="utf-8")
        assert operation.span_start is not None
        assert operation.span_end is not None
        assert current[operation.span_start : operation.span_end] == operation.expected
        updated = current[: operation.span_start] + str(operation.replacement or "") + current[operation.span_end :]
        edit_target.write_text(updated, encoding="utf-8")
        edits.append(operation)
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-go-subpath",
            workspace=str(tmp_path),
            source_tool="deterministic_go_subpath_repair",
            base_files=base_files,
            allowed_paths=tuple(base_files),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert result.error_code is None
    assert writes == []
    assert [operation.metadata["repair_kind"] for operation in edits] == ["go_import_subpath"]
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.source_tool == "deterministic_go_subpath_repair"
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text((tmp_path / relative_path).read_text(encoding="utf-8"))
    assert '"example.com/demo/src/engine"' in (tmp_path / relative_path).read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["planning"]["plan_summary"]["rule_id"] == "go.import_subpath"
    assert result.metadata["planning"]["plan_summary"]["source_tool"] == "deterministic_go_subpath_repair"
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None
    assert receipt.metadata["execution_records"][0]["operation"] == "edit_file"
    assert receipt.metadata["precise_edit_strategy_by_path"][relative_path]["strategy"] == "span_based"
    assert receipt.metadata["precise_edit_strategy_by_path"][relative_path]["editor_used"] is True


def test_public_go_bare_local_import_missing_go_mod_fails_closed_without_writes(tmp_path: Path) -> None:
    relative_path = "cmd/app/main.go"
    content = 'package main\n\nimport "src/models"\n'
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    writes: list[str] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append(path)
        (tmp_path / path).write_text(updated, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "modify"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-go-bare-local-fail-closed",
            workspace=str(tmp_path),
            source_tool="deterministic_go_bare_import_repair",
            base_files={relative_path: content},
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is False
    assert result.error_code == "repair_not_planned"
    assert result.receipts == ()
    assert writes == []
    assert target.read_text(encoding="utf-8") == content
    assert result.metadata["planning"]["source_tool"] == "deterministic_go_bare_import_repair"
    assert result.metadata["planning"]["planned"] is False


def test_public_rust_dependency_run_executes_with_receipt(tmp_path: Path) -> None:
    cargo_path = "Cargo.toml"
    source_path = "src/main.rs"
    cargo = '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\n'
    source = "use serde::Serialize;\nfn main() { let _ = serde_json::json!({}); }\n"
    target = tmp_path / cargo_path
    source_target = tmp_path / source_path
    source_target.parent.mkdir(parents=True)
    target.write_text(cargo, encoding="utf-8")
    source_target.write_text(source, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-deps",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_dependency_repair",
            base_files={cargo_path: cargo, source_path: source},
            artifact_quality_errors=("error[E0432]: unresolved import `serde`",),
            allowed_paths=(cargo_path, source_path),
        ),
        writer=writer,
    )
    repaired = target.read_text(encoding="utf-8")

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_rust_dependency_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (cargo_path,)
    assert receipt.before_hashes[cargo_path] == sha256_text(cargo)
    assert receipt.after_hashes[cargo_path] == sha256_text(repaired)
    assert 'serde = { version = "1.0", features = ["derive"] }' in repaired
    assert 'serde_json = "1.0"' in repaired
    assert result.metadata["planning"]["plan_summary"]["rule_id"] == "rust.unlinked_crate_dependency"
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_rust_missing_binary_entrypoint_run_creates_file_with_receipt(tmp_path: Path) -> None:
    cargo_path = "Cargo.toml"
    binary_path = "src/bin/demo.rs"
    cargo = '[package]\nname = "demo-app"\nversion = "0.1.0"\n\n[[bin]]\nname = "demo-cli"\npath = "src/bin/demo.rs"\n'
    target = tmp_path / cargo_path
    target.write_text(cargo, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.parent.mkdir(parents=True, exist_ok=True)
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "create",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-bin",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_missing_binary_entrypoint_repair",
            base_files={cargo_path: cargo},
            allowed_paths=(cargo_path, binary_path),
        ),
        writer=writer,
    )
    created = (tmp_path / binary_path).read_text(encoding="utf-8")

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.rule_id == "rust.missing_binary_entrypoint"
    assert receipt.source_tool == "deterministic_rust_missing_binary_entrypoint_repair"
    assert receipt.files_changed == (binary_path,)
    assert receipt.before_hashes[binary_path] == sha256_text("")
    assert receipt.after_hashes[binary_path] == sha256_text(created)
    assert "demo_app binary entry point" in created
    assert receipt.metadata["write_file_reasons_by_path"][binary_path] == "new_file_or_empty_file"
    assert receipt.metadata["execution_records"][0]["write_file_reason"] == "new_file_or_empty_file"
    assert receipt.metadata["execution_records"][0]["rollback_restore_strategy"]
    assert result.metadata["planning"]["plan_summary"]["rule_id"] == "rust.missing_binary_entrypoint"
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_cpp_include_path_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "src/engine/generator.cpp"
    header_path = "src/models/postcard.hpp"
    target = tmp_path / relative_path
    header = tmp_path / header_path
    content = '#include "src/models/postcard.hpp"\n#include <string>\n'
    target.parent.mkdir(parents=True)
    header.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    header.write_text("#pragma once\n", encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-cpp-include",
            workspace=str(tmp_path),
            source_tool="deterministic_cpp_include_path_repair",
            base_files={relative_path: content, header_path: "#pragma once\n"},
            allowed_paths=(relative_path, header_path),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_cpp_include_path_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert '#include "../models/postcard.hpp"' in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_cpp_standard_include_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "src/models/seed.hpp"
    target = tmp_path / relative_path
    content = "#pragma once\nnamespace demo { std::uint32_t seed(); }\n"
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-cpp-standard-include",
            workspace=str(tmp_path),
            source_tool="deterministic_cpp_standard_include_repair",
            base_files={relative_path: content},
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_cpp_standard_include_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "#include <cstdint>" in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_cpp_missing_private_members_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "src/models/poem.hpp"
    target = tmp_path / relative_path
    content = (
        "#pragma once\n"
        "#include <string>\n"
        "namespace demo {\n"
        "class Poem {\n"
        "public:\n"
        "    const std::string& title() const noexcept { return title_; }\n"
        "};\n"
        "}\n"
    )
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-cpp-private-members",
            workspace=str(tmp_path),
            source_tool="deterministic_cpp_missing_private_members_repair",
            base_files={relative_path: content},
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_cpp_missing_private_members_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "private:\n    std::string title_;" in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_cpp_placeholder_declaration_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "src/engine/generator.hpp"
    target = tmp_path / relative_path
    content = (
        "#pragma once\n"
        "namespace demo {\n"
        "class Generator {\n"
        "public:\n"
        "    std::render_return_type /* placeholder */ render_html() const = delete;\n"
        "};\n"
        "}\n"
    )
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-cpp-placeholder",
            workspace=str(tmp_path),
            source_tool="deterministic_cpp_placeholder_declaration_repair",
            base_files={relative_path: content},
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_cpp_placeholder_declaration_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "std::render_return_type" not in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_cpp_struct_getter_field_access_run_executes_with_receipt(tmp_path: Path) -> None:
    header_path = "src/models/postcard.hpp"
    source_path = "src/main.cpp"
    header = "#pragma once\nnamespace demo {\nstruct Postcard {\n    int poem;\n};\n}\n"
    source = (
        '#include "models/postcard.hpp"\nint main() {\n    demo::Postcard card{};\n    return card.get_poem();\n}\n'
    )
    target = tmp_path / source_path
    (tmp_path / header_path).parent.mkdir(parents=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / header_path).write_text(header, encoding="utf-8")
    target.write_text(source, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-cpp-struct-getter",
            workspace=str(tmp_path),
            source_tool="deterministic_cpp_struct_getter_field_access_repair",
            base_files={header_path: header, source_path: source},
            allowed_paths=(header_path, source_path),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_cpp_struct_getter_field_access_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (source_path,)
    assert receipt.before_hashes[source_path] == sha256_text(source)
    assert receipt.after_hashes[source_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "card.poem" in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_java_accessor_alias_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "src/main/java/demo/RhythmMonster.java"
    target = tmp_path / relative_path
    content = (
        "package demo;\n"
        "public final class RhythmMonster {\n"
        "    public int getTemperament() {\n"
        "        return 4;\n"
        "    }\n"
        "}\n"
    )
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-java-accessor",
            workspace=str(tmp_path),
            source_tool="deterministic_java_accessor_alias_repair",
            base_files={relative_path: content},
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_java_accessor_alias_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "public int temperament()" in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_repair_run_normalizes_dot_slash_paths_for_policy_and_planning(tmp_path: Path) -> None:
    relative_path = "src/models/Flight.ts"
    target = tmp_path / relative_path
    content = "export function summarizeFlight() {\n  return {\n    range\n    maxAltitude: 2,\n  };\n}\n"
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    write_paths: list[str] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        write_paths.append(path)
        (tmp_path / path).write_text(updated, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "modify"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-ts1005-dot-path",
            workspace=str(tmp_path),
            source_tool="deterministic_typescript_return_object_semicolon_repair",
            base_files={f"./{relative_path}": content},
            artifact_quality_errors=(
                f"TypeScript syntax check failed: ./{relative_path}(4,5): error TS1005: ',' expected.",
            ),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.receipts[0].files_changed == (relative_path,)
    assert write_paths == [relative_path]
    assert "    range,\n" in target.read_text(encoding="utf-8")


def test_public_repair_run_preserves_execution_failure_receipt_metadata(tmp_path: Path) -> None:
    relative_path = "src/models/Flight.ts"
    target = tmp_path / relative_path
    content = "export function summarizeFlight() {\n  return {\n    range\n    maxAltitude: 2,\n  };\n}\n"
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def rejecting_writer(path: str, updated: str) -> dict[str, object]:
        del path, updated
        return {"ok": False, "operation": "modify"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-ts1005-writer-rejected",
            workspace=str(tmp_path),
            source_tool="deterministic_typescript_return_object_semicolon_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(
                f"TypeScript syntax check failed: {relative_path}(4,5): error TS1005: ',' expected.",
            ),
            allowed_paths=(relative_path,),
        ),
        writer=rejecting_writer,
    )

    assert result.ok is False
    assert result.error_code == "repair_execution_failed"
    assert len(result.receipts) == 1
    assert result.receipts[0].status == "failed"
    assert result.metadata["execution_error"] == f"repair writer rejected {relative_path}"
    assert target.read_text(encoding="utf-8") == content


def test_public_repair_run_projects_non_authoritative_advisory_suggested_rules(tmp_path: Path) -> None:
    relative_path = "src/models/Flight.ts"
    target = tmp_path / relative_path
    content = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )
    suggested_rule = {
        "name": "rust_borrow_marker_self",
        "language": "rust",
        "pattern": r"found `&\\)` near method receiver",
        "fix_template": "replace `(&)` with `(&self)` only inside Rust impl method receivers",
        "confidence": 0.83,
        "evidence": ["src/lib.rs:12: expected one of `self`, `&self`, or `&mut self`"],
    }
    advisory = RepairAdvisoryV1(
        advisor_source="agi",
        message="suggest a future repair rule; do not execute it",
        suggested_rules=(suggested_rule,),
        metadata={"evidence_ref": "runtime/repair-coverage/rust.json"},
    )
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        (tmp_path / path).write_text(updated, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "modify"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-ts1005-advisory",
            workspace=str(tmp_path),
            source_tool="deterministic_typescript_return_object_semicolon_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected.",
            ),
            allowed_paths=(relative_path,),
            advisor_notes=(advisory,),
        ),
        writer=writer,
    )
    receipt_note = result.receipts[0].advisor_notes[0].to_dict()

    assert result.ok is True
    assert receipt_note["authoritative"] is False
    assert receipt_note["suggested_rules"][0]["name"] == "rust_borrow_marker_self"
    assert receipt_note["metadata"] == {"evidence_ref": "runtime/repair-coverage/rust.json"}
    assert result.metadata["planning"]["advisor_notes"][0]["suggested_rules"][0]["language"] == "rust"


def test_patch_composer_fails_closed_on_json_scalar_parent() -> None:
    base = {"package.json": '{"scripts":"npm test"}\n'}
    operations = [
        RepairOperation(kind="json_set", path="package.json", json_path=("scripts", "test"), value="vitest"),
    ]

    result = PatchComposer().compose(base, operations)

    assert not result.ok
    assert result.patches == ()
    assert result.issues[0].code == "json_path_parent_not_object"


def test_patch_composer_fails_closed_on_json_delete_missing_path() -> None:
    base = {"package.json": '{"scripts":{"test":"vitest"}}\n'}
    operations = [
        RepairOperation(kind="json_delete", path="package.json", json_path=("scripts", "build")),
    ]

    result = PatchComposer().compose(base, operations)

    assert not result.ok
    assert result.patches == ()
    assert result.issues[0].code == "json_path_not_found"


def test_patch_composer_fails_closed_without_partial_json_patch() -> None:
    base = {"package.json": '{"scripts":{"test":"vitest"}}\n'}
    operations = [
        RepairOperation(kind="json_set", path="package.json", json_path=("scripts", "build"), value="vite build"),
        RepairOperation(kind="json_delete", path="package.json", json_path=("scripts", "missing")),
    ]

    result = PatchComposer().compose(base, operations)

    assert not result.ok
    assert result.patches == ()
    assert [issue.code for issue in result.issues] == ["json_path_not_found"]


def test_policy_gate_blocks_advisor_authority_and_cycles() -> None:
    diagnostic = RepairDiagnostic(source="artifact_quality", code="ts", message="x", path="src/app.ts")
    first_plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        diagnostics=(diagnostic,),
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="export const x = 1;\n"),),
    )
    previous = (
        TransactionalRepairExecutor()
        .execute(
            workspace=Path("."),
            plan=RepairPlan(
                rule_id=first_plan.rule_id,
                source_tool=first_plan.source_tool,
                diagnostics=first_plan.diagnostics,
                operations=first_plan.operations,
                mode="shadow",
            ),
            composition=PatchComposer().compose({"src/app.ts": ""}, first_plan.operations),
        )
        .receipt
    )
    blocked_plan = RepairPlan(
        rule_id=first_plan.rule_id,
        source_tool=first_plan.source_tool,
        diagnostics=first_plan.diagnostics,
        operations=first_plan.operations,
        advisor_notes=(RepairAdvisorNote(source="agi", message="override policy", authoritative=True),),
    )

    assert blocked_plan.advisor_notes[0].authoritative is False
    assert blocked_plan.advisor_notes[0].metadata["requested_authoritative"] is True
    assert blocked_plan.advisor_notes[0].to_dict()["authoritative"] is False

    decision = RepairPolicyGate().evaluate_plan(
        blocked_plan,
        RepairPolicyContext(previous_receipts=(previous,), max_rule_activations=1),
    )

    assert not decision.allowed
    assert "advisor_note_marked_authoritative" in decision.reasons
    assert "rule_activation_cycle_breaker" in decision.reasons


def test_policy_gate_blocks_forbidden_composed_content() -> None:
    plan = RepairPlan(
        rule_id="rule.py",
        source_tool="deterministic_python_unittest_runtime_failure_repair",
        operations=(
            RepairOperation(kind="write_file", path="tests/test_app.py", content="import pytest\npytest.skip()\n"),
        ),
    )
    composition = PatchComposer().compose({"tests/test_app.py": ""}, plan.operations)

    decision = RepairPolicyGate().evaluate_composition(plan, composition)

    assert not decision.allowed
    assert decision.reasons == ("forbidden_repair_content:tests/test_app.py",)


def test_executor_shadow_mode_does_not_write(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app.ts"
    target.parent.mkdir()
    target.write_text("old\n", encoding="utf-8")
    before = target.read_text(encoding="utf-8")
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        mode="shadow",
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="new\n"),),
    )
    composition = PatchComposer().compose({"src/app.ts": before}, plan.operations)

    result = TransactionalRepairExecutor().execute(workspace=tmp_path, plan=plan, composition=composition)

    assert result.ok
    assert result.receipt.status == "shadow_observed"
    assert result.receipt.authoritative is False
    assert target.read_text(encoding="utf-8") == before


def test_executor_commit_requires_policy_gated_writer_or_editor(tmp_path: Path) -> None:
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="new\n"),),
    )
    composition = PatchComposer().compose({"src/app.ts": ""}, plan.operations)

    result = TransactionalRepairExecutor().execute(workspace=tmp_path, plan=plan, composition=composition)

    assert not result.ok
    assert result.error == "commit_requires_policy_gated_writer_or_editor"
    assert result.receipt.authoritative is False


def test_executor_commit_uses_writer_and_records_hashes(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app.ts"
    target.parent.mkdir()
    target.write_text("old\n", encoding="utf-8")
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="new\n"),),
    )
    composition = PatchComposer().compose({"src/app.ts": "old\n"}, plan.operations)

    def writer(path: str, content: str) -> dict[str, bool]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
    )

    assert result.ok
    assert target.read_text(encoding="utf-8") == "new\n"
    _assert_direct_runtime_receipt_pending_revalidation(result.receipt)
    assert result.receipt.before_hashes["src/app.ts"] == sha256_text("old\n")
    assert result.receipt.after_hashes["src/app.ts"] == sha256_text("new\n")


def test_executor_falls_back_to_writer_when_first_editor_operation_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app.ts"
    target.parent.mkdir()
    target.write_text("old\n", encoding="utf-8")
    operation = RepairOperation(
        kind="text_replace",
        path="src/app.ts",
        span_start=0,
        span_end=4,
        expected="old\n",
        replacement="new\nold\n",
        before_hash=sha256_text("old\n"),
        metadata={"repair_kind": "insert_prefix", "precision_strategy": "span_context_text_patch"},
    )
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(operation,),
    )
    composition = PatchComposer().compose({"src/app.ts": "old\n"}, plan.operations)
    writer_calls: list[tuple[str, str]] = []
    editor_calls: list[str] = []

    def editor(edit_operation: RepairOperation) -> dict[str, bool]:
        editor_calls.append(edit_operation.operation_id)
        return {"ok": False}

    def writer(path: str, content: str) -> dict[str, bool]:
        writer_calls.append((path, content))
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
        editor=editor,
    )

    assert result.ok
    assert editor_calls == [operation.operation_id]
    assert writer_calls == [("src/app.ts", "new\nold\n")]
    assert target.read_text(encoding="utf-8") == "new\nold\n"
    assert result.receipt.metadata["execution_records"][0]["operation"] == "write_file"


def test_executor_reports_rollback_failed_when_restore_is_rejected(tmp_path: Path) -> None:
    app = tmp_path / "src" / "app.ts"
    config = tmp_path / "src" / "config.ts"
    app.parent.mkdir()
    app.write_text("old app\n", encoding="utf-8")
    config.write_text("old config\n", encoding="utf-8")
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(
            RepairOperation(kind="write_file", path="src/app.ts", content="new app\n"),
            RepairOperation(kind="write_file", path="src/config.ts", content="new config\n"),
        ),
    )
    composition = PatchComposer().compose(
        {"src/app.ts": "old app\n", "src/config.ts": "old config\n"},
        plan.operations,
    )
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, bool]:
        write_calls.append((path, content))
        if path == "src/config.ts":
            return {"ok": False}
        if content == "old app\n":
            return {"ok": False}
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
    )

    assert not result.ok
    assert result.rolled_back is False
    assert result.receipt.status == "rollback_failed"
    assert result.receipt.metadata["rollback_attempted"] is True
    assert result.receipt.metadata["rollback_failed_paths"] == ["src/app.ts"]
    assert write_calls == [("src/app.ts", "new app\n"), ("src/config.ts", "new config\n"), ("src/app.ts", "old app\n")]


def test_receipt_context_marks_agi_advisory_non_authoritative(tmp_path: Path) -> None:
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        mode="shadow",
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="new\n"),),
        advisor_notes=(RepairAdvisorNote(source="agi", message="consider import boundary", confidence=0.7),),
    )
    composition = PatchComposer().compose({"src/app.ts": ""}, plan.operations)
    result = TransactionalRepairExecutor().execute(workspace=tmp_path, plan=plan, composition=composition)

    context = build_repair_receipt_context([result.receipt])

    assert context["agi_advisory_supported"] is True
    assert context["agi_advisory_active"] is False
    assert context["agi_advisory_authoritative"] is False
    assert context["agi_advisory_writes_allowed"] is False
    assert context["agi_advisory_registration_allowed"] is False
    assert context["agi_advisory_authoritative_receipts_allowed"] is False
    assert context["receipts"][0]["advisor_notes"] == [
        {
            "source": "agi",
            "confidence": 0.7,
            "advisory_only": True,
            "authoritative": False,
            "director_runtime_remains_authoritative": True,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "registration_allowed": False,
            "authoritative_receipts_allowed": False,
            "suggested_rules_are_advisory_only": True,
        }
    ]


def test_public_repair_metrics_project_success_rounds_coverage_and_agi_boundaries() -> None:
    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=("futurelang compiler: error FX9999: unknown repair gap",),
        )
    )
    result = project_director_repair_metrics(
        ProjectDirectorRepairMetricsV1(
            receipts=(
                RepairReceiptV1(
                    receipt_id="receipt_ok",
                    plan_id="plan_ok",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="applied",
                    authoritative=True,
                    errors_before=3,
                    errors_after=1,
                    net_error_reduction=2,
                ),
                RepairReceiptV1(
                    receipt_id="receipt_ineffective",
                    plan_id="plan_ineffective",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="applied",
                    authoritative=True,
                    errors_before=1,
                    errors_after=1,
                    net_error_reduction=0,
                ),
                RepairReceiptV1(
                    receipt_id="receipt_failed",
                    plan_id="plan_failed",
                    source_tool="deterministic_go_module_import_repair",
                    status="failed_revalidation",
                    authoritative=True,
                    errors_before=2,
                    errors_after=2,
                    net_error_reduction=0,
                ),
            ),
            coverage_reports=(coverage,),
            schedule_run_summaries=({"rounds_run": 2}, {"rounds_run": 4}),
        )
    )
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairMetricsResultV1)
    assert payload["schema_version"] == "director.repair_metrics.v1"
    assert payload["access"] == "read_only"
    assert payload["advisory_only"] is True
    assert payload["agi_execution_authority"] is False
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["receipt_count"] == 3
    assert payload["applied_receipt_count"] == 2
    assert payload["failed_receipt_count"] == 1
    assert payload["ineffective_receipt_count"] == 2
    assert payload["success_rate"] == pytest.approx(2 / 3)
    assert payload["average_convergence_rounds"] == pytest.approx(3.0)
    assert payload["uncovered_diagnostic_count"] == 1
    assert payload["coverage_gap_count"] == 1
    assert payload["metadata"]["failed_receipt_ids"] == ["receipt_failed"]
    assert payload["metadata"]["ineffective_receipt_ids"] == ["receipt_ineffective", "receipt_failed"]
    assert payload["metadata"]["schedule_rounds"] == [2, 4]


def test_advisor_overlay_does_not_change_authority_hash(tmp_path: Path) -> None:
    base_plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        mode="shadow",
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="new\n"),),
    )
    advised_plan = RepairPlan(
        rule_id=base_plan.rule_id,
        source_tool=base_plan.source_tool,
        mode=base_plan.mode,
        operations=base_plan.operations,
        advisor_notes=(RepairAdvisorNote(source="agi", message="read-only hint", confidence=0.5),),
    )
    composer = PatchComposer()
    executor = TransactionalRepairExecutor()
    base_receipt = executor.execute(
        workspace=tmp_path,
        plan=base_plan,
        composition=composer.compose({"src/app.ts": ""}, base_plan.operations),
    ).receipt
    advised_receipt = executor.execute(
        workspace=tmp_path,
        plan=advised_plan,
        composition=composer.compose({"src/app.ts": ""}, advised_plan.operations),
    ).receipt

    assert base_receipt.authority_hash() == advised_receipt.authority_hash()
    assert base_receipt.projection_hash() != advised_receipt.projection_hash()


@pytest.mark.parametrize(
    "field",
    sorted(FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS),
)
def test_public_advisory_contract_rejects_authoritative_fields(field: str) -> None:
    with pytest.raises(ValueError, match="forbidden authoritative fields"):
        RepairAdvisoryV1(
            advisor_source="agi",
            message="try this",
            metadata={field: "not allowed"},
        )


@pytest.mark.parametrize(
    "field",
    sorted(FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS),
)
def test_internal_advisor_note_rejects_authoritative_fields(field: str) -> None:
    with pytest.raises(ValueError, match="forbidden authoritative fields"):
        RepairAdvisorNote(
            source="agi",
            message="read-only evidence only",
            metadata={field: "not allowed"},
        )


def test_advisory_contracts_allow_non_authoritative_evidence_refs() -> None:
    public_note = RepairAdvisoryV1(
        advisor_source="agi",
        message="inspect latest receipt",
        metadata={"evidence_ref": "runtime/receipts/repair.json"},
    )
    internal_note = RepairAdvisorNote(
        source="agi",
        message="inspect latest receipt",
        metadata={"evidence_ref": "runtime/receipts/repair.json"},
    )

    assert public_note.metadata == {"evidence_ref": "runtime/receipts/repair.json"}
    assert internal_note.metadata == {"evidence_ref": "runtime/receipts/repair.json"}


def test_advisory_contracts_allow_non_authoritative_suggested_rules() -> None:
    suggested_rule = {
        "name": "rust_borrow_marker_self",
        "language": "rust",
        "pattern": r"found `&\\)` near method receiver",
        "fix_template": "replace `(&)` with `(&self)` only inside Rust impl method receivers",
        "confidence": 0.83,
        "evidence": ["src/lib.rs:12: expected one of `self`, `&self`, or `&mut self`"],
        "rationale": "LLMs often omit `self` in Rust receiver syntax.",
    }

    public_note = RepairAdvisoryV1(
        advisor_source="agi",
        message="suggest a future Rust syntax repair rule",
        suggested_rules=(suggested_rule,),
    )
    internal_note = RepairAdvisorNote(
        source="agi",
        message="suggest a future Rust syntax repair rule",
        suggested_rules=(suggested_rule,),
    )

    assert public_note.to_dict()["authoritative"] is False
    assert public_note.to_dict()["suggested_rules"][0]["pattern"] == suggested_rule["pattern"]
    internal_payload = internal_note.to_dict()
    assert internal_payload["advisory_only"] is True
    assert internal_payload["authoritative"] is False
    assert internal_payload["director_runtime_remains_authoritative"] is True
    assert internal_payload["agi_execution_authority"] is False
    assert internal_payload["writes_allowed"] is False
    assert internal_payload["registration_allowed"] is False
    assert internal_payload["authoritative_receipts_allowed"] is False
    assert internal_payload["suggested_rules_are_advisory_only"] is True
    assert internal_payload["suggested_rules"][0]["fix_template"] == suggested_rule["fix_template"]


@pytest.mark.parametrize(
    "field",
    sorted(FORBIDDEN_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS),
)
def test_advisory_suggested_rules_reject_authoritative_fields(field: str) -> None:
    with pytest.raises(ValueError, match="forbidden authoritative fields"):
        RepairAdvisoryV1(
            advisor_source="agi",
            message="malicious rule",
            suggested_rules=(
                {
                    "pattern": "x",
                    "fix_template": "y",
                    field: "not allowed",
                },
            ),
        )


def test_public_repair_advisory_policy_exposes_read_only_agi_boundaries() -> None:
    result = query_director_repair_advisory_policy(QueryDirectorRepairAdvisoryPolicyV1())
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairAdvisoryPolicyResultV1)
    assert payload["schema_version"] == "director.repair_advisory_policy.v1"
    assert payload["source"] == "director.runtime.repair_kernel.advisory_policy"
    assert payload["access"] == "read_only"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["execution_boundary"] == "read_only_advisory_no_writes_no_registration"
    assert payload["agi_execution_authority"] is False
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["authoritative_receipts_allowed"] is False
    assert "pattern" in payload["allowed_suggested_rule_fields"]
    assert "fix_template" in payload["allowed_suggested_rule_fields"]
    assert "repair_plan" in payload["forbidden_metadata_fields"]
    assert "write_file" in payload["forbidden_suggested_rule_fields"]
    assert payload["summary"]["suggested_rules_allowed"] is True
    assert payload["summary"]["director_runtime_remains_authoritative"] is True


def test_public_repair_advisory_validation_normalizes_non_authoritative_suggestions() -> None:
    result = validate_director_repair_advisory(
        QueryDirectorRepairAdvisoryValidationV1(
            advisor_source="resident_agi",
            message="Potential recurring shell quoting diagnostic.",
            confidence=0.9,
            suggested_rules=(
                {
                    "pattern": "shellcheck SC2086",
                    "fix_template": "quote variable expansions",
                    "language": "shell",
                    "confidence": 0.75,
                    "evidence": ["scripts/deploy.sh:12"],
                },
            ),
            metadata={"run_id": "run-1"},
        )
    )
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairAdvisoryValidationResultV1)
    assert payload["schema_version"] == "director.repair_advisory_validation.v1"
    assert payload["access"] == "read_only"
    assert payload["ok"] is True
    assert payload["agi_execution_authority"] is False
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["authoritative_receipts_allowed"] is False
    assert payload["normalized_advisory"]["authoritative"] is False
    assert payload["normalized_advisory"]["suggested_rules"][0]["language"] == "shell"
    forbidden_capability_fields = {
        "mode",
        "operation",
        "operations",
        "patch",
        "patches",
        "policy_override",
        "receipt",
        "receipts",
        "registered",
        "repair_plan",
        "rule_id",
        "source_tool",
        "success_verdict",
        "write_file",
    }
    assert forbidden_capability_fields.isdisjoint(payload)
    assert forbidden_capability_fields.isdisjoint(payload["normalized_advisory"])
    assert forbidden_capability_fields.isdisjoint(payload["normalized_advisory"]["suggested_rules"][0])
    assert payload["summary"]["accepted_suggested_rule_count"] == 1
    assert payload["summary"]["advisory_only"] is True
    assert payload["summary"]["suggested_rules_are_advisory_only"] is True
    assert payload["summary"]["agi_execution_authority"] is False
    assert payload["summary"]["writes_allowed"] is False
    assert payload["summary"]["registration_allowed"] is False
    assert payload["summary"]["authoritative_receipts_allowed"] is False
    assert payload["summary"]["director_runtime_remains_authoritative"] is True


def test_public_repair_advisory_validation_rejects_authoritative_fields() -> None:
    result = validate_director_repair_advisory(
        QueryDirectorRepairAdvisoryValidationV1(
            advisor_source="resident_agi",
            message="Attempt to smuggle authoritative fields.",
            suggested_rules=(
                {
                    "pattern": "x",
                    "fix_template": "y",
                    "source_tool": "deterministic_future_repair",
                    "patch": "*** Begin Patch",
                },
            ),
            metadata={"success_verdict": True},
        )
    )
    payload = result.to_dict()

    assert payload["ok"] is False
    assert payload["normalized_advisory"] is None
    assert payload["agi_execution_authority"] is False
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["authoritative_receipts_allowed"] is False
    forbidden_capability_fields = {
        "operation",
        "operations",
        "patch",
        "patches",
        "receipt",
        "receipts",
        "registered",
        "repair_plan",
        "source_tool",
        "success_verdict",
        "write_file",
    }
    assert forbidden_capability_fields.isdisjoint(payload)
    assert payload["summary"]["accepted_suggested_rule_count"] == 0
    assert payload["summary"]["advisory_only"] is True
    assert payload["summary"]["suggested_rules_are_advisory_only"] is True
    assert payload["summary"]["agi_execution_authority"] is False
    assert payload["summary"]["writes_allowed"] is False
    assert payload["summary"]["registration_allowed"] is False
    assert payload["summary"]["authoritative_receipts_allowed"] is False
    assert payload["summary"]["director_runtime_remains_authoritative"] is True
    assert "forbidden authoritative fields" in payload["errors"][0]


def test_public_shadow_comparison_is_read_only_and_reports_scope_match() -> None:
    result = compare_director_repair_shadow_run(
        CompareDirectorRepairShadowRunV1(
            baseline_tool_results=(
                {
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/app.ts",
                    },
                },
            ),
            kernel_receipts=(
                RepairReceiptV1(
                    receipt_id="receipt_1",
                    plan_id="plan_1",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="shadow_observed",
                    authoritative=False,
                    files_changed=("src/app.ts",),
                    metadata={"mode": "shadow"},
                ),
            ),
        )
    )
    payload = result.to_dict()

    assert payload["schema_version"] == "director.repair_shadow_comparison.v1"
    assert payload["source"] == "director.runtime.repair_kernel.shadow"
    assert payload["access"] == "read_only"
    assert payload["execution_boundary"] == "read_only_shadow_comparison_no_writes"
    assert payload["agi_execution_authority"] is False
    assert payload["writes_allowed"] is False
    assert payload["comparison_mode"] == "independent_shadow_run"
    assert payload["independent_shadow_required"] is True
    assert payload["independent_shadow_satisfied"] is True
    assert payload["matched"] is True
    assert payload["cutover_ready"] is False
    assert payload["cutover_blockers"] == [
        "missing_before_after_hash_evidence",
        "missing_revalidation_evidence",
        "non_authoritative_kernel_receipt",
    ]
    assert payload["missing_paths_in_kernel"] == []
    assert payload["extra_paths_in_kernel"] == []
    assert payload["metadata"]["writes_performed"] is False
    assert payload["metadata"]["cutover_readiness"]["comparison_mode"] == "independent_shadow_run"
    assert payload["metadata"]["cutover_readiness"]["independent_shadow_required"] is True
    assert payload["metadata"]["cutover_readiness"]["independent_shadow_satisfied"] is True


def test_public_shadow_comparison_requires_hash_and_revalidation_for_cutover() -> None:
    result = compare_director_repair_shadow_run(
        CompareDirectorRepairShadowRunV1(
            baseline_tool_results=(
                {
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/app.ts",
                        "before_hash": "before123",
                        "after_hash": "after456",
                    },
                },
            ),
            kernel_receipts=(
                RepairReceiptV1(
                    receipt_id="receipt_1",
                    plan_id="plan_1",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="applied",
                    authoritative=True,
                    files_changed=("src/app.ts",),
                    before_hashes={"src/app.ts": "before123"},
                    after_hashes={"src/app.ts": "after456"},
                    revalidation_evidence={
                        "command": ["tsc", "--noEmit"],
                        "exit_code": 0,
                        "errors_before": 1,
                        "errors_after": 0,
                    },
                    metadata={"mode": "commit"},
                ),
            ),
        )
    )
    payload = result.to_dict()

    assert payload["matched"] is True
    assert payload["comparison_mode"] == "independent_shadow_run"
    assert payload["independent_shadow_satisfied"] is True
    assert payload["cutover_ready"] is True
    assert payload["cutover_blockers"] == []
    assert payload["metadata"]["cutover_readiness"]["hashes_matched"] is True
    assert payload["metadata"]["cutover_readiness"]["revalidation_evidence_complete"] is True
    assert payload["metadata"]["cutover_readiness"]["revalidation_evidence_passed"] is True
    assert payload["metadata"]["cutover_readiness"]["authoritative_receipts"] is True
    assert payload["metadata"]["cutover_readiness"]["independent_shadow_satisfied"] is True


def _ready_shadow_comparison(path: str = "src/app.ts") -> DirectorRepairShadowComparisonResultV1:
    return DirectorRepairShadowComparisonResultV1(
        schema_version="director.repair_shadow_comparison.v1",
        source="director.runtime.repair_kernel.shadow",
        access="read_only",
        matched=True,
        baseline_source_tools=("deterministic_typescript_missing_export_repair",),
        kernel_source_tools=("deterministic_typescript_missing_export_repair",),
        baseline_paths=(path,),
        kernel_paths=(path,),
        comparison_mode="independent_shadow_run",
        independent_shadow_satisfied=True,
        cutover_ready=True,
        cutover_blockers=(),
    )


def test_public_cutover_readiness_requires_repeated_independent_shadow_success() -> None:
    result = evaluate_director_repair_cutover_readiness(
        EvaluateDirectorRepairCutoverReadinessV1(
            comparisons=(_ready_shadow_comparison(), _ready_shadow_comparison()),
            required_successful_runs=2,
        )
    )
    payload = result.to_dict()

    assert payload["schema_version"] == "director.repair_cutover_readiness.v1"
    assert payload["source"] == "director.runtime.repair_kernel.cutover_gate"
    assert payload["access"] == "read_only"
    assert payload["writes_allowed"] is False
    assert payload["cutover_ready"] is True
    assert payload["required_successful_runs"] == 2
    assert payload["successful_comparison_count"] == 2
    assert payload["cutover_blockers"] == []
    assert payload["metadata"]["successful_comparison_indices"] == [0, 1]
    assert payload["metadata"]["multi_run_cutover_gate"] is True


def test_public_cutover_readiness_blocks_self_check_shortfall_and_scope_drift() -> None:
    self_check = DirectorRepairShadowComparisonResultV1(
        schema_version="director.repair_shadow_comparison.v1",
        source="director.runtime.repair_kernel.shadow",
        access="read_only",
        matched=True,
        baseline_source_tools=("deterministic_typescript_missing_export_repair",),
        kernel_source_tools=("deterministic_typescript_missing_export_repair",),
        baseline_paths=("src/app.ts",),
        kernel_paths=("src/app.ts",),
        comparison_mode="receipt_projection_self_check",
        independent_shadow_satisfied=False,
        cutover_ready=False,
        cutover_blockers=("independent_shadow_required",),
    )
    shortfall = evaluate_director_repair_cutover_readiness(
        EvaluateDirectorRepairCutoverReadinessV1(
            comparisons=(_ready_shadow_comparison(), self_check),
            required_successful_runs=2,
        )
    ).to_dict()
    drift = evaluate_director_repair_cutover_readiness(
        EvaluateDirectorRepairCutoverReadinessV1(
            comparisons=(_ready_shadow_comparison("src/app.ts"), _ready_shadow_comparison("src/other.ts")),
            required_successful_runs=2,
        )
    ).to_dict()

    assert shortfall["cutover_ready"] is False
    assert shortfall["cutover_blockers"] == [
        "insufficient_successful_independent_shadow_runs",
        "shadow_comparison_not_cutover_ready",
    ]
    assert shortfall["metadata"]["failed_comparison_indices"] == [1]
    assert drift["cutover_ready"] is False
    assert drift["cutover_blockers"] == ["shadow_comparison_scope_drift"]


def test_public_shadow_comparison_self_check_cannot_cutover_even_when_scope_matches() -> None:
    result = compare_director_repair_shadow_run(
        CompareDirectorRepairShadowRunV1(
            comparison_mode="receipt_projection_self_check",
            baseline_tool_results=(
                {
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/app.ts",
                        "before_hash": "before123",
                        "after_hash": "after456",
                    },
                },
            ),
            kernel_receipts=(
                RepairReceiptV1(
                    receipt_id="receipt_1",
                    plan_id="plan_1",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="applied",
                    authoritative=True,
                    files_changed=("src/app.ts",),
                    before_hashes={"src/app.ts": "before123"},
                    after_hashes={"src/app.ts": "after456"},
                    revalidation_evidence={
                        "command": ["tsc", "--noEmit"],
                        "exit_code": 0,
                        "errors_before": 1,
                        "errors_after": 0,
                    },
                    metadata={"mode": "commit"},
                ),
            ),
        )
    )
    payload = result.to_dict()

    assert payload["matched"] is True
    assert payload["comparison_mode"] == "receipt_projection_self_check"
    assert payload["independent_shadow_required"] is True
    assert payload["independent_shadow_satisfied"] is False
    assert payload["cutover_ready"] is False
    assert payload["cutover_blockers"] == ["independent_shadow_required"]
    readiness = payload["metadata"]["cutover_readiness"]
    assert readiness["comparison_mode"] == "receipt_projection_self_check"
    assert readiness["hashes_matched"] is True
    assert readiness["revalidation_evidence_complete"] is True
    assert readiness["revalidation_evidence_passed"] is True
    assert readiness["authoritative_receipts"] is True
    assert readiness["independent_shadow_satisfied"] is False


def test_public_shadow_comparison_blocks_non_authoritative_shadow_receipts() -> None:
    result = compare_director_repair_shadow_run(
        CompareDirectorRepairShadowRunV1(
            baseline_tool_results=(
                {
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/app.ts",
                        "before_hash": "before123",
                        "after_hash": "after456",
                    },
                },
            ),
            kernel_receipts=(
                RepairReceiptV1(
                    receipt_id="receipt_1",
                    plan_id="plan_1",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="shadow_observed",
                    authoritative=False,
                    files_changed=("src/app.ts",),
                    before_hashes={"src/app.ts": "before123"},
                    after_hashes={"src/app.ts": "after456"},
                    revalidation_evidence={
                        "command": ["tsc", "--noEmit"],
                        "exit_code": 0,
                        "errors_before": 1,
                        "errors_after": 0,
                    },
                    metadata={"mode": "shadow"},
                ),
            ),
        )
    )
    payload = result.to_dict()

    assert payload["matched"] is True
    assert payload["cutover_ready"] is False
    assert payload["cutover_blockers"] == ["non_authoritative_kernel_receipt"]
    assert payload["metadata"]["cutover_readiness"]["hashes_matched"] is True
    assert payload["metadata"]["cutover_readiness"]["revalidation_evidence_complete"] is True
    assert payload["metadata"]["cutover_readiness"]["revalidation_evidence_passed"] is True
    assert payload["metadata"]["cutover_readiness"]["authoritative_receipts"] is False


def test_public_shadow_comparison_blocks_failed_revalidation_evidence_for_cutover() -> None:
    result = compare_director_repair_shadow_run(
        CompareDirectorRepairShadowRunV1(
            baseline_tool_results=(
                {
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/app.ts",
                        "before_hash": "before123",
                        "after_hash": "after456",
                    },
                },
            ),
            kernel_receipts=(
                RepairReceiptV1(
                    receipt_id="receipt_1",
                    plan_id="plan_1",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="applied",
                    authoritative=True,
                    files_changed=("src/app.ts",),
                    before_hashes={"src/app.ts": "before123"},
                    after_hashes={"src/app.ts": "after456"},
                    revalidation_evidence={
                        "command": ["tsc", "--noEmit"],
                        "exit_code": 1,
                        "errors_before": 1,
                        "errors_after": 1,
                    },
                    metadata={"mode": "commit"},
                ),
            ),
        )
    )
    payload = result.to_dict()

    assert payload["matched"] is True
    assert payload["cutover_ready"] is False
    assert payload["cutover_blockers"] == ["failed_revalidation_evidence"]
    readiness = payload["metadata"]["cutover_readiness"]
    assert readiness["hashes_matched"] is True
    assert readiness["revalidation_evidence_complete"] is True
    assert readiness["revalidation_evidence_passed"] is False
    assert readiness["authoritative_receipts"] is True
    assert readiness["revalidation_coverage"]["failed_revalidation_receipt_count"] == 1
    assert readiness["revalidation_coverage"]["failed_revalidation_receipt_ids"] == ["receipt_1"]
    assert readiness["revalidation_coverage"]["failed_revalidation_source_tools"] == [
        "deterministic_typescript_missing_export_repair"
    ]


def test_public_kernel_summary_projection_is_typed_and_read_only() -> None:
    result = project_director_repair_kernel_summary(
        ProjectDirectorRepairKernelSummaryV1(
            stage="materialization_quality_repairs",
            mode="commit",
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/app.ts(1,14): error TS2304: Cannot find name 'Widget'.",
            ),
            tool_results=(
                {
                    "tool": "write_file",
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "ok": True,
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/app.ts",
                        "operation": "modify",
                    },
                },
            ),
        )
    )
    payload = result.to_dict()
    summary = result.summary

    assert isinstance(result, DirectorRepairKernelSummaryProjectionResultV1)
    assert payload["schema_version"] == "director.repair_kernel_summary_projection.v1"
    assert payload["source"] == "director.runtime.repair_kernel.receipt_projection"
    assert payload["access"] == "read_only"
    assert payload["execution_boundary"] == "repair_kernel_summary_projection_no_writes_no_registration"
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["agi_execution_authority"] is False
    assert summary["authoritative"] is False
    assert summary["requires_revalidation"] is True
    assert summary["pending_revalidation_count"] == 1
    assert summary["receipts_with_revalidation"] == 0
    assert summary["revalidation_coverage"]["receipt_count"] == 1
    assert summary["revalidation_coverage"]["receipts_missing_revalidation"] == 1
    assert summary["revalidation_coverage"]["missing_revalidation_source_tools"] == [
        "deterministic_typescript_missing_export_repair"
    ]
    assert summary["revalidation_coverage"]["post_check_evidence_complete"] is False
    assert summary["receipt_count"] == 1
    assert summary["receipts"][0]["source_tool"] == "deterministic_typescript_missing_export_repair"
    assert summary["receipts"][0]["status"] == "pending_revalidation"
    assert summary["receipts"][0]["authoritative"] is False
    assert summary["receipts"][0]["metadata"]["requires_revalidation"] is True
    assert summary["dark_launch_comparison"]["metadata"]["read_only"] is True
    assert summary["dark_launch_comparison"]["metadata"]["writes_performed"] is False
    assert summary["dark_launch_comparison"]["metadata"]["comparison_mode"] == "receipt_projection_self_check"
    assert summary["dark_launch_comparison"]["comparison_mode"] == "receipt_projection_self_check"
    assert summary["dark_launch_comparison"]["cutover_ready"] is False
    assert summary["dark_launch_comparison"]["cutover_blockers"] == ["independent_shadow_required"]
    assert summary["dark_launch_comparison"]["independent_shadow_required"] is True
    assert summary["dark_launch_comparison"]["independent_shadow_satisfied"] is False
    assert summary["coverage_report"]["total_diagnostics"] == 1


def test_receipt_summary_without_receipts_is_not_authoritative() -> None:
    summary = build_director_repair_kernel_summary(stage="quality", tool_results=[], mode="commit")

    assert summary["receipt_count"] == 0
    assert summary["authoritative"] is False
    assert summary["coverage_report"]["total_diagnostics"] == 0
    assert summary["dark_launch_comparison"]["matched"] is True
    assert summary["dark_launch_comparison"]["metadata"]["read_only"] is True
    assert summary["dark_launch_comparison"]["metadata"]["writes_performed"] is False
    assert summary["dark_launch_comparison"]["metadata"]["comparison_mode"] == "receipt_projection_self_check"
    assert summary["dark_launch_comparison"]["cutover_ready"] is False
    assert summary["dark_launch_comparison"]["cutover_blockers"] == ["independent_shadow_required"]


def test_receipt_summary_includes_uncovered_diagnostic_report() -> None:
    summary = build_director_repair_kernel_summary(
        stage="quality",
        tool_results=[],
        mode="shadow",
        artifact_quality_errors=["Mystery compiler failure XYZ999: no deterministic rule covers this yet"],
    )

    coverage_report = summary["coverage_report"]
    assert coverage_report["total_diagnostics"] == 1
    assert coverage_report["uncovered_diagnostic_count"] == 1
    assert coverage_report["coverage_gap_count"] == 1
    assert coverage_report["rule_discovery_required"] is True
    assert coverage_report["items"][0]["known_rule_matched"] is False
    assert coverage_report["uncovered_diagnostics"][0]["message"]
    assert coverage_report["coverage_gaps"][0]["audit_reason"] == "known_rule_matched=false"


def test_receipt_summary_preserves_embedded_runtime_kernel_receipt_identity() -> None:
    summary = build_director_repair_kernel_summary(
        stage="quality",
        mode="commit",
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_go_bare_import_string_repair",
                    "file": "cmd/app/main.go",
                    "bytes_written": 42,
                    "before_hash": "before",
                    "after_hash": "after",
                    "repair_kernel": {
                        "owner_cell": "director.runtime",
                        "receipt_id": "repair_receipt_kernel_go",
                        "plan_id": "plan_kernel_go",
                        "status": "applied",
                        "authoritative": True,
                        "before_hashes": {"cmd/app/main.go": "before"},
                        "after_hashes": {"cmd/app/main.go": "after"},
                        "metadata": {"rule_runtime": "go"},
                    },
                },
            }
        ],
    )

    assert summary["authoritative"] is False
    assert summary["requires_revalidation"] is True
    assert summary["pending_revalidation_count"] == 1
    assert summary["revalidation_coverage"]["requires_revalidation"] is True
    assert summary["revalidation_coverage"]["receipts_missing_revalidation"] == 1
    assert summary["revalidation_coverage"]["source_tools_missing_revalidation"] == [
        "deterministic_go_bare_import_string_repair"
    ]
    receipt = summary["receipts"][0]
    assert receipt["receipt_id"] == "repair_receipt_kernel_go"
    assert receipt["plan_id"] == "plan_kernel_go"
    assert receipt["status"] == "pending_revalidation"
    assert receipt["metadata"]["execution_status"] == "applied"
    assert receipt["metadata"]["projection_source"] == "embedded_repair_kernel"
    assert receipt["metadata"]["requires_revalidation"] is True
    assert receipt["before_hashes"] == {"cmd/app/main.go": "before"}
    assert receipt["after_hashes"] == {"cmd/app/main.go": "after"}
    assert summary["dark_launch_comparison"]["kernel_paths"] == ["cmd/app/main.go"]
    assert summary["dark_launch_comparison"]["kernel_source_tools"] == ["deterministic_go_bare_import_string_repair"]


def test_receipt_summary_preserves_revalidation_evidence_counts() -> None:
    summary = build_director_repair_kernel_summary(
        stage="post_execution_language_repairs",
        mode="commit",
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_rust_dependency_repair",
                    "file": "Cargo.toml",
                    "action": "add_dependency",
                    "round_number": 2,
                    "revalidation": {
                        "command": ["cargo", "check", "--quiet"],
                        "exit_code": 0,
                        "round_number": 2,
                        "errors_before": 3,
                        "errors_after": 1,
                        "net_error_reduction": 2,
                        "max_rounds": 3,
                    },
                },
            }
        ],
    )

    assert summary["authoritative"] is False
    assert summary["requires_revalidation"] is False
    assert summary["pending_revalidation_count"] == 0
    assert summary["receipts_with_revalidation"] == 1
    assert summary["revalidation_coverage"]["receipt_count"] == 1
    assert summary["revalidation_coverage"]["receipts_missing_revalidation"] == 0
    assert summary["revalidation_coverage"]["failed_revalidation_receipt_count"] == 1
    assert summary["revalidation_coverage"]["post_check_evidence_available"] is True
    assert summary["revalidation_coverage"]["post_check_evidence_complete"] is True
    assert summary["revalidation_coverage"]["source_tools_with_revalidation"] == [
        "deterministic_rust_dependency_repair"
    ]
    assert summary["receipt_count"] == 1
    receipt = summary["receipts"][0]
    assert receipt["status"] == "failed_revalidation"
    assert receipt["authoritative"] is False
    assert receipt["round_number"] == 2
    assert receipt["errors_before"] == 3
    assert receipt["errors_after"] == 1
    assert receipt["net_error_reduction"] == 2
    assert receipt["revalidation_evidence"]["command"] == ["cargo", "check", "--quiet"]
    assert receipt["revalidation_evidence"]["metadata"]["max_rounds"] == 3
    shadow = summary["dark_launch_comparison"]
    assert shadow["matched"] is True
    assert shadow["baseline_source_tools"] == ["deterministic_rust_dependency_repair"]
    assert shadow["kernel_source_tools"] == ["deterministic_rust_dependency_repair"]
    assert shadow["baseline_paths"] == ["Cargo.toml"]
    assert shadow["kernel_paths"] == ["Cargo.toml"]
    assert shadow["metadata"]["read_only"] is True
    assert shadow["metadata"]["writes_performed"] is False


def test_receipt_summary_with_failed_revalidation_is_not_authoritative() -> None:
    summary = build_director_repair_kernel_summary(
        stage="post_execution_language_repairs",
        mode="commit",
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_rust_dependency_repair",
                    "file": "Cargo.toml",
                    "action": "add_dependency",
                    "revalidation": {
                        "command": ["cargo", "check", "--quiet"],
                        "exit_code": 1,
                        "errors_before": 3,
                        "errors_after": 2,
                    },
                },
            }
        ],
    )

    assert summary["authoritative"] is False
    assert summary["requires_revalidation"] is False
    assert summary["receipts_with_revalidation"] == 1
    assert summary["revalidation_coverage"]["failed_revalidation_receipt_count"] == 1
    assert summary["revalidation_coverage"]["failed_revalidation_receipt_ids"] == [summary["receipts"][0]["receipt_id"]]
    assert summary["revalidation_coverage"]["failed_revalidation_source_tools"] == [
        "deterministic_rust_dependency_repair"
    ]
    assert summary["revalidation_coverage"]["post_check_evidence_complete"] is True
    assert summary["receipts"][0]["status"] == "failed_revalidation"
    assert summary["receipts"][0]["authoritative"] is False


def test_public_revalidation_projection_updates_receipts_and_context() -> None:
    summary = build_director_repair_kernel_summary(
        stage="materialization_quality_repairs",
        mode="commit",
        artifact_quality_errors=[
            "TypeScript syntax check failed: src/app.ts(1,14): error TS2304: Cannot find name 'Widget'."
        ],
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_missing_export_repair",
                    "file": "src/app.ts",
                    "operation": "modify",
                },
            }
        ],
    )
    before_authority_hash = summary["receipts"][0]["authority_hash"]
    assert summary["authoritative"] is False
    assert summary["pending_revalidation_count"] == 1
    assert summary["revalidation_coverage"]["receipts_missing_revalidation"] == 1
    assert summary["receipts"][0]["status"] == "pending_revalidation"

    result = project_director_repair_revalidation_evidence(
        AttachDirectorRepairRevalidationEvidenceV1(
            summary={"repair_kernel": summary, "stage": "deterministic_quality_repair"},
            residual_artifact_quality_errors=(),
            command=("tsc", "--noEmit"),
            metadata={"stage": "director_materialization_quality"},
        )
    )
    assert isinstance(result, DirectorRepairRevalidationProjectionResultV1)
    assert result.access == "read_only"
    assert result.writes_allowed is False
    assert result.registration_allowed is False
    assert result.agi_execution_authority is False
    updated = result.summary
    repair_kernel = updated["repair_kernel"]
    receipt = repair_kernel["receipts"][0]

    assert repair_kernel["authoritative"] is True
    assert repair_kernel["requires_revalidation"] is False
    assert repair_kernel["pending_revalidation_count"] == 0
    assert repair_kernel["receipts_with_revalidation"] == 1
    assert repair_kernel["revalidation_coverage"]["receipts_missing_revalidation"] == 0
    assert repair_kernel["revalidation_coverage"]["post_check_evidence_available"] is True
    assert repair_kernel["revalidation_coverage"]["post_check_evidence_complete"] is True
    assert repair_kernel["revalidation_coverage"]["status_counts"] == {"applied": 1}
    assert receipt["status"] == "applied"
    assert receipt["authoritative"] is True
    assert receipt["revalidation_evidence"]["command"] == ["tsc", "--noEmit"]
    assert receipt["revalidation_evidence"]["exit_code"] == 0
    assert receipt["errors_before"] == 1
    assert receipt["errors_after"] == 0
    assert receipt["net_error_reduction"] == 1
    assert receipt["revalidation_evidence"]["metadata"]["resolved_diagnostic_signatures"]
    assert receipt["revalidation_evidence"]["metadata"]["residual_diagnostic_signatures"] == []
    assert receipt["authority_hash"] != before_authority_hash
    assert repair_kernel["revalidation"]["post_check_evidence_attached"] is True
    assert repair_kernel["revalidation"]["coverage"]["receipts_with_revalidation"] == 1
    context_receipt = repair_kernel["receipt_context"]["receipts"][0]
    assert context_receipt["post_check_evidence"]["available"] is True
    assert context_receipt["errors_after"] == 0

    compatibility_summary = attach_director_repair_revalidation_evidence(
        {"repair_kernel": summary, "stage": "deterministic_quality_repair"},
        residual_artifact_quality_errors=[],
        command=("tsc", "--noEmit"),
        metadata={"stage": "director_materialization_quality"},
    )
    assert compatibility_summary["repair_kernel"]["receipts"][0]["errors_after"] == 0


def test_public_revalidation_projection_marks_failed_post_check_as_non_authoritative() -> None:
    diagnostic = "TypeScript syntax check failed: src/app.ts(1,14): error TS2304: Cannot find name 'Widget'."
    summary = build_director_repair_kernel_summary(
        stage="materialization_quality_repairs",
        mode="commit",
        artifact_quality_errors=[diagnostic],
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_missing_export_repair",
                    "file": "src/app.ts",
                    "operation": "modify",
                },
            }
        ],
    )

    result = project_director_repair_revalidation_evidence(
        AttachDirectorRepairRevalidationEvidenceV1(
            summary={"repair_kernel": summary, "stage": "deterministic_quality_repair"},
            residual_artifact_quality_errors=(diagnostic,),
            command=("tsc", "--noEmit"),
        )
    )
    repair_kernel = result.summary["repair_kernel"]
    receipt = repair_kernel["receipts"][0]

    assert repair_kernel["authoritative"] is False
    assert repair_kernel["requires_revalidation"] is False
    assert repair_kernel["pending_revalidation_count"] == 0
    assert repair_kernel["revalidation"]["exit_code"] == 1
    assert repair_kernel["revalidation_coverage"]["receipts_missing_revalidation"] == 0
    assert repair_kernel["revalidation_coverage"]["failed_revalidation_receipt_count"] == 1
    assert repair_kernel["revalidation_coverage"]["post_check_evidence_complete"] is True
    assert receipt["status"] == "failed_revalidation"
    assert receipt["authoritative"] is False
    assert receipt["revalidation_evidence"]["exit_code"] == 1
    assert receipt["errors_after"] == 1
    assert receipt["net_error_reduction"] == 0


def test_public_revalidation_projection_uses_diagnostic_signatures_when_ids_are_missing() -> None:
    diagnostic = "TypeScript syntax check failed: src/app.ts(1,14): error TS2304: Cannot find name 'Widget'."
    summary = build_director_repair_kernel_summary(
        stage="materialization_quality_repairs",
        mode="commit",
        artifact_quality_errors=[diagnostic],
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_missing_export_repair",
                    "file": "src/app.ts",
                    "operation": "modify",
                },
            }
        ],
    )
    summary["receipts"][0]["diagnostics"][0]["diagnostic_id"] = ""

    result = project_director_repair_revalidation_evidence(
        AttachDirectorRepairRevalidationEvidenceV1(
            summary={"repair_kernel": summary, "stage": "deterministic_quality_repair"},
            residual_artifact_quality_errors=(diagnostic,),
            command=("tsc", "--noEmit"),
        )
    )
    repair_kernel = result.summary["repair_kernel"]
    receipt = repair_kernel["receipts"][0]
    evidence = receipt["revalidation_evidence"]

    assert evidence["residual_diagnostic_ids"] == []
    assert evidence["resolved_diagnostic_ids"] == []
    assert evidence["metadata"]["diagnostic_match_strategy"] == "stable_signature"
    assert evidence["metadata"]["residual_diagnostic_signatures"]
    assert evidence["metadata"]["resolved_diagnostic_signatures"] == []
    assert repair_kernel["revalidation_coverage"]["residual_diagnostic_receipt_count"] == 1
    assert receipt["status"] == "failed_revalidation"


def test_public_typescript_comma_planner_returns_composed_patch_projection() -> None:
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_return_object_semicolon_repair",
            base_files={
                "src/models/Flight.ts": (
                    "export function summarizeFlight() {\n"
                    "  const range = 10;\n"
                    "  const maxAltitude = 2;\n"
                    "  return {\n"
                    "    range\n"
                    "    maxAltitude: maxAltitude,\n"
                    "  };\n"
                    "}\n"
                )
            },
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/models/Flight.ts(6,5): error TS1005: ',' expected.",
            ),
        )
    )

    payload = result.to_dict()

    assert result.ok is True
    assert result.planned is True
    assert result.source_tool == "deterministic_typescript_return_object_semicolon_repair"
    assert result.agi_execution_authority is False
    assert result.advisory_authoritative is False
    assert payload["composition_summary"]["patches"][0]["path"] == "src/models/Flight.ts"
    assert "    range,\n" in payload["composition_summary"]["patches"][0]["content_after"]
    assert payload["composition_summary"]["patches"][0]["before_hash"]
    assert payload["composition_summary"]["patches"][0]["after_hash"]


def test_public_repair_coverage_report_exposes_uncovered_diagnostics() -> None:
    result = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/models/Flight.ts(6,5): error TS1005: ',' expected.",
                "src/app.ts(3,14): error TS9999: Unknown future compiler error.",
            )
        )
    )
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairCoverageReportV1)
    assert payload["schema_version"] == "director.repair_coverage_report.v1"
    assert payload["source"] == "director.runtime.repair_kernel.registry"
    assert payload["access"] == "read_only"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["execution_boundary"] == "read_only_coverage_no_writes"
    assert payload["agi_execution_authority"] is False
    assert payload["director_tool_execution_required"] is False
    assert payload["total_diagnostics"] == 2
    assert payload["covered_diagnostic_count"] == 1
    assert payload["uncovered_diagnostic_count"] == 1
    assert payload["coverage_gap_count"] == 1
    assert payload["rule_discovery_required"] is True
    assert payload["coverage_gap_languages"] == ["typescript"]
    assert payload["coverage_gap_recommended_routes"] == ["runtime_rule"]
    assert payload["coverage_gap_slot_statuses"] == ["reserved_slot_available"]
    assert payload["executable_runtime_plan_diagnostic_count"] == 1
    assert payload["metadata_only_diagnostic_count"] == 0
    assert payload["items"][0]["known_rule_matched"] is True
    assert payload["items"][0]["executable_runtime_plan_matched"] is True
    assert payload["items"][0]["metadata_only_match"] is False
    assert payload["items"][0]["recommended_route"] == "runtime_rule"
    assert payload["items"][0]["coverage_status"] == "executable_runtime"
    assert payload["items"][0]["matched_source_tools"] == [
        "deterministic_typescript_hyphenated_identifier_repair",
        "deterministic_typescript_return_object_semicolon_repair",
    ]
    assert payload["items"][1]["known_rule_matched"] is False
    assert payload["items"][1]["metadata_only_match"] is False
    assert payload["items"][1]["executable_runtime_plan_matched"] is False
    assert payload["items"][1]["language"] == "typescript"
    assert payload["items"][1]["diagnostic_language"] == "typescript"
    assert payload["items"][1]["diagnostic_code"] == "typescript_ts9999"
    assert payload["items"][1]["diagnostic_phase"] == "quality_repair"
    assert payload["items"][1]["phase_suggestion"] == "quality_repair"
    assert payload["items"][1]["diagnostic_archetype"] == "object_literal_syntax"
    assert payload["items"][1]["archetype_suggestion"] == "object_literal_syntax"
    assert payload["items"][1]["reserved_slot_available"] is True
    assert payload["items"][1]["slot_status"] == "reserved_slot_available"
    assert payload["items"][1]["recommended_route"] == "runtime_rule"
    assert payload["items"][1]["coverage_status"] == "coverage_gap"
    assert payload["uncovered_diagnostics"][0]["code"] == "typescript_ts9999"
    assert payload["coverage_gaps"][0]["diagnostic"]["code"] == "typescript_ts9999"
    assert payload["coverage_gaps"][0]["language"] == "typescript"
    assert payload["coverage_gaps"][0]["diagnostic_code"] == "typescript_ts9999"
    assert payload["coverage_gaps"][0]["phase_suggestion"] == "quality_repair"
    assert payload["coverage_gaps"][0]["archetype_suggestion"] == "object_literal_syntax"
    assert payload["coverage_gaps"][0]["reserved_slot_available"] is True
    assert payload["coverage_gaps"][0]["slot_status"] == "reserved_slot_available"
    assert payload["coverage_gaps"][0]["recommended_route"] == "runtime_rule"
    assert payload["coverage_gaps"][0]["coverage_status"] == "coverage_gap"
    assert payload["coverage_gaps"][0]["audit_reason"] == "known_rule_matched=false"
    assert payload["coverage_gaps"][0]["missing_capability"] == "deterministic_repair_rule"


def test_public_repair_coverage_suggests_rust_missing_method_self_family() -> None:
    result = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(
                "Rust syntax check failed: src/lib.rs:12: expected one of `self`, `&self`, or `&mut self`; found `&)`.",
            )
        )
    )
    payload = result.to_dict()
    item = payload["items"][0]

    assert payload["covered_diagnostic_count"] == 0
    assert payload["uncovered_diagnostic_count"] == 1
    assert payload["executable_runtime_plan_diagnostic_count"] == 0
    assert payload["metadata_only_diagnostic_count"] == 0
    assert item["known_rule_matched"] is False
    assert item["diagnostic_language"] == "rust"
    assert item["diagnostic_archetype"] == "missing_method_self"
    assert item["diagnostic_phase"] == "quality_repair"
    assert item["suggested_rule_family"] == "missing_method_self"


def test_public_repair_language_slots_reserve_future_languages_without_registering_rules() -> None:
    result = query_director_repair_language_slots(QueryDirectorRepairLanguageSlotsV1())
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairLanguageSlotsResultV1)
    assert payload["schema_version"] == "director.repair_language_slots.v1"
    assert payload["source"] == "director.runtime.repair_kernel.registry"
    assert payload["access"] == "read_only"
    assert payload["authoritative_rule_registration"] is False
    assert payload["agi_execution_authority"] is False
    assert payload["writes_allowed"] is False
    direct_slot_payload = DirectorRepairLanguageSlotV1(language="new-lang").to_dict()
    assert direct_slot_payload["repairer_module"].endswith(".new_lang_runtime")
    assert direct_slot_payload["implementation_status"] == "reserved_only"
    assert direct_slot_payload["slot_owner_cell"] == "director.runtime"
    assert direct_slot_payload["bench_evidence_required"] is True
    assert direct_slot_payload["rule_authoring_status"] == "reserved_only"
    assert direct_slot_payload["next_action"] == "add_bench_verified_rule_metadata_then_runtime_binding"
    languages = {item["language"] for item in payload["items"]}
    assert {
        "typescript",
        "go",
        "rust",
        "cpp",
        "java",
        "python",
        "shell",
        "sql",
        "csharp",
        "php",
        "ruby",
        "swift",
        "kotlin",
        "dart",
        "lua",
        "r",
        "vue",
        "svelte",
        "scala",
        "groovy",
        "elixir",
        "erlang",
        "haskell",
        "ocaml",
        "fsharp",
        "zig",
        "nim",
        "crystal",
        "perl",
        "powershell",
        "julia",
        "objective_c",
        "fortran",
        "matlab",
        "terraform",
        "dockerfile",
        "make",
        "yaml",
        "json",
        "toml",
        "nix",
        "starlark",
        "clojure",
        "elm",
        "rescript",
        "gleam",
        "solidity",
        "vyper",
        "qml",
        "proto",
        "graphql",
    }.issubset(languages)
    slots_by_language = {item["language"]: item for item in payload["items"]}
    assert slots_by_language["dockerfile"]["file_names"] == ["dockerfile", "containerfile"]
    assert slots_by_language["dockerfile"]["repairer_module"].endswith(".dockerfile_runtime")
    assert slots_by_language["dockerfile"]["implementation_status"] == "reserved_only"
    assert slots_by_language["dockerfile"]["registration_policy"] == "bench_verified_rule_required"
    assert slots_by_language["dockerfile"]["bench_evidence_required"] is True
    assert slots_by_language["dockerfile"]["rule_authoring_status"] == "reserved_only"
    assert slots_by_language["dockerfile"]["next_action"] == "add_bench_verified_rule_metadata_then_runtime_binding"
    assert slots_by_language["dockerfile"]["authoritative_source_tools"] == []
    assert slots_by_language["dockerfile"]["executable_runtime_source_tools"] == []
    assert slots_by_language["cpp"]["implementation_status"] == "executable_runtime"
    assert slots_by_language["cpp"]["next_action"] == "extend_existing_runtime_rule_with_bench_evidence"
    assert slots_by_language["rust"]["implementation_status"] == "executable_runtime"
    assert slots_by_language["rust"]["rule_authoring_status"] == "executable_runtime"
    assert slots_by_language["rust"]["executable_runtime_source_tools"] == [
        "deterministic_rust_crate_import_repair",
        "deterministic_rust_crate_import_rewrite_repair",
        "deterministic_rust_dependency_repair",
        "deterministic_rust_derive_repair",
        "deterministic_rust_duplicate_module_file_repair",
        "deterministic_rust_field_rename_suggestion_repair",
        "deterministic_rust_incompatible_copy_derive_repair",
        "deterministic_rust_lib_root_facade_repair",
        "deterministic_rust_line_suggestion_repair",
        "deterministic_rust_method_self_signature_repair",
        "deterministic_rust_missing_binary_entrypoint_repair",
        "deterministic_rust_missing_fields_repair",
        "deterministic_rust_missing_lib_target_repair",
        "deterministic_rust_missing_module_file_repair",
        "deterministic_rust_serde_derive_repair",
        "deterministic_rust_struct_literal_missing_field_repair",
        "deterministic_rust_trait_import_repair",
        "deterministic_rust_unresolved_pub_use_repair",
        "deterministic_rust_unused_import_repair",
        "deterministic_rust_wrong_crate_path_repair",
    ]
    assert "deterministic_rust_derive_repair" in slots_by_language["rust"]["executable_runtime_source_tools"]
    assert set(payload["summary"]["authoritative_rule_languages"]).issubset(languages)
    assert payload["summary"]["language_count"] >= 45
    assert payload["summary"]["next_actions_by_language"]["dockerfile"] == (
        "add_bench_verified_rule_metadata_then_runtime_binding"
    )
    assert payload["summary"]["next_actions_by_language"]["rust"] == "extend_existing_runtime_rule_with_bench_evidence"
    assert payload["summary"]["authoritative_rule_languages"] == [
        "cpp",
        "go",
        "html",
        "java",
        "javascript",
        "python",
        "rust",
        "typescript",
    ]
    assert payload["summary"]["executable_runtime_languages"] == [
        "cpp",
        "go",
        "html",
        "java",
        "javascript",
        "python",
        "rust",
        "typescript",
    ]
    assert "scala" in payload["summary"]["reserved_only_languages"]
    assert "php" in payload["summary"]["reserved_only_languages"]
    assert "kotlin" in payload["summary"]["reserved_only_languages"]
    assert "fortran" in payload["summary"]["reserved_only_languages"]
    assert "terraform" in payload["summary"]["reserved_only_languages"]
    assert "dockerfile" in payload["summary"]["reserved_only_languages"]
    assert "graphql" in payload["summary"]["reserved_only_languages"]
    assert payload["summary"]["reserved_only_language_count"] >= 40
    assert payload["summary"]["implementation_status_counts"]["executable_runtime"] == 8
    assert payload["summary"]["implementation_status_counts"].get("metadata_rule_registered", 0) == 0
    assert payload["summary"]["implementation_status_counts"]["reserved_only"] >= 40
    assert payload["summary"]["implementation_status_by_language"]["dockerfile"] == "reserved_only"
    assert payload["summary"]["implementation_status_by_language"]["cpp"] == "executable_runtime"
    assert payload["summary"]["implementation_status_by_language"]["rust"] == "executable_runtime"
    assert payload["summary"]["repairer_modules"]["starlark"].endswith(".starlark_runtime")
    assert payload["summary"]["reserved_only_repairer_modules"]["graphql"].endswith(".graphql_runtime")
    assert payload["summary"]["bench_driven_rule_addition_required"] is True

    diagnostic = RepairDiagnostic(
        source="shellcheck",
        code="shell_sc1009",
        message="The mentioned syntax error was in this if expression.",
        path="scripts/deploy.sh",
    )
    coverage_item = default_repair_rule_registry().coverage([diagnostic]).to_dict()["items"][0]
    assert coverage_item["diagnostic_language"] == "shell"
    assert coverage_item["known_rule_matched"] is False
    assert coverage_item["matched_source_tools"] == []

    dockerfile_diagnostic = RepairDiagnostic(
        source="hadolint",
        code="hadolint_dl3008",
        message="Pin versions in apt get install.",
        path="Dockerfile",
    )
    dockerfile_coverage = default_repair_rule_registry().coverage([dockerfile_diagnostic]).to_dict()["items"][0]
    assert dockerfile_coverage["diagnostic_language"] == "dockerfile"
    assert dockerfile_coverage["known_rule_matched"] is False

    starlark_diagnostic = RepairDiagnostic(
        source="bazel",
        code="bazel_missing_load",
        message="name 'py_library' is not defined",
        path="services/api/BUILD.bazel",
    )
    starlark_coverage = default_repair_rule_registry().coverage([starlark_diagnostic]).to_dict()["items"][0]
    assert starlark_coverage["diagnostic_language"] == "starlark"
    assert starlark_coverage["known_rule_matched"] is False


def test_public_post_execution_schedule_is_runtime_owned_and_read_only() -> None:
    result = query_director_repair_post_execution_schedule(QueryDirectorRepairPostExecutionScheduleV1())
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairPostExecutionScheduleResultV1)
    assert payload["schema_version"] == "director.repair_post_execution_schedule.v1"
    assert payload["source"] == "director.runtime.repair_kernel.scheduler"
    assert payload["access"] == "read_only"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["execution_boundary"] == "read_only_post_execution_schedule_no_runner_binding"
    assert payload["runner_binding_owner"] == "roles.adapters"
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["agi_execution_authority"] is False
    assert [item["step_id"] for item in payload["items"]] == [
        "go.module_import",
        "rust.dependency_resolution",
        "rust.post_execution_convergence",
        "cpp.post_execution",
        "java.post_execution",
    ]
    assert payload["items"][0]["phase"] == "dependency_resolution"
    assert payload["items"][1]["phase"] == "dependency_resolution"
    assert payload["items"][2]["phase"] == "multi_phase_convergence"
    assert payload["items"][1]["source_tool_kind"] == "executable_runtime"
    assert payload["items"][1]["executable_runtime_source_tool"] is True
    assert payload["items"][2]["source_tool"] == "deterministic_rust_post_repair"
    assert payload["items"][2]["source_tool_kind"] == "callback_schedule_label"
    assert payload["items"][2]["executable_runtime_source_tool"] is False
    assert payload["items"][3]["priority"] == 1
    assert payload["summary"]["source_tool_kind_counts"] == {
        "callback_schedule_label": 1,
        "executable_runtime": 4,
    }
    assert payload["summary"]["callback_schedule_label_source_tools"] == ["deterministic_rust_post_repair"]
    assert "deterministic_rust_post_repair" not in payload["summary"]["executable_runtime_source_tools"]
    assert payload["summary"]["runtime_schedule_authoritative"] is True
    assert payload["summary"]["runner_binding_owner"] == "roles.adapters"
    assert payload["summary"]["target_scheduler"] == "director.runtime.repair_kernel.scheduler"
    assert payload["summary"]["default_max_rounds"] == 1
    assert payload["summary"]["convergence_loop_owned_by"] == "director.runtime.repair_kernel.scheduler"
    assert payload["summary"]["cycle_breaker"] == "repeated_round_fingerprint"


def test_runtime_post_execution_schedule_runs_callbacks_and_injects_step_metadata() -> None:
    observed_step_ids: list[str] = []

    def runner(step) -> list[dict[str, object]]:
        observed_step_ids.append(step.step_id)
        if step.step_id != "rust.dependency_resolution":
            return []
        return [
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "source_tool": "deterministic_rust_dependency_repair",
                    "file": "Cargo.toml",
                },
            }
        ]

    run = run_post_execution_repair_schedule_callbacks(
        runner_step_ids=(
            "go.module_import",
            "rust.dependency_resolution",
            "rust.post_execution_convergence",
            "cpp.post_execution",
            "java.post_execution",
        ),
        runner=runner,
    )

    assert observed_step_ids == [
        "go.module_import",
        "rust.dependency_resolution",
        "rust.post_execution_convergence",
        "cpp.post_execution",
        "java.post_execution",
    ]
    assert [step.step_id for step in run.ordered_steps] == observed_step_ids
    assert len(run.tool_results) == 1
    payload = run.tool_results[0]["result"]
    assert payload["bridge_step_id"] == "rust.dependency_resolution"
    assert payload["language"] == "rust"
    assert payload["phase"] == "dependency_resolution"
    assert payload["priority"] == 0
    assert payload["scheduled_source_tool"] == "deterministic_rust_dependency_repair"
    assert payload["scheduled_source_tool_kind"] == "executable_runtime"
    assert payload["scheduled_source_tool_executable_runtime"] is True
    assert payload["schedule_source_tool_kind"] == "executable_runtime"
    assert payload["schedule_source_tool_is_runtime_executable"] is True
    assert payload["round_number"] == 1
    assert payload["max_rounds"] == 1
    assert payload["scheduler_round_number"] == 1
    assert payload["scheduler_max_rounds"] == 1
    assert payload["scheduler_rounds_run"] == 1
    assert payload["convergence_status"] == "max_rounds_reached"
    assert payload["convergence_stopped_reason"] == "max_rounds_reached"
    assert run.max_rounds == 1
    assert run.rounds_run == 1
    assert run.convergence_status == "max_rounds_reached"

    with pytest.raises(RuntimeError, match="runner is not declared"):
        run_post_execution_repair_schedule_callbacks(
            runner_step_ids=(
                "go.module_import",
                "rust.dependency_resolution",
                "rust.post_execution_convergence",
                "cpp.post_execution",
                "java.post_execution",
                "python.unregistered",
            ),
            runner=runner,
        )


def test_runtime_post_execution_schedule_runs_bounded_convergence_rounds() -> None:
    rust_rounds = 0

    def runner(step) -> list[dict[str, object]]:
        nonlocal rust_rounds
        if step.step_id != "rust.post_execution_convergence":
            return []
        rust_rounds += 1
        if rust_rounds >= 3:
            return []
        return [
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "source_tool": "deterministic_rust_post_repair",
                    "file": "Cargo.toml",
                    "after_hash": f"hash-{rust_rounds}",
                },
            }
        ]

    run = run_post_execution_repair_schedule_callbacks(
        runner_step_ids=(
            "go.module_import",
            "rust.dependency_resolution",
            "rust.post_execution_convergence",
            "cpp.post_execution",
            "java.post_execution",
        ),
        runner=runner,
        max_rounds=3,
    )

    assert len(run.tool_results) == 2
    assert run.max_rounds == 3
    assert run.rounds_run == 3
    assert run.convergence_status == "converged"
    assert run.stopped_reason == "converged_no_repairs_applied"
    assert [item["result"]["scheduler_round_number"] for item in run.tool_results] == [1, 2]
    assert all(item["result"]["scheduler_rounds_run"] == 3 for item in run.tool_results)
    assert all(item["result"]["convergence_status"] == "converged" for item in run.tool_results)


def test_runtime_post_execution_schedule_breaks_repeated_round_cycles() -> None:
    def runner(step) -> list[dict[str, object]]:
        if step.step_id != "rust.post_execution_convergence":
            return []
        return [
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "source_tool": "deterministic_rust_post_repair",
                    "file": "Cargo.toml",
                    "after_hash": "same-hash",
                },
            }
        ]

    run = run_post_execution_repair_schedule_callbacks(
        runner_step_ids=(
            "go.module_import",
            "rust.dependency_resolution",
            "rust.post_execution_convergence",
            "cpp.post_execution",
            "java.post_execution",
        ),
        runner=runner,
        max_rounds=3,
    )

    assert len(run.tool_results) == 1
    assert run.max_rounds == 3
    assert run.rounds_run == 2
    assert run.convergence_status == "cycle_broken"
    assert run.stopped_reason == "repeated_round_fingerprint"
    payload = run.tool_results[0]["result"]
    assert payload["scheduler_round_number"] == 1
    assert payload["scheduler_rounds_run"] == 2
    assert payload["convergence_status"] == "cycle_broken"
    assert payload["convergence_stopped_reason"] == "repeated_round_fingerprint"


def test_public_materialization_quality_schedule_is_runtime_owned_and_read_only() -> None:
    result = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1()
    )
    payload = result.to_dict()
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

    assert isinstance(result, DirectorRepairMaterializationQualityScheduleResultV1)
    assert payload["schema_version"] == "director.repair_materialization_quality_schedule.v1"
    assert payload["source"] == "director.runtime.repair_kernel.scheduler"
    assert payload["access"] == "read_only"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["execution_boundary"] == "read_only_materialization_quality_schedule_no_runner_binding"
    assert payload["runner_binding_owner"] == "roles.adapters"
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["agi_execution_authority"] is False
    assert [item["step_id"] for item in payload["items"]] == expected_step_ids
    assert payload["items"][0]["phase"] == "hygiene"
    assert payload["items"][0]["source_tool_kind"] == "callback_schedule_label"
    assert payload["items"][0]["executable_runtime_source_tool"] is False
    assert payload["items"][0]["runtime_source_tools"] == [
        "deterministic_scaffold_marker_cleanup",
        "deterministic_scaffold_marker_quality_cleanup",
    ]
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in payload["items"][2]["runtime_source_tools"]
    assert payload["items"][3]["source_tool"] == "deterministic_html_typescript_module_script_repair"
    assert payload["items"][3]["source_tool_kind"] == "callback_schedule_label"
    assert payload["items"][3]["runtime_source_tools"] == ["deterministic_html_typescript_module_script_repair"]
    assert "deterministic_python_package_shadow_bridge_repair" in payload["items"][7]["runtime_source_tools"]
    assert "deterministic_go_module_import_repair" in payload["items"][8]["runtime_source_tools"]
    assert payload["items"][3]["executable_runtime_source_tool"] is False
    assert payload["items"][4]["depends_on"] == ["materialization.html_entrypoint"]
    assert payload["items"][-1]["depends_on"] == ["materialization.python_import"]
    assert payload["summary"]["step_count"] == len(expected_step_ids)
    assert payload["summary"]["ordered_step_ids"] == expected_step_ids
    assert payload["summary"]["languages"] == ["go", "html", "javascript", "multi", "python", "rust", "typescript"]
    assert payload["summary"]["source_tool_kind_counts"] == {
        "callback_schedule_label": len(expected_step_ids),
        "executable_runtime": 0,
    }
    assert payload["summary"]["executable_runtime_source_tools"] == []
    assert payload["summary"]["callback_schedule_label_source_tools"] == payload["summary"]["source_tools"]
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in payload["summary"]["runtime_source_tools"]
    assert payload["summary"]["runtime_source_tool_count"] == len(payload["summary"]["runtime_source_tools"])
    assert payload["summary"]["runtime_schedule_authoritative"] is True
    assert payload["summary"]["runner_binding_owner"] == "roles.adapters"
    assert payload["summary"]["target_scheduler"] == "director.runtime.repair_kernel.scheduler"
    assert payload["summary"]["default_max_rounds"] == 1
    assert payload["summary"]["convergence_loop_owned_by"] == "director.runtime.repair_kernel.scheduler"
    assert payload["summary"]["cycle_breaker"] == "repeated_round_fingerprint"


def test_public_materialization_bridge_metadata_projection_is_runtime_owned() -> None:
    schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1()
    )

    result = project_director_repair_materialization_bridge_metadata(
        ProjectDirectorRepairMaterializationBridgeMetadataV1(
            ordered_steps=schedule.items,
            repair_kernel={
                "receipt_count": 2,
                "coverage_report": {"uncovered_diagnostic_count": 1},
            },
            schedule_reconciliation={"runner_step_ids": ["materialization.hygiene_scaffold"]},
            scheduler_bridge_evidence={
                "schema_version": "director.materialization_quality_scheduler_bridge.v1",
                "adapter_projection_bridge": True,
                "receipt_lifecycle_status_counts": {"missing_evidence": 1},
            },
            coverage_preaudit={"uncovered_diagnostic_count": 1, "rule_discovery_required": True},
            plan_probe_preaudit={
                "status": "covered_plannable",
                "covered_unplannable_diagnostic_count": 0,
                "plannable_source_tools": ["deterministic_scaffold_marker_cleanup"],
            },
            repair_kernel_migration_debt={
                "schema_version": "director.materialization_quality_repair_migration_debt.v1",
                "adapter_projection_debt": [{"step_id": "materialization.hygiene_scaffold"}],
            },
            receipt_lifecycle_by_step={
                "materialization.hygiene_scaffold": {"receipt_lifecycle_evidence_status": "missing_evidence"},
            },
            dark_launch_comparison={
                "cutover_ready": False,
                "cutover_blockers": ["adapter_projection_bridge"],
            },
            convergence_verifier_present=True,
        )
    )
    payload = result.to_dict()
    summary = payload["summary"]

    assert payload["schema_version"] == "director.materialization_quality_runtime_ports_metadata_projection.v1"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["execution_boundary"] == "read_only_materialization_runtime_ports_metadata_no_writes"
    assert payload["agi_execution_authority"] is False
    assert payload["director_tool_execution_required"] is False
    assert summary["schema_version"] == "director.materialization_quality_runtime_ports.v1"
    assert summary["runtime_schedule_owner"] == "director.runtime"
    assert summary["runner_binding_owner"] == "roles.adapters"
    assert summary["director_runtime_public_summary_entrypoint"] == (
        "project_director_repair_materialization_bridge_metadata"
    )
    assert summary["ordered_step_ids"][0] == "materialization.hygiene_scaffold"
    assert summary["runner_step_ids"] == ["materialization.hygiene_scaffold"]
    assert summary["receipt_count"] == 2
    assert summary["coverage_uncovered_diagnostic_count"] == 1
    assert summary["scheduler_bridge_summary_owner"] == "director.runtime"
    assert summary["scheduler_bridge"]["adapter_projection_bridge"] is True
    assert summary["repair_kernel_migration_debt"]["schema_version"] == (
        "director.materialization_quality_repair_migration_debt.v1"
    )
    assert summary["adapter_projection_debt"] == [{"step_id": "materialization.hygiene_scaffold"}]
    assert (
        summary["receipt_lifecycle_by_step"]["materialization.hygiene_scaffold"]["receipt_lifecycle_evidence_status"]
        == "missing_evidence"
    )
    assert summary["dark_launch_cutover_blockers"] == ["adapter_projection_bridge"]


def test_runtime_materialization_quality_facade_runs_schedule_and_projects_evidence() -> None:
    schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1()
    )
    runner_step_ids = tuple(item.step_id for item in schedule.items)

    def runner(step) -> list[dict[str, object]]:
        if step.step_id != "materialization.hygiene_scaffold":
            return []
        return [
            {
                "tool": "write_file",
                "ok": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_scaffold_marker_cleanup",
                    "bridge_step_id": step.step_id,
                    "revalidation_evidence": {
                        "command": ["rtk", "pytest", "tests/test_hygiene.py"],
                        "exit_code": 0,
                        "errors_after": 0,
                    },
                },
            }
        ]

    result = run_director_materialization_quality_repair_facade(
        artifact_quality_errors=("Artifact quality scan failed: placeholder marker found",),
        runner_step_ids=runner_step_ids,
        runner=runner,
        plan_probe_preaudit={"status": "covered_plannable"},
        convergence_verifier_present=True,
    )
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairMaterializationQualityFacadeResultV1)
    assert payload["schema_version"] == "director.materialization_quality_repair_facade_result.v1"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["runner_binding_owner"] == "roles.adapters"
    assert payload["writes_allowed"] is False
    assert payload["director_tool_execution_required"] is True
    assert payload["schedule_reconciliation"]["exact_match"] is True
    assert payload["schedule_reconciliation"]["runner_order_matches_runtime"] is True
    assert payload["tool_results"][0]["runtime_step_id"] == "materialization.hygiene_scaffold"
    assert payload["tool_results"][0]["evidence_status"] == "resolved_evidence"
    assert payload["summary"]["runtime_facade_owner"] == "director.runtime"
    assert payload["summary"]["write_tool_evidence"] is True
    assert payload["summary"]["convergence_verifier_present"] is True
    assert payload["coverage_preaudit"]["schema_version"] == "director.repair_coverage_report.v1"


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
