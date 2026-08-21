"""Shared test helpers preserved across repair-kernel contract file splits."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.director.runtime.internal.repair_kernel import (
    PatchComposer,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairOperation,
    RepairPlan,
    RepairPolicyContext,
    RepairPolicyGate,
    RepairReceipt,
    TransactionalRepairExecutor,
    javascript_syntax as js_syntax,
    normalize_artifact_quality_errors,
    runtime_dispatch as runtime_dispatch_module,
    typescript_syntax as ts_syntax,
)
from polaris.cells.director.runtime.internal.repair_kernel.contracts import sha256_text
from polaris.cells.director.runtime.public import (
    DirectorRepairShadowComparisonResultV1,
    PlanDirectorRepairCommandV1,
    RepairReceiptV1,
    plan_director_repair,
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


def _rust_copy_derive_error(*, path: str = "src/lib.rs", line: int = 2) -> str:
    return (
        "error[E0204]: the trait `Copy` cannot be implemented for this type\n"
        f" --> {path}:{line}:10\n"
        "  |\n"
        f"{line} | pub struct Demo {{ value: String }}\n"
        "  |          ^^^^"
    )


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
