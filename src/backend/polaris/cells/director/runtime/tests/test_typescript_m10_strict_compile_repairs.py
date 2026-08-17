"""M10/R167: strict-compile residual repairs after materialization settle lands smoke."""

from __future__ import annotations

import re

from polaris.cells.director.runtime.internal.repair_kernel.typescript_syntax import (
    TYPESCRIPT_IMPLICIT_RETURN_TYPE_SOURCE_TOOL,
    TYPESCRIPT_OBJECT_ASSIGN_ASSERTION_SOURCE_TOOL,
    TYPESCRIPT_PARAM_OBJECT_PROPERTY_SOURCE_TOOL,
    TYPESCRIPT_READONLY_ARRAY_MUTATION_SOURCE_TOOL,
    TYPESCRIPT_TRUNCATED_EOF_SOURCE_TOOL,
    build_typescript_implicit_return_type_plan,
    build_typescript_object_assign_assertion_plan,
    build_typescript_param_object_property_plan,
    build_typescript_readonly_array_mutation_plan,
    build_typescript_truncated_eof_plan,
)
from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairCoverageV1,
)
from polaris.cells.director.runtime.public.service import (
    plan_director_repair,
    query_director_repair_coverage,
)


def test_truncated_eof_closes_mid_signature_method() -> None:
    base = {
        "src/models/Flower.ts": "\n".join(
            [
                "export class Flower {",
                "  private raw = { nectar: 1 };",
                "  /**",
                "   * Allow a firefly to consume nectar.",
                "   */",
                "  public consume(am",
            ]
        )
    }
    plan = build_typescript_truncated_eof_plan(
        base_files=base,
        diagnostics=_diags(
            [
                "src/models/Flower.ts(6,20): error TS1005: ')' expected.",
            ]
        ),
        mode="commit",
    )
    assert plan is not None
    assert plan.source_tool == TYPESCRIPT_TRUNCATED_EOF_SOURCE_TOOL
    content = str(plan.operations[0].content or "")
    assert "consume(" in content
    assert content.count("{") == content.count("}")
    assert content.rstrip().endswith("}")


def test_ts7010_implicit_return_type_adds_void_on_interface_methods() -> None:
    base = {
        "src/engine/renderer.ts": "\n".join(
            [
                "export interface Canvas2DLike {",
                "  clearRect(x: number, y: number, width: number, height: number);",
                "  beginPath();",
                "  save(): void;",
                "}",
                "",
            ]
        )
    }
    diag = (
        "src/engine/renderer.ts(2,3): error TS7010: 'clearRect', which lacks return-type annotation, "
        "implicitly has an 'any' return type."
    )
    plan = build_typescript_implicit_return_type_plan(
        base_files=base,
        diagnostics=_diags([diag]),
        mode="commit",
    )
    assert plan is not None
    assert plan.source_tool == TYPESCRIPT_IMPLICIT_RETURN_TYPE_SOURCE_TOOL
    assert any(": void;" in str(op.replacement or "") for op in plan.operations)


def test_ts2322_object_assign_assertion_wraps_object_freeze() -> None:
    base = {
        "src/engine/simulation.ts": "\n".join(
            [
                "export type FireflyColor = 'green' | 'amber';",
                "export interface SimulationConfig {",
                "  readonly initialFireflies: ReadonlyArray<{ readonly color: FireflyColor }>;",
                "}",
                "export const DEFAULT_SIMULATION_CONFIG: SimulationConfig = Object.freeze({",
                "  initialFireflies: [{ color: 'green' }],",
                "});",
                "",
            ]
        )
    }
    diag = (
        "src/engine/simulation.ts(5,14): error TS2322: Type 'Readonly<{ initialFireflies: "
        "{ color: string; }[]; }>' is not assignable to type 'SimulationConfig'."
    )
    plan = build_typescript_object_assign_assertion_plan(
        base_files=base,
        diagnostics=_diags([diag]),
        mode="commit",
    )
    assert plan is not None
    assert plan.source_tool == TYPESCRIPT_OBJECT_ASSIGN_ASSERTION_SOURCE_TOOL
    assert any("as SimulationConfig" in str(op.replacement or "") for op in plan.operations)


