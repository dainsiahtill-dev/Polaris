from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality import scan_workspace_artifact_quality


def _write_trivial_test(path: Path, *, count: int = 4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"test('case {idx}', () => expect({idx} + 1).toBe({idx + 1}));" for idx in range(count)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_scoped_scan_detects_placeholder_tests_in_target_file(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "card-rules.test.ts")

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["tests/unit/card-rules.test.ts"],
    )

    assert errors
    assert "tests/unit/card-rules.test.ts" in errors[0]


def test_scoped_scan_ignores_unrelated_placeholder_tests(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "legacy.test.ts")
    changed = tmp_path / "src" / "feature.ts"
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("export const feature = true;\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/feature.ts"],
    )

    assert errors == []


def test_full_scan_detects_unrelated_placeholder_tests(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "legacy.test.ts")

    errors = scan_workspace_artifact_quality(str(tmp_path))

    assert errors
    assert "tests/unit/legacy.test.ts" in errors[0]


def test_scoped_scan_expands_declared_directory_targets(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "card-rules.test.ts")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["tests"])

    assert errors
    assert "tests/unit/card-rules.test.ts" in errors[0]


def test_scan_detects_generated_structural_marker(tmp_path: Path) -> None:
    target = tmp_path / "src" / "client" / "generated.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export const note = 'structural build passed';\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/client/generated.ts"])

    assert errors
    assert "structural build passed" in errors[0]


def test_scan_detects_audit_seed_scenario_scaffold(tmp_path: Path) -> None:
    target = tmp_path / "src" / "game" / "rules-engine.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
export const cardRulesEngineScenario0 = {
  title: "card-rules-engine planning scenario 0",
  tags: ["planning", "draft", "audit-seed"],
};
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/game/rules-engine.ts"])

    assert errors
    assert "audit-seed" in errors[0] or "planning scenario" in errors[0]


def test_scan_detects_structural_verification_scripts(tmp_path: Path) -> None:
    target = tmp_path / "scripts" / "build.mjs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "console.log(`build verification completed: ${required.length} files`);\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["scripts/build.mjs"])

    assert errors
    assert "build verification completed" in errors[0]


