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
    operation_content = plan.operations[0].content
    assert operation_content is not None
    assert "org.junit" not in operation_content
    assert "public static void main" in operation_content
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
                "package.json": ('{"name":"node-ts-app","private":true,"devDependencies":{"typescript":"5.4.5"}}\n'),
                "tsconfig.json": (
                    '{\n  "compilerOptions": {\n    "target": "ES2020",\n    "lib": ["ES2020", "DOM"],\n'
                    '    "strict": true\n  },\n  "include": ["src/**/*.ts"]\n}\n'
                ),
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
    assert payload["plan_summary"]["operation_count"] >= 2
    assert payload["composition_summary"]["ok"] is True
    changed = set(payload["composition_summary"]["changed_paths"] or [])
    assert "package.json" in changed
    assert "tsconfig.json" in changed
    pkg_after = ""
    tsconfig_after = ""
    for patch in payload["composition_summary"]["patches"] or []:
        if patch.get("path") == "package.json":
            pkg_after = str(patch.get("content_after") or "")
        if patch.get("path") == "tsconfig.json":
            tsconfig_after = str(patch.get("content_after") or "")
    assert '"@types/node"' in pkg_after
    assert '"types"' in tsconfig_after
    assert "node" in tsconfig_after


def test_public_runtime_dependency_repair_tsconfig_types_when_atypes_node_already_declared() -> None:
    """R178/M10: package already has @types/node but tsconfig never lists types:node."""

    source_tool = "deterministic_runtime_dependency_repair"
    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "package.json": (
                    '{"name":"glowing-insect-garden","private":true,'
                    '"devDependencies":{"@types/node":"^22.10.0","typescript":"^5.4.5"}}\n'
                ),
                "tsconfig.json": (
                    '{\n  "compilerOptions": {\n    "target": "ES2020",\n    "module": "ES2022",\n'
                    '    "lib": ["ES2020", "DOM"],\n    "strict": true,\n    "outDir": "dist",\n'
                    '    "rootDir": "src"\n  },\n  "include": ["src/**/*.ts"]\n}\n'
                ),
                "src/main.ts": "process.stdout.write('ok');\n",
            },
            artifact_quality_errors=(
                "src/main.ts(1,1): error TS2580: Cannot find name 'process'. "
                "Do you need to install type definitions for node? Try `npm i --save-dev @types/node`.",
            ),
            mode="commit",
        )
    )
    payload = planning_result.to_dict()
    assert payload["ok"] is True
    assert payload["planned"] is True
    changed = set(payload["composition_summary"]["changed_paths"] or [])
    assert "tsconfig.json" in changed
    # package.json must not be rewritten just to re-declare @types/node
    assert "package.json" not in changed
    tsconfig_after = ""
    for patch in payload["composition_summary"]["patches"] or []:
        if patch.get("path") == "tsconfig.json":
            tsconfig_after = str(patch.get("content_after") or "")
    assert '"types"' in tsconfig_after
    assert '"node"' in tsconfig_after


def test_public_runtime_dependency_repair_covers_and_plans_missing_python_requirements() -> None:
    source_tool = "deterministic_runtime_dependency_repair"
    diagnostics = (
        "requirements.txt must declare requests",
        "requirements.txt must declare pydantic",
    )

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=diagnostics))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={"src/main.py": "import requests\nfrom pydantic import BaseModel\n"},
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    )

    coverage_payload = coverage.to_dict()
    assert coverage_payload["covered_diagnostic_count"] == 2
    assert coverage_payload["uncovered_diagnostic_count"] == 0
    assert all(item["executable_runtime_plan_matched"] is True for item in coverage_payload["items"])
    assert all(item["matched_source_tools"] == [source_tool] for item in coverage_payload["items"])

    assert planning.ok is True
    assert planning.planned is True
    assert planning.effect_plan is not None
    forward = tuple(effect for effect in planning.effect_plan.effects if effect.contingency_kind == "forward")
    assert len(forward) == 1
    assert forward[0].tool_name == "write_file"
    assert forward[0].target_path == "requirements.txt"
    assert forward[0].exists_before is False
    assert dict(forward[0].arguments) == {
        "content": "pydantic\nrequests\n",
        "file": "requirements.txt",
    }