def test_ts2339_readonly_array_push_retypes_binding_once() -> None:
    base = {
        "src/main.ts": "\n".join(
            [
                "type GardenEvent = { kind: string };",
                "type GardenState = { events: readonly GardenEvent[] };",
                "function run(): GardenState {",
                "  const events: GardenState['events'] = [];",
                "  events.push({ kind: 'x' });",
                "  events.push({ kind: 'y' });",
                "  return { events };",
                "}",
                "",
            ]
        )
    }
    diags = [
        "src/main.ts(5,10): error TS2339: Property 'push' does not exist on type 'readonly GardenEvent[]'.",
        "src/main.ts(6,10): error TS2339: Property 'push' does not exist on type 'readonly GardenEvent[]'.",
    ]
    plan = build_typescript_readonly_array_mutation_plan(
        base_files=base,
        diagnostics=_diags(diags),
        mode="commit",
    )
    assert plan is not None
    assert plan.source_tool == TYPESCRIPT_READONLY_ARRAY_MUTATION_SOURCE_TOOL
    assert len(plan.operations) == 1
    assert "Array<" in str(plan.operations[0].replacement or "")


def test_ts2339_param_object_property_retypes_and_imports() -> None:
    base = {
        "src/models/Humidity.ts": "\n".join(
            [
                "export interface Humidity { readonly percent: number; }",
                "export function createHumidity(percent: number): Humidity { return { percent }; }",
                "",
            ]
        ),
        "src/main.ts": "\n".join(
            [
                "import { createHumidity } from './models/Humidity';",
                "function build(humidityPercent: number): number {",
                "  return humidityPercent.percent;",
                "}",
                "",
            ]
        ),
    }
    diag = "src/main.ts(3,10): error TS2339: Property 'percent' does not exist on type 'number'."
    plan = build_typescript_param_object_property_plan(
        base_files=base,
        diagnostics=_diags([diag]),
        mode="commit",
    )
    assert plan is not None
    assert plan.source_tool == TYPESCRIPT_PARAM_OBJECT_PROPERTY_SOURCE_TOOL
    joined = "\n".join(str(op.replacement or "") for op in plan.operations)
    assert "Humidity" in joined


def test_ts2554_too_many_args_trims_callsite_and_composes() -> None:
    base = {
        "src/engine/renderer.ts": "\n".join(
            [
                "function paintFlowers(",
                "  ctx: CanvasRenderingContext2D,",
                "  garden: GardenView,",
                "  t: ViewportTransform,",
                "): void {}",
                "function paintFireflies(",
                "  ctx: CanvasRenderingContext2D,",
                "  garden: GardenView,",
                "  t: ViewportTransform,",
                "): void {}",
                "export function render(ctx: CanvasRenderingContext2D, surface: RenderSurface, garden: GardenView, t: ViewportTransform): void {",
                "  paintFlowers(ctx, surface, garden, t);",
                "  paintFireflies(ctx, surface, garden, t);",
                "}",
                "",
            ]
        )
    }
    diags = [
        "src/engine/renderer.ts(12,38): error TS2554: Expected 3 arguments, but got 4.",
        "src/engine/renderer.ts(13,40): error TS2554: Expected 3 arguments, but got 4.",
    ]
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_too_few_arguments_repair",
            base_files=base,
            artifact_quality_errors=tuple(diags),
            mode="commit",
        )
    )
    assert result.ok is True
    assert result.planned is True
    after = ""
    for patch in (result.to_dict().get("composition_summary") or {}).get("patches") or []:
        if patch.get("path") == "src/engine/renderer.ts":
            after = str(patch.get("content_after") or "")
    assert "paintFlowers(ctx, garden, t)" in after
    assert "paintFireflies(ctx, garden, t)" in after
    assert "paintFlowers(ctx, surface, garden, t)" not in after


