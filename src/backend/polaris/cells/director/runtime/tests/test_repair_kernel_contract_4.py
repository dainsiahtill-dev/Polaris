"""Tests for the Director Runtime Repair Kernel contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.director.runtime.internal.repair_kernel import (
    PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL,
    RepairDiagnostic,
    RepairOperation,
    RepairRuleDefinition,
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
    build_python_readme_required_token_plan,
    default_repair_rule_registry,
    normalize_artifact_quality_errors,
    plan_runtime_repair,
    repair_cpp_failing_smoke_translation_unit_text,
    repair_cpp_include_paths_text,
    repair_cpp_invalid_placeholder_declarations_text,
    repair_cpp_missing_private_members_text,
    repair_cpp_missing_standard_includes_text,
    repair_cpp_struct_getter_field_access_text,
    repair_go_bare_import_strings_text,
    repair_go_nested_import_keywords_text,
    run_runtime_repair,
    runtime_repair_bindings,
    runtime_repair_source_tools,
    typescript_syntax as ts_syntax,
)
from polaris.cells.director.runtime.internal.repair_kernel.contracts import (
    FILE_ABSENT_HASH,
    sha256_text,
)
from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairPlanProbeV1,
    RunDirectorRepairCommandV1,
    plan_director_repair,
    query_director_repair_coverage,
    query_director_repair_plan_probe,
    run_director_repair,
)


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
    assert "export type { FairyMood } from './types';" in repaired
    effect_plan = planning["effect_plan"]
    forward_effects = [effect for effect in effect_plan["effects"] if effect["contingency_kind"] == "forward"]
    assert len(forward_effects) == 1
    assert forward_effects[0]["tool_name"] == "write_file"
    assert forward_effects[0]["arguments"]["file"] == "src/models/Fairy.ts"
    assert "export type { FairyMood } from './types';" in forward_effects[0]["arguments"]["content"]

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


def test_typescript_reexported_type_binding_imports_sibling_type_from_same_barrel() -> None:
    """Live L1-08: export type { FlightReport } does not bind local names.

    QA residual was TS2304 SimulationStep/Meters in the same file. The file
    already points at one type barrel. Import the missing type-position name
    from that same module without requiring the symbol to appear in the
    export type list (plan_probe often lacks sibling file contents).
    """

    renderer = (
        'export type { FlightReport } from "../models/index.js";\n'
        "\n"
        "function computeViewport(report: FlightReport, width: number): number {\n"
        "  return report.peakAltitude * width;\n"
        "}\n"
        "\n"
        "function projectPosition(step: SimulationStep): number {\n"
        "  return step.altitude;\n"
        "}\n"
    )
    diagnostics = (
        "src/engine/renderer.ts(3,36): error TS2304: Cannot find name 'FlightReport'.",
        "src/engine/renderer.ts(7,32): error TS2304: Cannot find name 'SimulationStep'.",
    )
    base_files = {"src/engine/renderer.ts": renderer}

    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            source_tools=(ts_syntax.TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,),
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert probe.status == "covered_plannable"
    assert probe.plannable_source_tools == (ts_syntax.TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,)
    assert planning["ok"] is True
    assert planning["planned"] is True
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert 'import type { FlightReport } from "../models/index.js";' in after
    assert 'import type { SimulationStep } from "../models/index.js";' in after
    assert "renderFlightReport" not in after


def test_typescript_private_property_access_unprivates_existing_field() -> None:
    """Live L1-08: web.ts reads FlightController.trajectory / windSpeedMs (TS2341)."""

    consumer = (
        "import { FlightController } from './engine/simulation.js';\n"
        "export function paint(controller: FlightController): number {\n"
        "  return controller.trajectory.length + controller.windSpeedMs;\n"
        "}\n"
    )
    owner = (
        "export class FlightController {\n"
        "  private readonly windSpeedMs: number;\n"
        "  private trajectory: number[] = [];\n"
        "  public constructor() {\n"
        "    this.windSpeedMs = 0;\n"
        "  }\n"
        "}\n"
    )
    diagnostics = (
        "src/web.ts(3,21): error TS2341: Property 'trajectory' is private "
        "and only accessible within class 'FlightController'.",
        "src/web.ts(3,51): error TS2341: Property 'windSpeedMs' is private "
        "and only accessible within class 'FlightController'.",
    )
    base_files = {
        "src/web.ts": consumer,
        "src/engine/simulation.ts": owner,
    }
    source_tool = ts_syntax.TYPESCRIPT_PRIVATE_PROPERTY_ACCESS_SOURCE_TOOL
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            source_tools=(source_tool,),
        )
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert probe.status == "covered_plannable"
    assert source_tool in probe.plannable_source_tools
    assert planning["ok"] is True
    assert planning["planned"] is True
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "private readonly windSpeedMs" not in after
    assert "private trajectory" not in after
    assert "readonly windSpeedMs" in after
    assert "trajectory: number[]" in after


def test_typescript_private_property_unprivates_ts2345_assignability() -> None:
    """Live L1-08: new FlightController(config) TS2345 when launchSpeedMs is private."""

    consumer = (
        "import { FlightController } from './engine/simulation.js';\n"
        "export function start(config: FlightController): FlightController {\n"
        "  return new FlightController(config);\n"
        "}\n"
    )
    owner = (
        "export class FlightController {\n"
        "  public readonly plane = {};\n"
        "  private readonly launchSpeedMs: number;\n"
        "  public constructor(_config: { readonly launchSpeedMs: number }) {\n"
        "    this.launchSpeedMs = _config.launchSpeedMs;\n"
        "  }\n"
        "}\n"
    )
    diagnostic = (
        "src/web.ts(3,29): error TS2345: Argument of type 'FlightController' is not assignable "
        "to parameter of type '{ readonly launchSpeedMs: number; }'.\n"
        "  Property 'launchSpeedMs' is private in type 'FlightController' but not in type "
        "'{ readonly launchSpeedMs: number; }'."
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_private_property_access_repair",
            base_files={"src/web.ts": consumer, "src/engine/simulation.ts": owner},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    assert planning["planned"] is True
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "private readonly launchSpeedMs" not in after
    assert "readonly launchSpeedMs" in after


def test_typescript_object_literal_class_return_uses_constructor_parameters() -> None:
    """Live L1-08: buildConfig(): FlightController { return { plane, wind } } is TS2740."""

    consumer = (
        "import { FlightController } from './engine/simulation.js';\n"
        "function buildConfig(): FlightController {\n"
        "  return { plane: {}, wind: {}, launchAngle: {}, launchSpeedMs: 1 };\n"
        "}\n"
    )
    owner = "export class FlightController {\n  public constructor(_config: { launchSpeedMs: number }) {}\n}\n"
    diagnostic = (
        "src/web.ts(3,3): error TS2740: Type '{ plane: {}; wind: {}; launchAngle: {}; launchSpeedMs: number; }' "
        "is missing the following properties from type 'FlightController': scenario, maxSteps, windSpeedMs, position"
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_object_literal_missing_props_repair",
            base_files={"src/web.ts": consumer, "src/engine/simulation.ts": owner},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    assert planning["ok"] is True
    assert planning["planned"] is True
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "function buildConfig(): ConstructorParameters<typeof FlightController>[0] {" in after
    assert "scenario:" not in after


def test_typescript_argument_shape_uses_build_scenario_and_phase_getter() -> None:
    """Live L1-08: SimulationConfig passed to FlightController; finalPhase object TS2322."""

    consumer = (
        "import { createAngle, createPlane, createWind } from './models/index.js';\n"
        "import { FlightController, SimulationConfig } from './engine/simulation.js';\n"
        "function start(config: SimulationConfig, controller: FlightController): void {\n"
        "  const next = new FlightController(config);\n"
        "  controller.reconfigure({ plane: next.plane, wind: next.wind, launchAngle: next.launchAngle, launchSpeedMs: next.launchSpeedMs });\n"
        "  render({ finalPhase: { kind: controller['phaseKind' as keyof FlightController] as unknown as string } });\n"
        "  void next;\n"
        "}\n"
        "function render(_scene: { finalPhase: import('./models/index.js').FlightPhase }): void {}\n"
    )
    owner = (
        "export interface SimulationConfig { readonly launchSpeedMs: number; readonly maxSteps: number }\n"
        "export function buildScenario(config: SimulationConfig, _deps: object): "
        "{ plane: object; wind: object; launch: { angle: object } } { return config as never; }\n"
        "export class FlightController {\n"
        "  public constructor(_config: { readonly plane: object; readonly wind: object; "
        "readonly launchAngle: object; readonly launchSpeedMs: number }) {}\n"
        "  public get currentLastPhase(): import('./models/index.js').FlightPhase { return { kind: 'cruise' } as never; }\n"
        "}\n"
    )
    diagnostics = (
        "src/web.ts(4,36): error TS2345: Argument of type 'SimulationConfig' is not assignable "
        "to parameter of type '{ readonly plane: object; readonly wind: object; readonly launchAngle: object; "
        "readonly launchSpeedMs: number; }'.\n"
        "  Type 'SimulationConfig' is missing the following properties from type "
        "'{ readonly plane: object; readonly wind: object; readonly launchAngle: object; "
        "readonly launchSpeedMs: number; }': plane, wind, launchAngle",
        "src/web.ts(5,19): error TS2322: Type 'string' is not assignable to type '\"climb\" | \"cruise\" | \"descent\" | \"landed\"'.",
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_argument_shape_adapter_repair",
            base_files={"src/web.ts": consumer, "src/engine/simulation.ts": owner},
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()
    assert planning["planned"] is True
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "buildScenario" in after
    assert "finalPhase: controller.currentLastPhase" in after
    assert "as unknown as string" not in after


def test_typescript_nonfinite_altitude_guard_lands_step_flight() -> None:
    """Live L1-08: InvalidWindError from Infinity altitude; Wind swallow still exit 2."""

    flight = (
        "import { FlightPhaseKind } from './types.js';\n"
        "import { effectiveWindSpeed } from './Wind.js';\n"
        "export function stepFlight(\n"
        "  scenario: { wind: { speedMs: number } },\n"
        "  position: { x: number; y: number },\n"
        "  velocity: { x: number; y: number },\n"
        "  dt: number,\n"
        "): { position: { x: number; y: number }; velocity: { x: number; y: number }; "
        "phase: { kind: string } } {\n"
        "  const airspeed = Math.hypot(velocity.x, velocity.y);\n"
        "  const wind = effectiveWindSpeed(scenario.wind, position.y);\n"
        "  void airspeed;\n"
        "  void wind;\n"
        "  void dt;\n"
        "  return { position, velocity, phase: { kind: FlightPhaseKind.Climb } };\n"
        "}\n"
    )
    diagnostic = (
        "InvalidWindError: altitude must be non-negative finite meters, got NaN\n"
        "    at effectiveWindSpeed (src/models/Wind.ts:63:11)\n"
        "    at stepFlight (src/models/Flight.ts:118:16)\n"
        "    at simulateFlight (src/models/Flight.ts:179:18)"
    )
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_nonfinite_altitude_guard_repair",
            base_files={"src/models/Flight.ts": flight, "src/models/Wind.ts": "export function effectiveWindSpeed() {}"},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()
    assert planning["planned"] is True
    after = planning["composition_summary"]["patches"][0]["content_after"]
    assert "Number.isFinite(position.y)" in after
    assert "FlightPhaseKind.Landed" in after
    assert "const airspeed = Math.hypot(velocity.x, velocity.y);" in after
    assert after.index("Number.isFinite(position.y)") < after.index("const airspeed")


def test_typescript_unresolved_identifier_does_not_alias_type_position_to_local_function() -> None:
    """Live L1-08: unresolved planner rewrote FlightReport → renderFlightReport."""

    renderer = (
        'export type { FlightReport } from "../models/index.js";\n'
        "\n"
        "export function renderFlightReport(): void {}\n"
        "\n"
        "function computeViewport(report: FlightReport, width: number): number {\n"
        "  return width;\n"
        "}\n"
    )
    diagnostic = "src/engine/renderer.ts(5,36): error TS2304: Cannot find name 'FlightReport'."
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=ts_syntax.TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL,
            base_files={"src/engine/renderer.ts": renderer},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    if planning["planned"]:
        after = planning["composition_summary"]["patches"][0]["content_after"]
        assert "report: renderFlightReport" not in after
        assert "report: FlightReport" in after
        assert 'import type { FlightReport } from "../models/index.js";' in after
    else:
        assert planning["planned"] is False


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
    # TS6133 alone cannot distinguish a local from a parameter without the
    # source line. Coverage therefore reports both safe candidates; the
    # source-aware planner below must select the parameter repair.
    assert coverage_payload["items"][0]["matched_rule_ids"] == [
        "typescript.unused_local",
        "typescript.unused_parameter",
    ]
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


def test_public_typescript_tsconfig_repair_removes_removed_compiler_option() -> None:
    source_tool = "deterministic_typescript_tsconfig_lib_repair"

    planning_result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            base_files={
                "tsconfig.json": (
                    '{"compilerOptions":{"target":"ES2020","module":"ES2020",'
                    '"moduleResolution":"node","charset":"utf8","strict":true}}\n'
                )
            },
            artifact_quality_issues=(
                {
                    "source": "typescript_tsconfig_scanner",
                    "code": "tsconfig_removed_compiler_option",
                    "message": (
                        "Artifact quality scan failed: tsconfig compilerOptions.charset "
                        "is removed by TypeScript 5 (TS5102); remove it from tsconfig.json"
                    ),
                    "severity": "error",
                    "path": "tsconfig.json",
                    "metadata": {
                        "raw": (
                            "Artifact quality scan failed: tsconfig compilerOptions.charset "
                            "is removed by TypeScript 5 (TS5102); remove it from tsconfig.json"
                        ),
                        "diagnostic_kind": "tsconfig_removed_compiler_option",
                        "diagnostic_code": "TS5102",
                        "compiler_option": "charset",
                        "json_path": ("compilerOptions", "charset"),
                    },
                },
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
    assert '"charset"' not in content_after
    assert '"strict": true' in content_after
    assert '"moduleResolution": "node"' in content_after


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


def test_go_error_string_helper_rule_uses_typed_identifier_metadata() -> None:
    relative_path = "models/gallery.go"
    content = 'package models\n\nvar (\n    ErrDuplicateCapsule = errString("capsule id already exists")\n)\n'
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="go_compile_error",
        message="typed metadata only",
        path=relative_path,
        raw="typed metadata only",
        metadata={
            "language": "go",
            "diagnostic_kind": "undefined_identifier",
            "identifier": "errString",
        },
    )

    plan = build_go_error_string_helper_plan(
        base_files={relative_path: content},
        diagnostics=(diagnostic,),
        mode="shadow",
    )

    assert plan is not None
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.path == relative_path
    assert operation.metadata["identifier"] == "errString"
    assert operation.replacement is not None
    assert "type errString string" in operation.replacement