@pytest.mark.parametrize(
    "diagnostic",
    (
        "requirements.txt must declare at least one dependency",
        "requirements.txt must declare ../../evil",
        "requirements.txt must declare requests/evil",
        "requirements.txt must declare https://example.invalid/pkg.whl",
        "requirements.txt must declare requests>=2",
    ),
)
def test_public_runtime_dependency_repair_rejects_ambiguous_python_requirements_evidence(
    diagnostic: str,
) -> None:
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_runtime_dependency_repair",
            base_files={"src/main.py": "print('ok')\n"},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )

    assert planning.planned is False
    assert planning.effect_plan is None


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
    assert planning_payload["plan_summary"]["operation_count"] == 2
    changed_paths = set(planning_payload["composition_summary"]["changed_paths"])
    assert changed_paths == {"package.json", "tsconfig.json"}
    patches = {patch["path"]: patch["content_after"] for patch in planning_payload["composition_summary"]["patches"]}
    assert '"@types/node"' in patches["package.json"]
    assert '"node"' in patches["tsconfig.json"]


def test_public_runtime_dependency_repair_covers_missing_node_type_definition_file() -> None:
    source_tool = "deterministic_runtime_dependency_repair"
    diagnostic = "error TS2688: Cannot find type definition file for 'node'."

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
    assert planning_payload["plan_summary"]["operation_count"] == 2
    changed_paths = set(planning_payload["composition_summary"]["changed_paths"])
    assert changed_paths == {"package.json", "tsconfig.json"}
    patches = {patch["path"]: patch["content_after"] for patch in planning_payload["composition_summary"]["patches"]}
    assert '"@types/node"' in patches["package.json"]
    assert '"node"' in patches["tsconfig.json"]


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