def test_ts2304_moonphase_star_reexport_import_added() -> None:
    base = {
        "src/models/types.ts": "export enum MoonPhase { NewMoon = 'new' }\n",
        "src/models/MoonPhase.ts": (
            "import { MoonPhase } from './types';\n"
            "export class MoonPhaseCycle {\n"
            "  current(): MoonPhase { return MoonPhase.NewMoon; }\n"
            "}\n"
        ),
        "src/models/index.ts": ("export * from './types';\nexport { MoonPhaseCycle } from './MoonPhase';\n"),
        "src/web.ts": (
            "import {\n  MoonPhaseCycle,\n} from './models/index';\nexport { MoonPhase, MoonPhaseCycle };\n"
        ),
    }
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_unresolved_identifier_repair",
            base_files=base,
            artifact_quality_errors=("src/web.ts(5,10): error TS2304: Cannot find name 'MoonPhase'.",),
            mode="commit",
        )
    )
    assert result.ok is True
    assert result.planned is True
    after = ""
    for patch in (result.to_dict().get("composition_summary") or {}).get("patches") or []:
        if patch.get("path") == "src/web.ts":
            after = str(patch.get("content_after") or "")
    assert "MoonPhase" in after
    assert re.search(r"import\s*\{[^}]*MoonPhase[^}]*\}\s*from", after, re.S)


def test_ts2345_compiler_host_missing_props_injected() -> None:
    base = {
        "src/verify.ts": "\n".join(
            [
                "import * as ts from 'typescript';",
                "export function parse(absPath: string, contents: string): void {",
                "  const sourceFile = ts.createSourceFile(absPath, contents, ts.ScriptTarget.ES2022, true);",
                "  const program = ts.createProgram([absPath], { noEmit: true }, {",
                "    getSourceFile: (fileName) => fileName === absPath ? sourceFile : undefined,",
                "    writeFile: () => undefined,",
                "    getDefaultLibFileName: () => 'lib.d.ts',",
                "    useCaseSensitiveFileNames: () => true,",
                "    fileExists: ts.sys.fileExists,",
                "    readFile: ts.sys.readFile,",
                "    readDirectory: ts.sys.readDirectory,",
                "  });",
                "  void program;",
                "}",
                "",
            ]
        )
    }
    diag = (
        "src/verify.ts(4,66): error TS2345: Argument of type '{ getSourceFile: "
        "(fileName: any) => any; writeFile: () => undefined; ... }' is not assignable "
        "to parameter of type 'CompilerHost'.\n"
        "  Type '{ getSourceFile: (fileName: any) => any; writeFile: () => undefined; "
        "... }' is missing the following properties from type 'CompilerHost': "
        "getCurrentDirectory, getCanonicalFileName, getNewLine"
    )
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_object_literal_missing_props_repair",
            base_files=base,
            artifact_quality_errors=(diag,),
            mode="commit",
        )
    )
    assert result.ok is True
    assert result.planned is True
    after = ""
    for patch in (result.to_dict().get("composition_summary") or {}).get("patches") or []:
        if patch.get("path") == "src/verify.ts":
            after = str(patch.get("content_after") or "")
    assert "getCurrentDirectory" in after
    assert "getCanonicalFileName" in after
    assert "getNewLine" in after
    # Must inject into the host object, not the short { noEmit: true } bag.
    assert "noEmit: true }, {" in after or "noEmit: true}," in after.replace(" ", "")
    assert re.search(
        r"readDirectory:\s*ts\.sys\.readDirectory,?\s*\n\s*getCurrentDirectory:",
        after,
    )


def test_ts2552_identifier_suggestion_renames_in_scope_local() -> None:
    base = {
        "src/web.ts": "\n".join(
            [
                "interface CanvasContext {",
                "  readonly canvas: HTMLCanvasElement;",
                "  readonly context: CanvasRenderingContext2D;",
                "}",
                "function acquireCanvas(documentRef: Document, id: string): CanvasContext | null {",
                "  const canvas = documentRef.getElementById(id);",
                "  if (!(canvas instanceof HTMLCanvasElement)) {",
                "    return null;",
                "  }",
                "  const context = canvas.getContext('2d');",
                "  if (_context === null) {",
                "    return null;",
                "  }",
                "  return { canvas, context };",
                "}",
                "",
            ]
        )
    }
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_identifier_suggestion_repair",
            base_files=base,
            artifact_quality_errors=(
                "src/web.ts(11,7): error TS2552: Cannot find name '_context'. Did you mean 'context'?",
            ),
            mode="commit",
        )
    )
    assert result.ok is True
    assert result.planned is True
    after = ""
    for patch in (result.to_dict().get("composition_summary") or {}).get("patches") or []:
        if patch.get("path") == "src/web.ts":
            after = str(patch.get("content_after") or "")
    assert "if (context === null)" in after
    assert "if (_context === null)" not in after


