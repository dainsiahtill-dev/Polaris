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

    effect_plan = planning_result.effect_plan
    assert effect_plan is not None
    forward_effects = tuple(effect for effect in effect_plan.effects if effect.contingency_kind == "forward")
    rollback_effects = tuple(effect for effect in effect_plan.effects if effect.contingency_kind == "rollback")
    assert len(forward_effects) == 1
    assert len(rollback_effects) == 1
    assert forward_effects[0].tool_name == "edit_file"
    assert dict(forward_effects[0].arguments) == {
        "file": "src/models/Market.ts",
        "replace": "  type ReputationTier,\n",
        "search": "  export type ReputationTier,\n",
    }
    assert rollback_effects[0].tool_name == "write_file"
    assert dict(rollback_effects[0].arguments) == {
        "content": content,
        "file": "src/models/Market.ts",
    }
    assert rollback_effects[0].activates_after_call_id == forward_effects[0].call_id
    assert effect_plan.effect_count == 2
    assert payload["effect_plan"]["plan_hash"] == effect_plan.plan_hash


def test_director_repair_effect_plan_is_recursive_immutable_and_hash_bound() -> None:
    arguments = (
        ("file", "src/app.ts"),
        ("replace", "good"),
        ("search", "bad"),
    )
    effect = DirectorRepairEffectV1(
        call_id="repair-call-1",
        operation_id="repair-op-1",
        tool_name="edit_file",
        arguments=arguments,
        contingency_kind="forward",
        target_path="src/app.ts",
        expected_before_hash="a" * 64,
        expected_after_hash="b" * 64,
        exists_before=True,
        exists_after=True,
    )
    rollback_effect = DirectorRepairEffectV1(
        call_id="repair-call-rollback-1",
        operation_id="repair-op-rollback-1",
        tool_name="write_file",
        arguments=(("content", "bad"), ("file", "src/app.ts")),
        contingency_kind="rollback",
        activates_after_call_id=effect.call_id,
        target_path="src/app.ts",
        expected_before_hash="b" * 64,
        expected_after_hash="a" * 64,
        exists_before=True,
        exists_after=True,
    )
    plan = DirectorRepairEffectPlanV1(
        plan_id="repair-plan-1",
        source_tool="deterministic_test_repair",
        effects=(effect, rollback_effect),
        round_number=1,
    )

    assert effect.arguments_hash == hash_directed_effect_arguments(arguments)
    assert plan.effect_count == 2
    assert plan.plan_hash == hash_director_repair_effect_plan(
        plan_id=plan.plan_id,
        source_tool=plan.source_tool,
        round_number=plan.round_number,
        effects=plan.effects,
    )
    assert plan.to_dict()["effects"][0]["arguments"] == {
        "file": "src/app.ts",
        "replace": "good",
        "search": "bad",
    }
    assert plan.plan_hash != hash_director_repair_effect_plan(
        plan_id=plan.plan_id,
        source_tool=plan.source_tool,
        round_number=plan.round_number,
        effects=plan.effects,
        schema_version="director.repair_effect_plan.v2",
        owner_cell=plan.owner_cell,
    )

    with pytest.raises(ValueError, match="schema_version"):
        DirectorRepairEffectPlanV1(
            plan_id="repair-plan-schema-drift",
            source_tool="deterministic_test_repair",
            effects=(effect, rollback_effect),
            schema_version="director.repair_effect_plan.v2",
        )

    with pytest.raises(ValueError, match="owner_cell"):
        DirectorRepairEffectPlanV1(
            plan_id="repair-plan-owner-drift",
            source_tool="deterministic_test_repair",
            effects=(effect, rollback_effect),
            owner_cell="roles.kernel",
        )

    with pytest.raises(TypeError, match="tuple of immutable key/value pairs"):
        DirectorRepairEffectV1(
            call_id="repair-call-2",
            operation_id="repair-op-2",
            tool_name="edit_file",
            arguments={"file": "src/app.ts"},  # type: ignore[arg-type]
            contingency_kind="forward",
            target_path="src/app.ts",
            expected_before_hash="a" * 64,
            expected_after_hash="b" * 64,
            exists_before=True,
            exists_after=True,
        )

    with pytest.raises(TypeError, match="tuple of immutable key/value pairs"):
        DirectorRepairEffectV1(
            call_id="repair-call-list",
            operation_id="repair-op-list",
            tool_name="edit_file",
            arguments=list(arguments),  # type: ignore[arg-type]
            contingency_kind="forward",
            target_path="src/app.ts",
            expected_before_hash="a" * 64,
            expected_after_hash="b" * 64,
            exists_before=True,
            exists_after=True,
        )

    with pytest.raises(TypeError, match="unexpected keyword argument 'arguments_hash'"):
        DirectorRepairEffectV1(  # type: ignore[call-arg]
            call_id="repair-call-forged-hash",
            operation_id="repair-op-forged-hash",
            tool_name="edit_file",
            arguments=arguments,
            arguments_hash="f" * 64,
            contingency_kind="forward",
            target_path="src/app.ts",
            expected_before_hash="a" * 64,
            expected_after_hash="b" * 64,
            exists_before=True,
            exists_after=True,
        )

    with pytest.raises(ValueError, match=r"workspace-relative|traversal-free"):
        DirectorRepairEffectV1(
            call_id="repair-call-traversal",
            operation_id="repair-op-traversal",
            tool_name="delete_file",
            arguments=(("file", "../app.ts"),),
            contingency_kind="forward",
            target_path="../app.ts",
            expected_before_hash="a" * 64,
            expected_after_hash="b" * 64,
            exists_before=True,
            exists_after=False,
        )

    with pytest.raises(ValueError, match="tool_name"):
        DirectorRepairEffectV1(
            call_id="repair-call-shell",
            operation_id="repair-op-shell",
            tool_name="execute_command",  # type: ignore[arg-type]
            arguments=(("file", "src/app.ts"),),
            contingency_kind="forward",
            target_path="src/app.ts",
            expected_before_hash="a" * 64,
            expected_after_hash="b" * 64,
            exists_before=True,
            exists_after=True,
        )

    with pytest.raises(ValueError, match="round_number must be exactly 1"):
        DirectorRepairEffectPlanV1(
            plan_id="repair-plan-round-2",
            source_tool="deterministic_test_repair",
            effects=(effect, rollback_effect),
            round_number=2,  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="call_id values must be unique"):
        DirectorRepairEffectPlanV1(
            plan_id="repair-plan-duplicate",
            source_tool="deterministic_test_repair",
            effects=(effect, effect),
            round_number=1,
        )


