"""Unit tests for DirectorAdapter pure logic (no I/O, no LLM).

Covers:
- _select_execution_strategy
- _apply_intelligent_correction
- _build_director_message
- _build_materialized_metadata
- _resolve_execution_backend_request
- get_capabilities / role_id
"""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.cells.roles.adapters.internal.director import execute_method as execute_method_module
from polaris.cells.roles.adapters.internal.director.adapter import DirectorAdapter, _normalize_director_role_response
from polaris.cells.roles.adapters.internal.director.execute_method import (
    _apply_deterministic_javascript_test_missing_target_repair,
    _apply_deterministic_patch_residue_cleanup,
    _apply_deterministic_python_unittest_missing_target_repair,
    _apply_deterministic_python_unittest_runtime_failure_repair,
    _apply_deterministic_scaffold_marker_cleanup,
    _apply_deterministic_typescript_reexport_repair,
    _build_empty_write_content_retry_message,
    _build_existing_workspace_task_evidence,
    _build_substantive_node_test_script,
    _can_accept_existing_workspace_scope,
    _director_direct_text_patch_only_enabled,
    _director_existing_scope_preflight_enabled,
    _emit_director_adapter_cognitive_receipt,
    _empty_write_content_retry_needed,
    _extract_task_target_path_candidates,
    _finalize_claimed_execution,
    _is_overstrict_node_test_script_contract,
    _looks_like_typescript_reexport_failure,
    _remove_patch_residue_lines,
    _resolve_claim_external_task_id,
    _run_empty_write_content_materialization_retry,
    _task_requires_fresh_materialization,
    _task_runtime_finalization_failed_result,
)
from polaris.cells.roles.adapters.internal.director.execution import DirectorPatchExecutor
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1, RoleExecutionResultV1
from polaris.kernelone.quality import scan_workspace_artifact_quality

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(tmp_path: Any, task_board: Any = None, task_runtime: Any = None) -> DirectorAdapter:
    """Create a DirectorAdapter with mocked heavy dependencies."""
    if task_board is None and task_runtime is None:
        adapter = DirectorAdapter(workspace=str(tmp_path))
    else:
        adapter = DirectorAdapter(workspace=str(tmp_path), task_board=task_board, task_runtime=task_runtime)
    return adapter


def _write_substantive_node_test_script(tmp_path: Any) -> None:
    script_path = tmp_path / "scripts" / "test.mjs"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        "import assert from 'node:assert/strict';\n"
        "assert.equal(typeof process.version, 'string');\n"
        "console.log('node smoke test passed');\n",
        encoding="utf-8",
    )


def test_deterministic_npm_script_repair_builds_before_missing_dist_entrypoint(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.npm_repairs import (
        _apply_deterministic_npm_test_script_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("console.log('hello');\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "outDir": "dist",
                    "rootDir": "src",
                    "target": "ES2020",
                    "module": "ES2020",
                },
                "include": ["src/**/*.ts"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "typescript-project",
                "version": "1.0.0",
                "main": "dist/main.js",
                "scripts": {
                    "build": "tsc",
                    "start": "node dist/main.js",
                    "test": (
                        "node -e \"const fs=require('fs');"
                        "JSON.parse(fs.readFileSync('package.json','utf8'));"
                        "console.log('Manifest check passed')\""
                    ),
                },
                "devDependencies": {"typescript": "^5.4.0"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])
    assert any("manifest-only test script" in error for error in errors)
    assert any("references missing local entrypoint 'dist/main.js'" in error for error in errors)

    results = _apply_deterministic_npm_test_script_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert repaired["scripts"]["test"] == "npm run build"
    assert repaired["scripts"]["start"] == "npm run build && node dist/main.js"
    repaired_errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])
    assert not any("manifest-only test script" in error for error in repaired_errors)
    assert not any("references missing local entrypoint" in error for error in repaired_errors)


def test_deterministic_npm_script_repair_replaces_placeholder_start_and_swallowing_build(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.npm_repairs import (
        _apply_deterministic_npm_test_script_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("console.log('firefly flower moon humidity');\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"outDir": "dist", "rootDir": "src"}, "include": ["src/**/*.ts"]}),
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "glowing-insect-garden",
                "version": "1.0.0",
                "main": "src/main.ts",
                "scripts": {
                    "build": "tsc --noEmit 2>/dev/null || echo 'TypeScript check skipped'",
                    "start": "echo 'Open index.html in a browser'",
                    "test": "npm run build",
                },
                "devDependencies": {"typescript": "^5.6.0"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])
    assert any("script 'start' is a placeholder command" in error for error in errors)
    assert any("script 'build' swallows command failures" in error for error in errors)

    results = _apply_deterministic_npm_test_script_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert repaired["scripts"]["build"] == "tsc"
    assert repaired["scripts"]["start"] == "npm run build && node dist/main.js"


def test_deterministic_npm_script_repair_removes_shell_substitution_test(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.npm_repairs import (
        _apply_deterministic_npm_test_script_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("console.log('hello');\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"target": "ES2020"}, "include": ["src/**/*.ts"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "typescript-project",
                "version": "1.0.0",
                "main": "dist/main.js",
                "scripts": {
                    "build": "tsc",
                    "test": 'node -e "console.error(`Missing value`);"',
                },
                "devDependencies": {"typescript": "^5.4.0"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])
    assert any("uses shell command substitution" in error for error in errors)

    results = _apply_deterministic_npm_test_script_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert repaired["scripts"]["test"] == "npm run build"


def test_deterministic_npm_script_repair_removes_missing_jest_config_flag(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.npm_repairs import (
        _apply_deterministic_npm_test_script_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export const ok = true;\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"target": "ES2020"}, "include": ["src/**/*.ts"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "typescript-project",
                "version": "1.0.0",
                "scripts": {
                    "build": "tsc",
                    "test": "jest --config jest.config.js --forceExit",
                },
                "devDependencies": {"jest": "^29.0.0", "typescript": "^5.4.0"},
                "jest": {"testEnvironment": "node"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])
    assert any("references missing config file 'jest.config.js'" in error for error in errors)

    results = _apply_deterministic_npm_test_script_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert repaired["scripts"]["test"] == "jest --forceExit"


def test_deterministic_runtime_dependency_repair_adds_typescript_dev_dependency(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.npm_repairs import (
        _apply_deterministic_runtime_dependency_repair,
    )

    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "typescript-project",
                "version": "1.0.0",
                "scripts": {"build": "tsc", "test": "npm run build"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    results = _apply_deterministic_runtime_dependency_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=[
            "Artifact quality scan failed: TypeScript project requires 'typescript' devDependency in package.json"
        ],
    )

    assert results
    repaired = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert repaired["devDependencies"]["typescript"] == "^5.6.0"


def test_deterministic_npm_script_repair_cleans_package_scaffold_metadata(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.npm_repairs import (
        _apply_deterministic_npm_test_script_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("console.log('hello');\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"target": "ES2020"}, "include": ["src/**/*.ts"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "polaris-typescript-project",
                "version": "1.0.0",
                "description": "Polaris TypeScript project scaffold",
                "scripts": {
                    "build": "tsc",
                    "start": "npm run build && node dist/main.js",
                    "test": "npm run build",
                },
                "devDependencies": {"typescript": "^5.4.0"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: deterministic scaffold marker 'TypeScript project scaffold' in package.json"
    ]

    results = _apply_deterministic_npm_test_script_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert repaired["name"] == "typescript-application"
    assert repaired["description"] == "TypeScript application"


def test_deterministic_materialization_repair_cleans_scaffold_marker_from_reported_source(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.generic_repairs import (
        _apply_deterministic_materialization_quality_repairs,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text(
        'console.log("Hello from Polaris TypeScript scaffold.");\n',
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: deterministic scaffold marker 'Polaris TypeScript scaffold' in src/main.ts"
    ]

    results, summary = _apply_deterministic_materialization_quality_repairs(
        _make_adapter(tmp_path),
        task={"metadata": {"target_files": ["src/main.ts"]}},
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    assert summary["source_tools"] == ["deterministic_scaffold_marker_quality_cleanup"]
    repaired = (tmp_path / "src" / "main.ts").read_text(encoding="utf-8")
    assert "Polaris TypeScript scaffold" not in repaired
    assert "TypeScript application" in repaired


def test_deterministic_typescript_missing_member_repair_adds_class_members(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_member_repair,
    )

    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models" / "flower.ts").write_text(
        "export class Flower {\n  public id: string;\n  constructor(id: string) {\n    this.id = id;\n  }\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "gardenengine.ts").write_text(
        "import { Flower } from '../models/flower.js';\n"
        "const flower = new Flower('f1');\n"
        "flower.getAttraction();\n"
        "flower.name.length;\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/engine/gardenengine.ts(3,8): error TS2339: Property 'getAttraction' "
        "does not exist on type 'Flower'.\n"
        "src/engine/gardenengine.ts(4,8): error TS2339: Property 'name' does not exist on type 'Flower'."
    ]

    results = _apply_deterministic_typescript_missing_member_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "models" / "flower.ts").read_text(encoding="utf-8")
    assert "public getAttraction(): number" in repaired
    assert "public get name(): string" in repaired
    assert "return this.id;" in repaired


def test_deterministic_typescript_missing_member_repair_infers_numeric_getter_for_arithmetic(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_member_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "moon.ts").write_text(
        "export class Moon {\n  public getIllumination(): number {\n    return 1;\n  }\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "firefly.ts").write_text(
        "import { Moon } from './moon.js';\n"
        "const moon = new Moon();\n"
        "const moonFactor = 0.4 + moon.brightness * 0.6;\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/firefly.ts(3,31): error TS2339: Property 'brightness' does not exist on type 'Moon'."
    ]

    results = _apply_deterministic_typescript_missing_member_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "moon.ts").read_text(encoding="utf-8")
    assert "public get brightness(): number" in repaired
    assert "return 0;" in repaired


def test_deterministic_typescript_missing_member_repair_infers_string_getter_for_id_comparison(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_member_repair,
    )

    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models" / "flower.ts").write_text(
        "export class Flower {\n  public get name(): string {\n    return '';\n  }\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "gardenengine.ts").write_text(
        "import { Flower } from '../models/flower.js';\n"
        "const flowers: Flower[] = [];\n"
        "const flowerId = 'flower-1';\n"
        "flowers.find(f => f.id === flowerId);\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/engine/gardenengine.ts(4,21): error TS2339: Property 'id' does not exist on type 'Flower'."
    ]

    results = _apply_deterministic_typescript_missing_member_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "models" / "flower.ts").read_text(encoding="utf-8")
    assert "public get id(): string" in repaired
    assert 'return "";' in repaired


def test_deterministic_typescript_missing_member_repair_adds_method_parameters_from_usage(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_member_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "firefly.ts").write_text("export class Firefly {}\n", encoding="utf-8")
    (tmp_path / "src" / "garden.ts").write_text(
        "import { Firefly } from './firefly.js';\nconst firefly = new Firefly();\nfirefly.update(0.5, 0.8);\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/garden.ts(3,9): error TS2339: Property 'update' does not exist on type 'Firefly'."
    ]

    results = _apply_deterministic_typescript_missing_member_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "firefly.ts").read_text(encoding="utf-8")
    assert "public update(_arg0: unknown, _arg1: unknown): number" in repaired


def test_deterministic_typescript_missing_member_repair_adds_interface_properties(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_member_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "simulation.ts").write_text(
        "export interface Flower {\n  mood: number;\n}\n\nexport type Firefly = {\n  id: string;\n};\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "render.ts").write_text(
        "import type { Firefly, Flower } from './simulation.js';\n"
        "const flower = {} as Flower;\n"
        "const firefly = {} as Firefly;\n"
        "const radius = flower.size * 2;\n"
        "const fill = flower.color;\n"
        "const x = firefly.baseX + firefly.brightness;\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/render.ts(4,23): error TS2339: Property 'size' does not exist on type 'Flower'.\n"
        "src/render.ts(5,21): error TS2339: Property 'color' does not exist on type 'Flower'.\n"
        "src/render.ts(6,19): error TS2339: Property 'baseX' does not exist on type 'Firefly'.\n"
        "src/render.ts(6,35): error TS2339: Property 'brightness' does not exist on type 'Firefly'."
    ]

    results = _apply_deterministic_typescript_missing_member_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "simulation.ts").read_text(encoding="utf-8")
    assert "  size: number;" in repaired
    assert "  color: string;" in repaired
    assert "  baseX: number;" in repaired
    assert "  brightness: number;" in repaired


def test_deterministic_typescript_missing_member_repair_infers_destructured_object_shapes(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_member_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "models.ts").write_text(
        "export interface Firefly {\n  color: string;\n}\n\nexport interface Flower {\n  emotion: unknown;\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "render.ts").write_text(
        "import type { Firefly, Flower } from './models.js';\n"
        "export function drawFirefly(firefly: Firefly): string {\n"
        "  const { color } = firefly;\n"
        "  return `${color.r},${color.g},${color.b}`;\n"
        "}\n"
        "export function drawFlower(flower: Flower): number {\n"
        "  const { emotion } = flower;\n"
        "  return emotion.hue + emotion.intensity;\n"
        "}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/render.ts(4,19): error TS2339: Property 'r' does not exist on type 'string'.\n"
        "src/render.ts(4,30): error TS2339: Property 'g' does not exist on type 'string'.\n"
        "src/render.ts(4,41): error TS2339: Property 'b' does not exist on type 'string'.\n"
        "src/render.ts(8,10): error TS18046: 'emotion' is of type 'unknown'."
    ]

    results = _apply_deterministic_typescript_missing_member_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "models.ts").read_text(encoding="utf-8")
    assert "color: { r: number; g: number; b: number };" in repaired
    assert "emotion: { hue: number; intensity: number };" in repaired


def test_deterministic_typescript_missing_member_repair_fills_returns_when_adding_members(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_member_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "flower.ts").write_text(
        "export interface Flower {\n"
        "  name: string;\n"
        "}\n\n"
        "export function createFlower(name: string): Flower {\n"
        "  return {\n"
        "    name,\n"
        "  };\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "render.ts").write_text(
        "import type { Flower } from './flower.js';\n"
        "export function drawFlower(flower: Flower): number {\n"
        "  const { x, emotion, petalCount } = flower;\n"
        "  return x + emotion.hue + emotion.intensity + petalCount;\n"
        "}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/render.ts(3,11): error TS2339: Property 'x' does not exist on type 'Flower'.\n"
        "src/render.ts(3,14): error TS2339: Property 'emotion' does not exist on type 'Flower'.\n"
        "src/render.ts(3,23): error TS2339: Property 'petalCount' does not exist on type 'Flower'."
    ]

    results = _apply_deterministic_typescript_missing_member_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "flower.ts").read_text(encoding="utf-8")
    assert "  x: number;" in repaired
    assert "  emotion: { hue: number; intensity: number };" in repaired
    assert "  petalCount: number;" in repaired
    assert "    x: 0," in repaired
    assert "    emotion: { hue: 0, intensity: 0 }," in repaired
    assert "    petalCount: 0," in repaired


def test_deterministic_typescript_missing_member_repair_fills_required_object_literals(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_member_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "flower.ts").write_text(
        "export interface Flower {\n"
        "  name: string;\n"
        "  x: number;\n"
        "  y: number;\n"
        "  emotion: { hue: number; intensity: number };\n"
        "  petalCount: number;\n"
        "}\n\n"
        "export function createFlower(name: string): Flower {\n"
        "  return {\n"
        "    name,\n"
        "  };\n"
        "}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/flower.ts(10,3): error TS2739: Type '{ name: string; }' is missing "
        "the following properties from type 'Flower': x, y, emotion, petalCount"
    ]

    results = _apply_deterministic_typescript_missing_member_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "flower.ts").read_text(encoding="utf-8")
    assert "    x: 0," in repaired
    assert "    y: 0," in repaired
    assert "    emotion: { hue: 0, intensity: 0 }," in repaired
    assert "    petalCount: 0," in repaired


def test_deterministic_typescript_missing_member_repair_defaults_unhostable_union_access(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_member_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "moon.ts").write_text(
        "export type MoonPhase = 'new' | 'full';\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "render.ts").write_text(
        "import type { MoonPhase } from './moon.js';\n"
        "export function drawMoon(moon: MoonPhase): number {\n"
        "  const phaseAngle = moon.phaseAngle;\n"
        "  return phaseAngle;\n"
        "}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/render.ts(3,27): error TS2339: Property 'phaseAngle' does not exist on type 'MoonPhase'.\n"
        "  Property 'phaseAngle' does not exist on type '\"new\"'."
    ]

    results = _apply_deterministic_typescript_missing_member_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "render.ts").read_text(encoding="utf-8")
    assert "const phaseAngle = 0;" in repaired


def test_deterministic_typescript_missing_member_repair_adds_static_factories_for_typeof_errors(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_member_repair,
    )

    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models" / "flower.ts").write_text(
        "export class Flower {\n  constructor(name: string, color: string, isBlooming: boolean = true) {}\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "models" / "firefly.ts").write_text(
        "export class Firefly {\n  constructor(name: string, isGlowing: boolean = true) {}\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "models" / "moonphase.ts").write_text(
        "export class MoonPhase {\n  constructor() {}\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "simulation.ts").write_text(
        "import { Flower } from '../models/flower';\n"
        "import { Firefly } from '../models/firefly';\n"
        "import { MoonPhase } from '../models/moonphase';\n"
        "const flowers: Flower[] = [];\n"
        "flowers.push(Flower.createRandom(1));\n"
        "const fireflies: Firefly[] = [];\n"
        "fireflies.push(Firefly.createRandom(1));\n"
        "let moonPhase = MoonPhase.random();\n"
        "moonPhase = MoonPhase.next(moonPhase);\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/engine/simulation.ts(5,21): error TS2339: Property 'createRandom' does not exist on type "
        "'typeof Flower'.\n"
        "src/engine/simulation.ts(7,23): error TS2339: Property 'createRandom' does not exist on type "
        "'typeof Firefly'.\n"
        "src/engine/simulation.ts(8,27): error TS2339: Property 'random' does not exist on type "
        "'typeof MoonPhase'.\n"
        "src/engine/simulation.ts(9,23): error TS2339: Property 'next' does not exist on type "
        "'typeof MoonPhase'."
    ]

    results = _apply_deterministic_typescript_missing_member_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    flower = (tmp_path / "src" / "models" / "flower.ts").read_text(encoding="utf-8")
    firefly = (tmp_path / "src" / "models" / "firefly.ts").read_text(encoding="utf-8")
    moon = (tmp_path / "src" / "models" / "moonphase.ts").read_text(encoding="utf-8")
    simulation = (tmp_path / "src" / "engine" / "simulation.ts").read_text(encoding="utf-8")
    assert "public static createRandom(_arg0: unknown): Flower" in flower
    assert 'return new Flower("", "", false);' in flower
    assert "public static createRandom(_arg0: unknown): Firefly" in firefly
    assert 'return new Firefly("", false);' in firefly
    assert "public static random(): MoonPhase" in moon
    assert "public static next(_arg0: unknown): MoonPhase" in moon
    assert "undefined as unknown as unknown(" not in simulation


def test_deterministic_typescript_missing_member_repair_adds_static_values_for_typeof_errors(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_member_repair,
    )

    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models" / "moonphase.ts").write_text(
        "export class MoonPhase {\n"
        "  constructor() {}\n"
        "  public get NewMoon(): unknown {\n"
        "    return undefined;\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "garden.ts").write_text(
        "import { MoonPhase } from '../models/moonphase';\n"
        "const currentMoon = MoonPhase.NewMoon;\n"
        "if (currentMoon === MoonPhase.FullMoon) console.log('bright');\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/engine/garden.ts(2,31): error TS2339: Property 'NewMoon' does not exist on type "
        "'typeof MoonPhase'.\n"
        "src/engine/garden.ts(3,31): error TS2339: Property 'FullMoon' does not exist on type "
        "'typeof MoonPhase'."
    ]

    results = _apply_deterministic_typescript_missing_member_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    moon = (tmp_path / "src" / "models" / "moonphase.ts").read_text(encoding="utf-8")
    assert "public static readonly NewMoon: MoonPhase = new MoonPhase();" in moon
    assert "public static readonly FullMoon: MoonPhase = new MoonPhase();" in moon
    assert "public get NewMoon" in moon


def test_deterministic_typescript_missing_export_repair_adds_constructed_class(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_export_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text(
        "import { GardenSimulator } from './product';\nconst garden = new GardenSimulator();\ngarden.report();\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "product.ts").write_text(
        "export interface GardenState {\n"
        "  flowers: number;\n"
        "}\n"
        "\n"
        "export function createGardenState(): GardenState {\n"
        "  return { flowers: 3 };\n"
        "}\n"
        "\n"
        "export function gardenReport(state: GardenState): string {\n"
        "  return `flowers=${state.flowers}`;\n"
        "}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/main.ts(1,10): error TS2305: Module '\"./product\"' has no exported member 'GardenSimulator'."
    ]

    results = _apply_deterministic_typescript_missing_export_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    result_payload = results[0]["result"]
    assert result_payload["source_tool"] == "deterministic_typescript_missing_export_repair"
    assert result_payload["before_sha256"]
    assert result_payload["after_sha256"]
    assert result_payload["before_sha256"] != result_payload["after_sha256"]
    assert "export class GardenSimulator" in result_payload["diff_excerpt"]
    repaired = (tmp_path / "src" / "product.ts").read_text(encoding="utf-8")
    assert "export class GardenSimulator" in repaired
    assert "public constructor(..._args: unknown[]) {}" in repaired
    assert "public report(..._args: unknown[]): string" in repaired


def test_deterministic_typescript_missing_export_repair_adds_type_and_function(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_export_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "render.ts").write_text(
        "import { SimulationState, updateSimulation } from './simulation';\n"
        "type Snapshot = SimulationState;\n"
        "const current: Snapshot = updateSimulation({ speed: 1 });\n"
        "export { current };\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "simulation.ts").write_text(
        "export class GardenSimulation {\n  public start(): void {}\n}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/render.ts(1,10): error TS2305: Module '\"./simulation\"' has no exported member "
        "'SimulationState'.\n"
        "src/render.ts(1,27): error TS2305: Module '\"./simulation\"' has no exported member "
        "'updateSimulation'."
    ]

    results = _apply_deterministic_typescript_missing_export_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "simulation.ts").read_text(encoding="utf-8")
    assert "export type SimulationState = any;" in repaired
    assert "export function updateSimulation(..._args: unknown[]): any" in repaired


def test_deterministic_typescript_missing_export_repair_uses_ts2724_suggestion_alias(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_export_repair,
    )

    model_dir = tmp_path / "src" / "models"
    model_dir.mkdir(parents=True)
    (tmp_path / "src" / "index.ts").write_text(
        "import { IHumidityState } from './models/humidity';\n"
        "const state: IHumidityState = new IHumidityState();\n"
        "export { state };\n",
        encoding="utf-8",
    )
    (model_dir / "humidity.ts").write_text(
        "export class HumidityState {\n  public level = 60;\n}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/index.ts(1,10): error TS2724: '\"./models/humidity\"' has no exported member named "
        "'IHumidityState'. Did you mean 'HumidityState'?"
    ]

    results = _apply_deterministic_typescript_missing_export_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (model_dir / "humidity.ts").read_text(encoding="utf-8")
    assert "export { HumidityState as IHumidityState };" in repaired
    assert "export type IHumidityState = any;" not in repaired


def test_deterministic_typescript_missing_export_repair_avoids_unknown_type_property_trap(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_export_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "render.ts").write_text(
        "import { Moon } from './simulation';\n"
        "export function renderMoon(moon: Moon): string {\n"
        "  return `${moon.brightness}`;\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "simulation.ts").write_text(
        "export interface SimulationState { moon: { brightness: number }; }\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/render.ts(1,10): error TS2305: Module '\"./simulation\"' has no exported member 'Moon'."
    ]

    results = _apply_deterministic_typescript_missing_export_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "simulation.ts").read_text(encoding="utf-8")
    assert "export type Moon = any;" in repaired
    assert "export type Moon = unknown;" not in repaired


def test_deterministic_materialization_repair_routes_typescript_missing_export(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.generic_repairs import (
        _apply_deterministic_materialization_quality_repairs,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text(
        "import { GardenSimulator } from './product';\nnew GardenSimulator().report();\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "product.ts").write_text(
        "export function gardenReport(): string {\n  return 'ok';\n}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/main.ts(1,10): error TS2305: Module '\"./product\"' has no exported member 'GardenSimulator'."
    ]

    results, summary = _apply_deterministic_materialization_quality_repairs(
        _make_adapter(tmp_path),
        task={"metadata": {"target_files": ["src/main.ts", "src/product.ts"]}},
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    assert "deterministic_typescript_missing_export_repair" in summary["source_tools"]
    repaired = (tmp_path / "src" / "product.ts").read_text(encoding="utf-8")
    assert "export class GardenSimulator" in repaired
    assert "public report(..._args: unknown[]): string" in repaired


def test_deterministic_typescript_number_to_string_argument_repair_wraps_argument(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_number_to_string_argument_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "garden.ts").write_text(
        "const fireflies = Array.from({ length: 3 }, (_, i) => new Firefly(i, width, height));\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/garden.ts(1,65): error TS2345: Argument of type 'number' is not assignable "
        "to parameter of type 'string'."
    ]

    results = _apply_deterministic_typescript_number_to_string_argument_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "garden.ts").read_text(encoding="utf-8")
    assert "new Firefly(String(i), width, height)" in repaired


def test_deterministic_typescript_too_few_arguments_repair_adds_trailing_defaults(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_too_few_arguments_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "firefly.ts").write_text(
        "export class Firefly {\n  update(deltaTime: number, moonPhase: number, temperature: number): void {}\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "garden.ts").write_text(
        "import { Firefly } from './firefly.js';\nconst firefly = new Firefly();\nfirefly.update(0.5, 0.8);\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/garden.ts(3,9): error TS2554: Expected 3 arguments, but got 2."
    ]

    results = _apply_deterministic_typescript_too_few_arguments_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "firefly.ts").read_text(encoding="utf-8")
    assert "temperature: number = 0" in repaired


def test_deterministic_typescript_too_few_arguments_repair_fixes_two_arg_clamp_calls(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_too_few_arguments_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "engine.ts").write_text(
        "function clamp(value: number, min: number, max: number): number {\n"
        "  return Math.max(min, Math.min(max, value));\n"
        "}\n"
        "let y = clamp(42, 600);\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/engine.ts(4,9): error TS2554: Expected 3 arguments, but got 2."
    ]

    results = _apply_deterministic_typescript_too_few_arguments_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "engine.ts").read_text(encoding="utf-8")
    assert "let y = clamp(42, 0, 600);" in repaired