def test_ts6133_unused_local_prefixes_destructured_binding() -> None:
    base = {
        "src/web.ts": (
            "export function boot(acquired: { canvas: HTMLCanvasElement; context: CanvasRenderingContext2D }): number {\n"
            "  const { canvas, context } = acquired;\n"
            "  return canvas.width;\n"
            "}\n"
        )
    }
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_unused_local_repair",
            base_files=base,
            artifact_quality_errors=(
                "src/web.ts(2,19): error TS6133: 'context' is declared but its value is never read.",
            ),
            mode="commit",
        )
    )
    assert result.ok is True
    assert result.planned is True
    after = ""
    for patch in (result.to_dict().get("composition_summary") or {}).get("patches") or []:
        if patch.get("path") == "src/web.ts":
            after = str(patch.get("content_after") or "")
    assert "context: _context" in after


def test_ts2345_argument_shape_adapter_maps_intensity_to_glow() -> None:
    base = {
        "src/models/types.ts": (
            "export interface FireflyFlashEvent {\n"
            "  readonly id: string;\n"
            "  readonly at: number;\n"
            "  readonly intensity: number;\n"
            "}\n"
        ),
        "src/web.ts": "\n".join(
            [
                "import type { FireflyFlashEvent } from './models/types';",
                "function renderFirefly(state: { glow: number; phase: number }): void {",
                "  void state.glow;",
                "  void state.phase;",
                "}",
                "export function paint(flash: { ok: true; value: FireflyFlashEvent }): void {",
                "  renderFirefly(flash.value);",
                "}",
                "",
            ]
        ),
    }
    diag = (
        "src/web.ts(7,17): error TS2345: Argument of type 'FireflyFlashEvent' is not assignable "
        "to parameter of type '{ glow: number; phase: number; }'. "
        "Type 'FireflyFlashEvent' is missing the following properties from type "
        "'{ glow: number; phase: number; }': glow, phase"
    )
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_argument_shape_adapter_repair",
            base_files=base,
            artifact_quality_errors=(diag,),
            mode="commit",
        )
    )
    assert result.ok is True
    assert result.planned is True
    after = ""
    for patch in (result.to_dict().get("composition_summary") or {}).get("patches") or []:
        if patch.get("path") == "src/web.ts":
            after = str(patch.get("content_after") or "")
    assert "glow:" in after
    assert "phase:" in after
    assert "intensity" in after
    assert "renderFirefly(flash.value)" not in after


def test_json_as_source_does_not_invent_smoke_for_build_only_test_script() -> None:
    base = {
        "package.json": (
            "{\n"
            '  "name": "demo",\n'
            '  "scripts": {"test": "npm run build", "build": "tsc -p tsconfig.json"},\n'
            '  "devDependencies": {"typescript": "^5.4.0"}\n'
            "}\n"
        ),
        "src/main.ts": "export function main(): void {}\n",
        "tsconfig.json": '{ "compilerOptions": { "strict": true }, "include": ["src"] }\n',
    }
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_json_as_source_repair",
            base_files=base,
            artifact_quality_errors=(),
            mode="commit",
        )
    )
    assert result.ok is False
    assert result.planned is False