def test_multi_edit_patch_collapses_to_one_atomic_forward_and_rollback() -> None:
    before = "abcde"
    after_second = "aBcDe"
    first = RepairOperation(
        kind="text_replace",
        path="src/app.ts",
        operation_id="edit-first",
        span_start=1,
        span_end=2,
        expected="b",
        replacement="B",
    )
    second = RepairOperation(
        kind="text_replace",
        path="src/app.ts",
        operation_id="edit-second",
        span_start=3,
        span_end=4,
        expected="d",
        replacement="D",
    )
    plan = RepairPlan(
        plan_id="multi-edit-plan",
        rule_id="multi-edit-rule",
        source_tool="deterministic_multi_edit_repair",
        operations=(first, second),
    )
    composition = CompositionResult(
        ok=True,
        patches=(
            ComposedPatch(
                path="src/app.ts",
                content_before=before,
                content_after=after_second,
                operation_ids=(first.operation_id, second.operation_id),
            ),
        ),
    )

    effect_plan = runtime_public_service._to_public_repair_effect_plan(plan, composition)

    assert effect_plan is not None
    rollbacks = tuple(effect for effect in effect_plan.effects if effect.contingency_kind == "rollback")
    forwards = tuple(effect for effect in effect_plan.effects if effect.contingency_kind == "forward")
    assert len(rollbacks) == 1
    assert len(forwards) == 1
    assert forwards[0].tool_name == "write_file"
    assert dict(forwards[0].arguments)["content"] == after_second
    assert rollbacks[0].tool_name == "write_file"
    assert dict(rollbacks[0].arguments)["content"] == before
    assert rollbacks[0].expected_before_hash == forwards[0].expected_after_hash
    assert rollbacks[0].expected_after_hash == sha256_text(before)


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