def test_deterministic_typescript_missing_member_repair_does_not_confuse_parameters_with_fields(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_missing_member_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "flower.ts").write_text(
        "export class Flower {\n  updateHumidity(humidity: number): void {\n    this.humidity = humidity;\n  }\n}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/flower.ts(3,10): error TS2339: Property 'humidity' does not exist on type 'Flower'."
    ]

    results = _apply_deterministic_typescript_missing_member_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "flower.ts").read_text(encoding="utf-8")
    assert "public humidity: number = 0;" in repaired


def test_deterministic_typescript_uninitialized_property_repair_adds_default_value(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_uninitialized_property_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "flower.ts").write_text(
        "export class Flower {\n  public happiness: number;\n  constructor() {}\n}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/flower.ts(2,10): error TS2564: Property 'happiness' has no initializer "
        "and is not definitely assigned in the constructor."
    ]

    results = _apply_deterministic_typescript_uninitialized_property_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = (tmp_path / "src" / "flower.ts").read_text(encoding="utf-8")
    assert "public happiness: number = 0;" in repaired


def test_deterministic_typescript_tsconfig_lib_repair_adds_dom(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_tsconfig_lib_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("console.log('hello');\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "target": "ES2020",
                    "module": "ES2020",
                    "lib": ["ES2020"],
                },
                "include": ["src/**/*.ts"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/main.ts(1,1): error TS2584: Cannot find name 'console'. "
        "Do you need to change your target library? Try changing the 'lib' compiler option to include 'dom'."
    ]

    results = _apply_deterministic_typescript_tsconfig_lib_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = json.loads((tmp_path / "tsconfig.json").read_text(encoding="utf-8"))
    assert repaired["compilerOptions"]["lib"] == ["ES2020", "DOM"]


def test_deterministic_typescript_tsconfig_lib_repair_adds_dom_for_window(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
        _apply_deterministic_typescript_tsconfig_lib_repair,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("window.setInterval(() => undefined, 1000);\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "target": "ES2020",
                    "module": "ES2020",
                    "lib": ["ES2020"],
                },
                "include": ["src/**/*.ts"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/main.ts(1,1): error TS2304: Cannot find name 'window'. "
        "Do you need to change your target library? Try changing the 'lib' compiler option to include 'dom'."
    ]

    results = _apply_deterministic_typescript_tsconfig_lib_repair(
        _make_adapter(tmp_path),
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    repaired = json.loads((tmp_path / "tsconfig.json").read_text(encoding="utf-8"))
    assert repaired["compilerOptions"]["lib"] == ["ES2020", "DOM"]


@pytest.mark.asyncio
async def test_phase_quality_repair_loop_continues_after_deterministic_progress_when_llm_empty(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(tmp_path)
    error_rounds = [
        [
            "Artifact quality scan failed: TypeScript project typecheck failed: "
            "src/firefly.ts(1,1): error TS2339: Property 'illumination' does not exist on type 'Moon'."
        ],
        [
            "Artifact quality scan failed: TypeScript project typecheck failed: "
            "src/firefly.ts(1,1): error TS2339: Property 'brightness' does not exist on type 'Moon'."
        ],
        [],
    ]
    deterministic_inputs: list[list[str]] = []

    def fake_collect_materialization_quality_errors(*args: Any, **kwargs: Any) -> list[str]:
        return error_rounds.pop(0) if error_rounds else []

    def fake_deterministic_quality_repair(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        artifact_quality_errors = list(kwargs.get("artifact_quality_errors") or [])
        deterministic_inputs.append(artifact_quality_errors)
        if not artifact_quality_errors:
            return [], {"stage": "deterministic_quality_repair", "success": False}
        return [
            {
                "tool_name": "write_file",
                "tool": "write_file",
                "success": True,
                "result": {"ok": True, "source_tool": "fake_deterministic_repair", "file": "src/moon.ts"},
            }
        ], {"stage": "deterministic_quality_repair", "success": True}

    async def fake_llm_quality_repair(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return [], {"stage": "llm_quality_repair", "success": False}

    monkeypatch.setattr(
        execute_method_module, "_collect_materialization_quality_errors", fake_collect_materialization_quality_errors
    )
    monkeypatch.setattr(execute_method_module, "_collect_step_verify_errors", lambda *args, **kwargs: [])
    monkeypatch.setattr(execute_method_module, "_apply_deterministic_python_static_smoke", lambda *args, **kwargs: [])
    monkeypatch.setattr(execute_method_module, "_apply_deterministic_python_runtime_smoke", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        execute_method_module, "_filter_satisfied_declared_target_missing_errors", lambda errors, workspace: errors
    )
    monkeypatch.setattr(execute_method_module, "_missing_declared_target_files", lambda task, workspace: [])
    monkeypatch.setattr(
        execute_method_module,
        "_apply_deterministic_declared_target_contract_repairs",
        lambda *args, **kwargs: ([], {"stage": "deterministic_contract_repair", "success": False}),
    )
    monkeypatch.setattr(
        execute_method_module, "_apply_deterministic_materialization_quality_repairs", fake_deterministic_quality_repair
    )
    monkeypatch.setattr(execute_method_module, "_run_materialization_quality_repair_retry", fake_llm_quality_repair)
    monkeypatch.setattr(
        execute_method_module,
        "_collect_workspace_code_diff",
        lambda *args, **kwargs: ({}, [], ["src/moon.ts"], ["src/firefly.ts", "src/moon.ts"]),
    )

    quality_repair_attempts: list[dict[str, Any]] = []

    state, residual_errors, _summary, _write_evidence = await execute_method_module._phase_quality_repair_loop(
        adapter,
        adapter_workspace=str(tmp_path),
        baseline_files={},
        context={},
        llm_call_timeout=1.0,
        message="repair TypeScript project",
        quality_repair_attempts=quality_repair_attempts,
        quality_repair_summary=None,
        run_id="run-1",
        target_task_id="task-1",
        task={"metadata": {"target_files": ["src/firefly.ts", "src/moon.ts"]}},
        workspace_name=tmp_path.name,
        write_tool_evidence=False,
        state=execute_method_module.MaterializationState(
            current_files={},
            new_files=[],
            modified_files=[],
            all_affected_files=["src/firefly.ts"],
            tool_results=[],
        ),
    )

    assert residual_errors == []
    assert len(deterministic_inputs) == 2
    assert "illumination" in deterministic_inputs[0][0]
    assert "brightness" in deterministic_inputs[1][0]
    assert len(state.tool_results) == 2
    assert quality_repair_attempts[0]["revalidated"] is True
    assert quality_repair_attempts[0]["success"] is False
    assert quality_repair_attempts[0]["residual_error_count"] == 1
    assert quality_repair_attempts[-1]["revalidated"] is True
    assert quality_repair_attempts[-1]["success"] is True
    assert quality_repair_attempts[-1]["residual_error_count"] == 0


def test_empty_write_content_retry_needed_only_for_blank_write() -> None:
    assert (
        _empty_write_content_retry_needed(
            [
                {
                    "tool_name": "write_file",
                    "arguments": {"file": "src/app.py", "content": ""},
                    "status": "error",
                }
            ]
        )
        is True
    )
    assert (
        _empty_write_content_retry_needed(
            [
                {
                    "tool_name": "write_file",
                    "arguments": {"file": "src/app.py", "content": "print('ok')\n"},
                    "status": "success",
                }
            ]
        )
        is False
    )
    assert _empty_write_content_retry_needed([{"tool_name": "read_file", "status": "success"}]) is False


def test_empty_write_retry_uses_concrete_scope_path_when_target_files_missing() -> None:
    task = {
        "subject": "实现交互式 CLI 入口与 REPL 循环",
        "scope_paths": ["main.py"],
    }

    assert _extract_task_target_path_candidates(task) == ["main.py"]
    message = _build_empty_write_content_retry_message(
        task,
        original_message="[mode:materialize]\n范围: main.py",
        tool_results=[{"tool_name": "write_file", "arguments": {"file": "main.py", "content": ""}}],
    )

    assert "Allowed target files: main.py." in message


@pytest.mark.asyncio
async def test_execute_retries_blank_write_content_with_materialize_prompt(tmp_path: Any) -> None:
    adapter = _make_adapter(tmp_path)
    task = adapter.task_board.create(
        subject="Create app module",
        description="Create src/app.py with a runnable entry point.",
        metadata={"target_files": ["src/app.py"], "scope_paths": ["src/app.py"]},
    )
    seen_messages: list[str] = []
    seen_contexts: list[dict[str, Any]] = []

    async def _dialogue(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args
        seen_messages.append(message)
        raw_context = kwargs.get("context")
        seen_contexts.append(raw_context if isinstance(raw_context, dict) else {})
        if len(seen_messages) == 1:
            return {
                "content": "I will create src/app.py.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "tool_name": "write_file",
                        "status": "success",
                        "success": True,
                        "arguments": {"file": "src/app.py", "content": ""},
                        "result": {"path": "src/app.py", "ok": True},
                    }
                ],
            }
        return {
            "content": (
                "src/app.py\n"
                "```python\n"
                "APP_STATUS = 'ok'\n"
                "\n"
                "\n"
                "def main() -> str:\n"
                "    return APP_STATUS\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    print(main())\n"
                "```\n"
            ),
            "success": True,
            "tool_results": [
                {
                    "tool": "write_file",
                    "tool_name": "write_file",
                    "status": "success",
                    "success": True,
                    "arguments": {"file": "src/app.py", "content": ""},
                    "result": {"path": "src/app.py", "ok": True},
                }
            ],
        }

    async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

    adapter._invoke_role_dialogue_with_timeout = _dialogue  # type: ignore[method-assign]
    adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id=str(task.id),
        input_data={"task_id": str(task.id)},
        context={"run_id": "run-empty-write-retry"},
    )

    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == (
        "APP_STATUS = 'ok'\n\n\ndef main() -> str:\n    return APP_STATUS\n\n\n"
        "if __name__ == '__main__':\n    print(main())\n"
    )
    assert len(seen_messages) == 2
    assert "previous write tool call had blank content" in seen_messages[1]
    assert seen_contexts[1]["_transaction_kernel_forced_tool_choice"] == {
        "type": "function",
        "function": {"name": "write_file"},
    }
    forced_defs = seen_contexts[1]["_transaction_kernel_forced_tool_definitions"]
    assert forced_defs and forced_defs[0]["function"]["name"] == "write_file"
    assert seen_contexts[1]["director_empty_write_retry"]["write_only_single_target"] == {
        "tool": "write_file",
        "target_file": "src/app.py",
    }
    assert result.get("error_code") != "director_no_materialized_changes"


@pytest.mark.asyncio
async def test_empty_write_retry_existing_target_forces_edit_blocks(tmp_path: Any) -> None:
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    class _Execution:
        def __init__(self) -> None:
            self.allowed_tool_names: set[str] | None = None
            self.allow_patch_fallback: bool | None = None

        @staticmethod
        def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
            del result
            return []

        async def execute_tools(
            self,
            content: str,
            target_task_id: str,
            update_task_progress: Any,
            **kwargs: Any,
        ) -> list[dict[str, Any]]:
            del content, target_task_id, update_task_progress
            self.allowed_tool_names = kwargs.get("allowed_tool_names")
            self.allow_patch_fallback = kwargs.get("allow_patch_fallback")
            return []

    class _Adapter:
        workspace = str(tmp_path)
        _update_task_progress = staticmethod(lambda *args, **kwargs: None)

        def __init__(self) -> None:
            self._execution = _Execution()
            self.retry_message = ""
            self.retry_context: dict[str, Any] = {}

        async def _invoke_role_dialogue_with_timeout(
            self,
            message: str,
            *,
            context: dict[str, Any],
            timeout_seconds: float,
            stage_label: str,
        ) -> dict[str, Any]:
            del timeout_seconds, stage_label
            self.retry_message = message
            self.retry_context = context
            return {"content": "calculator.py\n```python\npass\n```", "success": True}

        async def _execute_tools(
            self,
            content: str,
            target_task_id: str,
            **kwargs: Any,
        ) -> list[dict[str, Any]]:
            return await self._execution.execute_tools(
                content,
                target_task_id,
                self._update_task_progress,
                **kwargs,
            )

    adapter = _Adapter()

    _, summary = await _run_empty_write_content_materialization_retry(
        adapter,
        task={"target_files": ["calculator.py"]},
        target_task_id="PM-0001-1-S1-fill2",
        context={"run_id": "run-existing-empty-write"},
        original_message="[mode:materialize]\n范围: calculator.py",
        tool_results=[{"tool_name": "write_file", "arguments": {"file": "calculator.py", "content": ""}}],
        llm_call_timeout=10,
    )

    assert adapter.retry_context["_transaction_kernel_forced_tool_choice"] == {
        "type": "function",
        "function": {"name": "edit_blocks"},
    }
    forced_defs = adapter.retry_context["_transaction_kernel_forced_tool_definitions"]
    assert forced_defs and forced_defs[0]["function"]["name"] == "edit_blocks"
    assert adapter.retry_context["director_empty_write_retry"]["write_only_single_target"] == {
        "tool": "edit_blocks",
        "target_file": "calculator.py",
    }
    assert "valid edit_blocks tool call" in adapter.retry_message
    assert "valid write_file tool call" not in adapter.retry_message
    assert adapter._execution.allowed_tool_names == {"edit_blocks"}
    assert adapter._execution.allow_patch_fallback is False
    assert summary["attempted"] is True


def _source_tools_from_tool_results(tool_results: Any) -> list[str]:
    source_tools: list[str] = []
    if not isinstance(tool_results, list):
        return source_tools
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        raw_result = item.get("result")
        if not isinstance(raw_result, dict):
            continue
        source_tools.append(str(raw_result.get("source_tool") or ""))
    return source_tools


def test_validate_generated_output_allows_todo_status_enum_value(tmp_path: Any) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    target = tmp_path / "src" / "models" / "task.model.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export enum TaskStatus {\n"
        "  Todo = 'TODO',\n"
        "  InProgress = 'IN_PROGRESS',\n"
        "  Done = 'DONE',\n"
        "}\n\n"
        "export interface Task {\n"
        "  id: string;\n"
        "  tenant_id: string;\n"
        "  status: TaskStatus;\n"
        "  version: number;\n"
        "}\n",
        encoding="utf-8",
    )

    error = executor.validate_generated_output(
        {
            "subject": "Task model version status",
            "description": "Implement tenant task status and version model",
        },
        ["src/models/task.model.ts"],
    )

    assert error is None


def test_validate_generated_output_rejects_todo_comment(tmp_path: Any) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    target = tmp_path / "src" / "models" / "task.model.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "// TODO implement task versioning\nexport interface Task {\n  tenant_id: string;\n  version: number;\n}\n",
        encoding="utf-8",
    )

    error = executor.validate_generated_output(
        {
            "subject": "Task model version status",
            "description": "Implement tenant task status and version model",
        },
        ["src/models/task.model.ts"],
    )

    assert error is not None
    assert "generic/placeholder content detected" in error


def test_validate_generated_output_allows_title_case_todo_product_heading(tmp_path: Any) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Todo API\n\n"
        "This FastAPI service implements authenticated todo creation, listing, completion, and deletion.\n",
        encoding="utf-8",
    )
    test_file = tmp_path / "tests" / "test_e2e.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "# Todo workflow coverage\ndef test_todo_create_and_complete_flow():\n    assert 'todo'.upper() == 'TODO'\n",
        encoding="utf-8",
    )

    error = executor.validate_generated_output(
        {
            "subject": "Todo API integration verification",
            "description": "Verify todo create and complete workflow with README instructions",
        },
        ["README.md", "tests/test_e2e.py"],
    )

    assert error is None


def test_validate_generated_output_uses_target_path_as_domain_signal_for_config(tmp_path: Any) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    config = tmp_path / "tsconfig.json"
    config.write_text(
        "{\n"
        '  "compilerOptions": {\n'
        '    "target": "ES2022",\n'
        '    "module": "ES2022",\n'
        '    "rootDir": "src",\n'
        '    "outDir": "dist",\n'
        '    "strict": true\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    error = executor.validate_generated_output(
        {
            "subject": "Create tsconfig.json",
            "description": "test -f tsconfig.json && npx tsc --noEmit",
        },
        ["tsconfig.json"],
    )

    assert error is None


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------


class TestSelectExecutionStrategy:
    """_select_execution_strategy is a pure function of directive + task + context."""

    def test_architect_concern_triggers_conservative(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        context = {
            "metadata": {
                "architect_constraints": [{"type": "concern", "detail": "risky"}],
            }
        }
        result = adapter._select_execution_strategy("do something", {}, context)
        assert result == "conservative"

    def test_large_scope_and_complex_directive_triggers_incremental(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = "x" * 301
        task = {"target_files": ["a"] * 5, "scope_paths": ["b"] * 6}
        result = adapter._select_execution_strategy(directive, task, {})
        assert result == "incremental"

    def test_refactor_triggers_conservative(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._select_execution_strategy("refactor the module", {}, {})
        assert result == "conservative"

    def test_verify_triggers_focused(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._select_execution_strategy("verify the test suite", {}, {})
        assert result == "focused"

    def test_medium_scope_and_complex_triggers_aggressive(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = "x" * 301
        task = {"target_files": ["a"] * 3, "scope_paths": ["b"] * 3}
        result = adapter._select_execution_strategy(directive, task, {})
        assert result == "aggressive"

    def test_simple_directive_returns_default(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._select_execution_strategy("fix bug", {}, {})
        assert result == "default"

    def test_refactor_zh_triggers_conservative(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._select_execution_strategy("重构代码", {}, {})
        assert result == "conservative"


class TestDirectorAdapterCognitiveRuntimeReceipt:
    """Director materialization must leave Cognitive Runtime evidence."""

    def test_role_runtime_metadata_requires_context_os_and_repo_intelligence(self) -> None:
        metadata = DirectorAdapter._build_role_runtime_metadata(
            {
                "run_id": "run-1",
                "task_id": "TASK-1",
                "metadata": {"source": "caller"},
            },
            max_retries=2,
        )

        assert metadata["role_runtime_required"] is True
        assert metadata["cognitive_runtime_required"] is True
        assert metadata["context_os_expected"] is True
        assert metadata["use_repo_intelligence"] is True
        assert metadata["repo_intel_max_files"] == 20
        assert metadata["repo_intel_max_symbols"] == 40
        assert metadata["run_id"] == "run-1"
        assert metadata["task_id"] == "TASK-1"
        assert metadata["source"] == "caller"
        assert metadata["cognitive_runtime_approval_mode"] == "auto_accept"
        assert metadata["cognitive_runtime_approval"] == {
            "mode": "auto_accept",
            "source": "roles.adapters.director",
            "scope": "director_execution_preflight",
            "approved_by": "director_adapter",
        }

    @pytest.mark.asyncio
    async def test_role_runtime_session_promotes_metadata_tool_receipts(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import polaris.cells.roles.runtime.public.service as runtime_service_module

        adapter = _make_adapter(tmp_path)
        receipt = {
            "results": [
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "result": {"path": "src/app.ts"},
                }
            ]
        }
        tool_results = [
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {"path": "src/app.ts"},
            }
        ]

        class FakeRuntimeService:
            async def execute_role_session(self, command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
                assert command.role == "director"
                assert command.stream is False
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role="director",
                    workspace=str(tmp_path),
                    session_id=command.session_id,
                    task_id=command.task_id,
                    run_id=command.run_id,
                    output="done",
                    tool_calls=("write_file",),
                    metadata={
                        "batch_receipt": receipt,
                        "tool_results": tool_results,
                    },
                )

        monkeypatch.setattr(runtime_service_module, "RoleRuntimeService", FakeRuntimeService)

        result = await adapter._invoke_role_runtime_session(
            "write src/app.ts",
            context={"task_id": "TASK-1", "run_id": "RUN-1"},
            max_retries=1,
        )

        assert result["success"] is True
        assert result["batch_receipt"] == receipt
        assert result["tool_results"] == tool_results
        assert result["raw_response"]["batch_receipt"] == receipt

    def test_emit_cognitive_runtime_receipt_records_and_exports_handoff(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        class _Service:
            def record_runtime_receipt(self, command: Any) -> Any:
                captured["receipt_command"] = command
                return SimpleNamespace(ok=True, receipt=SimpleNamespace(receipt_id="receipt-1"))

            def export_handoff_pack(self, command: Any) -> None:
                captured["handoff_command"] = command

            def close(self) -> None:
                captured["closed"] = True

        service = _Service()
        monkeypatch.setattr(
            "polaris.cells.factory.cognitive_runtime.public.service.get_cognitive_runtime_public_service",
            lambda: service,
        )
        adapter = SimpleNamespace(workspace=str(tmp_path))

        receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task={"metadata": {"session_id": "session-1"}},
            target_task_id="TASK-1",
            run_id="run-1",
            context={"metadata": {"turn_envelope": {"turn_id": "turn-1"}}},
            receipt_type="director_adapter_materialization_completed",
            payload={"status": "completed", "changed_files": ["src/app.py"]},
            export_handoff=True,
        )

        assert receipt["ok"] is True
        assert receipt["receipt_id"] == "receipt-1"
        receipt_command = captured["receipt_command"]
        assert receipt_command.receipt_type == "director_adapter_materialization_completed"
        assert receipt_command.session_id == "session-1"
        assert receipt_command.run_id == "run-1"
        assert receipt_command.payload["source"] == "roles.adapters.director"
        assert receipt_command.payload["context_os_expected"] is True
        assert receipt_command.payload["changed_files"] == ["src/app.py"]
        handoff_command = captured["handoff_command"]
        assert handoff_command.session_id == "session-1"
        assert handoff_command.turn_envelope["receipt_ids"] == ["receipt-1"]
        assert captured["closed"] is True


# ---------------------------------------------------------------------------
# Intelligent correction
# ---------------------------------------------------------------------------


class TestApplyIntelligentCorrection:
    """_apply_intelligent_correction analyzes failure patterns."""

    def test_success_returns_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._apply_intelligent_correction({"success": True}, [])
        assert result["success"] is True
        assert "_correction_hints" not in result

    def test_timeout_pattern_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [
            {"error": "LLM timeout"},
            {"error": "timeout after 30s"},
        ]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert "_correction_hints" in result
        assert any("smaller steps" in h for h in result["_correction_hints"])

    def test_syntax_error_pattern_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [
            {"error": "SyntaxError"},
            {"error": "语法错误"},
        ]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert any("syntax" in h.lower() for h in result["_correction_hints"])

    def test_missing_dependency_pattern_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [
            {"error": "module not found"},
            {"error": "找不到文件"},
        ]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert any("dependencies" in h.lower() for h in result["_correction_hints"])

    def test_permission_pattern_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [
            {"error": "permission denied"},
            {"error": "权限不足"},
        ]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert any("permissions" in h.lower() for h in result["_correction_hints"])

    def test_single_failure_no_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [{"error": "timeout"}]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert "_correction_hints" not in result


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------


class TestBuildDirectorMessage:
    """_build_director_message constructs prompt text deterministically."""

    def test_includes_subject(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "Fix login", "description": "Bug in auth"})
        assert "任务: Fix login" in msg
        assert "文本文件块格式" in msg

    def test_sanitizes_description(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "T", "description": "# Header\n\nBody line"})
        assert "描述:" in msg

    def test_empty_description_omitted(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "T", "description": ""})
        # The line "描述: " with empty content should still appear because implementation
        # does not filter it out; we just assert no crash.
        assert "任务: T" in msg

    def test_uses_real_scope_instead_of_placeholder_path(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Scaffold app",
                "metadata": {
                    "goal": "Create a Vite app",
                    "scope": "package.json, src/main.tsx",
                    "steps": ["Create package manifest"],
                    "acceptance": ["npm test passes"],
                },
            }
        )
        assert "范围: package.json, src/main.tsx" in msg
        assert "- Create package manifest" in msg
        assert "- npm test passes" in msg
        assert "path/to/file.py" not in msg

    def test_includes_pm_contract_paths_checklist_and_acceptance(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement Three.js client scene",
                "description": "Implement the client3d task",
                "metadata": {
                    "goal": "Add the missing client3d capability",
                    "scope_paths": ["src/client/three-scene.ts"],
                    "target_files": ["src/client/three-scene.ts"],
                    "execution_checklist": ["Modify the existing Three.js scene file"],
                    "acceptance_criteria": ["Run `npm run build` passes"],
                },
            }
        )

        assert "范围: src/client/three-scene.ts" in msg
        assert "目标文件: src/client/three-scene.ts" in msg
        assert "- Modify the existing Three.js scene file" in msg
        assert "- Run `npm run build` passes" in msg

    def test_includes_ce_blueprint_and_factory_context(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement firefly garden simulator",
                "metadata": {
                    "goal": "Create the L1-01 simulator artifacts",
                    "scope_paths": ["src/engine/SimulationEngine.ts"],
                    "target_files": ["src/engine/SimulationEngine.ts"],
                    "execution_checklist": ["Write the simulation engine"],
                    "acceptance_criteria": ["npm run build passes"],
                },
            },
            context={
                "blueprint_id": "bp-L1-01-4",
                "construction_step": {
                    "target_file": "src/engine/SimulationEngine.ts",
                    "signatures": ["class SimulationEngine", "runSimulation()"],
                    "verify": "npm run build",
                },
                "metadata": {
                    "factory_bench_project_id": "L1-01",
                    "factory_bench_title": "发光昆虫花园模拟器",
                },
            },
        )

        assert "PM Task Contract / 任务合同:" in msg
        assert "Acceptance criteria / 验收标准:" in msg
        assert "Chief Engineer Blueprint / CE 蓝图交接:" in msg
        assert "- blueprint_id: bp-L1-01-4" in msg
        assert "- construction target: src/engine/SimulationEngine.ts" in msg
        assert "- construction signatures: class SimulationEngine; runSimulation()" in msg
        assert "- construction verify: npm run build" in msg
        assert "- factory bench project: L1-01 - 发光昆虫花园模拟器" in msg

    def test_message_requires_unittest_and_contract_scoped_python_tests(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement calculator tests",
                "metadata": {
                    "scope_paths": ["calculator.py", "tests/test_calculator.py"],
                    "target_files": ["calculator.py", "tests/test_calculator.py"],
                    "execution_checklist": ["Add calculator regression tests"],
                    "acceptance_criteria": ["2+3*4 returns 14", "10/0 is rejected"],
                },
            }
        )

        assert "标准库 unittest" in msg
        assert "python -m unittest discover -s tests -p 'test_*.py' -v" in msg
        assert "至少发现并运行 1 个测试" in msg
        assert "不得新增合同外功能断言" in msg
        assert "未声明第三方测试依赖" in msg

    def test_includes_qa_rework_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Fix QA findings",
                "metadata": {
                    "qa_rework_reason": "placeholder_content_detected",
                    "qa_rework_evidence": [
                        "src/backend/fashiongen_worker.py:\\bplaceholder\\b",
                        "src/main/providers.ts:\\bplaceholder\\b",
                    ],
                },
            }
        )

        assert "QA 返工要求" in msg
        assert "placeholder_content_detected" in msg
        assert "src/backend/fashiongen_worker.py" in msg
        assert "src/main/providers.ts" in msg