def test_public_plan_and_coverage_match_new_m10_rules() -> None:
    diags = [
        "src/a.ts(1,1): error TS7010: 'clearRect', which lacks return-type annotation, implicitly has an 'any' return type.",
        "src/b.ts(1,1): error TS2322: Type 'Readonly<{ x: string }>' is not assignable to type 'SimulationConfig'.",
        "src/c.ts(1,1): error TS2339: Property 'push' does not exist on type 'readonly GardenEvent[]'.",
        "src/d.ts(1,1): error TS2339: Property 'percent' does not exist on type 'number'.",
    ]
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=tuple(diags)))
    payload = coverage.to_dict() if hasattr(coverage, "to_dict") else coverage
    items = payload.get("items") or []
    matched_tools: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for tool in item.get("matched_source_tools") or ():
            matched_tools.add(str(tool))
    assert TYPESCRIPT_IMPLICIT_RETURN_TYPE_SOURCE_TOOL in matched_tools
    # smoke public plan accepts the new source_tool
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=TYPESCRIPT_IMPLICIT_RETURN_TYPE_SOURCE_TOOL,
            base_files={
                "src/a.ts": ("export interface X {\n  clearRect(x: number);\n}\n"),
            },
            artifact_quality_errors=(
                "src/a.ts(2,3): error TS7010: 'clearRect', which lacks return-type annotation, "
                "implicitly has an 'any' return type.",
            ),
            mode="commit",
        )
    )
    assert result.ok is True
    assert result.planned is True


def test_ts2540_readonly_assignment_strips_class_field_across_files() -> None:
    """R175/M10: assignment in main.ts, readonly field on class in another file."""

    base = {
        "src/main.ts": (
            "import { createFirefly } from './models/Firefly';\n"
            "export function boot(): void {\n"
            "  const fireflies = [createFirefly({ id: 'a', sex: 0 as never })];\n"
            "  fireflies[0]!.sex = 'Male' as never;\n"
            "}\n"
        ),
        "src/models/Firefly.ts": (
            "export class Firefly {\n"
            "  readonly id: string;\n"
            "  readonly sex: string;\n"
            "  constructor(config: { id: string; sex: string }) {\n"
            "    this.id = config.id;\n"
            "    this.sex = config.sex;\n"
            "  }\n"
            "}\n"
            "export function createFirefly(config: { id: string; sex: string }): Firefly {\n"
            "  return new Firefly(config);\n"
            "}\n"
        ),
        "src/models/types.ts": (
            "export interface FireflyState {\n  readonly id: string;\n  readonly sex: string;\n}\n"
        ),
    }
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_readonly_assignment_repair",
            base_files=base,
            artifact_quality_errors=(
                "src/main.ts(4,17): error TS2540: Cannot assign to 'sex' because it is a read-only property.",
            ),
            mode="commit",
        )
    )
    assert result.ok is True
    assert result.planned is True
    after = ""
    for patch in (result.to_dict().get("composition_summary") or {}).get("patches") or []:
        if patch.get("path") == "src/models/Firefly.ts":
            after = str(patch.get("content_after") or "")
    assert "  sex: string;" in after
    assert "  readonly sex: string;" not in after


def test_ts2304_unresolved_unwraps_phantom_local_call() -> None:
    """R175/M10: undefined deltaMult(expr) unwraps to expr."""

    base = {
        "src/models/Flower.ts": (
            "export class Flower {\n"
            "  bloom = 1;\n"
            "  tick(deltaSeconds: number, humMult: number): number {\n"
            "    this.bloom = this.bloom - 0.02 * deltaMult(decayAdjusted(deltaSeconds, humMult));\n"
            "    return this.bloom;\n"
            "  }\n"
            "}\n"
            "function decayAdjusted(deltaSeconds: number, humMult: number): number {\n"
            "  return deltaSeconds * humMult;\n"
            "}\n"
            "function _decayMult(deltaSeconds: number, humMult: number): number {\n"
            "  return deltaSeconds * humMult;\n"
            "}\n"
        )
    }
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_unresolved_identifier_repair",
            base_files=base,
            artifact_quality_errors=("src/models/Flower.ts(4,42): error TS2304: Cannot find name 'deltaMult'.",),
            mode="commit",
        )
    )
    assert result.ok is True
    assert result.planned is True
    after = ""
    for patch in (result.to_dict().get("composition_summary") or {}).get("patches") or []:
        if patch.get("path") == "src/models/Flower.ts":
            after = str(patch.get("content_after") or "")
    assert "deltaMult(" not in after
    assert "decayAdjusted(deltaSeconds, humMult)" in after