def test_public_typescript_unresolved_identifier_import_from_existing_barrel_is_plannable() -> None:
    diagnostic = "src/main.ts(7,47): error TS2304: Cannot find name 'dayOfYear'."
    main = (
        'import { createMoonPhase, fromDayOfCycle, ambientLightAt } from "./index.js";\n'
        "\n"
        "export function run(now: number): string {\n"
        "  const moon = createMoonPhase(fromDayOfCycle(dayOfYear(now)));\n"
        "  return `${moon}:${ambientLightAt(moon)}`;\n"
        "}\n"
    )
    barrel = 'export { createMoonPhase, fromDayOfCycle, ambientLightAt, dayOfYear } from "./models/MoonPhase.js";\n'
    moon = (
        "export type MoonPhase = 'new' | 'full';\n"
        "export function createMoonPhase(_day: number): MoonPhase { return 'new'; }\n"
        "export function fromDayOfCycle(day: number): number { return day % 29; }\n"
        "export function ambientLightAt(_moon: MoonPhase): number { return 0.5; }\n"
        "export function dayOfYear(now: number): number { return Math.floor(now / 86400000); }\n"
    )
    base_files = {
        "src/main.ts": main,
        "src/index.ts": barrel,
        "src/models/MoonPhase.ts": moon,
    }

    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=(diagnostic,),
            base_files=base_files,
            source_tools=(ts_syntax.TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL,),
        )
    ).to_dict()
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert probe["status"] == "covered_plannable"
    assert probe["plannable_source_tools"] == [ts_syntax.TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL]
    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["plan_summary"]["rule_id"] == "typescript.unresolved_identifier"
    assert planning["plan_summary"]["operation_count"] == 1
    patch = planning["composition_summary"]["patches"][0]
    assert patch["path"] == "src/main.ts"
    assert 'dayOfYear } from "./index.js";' in patch["content_after"]


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


def test_typescript_duplicate_object_property_diagnostic_is_executable_coverage() -> None:
    diagnostic = (
        "src/models/types.ts(28,3): error TS1117: "
        "An object literal cannot have multiple properties with the same name."
    )

    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(diagnostic,))
    )

    assert coverage.covered_diagnostic_count == 1
    assert coverage.executable_runtime_plan_diagnostic_count == 1
    assert coverage.uncovered_diagnostic_count == 0
    assert coverage.items[0].matched_rule_ids == ("typescript.duplicate_object_property",)
    assert coverage.items[0].matched_source_tools == (
        "deterministic_typescript_duplicate_object_property_repair",
    )


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