def test_scan_detects_npm_default_failing_test_script(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"Error: no test specified\\" && exit 1"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert errors
    assert "npm default failing test script" in errors[0]


def test_scan_detects_npm_no_test_specified_even_when_exit_code_is_zero(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"Error: no test specified\\" && exit 0"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert errors
    assert "npm default failing test script" in errors[0]


def test_scan_detects_npm_no_tests_specified_plural(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"No tests specified\\" && exit 0"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert errors
    assert "npm default failing test script" in errors[0]


def test_scan_detects_node_test_runner_without_test_files_when_sources_exist(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        """
{
  "name": "typescript-project",
  "version": "1.0.0",
  "scripts": {
    "test": "vitest run"
  },
  "devDependencies": {
    "vitest": "^1.6.1"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "services" / "taskgraph.ts"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("export class TaskGraph {}\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("test runner script but no test/spec files exist" in error for error in errors)


def test_scan_allows_self_contained_node_test_script_without_test_files(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        """
{
  "name": "typescript-project",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "services" / "taskgraph.ts"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("export class TaskGraph {}\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert errors == []


def test_scan_detects_return_object_property_semicolon(tmp_path: Path) -> None:
    target = tmp_path / "src" / "models" / "task.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
export function summary() {
  const lanes: Record<string, number> = {};
  return {
    total: 1,
    lanes;
  };
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/models/task.ts"])

    assert errors
    assert "semicolon-terminated property" in errors[0]


def test_scan_detects_typescript_zod_type_class_name_collision(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"zod":"^3.23.8"}}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "models" / "task_definition.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
import { z } from 'zod';

export const TaskDefinitionSchema = z.object({
  name: z.string(),
});

type TaskDefinition = z.infer<typeof TaskDefinitionSchema>;

export class TaskDefinition {
  constructor(public data: TaskDefinition) {}
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/models/task_definition.ts"],
    )

    assert errors
    assert "TypeScript zod inferred type collides with class TaskDefinition" in errors[0]


def test_scan_detects_python_runtime_masquerading_as_npm_manifest(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "main": "src/main.py",
  "scripts": {
    "start": "python src/main.py"
  },
  "dependencies": {
    "pytest": "^7.0.0"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("Python runtime entrypoint" in error for error in errors)
    assert any("Python command" in error for error in errors)
    assert any("Python package dependency 'pytest'" in error for error in errors)


def test_scan_detects_unresolved_runtime_typescript_imports(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        '{"name":"tenant-workspace","scripts":{"test":"node scripts/test.mjs"}}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "middleware" / "tenant.middleware.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import { Request, Response, NextFunction } from 'express';\n"
        "import { RequestContext } from '../context';\n"
        "export const tenantMiddleware = true;\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/middleware/tenant.middleware.ts"],
    )

    assert any("undeclared runtime import 'express'" in error for error in errors)
    assert any("unresolved relative import '../context'" in error for error in errors)


def test_scan_requires_node_types_for_typescript_builtin_import(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        '{"name":"tenant-workspace","scripts":{"test":"node scripts/test.mjs"},"dependencies":{"express":"^4.18.2"}}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "middleware" / "tenant.middleware.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import { Request, Response, NextFunction } from 'express';\n"
        "import { AsyncLocalStorage } from 'async_hooks';\n"
        "export const tenantContext = new AsyncLocalStorage<Map<string, string>>();\n"
        "export function tenantMiddleware(req: Request, res: Response, next: NextFunction): void { next(); }\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/middleware/tenant.middleware.ts"],
    )

    assert any("requires '@types/node'" in error for error in errors)


def test_scan_allows_node_builtin_import_when_node_types_declared(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        '{"name":"tenant-workspace","scripts":{"test":"node scripts/test.mjs"},'
        '"dependencies":{"express":"^4.18.2"},"devDependencies":{"@types/node":"^22.10.0"}}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "middleware" / "tenant.middleware.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import { Request, Response, NextFunction } from 'express';\n"
        "import { AsyncLocalStorage } from 'async_hooks';\n"
        "export const tenantContext = new AsyncLocalStorage<Map<string, string>>();\n"
        "export function tenantMiddleware(req: Request, res: Response, next: NextFunction): void { next(); }\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/middleware/tenant.middleware.ts"],
    )

    assert errors == []


def test_scan_detects_escaped_newline_that_comments_out_typescript_export(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        '{"name":"tenant-workspace","scripts":{"test":"node scripts/test.mjs"},'
        '"dependencies":{"express":"^4.18.2"},"devDependencies":{"@types/node":"^22.10.0"}}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "middleware" / "tenant.middleware.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import { AsyncLocalStorage } from 'async_hooks';\n"
        "// Context for tenant request lifecycle\\nexport const tenantContext = "
        "new AsyncLocalStorage<{ tenantId: string }>();\n"
        "export function tenantMiddleware(): void { tenantContext.getStore(); }\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/middleware/tenant.middleware.ts"],
    )

    assert any("escaped newline in line comment" in error for error in errors)


def test_scan_resolves_dotted_typescript_relative_import_stems(tmp_path: Path) -> None:
    dag_service = tmp_path / "src" / "services" / "dag.service.ts"
    dag_service.parent.mkdir(parents=True, exist_ok=True)
    dag_service.write_text("export class DagService {}\n", encoding="utf-8")
    task_service = tmp_path / "src" / "services" / "task.service.ts"
    task_service.write_text(
        "import { DagService } from './dag.service';\nexport const taskService = new DagService();\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/services/task.service.ts"],
    )

    assert errors == []


def test_scan_detects_patch_residue_marker(tmp_path: Path) -> None:
    target = tmp_path / "src" / "assets" / "card-assets.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export const assetReady = true;\n>>>> REPLACE src/assets/card-assets.ts\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/assets/card-assets.ts"])

    assert errors
    assert "patch residue marker" in errors[0]


def test_scan_detects_repeated_numeric_helper_filler(tmp_path: Path) -> None:
    target = tmp_path / "src" / "client" / "feature.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            f"export function featureHelper{index}(value: number): number {{ return value + {index}; }}"
            for index in range(6)
        )
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/client/feature.ts"])

    assert errors
    assert "numeric helper filler" in errors[0]


def test_scan_detects_generic_payload_store_scaffold(tmp_path: Path) -> None:
    target = tmp_path / "src" / "state" / "store.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
export interface CardRecord {
  payload: string;
  index: number;
}

export class CardStore {
  private readonly items = new Map<string, CardRecord>();
}

export function cardHelper1(value: number): number { return value + 1; }
export function cardHelper2(value: number): number { return value + 2; }
export function cardHelper3(value: number): number { return value + 3; }
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/state/store.ts"])

    assert errors
    assert any("generic payload/index store scaffold" in error for error in errors)