def test_ts6133_deletes_already_underscored_unused_function() -> None:
    """R175/M10: unused `_decayMult` helper is deleted, not re-prefixed."""

    base = {
        "src/models/Flower.ts": (
            "export function createFlower(): number {\n"
            "  return 1;\n"
            "}\n"
            "function _decayMult(deltaSeconds: number, humMult: number): number {\n"
            "  return deltaSeconds * humMult;\n"
            "}\n"
        )
    }
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_unused_local_repair",
            base_files=base,
            artifact_quality_errors=(
                "src/models/Flower.ts(4,10): error TS6133: '_decayMult' is declared but its value is never read.",
            ),
            mode="commit",
        )
    )
    assert result.ok is True
    assert result.planned is True
    after = ""
    for patch in (result.to_dict().get("composition_summary") or {}).get("patches") or []:
        if patch.get("path") == "src/models/Flower.ts":
            after = str(patch.get("content_after") or "")
    assert "_decayMult" not in after
    assert "createFlower" in after


def test_ts6133_unused_import_binding_is_removed_not_underscored() -> None:
    """R176/M10: unused type import must be removed, never rewritten to `_Name`."""

    base = {
        "src/models/types.ts": (
            "export type HumidityBand = 'arid' | 'humid';\n"
            "export type HumidityState = { readonly band: HumidityBand };\n"
            "export type MoonState = { readonly illumination: number };\n"
            "export type Rng = () => number;\n"
            "export const HUMIDITY_MIN = 0;\n"
            "export const HUMIDITY_MAX = 100;\n"
            "export const NIGHT_TICK_START = 16;\n"
            "export const NIGHT_TICK_END = 28;\n"
        ),
        "src/models/Firefly.ts": (
            "import {\n"
            "  HUMIDITY_MAX,\n"
            "  HUMIDITY_MIN,\n"
            "  NIGHT_TICK_END,\n"
            "  NIGHT_TICK_START,\n"
            "  type HumidityBand,\n"
            "  type HumidityState,\n"
            "  type MoonState,\n"
            "  type Rng,\n"
            "} from './types';\n"
            "export function glow(state: HumidityState, moon: MoonState, rng: Rng): number {\n"
            "  return (state.band === 'humid' ? 1 : 0) + moon.illumination + rng() + HUMIDITY_MIN + HUMIDITY_MAX + NIGHT_TICK_START + NIGHT_TICK_END;\n"
            "}\n"
        ),
    }
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_unused_local_repair",
            base_files=base,
            artifact_quality_errors=(
                "src/models/Firefly.ts(6,8): error TS6133: 'HumidityBand' is declared but its value is never read.",
            ),
            mode="commit",
        )
    )
    assert result.ok is True
    assert result.planned is True
    after = ""
    for patch in (result.to_dict().get("composition_summary") or {}).get("patches") or []:
        if patch.get("path") == "src/models/Firefly.ts":
            after = str(patch.get("content_after") or "")
    assert "HumidityBand" not in after
    assert "_HumidityBand" not in after
    assert "type HumidityState" in after
    assert "HUMIDITY_MAX" in after


def test_ts2724_phantom_underscore_import_is_removed() -> None:
    """R176/M10: TS2724 `_HumidityBand` with Did-you-mean HumidityBand removes import."""

    base = {
        "src/models/types.ts": (
            "export type HumidityBand = 'arid' | 'humid';\n"
            "export type HumidityState = { readonly band: HumidityBand };\n"
            "export type MoonState = { readonly illumination: number };\n"
            "export type Rng = () => number;\n"
            "export const HUMIDITY_MIN = 0;\n"
            "export const HUMIDITY_MAX = 100;\n"
        ),
        "src/models/Firefly.ts": (
            "import {\n"
            "  HUMIDITY_MAX,\n"
            "  HUMIDITY_MIN,\n"
            "  type _HumidityBand,\n"
            "  type HumidityState,\n"
            "  type MoonState,\n"
            "  type Rng,\n"
            "} from './types';\n"
            "export function glow(state: HumidityState, moon: MoonState, rng: Rng): number {\n"
            "  return HUMIDITY_MIN + HUMIDITY_MAX + moon.illumination + rng() + (state.band === 'humid' ? 1 : 0);\n"
            "}\n"
        ),
    }
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_missing_export_repair",
            base_files=base,
            artifact_quality_errors=(
                "src/models/Firefly.ts(4,8): error TS2724: '\"./types\"' has no exported member "
                "named '_HumidityBand'. Did you mean 'HumidityBand'?",
            ),
            mode="commit",
        )
    )
    assert result.ok is True
    assert result.planned is True
    after = ""
    for patch in (result.to_dict().get("composition_summary") or {}).get("patches") or []:
        if patch.get("path") == "src/models/Firefly.ts":
            after = str(patch.get("content_after") or "")
    assert "_HumidityBand" not in after
    assert "type HumidityState" in after
    # Must not forge export alias on types.ts
    types_after = ""
    for patch in (result.to_dict().get("composition_summary") or {}).get("patches") or []:
        if patch.get("path") == "src/models/types.ts":
            types_after = str(patch.get("content_after") or "")
    assert "_HumidityBand" not in types_after