def test_runtime_dispatcher_supported_binding_consumes_typed_diagnostics_without_legacy_errors() -> None:
    content = "const label = makeLabel(42, width);\n"
    column = content.index("42") + 1
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts2345",
        message="Argument of type 'number' is not assignable to parameter of type 'string'.",
        path="src/garden.ts",
        line=1,
        column=column,
        raw=(
            "src/garden.ts"
            f"(1,{column}): error TS2345: Argument of type 'number' is not assignable "
            "to parameter of type 'string'."
        ),
        metadata={"confidence": "parser"},
    )

    planning = plan_runtime_repair(
        source_tool=ts_syntax.TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL,
        base_files={"src/garden.ts": content},
        artifact_quality_errors=(),
        repair_diagnostics=(diagnostic,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert planning.diagnostics[0].path == "src/garden.ts"
    assert planning.composition.patches[0].content_after == "const label = makeLabel(String(42), width);\n"


def test_runtime_dispatcher_supported_binding_runs_typed_diagnostics_without_legacy_errors(tmp_path: Path) -> None:
    relative_path = "src/garden.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = "const label = makeLabel(42, width);\n"
    target.write_text(content, encoding="utf-8")
    column = content.index("42") + 1
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts2345",
        message="Argument of type 'number' is not assignable to parameter of type 'string'.",
        path=relative_path,
        line=1,
        column=column,
        raw=(
            "src/garden.ts"
            f"(1,{column}): error TS2345: Argument of type 'number' is not assignable "
            "to parameter of type 'string'."
        ),
        metadata={"confidence": "parser"},
    )
    writes: list[str] = []

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
        return {"ok": True}

    result = run_runtime_repair(
        source_tool=ts_syntax.TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL,
        workspace=tmp_path,
        base_files={relative_path: content},
        artifact_quality_errors=(),
        repair_diagnostics=(diagnostic,),
        writer=writer,
        editor=editor,
        allowed_paths=(relative_path,),
    )

    assert result.ok is True
    assert writes == []
    assert target.read_text(encoding="utf-8") == "const label = makeLabel(String(42), width);\n"


def test_runtime_dispatcher_generic_hygiene_uses_typed_scaffold_diagnostic_path() -> None:
    content = 'console.log("Polaris TypeScript scaffold");\n'
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="scaffold_marker",
        message="Scaffold marker remains in generated source.",
        path="src/main.ts",
        raw="Scaffold marker remains in generated source.",
        metadata={"stable_issue_id": "typed-generic-scaffold-marker"},
    )

    planning = plan_runtime_repair(
        source_tool=SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL,
        base_files={"src/main.ts": content},
        artifact_quality_errors=(),
        repair_diagnostics=(diagnostic,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.composition is not None
    assert planning.diagnostics[0].metadata["stable_issue_id"] == "typed-generic-scaffold-marker"
    assert planning.plan.diagnostics[0].metadata["stable_issue_id"] == "typed-generic-scaffold-marker"
    assert planning.composition.patches[0].content_after == 'console.log("TypeScript application");\n'


def test_runtime_dispatcher_generic_hygiene_runs_typed_scaffold_diagnostic_path(tmp_path: Path) -> None:
    relative_path = "src/main.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = 'console.log("Polaris TypeScript scaffold");\n'
    target.write_text(content, encoding="utf-8")
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="scaffold_marker",
        message="Scaffold marker remains in generated source.",
        path=relative_path,
        raw="Scaffold marker remains in generated source.",
        metadata={"stable_issue_id": "typed-generic-scaffold-marker-run"},
    )
    writes: list[str] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append(path)
        raise AssertionError("typed scaffold marker cleanup must prefer edit_file over write_file")

    def editor(operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert operation.span_start is not None
        assert operation.span_end is not None
        target.write_text(
            current[: operation.span_start] + str(operation.replacement or "") + current[operation.span_end :],
            encoding="utf-8",
        )
        return {"ok": True}

    result = run_runtime_repair(
        source_tool=SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL,
        workspace=tmp_path,
        base_files={relative_path: content},
        artifact_quality_errors=(),
        repair_diagnostics=(diagnostic,),
        writer=writer,
        editor=editor,
        allowed_paths=(relative_path,),
    )

    assert result.ok is True
    assert writes == []
    assert target.read_text(encoding="utf-8") == 'console.log("TypeScript application");\n'
    assert result.planning.diagnostics[0].metadata["stable_issue_id"] == "typed-generic-scaffold-marker-run"


def test_runtime_convergence_planner_uses_typed_generic_hygiene_diagnostic_path() -> None:
    content = 'console.log("Polaris TypeScript scaffold");\n'
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="scaffold_marker",
        message="Scaffold marker remains in generated source.",
        path="src/main.ts",
        raw="Scaffold marker remains in generated source.",
        metadata={"stable_issue_id": "typed-generic-scaffold-marker-convergence"},
    )

    planner = runtime_dispatch_module.build_runtime_repair_convergence_planner(
        source_tools=(SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL,),
        base_files={"src/main.ts": content},
        mode="shadow",
    )
    plans = planner((diagnostic,), 1)

    assert len(plans) == 1
    assert plans[0].source_tool == SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL
    assert plans[0].diagnostics[0].metadata["stable_issue_id"] == "typed-generic-scaffold-marker-convergence"
    assert plans[0].operations[0].path == "src/main.ts"


def test_single_runtime_entrypoints_preserve_typed_diagnostics_on_unsupported_tool(tmp_path: Path) -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts1005",
        message="',' expected.",
        path="src/models/Flight.ts",
        line=6,
        column=47,
        diagnostic_id="diag-typed-single-entrypoint",
        raw="src/models/Flight.ts(6,47): error TS1005: ',' expected.",
        metadata={"stable_issue_id": "typed-single-entrypoint"},
    )

    planning = plan_runtime_repair(
        source_tool="unsupported.future_rule",
        base_files={},
        artifact_quality_errors=(),
        repair_diagnostics=(diagnostic,),
    )

    assert planning.error_code == "unsupported_repair_source_tool"
    assert planning.diagnostics[0].diagnostic_id == "diag-typed-single-entrypoint"
    assert planning.diagnostics[0].line == 6
    assert planning.diagnostics[0].metadata["stable_issue_id"] == "typed-single-entrypoint"

    def writer(_: str, __: str) -> dict[str, object]:
        raise AssertionError("unsupported runtime source tool must not write")

    result = run_runtime_repair(
        source_tool="unsupported.future_rule",
        workspace=tmp_path,
        base_files={},
        artifact_quality_errors=(),
        repair_diagnostics=(diagnostic,),
        writer=writer,
    )

    assert result.ok is False
    assert result.error_code == "unsupported_repair_source_tool"
    assert result.planning.diagnostics[0].diagnostic_id == "diag-typed-single-entrypoint"
    assert result.planning.diagnostics[0].column == 47
    assert result.planning.diagnostics[0].metadata["stable_issue_id"] == "typed-single-entrypoint"


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


