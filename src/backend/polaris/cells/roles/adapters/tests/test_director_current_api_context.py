"""Current TypeScript API context follows consumer imports, without write grants."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director.quality_gate._prompt_and_targets import (
    _build_materialization_quality_repair_message,
    _typescript_callee_definition_refs,
)


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    tests = tmp_path / "checks"
    api = tmp_path / "components" / "engine"
    tests.mkdir(parents=True)
    api.mkdir(parents=True)
    returned = api / "result.ts"
    returned.write_text("export interface TickResult { readonly snapshots: readonly number[]; }\n", encoding="utf-8")
    implementation = api / "runner.ts"
    implementation.write_text(
        "import type { TickResult as StepResult } from './result.js';\n"
        "export class Runner {\n"
        "  constructor(public id: string, public speed: number) {}\n"
        "  public step(): StepResult { return { snapshots: [this.speed] }; }\n"
        "}\n",
        encoding="utf-8",
    )
    consumer = tests / "runner.test.ts"
    consumer.write_text(
        "import { Runner as Engine } from '../components/engine/runner.js';\n"
        "const engine = new Engine('a', 1, 2);\n"
        "const tick = engine.step();\n"
        "console.log(tick.items);\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "blueprint.md").write_text(
        "export class Runner { constructor(a, b, c, d) {} step(): OldState; } // OLD_DOCUMENT_API\n",
        encoding="utf-8",
    )
    return consumer, implementation, returned


def test_imported_class_constructor_method_and_returned_type_are_current_readonly_evidence(tmp_path: Path) -> None:
    consumer, implementation, returned = _project(tmp_path)
    files = [consumer, implementation, returned, tmp_path / "docs/blueprint.md"]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    targets = ["checks/runner.test.ts"]
    errors = [
        "checks/runner.test.ts(2,29): error TS2554: Expected 2 arguments, but got 3.",
        "checks/runner.test.ts(4,18): error TS2339: Property 'items' does not exist on type 'TickResult'.",
    ]
    refs = _typescript_callee_definition_refs(
        workspace=tmp_path, artifact_quality_errors=errors, repair_targets=set(targets)
    )
    assert {path for path, _ in refs} == {"components/engine/runner.ts", "components/engine/result.ts"}
    message = _build_materialization_quality_repair_message(
        original_message="Repair compiler errors using current definitions.",
        artifact_quality_errors=errors,
        changed_files=targets,
        repair_target_files=targets,
        workspace_full=str(tmp_path),
    )
    assert "constructor(public id: string, public speed: number)" in message
    assert "public step(): StepResult" in message
    assert "readonly snapshots: readonly number[]" in message
    assert "OLD_DOCUMENT_API" not in message
    assert "READ-ONLY" in message
    assert "Authorized tool target paths:\n- checks/runner.test.ts" in message
    assert "Authorized tool target paths:\n- components/engine" not in message
    assert targets == ["checks/runner.test.ts"]
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def test_current_type_definition_far_from_header_is_not_lost_to_a_small_window(tmp_path: Path) -> None:
    _consumer, implementation, returned = _project(tmp_path)
    returned.write_text(
        "// source context\n" * 140 + "export interface TickResult { readonly snapshots: readonly number[]; }\n",
        encoding="utf-8",
    )
    implementation.write_text(
        "// constructor context\n" * 140 + implementation.read_text(encoding="utf-8"), encoding="utf-8"
    )
    message = _build_materialization_quality_repair_message(
        original_message="Repair compiler errors.",
        artifact_quality_errors=[
            "checks/runner.test.ts(4,18): error TS2339: Property 'items' does not exist on type 'TickResult'."
        ],
        changed_files=["checks/runner.test.ts"],
        repair_target_files=["checks/runner.test.ts"],
        workspace_full=str(tmp_path),
    )
    assert "constructor(public id: string, public speed: number)" in message
    assert "public step(): StepResult" in message
    assert "readonly snapshots: readonly number[]" in message


def test_current_api_context_rejects_external_relative_import_and_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external.ts"
    external.write_text("export function secret(a: string, b: number) {} // EXTERNAL_API_BYTES\n", encoding="utf-8")
    consumer = workspace / "test.ts"
    consumer.write_text("import { secret } from '../external.js';\nsecret('x');\n", encoding="utf-8")
    errors = ["test.ts(2,1): error TS2554: Expected 2 arguments, but got 1."]
    assert (
        _typescript_callee_definition_refs(
            workspace=workspace, artifact_quality_errors=errors, repair_targets={"test.ts"}
        )
        == []
    )
    linked = workspace / "linked.ts"
    linked.symlink_to(external)
    consumer.write_text("import { secret } from './linked.js';\nsecret('x');\n", encoding="utf-8")
    message = _build_materialization_quality_repair_message(
        original_message="Repair compiler errors.",
        artifact_quality_errors=errors,
        changed_files=["test.ts"],
        repair_target_files=["test.ts"],
        workspace_full=str(workspace),
    )
    assert "EXTERNAL_API_BYTES" not in message
    assert linked.is_symlink()


def test_source_snapshot_budget_omits_oversized_import_without_unbounded_scan(tmp_path: Path) -> None:
    _consumer, implementation, _ = _project(tmp_path)
    implementation.write_text("export class Runner {}\n" + "// bounded source\n" * 6000, encoding="utf-8")
    refs = _typescript_callee_definition_refs(
        workspace=tmp_path,
        artifact_quality_errors=["checks/runner.test.ts(2,29): error TS2554: Expected 2 arguments, but got 3."],
        repair_targets={"checks/runner.test.ts"},
    )
    assert "components/engine/runner.ts" not in {path for path, _ in refs}


def test_relative_reexport_alias_uses_leaf_definition_body(tmp_path: Path) -> None:
    consumer, implementation, _ = _project(tmp_path)
    barrel = implementation.parent / "api.ts"
    barrel.write_text("export { Runner as Engine } from './runner.js';\n", encoding="utf-8")
    consumer.write_text(
        "import { Engine } from '../components/engine/api.js';\nnew Engine('a', 1, 2);\n", encoding="utf-8"
    )
    message = _build_materialization_quality_repair_message(
        original_message="Repair imported class call.",
        artifact_quality_errors=["checks/runner.test.ts(2,1): error TS2554: Expected 2 arguments, but got 3."],
        changed_files=["checks/runner.test.ts"],
        repair_target_files=["checks/runner.test.ts"],
        workspace_full=str(tmp_path),
    )
    assert "constructor(public id: string, public speed: number)" in message
    assert "readonly snapshots: readonly number[]" in message


def test_current_api_discovery_never_walks_the_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    consumer, _, _ = _project(tmp_path)

    def forbidden_walk(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Current API evidence must follow relative import edges, not scan directories")

    monkeypatch.setattr(Path, "rglob", forbidden_walk)
    monkeypatch.setattr(Path, "walk", forbidden_walk)
    refs = _typescript_callee_definition_refs(
        workspace=tmp_path,
        artifact_quality_errors=["checks/runner.test.ts(2,29): error TS2554: Expected 2 arguments, but got 3."],
        repair_targets={consumer.relative_to(tmp_path).as_posix()},
    )
    assert {path for path, _ in refs} == {"components/engine/runner.ts", "components/engine/result.ts"}