def _diags(raw_lines: list[str]):
    from polaris.cells.director.runtime.internal.repair_kernel.typescript_runtime import (
        _diagnostics_for_typescript_runtime,
    )

    return _diagnostics_for_typescript_runtime(artifact_quality_errors=raw_lines, repair_diagnostics=None)


def test_r180_ts2300_duplicate_interface_member_keeps_first() -> None:
    """TS2300 interface member dups (fillStyle) delete only later lines."""

    from polaris.cells.director.runtime.internal.repair_kernel.typescript_syntax import (
        TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL,
        build_typescript_duplicate_object_property_plan,
    )

    base = {
        "src/engine/renderer.ts": "\n".join(
            [
                "export interface CanvasContext {",
                "  fillStyle: string | CanvasGradient;",
                "  fillStyle: string | CanvasGradient;",
                "  fillRect(x: number, y: number, w: number, h: number): void;",
                "}",
                "",
            ]
        )
    }
    diags = _diags(
        [
            "src/engine/renderer.ts(2,3): error TS2300: Duplicate identifier 'fillStyle'.",
            "src/engine/renderer.ts(3,3): error TS2300: Duplicate identifier 'fillStyle'.",
        ]
    )
    plan = build_typescript_duplicate_object_property_plan(
        base_files=base,
        diagnostics=diags,
        mode="commit",
    )
    assert plan is not None
    assert plan.source_tool == TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL
    op = plan.operations[0]
    after = (
        base["src/engine/renderer.ts"][: op.span_start] + op.replacement + base["src/engine/renderer.ts"][op.span_end :]
    )
    assert after.count("fillStyle:") == 1
    assert "fillRect" in after


def test_r180_ts2305_missing_export_class_does_not_invent_domain_stub() -> None:
    """Missing domain classes require owner/LLM repair, never an M10 stub."""

    from polaris.cells.director.runtime.internal.repair_kernel.typescript_syntax import (
        TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
        build_typescript_runtime_plan_for_source_tool,
    )

    base = {
        "src/models/index.ts": "export const VERSION = 1;\n",
        "src/engine/simulation.ts": "\n".join(
            [
                "import { GardenScene } from '../models/index.js';",
                "export class Engine {",
                "  private readonly scene: GardenScene;",
                "  constructor() {",
                "    this.scene = new GardenScene({ seed: 1 });",
                "  }",
                "  fireflies() { return this.scene.snapshot().fireflies; }",
                "}",
                "",
            ]
        ),
        "src/web.ts": "\n".join(
            [
                "import { GardenScene } from './models/index.js';",
                "const scene = new GardenScene({ seed: 1 });",
                "const snap = scene.snapshot();",
                "scene.publishForRegistry?.(snap.fireflies[0]?.id ?? 'x', 'firefly');",
                "",
            ]
        ),
    }
    diags = _diags(
        [
            "src/engine/simulation.ts(1,10): error TS2305: Module '\"../models/index.js\"' has no exported member 'GardenScene'.",
        ]
    )
    plan = build_typescript_runtime_plan_for_source_tool(
        source_tool=TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
        base_files=base,
        diagnostics=diags,
        mode="commit",
    )
    assert plan is None