def test_runtime_dispatcher_typescript_escaped_newline_uses_typed_diagnostic_path() -> None:
    source_tool = ts_syntax.TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL
    content = (
        "import { AsyncLocalStorage } from 'async_hooks';\n\n"
        "// Context for tenant lifecycle\\n"
        "export const tenantContext = new AsyncLocalStorage<{ tenantId: string }>();\n"
    )
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_escaped_newline",
        message="Escaped newline remains in a TypeScript line comment.",
        path="src/middleware/auth.ts",
        raw="Escaped newline remains in a TypeScript line comment.",
        metadata={"stable_issue_id": "typed-typescript-escaped-newline"},
    )

    planning = plan_runtime_repair(
        source_tool=source_tool,
        base_files={"src/middleware/auth.ts": content},
        artifact_quality_errors=(),
        repair_diagnostics=(diagnostic,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.composition is not None
    assert planning.plan.diagnostics[0].metadata["stable_issue_id"] == "typed-typescript-escaped-newline"
    assert "lifecycle\\nexport const tenantContext" not in planning.composition.patches[0].content_after
    assert "\nexport const tenantContext" in planning.composition.patches[0].content_after


def test_typescript_escaped_newline_rule_uses_structured_issue_kind_metadata() -> None:
    source_tool = ts_syntax.TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL
    content = (
        "import { AsyncLocalStorage } from 'async_hooks';\n\n"
        "// Context for tenant lifecycle\\n"
        "export const tenantContext = new AsyncLocalStorage<{ tenantId: string }>();\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            {
                "source": "artifact_quality",
                "code": "typescript_syntax_red_flag",
                "message": "typed syntax red flag",
                "path": "src/middleware/auth.ts",
                "issue_kind": "escaped_newline",
                "raw": "typed issue metadata only",
            }
        ]
    )

    planning = plan_runtime_repair(
        source_tool=source_tool,
        base_files={"src/middleware/auth.ts": content},
        artifact_quality_errors=(),
        repair_diagnostics=diagnostics,
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.composition is not None
    assert planning.plan.diagnostics[0].metadata["issue_kind"] == "escaped_newline"
    assert "lifecycle\\nexport const tenantContext" not in planning.composition.patches[0].content_after
    assert "\nexport const tenantContext" in planning.composition.patches[0].content_after


def test_runtime_dispatcher_typescript_escaped_newline_runs_typed_diagnostic_path(tmp_path: Path) -> None:
    source_tool = ts_syntax.TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL
    relative_path = "src/middleware/auth.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = (
        "import { AsyncLocalStorage } from 'async_hooks';\n\n"
        "// Context for tenant lifecycle\\n"
        "export const tenantContext = new AsyncLocalStorage<{ tenantId: string }>();\n"
    )
    target.write_text(content, encoding="utf-8")
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_escaped_newline",
        message="Escaped newline remains in a TypeScript line comment.",
        path=relative_path,
        raw="Escaped newline remains in a TypeScript line comment.",
        metadata={"stable_issue_id": "typed-typescript-escaped-newline-run"},
    )
    writes: list[str] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append(path)
        raise AssertionError("escaped-newline repair must prefer edit_file over write_file")

    def editor(operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert operation.span_start is not None
        assert operation.span_end is not None
        target.write_text(
            current[: operation.span_start] + str(operation.replacement or "") + current[operation.span_end :],
            encoding="utf-8",
        )
        return {"ok": True}

    result = run_runtime_repair(
        source_tool=source_tool,
        workspace=tmp_path,
        base_files={relative_path: content},
        artifact_quality_errors=(),
        repair_diagnostics=(diagnostic,),
        writer=writer,
        editor=editor,
        allowed_paths=(relative_path,),
    )

    assert result.ok is True
    assert writes == []
    repaired = target.read_text(encoding="utf-8")
    assert "lifecycle\\nexport const tenantContext" not in repaired
    assert "\nexport const tenantContext" in repaired
    assert result.planning.diagnostics[0].metadata["stable_issue_id"] == "typed-typescript-escaped-newline-run"


def test_runtime_convergence_planner_uses_typed_typescript_escaped_newline_path() -> None:
    source_tool = ts_syntax.TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL
    content = (
        "import { AsyncLocalStorage } from 'async_hooks';\n\n"
        "// Context for tenant lifecycle\\n"
        "export const tenantContext = new AsyncLocalStorage<{ tenantId: string }>();\n"
    )
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_escaped_newline",
        message="Escaped newline remains in a TypeScript line comment.",
        path="src/middleware/auth.ts",
        raw="Escaped newline remains in a TypeScript line comment.",
        metadata={"stable_issue_id": "typed-typescript-escaped-newline-convergence"},
    )

    planner = runtime_dispatch_module.build_runtime_repair_convergence_planner(
        source_tools=(source_tool,),
        base_files={"src/middleware/auth.ts": content},
        mode="shadow",
    )
    plans = planner((diagnostic,), 1)

    assert len(plans) == 1
    assert plans[0].source_tool == source_tool
    assert plans[0].diagnostics[0].metadata["stable_issue_id"] == "typed-typescript-escaped-newline-convergence"
    assert plans[0].operations[0].path == "src/middleware/auth.ts"


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