def test_public_javascript_frontend_smoke_target_respects_esm_package(tmp_path: Path) -> None:
    """Generated smoke accepts mixed CLI/Web entrypoints and rejects broken HTML references."""

    source_tool = js_syntax.JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL
    diagnostic = "artifact_quality_error: npm test failed (exit=1): Could not find 'dist/tests/verify.test.js'"
    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "package.json": (
                    '{"name":"web-ts-app","type":"module","main":"dist/main.js",'
                    '"scripts":{"build":"tsc -p tsconfig.json",'
                    '"test":"npm run build && node --test dist/tests/verify.test.js"}}\n'
                ),
                "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n',
                "index.html": '<script type="module" src="./dist/web.js"></script>\n',
                "src/main.ts": "console.log('ok');\n",
                "tests/verify.test.ts": "import assert from 'node:assert';\nassert.ok(true);\n",
                "dist/main.js": "console.log('ok');\n",
                "dist/web.js": "console.log('web');\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    )
    payload = planning_result.to_dict()

    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["composition_summary"]["changed_paths"] == ["package.json", "tests/verify.test.js"]
    patches = {patch["path"]: patch["content_after"] for patch in payload["composition_summary"]["patches"]}
    assert "node --test tests/verify.test.js" in patches["package.json"]
    smoke_content = patches["tests/verify.test.js"]
    assert "import assert from 'node:assert';" in smoke_content
    assert "fileURLToPath(import.meta.url)" in smoke_content
    assert "require('assert')" not in smoke_content
    assert "is not referenced by declared HTML" not in smoke_content
    assert "HTML references undeclared script" in smoke_content

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the generated JavaScript smoke test")
    fixture_files = {
        "package.json": '{"name":"web-ts-app","type":"module"}\n',
        "index.html": '<script type="module" src="./dist/web.js"></script>\n',
        "dist/main.js": "console.log('cli');\n",
        "dist/web.js": "console.log('web');\n",
        "tests/verify.test.js": smoke_content,
    }
    for relative_path, content in fixture_files.items():
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    passed = subprocess.run(
        [node, "--test", "tests/verify.test.js"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert passed.returncode == 0, passed.stdout + passed.stderr

    (tmp_path / "index.html").write_text(
        '<script type="module" src="./dist/missing.js"></script>\n',
        encoding="utf-8",
    )
    rejected = subprocess.run(
        [node, "--test", "tests/verify.test.js"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert rejected.returncode != 0
    assert "HTML references undeclared script dist/missing.js" in rejected.stdout + rejected.stderr


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


def test_public_typescript_local_js_import_repair_plans_direct_node_typescript_test_runtime_miss() -> None:
    diagnostic = (
        "npm test failed (exit=1): Error [ERR_MODULE_NOT_FOUND]: Cannot find module "
        "/workspace/src/verify.js imported from /workspace/tests/verify.test.ts"
    )
    base_files = {
        "package.json": '{"type":"module","scripts":{"test":"node --test tests/verify.test.ts"}}\n',
        "tsconfig.json": (
            '{"compilerOptions":{"module":"NodeNext","moduleResolution":"NodeNext"},'
            '"include":["src/**/*.ts"],"exclude":["tests"]}\n'
        ),
        "src/verify.ts": "export function verify(): boolean { return true; }\n",
        "tests/verify.test.ts": (
            'import { verify } from "../src/verify.js";\n'
            'import test from "node:test";\n'
            'test("verify", () => { if (!verify()) throw new Error("failed"); });\n'
        ),
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
    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["executable_runtime_plan_diagnostic_count"] == 1
    assert js_syntax.TYPESCRIPT_LOCAL_JS_IMPORT_SOURCE_TOOL in coverage_payload["items"][0]["matched_source_tools"]
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.local_js_import_extension"
    assert planning["composition_summary"]["patch_count"] == 1
    patch = planning["composition_summary"]["patches"][0]
    assert patch["path"] == "tests/verify.test.ts"
    assert 'from "../src/verify.ts"' in patch["content_after"]
    assert 'from "../src/verify.js"' not in patch["content_after"]


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


def test_public_npm_script_contract_python_command_diagnostic_is_covered_plannable() -> None:
    source_tool = js_syntax.NPM_SCRIPT_CONTRACT_SOURCE_TOOL
    diagnostic = (
        "Artifact quality scan failed: npm package manifest contains Python command in script 'test:py' in package.json"
    )
    base_files = {
        "package.json": (
            '{"type":"module","scripts":{'
            '"test":"node --test tests/product.test.js",'
            '"test:py":"python -m unittest discover -s tests",'
            '"test:all":"node --test tests/product.test.js && python -m unittest discover -s tests"'
            "}}\n"
        ),
        "tests/product.test.js": "import test from 'node:test';\ntest('ok', () => {});\n",
    }

    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=(diagnostic,),
            base_files=base_files,
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    coverage_payload = coverage.to_dict()
    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["items"][0]["known_rule_matched"] is True
    assert coverage_payload["items"][0]["executable_runtime_plan_matched"] is True
    assert coverage_payload["items"][0]["matched_source_tools"] == [source_tool]
    assert probe.status == "covered_plannable"
    assert probe.plannable_source_tools == (source_tool,)
    assert probe.items[0].status == "covered_plannable"
    assert probe.items[0].patch_count == 1
    assert planning["ok"] is True
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "python" not in content_after.lower()
    assert '"test:py": "node --test tests/product.test.js"' in content_after
    assert '"test:all": "node --test tests/product.test.js"' in content_after


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


def test_public_javascript_dom_global_runtime_guard_uses_typed_runtime_global() -> None:
    source_tool = js_syntax.JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL
    diagnostic = {
        "source": "runtime_smoke",
        "code": "javascript_dom_global_in_node_runtime",
        "message": "Browser DOM global window is not available in Node.",
        "path": "src/web.ts",
        "runtime_global": "window",
        "raw": "typed runtime smoke metadata only",
    }

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
                    '  window.dispatchEvent(new Event("polaris-ready"));\n'
                    "}\n\n"
                    "function whenReady(): void {\n"
                    "  bootstrap();\n"
                    "}\n\n"
                    "whenReady();\n"
                ),
            },
            artifact_quality_issues=(diagnostic,),
            mode="shadow",
        )
    )
    planning_payload = planning_result.to_dict()

    assert planning_payload["ok"] is True
    assert planning_payload["planned"] is True
    assert planning_payload["plan_summary"]["rule_id"] == "javascript.dom_global_runtime_guard"
    content_after = planning_payload["composition_summary"]["patches"][0]["content_after"]
    assert 'if (typeof window !== "undefined") {' in content_after
    assert 'if (typeof document !== "undefined") {' not in content_after
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


def test_public_typescript_strict_null_relaxation_repair_relaxes_tsconfig_on_ts18048() -> None:
    """Round B-v2: deterministic tsconfig relaxation when TS18048/TS2322 dominate.

    L1-01 m03-r21 proved MiniMax-M3 ignores prompt-side relaxation guidance.
    A deterministic repair relaxing compilerOptions.strict on TS18048/TS2322
    does NOT rely on model compliance, and unblocks the build so the existing
    node_test_missing_directory_target repair can run and create the test file.
    """
    diagnostic = (
        "src/models/Humidity.ts(12,7): error TS18048: 'dewPoint' is possibly 'undefined'.\n"
        "src/models/Humidity.ts(20,9): error TS2322: Type 'number | undefined' "
        "is not assignable to type 'number'."
    )
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_STRICT_NULL_RELAXATION_SOURCE_TOOL,
            base_files={
                "tsconfig.json": (
                    '{"compilerOptions":{"strict":true,"strictNullChecks":true,'
                    '"noUnusedLocals":true,"outDir":"dist","rootDir":"src"}}\n'
                ),
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    coverage_payload = coverage.to_dict()
    matched_source_tools = coverage_payload["items"][0]["matched_source_tools"]
    assert ts_syntax.TYPESCRIPT_STRICT_NULL_RELAXATION_SOURCE_TOOL in matched_source_tools
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.strict_null_relaxation"
    content_after = planning["composition_summary"]["patches"][0]["content_after"]
    assert '"strict": false' in content_after
    assert '"noUnusedLocals": false' in content_after


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


def test_public_html_module_script_consumes_scanner_missing_compiled_javascript_diagnostic() -> None:
    diagnostic = (
        "Artifact quality scan failed: HTML module script references missing compiled JavaScript "
        "'./src/web.js' in index.html; TypeScript build emitted './dist/web.js'"
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
            base_files={
                "index.html": '<script type="module" src="./src/web.js"></script>\n',
                "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"},"include":["src/**/*.ts"]}\n',
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
    assert 'src="./dist/web.js"' in planning["composition_summary"]["patches"][0]["content_after"]


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


def test_public_html_module_script_rewrites_dot_slash_src_typescript_to_dist() -> None:
    """L1-01 r154: ./src/web.ts must become ./dist/web.js (tsc rootDir=src), not ./src/web.js."""

    diagnostic = (
        "Artifact quality scan failed: HTML module script references TypeScript source "
        "'./src/web.ts' in index.html; static entrypoints must load JavaScript"
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
            base_files={
                "index.html": (
                    '<!doctype html>\n<html lang="en">\n<body>\n'
                    '<script type="module" src="./src/web.ts"></script>\n'
                    "</body>\n</html>\n"
                ),
                "tsconfig.json": ('{"compilerOptions":{"outDir":"dist","rootDir":"src"},"include":["src/**/*.ts"]}\n'),
                "src/web.ts": "console.log('web');\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert 'src="./dist/web.js"' in repaired


def test_public_html_module_script_rewrites_external_and_inline_typescript_refs() -> None:
    """One scanner diagnostic repairs every browser import of the same TS entrypoint."""

    diagnostic = (
        "Artifact quality scan failed: HTML module script references TypeScript source "
        "'./src/web.ts' in index.html; static entrypoints must load JavaScript"
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
            base_files={
                "index.html": (
                    '<script type="module" src="./src/web.ts"></script>\n'
                    '<script type="module">\n'
                    "import { startWhenReady } from './src/web.ts';\n"
                    "startWhenReady('garden');\n"
                    "</script>\n"
                ),
                "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n',
                "src/web.ts": "export const startWhenReady = () => {};\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["operation_count"] == 2
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert repaired.count("./dist/web.js") == 2
    assert "./src/web.ts" not in repaired
    assert "./src/web.ts" not in repaired
    assert "./src/web.js" not in repaired


def test_public_html_truncated_entrypoint_closes_script_and_html_and_rewrites_ts_script() -> None:
    """L1-01 r154: truncated index.html + .ts module script must clear both quality errors."""

    truncated = (
        '<!doctype html>\n<html lang="en">\n'
        '<script type="module" src="./src/web.ts">\n'
        '<body>\n<main><canvas id="garden-canvas"></main></body>\n'
        "<head><title>firefly</title></head>\n"
    )
    diagnostics = (
        "Artifact quality scan failed: syntax error in index.html: truncated/incomplete HTML: "
        "missing </html> closing tag; 1 unclosed <script> tag(s)",
        "Artifact quality scan failed: HTML module script references TypeScript source "
        "'./src/web.ts' in index.html; static entrypoints must load JavaScript",
    )

    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=diagnostics)
    ).to_dict()
    assert coverage["covered_diagnostic_count"] == 2
    assert coverage["uncovered_diagnostic_count"] == 0

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
            base_files={
                "index.html": truncated,
                "tsconfig.json": ('{"compilerOptions":{"outDir":"dist","rootDir":"src"},"include":["src/**/*.ts"]}\n'),
                "src/web.ts": "export {};\n",
                "dist/web.js": "export {};\n",
            },
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "html.truncated_entrypoint_closure"
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "</html>" in repaired.lower()
    assert repaired.lower().count("<script") <= repaired.lower().count("</script>")
    assert 'src="./dist/web.js"' in repaired
    assert "./src/web.ts" not in repaired


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


def test_public_typescript_duplicate_function_removes_later_stub() -> None:
    """L1-01 r157: trailing stub export function after real async export blocks tsc."""

    diagnostic = (
        "src/verify.ts(10,17): error TS2393: Duplicate function implementation.\n"
        "src/verify.ts(10,17): error TS2323: Cannot redeclare exported variable 'runVerification'."
    )
    source = (
        "export async function runVerification(): Promise<boolean> {\n"
        "  return true;\n"
        "}\n"
        "\n"
        "export function runVerification(..._args: unknown[]): any {\n"
        "  return false;\n"
        "}\n"
    )
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_DUPLICATE_FUNCTION_SOURCE_TOOL,
            base_files={"src/verify.ts": source},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert coverage.to_dict()["covered_diagnostic_count"] >= 1
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.duplicate_function"
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert after.count("function runVerification") == 1
    assert "export async function runVerification" in after
    assert "export function runVerification(..._args" not in after


def test_public_typescript_member_alias_rewrites_position_glow_and_garden_tick() -> None:
    """L1-01 r160: Firefly.x/glow and Garden.setMoonPhase/tick vs real model surface."""

    # Line numbers must match source rows (1-based) used by the alias planner.
    diagnostics = (
        "src/web.ts(4,10): error TS2339: Property 'setMoonPhase' does not exist on type 'Garden'.",
        "src/web.ts(5,10): error TS2339: Property 'tick' does not exist on type 'Garden'.",
        "src/web.ts(6,21): error TS2339: Property 'x' does not exist on type 'Firefly'.",
        "src/web.ts(7,21): error TS2339: Property 'glow' does not exist on type 'Firefly'.",
    )
    base_files = {
        "src/models/Firefly.ts": (
            "export class Firefly {\n"
            "  get position(): { x: number; y: number } { return { x: 1, y: 2 }; }\n"
            "  currentGlow(): number { return 0.5; }\n"
            "  tick(dt: number): void { void dt; }\n"
            "}\n"
        ),
        "src/models/index.ts": (
            "import type { Firefly } from './Firefly.js';\n"
            "export interface Garden {\n"
            "  readonly fireflies: ReadonlyArray<Firefly>;\n"
            "  readonly moon: { tick(dt: number): void };\n"
            "}\n"
        ),
        "src/web.ts": (
            "import type { Garden } from './models/index.js';\n"  # 1
            "import type { Firefly } from './models/Firefly.js';\n"  # 2
            "export function paint(garden: Garden, firefly: Firefly): void {\n"  # 3
            "  garden.setMoonPhase('full');\n"  # 4
            "  garden.tick(0.016);\n"  # 5
            "  const _x = firefly.x;\n"  # 6
            "  const _g = firefly.glow;\n"  # 7
            "  void _x; void _g;\n"  # 8
            "}\n"  # 9
        ),
    }
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()
    assert planning["ok"] is True
    assert planning["planned"] is True
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "setMoonPhase(" not in after
    assert "__entity.tick" in after
    assert "firefly.position.x" in after
    assert "firefly.currentGlow()" in after


def test_public_typescript_literal_union_expand_adds_missing_literals() -> None:
    """L1-01 r160: Type '\"waxing\"' not assignable to MoonPhaseName expands the union."""

    diagnostic = "src/web.ts(3,10): error TS2322: Type '\"waxing\"' is not assignable to type 'MoonPhaseName'."
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_LITERAL_UNION_EXPAND_SOURCE_TOOL,
            base_files={
                "src/models/types.ts": 'export type MoonPhaseName = "new" | "full";\n',
                "src/web.ts": 'import type { MoonPhaseName } from "./models/types.js";\nconst p: MoonPhaseName = "waxing";\n',
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    assert planning["ok"] is True
    assert planning["planned"] is True
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert '"waxing"' in after
    assert "MoonPhaseName" in after


def test_public_typescript_literal_union_expand_normalizes_type_only_string_enum() -> None:
    """TS2322 string literals can safely use a type-only string enum as a union."""

    diagnostics = (
        "src/models/Firefly.ts(10,5): error TS2322: Type '\"resting\"' is not assignable to type 'FireflyMode'.",
        "src/models/Firefly.ts(14,5): error TS2322: Type '\"flashing\"' is not assignable to type 'FireflyMode'.",
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_LITERAL_UNION_EXPAND_SOURCE_TOOL,
            base_files={
                "src/models/types.ts": (
                    "export enum FireflyMode {\n"
                    "  Resting = 'resting',\n"
                    "  Glowing = 'glowing',\n"
                    "  Flashing = 'flashing',\n"
                    "}\n"
                ),
                "src/models/Firefly.ts": (
                    "import type { FireflyMode } from './types.js';\n"
                    "let mode: FireflyMode = 'resting';\n"
                    "mode = 'flashing';\n"
                ),
            },
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    patch = planning["composition_summary"]["patches"][0]
    assert patch["path"] == "src/models/types.ts"
    assert 'export type FireflyMode = "resting" | "glowing" | "flashing";' in patch["content_after"]


def test_public_typescript_literal_union_expand_preserves_runtime_enum_authority() -> None:
    """Runtime ``Enum.Member`` consumers make enum-to-union normalization unsafe."""

    diagnostic = "src/models/Firefly.ts(2,5): error TS2322: Type '\"resting\"' is not assignable to type 'FireflyMode'."
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_LITERAL_UNION_EXPAND_SOURCE_TOOL,
            base_files={
                "src/models/types.ts": ("export enum FireflyMode { Resting = 'resting', Flashing = 'flashing' }\n"),
                "src/models/Firefly.ts": (
                    "import { FireflyMode } from './types.js';\n"
                    "let mode: FireflyMode = 'resting';\n"
                    "export const initial = FireflyMode.Resting;\n"
                ),
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["patches"] == []


def test_public_typescript_init_property_alias_renames_garden_init_keys() -> None:
    """L1-01 r160: createGarden({ fireflies, flowers, humidity }) → *Count / initialHumidity."""

    diagnostic = (
        "src/web.ts(1,20): error TS2353: Object literal may only specify known properties, "
        "and 'fireflies' does not exist in type 'GardenInit'."
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_INIT_PROPERTY_ALIAS_SOURCE_TOOL,
            base_files={
                "src/models/index.ts": (
                    "export interface GardenInit {\n"
                    "  readonly fireflyCount?: number;\n"
                    "  readonly flowerCount?: number;\n"
                    "  readonly initialHumidity?: number;\n"
                    "}\n"
                ),
                "src/web.ts": "const g = createGarden({ fireflies: 6, flowers: 5, humidity: 0.7 });\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    assert planning["ok"] is True
    assert planning["planned"] is True
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "fireflyCount: 6" in after
    assert "flowerCount: 5" in after
    assert "initialHumidity: 0.7" in after
    assert "fireflies:" not in after


def test_public_typescript_import_type_value_conflict_drops_type_flower_keeps_value() -> None:
    """L1-01 r164: type Flower + Flower value import → TS2300/TS1361; keep value only."""

    diagnostics = (
        "src/engine/simulation.ts(3,8): error TS2300: Duplicate identifier 'Flower'.",
        "src/engine/simulation.ts(6,3): error TS2300: Duplicate identifier 'Flower'.",
        "src/engine/simulation.ts(10,11): error TS1361: 'Flower' cannot be used as a value "
        "because it was imported using 'import type'.",
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
            base_files={
                "src/models/Flower.ts": "export class Flower { constructor(public n: number) {} }\n",
                "src/engine/simulation.ts": (
                    "import {\n"  # 1
                    "  type Firefly,\n"  # 2
                    "  type Flower,\n"  # 3
                    "  Humidity,\n"  # 4
                    "  createFireflySwarm,\n"  # 5
                    "  Flower,\n"  # 6
                    "} from '../models';\n"  # 7
                    "export function seed(): Flower {\n"  # 8
                    "  const f: Firefly | null = null;\n"  # 9
                    "  return new Flower(1);\n"  # 10
                    "}\n"
                ),
            },
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.import_type_value_conflict"
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "type Flower" not in after
    assert "Flower" in after
    assert "new Flower(1)" in after
    assert "type Firefly" in after  # unrelated type-only stays


def test_public_typescript_import_type_value_conflict_promotes_type_only_import() -> None:
    """L1-01 r164: pure import type { Flower } used as value → promote to value import."""

    diagnostic = (
        "src/engine/simulation.ts(2,14): error TS1361: 'Flower' cannot be used as a value "
        "because it was imported using 'import type'."
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
            base_files={
                "src/models/Flower.ts": "export class Flower {}\n",
                "src/engine/simulation.ts": (
                    "import type { Flower } from '../models';\n"
                    "export function make(): Flower { return new Flower(); }\n"
                ),
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    assert planning["ok"] is True
    assert planning["planned"] is True
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "import type { Flower }" not in after
    assert "import { Flower }" in after or "import {Flower}" in after.replace(" ", "")
    assert "new Flower()" in after


def test_public_typescript_arg_type_function_alias_rewrites_humidity_to_hydration() -> None:
    """L1-01 r161: adjustHumidity(FlowerState) → adjustHydration + import rewrite."""

    diagnostic = (
        "src/web.ts(4,12): error TS2345: Argument of type 'FlowerState' is not assignable "
        "to parameter of type 'HumidityState'."
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_ARG_TYPE_FUNCTION_ALIAS_SOURCE_TOOL,
            base_files={
                "src/models/Flower.ts": (
                    "export interface FlowerState { hydration: number; }\n"
                    "export function adjustHydration(state: FlowerState, delta: number): FlowerState {\n"
                    "  return { hydration: state.hydration + delta };\n"
                    "}\n"
                ),
                "src/models/Humidity.ts": (
                    "export interface HumidityState { value: number; }\n"
                    "export function adjustHumidity(state: HumidityState, delta: number): HumidityState {\n"
                    "  return { value: state.value + delta };\n"
                    "}\n"
                ),
                "src/models/index.ts": ("export * from './Flower.js';\nexport * from './Humidity.js';\n"),
                "src/web.ts": (
                    "import { adjustHumidity } from './models/index.js';\n"  # 1
                    "import type { FlowerState } from './models/index.js';\n"  # 2
                    "export function water(fl: FlowerState): FlowerState {\n"  # 3
                    "  return adjustHumidity(fl, 0.1);\n"  # 4
                    "}\n"  # 5
                ),
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.arg_type_function_alias"
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "adjustHydration(fl, 0.1)" in after
    assert "adjustHumidity(fl" not in after
    # Named import must follow the callee rename (otherwise TS2304).
    assert "import { adjustHydration } from './models/index.js';" in after
    assert "import { adjustHumidity }" not in after


def test_public_typescript_json_as_source_does_not_invent_vitest_smoke() -> None:
    """Missing tests stay owned by their declared PM/CE task, not M10."""

    real_package = (
        "{\n"
        '  "name": "firefly-garden-simulator",\n'
        '  "version": "0.1.0",\n'
        '  "private": true,\n'
        '  "type": "module",\n'
        '  "scripts": {\n'
        '    "build": "tsc -p tsconfig.json",\n'
        '    "test": "vitest run",\n'
        '    "start": "node dist/main.js"\n'
        "  }\n"
        "}\n"
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL,
            base_files={
                "package.json": real_package,
                "src/main.ts": "export function main(): void {}\n",
            },
            artifact_quality_errors=(),
            mode="shadow",
        )
    ).to_dict()
    assert planning["ok"] is False
    assert planning["planned"] is False


def test_public_typescript_json_as_source_rewrites_only_proven_misplaced_manifest() -> None:
    """L1-01 r159: package.json body written into src/verify.ts blocks tsc (TS1005).

    Missing test artifacts remain outside this source tool's authority.
    """

    diagnostic = "src/verify.ts(1,8): error TS1005: ';' expected."
    package_json_body = (
        '{"name":"firefly-garden-simulator","version":"1.0.0","private":true,'
        '"scripts":{"build":"tsc -p tsconfig.json","test":"node --test --import tsx tests/*.test.ts",'
        '"verify":"node --experimental-strip-types src/verify.ts"},"type":"module"}\n'
    )
    real_package = (
        "{\n"
        '  "name": "firefly-garden-simulator",\n'
        '  "version": "0.1.0",\n'
        '  "private": true,\n'
        '  "type": "module",\n'
        '  "scripts": {\n'
        '    "build": "tsc -p tsconfig.json",\n'
        '    "start": "node dist/main.js",\n'
        '    "test": "node --test --import tsx tests/*.test.ts"\n'
        "  }\n"
        "}\n"
    )
    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(diagnostic,),
            artifact_quality_issues=(
                {
                    "code": "typescript_json_as_source",
                    "message": "package manifest JSON was proven in a TypeScript source file",
                    "path": "src/verify.ts",
                    "source": "materialization_quality",
                },
            ),
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL,
            base_files={
                "src/verify.ts": package_json_body,
                "package.json": real_package,
                "src/main.ts": "export function main(): void {}\n",
            },
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    coverage_payload = coverage.to_dict()
    assert coverage_payload["covered_diagnostic_count"] == 1
    assert coverage_payload["items"][0]["known_rule_matched"] is False
    assert coverage_payload["items"][1]["matched_rule_ids"] == ["typescript.json_as_source"]
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.json_as_source"
    patches = planning["composition_summary"]["patches"]
    by_path = {str(patch.get("path") or ""): patch for patch in patches}
    assert "src/verify.ts" in by_path
    verify_after = str(by_path["src/verify.ts"].get("content_after") or "")
    assert '"scripts"' not in verify_after
    assert "export function runVerification" in verify_after
    assert "tests/verify.test.ts" not in by_path


def test_public_typescript_readonly_assignment_mutates_readonly_array_fields() -> None:
    """L1-01 r157: TS2542 ReadonlyArray index writes + TS2540 property writes."""

    diagnostics = (
        "src/engine/simulation.ts(7,5): error TS2542: Index signature in type "
        "'readonly number[]' only permits reading.",
        "src/engine/simulation.ts(9,3): error TS2540: Cannot assign to 'humidity' because it is a read-only property.",
    )
    source = (
        "export interface GardenScene {\n"  # 1
        "  readonly humidity: number;\n"  # 2
        "  readonly fireflies: ReadonlyArray<number>;\n"  # 3
        "}\n"  # 4
        "export function step(scene: GardenScene): GardenScene {\n"  # 5
        "  for (let i = 0; i < scene.fireflies.length; i++) {\n"  # 6
        "    scene.fireflies[i] = i;\n"  # 7
        "  }\n"  # 8
        "  scene.humidity = 1;\n"  # 9
        "  return scene;\n"  # 10
        "}\n"  # 11
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_READONLY_ASSIGNMENT_SOURCE_TOOL,
            base_files={"src/engine/simulation.ts": source},
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "readonly humidity" not in after
    assert "ReadonlyArray" not in after
    assert "fireflies: number[]" in after
    assert "humidity: number" in after


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