def test_r180_ts2307_missing_relative_module_stub() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel.typescript_syntax import (
        TYPESCRIPT_MISSING_RELATIVE_MODULE_SOURCE_TOOL,
        build_typescript_runtime_plan_for_source_tool,
    )

    base = {
        "src/main.ts": "\n".join(
            [
                "async function tryRun(): Promise<void> {",
                "  const mod = await import('./verify.js');",
                "  if (typeof mod.runVerification === 'function') {",
                "    await mod.runVerification();",
                "  }",
                "}",
                "",
            ]
        )
    }
    diags = _diags(
        [
            "src/main.ts(2,30): error TS2307: Cannot find module './verify.js' or its corresponding type declarations.",
        ]
    )
    plan = build_typescript_runtime_plan_for_source_tool(
        source_tool=TYPESCRIPT_MISSING_RELATIVE_MODULE_SOURCE_TOOL,
        base_files=base,
        diagnostics=diags,
        mode="commit",
    )
    assert plan is not None
    assert plan.source_tool == TYPESCRIPT_MISSING_RELATIVE_MODULE_SOURCE_TOOL
    assert plan.operations[0].path == "src/verify.ts"
    assert "runVerification" in str(plan.operations[0].content or "")


def test_l217_ts2307_directory_prefix_rewrite_does_not_invent_nested_module() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel.typescript_syntax import (
        TYPESCRIPT_MISSING_RELATIVE_MODULE_SOURCE_TOOL,
        build_typescript_runtime_plan_for_source_tool,
    )

    base = {
        "src/models/index.ts": "export { brandId } from './models/types.js';\n",
        "src/models/types.ts": "export function brandId(value: string): string { return value; }\n",
    }
    diags = _diags(
        [
            "src/models/index.ts(1,27): error TS2307: Cannot find module './models/types.js' "
            "or its corresponding type declarations.",
        ]
    )
    plan = build_typescript_runtime_plan_for_source_tool(
        source_tool=TYPESCRIPT_MISSING_RELATIVE_MODULE_SOURCE_TOOL,
        base_files=base,
        diagnostics=diags,
        mode="commit",
    )
    assert plan is not None
    assert plan.operations
    assert all(operation.path != "src/models/models/types.ts" for operation in plan.operations)
    assert plan.operations[0].kind == "text_replace"
    assert plan.operations[0].path == "src/models/index.ts"
    assert plan.operations[0].replacement == "'./types.js'"


def test_r180_ts2339_declare_const_literal_and_ts2664_augmentation() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel.typescript_syntax import (
        TYPESCRIPT_INVALID_MODULE_AUGMENTATION_SOURCE_TOOL,
        TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
        build_typescript_runtime_plan_for_source_tool,
    )

    base = {
        "src/web.ts": "\n".join(
            [
                "declare const window: {",
                "  requestAnimationFrame(callback: (t: number) => void): number;",
                "};",
                "let rafId = 0;",
                "window.cancelAnimationFrame?.(rafId);",
                "",
                "declare module '../models/index.js' {",
                "  interface GardenScene { publishForRegistry?(id: string): void; }",
                "}",
                "",
            ]
        )
    }
    member_plan = build_typescript_runtime_plan_for_source_tool(
        source_tool=TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
        base_files=base,
        diagnostics=_diags(
            [
                "src/web.ts(5,14): error TS2339: Property 'cancelAnimationFrame' does not exist on type "
                "'{ requestAnimationFrame(callback: (t: number) => void): number; }'.",
            ]
        ),
        mode="commit",
    )
    assert member_plan is not None
    rep = member_plan.operations[0].replacement or ""
    assert "cancelAnimationFrame" in rep
    assert "(" in rep  # method form, not bare property

    aug_plan = build_typescript_runtime_plan_for_source_tool(
        source_tool=TYPESCRIPT_INVALID_MODULE_AUGMENTATION_SOURCE_TOOL,
        base_files=base,
        diagnostics=_diags(
            [
                "src/web.ts(7,16): error TS2664: Invalid module name in augmentation, "
                "module '../models/index.js' cannot be found.",
            ]
        ),
        mode="commit",
    )
    assert aug_plan is not None
    op = aug_plan.operations[0]
    after = base["src/web.ts"][: op.span_start] + op.replacement + base["src/web.ts"][op.span_end :]
    assert "declare module" not in after
    assert "cancelAnimationFrame" in base["src/web.ts"]  # original still has usage