class TestDirectorFailureClosure:
    """Runtime failures must fail the claimed task instead of leaving it running."""

    def test_finalize_claimed_execution_reports_terminal_transition_failure(self) -> None:
        class _Runtime:
            def complete_execution(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                del args, kwargs
                raise RuntimeError("Cannot transition task from 'failed' to 'completed'")

        adapter = SimpleNamespace(task_runtime=_Runtime())

        finalize_result = _finalize_claimed_execution(
            adapter,
            target_task_id="task-1",
            session_id="session-1",
            outcome="completed",
            result_summary="done",
            metadata={"adapter_phase": "completed"},
        )
        result = _task_runtime_finalization_failed_result(
            target_task_id="task-1",
            requested_outcome="completed",
            finalize_result=finalize_result,
        )

        assert finalize_result["success"] is False
        assert finalize_result["reason"] == "task_runtime_terminal_transition_failed"
        assert result["success"] is False
        assert result["error_code"] == "director_task_runtime_finalization_failed"
        assert result["root_cause_hint"] == "task_runtime_terminal_transition_failed"

    def test_role_response_normalization_keeps_kernel_errors_failed(self) -> None:
        result = _normalize_director_role_response(
            {
                "response": "[ROLE_EXECUTION_ERROR] provider failed",
                "success": True,
                "provider": "anthropic_compat-test",
                "model": "kimi-for-coding",
            }
        )

        assert result["success"] is False
        assert "provider failed" in result["error"]
        assert result["provider"] == "anthropic_compat-test"
        assert result["model"] == "kimi-for-coding"

    def test_role_response_normalization_preserves_batch_receipt(self) -> None:
        receipt = {
            "results": [
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "result": {"path": "src/app.ts"},
                }
            ]
        }
        result = _normalize_director_role_response(
            {
                "response": "done",
                "provider": "anthropic_compat-test",
                "model": "kimi-for-coding",
                "batch_receipt": receipt,
            }
        )

        assert result["success"] is True
        assert result["batch_receipt"] == receipt

    def test_role_response_normalization_promotes_runtime_metadata_receipts(self) -> None:
        receipt = {
            "results": [
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "result": {"path": "src/app.ts"},
                }
            ]
        }
        tool_results = [
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {"path": "src/app.ts"},
            }
        ]

        result = _normalize_director_role_response(
            {
                "response": "done",
                "success": True,
                "metadata": {
                    "batch_receipt": receipt,
                    "tool_results": tool_results,
                },
            }
        )

        assert result["success"] is True
        assert result["batch_receipt"] == receipt
        assert result["tool_results"] == tool_results

    def test_direct_text_patch_flag_resolves_from_context(self, tmp_path: Any) -> None:
        del tmp_path
        assert _director_direct_text_patch_only_enabled({"director_direct_text_patch_only": "true"}) is True
        assert _director_direct_text_patch_only_enabled({"director_direct_text_patch_only": "0"}) is False

    def test_existing_scope_preflight_defaults_enabled_and_can_disable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KERNELONE_DIRECTOR_EXISTING_SCOPE_PREFLIGHT", raising=False)
        assert _director_existing_scope_preflight_enabled({}) is True
        assert _director_existing_scope_preflight_enabled({"director_existing_scope_preflight": "off"}) is False

    @pytest.mark.asyncio
    async def test_role_dialogue_runtime_error_returns_failed_payload(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        async def _boom_dialogue(message: str, *, context: dict[str, Any] | None) -> dict[str, Any]:
            del message, context
            raise RuntimeError("kernel contract retry failed")

        adapter._invoke_role_dialogue = _boom_dialogue  # type: ignore[method-assign]

        result = await adapter._invoke_role_dialogue_with_timeout(
            "write files",
            context={},
            timeout_seconds=1.0,
            stage_label="unit",
        )

        assert result["success"] is False
        assert "kernel contract retry failed" in str(result.get("error") or "")

    @pytest.mark.asyncio
    async def test_execute_fails_claimed_task_on_unhandled_runtime_error(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="实现核心模块",
            description="创建文件",
            metadata={"scope": "src/core.ts", "steps": ["写入核心文件"]},
        )

        async def _boom_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise RuntimeError("director kernel exploded")

        adapter._invoke_role_dialogue_with_timeout = _boom_call  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-fail-closed"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director.runtime.exception"
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "failed"

    @pytest.mark.asyncio
    async def test_execute_rejects_workspace_diff_without_write_tool_receipt(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Repair failing TypeScript test",
            description="Apply the smallest code change and verify npm test behavior.",
            metadata={
                "scope": "src/types/domain.ts",
                "steps": ["Update the domain type contract"],
                "acceptance": ["The TypeScript test failure is repaired"],
            },
        )
        captured: dict[str, Any] = {}

        async def _mutating_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            captured["context"] = kwargs.get("context")
            target = tmp_path / "src" / "types" / "domain.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("export type DomainState = 'ready';\n", encoding="utf-8")
            return {"content": "Applied directly by runtime provider.", "success": True}

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after ambiguous workspace diff evidence")

        adapter._invoke_role_dialogue_with_timeout = _mutating_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-diff-evidence"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_missing_write_receipt"
        assert result["materialization_mode"] == "workspace_diff_without_write_tool"
        assert captured["context"]["run_id"] == "run-director-diff-evidence"
        assert any(
            signal.get("code") == "director_missing_write_receipt"
            for signal in result.get("decision_signals", [])
            if isinstance(signal, dict)
        )
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "failed"
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("new_file_count") == 1
        assert adapter_result.get("write_tool_evidence") is False
        assert adapter_result.get("materialization_error") == "director_missing_write_receipt"

    @pytest.mark.asyncio
    async def test_execute_rejects_off_target_workspace_diff_as_materialization(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Implement browser networking client",
            description="Update the declared network client target file.",
            metadata={
                "target_files": ["src/client/network-client.ts"],
                "scope_paths": ["src"],
                "steps": ["Implement src/client/network-client.ts"],
                "acceptance": ["src/client/network-client.ts is changed"],
            },
        )

        async def _off_target_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            off_target = tmp_path / "src" / "server" / "moderation.ts"
            off_target.parent.mkdir(parents=True, exist_ok=True)
            off_target.write_text("export const moderationReady = true;\n", encoding="utf-8")
            return {"content": "Changed a different file.", "success": True}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _off_target_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-off-target-diff"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_materialized_out_of_scope"
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("new_files") == []
        assert adapter_result.get("modified_files") == []

    @pytest.mark.asyncio
    async def test_execute_keeps_failed_no_write_separate_from_sibling_diff(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Implement garden simulator module",
            description="Update only the declared garden module.",
            metadata={
                "target_files": ["src/garden.ts"],
                "scope_paths": ["src/garden.ts"],
                "steps": ["Implement src/garden.ts"],
                "acceptance": ["src/garden.ts is changed"],
            },
        )

        async def _failed_dialogue_with_sibling_diff(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            sibling = tmp_path / "scripts" / "verify.js"
            sibling.parent.mkdir(parents=True, exist_ok=True)
            sibling.write_text("console.log('sibling task changed this file');\n", encoding="utf-8")
            return {"content": "", "success": False, "error": "single_batch_contract_violation"}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _failed_dialogue_with_sibling_diff  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-sibling-diff"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_no_materialized_changes"
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_no_materialized_changes"
        assert adapter_result.get("out_of_scope_files") == ["scripts/verify.js"]

    def test_no_materialized_changes_ignores_sibling_diff_after_failed_write_tool(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            MaterializationState,
            _phase_no_materialized_changes,
        )

        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Bootstrap package manifest",
            description="Create only package.json.",
            metadata={"target_files": ["package.json"], "scope_paths": ["package.json"]},
        )
        state = MaterializationState(
            current_files={"tsconfig.json": "sibling-fingerprint"},
            new_files=[],
            modified_files=[],
            all_affected_files=[],
            tool_results=[
                {
                    "tool": "write_file",
                    "success": False,
                    "args": {"file": "package.json", "content": ""},
                    "error": "Director write policy denied",
                }
            ],
        )

        result = _phase_no_materialized_changes(
            adapter,
            baseline_files={},
            board_claim_applied=False,
            can_accept_existing_scope=False,
            context={},
            direct_fallback_summary=None,
            empty_write_content_retry_summary=None,
            existing_contract_evidence={},
            primary_llm_summary={"success": True},
            requires_fresh_materialization=True,
            run_id="run-failed-write-sibling-diff",
            target_task_id=str(task.id),
            task={"target_files": ["package.json"], "scope_paths": ["package.json"]},
            task_claim_session_id="",
            workspace_name=tmp_path.name,
            write_tool_evidence=False,
            state=state,
        )

        assert result is not None
        assert result["success"] is False
        assert result["error_code"] == "director_no_materialized_changes"

    @pytest.mark.asyncio
    async def test_execute_fails_when_changed_test_file_keeps_placeholder_arithmetic(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        test_file = tmp_path / "tests" / "unit" / "card-rules.test.ts"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(
            "\n".join(f"test('case {idx}', () => expect({idx} + 1).toBe({idx + 1}));" for idx in range(4)) + "\n",
            encoding="utf-8",
        )
        task = adapter.task_board.create(
            subject="Replace placeholder Card3D unit tests",
            description="Remove trivial arithmetic placeholder tests and replace them with domain assertions.",
            metadata={
                "target_files": ["tests/unit/card-rules.test.ts"],
                "steps": ["Replace or remove existing trivial arithmetic placeholder tests"],
                "acceptance": ["No trivial arithmetic placeholder tests remain"],
            },
        )

        async def _append_only_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            with test_file.open("a", encoding="utf-8") as handle:
                handle.write("test('domain rule', () => expect(resolveCardRule()).toBeDefined());\n")
            return {
                "content": "Appended replacement tests.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "tests/unit/card-rules.test.ts"},
                    }
                ],
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after workspace diff evidence")

        adapter._invoke_role_dialogue_with_timeout = _append_only_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-artifact-quality"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_materialization_quality_failed"
        assert any("tests/unit/card-rules.test.ts" in item for item in result["artifact_quality_errors"])
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "failed"
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_materialization_quality_failed"

    @pytest.mark.asyncio
    async def test_execute_repairs_npm_default_failing_test_script_before_failing_quality_gate(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Build web e2e testing workspace",
            description="Create a runnable web e2e workspace with source code and tests.",
            metadata={
                "target_files": [
                    "package.json",
                    "src/index.js",
                    "tests/index.test.js",
                    "scripts/test.mjs",
                ],
                "scope_paths": ["package.json", "src", "tests", "scripts"],
                "steps": ["Create package scripts", "Create source module", "Create executable tests"],
                "acceptance": ["npm test exits 0 and exercises the web e2e source module"],
            },
        )
        stage_labels: list[str] = []

        async def _gemma_like_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            package_json = tmp_path / "package.json"
            package_json.write_text(
                """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"Error: no test specified\\" && exit 1",
    "start": "node src/index.js"
  }
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            if stage_labels[-1] == "quality_repair":
                package_json.write_text(
                    """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs",
    "start": "node src/index.js"
  }
}
""".strip()
                    + "\n",
                    encoding="utf-8",
                )
                src = tmp_path / "src" / "index.js"
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_text(
                    "export function createWebE2eStatus() {\n  return { name: 'web-e2e-workspace', ready: true };\n}\n",
                    encoding="utf-8",
                )
                tests = tmp_path / "tests" / "index.test.js"
                tests.parent.mkdir(parents=True, exist_ok=True)
                tests.write_text(
                    "import { createWebE2eStatus } from '../src/index.js';\n"
                    "export function runWebE2eChecks() {\n"
                    "  const status = createWebE2eStatus();\n"
                    "  if (!status.ready) throw new Error('web e2e status not ready');\n"
                    "}\n",
                    encoding="utf-8",
                )
                script = tmp_path / "scripts" / "test.mjs"
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text(
                    "import { runWebE2eChecks } from '../tests/index.test.js';\n"
                    "runWebE2eChecks();\n"
                    "console.log('web e2e checks passed');\n",
                    encoding="utf-8",
                )
                changed = [
                    "package.json",
                    "src/index.js",
                    "tests/index.test.js",
                    "scripts/test.mjs",
                ]
            else:
                changed = ["package.json"]
            return {
                "content": "Wrote workspace files.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": path},
                    }
                    for path in changed
                ],
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after authoritative write evidence")

        adapter._invoke_role_dialogue_with_timeout = _gemma_like_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-package-quality-repair"},
        )

        assert result["success"] is True
        assert stage_labels == ["first_call", "quality_repair"]
        assert result["tools_executed"] >= 5
        assert "package.json" in result["changed_files"]
        assert "Error: no test specified" not in (tmp_path / "package.json").read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_execute_does_not_repair_npm_default_test_script_with_manifest_only_check(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Create package manifest",
            description="Create a package.json with a runnable local test script.",
            metadata={
                "target_files": ["package.json"],
                "scope_paths": ["package.json"],
                "steps": ["Create package manifest"],
                "acceptance": ["npm test runs a local package manifest check"],
            },
        )
        stage_labels: list[str] = []

        async def _bad_package_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            package_json = tmp_path / "package.json"
            package_json.write_text(
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
            return {
                "content": "Wrote package manifest.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "package.json"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _bad_package_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-package-deterministic-test-script-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        assert result["success"] is False
        assert stage_labels[0] == "first_call"
        assert "Error: no test specified" in package_text
        assert "package manifest check passed" not in package_text

    @pytest.mark.asyncio
    async def test_execute_does_not_repair_invalid_npm_test_script_with_manifest_only_check(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Create package manifest",
            description="Create a package.json with a syntactically valid npm test script.",
            metadata={
                "target_files": ["package.json"],
                "scope_paths": ["package.json"],
                "steps": ["Create package manifest"],
                "acceptance": ["npm test parses and exits 0"],
            },
        )
        stage_labels: list[str] = []

        async def _bad_package_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            (tmp_path / "package.json").write_text(
                """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node -e \\"console.log('unterminated npm script')"
  }
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote package manifest.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "package.json"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _bad_package_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-package-invalid-script-deterministic-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])
        assert result["success"] is False
        assert stage_labels[0] == "first_call"
        assert quality_errors
        assert "unterminated npm script" in package_text
        assert "package manifest check passed" not in package_text

    @pytest.mark.asyncio
    async def test_execute_repairs_typescript_return_object_property_semicolon(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Create task model summary",
            description="Create a task model summary function with valid TypeScript syntax.",
            metadata={
                "target_files": ["src/models/task.ts"],
                "scope_paths": ["src/models/task.ts"],
                "steps": ["Create task model"],
                "acceptance": ["src/models/task.ts typechecks"],
            },
        )

        async def _bad_typescript_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
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
            return {
                "content": "Wrote task model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/task.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _bad_typescript_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-typescript-return-object-semicolon-repair"},
        )

        repaired = (tmp_path / "src" / "models" / "task.ts").read_text(encoding="utf-8")
        assert result["success"] is True
        assert "    lanes,\n" in repaired
        assert "    lanes;\n" not in repaired
        assert "src/models/task.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_declares_runtime_dependency_when_quality_repair_repeats_undeclared_import(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "tenant-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {}
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        _write_substantive_node_test_script(tmp_path)
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Define tenant model",
            description="Create the tenant model with runtime imports declared in package.json.",
            metadata={
                "target_files": ["src/models/tenant.model.ts"],
                "scope_paths": ["src/models/tenant.model.ts", "package.json"],
                "steps": ["Create tenant model"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )
        stage_labels: list[str] = []

        async def _repeating_gemma_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            target = tmp_path / "src" / "models" / "tenant.model.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { Entity, OneToMany, PrimaryColumn } from 'typeorm';\n"
                "@Entity('tenants')\n"
                "export class TenantModel {\n"
                "  @PrimaryColumn()\n"
                "  id: string;\n"
                "\n"
                "  @OneToMany(() => Task, (task) => task.tenant)\n"
                "  tasks: Task[];\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote tenant model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/tenant.model.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _repeating_gemma_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-undeclared-import-deterministic-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        tenant_text = (tmp_path / "src" / "models" / "tenant.model.ts").read_text(encoding="utf-8")
        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert '"typeorm":' in package_text
        assert "from 'typeorm'" not in tenant_text
        assert "@Entity" not in tenant_text
        assert "tasks: unknown[] = [];" in tenant_text
        assert "package.json" in result["changed_files"]
        assert "src/models/tenant.model.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_declares_mongoose_runtime_dependency_for_audit_log_model(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "tenant-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {}
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        _write_substantive_node_test_script(tmp_path)
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Tenant Context & Audit Log Middleware",
            description="Implement immutable audit log model with tenant context.",
            metadata={
                "target_files": ["src/models/auditlog.ts"],
                "scope_paths": ["src/models/auditlog.ts", "package.json"],
                "steps": ["Create audit log model"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )
        stage_labels: list[str] = []

        async def _mongoose_audit_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            target = tmp_path / "src" / "models" / "auditlog.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { Schema, model, Document } from 'mongoose';\n\n"
                "export interface IAuditLog extends Document {\n"
                "  actor_id: string;\n"
                "  tenant_id: string;\n"
                "  action: 'CREATE' | 'UPDATE' | 'DELETE';\n"
                "  target_entity: string;\n"
                "  delta: Record<string, unknown>;\n"
                "  timestamp: Date;\n"
                "}\n\n"
                "const AuditLogSchema = new Schema<IAuditLog>({\n"
                "  actor_id: { type: String, required: true },\n"
                "  tenant_id: { type: String, required: true },\n"
                "  action: { type: String, enum: ['CREATE', 'UPDATE', 'DELETE'], required: true },\n"
                "  target_entity: { type: String, required: true },\n"
                "  delta: { type: Object, required: true },\n"
                "  timestamp: { type: Date, default: Date.now },\n"
                "});\n\n"
                "export const AuditLog = model<IAuditLog>('AuditLog', AuditLogSchema);\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote audit log model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/auditlog.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _mongoose_audit_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-mongoose-runtime-dependency-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/models/auditlog.ts", "package.json"],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert quality_errors == []
        assert '"mongoose":' in package_text
        assert "deterministic_runtime_dependency_repair" in source_tools
        assert "package.json" in result["changed_files"]
        assert "src/models/auditlog.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_declares_uuid_and_winston_runtime_dependencies_for_audit_log(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "workflow-audit-service",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {}
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        _write_substantive_node_test_script(tmp_path)
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Immutable Audit Logging Implementation",
            description="Create a TypeScript audit log service with stable event IDs and structured logging.",
            metadata={
                "target_files": ["src/services/auditlog.ts"],
                "scope_paths": ["src/services/auditlog.ts", "package.json"],
                "steps": ["Create the audit log service"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )
        stage_labels: list[str] = []

        async def _audit_log_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            target = tmp_path / "src" / "services" / "auditlog.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { v4 as uuidv4 } from 'uuid';\n"
                "import winston from 'winston';\n\n"
                "export interface AuditEvent {\n"
                "  id: string;\n"
                "  action: string;\n"
                "  targetId: string;\n"
                "  createdAt: string;\n"
                "}\n\n"
                "const logger = winston.createLogger({\n"
                "  transports: [new winston.transports.Console()],\n"
                "});\n\n"
                "export function recordAuditEvent(action: string, targetId: string): AuditEvent {\n"
                "  const event = { id: uuidv4(), action, targetId, createdAt: new Date().toISOString() };\n"
                "  logger.info('audit.event', event);\n"
                "  return event;\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote audit log service.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/services/auditlog.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _audit_log_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-audit-log-runtime-dependency-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/services/auditlog.ts", "package.json"],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert quality_errors == []
        assert '"uuid":' in package_text
        assert '"winston":' in package_text
        assert "deterministic_runtime_dependency_repair" in source_tools
        assert "package.json" in result["changed_files"]
        assert "src/services/auditlog.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_tenant_middleware_escaped_newline_and_node_types(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "tenant-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {
    "express": "^4.18.2"
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        _write_substantive_node_test_script(tmp_path)
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Tenant Context Middleware",
            description="Create request-scoped tenant context middleware for an Express service.",
            metadata={
                "target_files": ["src/middleware/auth.ts"],
                "scope_paths": ["src/middleware/auth.ts", "package.json"],
                "steps": ["Create tenant middleware"],
                "acceptance": ["TypeScript exports remain reachable and Node builtin typings are declared"],
            },
        )
        stage_labels: list[str] = []

        async def _gemma_escaped_newline_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            target = tmp_path / "src" / "middleware" / "auth.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { Request, Response, NextFunction } from 'express';\n"
                "import { AsyncLocalStorage } from 'async_hooks';\n\n"
                "export interface TenantContext {\n"
                "  tenantId: string;\n"
                "}\n\n"
                "// Context for storing tenant information across the request lifecycle\\n"
                "export const tenantContext = new AsyncLocalStorage<TenantContext>();\n\n"
                "export function tenantMiddleware(req: Request, res: Response, next: NextFunction): void {\n"
                "  const tenantId = String(req.headers['x-tenant-id'] || 'default');\n"
                "  tenantContext.run({ tenantId }, () => next());\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote tenant middleware.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/middleware/auth.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _gemma_escaped_newline_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-tenant-middleware-escaped-newline-repair"},
        )

        package_payload = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        repaired = (tmp_path / "src" / "middleware" / "auth.ts").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/middleware/auth.ts", "package.json"],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert "lifecycle\\nexport const tenantContext" not in repaired
        assert "\nexport const tenantContext" in repaired
        assert package_payload["devDependencies"]["@types/node"] == "^22.10.0"
        assert quality_errors == []
        assert "deterministic_typescript_escaped_newline_repair" in source_tools
        assert "deterministic_runtime_dependency_repair" in source_tools
        assert "src/middleware/auth.ts" in result["changed_files"]
        assert "package.json" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_zod_type_class_name_collision(
        self,
        tmp_path: Any,
    ) -> None:
        (tmp_path / "package.json").write_text(
            """
{
  "name": "task-definition-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {
    "zod": "^3.23.8"
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        _write_substantive_node_test_script(tmp_path)
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Task Definition Model",
            description="Create zod-backed task definition model.",
            metadata={
                "target_files": ["src/models/task_definition.ts"],
                "scope_paths": ["src/models/task_definition.ts", "package.json"],
                "steps": ["Create task definition schema and model"],
                "acceptance": ["TypeScript typecheck accepts schema and class exports"],
            },
        )

        async def _zod_collision_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "models" / "task_definition.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { z } from 'zod';\n\n"
                "export const TaskDefinitionSchema = z.object({\n"
                "  id: z.string().uuid().optional(),\n"
                "  name: z.string().min(1),\n"
                "});\n\n"
                "type TaskDefinition = z.infer<typeof TaskDefinitionSchema>;\n\n"
                "export class TaskDefinition {\n"
                "  constructor(public data: TaskDefinition) {}\n\n"
                "  static validate(data: any): TaskDefinition {\n"
                "    const result = TaskDefinitionSchema.safeParse(data);\n"
                "    if (!result.success) throw new Error('Validation failed');\n"
                "    return new TaskDefinition(result.data);\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote task definition model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/task_definition.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _zod_collision_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-zod-type-class-collision-repair"},
        )

        repaired = (tmp_path / "src" / "models" / "task_definition.ts").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/models/task_definition.ts", "package.json"],
        )
        source_tools = _source_tools_from_tool_results(result["tool_results"])

        assert result["success"] is True, result
        assert "type TaskDefinitionData = z.infer<typeof TaskDefinitionSchema>;" in repaired
        assert "constructor(public data: TaskDefinitionData)" in repaired
        assert quality_errors == []
        assert "deterministic_typescript_zod_type_class_collision_repair" in source_tools
        assert "src/models/task_definition.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_missing_declared_target_from_nearby_existing_module(
        self,
        tmp_path: Any,
    ) -> None:
        existing_task = tmp_path / "src" / "models" / "task.ts"
        existing_task.parent.mkdir(parents=True, exist_ok=True)
        existing_task.write_text(
            "export interface TaskModel {\n  id: string;\n  tenantId: string;\n  title: string;\n}\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Define tenant and task model files",
            description="Create explicit tenant.model.ts and task.model.ts model files.",
            metadata={
                "target_files": ["src/models/tenant.model.ts", "src/models/task.model.ts"],
                "scope_paths": ["src/models"],
                "steps": ["Create tenant and task model files"],
                "acceptance": ["Both declared target model files exist"],
            },
        )

        async def _tenant_only_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "models" / "tenant.model.ts"
            target.write_text(
                "export interface TenantModel {\n  id: string;\n  name: string;\n}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote tenant model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/tenant.model.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _tenant_only_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-missing-target-nearby-repair"},
        )

        repaired_task = tmp_path / "src" / "models" / "task.model.ts"
        assert result["success"] is True
        assert repaired_task.read_text(encoding="utf-8") == existing_task.read_text(encoding="utf-8")
        assert "src/models/task.model.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_fails_when_changed_file_has_no_domain_signal(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        target = tmp_path / "src" / "fish" / "arena.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        task = adapter.task_board.create(
            subject="Implement fish predator prey multiplayer arena",
            description="Build fish arena movement and predator prey scoring for the online game.",
            metadata={
                "target_files": ["src/fish/arena.ts"],
                "scope_paths": ["src/fish/arena.ts"],
                "steps": ["Implement fish arena gameplay"],
                "acceptance": ["No generic unrelated implementation remains"],
            },
        )

        async def _write_unrelated_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target.write_text(
                "export function calculateInvoiceTotal(values: number[]): number {\n"
                "  return values.reduce((total, value) => total + value, 0);\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote an implementation.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/fish/arena.ts"},
                    }
                ],
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after workspace diff evidence")

        adapter._invoke_role_dialogue_with_timeout = _write_unrelated_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-semantic-quality"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_materialization_semantic_quality_failed"
        assert "no project-domain signal" in result["semantic_quality_error"]
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_materialization_semantic_quality_failed"

    @pytest.mark.asyncio
    async def test_execute_repairs_semantic_quality_failure_before_final_fail(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        target = tmp_path / "src" / "fish" / "arena.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        task = adapter.task_board.create(
            subject="Implement fish predator prey multiplayer arena",
            description="Build fish arena movement and predator prey scoring for the online game.",
            metadata={
                "target_files": ["src/fish/arena.ts"],
                "scope_paths": ["src/fish/arena.ts"],
                "steps": ["Implement fish arena gameplay"],
                "acceptance": ["Arena code contains fish domain behavior"],
            },
        )
        stages: list[str] = []
        repair_contexts: list[dict[str, Any]] = []

        async def _repairing_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_label = str(kwargs.get("stage_label") or "primary")
            stages.append(stage_label)
            raw_context = kwargs.get("context")
            context: dict[str, Any] = raw_context if isinstance(raw_context, dict) else {}
            if stage_label.startswith("quality_repair"):
                repair_contexts.append(context)
                target.write_text(
                    "export function renderFishArena(predators: number, prey: number): string {\n"
                    "  const balance = prey - predators;\n"
                    "  return `fish arena predator prey balance ${balance}`;\n"
                    "}\n",
                    encoding="utf-8",
                )
                return {
                    "content": "Rewrote arena implementation.",
                    "success": True,
                    "tool_results": [
                        {
                            "tool": "write_file",
                            "success": True,
                            "result": {"path": "src/fish/arena.ts", "file": "src/fish/arena.ts"},
                        }
                    ],
                }

            target.write_text(
                "export function calculateInvoiceTotal(values: number[]): number {\n"
                "  return values.reduce((total, value) => total + value, 0);\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote an implementation.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/fish/arena.ts", "file": "src/fish/arena.ts"},
                    }
                ],
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after workspace diff evidence")

        adapter._invoke_role_dialogue_with_timeout = _repairing_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-semantic-quality-repair"},
        )

        assert result["success"] is True
        assert stages.count("quality_repair") == 1
        assert "fish arena predator prey" in target.read_text(encoding="utf-8")
        assert repair_contexts[0]["director_quality_repair"]["artifact_quality_errors"]
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert len(adapter_result.get("semantic_quality_repair_attempts") or []) == 1

    @pytest.mark.asyncio
    async def test_execute_fails_autofix_declared_scope_without_real_materialization(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Implement interactive game renderer",
            description="Quality gate repair task generated because the PM contract omitted renderer scope.",
            metadata={
                "scope_paths": ["src/renderer/game-view.tsx"],
                "target_files": ["src/renderer/game-view.tsx"],
                "autofix": True,
                "autofix_reason": "game_pm_domain_coverage",
            },
        )

        async def _empty_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "role_model_not_configured"}

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct runtime provider bypass must not be called")

        adapter._invoke_role_dialogue_with_timeout = _empty_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-scaffold"},
        )

        target = tmp_path / "src" / "renderer" / "game-view.tsx"
        assert result["success"] is False
        assert result["error_code"] == "director_no_materialized_changes"
        assert target.exists() is False
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_no_materialized_changes"
        assert adapter_result.get("new_files") == []
        assert adapter_result.get("primary_llm", {}).get("error") == "role_model_not_configured"
        assert adapter_result.get("direct_fallback", {}).get("skipped_reason") == "direct_runtime_provider_removed"

    @pytest.mark.asyncio
    async def test_execute_accepts_existing_scope_after_read_only_mutation_guard(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "server" / "app.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "\n".join(
                [
                    "import http from 'http';",
                    "",
                    "export const server = http.createServer((_req, res) => {",
                    "  res.writeHead(200, { 'Content-Type': 'application/json' });",
                    "  res.end(JSON.stringify({ status: 'ok' }));",
                    "});",
                    "",
                    "export function startServer(port = 3000): void {",
                    "  server.listen(port);",
                    "}",
                    "",
                    "export default server;",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Extend Node.js backend entrypoint",
            description="Implement Node.js backend entrypoint.",
            metadata={
                "phase": "implementation",
                "scope_paths": ["src/server/app.ts"],
                "target_files": ["src/server/app.ts"],
                "steps": ["Implement src/server/app.ts"],
                "acceptance": ["npm run build verifies src/server/app.ts"],
            },
        )

        async def _read_only_contract_violation(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {
                "content": "",
                "success": False,
                "error": (
                    "TransactionKernel execution failed: single_batch_contract_violation: "
                    "mutation requested but no write tool invocation in decision batch."
                ),
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct runtime provider bypass must not be called")

        adapter._invoke_role_dialogue_with_timeout = _read_only_contract_violation  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-scope-after-read-only"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "verified_existing_workspace_scope"
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "completed"
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("existing_contract_evidence", {}).get("ok") is True
        assert adapter_result.get("primary_llm", {}).get("error", "").startswith("TransactionKernel execution failed")
        assert adapter_result.get("direct_fallback", {}).get("skipped_reason") == "direct_runtime_provider_removed"

    @pytest.mark.asyncio
    async def test_execute_accepts_existing_scope_after_read_write_batch_violation(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "server" / "session-store.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "export class SessionStore {\n"
            "  private readonly rows = new Map<string, string>();\n"
            "  save(roomId: string, value: string): void { this.rows.set(roomId, value); }\n"
            "  load(roomId: string): string | undefined { return this.rows.get(roomId); }\n"
            "}\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Extend multiplayer session persistence",
            description="Implement multiplayer session persistence.",
            metadata={
                "phase": "core",
                "scope_paths": ["src/server/session-store.ts"],
                "target_files": ["src/server/session-store.ts"],
                "acceptance": ["src/server/session-store.ts exposes persistence methods"],
            },
        )

        async def _batch_contract_violation(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {
                "content": "",
                "success": False,
                "error": (
                    "TransactionKernel execution failed: single_batch_contract_violation: "
                    "Cannot mix Read tools (read_file) and Write tools (write_file) in the same parallel batch."
                ),
            }

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _batch_contract_violation  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-scope-after-batch-violation"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "verified_existing_workspace_scope"
        assert result["existing_contract_evidence"]["ok"] is True

    @pytest.mark.asyncio
    async def test_execute_accepts_existing_scope_after_successful_no_diff_response(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "server" / "app.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export const serverReady = true;\n", encoding="utf-8")
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Extend Node.js backend entrypoint",
            description="Implement Node.js backend entrypoint.",
            metadata={
                "phase": "implementation",
                "scope_paths": ["src/server/app.ts"],
                "target_files": ["src/server/app.ts"],
            },
        )

        async def _successful_no_diff_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "Verified existing backend entrypoint.", "success": True}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _successful_no_diff_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-scope-after-successful-no-diff"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "verified_existing_workspace_scope"

    @pytest.mark.asyncio
    async def test_execute_preflights_existing_verification_scope(self, tmp_path: Any) -> None:
        source = tmp_path / "src" / "server" / "room-state.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "export interface RoomStateRecord { id: string; roomId: string; }\n"
            "export function validateRoomStateRecord(record: RoomStateRecord): string[] {\n"
            "  const failures: string[] = [];\n"
            "  if (!record.id) failures.push('missing id');\n"
            "  if (!record.roomId) failures.push('missing roomId');\n"
            "  return failures;\n"
            "}\n",
            encoding="utf-8",
        )
        target = tmp_path / "tests" / "integration" / "multiplayer-flow.test.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "import { validateRoomStateRecord } from '../../src/server/room-state';\n"
            "\n"
            "export function runMultiplayerFlowIntegrationChecks(): string[] {\n"
            "  const failures: string[] = [];\n"
            "  const issues = validateRoomStateRecord({ id: 'room-1', roomId: 'room-1' });\n"
            "  if (issues.length > 0) failures.push(issues.join(','));\n"
            "  return failures;\n"
            "}\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Strengthen multiplayer card integration tests",
            description="Verify multiplayer card integration tests according to acceptance criteria.",
            metadata={
                "phase": "verify",
                "scope_paths": ["tests/integration/multiplayer-flow.test.ts"],
                "target_files": ["tests/integration/multiplayer-flow.test.ts"],
                "acceptance": ["No placeholder tests remain"],
            },
        )

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("existing verification scope preflight should finish before LLM dialogue")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-verification-scope-preflight"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "preflight_verified_existing_workspace_scope"
        raw_evidence = result.get("existing_contract_evidence")
        evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
        assert evidence.get("ok") is True

    @pytest.mark.asyncio
    async def test_execute_repairs_overstrict_node_test_contract_before_llm(self, tmp_path: Any) -> None:
        script = tmp_path / "scripts" / "test.mjs"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "import { readFileSync } from 'node:fs';\n"
            "const text = readFileSync('src/analytics/match-analytics.ts', 'utf8');\n"
            "if (!/validate[A-Za-z]+Record/.test(text)) {\n"
            "  throw new Error('missing validation contract in src/analytics/match-analytics.ts');\n"
            "}\n",
            encoding="utf-8",
        )
        source_paths = [
            "src/analytics/match-analytics.ts",
            "src/animation/card-animations.ts",
            "src/assets/card-assets.ts",
            "src/client/card-table.ts",
            "src/client/network-client.ts",
            "src/client/three-scene.ts",
            "src/game/card-catalog.ts",
            "src/game/deck-builder.ts",
            "src/game/rules-engine.ts",
            "src/lobby/lobby-service.ts",
            "src/physics/table-layout.ts",
            "src/server/app.ts",
            "src/server/matchmaking.ts",
            "src/server/moderation.ts",
            "src/server/realtime-gateway.ts",
            "src/server/room-state.ts",
            "src/shared/protocol.ts",
            "src/shared/telemetry.ts",
        ]
        for index, rel_path in enumerate(source_paths):
            target = tmp_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"export const module{index}Ready = true;\n", encoding="utf-8")

        required_test_paths = [
            "tests/unit/card-rules.test.ts",
            "tests/unit/deck-builder.test.ts",
            "tests/integration/multiplayer-flow.test.ts",
            "tests/integration/realtime-sync.test.ts",
            "tests/e2e/card-table-3d.test.ts",
        ]
        for index, rel_path in enumerate(required_test_paths):
            target = tmp_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { module0Ready } from '../../src/analytics/match-analytics';\n"
                f"export function runCard3DChecks{index}(): string[] {{\n"
                "  const failures: string[] = [];\n"
                "  if (!module0Ready) failures.push('module not ready');\n"
                "  return failures;\n"
                "}\n",
                encoding="utf-8",
            )

        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Strengthen multiplayer card integration test runner",
            description="Replace the brittle scripts/test.mjs validation-contract gate with substantive test checks.",
            metadata={
                "phase": "verify",
                "scope_paths": ["scripts/test.mjs", "tests/integration/multiplayer-flow.test.ts"],
                "target_files": ["scripts/test.mjs", "tests/integration/multiplayer-flow.test.ts"],
                "acceptance": ["npm run test verifies the Card3D behavior test suite"],
            },
        )

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("deterministic test script repair should finish before LLM dialogue")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-node-test-script-contract-repair"},
        )

        rewritten = script.read_text(encoding="utf-8")
        assert result["success"] is True
        assert result["materialization_mode"] == "write_tool_and_workspace_diff"
        assert result["tools_executed"] == 1
        assert result["changed_files"] == ["scripts/test.mjs"]
        assert rewritten == _build_substantive_node_test_script()
        assert "missing validation contract" not in rewritten
        assert "test file lacks executable check contract" in rewritten

    def test_detects_legacy_overstrict_node_export_contract(self) -> None:
        legacy_script = (
            "for (const file of sourceFiles) {\n"
            "  const text = readFileSync(file, 'utf8');\n"
            "  if (!/export\\s+(class|function|const|interface|type)/.test(text)) {\n"
            "    throw new Error('missing export in ' + file);\n"
            "  }\n"
            "}\n"
        )

        assert _is_overstrict_node_test_script_contract(legacy_script) is True
        assert _is_overstrict_node_test_script_contract(_build_substantive_node_test_script()) is False

    def test_substantive_node_test_script_accepts_named_export_blocks(self, tmp_path: Any) -> None:
        script = tmp_path / "scripts" / "test.mjs"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(_build_substantive_node_test_script(), encoding="utf-8")

        for relative in [
            "src/server/app.ts",
            "src/client/three-scene.ts",
            "src/client/card-table.ts",
            "src/client/network-client.ts",
            "src/server/realtime-gateway.ts",
            "src/server/matchmaking.ts",
            "src/server/room-state.ts",
            "src/server/session-store.ts",
            "src/server/moderation.ts",
            "src/game/card-catalog.ts",
            "src/game/deck-builder.ts",
            "src/game/rules-engine.ts",
            "src/shared/protocol.ts",
            "src/shared/player-presence.ts",
            "src/shared/telemetry.ts",
            "src/assets/card-assets.ts",
            "src/animation/card-animations.ts",
            "src/auth/session-auth.ts",
        ]:
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == "src/server/app.ts":
                target.write_text(
                    "const server = { listen() {} };\n"
                    "const sessions = new Map<string, string>();\n"
                    "export { server, sessions };\n",
                    encoding="utf-8",
                )
            else:
                stem = target.stem.replace("-", "_")
                target.write_text(f"export const {stem}Ready = true;\n", encoding="utf-8")

        for index, relative in enumerate(
            [
                "tests/unit/card-rules.test.ts",
                "tests/unit/deck-builder.test.ts",
                "tests/integration/multiplayer-flow.test.ts",
                "tests/integration/realtime-sync.test.ts",
                "tests/e2e/card-table-3d.test.ts",
            ]
        ):
            test_file = tmp_path / relative
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(
                "import { card_catalogReady } from '../../src/game/card-catalog';\n"
                f"export function runCard3DChecks{index}(): string[] {{\n"
                "  const failures: string[] = [];\n"
                "  if (!card_catalogReady) failures.push('catalog not ready');\n"
                "  return failures;\n"
                "}\n",
                encoding="utf-8",
            )

        result = subprocess.run(
            ["node", "scripts/test.mjs", "--watch=false"],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "card3d behavior checks passed" in result.stdout

    def test_deterministic_patch_residue_cleanup_removes_declared_marker(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "assets" / "card-assets.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "export const cardAssetsReady = true;\n"
            ">>>> REPLACE src/assets/card-assets.ts\n"
            "export const assetCount = 52;\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)

        results = _apply_deterministic_patch_residue_cleanup(
            adapter,
            task={
                "metadata": {
                    "target_files": ["src/assets/card-assets.ts"],
                    "scope_paths": ["src/assets/card-assets.ts"],
                }
            },
            task_id="PM-CARD3D-ASSETS-18",
        )

        cleaned = target.read_text(encoding="utf-8")
        assert len(results) == 1
        assert results[0]["tool"] == "write_file"
        assert results[0]["result"]["source_tool"] == "deterministic_patch_residue_cleanup"
        assert ">>>> REPLACE" not in cleaned
        assert "export const cardAssetsReady = true;" in cleaned
        assert "export const assetCount = 52;" in cleaned
        assert _remove_patch_residue_lines(cleaned) == cleaned

    def test_deterministic_patch_residue_cleanup_ignores_unscoped_files(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "assets" / "card-assets.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        original = "export const cardAssetsReady = true;\n>>>> REPLACE src/assets/card-assets.ts\n"
        target.write_text(original, encoding="utf-8")
        adapter = _make_adapter(tmp_path)

        results = _apply_deterministic_patch_residue_cleanup(
            adapter,
            task={"metadata": {"target_files": ["src/server/app.ts"]}},
            task_id="PM-CARD3D-SERVER-01",
        )

        assert results == []
        assert target.read_text(encoding="utf-8") == original

    def test_deterministic_scaffold_marker_cleanup_rewrites_declared_residue_files(self, tmp_path: Any) -> None:
        source = tmp_path / "src" / "server" / "app.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            'export const tags = ["runtime", "audit-seed"];\nexport const title = "server planning scenario 0";\n',
            encoding="utf-8",
        )
        script = tmp_path / "scripts" / "test.mjs"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "throw new Error(`trivial arithmetic placeholder test scripts/test.mjs`);\n"
            "console.log(`test verification completed: 1 files`);\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)

        results = _apply_deterministic_scaffold_marker_cleanup(
            adapter,
            task={
                "metadata": {
                    "target_files": ["src/server/app.ts", "scripts/test.mjs"],
                    "scope_paths": ["src/server/app.ts", "scripts/test.mjs"],
                    "autofix_reason": "deterministic_scaffold_residue_cleanup",
                }
            },
            task_id="PM-AUTO-SEED-RESIDUE-CLEANUP",
        )

        assert len(results) == 2
        assert {item["result"]["source_tool"] for item in results} == {"deterministic_scaffold_marker_cleanup"}
        source_text = source.read_text(encoding="utf-8")
        script_text = script.read_text(encoding="utf-8")
        assert "audit-seed" not in source_text
        assert "planning scenario" not in source_text
        assert "test verification completed" not in script_text
        assert "placeholder" not in script_text
        assert "verified-sample" in source_text
        assert "test contract checks passed" in script_text

    @pytest.mark.asyncio
    async def test_execute_completes_scaffold_marker_cleanup_without_llm_call(self, tmp_path: Any) -> None:
        source = tmp_path / "src" / "server" / "app.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            'export const tags = ["runtime", "audit-seed"];\nexport const title = "server planning scenario 0";\n',
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Clean deterministic scaffold residue",
            description="Remove deterministic scaffold residue before QA.",
            metadata={
                "target_files": ["src/server/app.ts"],
                "scope_paths": ["src/server/app.ts"],
                "steps": ["Clean deterministic scaffold residue"],
                "acceptance": ["Declared files contain no audit-seed or deterministic scaffold markers"],
                "autofix_reason": "deterministic_scaffold_residue_cleanup",
            },
        )

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("cleanup task should complete without invoking Gemma")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-scaffold-marker-cleanup"},
        )

        assert result["success"] is True
        assert result["tools_executed"] >= 1
        assert "src/server/app.ts" in result["changed_files"]
        source_text = source.read_text(encoding="utf-8")
        assert "audit-seed" not in source_text
        assert "planning scenario" not in source_text

    @pytest.mark.asyncio
    async def test_ready_queue_fallback_claim_preserves_selected_task_identity(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "combat" / "combat-system.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export const combatReady = true;\n", encoding="utf-8")
        adapter = _make_adapter(tmp_path)
        combat = adapter.task_board.create(
            subject="Audit turn based combat system scope",
            description="Materialize combat scope.",
            metadata={
                "external_task_id": "PM-AUTO-COMBAT",
                "source_task_id": "PM-AUTO-COMBAT",
                "target_files": ["src/combat/combat-system.ts"],
            },
        )

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("existing scope preflight should finish before LLM dialogue")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id="PM-AUTO-AI",
            input_data={"task_id": "PM-AUTO-AI"},
            context={"run_id": "run-director-identity"},
        )

        assert result["success"] is True
        updated = adapter.task_board.get_task(str(combat.id))
        assert updated is not None
        metadata_raw = updated.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        runtime_execution_raw = metadata.get("runtime_execution")
        runtime_execution: dict[str, Any] = runtime_execution_raw if isinstance(runtime_execution_raw, dict) else {}
        assert metadata["external_task_id"] == "PM-AUTO-COMBAT"
        assert runtime_execution["external_task_id"] == "PM-AUTO-COMBAT"

    @pytest.mark.asyncio
    async def test_task_market_claim_materializes_requested_external_id_without_ready_queue_fallback(
        self, tmp_path: Any
    ) -> None:
        existing_target = tmp_path / "worker_3.py"
        existing_target.write_text('MARKER = "D4-SAT-3"\n', encoding="utf-8")
        adapter = _make_adapter(tmp_path)
        sibling = adapter.task_board.create(
            subject="Create independent saturation Python file 3",
            description="Create worker_3.py.",
            metadata={
                "external_task_id": "D4-SAT-3",
                "source_task_id": "D4-SAT-3",
                "target_files": ["worker_3.py"],
                "scope_paths": ["worker_3.py"],
            },
        )

        async def _empty_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "role_model_not_configured"}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _empty_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id="D4-SAT-2",
            input_data={
                "task_id": "D4-SAT-2",
                "subject": "Create independent saturation Python file 2",
                "description": "Create worker_2.py.",
                "input": "Create worker_2.py with MARKER D4-SAT-2.",
                "target_files": ["worker_2.py"],
                "scope_paths": ["worker_2.py"],
                "metadata": {
                    "task_market_task_id": "D4-SAT-2",
                    "pm_task_id": "D4-SAT-2",
                    "source": "runtime.task_market.pending_exec",
                },
            },
            context={"run_id": "run-director-task-market-exact"},
        )

        assert result["task_id"] != str(sibling.id)
        materialized = adapter.task_runtime.get_task("D4-SAT-2")
        assert materialized is not None
        assert materialized["metadata"]["external_task_id"] == "D4-SAT-2"
        sibling_after = adapter.task_board.get_task(str(sibling.id))
        assert sibling_after is not None
        assert sibling_after["status"] != "completed"

    def test_claim_external_task_id_prefers_selected_task_source(self) -> None:
        assert (
            _resolve_claim_external_task_id(
                {
                    "id": 4,
                    "metadata": {
                        "external_task_id": "PM-AUTO-AI",
                        "source_task_id": "PM-AUTO-COMBAT",
                    },
                },
                "PM-AUTO-AI",
            )
            == "PM-AUTO-COMBAT"
        )

    def test_get_task_resolves_external_task_id_before_queue_fallback(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        adapter.task_board.create(
            subject="Create independent saturation Python file 3",
            description="Create worker_3.py.",
            metadata={
                "external_task_id": "D4-SAT-3",
                "source_task_id": "D4-SAT-3",
                "target_files": ["worker_3.py"],
            },
        )
        adapter.task_board.create(
            subject="Create independent saturation Python file 2",
            description="Create worker_2.py.",
            metadata={
                "external_task_id": "D4-SAT-2",
                "source_task_id": "D4-SAT-2",
                "target_files": ["worker_2.py"],
            },
        )

        selected = adapter._get_task("D4-SAT-2")

        assert selected is not None
        metadata_raw = selected.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        assert metadata["external_task_id"] == "D4-SAT-2"
        assert metadata["target_files"] == ["worker_2.py"]

    @pytest.mark.asyncio
    async def test_execute_rejects_existing_autofix_scaffold_without_real_materialization(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        target = tmp_path / "src" / "renderer" / "game-view.tsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            'export const gameViewScaffoldVersion = "deterministic-declared-scope-v1";\n',
            encoding="utf-8",
        )
        task = adapter.task_board.create(
            subject="Implement interactive game renderer",
            description="Quality gate repair task generated because the PM contract omitted renderer scope.",
            metadata={
                "scope_paths": ["src/renderer/game-view.tsx"],
                "target_files": ["src/renderer/game-view.tsx"],
                "autofix": True,
                "autofix_reason": "game_pm_domain_coverage",
            },
        )

        async def _empty_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "role_model_not_configured"}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _empty_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-scaffold"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_no_materialized_changes"
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_no_materialized_changes"
        assert adapter_result.get("modified_files") == []
        assert adapter_result.get("primary_llm", {}).get("error") == "role_model_not_configured"

    def test_text_patch_mode_requests_parseable_file_blocks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "T"}, text_patch_mode=True)
        assert "当前运行时要求纯文本补丁" in msg
        assert "relative/path.ext" in msg
        assert "path/to/file.py" not in msg


# ---------------------------------------------------------------------------
# Existing workspace evidence
# ---------------------------------------------------------------------------


class TestExistingWorkspaceTaskEvidence:
    """Director can verify already-materialized task scope without fresh diffs."""

    def test_declared_scope_present(self) -> None:
        task = {
            "scope": [
                "package.json",
                "src/types",
                "src/spec",
                "src/services",
                "src/store",
            ]
        }
        current_files = {
            "package.json": "1",
            "src/types/domain.ts": "1",
            "src/spec/generationSpec.ts": "1",
            "src/services/mockGenerationService.ts": "1",
            "src/store/useStudioStore.ts": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert evidence["reason"] == "declared_scope_present"
        assert "src/spec" in evidence["existing_paths"]

    def test_missing_or_weak_scope_is_not_enough(self) -> None:
        task = {"scope": ["src/workbench", "src/library", "src/layouts", "src/components"]}
        current_files = {"src/components/StudioShell.tsx": "1"}

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is False
        assert evidence["reason"] == "declared_scope_incomplete"

    def test_no_scope_paths_is_not_evidence(self) -> None:
        evidence = _build_existing_workspace_task_evidence(
            task={"goal": "Implement a UI"},
            current_files={"src/App.tsx": "1"},
        )

        assert evidence["ok"] is False
        assert evidence["reason"] == "no_declared_scope_paths"

    def test_glob_scope_paths_match_workspace_files(self) -> None:
        task = {
            "metadata": {
                "scope": [
                    "src/**/*.test.ts",
                    "src/**/*.test.tsx",
                    "README.md",
                    "tests",
                ]
            }
        }
        current_files = {
            "src/spec/generationSpec.test.ts": "1",
            "src/App.test.tsx": "1",
            "README.md": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert "src/**/*.test.ts" in evidence["existing_paths"]
        assert "README.md" in evidence["existing_paths"]

    def test_existing_scope_rejects_placeholder_tests_when_workspace_is_available(self, tmp_path: Any) -> None:
        test_file = tmp_path / "tests" / "unit" / "card-rules.test.ts"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(
            "\n".join(f"test('case {idx}', () => expect({idx} + 1).toBe({idx + 1}));" for idx in range(4)) + "\n",
            encoding="utf-8",
        )
        task = {
            "target_files": ["tests/unit/card-rules.test.ts"],
            "scope_paths": ["tests"],
        }
        current_files = {"tests/unit/card-rules.test.ts": "1"}

        evidence = _build_existing_workspace_task_evidence(
            task=task,
            current_files=current_files,
            workspace_full=str(tmp_path),
        )

        assert evidence["ok"] is False
        assert evidence["reason"] == "declared_scope_quality_failed"
        assert any("trivial arithmetic placeholder" in item for item in evidence["artifact_quality_errors"])

    def test_materialized_orchestration_scope_markers_are_evidence(self) -> None:
        task = {
            "subject": (
                "Execute PM tasks strictly in order:\n"
                "- Project Foundation [scope: package.json, tsconfig.json, vite.config.ts, tailwind.config.js]\n"
                "- Domain Layer [scope: src/types, src/spec, src/services, src/store]\n"
                "- Delivery Verification [scope: tests, src/**/*.test.tsx, README.md]"
            )
        }
        current_files = {
            "package.json": "1",
            "tsconfig.json": "1",
            "vite.config.ts": "1",
            "tailwind.config.js": "1",
            "src/types/domain.ts": "1",
            "src/spec/generationSpec.ts": "1",
            "src/services/mockGenerationService.ts": "1",
            "src/store/useStudioStore.ts": "1",
            "src/App.test.tsx": "1",
            "tests/routes/WorkbenchRoute.test.tsx": "1",
            "README.md": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert "package.json" in evidence["existing_paths"]
        assert "src/**/*.test.tsx" in evidence["existing_paths"]
        assert evidence["reason"] == "declared_scope_present"

    def test_scope_label_prefixes_do_not_pollute_path_candidates(self) -> None:
        task = {"metadata": {"scope": "Root configuration files: package.json, tsconfig.json, postcss.config.js"}}
        current_files = {
            "package.json": "1",
            "tsconfig.json": "1",
            "postcss.config.js": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert "package.json" in evidence["existing_paths"]
        assert all("Root configuration files" not in item for item in evidence["candidate_paths"])

    def test_workspace_basename_prefix_is_not_treated_as_nested_scope(self) -> None:
        task = {
            "metadata": {
                "scope": "fashion-gen-studio/package.json, fashion-gen-studio/src/, vite.config.ts",
            }
        }
        current_files = {
            "package.json": "1",
            "src/App.tsx": "1",
            "vite.config.ts": "1",
        }

        evidence = _build_existing_workspace_task_evidence(
            task=task,
            current_files=current_files,
            workspace_name="fashion-gen-studio",
        )

        assert evidence["ok"] is True
        assert "package.json" in evidence["existing_paths"]
        assert "src" in evidence["existing_paths"]
        assert "fashion-gen-studio/package.json" not in evidence["missing_paths"]

    def test_repair_tasks_require_fresh_materialization(self) -> None:
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Repair TypeScript failure",
                    "metadata": {"acceptance": ["npm test returns PASS"]},
                }
            )
            is True
        )
        assert _task_requires_fresh_materialization({"subject": "Create initial source files"}) is True
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Implement Card3D tests",
                    "phase": "verification",
                    "target_files": ["tests/integration/multiplayer-flow.test.ts"],
                }
            )
            is True
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "title": "补齐领域验收测试",
                    "goal": "移除旧的占位测试，创建覆盖卡牌、牌组、多人流程、同步与3D场景的测试",
                    "phase": "verify",
                    "target_files": [
                        "tests/unit/card-rules.test.ts",
                        "tests/integration/multiplayer-flow.test.ts",
                    ],
                    "execution_checklist": ["删除已存在的 trivial 占位测试（如算术测试）"],
                }
            )
            is True
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Replace placeholder Card3D unit tests",
                    "description": "Remove trivial arithmetic placeholder tests.",
                }
            )
            is True
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "QA Placeholder Repair Verification",
                    "phase": "verification",
                    "metadata": {"qa_rework_verification_only": True},
                }
            )
            is False
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Frontend Test Failure Reproduction",
                    "description": "Fix npm test failure with the smallest target-project change after evidence is collected.",
                    "metadata": {
                        "phase": "requirements",
                        "steps": ["Run npm test", "Identify failing assertion"],
                        "acceptance": ["The failing Vitest case is identified"],
                    },
                }
            )
            is False
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Requirements task reopened by QA",
                    "metadata": {
                        "phase": "requirements",
                        "qa_rework_requested": True,
                        "adapter_result": {
                            "qa_passed": False,
                            "qa_rework_reason": "placeholder_content_detected",
                        },
                    },
                }
            )
            is True
        )

    def test_transient_provider_errors_can_accept_existing_scope(self) -> None:
        task = {
            "subject": "Extend realtime gateway",
            "phase": "implementation",
            "target_files": ["src/server/realtime-gateway.ts"],
        }

        assert (
            _can_accept_existing_workspace_scope(
                task=task,
                requires_fresh_materialization=True,
                write_tool_evidence=False,
                primary_llm_summary={
                    "success": False,
                    "error": "TransactionKernel execution failed: circuit_open:50s_remaining",
                },
            )
            is True
        )
        assert (
            _can_accept_existing_workspace_scope(
                task=task,
                requires_fresh_materialization=True,
                write_tool_evidence=False,
                primary_llm_summary={
                    "success": False,
                    "error": "429 Client Error: Too Many Requests for url",
                },
            )
            is True
        )

    def test_non_transient_no_write_still_requires_materialization(self) -> None:
        assert (
            _can_accept_existing_workspace_scope(
                task={
                    "subject": "Extend realtime gateway",
                    "phase": "implementation",
                    "target_files": ["src/server/realtime-gateway.ts"],
                },
                requires_fresh_materialization=True,
                write_tool_evidence=False,
                primary_llm_summary={"success": False, "error": "model returned no tool calls"},
            )
            is False
        )


class TestDeterministicPythonRuntimeSmokeLongRunningBoundary:
    """Runtime smoke must not penalize a long-running __main__ block.

    Live factory-bench L4-23 (2026-06-17, after the static-smoke
    fix): the model wrote ``gateway/server.py`` whose ``__main__``
    block launches an HTTP server with ``serve_forever()`` — the
    canonical pattern for a Python web gateway. The runtime smoke
    killed the process after 5s and reported it as a timeout
    failure, which requeued the parent task and broke
    integration_qa. The script itself is correct:
    ``python3 gateway/server.py`` really starts the server on
    127.0.0.1:8080 and waits for connections. The platform must
    not penalize a long-running, contract-compliant __main__
    block as a quality failure.

    Strategy: when the runtime smoke hits its timeout, check
    whether the process is still alive. If yes, the model wrote
    a process that intentionally runs forever (server/daemon) —
    kill it and do NOT add it to ``artifact_quality_errors``. If
    the process already exited (perhaps during the cleanup),
    the timeout itself is the failure — report it as before.
    The smoke's job is to surface CALL-TIME errors, not loop
    semantics; a long-running server is contract-compliant for
    web-gateway / daemon / game-loop L4-L8 briefs.
    """

    def test_long_running_main_block_is_not_a_failure(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_runtime_smoke,
        )

        adapter = _make_adapter(tmp_path)
        # A server-style main block: bind a TCP socket, accept
        # forever. Behaves like ``serve_forever()`` in stdlib
        # http.server — exactly the L4-23 pattern.
        (tmp_path / "server.py").write_text(
            "import socket\n"
            "if __name__ == '__main__':\n"
            "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            "    s.bind(('127.0.0.1', 0))\n"
            "    s.listen(5)\n"
            "    while True:\n"
            "        s.accept()\n",
            encoding="utf-8",
        )

        errors = _apply_deterministic_python_runtime_smoke(
            adapter,
            task_id="task-server-bb-1",
            all_affected_files=["server.py"],
            timeout_seconds=1.0,
        )

        # Long-running process: smoke should NOT flag it as a failure.
        assert errors == [], errors

    def test_clean_main_block_still_passes(self, tmp_path: Any) -> None:
        """A clean main that exits within timeout is still a pass."""
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_runtime_smoke,
        )

        adapter = _make_adapter(tmp_path)
        (tmp_path / "ok.py").write_text(
            "if __name__ == '__main__':\n    print('done')\n",
            encoding="utf-8",
        )

        errors = _apply_deterministic_python_runtime_smoke(
            adapter,
            task_id="task-ok-bb-1",
            all_affected_files=["ok.py"],
            timeout_seconds=1.0,
        )

        assert errors == [], errors

    def test_subprocess_timeout_expired_process_killed(self, tmp_path: Any) -> None:
        """Sanity: when a long-running process is killed, the
        subprocess handle must be cleaned up so no zombie lingers.
        This guards against the regression where the smoke leaves
        a leaked subprocess after returning no errors."""
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_runtime_smoke,
        )

        adapter = _make_adapter(tmp_path)
        (tmp_path / "server.py").write_text(
            "import time\nif __name__ == '__main__':\n    while True:\n        time.sleep(0.5)\n",
            encoding="utf-8",
        )

        # Find one leftover python3 process owned by this test pid
        # BEFORE running. (None expected, but the assertion is that
        # the count does not GROW after running.)
        import os
        import subprocess

        my_pid = os.getpid()
        before = (
            subprocess.run(
                ["pgrep", "-P", str(my_pid), "python3"],
                capture_output=True,
                text=True,
                check=False,
            )
            .stdout.strip()
            .split()
        )
        errors = _apply_deterministic_python_runtime_smoke(
            adapter,
            task_id="task-zombie-bb-1",
            all_affected_files=["server.py"],
            timeout_seconds=0.5,
        )
        after = (
            subprocess.run(
                ["pgrep", "-P", str(my_pid), "python3"],
                capture_output=True,
                text=True,
                check=False,
            )
            .stdout.strip()
            .split()
        )
        assert errors == [], errors
        # No new zombie child of this test process
        assert len(after) <= len(before), (before, after)


class TestDeterministicPythonStaticSmoke:
    """Director py_compiles every .py file the model touches, not just declared targets.

    Live factory-bench L2-07 (2026-06-17, after runtime-smoke fix): the
    model wrote 13 .py files, 10 of which were in the task's declared
    target list and py_compile-checked by the existing quality gate.
    The remaining 3 (including ``src/ledger/ui/stats_view.py``)
    contained a ``SyntaxError: keyword argument repeated: columns`` —
    the model wrote ``columns=(...)`` twice in the same ``Treeview``
    constructor. The platform marked the run as PASS, and the
    downstream task-market integration_qa was not even invoked for
    that file. A rigid ruler must py_compile every .py artifact the
    model wrote, regardless of whether the contract asked for it.
    """

    def test_python_static_smoke_catches_syntax_error_in_undeclared_file(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_static_smoke,
        )

        adapter = _make_adapter(tmp_path)
        # Duplicate keyword argument — a real, deterministic Python
        # syntax error. ``def f(x, x):`` raises SyntaxError at compile
        # time. The model emitted the same kind of bug in
        # L2-07 ``stats_view.py`` (duplicate ``columns=``).
        (tmp_path / "stats_view.py").write_text(
            "def f(x, x):\n    return x\n",
            encoding="utf-8",
        )

        errors = _apply_deterministic_python_static_smoke(
            adapter,
            all_affected_files=["stats_view.py"],
        )

        assert len(errors) == 1, errors
        assert "stats_view.py" in errors[0]
        # Error message should mention the syntax issue
        assert "syntax" in errors[0].lower() or "invalid" in errors[0].lower()

    def test_python_static_smoke_passes_clean_files(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_static_smoke,
        )

        adapter = _make_adapter(tmp_path)
        (tmp_path / "clean_a.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "clean_b.py").write_text(
            "def hello() -> str:\n    return 'hi'\n",
            encoding="utf-8",
        )

        errors = _apply_deterministic_python_static_smoke(
            adapter,
            all_affected_files=["clean_a.py", "clean_b.py"],
        )

        assert errors == [], errors

    def test_python_static_smoke_skips_non_python_files(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_static_smoke,
        )

        adapter = _make_adapter(tmp_path)
        (tmp_path / "readme.md").write_text("# title\n", encoding="utf-8")
        (tmp_path / "config.toml").write_text("x = 1\n", encoding="utf-8")

        errors = _apply_deterministic_python_static_smoke(
            adapter,
            all_affected_files=["readme.md", "config.toml"],
        )

        assert errors == [], errors

    def test_python_static_smoke_catches_multiple_broken_files(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_static_smoke,
        )

        adapter = _make_adapter(tmp_path)
        # Two distinct, real Python syntax errors + one clean file.
        (tmp_path / "broken_a.py").write_text("def f(x, x):\n    return x\n", encoding="utf-8")
        (tmp_path / "broken_b.py").write_text("class 123Bad:\n    pass\n", encoding="utf-8")
        (tmp_path / "clean.py").write_text("z = 3\n", encoding="utf-8")

        errors = _apply_deterministic_python_static_smoke(
            adapter,
            all_affected_files=["broken_a.py", "broken_b.py", "clean.py"],
        )

        # Both broken files should be reported; clean is silent.
        assert len(errors) == 2, errors
        assert any("broken_a.py" in e for e in errors)
        assert any("broken_b.py" in e for e in errors)


class TestDeterministicPythonRuntimeSmoke:
    """Director surfaces runtime errors that py_compile misses.

    Live factory-bench L1-01 (2026-06-17, after symbol-coherence fix):
    qwen3.6-27b-int4 wrote calculator.py that imports cleanly and
    py_compiles, but its ``__main__`` block calls ``evaluate('1+2')``
    which raises ``ValueError`` at call time (the model's tokenizer
    stores ``value=float(text)`` for operator tokens — a model bug,
    but a real one the platform should surface). The post-write
    materialization quality gate currently relies on ``py_compile`` +
    ``scan_workspace_artifact_quality`` — neither catches call-time
    errors. A rigid ruler must run the code, not just parse it.

    Strategy: for each ``.py`` file with a ``__main__`` block, run it
    in a subprocess with a timeout. If exit code != 0 or the process
    is killed by the timeout, surface a runtime error so the
    materialization repair ladder can fix it (currently via the LLM
    repair path; the deterministic path is intentionally conservative
    because runtime fixes are project-specific).
    """

    def test_python_runtime_smoke_catches_call_time_error(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_runtime_smoke,
        )

        adapter = _make_adapter(tmp_path)
        # A script with __main__ that crashes. py_compile will pass;
        # only running it surfaces the bug.
        (tmp_path / "calculator.py").write_text(
            "def evaluate(expr: str) -> float:\n"
            "    raise ValueError('broken tokenizer')\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    print(evaluate('1+2'))\n",
            encoding="utf-8",
        )

        errors = _apply_deterministic_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-1",
            all_affected_files=["calculator.py"],
            timeout_seconds=10.0,
        )

        assert len(errors) == 1, errors
        assert "calculator.py" in errors[0]
        assert "ValueError" in errors[0] or "Traceback" in errors[0]

    def test_python_runtime_smoke_passes_clean_main(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_runtime_smoke,
        )

        adapter = _make_adapter(tmp_path)
        (tmp_path / "hello.py").write_text(
            "if __name__ == '__main__':\n    print('hello')\n",
            encoding="utf-8",
        )

        errors = _apply_deterministic_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-2",
            all_affected_files=["hello.py"],
            timeout_seconds=10.0,
        )

        assert errors == [], errors

    def test_python_runtime_smoke_sets_workspace_pythonpath_for_test_scripts(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_runtime_smoke,
        )

        adapter = _make_adapter(tmp_path)
        (tmp_path / "guess_number.py").write_text(
            "def generate_target() -> int:\n    return 42\n",
            encoding="utf-8",
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_guess_number.py").write_text(
            "import guess_number\n\n"
            "def test_target() -> None:\n"
            "    assert guess_number.generate_target() == 42\n\n"
            "if __name__ == '__main__':\n"
            "    test_target()\n",
            encoding="utf-8",
        )

        errors = _apply_deterministic_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-test-import",
            all_affected_files=["tests/test_guess_number.py"],
            timeout_seconds=10.0,
        )

        assert errors == [], errors

    def test_python_unittest_missing_target_repair_creates_discoverable_test(self, tmp_path: Any) -> None:
        class _Adapter:
            workspace = str(tmp_path)

            def __init__(self) -> None:
                self.progress_files: list[str] = []
                self._execution = SimpleNamespace(_message_bus=None)

            def _update_task_progress(self, task_id: str, status: str, **kwargs: Any) -> None:
                del task_id, status
                current_file = str(kwargs.get("current_file") or "")
                if current_file:
                    self.progress_files.append(current_file)

        (tmp_path / "guess_number_game.py").write_text(
            "def generate_target() -> int:\n    return 42\n",
            encoding="utf-8",
        )
        adapter = _Adapter()

        results = _apply_deterministic_python_unittest_missing_target_repair(
            adapter,
            task={"target_files": ["guess_number_game.py", "tests/test_guess_number_game.py"]},
            task_id="TASK-3",
        )

        test_file = tmp_path / "tests" / "test_guess_number_game.py"
        assert [item["result"]["file"] for item in results] == ["tests/test_guess_number_game.py"]
        assert test_file.exists()
        content = test_file.read_text(encoding="utf-8")
        assert "unittest" in content
        assert "guess_number_game" in content
        assert adapter.progress_files == ["tests/test_guess_number_game.py"]

        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "Ran 2 tests" in completed.stderr

    def test_python_unittest_runtime_failure_repair_replaces_overstrict_test(self, tmp_path: Any) -> None:
        class _Adapter:
            workspace = str(tmp_path)

            def __init__(self) -> None:
                self.progress_files: list[str] = []
                self._execution = SimpleNamespace(_message_bus=None)

            def _update_task_progress(self, task_id: str, status: str, **kwargs: Any) -> None:
                del task_id, status
                current_file = str(kwargs.get("current_file") or "")
                if current_file:
                    self.progress_files.append(current_file)

        (tmp_path / "guess_number.py").write_text(
            "def play() -> None:\n    print('ready')\n",
            encoding="utf-8",
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        failing_test = tests_dir / "test_guess_number.py"
        failing_test.write_text(
            "import unittest\n\n"
            "class TestGuessNumber(unittest.TestCase):\n"
            "    def test_correct_guess_ends_game(self):\n"
            "        self.assertTrue(False, 'Game should show congratulations/stats on correct guess')\n",
            encoding="utf-8",
        )
        adapter = _Adapter()

        results = _apply_deterministic_python_unittest_runtime_failure_repair(
            adapter,
            task={"target_files": ["guess_number.py", "tests/test_guess_number.py"]},
            task_id="TASK-3",
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke timed out for 'tests/test_guess_number.py' "
                "after 5.0s; tail:\nwaiting for interactive input"
            ],
        )

        assert [item["result"]["file"] for item in results] == ["tests/test_guess_number.py"]
        content = failing_test.read_text(encoding="utf-8")
        assert "DeclaredPythonModuleSmokeTests" in content
        assert "Game should show congratulations" not in content
        assert adapter.progress_files == ["tests/test_guess_number.py"]

        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "Ran 2 tests" in completed.stderr

    def test_javascript_test_missing_target_repair_creates_frontend_smoke(self, tmp_path: Any) -> None:
        class _Adapter:
            workspace = str(tmp_path)

            def __init__(self) -> None:
                self.progress_files: list[str] = []
                self._execution = SimpleNamespace(_message_bus=None)

            def _update_task_progress(self, task_id: str, status: str, **kwargs: Any) -> None:
                del task_id, status
                current_file = str(kwargs.get("current_file") or "")
                if current_file:
                    self.progress_files.append(current_file)

        (tmp_path / "index.html").write_text(
            '<!doctype html><html><body><input id="taskInput"><button id="addBtn"></button>'
            '<script src="app.js"></script></body></html>\n',
            encoding="utf-8",
        )
        (tmp_path / "app.js").write_text(
            "const input = document.getElementById('taskInput');\n"
            "const add = document.getElementById('addBtn');\n"
            "localStorage.setItem('todo_app_data', JSON.stringify([]));\n"
            "void input; void add;\n",
            encoding="utf-8",
        )
        (tmp_path / "style.css").write_text("body { font-family: sans-serif; }\n", encoding="utf-8")
        adapter = _Adapter()

        results = _apply_deterministic_javascript_test_missing_target_repair(
            adapter,
            task={"target_files": ["index.html", "app.js", "style.css", "tests/app.test.js"]},
            task_id="TASK-1",
            artifact_quality_errors=[
                "Artifact quality scan failed: declared target file missing 'tests/app.test.js'",
            ],
        )

        test_file = tmp_path / "tests" / "app.test.js"
        assert [item["result"]["file"] for item in results] == ["tests/app.test.js"]
        assert test_file.exists()
        content = test_file.read_text(encoding="utf-8")
        assert "getElementById" in content
        assert "localStorage" in content
        assert adapter.progress_files == ["tests/app.test.js"]

        completed = subprocess.run(
            ["node", "tests/app.test.js"],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "frontend smoke checks passed" in completed.stdout

        (tmp_path / "index.html").write_text(
            '<!doctype html><html><body><input id="renamedTaskInput"><button id="addBtn"></button>'
            '<script src="app.js"></script></body></html>\n',
            encoding="utf-8",
        )
        failed = subprocess.run(
            ["node", "tests/app.test.js"],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert failed.returncode != 0
        assert "references missing DOM id taskInput" in failed.stderr

    def test_python_runtime_smoke_skips_module_without_main(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_runtime_smoke,
        )

        adapter = _make_adapter(tmp_path)
        # Library file with no __main__ block — must not be executed
        # (calling it would hang on missing CLI args or do nothing
        # useful). Just skip.
        (tmp_path / "library.py").write_text(
            "def helper() -> int:\n    return 42\n",
            encoding="utf-8",
        )

        errors = _apply_deterministic_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-3",
            all_affected_files=["library.py"],
            timeout_seconds=10.0,
        )

        assert errors == [], errors

    def test_python_runtime_smoke_skips_non_python_files(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_runtime_smoke,
        )

        adapter = _make_adapter(tmp_path)
        (tmp_path / "readme.md").write_text("# title\n", encoding="utf-8")
        (tmp_path / "config.toml").write_text("x = 1\n", encoding="utf-8")

        errors = _apply_deterministic_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-4",
            all_affected_files=["readme.md", "config.toml"],
            timeout_seconds=10.0,
        )

        assert errors == [], errors

    def test_python_runtime_smoke_terminates_hung_process(self, tmp_path: Any) -> None:
        """The smoke test must kill the child process after the
        configured timeout so a hung ``__main__`` does not leak
        past the Director turn boundary. After the L4-23 fix-#3
        boundary change, a long-running script is NOT a quality
        failure; the smoke only ensures the child is reaped so
        the next smoke call in the same turn is not contaminated.
        We verify the reap by re-running the smoke on the same
        file and expecting it to return within a small multiple
        of the timeout (i.e. it did not block on a leftover
        process).
        """
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_python_runtime_smoke,
        )

        adapter = _make_adapter(tmp_path)
        (tmp_path / "hung.py").write_text(
            "import time\nif __name__ == '__main__':\n    while True:\n        time.sleep(0.1)\n",
            encoding="utf-8",
        )

        # Long-running: the smoke must NOT flag this as a quality
        # failure (the L4-23 fix-#3 boundary). It must, however,
        # kill the child so subsequent smokes in the same turn
        # are not blocked by a leaked process.
        import time

        first = _apply_deterministic_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-5",
            all_affected_files=["hung.py"],
            timeout_seconds=0.5,
        )
        assert first == [], first
        started = time.monotonic()
        second = _apply_deterministic_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-5b",
            all_affected_files=["hung.py"],
            timeout_seconds=0.5,
        )
        elapsed = time.monotonic() - started
        assert second == [], second
        assert elapsed < 5.0, f"second smoke took {elapsed:.2f}s -- leaked process"


class TestDeterministicPythonUnresolvedSymbolRepair:
    """Director can repair cross-file Python unresolved import symbol failures
    without LLM cooperation — same fail-closed posture as the TS reexport
    repair. Live factory-bench L6-32 (2026-06-17, after multi-missing fix):
    the weak model wrote shared/__init__.py importing Registry from
    shared.registry, but the registry module only defined ServiceRegistry.
    The post-write QA gate caught it; the LLM repair call echoed the
    prompt back (tool_choice was honored, the model still didn't emit
    a tool call). The platform must add the missing symbol itself.
    """

    def test_repairs_missing_symbol_with_similar_named_alias(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_unresolved_import_symbol_repair,
        )

        adapter = _make_adapter(tmp_path)
        (tmp_path / "shared").mkdir(parents=True)
        (tmp_path / "shared" / "__init__.py").write_text(
            "from shared.registry import Registry\n",
            encoding="utf-8",
        )
        (tmp_path / "shared" / "registry.py").write_text(
            "class ServiceRegistry:\n    pass\n",
            encoding="utf-8",
        )

        results = _apply_deterministic_unresolved_import_symbol_repair(
            adapter,
            task={"subject": "Fix Python cross-file import"},
            task_id="task-py-1",
            artifact_quality_errors=[
                (
                    "Artifact quality scan failed: unresolved import symbol "
                    "'Registry' from 'shared.registry' in "
                    "shared/__init__.py"
                ),
            ],
        )

        assert results and results[0]["success"] is True, results
        registry_text = (tmp_path / "shared" / "registry.py").read_text(encoding="utf-8")
        # The missing symbol is now resolvable
        assert "Registry" in registry_text
        # The most useful general fix is an alias to the closest-named class
        assert "Registry = ServiceRegistry" in registry_text
        # The importer should now import successfully
        import sys
        from importlib import import_module

        sys.path.insert(0, str(tmp_path))
        try:
            mod = import_module("shared.registry")
            assert hasattr(mod, "Registry")
        finally:
            sys.path.remove(str(tmp_path))
            for cached in list(sys.modules):
                if cached.startswith("shared"):
                    del sys.modules[cached]

    def test_repairs_missing_symbol_with_class_stub_when_no_similar(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_unresolved_import_symbol_repair,
        )

        adapter = _make_adapter(tmp_path)
        (tmp_path / "shared").mkdir(parents=True)
        (tmp_path / "shared" / "__init__.py").write_text(
            "from shared.config import SomeConfig\n",
            encoding="utf-8",
        )
        (tmp_path / "shared" / "config.py").write_text(
            "SETTING = 1\n",
            encoding="utf-8",
        )

        results = _apply_deterministic_unresolved_import_symbol_repair(
            adapter,
            task={"subject": "Fix Python import"},
            task_id="task-py-2",
            artifact_quality_errors=[
                (
                    "Artifact quality scan failed: unresolved import symbol "
                    "'SomeConfig' from 'shared.config' in "
                    "shared/__init__.py"
                ),
            ],
        )

        assert results and results[0]["success"] is True, results
        config_text = (tmp_path / "shared" / "config.py").read_text(encoding="utf-8")
        assert "class SomeConfig" in config_text

    def test_idempotent_when_symbol_already_defined(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_unresolved_import_symbol_repair,
        )

        adapter = _make_adapter(tmp_path)
        (tmp_path / "shared").mkdir(parents=True)
        (tmp_path / "shared" / "__init__.py").write_text(
            "from shared.registry import Registry\n",
            encoding="utf-8",
        )
        (tmp_path / "shared" / "registry.py").write_text(
            "class Registry:\n    pass\n",
            encoding="utf-8",
        )

        results = _apply_deterministic_unresolved_import_symbol_repair(
            adapter,
            task={"subject": "Fix Python import"},
            task_id="task-py-3",
            artifact_quality_errors=[
                (
                    "Artifact quality scan failed: unresolved import symbol "
                    "'Registry' from 'shared.registry' in "
                    "shared/__init__.py"
                ),
            ],
        )
        # Symbol already present -> no changes needed
        assert results == [], results
        registry_text = (tmp_path / "shared" / "registry.py").read_text(encoding="utf-8")
        # Must not duplicate the class definition
        assert registry_text.count("class Registry") == 1

    def test_skips_when_exporter_file_missing(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _apply_deterministic_unresolved_import_symbol_repair,
        )

        adapter = _make_adapter(tmp_path)
        (tmp_path / "shared").mkdir(parents=True)
        (tmp_path / "shared" / "__init__.py").write_text(
            "from shared.missing import Symbol\n",
            encoding="utf-8",
        )
        # shared/missing.py does not exist

        results = _apply_deterministic_unresolved_import_symbol_repair(
            adapter,
            task={"subject": "Fix Python import"},
            task_id="task-py-4",
            artifact_quality_errors=[
                (
                    "Artifact quality scan failed: unresolved import symbol "
                    "'Symbol' from 'shared.missing' in "
                    "shared/__init__.py"
                ),
            ],
        )
        # Cannot create the missing file deterministically -> return empty
        assert results == [], results


class TestDeterministicTypescriptReexportRepair:
    """Director can materialize a narrow TypeScript runtime re-export fix."""

    def test_repairs_missing_runtime_reexport(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        type_dir = tmp_path / "src" / "types"
        test_dir = type_dir / "__tests__"
        test_dir.mkdir(parents=True)
        (type_dir / "asset.ts").write_text(
            "export enum AssetType {\n  garment = 'garment',\n}\n",
            encoding="utf-8",
        )
        (type_dir / "generation.ts").write_text(
            "import type { Asset } from './asset';\n"
            "export enum TaskType {\n  garment_to_model = 'garment_to_model',\n}\n"
            "export interface GenerationSpec {\n  input_assets: Asset[];\n}\n",
            encoding="utf-8",
        )
        (test_dir / "spec.test.ts").write_text(
            "import { GenerationSpec, TaskType, AssetType } from '../generation';\nconst type = AssetType.garment;\n",
            encoding="utf-8",
        )

        results = _apply_deterministic_typescript_reexport_repair(
            adapter,
            task={
                "subject": "Repair TypeScript npm test failure",
                "description": (
                    "Vitest reports Cannot read properties of undefined while importing AssetType from ../generation."
                ),
            },
            task_id="task-1",
        )

        assert results and results[0]["success"] is True
        generation_text = (type_dir / "generation.ts").read_text(encoding="utf-8")
        assert "export { AssetType } from './asset';" in generation_text

        second = _apply_deterministic_typescript_reexport_repair(
            adapter,
            task={
                "subject": "Repair TypeScript npm test failure",
                "description": "Cannot read properties of undefined for AssetType",
            },
            task_id="task-1",
        )

        assert second == []
        updated_text = (type_dir / "generation.ts").read_text(encoding="utf-8")
        assert updated_text.count("export { AssetType } from './asset';") == 1

    def test_export_import_contract_fix_triggers_reexport_repair(self) -> None:
        assert (
            _looks_like_typescript_reexport_failure(
                "Apply a minimal TypeScript export/import contract fix; npm test must pass."
            )
            is True
        )


# ---------------------------------------------------------------------------
# Materialized metadata
# ---------------------------------------------------------------------------


class TestBuildMaterializedMetadata:
    """_build_materialized_metadata is a pure dict transformation."""

    def test_basic_fields(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        meta = adapter._build_materialized_metadata("req-1", {"goal": "g", "scope": "s", "steps": ["a"]})
        assert meta["goal"] == "g"
        assert meta["scope"] == "s"
        assert meta["steps"] == ["a"]
        assert meta["phase"] == "implementation"
        assert meta["pm_task_id"] == "req-1"
        assert meta["source"] == "director_adapter.materialized_orchestration_task"

    def test_input_metadata_merged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        meta = adapter._build_materialized_metadata(
            "req-1",
            {"metadata": {"custom": "v", "projection": {"x": 1}}},
        )
        assert meta["custom"] == "v"
        assert "projection" not in meta  # projection key is stripped

    def test_none_input_data(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        meta = adapter._build_materialized_metadata("req-1", None)  # type: ignore[arg-type]
        assert meta["pm_task_id"] == "req-1"

    def test_nested_pm_task_metadata_preserves_execution_contract(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        meta = adapter._build_materialized_metadata(
            "task-0-director",
            {
                "metadata": {
                    "id": "T01-001",
                    "goal": "Create the TypeScript foundation",
                    "target_files": ["package.json", "src/index.ts"],
                    "scope_paths": ["src/config"],
                    "blueprint_id": "ce_T01-001",
                }
            },
        )

        assert meta["pm_task_id"] == "T01-001"
        assert meta["target_files"] == ["package.json", "src/index.ts"]
        assert meta["scope_paths"] == ["src/config"]
        assert meta["blueprint_id"] == "ce_T01-001"


# ---------------------------------------------------------------------------
# Execution backend resolution
# ---------------------------------------------------------------------------


class TestResolveExecutionBackendRequest:
    """_resolve_execution_backend_request delegates to resolve_director_execution_backend."""

    def test_defaults_to_code_edit(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        req = adapter._resolve_execution_backend_request(
            task_id="t1",
            task={},
            input_data={},
            context={},
        )
        assert req.execution_backend == "code_edit"
        assert req.is_supported is True
        assert req.is_projection_backend is False

    def test_projection_hint_in_request(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        req = adapter._resolve_execution_backend_request(
            task_id="t1",
            task={"metadata": {"execution_backend": "projection_generate", "projection": {"scenario_id": "s1"}}},
            input_data={},
            context={},
        )
        assert req.execution_backend == "projection_generate"
        assert req.scenario_id == "s1"
        assert req.is_projection_backend is True


# ---------------------------------------------------------------------------
# Persist metadata
# ---------------------------------------------------------------------------


class TestPersistExecutionBackendMetadata:
    """_persist_execution_backend_metadata delegates to _update_board_task."""

    def test_noop_when_task_id_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # Should not raise even with no task_board
        adapter._persist_execution_backend_metadata("", MagicMock())

    def test_calls_update_board_task(self, tmp_path: Any) -> None:
        mock_runtime = MagicMock()
        adapter = _make_adapter(tmp_path, task_runtime=mock_runtime)
        from polaris.cells.roles.adapters.internal.director_execution_backend import DirectorExecutionBackendRequest

        req = DirectorExecutionBackendRequest(execution_backend="code_edit")
        adapter._persist_execution_backend_metadata("t1", req)
        mock_runtime.update_task.assert_called_once()


# ---------------------------------------------------------------------------
# Capabilities / role_id
# ---------------------------------------------------------------------------


class TestDirectorAdapterIdentity:
    def test_role_id(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter.role_id == "director"

    def test_capabilities(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        caps = adapter.get_capabilities()
        assert "execute_task" in caps
        assert "sequential_execution" in caps
        assert "adaptive_strategy_selection" in caps


class TestDirectorRuntimeFallback:
    @pytest.mark.asyncio
    async def test_role_dialogue_uses_role_runtime_context_os_path_first(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        captured: dict[str, Any] = {}

        class FakeRoleRuntimeService:
            async def execute_role_session(self, command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
                captured["command"] = command
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role="director",
                    workspace=str(tmp_path),
                    task_id=command.task_id,
                    session_id=command.session_id,
                    run_id=command.run_id,
                    output="done",
                    usage={"tokens": 10},
                    metadata={"provider_id": "anthropic_compat-test", "model": "kimi-for-coding"},
                )

        monkeypatch.setattr(
            "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
            FakeRoleRuntimeService,
        )

        result = await adapter._invoke_role_dialogue(
            "write src/app.ts",
            context={"run_id": "run-runtime-first", "task_id": "task-runtime-first"},
        )

        assert result["success"] is True
        assert result["metadata"]["role_runtime_entrypoint"] == "roles.runtime.execute_role_session"
        assert result["metadata"]["context_os_expected"] is True
        command = captured["command"]
        assert isinstance(command, ExecuteRoleSessionCommandV1)
        assert command.role == "director"
        assert command.domain == "code"
        assert command.stream is False
        assert command.run_id == "run-runtime-first"
        assert command.task_id == "task-runtime-first"
        assert command.metadata["role_runtime_required"] is True
        assert command.metadata["cognitive_runtime_required"] is True

    @pytest.mark.asyncio
    async def test_role_dialogue_fails_closed_when_runtime_boundary_unavailable(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = _make_adapter(tmp_path)

        class UnavailableRoleRuntimeService:
            def __init__(self) -> None:
                raise ImportError("runtime boundary unavailable")

        monkeypatch.setattr(
            "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
            UnavailableRoleRuntimeService,
        )

        with pytest.raises(RuntimeError, match="director_role_runtime_boundary_unavailable"):
            await adapter._invoke_role_dialogue("write src/app.ts")

    @pytest.mark.asyncio
    async def test_role_dialogue_runtime_execution_failure_does_not_fallback_to_legacy(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = _make_adapter(tmp_path)

        class FailingRoleRuntimeService:
            async def execute_role_session(self, command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
                del command
                raise RuntimeError("runtime provider failed")

        monkeypatch.setattr(
            "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
            FailingRoleRuntimeService,
        )

        with pytest.raises(RuntimeError, match="runtime provider failed"):
            await adapter._invoke_role_dialogue("write src/app.ts")

    @pytest.mark.asyncio
    async def test_direct_runtime_provider_bypass_is_removed(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        with pytest.raises(RuntimeError, match="director_direct_runtime_provider_removed"):
            await adapter._invoke_direct_runtime_provider("write a file", timeout_seconds=3)


# ---------------------------------------------------------------------------
# Integration with execution backend module (pure helpers)
# ---------------------------------------------------------------------------


class TestDirectorExecutionBackendPure:
    """Tests for the pure helper functions in director_execution_backend."""

    def test_normalize_backend(self) -> None:
        from polaris.cells.roles.adapters.internal.director_execution_backend import _normalize_backend

        assert _normalize_backend("code_edit") == "code_edit"
        assert _normalize_backend("projection_generate") == "projection_generate"
        assert _normalize_backend("") == "code_edit"
        assert _normalize_backend("unknown") == "unknown"

    def test_normalize_project_slug(self) -> None:
        from polaris.cells.roles.adapters.internal.director_execution_backend import _normalize_project_slug

        assert _normalize_project_slug("My Project", default_value="default") == "my_project"
        assert _normalize_project_slug("", default_value="default") == "default"

    def test_normalize_bool(self) -> None:
        from polaris.cells.roles.adapters.internal.director_execution_backend import _normalize_bool

        assert _normalize_bool(True, default=False) is True
        assert _normalize_bool("1", default=False) is True
        assert _normalize_bool("false", default=True) is False
        assert _normalize_bool(None, default=True) is True

    def test_request_to_task_metadata(self) -> None:
        from polaris.cells.roles.adapters.internal.director_execution_backend import DirectorExecutionBackendRequest

        req = DirectorExecutionBackendRequest(execution_backend="projection_generate", scenario_id="s1")
        meta = req.to_task_metadata()
        assert meta["execution_backend"] == "projection_generate"
        assert meta["projection"]["scenario_id"] == "s1"


def test_scaffold_synthesis_default_off(monkeypatch, tmp_path: Any) -> None:
    """§8 integrity: a declared TS target with no clean nearby source must never
    be fabricated — and the historical opt-in env vars are now permanently inert,
    so setting them re-arms nothing (the re-activation footgun is gone)."""
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _apply_deterministic_materialization_quality_repairs,
    )

    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )
    task = {
        "subject": "Define tenant model",
        "description": "Create the tenant model file.",
        "target_files": ["src/models/tenant.model.ts"],
    }
    artifact_quality_errors = [
        "Artifact quality scan failed: declared target file missing 'src/models/tenant.model.ts'",
    ]

    def _run() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return _apply_deterministic_materialization_quality_repairs(
            adapter,
            task=task,
            task_id="task-1",
            artifact_quality_errors=artifact_quality_errors,
        )

    # Default (env unset): nothing fabricated.
    monkeypatch.delenv("KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS", raising=False)
    monkeypatch.delenv("KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS", raising=False)
    results, summary = _run()
    assert results == []
    assert summary["attempted"] is False
    assert not (tmp_path / "src" / "models" / "tenant.model.ts").exists()

    # Env vars are now permanently inert: opting in fabricates nothing either.
    monkeypatch.setenv("KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS", "1")
    monkeypatch.setenv("KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS", "1")
    results, summary = _run()
    assert results == []
    assert summary["attempted"] is False
    assert not (tmp_path / "src" / "models" / "tenant.model.ts").exists()


def test_business_contract_synthesis_default_off(monkeypatch, tmp_path: Any) -> None:
    """§8 regression: Director must not fabricate business-domain service code
    unless a legacy benchmark explicitly opts into the synthesizer."""
    monkeypatch.delenv("KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS", raising=False)
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _apply_deterministic_materialization_quality_repairs,
    )

    task = {
        "subject": "Audit service",
        "description": "Create audit service and middleware.",
        "target_files": [
            "src/services/audit.service.ts",
            "src/middleware/audit.middleware.ts",
        ],
    }
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    errors = [
        "unresolved relative import './audit.entity' in src/services/audit.service.ts",
    ]

    results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task=task,
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results == []
    assert summary["attempted"] is False
    assert not (tmp_path / "src" / "services" / "audit.service.ts").exists()
    assert not (tmp_path / "src" / "middleware" / "audit.middleware.ts").exists()

    # The opt-in env var is now permanently inert: setting it fabricates nothing.
    monkeypatch.setenv("KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS", "1")
    results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task=task,
        task_id="task-1",
        artifact_quality_errors=errors,
    )
    assert results == []
    assert summary["attempted"] is False
    assert not (tmp_path / "src" / "services" / "audit.service.ts").exists()
    assert not (tmp_path / "src" / "middleware" / "audit.middleware.ts").exists()


def test_typescript_relative_import_case_repair_rewrites_importer_only(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _apply_deterministic_materialization_quality_repairs,
    )

    src = tmp_path / "src"
    src.mkdir(parents=True)
    garden = src / "Garden.ts"
    moon = src / "moon.ts"
    garden.write_text(
        "import { Moon } from './Moon';\nexport class Garden { moon = new Moon(); }\n",
        encoding="utf-8",
    )
    moon.write_text("export class Moon {}\n", encoding="utf-8")
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task={"target_files": ["src/Garden.ts", "src/moon.ts"]},
        task_id="task-2",
        artifact_quality_errors=["Artifact quality scan failed: unresolved relative import './Moon' in src/Garden.ts"],
    )

    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_relative_import_case_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert "from './moon'" in garden.read_text(encoding="utf-8")
    assert moon.read_text(encoding="utf-8") == "export class Moon {}\n"


def test_typescript_unresolved_unused_import_repair_removes_import(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _apply_deterministic_materialization_quality_repairs,
    )

    src = tmp_path / "src"
    src.mkdir(parents=True)
    main = src / "main.ts"
    main.write_text(
        'import { Garden } from "./engine/garden";\nconsole.log("ready");\n',
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task={"target_files": []},
        task_id="task-unused-import",
        artifact_quality_errors=[
            "Artifact quality scan failed: unresolved relative import './engine/garden' in src/main.ts"
        ],
    )

    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_unused_import_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert main.read_text(encoding="utf-8") == 'console.log("ready");\n'


def test_npm_test_script_repair_handles_ts_source_require_module_not_found(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _apply_deterministic_materialization_quality_repairs,
    )

    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "models" / "firefly.ts").write_text("export class Firefly {}\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n', encoding="utf-8")
    package_path = tmp_path / "package.json"
    package_path.write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "tsc",
                    "test": "node -e \"require('./src/models/firefly')\"",
                },
                "devDependencies": {"typescript": "^5.0.0"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task={"target_files": []},
        task_id="task-npm-test",
        artifact_quality_errors=[
            "Artifact quality scan failed: workspace validation command failed (npm test): "
            "node -e \"require('./src/models/firefly')\"\nError: Cannot find module './src/models/firefly'"
        ],
    )

    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_npm_script_contract_repair"
        for item in results
        if isinstance(item, dict)
    )
    payload = json.loads(package_path.read_text(encoding="utf-8"))
    assert payload["scripts"]["test"] == "npm run build"


def test_typescript_comma_expected_repair_fixes_object_literal_semicolons(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _apply_deterministic_materialization_quality_repairs,
    )

    model_dir = tmp_path / "src" / "models"
    model_dir.mkdir(parents=True)
    flower = model_dir / "flower.ts"
    flower.write_text(
        "\n".join(
            [
                "export enum FlowerType {",
                "  Moonflower = 'moonflower'",
                "}",
                "",
                "export interface FlowerState {",
                "  type: FlowerType;",
                "  wilted: boolean;",
                "}",
                "",
                "export class Flower implements FlowerState {",
                "  public type: FlowerType = FlowerType.Moonflower;",
                "  public wilted: boolean = false;",
                "",
                "  private calculateAttractiveness(): number {",
                "    const baseAttractiveness: Record<FlowerType, number> = {",
                "      [FlowerType.Moonflower]: 0.9;",
                "    };",
                "    return baseAttractiveness[this.type];",
                "  }",
                "",
                "  public getState(): FlowerState {",
                "    return {",
                "      type: this.type,",
                "      wilted: this.wilted;",
                "    };",
                "  }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task={"target_files": ["src/models/flower.ts"]},
        task_id="task-1",
        artifact_quality_errors=[
            "Artifact quality scan failed: syntax error in src/models/flower.ts: "
            "src/models/flower.ts(16,37): error TS1005: ',' expected."
        ],
    )

    repaired = flower.read_text(encoding="utf-8")
    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_return_object_semicolon_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert "[FlowerType.Moonflower]: 0.9," in repaired
    assert "wilted: this.wilted," in repaired
    assert "type: FlowerType;" in repaired
    assert "public type: FlowerType = FlowerType.Moonflower;" in repaired


def test_typescript_enum_member_separator_repair_fixes_enum_semicolon_only(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _apply_deterministic_materialization_quality_repairs,
    )

    model_dir = tmp_path / "src" / "models"
    model_dir.mkdir(parents=True)
    moonphase = model_dir / "moonphase.ts"
    moonphase.write_text(
        "\n".join(
            [
                "export enum MoonPhase {",
                "  New,",
                "  Full,",
                "  WaningCrescent;",
                "}",
                "",
                "export interface MoonState {",
                "  phase: MoonPhase;",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task={"target_files": ["src/models/moonphase.ts"]},
        task_id="task-1",
        artifact_quality_errors=[
            "TypeScript syntax check failed: "
            "src/models/moonphase.ts(4,18): error TS1357: "
            "An enum member name must be followed by a ',', '=', or '}'."
        ],
    )

    repaired = moonphase.read_text(encoding="utf-8")
    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_enum_member_separator_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert "  WaningCrescent," in repaired
    assert "  phase: MoonPhase;" in repaired


def test_typescript_missing_closing_brace_repair_fixes_ts1005_brace_expected(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _apply_deterministic_materialization_quality_repairs,
    )

    engine_dir = tmp_path / "src" / "engine"
    engine_dir.mkdir(parents=True)
    renderer = engine_dir / "renderer.ts"
    renderer.write_text(
        "\n".join(
            [
                "export function renderGarden(): string {",
                "  const firefly = { glow: 1 };",
                "  if (firefly.glow > 0) {",
                "    return 'firefly flower moon humidity';",
                "  }",
            ]
        ),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task={"target_files": ["src/engine/renderer.ts"]},
        task_id="task-1",
        artifact_quality_errors=[
            "TypeScript syntax check failed: src/engine/renderer.ts(1,3780): error TS1005: '}' expected."
        ],
    )

    repaired = renderer.read_text(encoding="utf-8")
    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_missing_closing_brace_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert repaired.rstrip().endswith("}")
    assert repaired.count("{") == repaired.count("}")


def test_typescript_unresolved_identifier_repair_uses_function_parameter_alias(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _apply_deterministic_materialization_quality_repairs,
    )

    engine_dir = tmp_path / "src" / "engine"
    engine_dir.mkdir(parents=True)
    simulation = engine_dir / "simulation.ts"
    simulation.write_text(
        "\n".join(
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
        ),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task={"target_files": ["src/engine/simulation.ts"]},
        task_id="task-1",
        artifact_quality_errors=[
            "Artifact quality scan failed: TypeScript project typecheck failed: "
            "src/engine/simulation.ts(10,8): error TS2304: Cannot find name 'newState'.",
            "src/engine/simulation.ts(11,8): error TS2304: Cannot find name 'newState'.",
            "src/engine/simulation.ts(12,8): error TS2304: Cannot find name 'newState'.",
        ],
    )

    repaired = simulation.read_text(encoding="utf-8")
    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_unresolved_identifier_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert "const newState = { ...state" in repaired
    assert "return newState;" in repaired
    assert "`${state.moonPhase}`;" in repaired
    assert "`${state.humidity}`;" in repaired
    assert "`${state.tick}`;" in repaired


def test_typescript_comma_expected_repair_accepts_plain_tsc_error_format(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _apply_deterministic_materialization_quality_repairs,
    )

    model_dir = tmp_path / "src" / "models"
    model_dir.mkdir(parents=True)
    flower = model_dir / "Flower.ts"
    flower.write_text(
        "\n".join(
            [
                "export interface FlowerState {",
                "  color: string;",
                "}",
                "",
                "export class Flower {",
                "  public state: FlowerState;",
                "  constructor(color: string) {",
                "    this.state = {",
                "      color;",
                "    };",
                "  }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task={"target_files": ["src/models/Flower.ts"]},
        task_id="task-1",
        artifact_quality_errors=["src/models/Flower.ts(9,12): error TS1005: ',' expected."],
    )

    repaired = flower.read_text(encoding="utf-8")
    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_return_object_semicolon_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert "      color,\n" in repaired
    assert "      color;\n" not in repaired


def test_materialization_quality_errors_scan_declared_target_files(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _collect_materialization_quality_errors,
    )

    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "changed.ts").write_text("export const changed = 1;\n", encoding="utf-8")
    (src / "declared.ts").write_text(
        "export function broken() {\n  return {\n    value: 1;\n  };\n}\n",
        encoding="utf-8",
    )
    adapter = SimpleNamespace(workspace=str(tmp_path))

    errors = _collect_materialization_quality_errors(
        adapter,
        task={"target_files": ["src/changed.ts", "src/declared.ts"]},
        all_affected_files=["src/changed.ts"],
        workspace_name=tmp_path.name,
    )

    assert any("src/declared.ts" in error for error in errors)


def test_materialization_quality_errors_keep_pinned_step_single_file_scope(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _collect_materialization_quality_errors,
    )

    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "pinned.ts").write_text("export const pinned = 1;\n", encoding="utf-8")
    (src / "other.ts").write_text(
        "export function broken() {\n  return {\n    value: 1;\n  };\n}\n",
        encoding="utf-8",
    )
    adapter = SimpleNamespace(workspace=str(tmp_path))

    errors = _collect_materialization_quality_errors(
        adapter,
        task={"target_files": ["src/pinned.ts", "src/other.ts"]},
        all_affected_files=["src/pinned.ts"],
        workspace_name=tmp_path.name,
        context={"construction_step": {"target_file": "src/pinned.ts"}},
    )

    assert not any("src/other.ts" in error for error in errors)


def test_placeholder_node_test_synthesis_default_off(monkeypatch, tmp_path: Any) -> None:
    """§8 regression: missing test files should not be masked by fabricated
    placeholder tests in production/director hot paths."""
    monkeypatch.delenv("KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS", raising=False)
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _apply_deterministic_materialization_quality_repairs,
    )

    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    errors = [
        "npm package manifest has test runner script but no test/spec files exist in package.json",
    ]

    results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task={"target_files": ["package.json"]},
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results == []
    assert summary["attempted"] is False
    assert not (tmp_path / "tests" / "unit" / "workspace.test.ts").exists()

    # The opt-in env var is now permanently inert: setting it fabricates nothing.
    monkeypatch.setenv("KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS", "1")
    results, summary = _apply_deterministic_materialization_quality_repairs(
        adapter,
        task={"target_files": ["package.json"]},
        task_id="task-1",
        artifact_quality_errors=errors,
    )
    assert results == []
    assert summary["attempted"] is False
    assert not (tmp_path / "tests" / "unit" / "workspace.test.ts").exists()


class TestDeclaredPathCaseInsensitiveMatching:
    """L2-09 PM-0001-2 regression: PM declared "readme.md", Director wrote
    "README.md"; the case-sensitive declared-path filter dropped the task's
    only real output and produced director_no_materialized_changes."""

    def test_filter_keeps_case_mismatched_target(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_diff_to_task_declared_paths,
        )

        new_files, modified_files = _filter_diff_to_task_declared_paths(
            task={"target_files": ["readme.md"]},
            new_files=["README.md"],
            modified_files=[],
        )
        assert new_files == ["README.md"]
        assert modified_files == []

    def test_filter_keeps_exact_case_target(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_diff_to_task_declared_paths,
        )

        new_files, _ = _filter_diff_to_task_declared_paths(
            task={"target_files": ["src/App.tsx"]},
            new_files=["src/App.tsx", "src/unrelated.ts"],
            modified_files=[],
        )
        assert new_files == ["src/App.tsx"]

    def test_filter_still_excludes_unrelated_files(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_diff_to_task_declared_paths,
        )

        new_files, modified_files = _filter_diff_to_task_declared_paths(
            task={"target_files": ["readme.md"]},
            new_files=["game.js"],
            modified_files=["index.html"],
        )
        assert new_files == []
        assert modified_files == []

    def test_out_of_scope_diff_reports_filtered_real_output(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _collect_workspace_out_of_scope_diff,
        )

        diff = _collect_workspace_out_of_scope_diff(
            task={"scope": ["src/python"], "target_files": ["src/python/guess_number.py"]},
            baseline_files={},
            current_files={"guess_number.py": "fingerprint"},
        )

        assert diff["affected_files"] == ["guess_number.py"]
        assert diff["new_files"] == ["guess_number.py"]
        assert diff["modified_files"] == []

    def test_out_of_scope_diff_ignores_declared_output(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _collect_workspace_out_of_scope_diff,
        )

        diff = _collect_workspace_out_of_scope_diff(
            task={"scope": ["src/python"], "target_files": ["src/python/guess_number.py"]},
            baseline_files={},
            current_files={"src/python/guess_number.py": "fingerprint"},
        )

        assert diff["affected_files"] == []
        assert diff["new_files"] == []
        assert diff["modified_files"] == []

    def test_directory_candidate_matches_case_insensitively(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _path_matches_declared_candidate,
        )

        assert _path_matches_declared_candidate("Docs/Guide.md", "docs")
        assert _path_matches_declared_candidate("src/app.PY", "src/app.py")
        assert not _path_matches_declared_candidate("other/file.md", "docs")

    def test_glob_candidate_matches_case_insensitively(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _glob_path_matches,
        )

        assert _glob_path_matches("README.md", "readme.*")
        assert _glob_path_matches("src/Views/Home.vue", "src/**/home.vue")


class TestAcceptanceVerifyExistsExemption:
    """L2-09 class: identical-rewrite / case-variant writes produce an empty
    diff; when the PM contract's own `verify <path> exists` machine checks all
    pass AND write receipts exist, the task is satisfied, not failed."""

    @staticmethod
    def _evaluate(task: dict, workspace: Any, write_tool_evidence: bool = True) -> tuple[bool, dict]:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _evaluate_acceptance_verify_exists,
        )

        return _evaluate_acceptance_verify_exists(
            task=task,
            workspace_full=str(workspace),
            write_tool_evidence=write_tool_evidence,
        )

    def test_all_assertions_pass(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["包含运行说明", "verify ./readme.md exists"]},
            tmp_path,
        )
        assert satisfied is True
        assert evidence == {"checked": 1, "passed": ["readme.md"], "missing": []}

    def test_missing_path_not_exempted(self, tmp_path) -> None:
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["verify ./readme.md exists"]},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["missing"] == ["readme.md"]

    def test_no_machine_assertions_no_exemption(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["README.md 存在于工作区根"]},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["checked"] == 0

    def test_requires_write_tool_evidence(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
        satisfied, _ = self._evaluate(
            {"acceptance_criteria": ["verify ./readme.md exists"]},
            tmp_path,
            write_tool_evidence=False,
        )
        assert satisfied is False

    def test_nested_path_case_insensitive(self, tmp_path) -> None:
        (tmp_path / "Docs").mkdir()
        (tmp_path / "Docs" / "Guide.md").write_text("g\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["verify docs/guide.md exists"]},
            tmp_path,
        )
        assert satisfied is True
        assert evidence["passed"] == ["docs/guide.md"]

    def test_one_missing_among_many_blocks_exemption(self, tmp_path) -> None:
        (tmp_path / "a.md").write_text("a\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["verify a.md exists", "verify b.md exists"]},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["passed"] == ["a.md"]
        assert evidence["missing"] == ["b.md"]

    def test_posix_test_file_assertion_passes(self, tmp_path) -> None:
        (tmp_path / "requirements.txt").write_text("# standard library only\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["test -f requirements.txt"]},
            tmp_path,
        )
        assert satisfied is True
        assert evidence == {"checked": 1, "passed": ["requirements.txt"], "missing": []}

    def test_posix_test_and_grep_assertion_passes(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("Run with python3 calculator.py\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["test -f README.md && grep -q 'python3 calculator.py' README.md"]},
            tmp_path,
        )
        assert satisfied is True
        assert evidence == {"checked": 1, "passed": ["README.md"], "missing": []}

    def test_posix_grep_literal_miss_blocks_exemption(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("Run with python calculator.py\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["test -f README.md && grep -q 'python3 calculator.py' README.md"]},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["checked"] == 1
        assert evidence["passed"] == ["README.md"]
        assert evidence["missing"] == ["README.md"]

    def test_arbitrary_shell_command_not_exempted(self, tmp_path) -> None:
        (tmp_path / "calculator.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ['python3 -c "import calculator; print(calculator.add(2,3))"']},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["checked"] == 0


class TestQualityRepairMissingTargetContract:
    """L2-10 r3 regression: the repair turn rewrote src/main.js (already
    present) instead of creating the missing src/styles.css — the repair
    message itself seeded the wrong target by listing changed files as paths."""

    def test_missing_declared_targets_derived_from_workspace(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_declared_target_files,
        )

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.js").write_text("x\n", encoding="utf-8")
        (tmp_path / "index.html").write_text("<html></html>\n", encoding="utf-8")
        task = {"target_files": ["index.html", "src/main.js", "src/styles.css", "package.json"]}
        missing = _missing_declared_target_files(task, str(tmp_path))
        assert missing == ["src/styles.css", "package.json"]

    def test_missing_targets_case_insensitive(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_declared_target_files,
        )

        (tmp_path / "README.md").write_text("r\n", encoding="utf-8")
        task = {"target_files": ["readme.md"]}
        assert _missing_declared_target_files(task, str(tmp_path)) == []

    def test_satisfied_declared_target_missing_errors_are_filtered(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_satisfied_declared_target_missing_errors,
        )

        (tmp_path / "records.json").write_text("[]\n", encoding="utf-8")

        errors = _filter_satisfied_declared_target_missing_errors(
            [
                "Artifact quality scan failed: declared target file missing 'records.json'",
                "Artifact quality scan failed: unresolved relative import './router' in src/main.tsx",
            ],
            str(tmp_path),
        )

        assert errors == ["Artifact quality scan failed: unresolved relative import './router' in src/main.tsx"]

    @pytest.mark.asyncio
    async def test_quality_repair_retry_targets_unresolved_relative_import(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_message = ""

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                self.repair_message = message
                return {"content": ""}

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.tsx").write_text(
            "import { router } from './router';\n",
            encoding="utf-8",
        )
        task = {"target_files": ["src/main.tsx"]}
        adapter = _Adapter()

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task=task,
            target_task_id="task-1",
            run_id="run-1",
            context={},
            original_message="Create the React app shell.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved relative import './router' in src/main.tsx",
            ],
            changed_files=["src/main.tsx"],
        )

        assert summary["missing_target_files"] == ["src/router.tsx"]
        assert "MISSING TARGET FILES" in adapter.repair_message
        assert "src/router.tsx" in adapter.repair_message

    def test_repair_targets_css_import_exact_path(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.tsx").write_text(
            "import './styles/global.css';\n",
            encoding="utf-8",
        )

        missing = _missing_materialization_quality_repair_target_files(
            {"target_files": ["src/app.tsx"]},
            str(tmp_path),
            ["Artifact quality scan failed: unresolved relative import './styles/global.css' in src/app.tsx"],
        )

        assert missing == ["src/styles/global.css"]

    def test_repair_targets_unresolved_import_before_unrelated_declared_targets(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        (tmp_path / "src" / "data").mkdir(parents=True)
        (tmp_path / "src" / "data" / "seed.ts").write_text(
            "import { Flower } from './types';\nexport const flowers = [];\n",
            encoding="utf-8",
        )

        missing = _missing_materialization_quality_repair_target_files(
            {
                "target_files": [
                    "src/data/seed.ts",
                    "scripts/verify-rules.ts",
                ],
            },
            str(tmp_path),
            ["Artifact quality scan failed: unresolved relative import './types' in src/data/seed.ts"],
        )

        assert missing == ["src/data/types.ts", "scripts/verify-rules.ts"]

    def test_unresolved_import_repair_batches_all_missing_declared_targets(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc","start":"node dist/index.js"}}\n',
            encoding="utf-8",
        )
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"outDir":"dist"}}\n', encoding="utf-8")
        (tmp_path / "src" / "models" / "flower.ts").write_text(
            "export class Flower {}\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "models" / "firefly.ts").write_text(
            "import { Moon } from './moon';\nexport class Firefly { moon?: Moon; }\n",
            encoding="utf-8",
        )

        missing = _missing_materialization_quality_repair_target_files(
            {
                "target_files": [
                    "package.json",
                    "tsconfig.json",
                    "src/models/flower.ts",
                    "src/models/firefly.ts",
                    "src/models/moon.ts",
                    "src/models/humidity.ts",
                    "src/engine/garden.ts",
                    "src/index.ts",
                ],
            },
            str(tmp_path),
            ["Artifact quality scan failed: unresolved relative import './moon' in src/models/firefly.ts"],
        )

        assert missing == [
            "src/models/moon.ts",
            "src/models/humidity.ts",
            "src/engine/garden.ts",
            "src/index.ts",
        ]

    @pytest.mark.asyncio
    async def test_package_manifest_quality_error_targets_package_json(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_message = ""
                self.repair_context: dict[str, Any] = {}

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                self.repair_message = message
                self.repair_context = context
                return {"content": ""}

        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"test": "jest"},
                    "devDependencies": {"jest": "^29.0.0"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        adapter = _Adapter()

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["package.json"]},
            target_task_id="task-1",
            run_id="run-1",
            context={},
            original_message="Create package manifest.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest has test runner script "
                "but no test/spec files exist in package.json",
            ],
            changed_files=["package.json"],
        )

        assert summary["semantic_quality_target_files"] == ["package.json"]
        assert summary["repair_target_files"] == ["package.json"]
        assert adapter.repair_context["director_quality_repair"]["write_only_single_target"] == {
            "tool": "write_file",
            "target_file": "package.json",
        }
        assert "NPM PACKAGE MANIFEST REPAIR" in adapter.repair_message

    @pytest.mark.asyncio
    async def test_package_manifest_quality_error_preserves_missing_targets(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_message = ""
                self.repair_context: dict[str, Any] = {}

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del timeout_seconds, stage_label
                self.repair_message = message
                self.repair_context = context
                return {"content": ""}

        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"build": "tsc", "start": "node dist/main.js", "test": "node dist/main.js"},
                    "devDependencies": {"typescript": "^5.3.0"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        adapter = _Adapter()

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["package.json", "README.md", "src/main.ts", "index.html"]},
            target_task_id="task-1",
            run_id="run-1",
            context={},
            original_message="Create TypeScript project scaffold.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest script "
                "'start' references missing local entrypoint 'dist/main.js'",
                "Artifact quality scan failed: declared target file 'README.md' is missing",
                "Artifact quality scan failed: declared target file 'src/main.ts' is missing",
                "Artifact quality scan failed: declared target file 'index.html' is missing",
            ],
            changed_files=["package.json"],
        )

        assert summary["semantic_quality_target_files"] == ["package.json"]
        assert summary["missing_target_files"] == ["README.md", "src/main.ts", "index.html"]
        assert summary["repair_target_files"] == ["README.md", "src/main.ts", "index.html", "package.json"]
        assert "write_only_single_target" not in adapter.repair_context["director_quality_repair"]
        assert "NPM PACKAGE MANIFEST REPAIR" in adapter.repair_message
        assert "MISSING TARGET FILES" in adapter.repair_message

    @pytest.mark.asyncio
    async def test_package_manifest_quality_error_targets_existing_path_when_changed_files_empty(
        self, tmp_path
    ) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                return {"content": ""}

        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"build": "tsc", "start": "node dist/index.js"},
                    "devDependencies": {"typescript": "^5.3.0"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        _, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["package.json", "src/config.ts"]},
            target_task_id="task-1",
            run_id="run-1",
            context={},
            original_message="Create TypeScript project scaffold.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest script "
                "'start' references missing local entrypoint 'dist/index.js' in package.json"
            ],
            changed_files=[],
        )

        assert summary["semantic_quality_target_files"] == ["package.json"]
        assert summary["repair_target_files"] == ["package.json"]

    @pytest.mark.asyncio
    async def test_quality_repair_second_attempt_rotates_to_typescript_source_after_package_target(
        self, tmp_path
    ) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_context: dict[str, Any] = {}

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, timeout_seconds, stage_label
                self.repair_context = context
                return {"content": ""}

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text("const config = { humidity: 65.0; };\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"build": "tsc", "start": "node dist/index.js"},
                    "devDependencies": {"typescript": "^5.3.0"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        adapter = _Adapter()

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["package.json", "src/index.ts"]},
            target_task_id="task-1",
            run_id="run-1",
            context={},
            original_message="Create TypeScript garden simulation.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest script "
                "'start' references missing local entrypoint 'dist/index.js' in package.json",
                "Artifact quality scan failed: TypeScript project typecheck failed: "
                "src/index.ts(16,21): error TS1005: ',' expected.",
            ],
            changed_files=["package.json", "src/index.ts"],
            repair_attempt=2,
        )

        assert summary["semantic_quality_target_files"] == ["package.json", "src/index.ts"]
        assert summary["repair_target_files"] == ["src/index.ts"]
        assert adapter.repair_context["director_quality_repair"]["write_only_single_target"] == {
            "tool": "write_file",
            "target_file": "src/index.ts",
        }

    def test_repair_targets_existing_import_case_variant_is_not_missing(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Router.TSX").write_text(
            "export const router = {};\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "main.tsx").write_text(
            "import { router } from './router';\n",
            encoding="utf-8",
        )

        missing = _missing_materialization_quality_repair_target_files(
            {"target_files": ["src/main.tsx"]},
            str(tmp_path),
            ["Artifact quality scan failed: unresolved relative import './router' in src/main.tsx"],
        )

        assert missing == []

    @pytest.mark.asyncio
    async def test_single_missing_target_repair_sets_forced_write_context(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_context: dict[str, Any] = {}

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, timeout_seconds, stage_label
                self.repair_context = context
                return {"content": ""}

        (tmp_path / "services" / "product_service").mkdir(parents=True)
        adapter = _Adapter()

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["services/product_service/app.py"]},
            target_task_id="PM-0001-1",
            run_id="run-single-missing-write-only",
            context={},
            original_message="Create the missing product service file.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: declared target file missing 'services/product_service/app.py'"
            ],
            changed_files=["services/product_service/__init__.py"],
        )

        assert summary["missing_target_files"] == ["services/product_service/app.py"]
        assert adapter.repair_context["_transaction_kernel_forced_tool_choice"] == {
            "type": "function",
            "function": {"name": "write_file"},
        }
        assert (
            adapter.repair_context["_transaction_kernel_forced_tool_definitions"][0]["function"]["name"] == "write_file"
        )
        assert adapter.repair_context["_transaction_kernel_forced_tool_definitions"][0]["function"]["parameters"][
            "required"
        ] == ["file", "content"]
        assert adapter.repair_context["director_quality_repair"]["write_only_single_target"] == {
            "tool": "write_file",
            "target_file": "services/product_service/app.py",
        }

    @pytest.mark.asyncio
    async def test_single_missing_target_repair_coerces_raw_content_to_write_file(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                return {"content": "```python\nprint('service ready')\n```", "success": True}

        (tmp_path / "services" / "product_service").mkdir(parents=True)

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["services/product_service/app.py"]},
            target_task_id="PM-0001-1",
            run_id="run-single-missing-raw-content",
            context={},
            original_message="Create the missing product service file.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: declared target file missing 'services/product_service/app.py'"
            ],
            changed_files=[],
        )

        assert summary["tool_results"] == 1
        assert summary["write_tool_evidence"] is True
        assert tool_results[0]["result"]["source_tool"] == "director_quality_repair_raw_single_target_write_file"
        assert (tmp_path / "services" / "product_service" / "app.py").read_text(encoding="utf-8") == (
            "print('service ready')"
        )

    @pytest.mark.asyncio
    async def test_single_target_repair_refuses_raw_tool_receipt_content(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                return {
                    "content": (
                        "**write_file**: Error - {'ok': False, 'error': "
                        "'Destructive shrink rejected: this edit would replace tests/garden.test.ts'}"
                    ),
                    "success": True,
                }

        (tmp_path / "tests").mkdir(parents=True)

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["tests/simulation.test.ts"]},
            target_task_id="PM-0001-2",
            run_id="run-tool-receipt-raw-content",
            context={},
            original_message="Create the missing simulation test file.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: declared target file missing 'tests/simulation.test.ts'"
            ],
            changed_files=[],
        )

        assert tool_results == []
        assert summary["write_tool_evidence"] is False
        assert not (tmp_path / "tests" / "simulation.test.ts").exists()

    @pytest.mark.asyncio
    async def test_quality_repair_timeout_is_bounded_below_director_call_timeout(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.timeout_seconds = 0.0

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, stage_label
                self.timeout_seconds = timeout_seconds
                return {"content": ""}

        adapter = _Adapter()

        await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["package.json"]},
            target_task_id="PM-0001-1",
            run_id="run-quality-repair-timeout",
            context={},
            original_message="Repair package manifest.",
            llm_call_timeout=900,
            artifact_quality_errors=["Artifact quality scan failed: npm placeholder test script in package.json"],
            changed_files=["package.json"],
        )

        assert adapter.timeout_seconds == 180.0

    @pytest.mark.asyncio
    async def test_existing_python_runtime_smoke_failure_repair_forces_write_context(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            def __init__(self) -> None:
                self.allowed_tool_names: set[str] | None = None
                self.allow_patch_fallback: bool | None = None

            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            async def execute_tools(
                self,
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **kwargs: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                self.allowed_tool_names = kwargs.get("allowed_tool_names")
                self.allow_patch_fallback = kwargs.get("allow_patch_fallback")
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self._execution = _Execution()
                self.repair_context: dict[str, Any] = {}
                self.repair_message = ""

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del timeout_seconds, stage_label
                self.repair_context = context
                self.repair_message = message
                return {"content": ""}

        adapter = _Adapter()
        (tmp_path / "calculator.py").write_text("print('placeholder')\n", encoding="utf-8")

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["calculator.py"]},
            target_task_id="PM-0001-1",
            run_id="run-runtime-smoke-write-only",
            context={},
            original_message="Create a runnable Python CLI script.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke crashed for 'calculator.py' "
                "(returncode=2); tail:\nusage: calculator.py [-h] a {+,-,*,/} b"
            ],
            changed_files=["calculator.py"],
        )

        assert summary["missing_target_files"] == []
        assert summary["runtime_smoke_target_files"] == ["calculator.py"]
        assert summary["repair_target_files"] == ["calculator.py"]
        assert adapter.repair_context["_transaction_kernel_forced_tool_choice"] == {
            "type": "function",
            "function": {"name": "write_file"},
        }
        assert adapter.repair_context["director_quality_repair"]["write_only_single_target"] == {
            "tool": "write_file",
            "target_file": "calculator.py",
        }
        assert adapter._execution.allowed_tool_names == {"write_file"}
        assert adapter._execution.allow_patch_fallback is False
        assert "EXISTING FAILED TARGET FILES" in adapter.repair_message
        assert "SINGLE FAILED TARGET REPAIR" in adapter.repair_message
        assert "MISSING TARGET FILES" not in adapter.repair_message
        assert "calculator.py" in adapter.repair_message
        assert "rewrite only the existing failed target" in adapter.repair_message

    @pytest.mark.asyncio
    async def test_python_runtime_smoke_repair_targets_existing_workspace_file_across_tasks(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            def __init__(self) -> None:
                self.allowed_tool_names: set[str] | None = None
                self.allow_patch_fallback: bool | None = None

            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            async def execute_tools(
                self,
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **kwargs: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                self.allowed_tool_names = kwargs.get("allowed_tool_names")
                self.allow_patch_fallback = kwargs.get("allow_patch_fallback")
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self._execution = _Execution()
                self.repair_context: dict[str, Any] = {}
                self.repair_message = ""

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del timeout_seconds, stage_label
                self.repair_context = context
                self.repair_message = message
                return {"content": ""}

        adapter = _Adapter()
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "cli.py").write_text(
            "from src.parser import ExpressionParser\nprint(ExpressionParser)\n",
            encoding="utf-8",
        )
        (tmp_path / "README.md").write_text("placeholder\n", encoding="utf-8")

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["README.md"]},
            target_task_id="PM-0001-3",
            run_id="run-runtime-smoke-cross-task",
            context={},
            original_message="Complete the README for the CLI project.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke crashed for 'src/cli.py' "
                "(returncode=1); tail:\n"
                "Traceback (most recent call last):\n"
                '  File "/tmp/work/src/cli.py", line 1, in <module>\n'
                "ModuleNotFoundError: No module named 'src'"
            ],
            changed_files=["README.md"],
        )

        assert summary["missing_target_files"] == []
        assert summary["runtime_smoke_target_files"] == ["src/cli.py"]
        assert summary["repair_target_files"] == ["src/cli.py"]
        assert adapter.repair_context["director_quality_repair"]["write_only_single_target"] == {
            "tool": "write_file",
            "target_file": "src/cli.py",
        }
        assert adapter._execution.allowed_tool_names == {"write_file"}
        assert adapter._execution.allow_patch_fallback is False
        assert "SINGLE FAILED TARGET REPAIR" in adapter.repair_message
        assert "src/cli.py" in adapter.repair_message

    def test_python_runtime_smoke_test_failure_prefers_traceback_source_file(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _python_runtime_smoke_repair_target_files,
        )

        (tmp_path / "calculator.py").write_text("def tokenize(value):\n    return []\n", encoding="utf-8")
        (tmp_path / "test_calculator.py").write_text(
            "from calculator import tokenize\n\ndef test_empty():\n    assert tokenize('') == []\n",
            encoding="utf-8",
        )

        targets = _python_runtime_smoke_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke crashed for 'test_calculator.py' "
                "(returncode=1); tail:\n"
                '  File "/tmp/work/test_calculator.py", line 230, in test_tokenize_empty_raises\n'
                "    with self.assertRaises(SyntaxError):\n"
                '  File "calculator.py", line 34, in tokenize\n'
                "    raise SyntaxError('bad')\n"
                "AssertionError: SyntaxError not raised"
            ],
            changed_files=["test_calculator.py"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["calculator.py", "test_calculator.py"]

    def test_python_runtime_smoke_test_failure_infers_imported_workspace_module(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _python_runtime_smoke_repair_target_files,
        )

        (tmp_path / "calculator.py").write_text("def evaluate_expression(value):\n    return value\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_calculator.py").write_text(
            "import calculator\n\n"
            "def call_calculator(expression):\n"
            "    raise AssertionError('calculator module must expose parse_and_evaluate(), evaluate(), or calculate()')\n",
            encoding="utf-8",
        )

        targets = _python_runtime_smoke_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_calculator.py' "
                "(returncode=1); tail:\n"
                f'  File "{tmp_path / "tests" / "test_calculator.py"}", line 4, in call_calculator\n'
                "    raise AssertionError('calculator module must expose parse_and_evaluate(), evaluate(), or calculate()')\n"
                "AssertionError: calculator module must expose parse_and_evaluate(), evaluate(), or calculate()"
            ],
            changed_files=["README.md", "tests/test_calculator.py"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["calculator.py", "tests/test_calculator.py"]

    @pytest.mark.asyncio
    async def test_semantic_quality_single_changed_file_repair_forces_write_context(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            def __init__(self) -> None:
                self.allowed_tool_names: set[str] | None = None
                self.allow_patch_fallback: bool | None = None

            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            async def execute_tools(
                self,
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **kwargs: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                self.allowed_tool_names = kwargs.get("allowed_tool_names")
                self.allow_patch_fallback = kwargs.get("allow_patch_fallback")
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self._execution = _Execution()
                self.repair_context: dict[str, Any] = {}
                self.repair_message = ""

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del timeout_seconds, stage_label
                self.repair_context = context
                self.repair_message = message
                return {"content": ""}

        adapter = _Adapter()
        (tmp_path / "test_calculator.py").write_text(
            "from __future__ import annotations\n\n\ndef workspace_artifact_ready() -> bool:\n    return True\n",
            encoding="utf-8",
        )

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["test_calculator.py"]},
            target_task_id="PM-0001-3",
            run_id="run-semantic-quality-write-only",
            context={},
            original_message="Create meaningful calculator tests.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Director output quality gate failed: no project-domain signal found in changed files; "
                "expected one of calculator, arithmetic, expression"
            ],
            changed_files=["test_calculator.py"],
        )

        assert summary["missing_target_files"] == []
        assert summary["semantic_quality_target_files"] == ["test_calculator.py"]
        assert summary["repair_target_files"] == ["test_calculator.py"]
        assert adapter.repair_context["director_quality_repair"]["write_only_single_target"] == {
            "tool": "write_file",
            "target_file": "test_calculator.py",
        }
        assert adapter._execution.allowed_tool_names == {"write_file"}
        assert adapter._execution.allow_patch_fallback is False
        assert "EXISTING FAILED TARGET FILES" in adapter.repair_message
        assert "SINGLE FAILED TARGET REPAIR" in adapter.repair_message
        assert "test_calculator.py" in adapter.repair_message

    def test_semantic_quality_repair_uses_explicit_error_path_when_multiple_files_changed(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _semantic_quality_repair_target_files,
        )

        (tmp_path / "README.md").write_text("# Todo App\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_product.py").write_text("raise NotImplementedError\n", encoding="utf-8")

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=[
                "Director output quality gate failed: generic/placeholder content detected: "
                "tests/test_product.py:\\bNotImplemented(?:Error|Exception)?\\b"
            ],
            changed_files=["README.md", "tests/test_product.py"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["tests/test_product.py"]

    def test_explicit_artifact_quality_repair_targets_prettier_syntax_path(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _explicit_artifact_quality_repair_target_files,
        )

        source = tmp_path / "src" / "engine" / "simulation.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const broken = 'unterminated\n", encoding="utf-8")
        (tmp_path / "index.html").write_text('<div id="app"></div>\n', encoding="utf-8")

        targets = _explicit_artifact_quality_repair_target_files(
            artifact_quality_errors=[
                "[error] src/engine/simulation.ts: SyntaxError: Unterminated string literal. (1:24)"
            ],
            changed_files=["src/engine/simulation.ts", "index.html"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/engine/simulation.ts"]

    @pytest.mark.asyncio
    async def test_quality_repair_forces_write_for_explicit_artifact_syntax_path(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            def __init__(self) -> None:
                self.allowed_tool_names: set[str] | None = None
                self.allow_patch_fallback: bool | None = None

            @staticmethod
            def extract_kernel_tool_results(result) -> list:
                del result
                return []

            async def execute_tools(
                self,
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **kwargs: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                self.allowed_tool_names = kwargs.get("allowed_tool_names")
                self.allow_patch_fallback = kwargs.get("allow_patch_fallback")
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self._execution = _Execution()
                self.repair_context: dict[str, Any] = {}
                self.repair_message = ""

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del timeout_seconds, stage_label
                self.repair_context = context
                self.repair_message = message
                return {"content": ""}

        source = tmp_path / "src" / "engine" / "simulation.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const broken = 'unterminated\n", encoding="utf-8")
        (tmp_path / "index.html").write_text('<div id="app"></div>\n', encoding="utf-8")

        adapter = _Adapter()
        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["src/engine/simulation.ts", "index.html"]},
            target_task_id="PM-0001-9",
            run_id="run-explicit-artifact-quality-write-only",
            context={},
            original_message="Implement the simulation engine and app shell.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "[error] src/engine/simulation.ts: SyntaxError: Unterminated string literal. (1:24)"
            ],
            changed_files=["src/engine/simulation.ts", "index.html"],
        )

        assert summary["explicit_quality_target_files"] == ["src/engine/simulation.ts"]
        assert summary["repair_target_files"] == ["src/engine/simulation.ts"]
        assert adapter.repair_context["director_quality_repair"]["write_only_single_target"] == {
            "tool": "write_file",
            "target_file": "src/engine/simulation.ts",
        }
        assert adapter._execution.allowed_tool_names == {"write_file"}
        assert adapter._execution.allow_patch_fallback is False
        assert "EXISTING FAILED TARGET FILES" in adapter.repair_message
        assert "SINGLE FAILED TARGET REPAIR" in adapter.repair_message
        assert "src/engine/simulation.ts" in adapter.repair_message

    def test_semantic_quality_repair_accepts_new_catalog_languages(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _semantic_quality_repair_target_files,
        )

        source = tmp_path / "src" / "dragon_factory.go"
        source.parent.mkdir(parents=True)
        source.write_text("package main\n\nfunc main() {}\n", encoding="utf-8")

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=[
                "Director output quality gate failed: generic/placeholder content detected: src/dragon_factory.go:TODO"
            ],
            changed_files=["README.md", "src/dragon_factory.go"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/dragon_factory.go"]

    @pytest.mark.asyncio
    async def test_multi_missing_target_repair_forces_write_tool_choice(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """General C7 L6 gap: when multiple declared target files are missing,
        the weak Director LLM (e.g. qwen3.6-27b-int4) is structurally prone
        to echo the long repair prompt back as its response with zero tool
        calls. Live factory-bench L6-32 (2026-06-17): all three repair
        attempts echoed the prompt verbatim and the loop broke after the hard
        cap. The single-missing path already forces tool_choice=write_file;
        the multi-missing path must enforce the same structural constraint
        so the model cannot sidestep the contract.
        """
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        # Deterministic repair must NOT mask this with synthesized content;
        # we are testing the LLM repair path contract, not the synthesizer.
        monkeypatch.delenv("KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS", raising=False)
        monkeypatch.delenv("KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS", raising=False)

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result) -> list:
                return []

            @staticmethod
            async def execute_tools(content, target_task_id, update_task_progress, **_) -> list:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *a, **k: None)

            def __init__(self) -> None:
                self.repair_context: dict[str, Any] = {}
                self.repair_message = ""
                self.invocations = 0

            async def _invoke_role_dialogue_with_timeout(self, message, *, context, timeout_seconds, stage_label):
                del timeout_seconds, stage_label
                self.invocations += 1
                self.repair_message = message
                self.repair_context = context
                return {"content": message}

        (tmp_path / "services" / "user_service").mkdir(parents=True)
        (tmp_path / "services" / "product_service").mkdir(parents=True)
        (tmp_path / "common").mkdir(parents=True)
        adapter = _Adapter()

        missing_targets = [
            "services/__init__.py",
            "services/user_service/__init__.py",
            "services/product_service/__init__.py",
            "common/__init__.py",
            "scripts/run_all.py",
        ]
        artifact_quality_errors = [
            f"Artifact quality scan failed: declared target file missing '{p}'" for p in missing_targets
        ]

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": missing_targets},
            target_task_id="PM-0001-1",
            run_id="run-multi-missing-forced-write",
            context={},
            original_message="Create Python microservice skeleton with 4 services.",
            llm_call_timeout=10,
            artifact_quality_errors=artifact_quality_errors,
            changed_files=["pyproject.toml"],
        )

        assert adapter.invocations == 1, summary
        assert adapter.repair_context["_transaction_kernel_forced_tool_choice"] == {
            "type": "function",
            "function": {"name": "write_file"},
        }, adapter.repair_context
        forced_defs = adapter.repair_context["_transaction_kernel_forced_tool_definitions"]
        assert forced_defs and forced_defs[0]["function"]["name"] == "write_file"
        assert forced_defs[0]["function"]["parameters"]["required"] == [
            "file",
            "content",
        ]
        assert "SINGLE MISSING TARGET REPAIR" not in adapter.repair_message
        assert "write_only_single_target" not in adapter.repair_context["director_quality_repair"]
        assert adapter.repair_context["director_quality_repair"]["repair_target_files"] == missing_targets
        for missing_target in missing_targets:
            assert missing_target in adapter.repair_message
        assert "MISSING TARGET FILES" in adapter.repair_message
        assert summary["missing_target_files"] == missing_targets
        assert summary["repair_target_files"] == missing_targets

    def test_materialization_quality_repair_retry_after_first_attempt_stays_single_target(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _select_materialization_quality_repair_target_batch,
        )

        missing_targets = [
            "src/main.ts",
            "README.md",
            "tests/main.test.ts",
        ]

        assert _select_materialization_quality_repair_target_batch(missing_targets, repair_attempt=1) == missing_targets
        assert _select_materialization_quality_repair_target_batch(missing_targets, repair_attempt=2) == ["src/main.ts"]

    def test_materialization_quality_repair_retry_can_rotate_single_target_after_first_attempt(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _select_materialization_quality_repair_target_batch,
        )

        missing_targets = [
            "src/main.ts",
            "README.md",
            "tests/main.test.ts",
        ]

        assert _select_materialization_quality_repair_target_batch(
            missing_targets,
            repair_attempt=2,
            rotate_after_first_attempt=True,
        ) == ["README.md"]
        assert _select_materialization_quality_repair_target_batch(
            missing_targets,
            repair_attempt=3,
            rotate_after_first_attempt=True,
        ) == ["tests/main.test.ts"]
        assert _select_materialization_quality_repair_target_batch(
            missing_targets,
            repair_attempt=4,
            rotate_after_first_attempt=True,
        ) == ["src/main.ts"]

    def test_repair_message_names_missing_targets_and_hides_changed_paths(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )
        from polaris.cells.roles.kernel.public import (
            extract_target_files_from_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="实现 Markdown 预览器核心文件",
            artifact_quality_errors=["Artifact quality scan failed: declared target file missing 'src/styles.css'"],
            changed_files=["index.html", "package.json", "src/main.js"],
            missing_target_files=["src/styles.css"],
        )
        assert "MISSING TARGET FILES" in message
        assert "[director_quality_repair:write_only_single_target]" in message
        assert "src/styles.css" in message
        # Changed files appear only as a count — path-shaped tokens seed the
        # retry target extractor with wrong targets.
        assert "src/main.js" not in message
        assert "3 file(s) were already written" in message
        extracted = extract_target_files_from_message(message)
        assert "src/styles.css" in extracted
        assert "src/main.js" not in extracted

    def test_single_missing_target_repair_forces_one_write_without_reads(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create app assets.",
            artifact_quality_errors=["Artifact quality scan failed: declared target file missing 'src/styles.css'"],
            changed_files=["index.html"],
            missing_target_files=["src/styles.css"],
        )

        assert "SINGLE MISSING TARGET REPAIR" in message
        assert "exactly one write_file" in message
        assert "src/styles.css" in message
        assert "Do not read" in message
        assert "Do not list" in message

    def test_unresolved_symbol_repair_targets_exporting_module(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create shared SDK.",
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved import symbol 'HTTPClient' "
                "from 'common.http_client' in common/__init__.py "
                "(sibling module does not define it)"
            ],
            changed_files=["common/__init__.py", "common/http_client.py"],
            missing_target_files=[],
        )

        assert "CROSS-FILE SYMBOL REPAIR" in message
        assert "common.http_client" in message
        assert "HTTPClient" in message
        assert "common/__init__.py" in message
        assert "Do not edit the importing file" in message
        assert "make the exporting module define or export exactly the missing symbol" in message
        assert "Do not read files first" in message
        assert "Do not list directories" in message
        assert "Emit exactly one write_file or edit_file" in message

    def test_unresolved_relative_import_repair_prompt_omits_parent_traversal_specifier(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create TypeScript simulation modules.",
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved relative import '../data/seeddata' "
                "in src/engine/gardenengine.ts"
            ],
            changed_files=["src/engine/gardenengine.ts"],
            missing_target_files=["src/data/seeddata.ts"],
        )

        assert "../data/seeddata" not in message
        assert "src/data/seeddata.ts" in message
        assert "src/engine/gardenengine.ts" in message
        assert "Raw relative specifier omitted for path safety" in message

    def test_typecheck_repair_prompt_omits_external_dependency_parent_paths(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create TypeScript simulation modules.",
            artifact_quality_errors=[
                "Artifact quality scan failed: TypeScript project typecheck failed: "
                "../../../../node_modules/@types/three/src/nodes/accessors/ReferenceNode.d.ts(31,26): "
                "error TS1139: Type parameter declaration expected."
            ],
            changed_files=["package.json", "src/engine/simulation.ts"],
            missing_target_files=[],
            repair_target_files=["package.json"],
        )

        assert "../" not in message
        assert "node_modules" not in message
        assert "external dependency diagnostic TS1139" in message
        assert "Path omitted for workspace safety" in message

    def test_repair_message_without_missing_block_when_none(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="task",
            artifact_quality_errors=["some error"],
            changed_files=[],
            missing_target_files=[],
        )
        assert "MISSING TARGET FILES" not in message
        assert "0 file(s) were already written" in message

    def test_python_runtime_smoke_no_args_gets_cli_entrypoint_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="实现一个命令行交互式计算器，支持循环读取用户输入。",
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke crashed for 'calculator.py' "
                "(returncode=1); tail:\nError: No expression provided"
            ],
            changed_files=["calculator.py"],
            missing_target_files=[],
            repair_target_files=["calculator.py"],
        )

        assert "PYTHON CLI ENTRYPOINT REPAIR" in message
        assert "python <script>" in message
        assert "no-argument path must not crash or exit non-zero" in message
        assert "interactive CLI/input loop" in message
        assert "Do not require positional argv" in message

    def test_semantic_quality_repair_allows_rewriting_failed_changed_artifact(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="输出 verification_report.md",
            artifact_quality_errors=[
                "Director output quality gate failed: no project-domain signal found in changed files; "
                "expected one of ['task-1', 'task-2', 'readme', 'verification_report', 'task-3']"
            ],
            changed_files=["verification_report.md"],
            missing_target_files=[],
        )

        assert "MISSING TARGET FILES" not in message
        assert "must NOT be rewritten" not in message
        assert "failed quality gates" in message
        assert "rewrite only the failing changed artifact" in message
        # The repair-specific changed-file line remains count-based; the
        # original task contract may still legitimately name the target path.
        assert "1 file(s) were already written and failed quality gates" in message


class TestSyntaxRepairDirective:
    """I3-r15: the narrow-edit-ONLY directive (added L2-11 r2 to break the
    whole-file rewrite slip) backfired on weak local models — qwen could not
    form edit_blocks at all (121x "missing blocks or start") and was also
    forbidden the write_file rewrite it CAN do, leaving no usable repair path.
    The directive must give the laborer an executable path."""

    def test_syntax_error_gives_executable_repair_path(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="实现打字测试器",
            artifact_quality_errors=[
                "Artifact quality scan failed: syntax error in typing.js: typing.js:9\n"
                "    endTime: null;\n                 ^\n\nSyntaxError: Unexpected token ';'"
            ],
            changed_files=["typing.js"],
            missing_target_files=[],
        )
        assert "SYNTAX REPAIR DIRECTIVE" in message
        # The trap is gone: write_file rewrite of the one line is offered as the
        # easiest reliable path, with edit_blocks as a copy-verbatim alternative.
        assert "Do NOT rewrite the whole file" not in message
        assert "write_file" in message
        assert "edit_blocks" in message
        # Still constrains the change to the one broken line.
        assert "byte-for-byte" in message

    def test_tool_receipt_contamination_is_sanitized_before_repair_prompt(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create simulation tests.",
            artifact_quality_errors=[
                "Artifact quality scan failed: syntax error in tests/simulation.test.ts: simulation.test.ts:1\n"
                "**write_file**: Error - {'ok': False, 'error': 'Destructive shrink rejected: "
                "this edit would replace tests/garden.test.ts'}"
            ],
            changed_files=["tests/simulation.test.ts"],
            missing_target_files=[],
            repair_target_files=["tests/simulation.test.ts"],
        )

        assert "tool execution receipt contamination in tests/simulation.test.ts" in message
        assert "Destructive shrink rejected" not in message
        assert "**write_file**: Error" not in message
        assert "tests/garden.test.ts" not in message

    def test_no_directive_without_syntax_errors(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="task",
            artifact_quality_errors=["declared target file missing 'readme.md'"],
            changed_files=[],
            missing_target_files=["readme.md"],
        )
        assert "SYNTAX REPAIR DIRECTIVE" not in message


class TestTruncatedFileDirective:
    """L2-11 r6: index.html was whole-file-rewritten three times and every
    copy was output-limit-truncated; only append converges."""

    def test_truncation_error_gets_append_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="构建打字测试器",
            artifact_quality_errors=[
                "Artifact quality scan failed: syntax error in index.html: "
                "truncated/incomplete HTML: missing </html> closing tag; 1 unclosed <script> tag(s)"
            ],
            changed_files=["index.html"],
            missing_target_files=[],
        )
        assert "TRUNCATED FILE DIRECTIVE" in message
        assert "append_to_file" in message
        assert "Do NOT rewrite" in message
        assert "SYNTAX REPAIR DIRECTIVE" not in message

    def test_plain_syntax_error_keeps_narrow_edit_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="task",
            artifact_quality_errors=[
                "Artifact quality scan failed: syntax error in app.js: app.js:9\n"
                "    gfm: true;\n^\nSyntaxError: Unexpected token ';'"
            ],
            changed_files=["app.js"],
            missing_target_files=[],
        )
        assert "SYNTAX REPAIR DIRECTIVE" in message
        assert "TRUNCATED FILE DIRECTIVE" not in message


class TestCrossFileCoherenceRepair:
    """C7-text W3 (#54 repair-mode cross-file coherence): an unresolved relative
    import means the importer points at a module that does not exist yet. The
    repair message reframes it as a coherence obligation (create the module,
    export what the importer uses) instead of a bare missing path. Floor-safe:
    absent unless an unresolved-import error is present."""

    def test_unresolved_import_gets_coherence_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="实现 React 应用",
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved relative import './router' in src/main.tsx"
            ],
            changed_files=["src/main.tsx"],
            missing_target_files=["src/router.tsx"],
        )
        assert "CROSS-FILE COHERENCE REPAIR" in message
        assert "./router" in message
        assert "EXPORT" in message
        # Must not steer the model to rewrite the (existing) importing file.
        assert "Do not edit the importing file" in message

    def test_no_coherence_block_without_unresolved_imports(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="task",
            artifact_quality_errors=["Artifact quality scan failed: declared target file missing 'readme.md'"],
            changed_files=[],
            missing_target_files=["readme.md"],
        )
        assert "CROSS-FILE COHERENCE REPAIR" not in message

    def test_coherence_block_is_floor_inert(self) -> None:
        """Floor-safety lock: with no unresolved-import error the builder emits
        no trace of the coherence block (the L2 success path is unperturbed)."""
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="build app",
            artifact_quality_errors=["Artifact quality scan failed: syntax error in app.js: ..."],
            changed_files=["app.js"],
            missing_target_files=[],
        )
        assert "CROSS-FILE COHERENCE REPAIR" not in message
        assert "coherence" not in message.lower()


class TestCollectStepVerifyErrors:
    """写后即查（Fix-9, live I3-r11）: step verify 必须在执行轮内跑进修复梯,
    而不是等 exec→QA→bounce→exec 的市场往返(~30min/圈盲猜)。"""

    @staticmethod
    def _collect(context: Any, workspace: str) -> list[str]:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _collect_step_verify_errors,
        )

        return _collect_step_verify_errors(SimpleNamespace(workspace=workspace), context)

    def test_non_step_context_is_noop(self, tmp_path: Any) -> None:
        assert self._collect({}, str(tmp_path)) == []
        assert self._collect(None, str(tmp_path)) == []
        assert self._collect({"construction_step": {"target_file": "a.md"}}, str(tmp_path)) == []

    def test_passing_verify_returns_no_errors(self, tmp_path: Any) -> None:
        (tmp_path / "index.html").write_text('<canvas id="game-canvas"></canvas>', encoding="utf-8")
        context = {"construction_step": {"verify": "test -f ./index.html && grep -q 'id=\"game-canvas\"' ./index.html"}}
        assert self._collect(context, str(tmp_path)) == []

    def test_failing_verify_yields_repairable_error(self, tmp_path: Any) -> None:
        (tmp_path / "index.html").write_text('<canvas id="gameCanvas"></canvas>', encoding="utf-8")
        context = {"construction_step": {"verify": "grep -q 'id=\"game-canvas\"' ./index.html"}}
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "step verify failed" in errors[0]
        assert "game-canvas" in errors[0]

    def test_list_verify_joined(self, tmp_path: Any) -> None:
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        context = {"construction_step": {"verify": ["test -f ./a.md", "grep -q x ./a.md"]}}
        assert self._collect(context, str(tmp_path)) == []

    def test_near_miss_verify_target_path_is_repairable_error(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "rules" / "dancerule.ts"
        target.parent.mkdir(parents=True)
        target.write_text("export interface DanceRule {}\n", encoding="utf-8")
        context = {
            "construction_step": {
                "target_file": "src/rules/dancerule.ts",
                "verify": ("test -f ./src/rules/dance-rule.ts && grep -q 'DanceRule' ./src/rules/dance-rule.ts"),
            }
        }
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "step verify target mismatch" in errors[0]
        assert "src/rules/dancerule.ts" in errors[0]
        assert "src/rules/dance-rule.ts" in errors[0]

    def test_verify_may_reference_test_file_for_source_target(self, tmp_path: Any) -> None:
        source = tmp_path / "src" / "main.ts"
        test_file = tmp_path / "tests" / "main.test.ts"
        source.parent.mkdir(parents=True)
        test_file.parent.mkdir(parents=True)
        source.write_text("export const answer = 42;\n", encoding="utf-8")
        test_file.write_text("import '../src/main';\n", encoding="utf-8")
        context = {
            "construction_step": {
                "target_file": "src/main.ts",
                "verify": "test -f ./tests/main.test.ts",
            }
        }
        assert self._collect(context, str(tmp_path)) == []

    def test_failure_names_first_failing_clause(self, tmp_path: Any) -> None:
        """Fix-10 (live I3-r12): S2 passed 7/8 clauses but teaching carried only
        the whole command + exit 1 — the model could not tell WHICH check failed."""
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        context = {
            "construction_step": {
                "verify": (
                    "test -f ./style.css && grep -q '#game' ./style.css && [ \"$(wc -l < ./style.css)\" -le 120 ]"
                )
            }
        }
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "failing clause [3/3]:" in errors[0]
        assert "wc -l" in errors[0].split("failing clause", 1)[1]

    def test_single_clause_failure_has_no_clause_suffix(self, tmp_path: Any) -> None:
        context = {"construction_step": {"verify": "test -f ./missing.md"}}
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "failing clause" not in errors[0]

    def test_quoted_and_inside_pattern_aborts_clause_diagnosis(self, tmp_path: Any) -> None:
        """Splitting on ' && ' cuts through the quoted pattern; the sh -n guard
        must abandon diagnosis instead of naming a bogus clause."""
        (tmp_path / "a.txt").write_text("plain\n", encoding="utf-8")
        context = {"construction_step": {"verify": "grep -q 'a && b' ./a.txt && test -f ./a.txt"}}
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "step verify failed" in errors[0]
        assert "failing clause" not in errors[0]

    def test_state_carrying_chain_aborts_clause_diagnosis(self, tmp_path: Any) -> None:
        """Adversarial review (live repro): a cd/VAR= clause passes sh -n but its
        successors re-run in a fresh shell against the wrong cwd/env — naming
        a wrong clause actively misleads the next attempt."""
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "app.js").write_text("bar\n", encoding="utf-8")
        for verify in (
            "cd src && test -f app.js && grep -q foo app.js",
            'X=1 && [ "$X" = 1 ] && test -f missing.txt',
            "export V=2 && test -f missing.txt",
        ):
            errors = self._collect({"construction_step": {"verify": verify}}, str(tmp_path))
            assert len(errors) == 1, verify
            assert "failing clause" not in errors[0], verify

    def test_top_level_or_chain_aborts_clause_diagnosis(self, tmp_path: Any) -> None:
        context = {"construction_step": {"verify": "test -f ./a.txt && grep -q x ./a.txt || test -f ./b.txt"}}
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "failing clause" not in errors[0]

    def test_clause_detail_precedes_full_command_in_message(self, tmp_path: Any) -> None:
        """Teaching channels truncate (step card 240 chars) — the actionable
        clause must come before the potentially long full command."""
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        verify = 'test -f ./style.css && [ "$(wc -l < ./style.css)" -le 120 ]'
        errors = self._collect({"construction_step": {"verify": verify}}, str(tmp_path))
        assert len(errors) == 1
        assert errors[0].index("failing clause") < errors[0].index("full:")


class TestSingleFileStepTarget:
    """对抗复核 C-fix: 钉靶步轮的质量门只裁决该步拥有的文件 — package.json 等
    其他文件的旧垃圾会要求被钉死的写工具做不到的修复, 反弹环永不收敛。"""

    @staticmethod
    def _target(source: Any) -> str:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _single_file_step_target,
        )

        return _single_file_step_target(source)

    def test_clean_step_target_is_extracted(self) -> None:
        assert self._target({"construction_step": {"target_file": "./style.css"}}) == "style.css"

    def test_malformed_targets_are_refused(self) -> None:
        for target in ("src/*.js", "a.js, b.js", "/etc/passwd", "../x.js"):
            assert self._target({"construction_step": {"target_file": target}}) == "", target

    def test_non_step_sources_are_refused(self) -> None:
        assert self._target(None) == ""
        assert self._target({}) == ""
        assert self._target({"construction_step": {}}) == ""

    def test_quality_scan_is_scoped_to_step_target(self, tmp_path: Any, monkeypatch: Any) -> None:
        from polaris.cells.roles.adapters.internal.director import execute_method

        seen: dict[str, Any] = {}

        def _capture(workspace: str, relative_paths: list[str] | None = None) -> list[str]:
            seen["paths"] = list(relative_paths or [])
            return []

        monkeypatch.setattr(execute_method, "scan_workspace_artifact_quality", _capture)
        adapter = SimpleNamespace(workspace=str(tmp_path))
        context = {"construction_step": {"target_file": "style.css"}}
        execute_method._collect_materialization_quality_errors(
            adapter,
            task={"task_id": "PM-1-S2"},
            all_affected_files=["style.css", "main.js", "package.json"],
            workspace_name="ws",
            context=context,
        )
        assert seen["paths"] == ["style.css"]

    def test_non_step_turn_keeps_full_scan_scope(self, tmp_path: Any, monkeypatch: Any) -> None:
        from polaris.cells.roles.adapters.internal.director import execute_method

        seen: dict[str, Any] = {}

        def _capture(workspace: str, relative_paths: list[str] | None = None) -> list[str]:
            seen["paths"] = list(relative_paths or [])
            return []

        monkeypatch.setattr(execute_method, "scan_workspace_artifact_quality", _capture)
        adapter = SimpleNamespace(workspace=str(tmp_path))
        execute_method._collect_materialization_quality_errors(
            adapter,
            task={"task_id": "T-1"},
            all_affected_files=["a.js", "b.js"],
            workspace_name="ws",
            context={"run_id": "r"},
        )
        assert set(seen["paths"]) >= {"a.js", "b.js"}
