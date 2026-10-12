"""A missing Node main selected by a package script belongs to its manifest."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.roles.adapters.public import resolve_director_causal_quality_repair_target_files


@pytest.mark.parametrize("matches_script", [True, False])
def test_compiled_entrypoint_routes_to_manifest_only_when_script_matches(tmp_path: Path, matches_script: bool) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"start": "node dist/entry/cli.js --frames 12"}}), encoding="utf-8"
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"rootDir":"./","outDir":"./dist"},"include":["src/**/*.ts"]}', encoding="utf-8"
    )
    path = tmp_path / "src/entry/cli.ts"
    path.parent.mkdir(parents=True)
    path.write_text("console.log('ready');", encoding="utf-8")
    missing = "dist/entry/cli.js" if matches_script else "dist/domain/missing.js"
    errors = [
        "workspace validation command failed (npm run start):\n"
        + f"Error: Cannot find module '/workspace/{missing}'\n"
        + "code: 'MODULE_NOT_FOUND', requireStack: []\nNode.js v22.23.2"
    ]

    targets = resolve_director_causal_quality_repair_target_files(
        artifact_quality_errors=errors,
        changed_files=["package.json", "tsconfig.json", "src/entry/cli.ts"],
        workspace_full=str(tmp_path),
    )

    assert ("package.json" in targets) is matches_script
    assert all(not target.startswith("dist/") for target in targets)


def test_manifest_plan_probe_reads_nested_source_without_adding_mutation_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.adapters.internal.director import materialization_quality_callback_ports as ports
    from polaris.cells.roles.adapters.internal.director.materialization_quality_callback_ports import (
        _project_materialization_plan_probe_preaudit,
    )

    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "main": "dist/entry/cli.js",
                "devDependencies": {"typescript": "^5"},
                "scripts": {"start": "node dist/entry/cli.js"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"rootDir":"./","outDir":"./dist"}}', encoding="utf-8")
    src = tmp_path / "src/entry/cli.ts"
    src.parent.mkdir(parents=True)
    src.write_text("console.log('ready');", encoding="utf-8")
    task = {"id": "TASK-CONFIG", "target_files": ["package.json", "tsconfig.json"]}
    probe = _project_materialization_plan_probe_preaudit(
        SimpleNamespace(workspace=tmp_path),
        task=task,
        artifact_quality_errors=[
            "workspace validation command failed (npm run start):\n"
            "Error: Cannot find module '/workspace/dist/entry/cli.js'\n"
            "code: 'MODULE_NOT_FOUND', requireStack: []\nNode.js v22.23.2"
        ],
        coverage_preaudit={},
    )
    assert probe["status"] == "covered_plannable"
    assert probe["base_file_count"] >= 3
    assert task["target_files"] == ["package.json", "tsconfig.json"]
    calls: list[dict] = []

    def capture(_adapter: object, **kwargs: object) -> list:
        calls.append(kwargs)
        return []

    monkeypatch.setattr(ports, "run_runtime_repair_with_director_tools", capture)
    ports._run_materialization_node_manifest(
        SimpleNamespace(workspace=tmp_path),
        task=task,
        task_id="TASK-CONFIG",
        artifact_quality_errors=[
            "workspace validation command failed (npm run start):\n"
            "Error: Cannot find module '/workspace/dist/entry/cli.js'\n"
            "code: 'MODULE_NOT_FOUND', requireStack: []\nNode.js v22.23.2"
        ],
    )
    npm = next(call for call in calls if call["source_tool"] == "deterministic_npm_script_contract_repair")
    assert "src/entry/cli.ts" in npm["base_files"]
    assert npm["allowed_paths"] == ("package.json",)