def test_typescript_string_literal_suggestion_uses_typed_metadata_without_raw_message() -> None:
    content = (
        'export type MarketPhase = "pre-open" | "open" | "closed";\n'
        "export function initialPhase(): MarketPhase {\n"
        '  return "pre_open";\n'
        "}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            {
                "source": "artifact_quality",
                "code": "typescript_ts2820",
                "message": "typed string literal suggestion",
                "path": "src/models/Market.ts",
                "line": 3,
                "column": 5,
                "actual": '"pre_open"',
                "suggestion": '"pre-open"',
                "raw": "typed metadata only",
            }
        ]
    )

    plan = build_typescript_string_literal_suggestion_plan(
        base_files={"src/models/Market.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert len(plan.operations) == 1
    assert plan.operations[0].expected == '"pre_open"'
    assert plan.operations[0].replacement == '"pre-open"'


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
        assert operation.replacement is not None
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
        assert operation.replacement is not None
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
    assert item["runtime_blockers"][0]["metadata"]["failure_class"] == (
        TaskBoundaryFailureClassV1.INCOMPLETE_MATERIALIZATION.value
    )
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


def test_typescript_ts6133_multiline_unused_parameter_is_covered_plannable() -> None:
    source_tool = ts_syntax.TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL
    diagnostic = "src/models/Humidity.ts(7,3): error TS6133: 'flower' is declared but its value is never read."
    source = (
        "export interface Humidity { readonly value: number }\n"
        "function humidityMultiplierForFlower(humidity: Humidity): number {\n"
        "  return humidity.value > 0.7 ? 1 : 0.8;\n"
        "}\n"
        "export function humidityEffectOnFlower(\n"
        "  flower: { openness: number; health: number; species: string },\n"
        "  humidity: Humidity,\n"
        "): number {\n"
        "  const factor = humidityMultiplierForFlower(humidity);\n"
        "  return factor > 0.9 ? 0.02 : -0.01;\n"
        "}\n"
    )

    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=(source_tool,),
            artifact_quality_errors=(diagnostic,),
            base_files={"src/models/Humidity.ts": source},
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            artifact_quality_errors=(diagnostic,),
            base_files={"src/models/Humidity.ts": source},
            mode="shadow",
        )
    ).to_dict()

    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == (source_tool,)
    assert result.items[0].patch_count == 1
    assert planning["ok"] is True
    assert planning["planned"] is True
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "_flower: { openness: number; health: number; species: string }," in repaired
    assert "  flower: { openness" not in repaired
    assert "humidityMultiplierForFlower(humidity)" in repaired


