"""Registered npm repairs must infer nested compiled mains without guessing."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from polaris.cells.director.runtime.public.contracts import (
    DirectorRepairRevalidationInputV1,
    QueryDirectorRepairPlanProbeV1,
    RunDirectorRepairCommandV1,
)
from polaris.cells.director.runtime.public.service import query_director_repair_plan_probe, run_director_repair


@pytest.mark.parametrize(
    "root_dir,missing,expected",
    [("./", "dist/entry/cli.js", "dist/src/entry/cli.js"), ("src", "dist/cli.js", "dist/entry/cli.js")],
)
def test_public_npm_probe_repairs_nested_compiled_entrypoint(root_dir: str, missing: str, expected: str) -> None:
    package = json.dumps(
        {
            "main": missing,
            "devDependencies": {"typescript": "^5.0.0"},
            "scripts": {"build": "tsc -p tsconfig.json", "start": f"node {missing} --frames 12"},
        }
    )
    raw = (
        "workspace validation command failed (npm run start):\n"
        + f"Error: Cannot find module '/workspace/{missing}'\n"
        + "code: 'MODULE_NOT_FOUND', requireStack: []\nNode.js v22.23.2"
    )
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            (raw,),
            base_files={
                "package.json": package,
                "tsconfig.json": json.dumps({"compilerOptions": {"rootDir": root_dir, "outDir": "./dist"}}),
                "src/entry/cli.ts": "console.log('ready');",
            },
            source_tools=("deterministic_npm_script_contract_repair",),
        )
    )

    assert probe.status == "covered_plannable"
    item = probe.items[0]
    assert item.changed_paths == ("package.json",)
    effects = item.planning_result.effect_plan.effects
    forward = next(effect for effect in effects if effect.contingency_kind == "forward")
    assert forward.target_path == "package.json"
    assert json.loads(forward.to_dict()["arguments"]["content"])["scripts"]["start"] == f"node {expected} --frames 12"


@pytest.mark.skipif(not shutil.which("tsc") or not shutil.which("npm"), reason="real Node and tsc required")
def test_public_npm_repair_revalidates_real_compiled_cli_and_keeps_arguments(tmp_path: Path) -> None:
    files = {
        "package.json": json.dumps(
            {
                "main": "dist/entry/cli.js",
                "scripts": {"start": "node dist/entry/cli.js --frames 12"},
                "devDependencies": {"typescript": "^5"},
            }
        ),
        "tsconfig.json": '{"compilerOptions":{"rootDir":"./","outDir":"./dist"},"include":["src/**/*.ts"]}',
        "src/entry/cli.ts": "console.log('ready');",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    build = subprocess.run(
        ["tsc", "-p", "tsconfig.json"], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8"
    )
    assert build.returncode == 0, build.stderr
    assert (tmp_path / "dist/src/entry/cli.js").is_file()
    before = subprocess.run(["npm", "run", "start"], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")
    assert before.returncode != 0
    error = "workspace validation command failed (npm run start):\n" + before.stdout + before.stderr

    def writer(path: str, content: str) -> dict:
        assert path == "package.json"
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    def revalidate(_request: object) -> DirectorRepairRevalidationInputV1:
        after = subprocess.run(["npm", "run", "start"], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")
        assert "ready" in after.stdout
        return DirectorRepairRevalidationInputV1(
            residual_artifact_quality_errors=() if after.returncode == 0 else (after.stderr,),
            command=("npm", "run", "start"),
            exit_code=after.returncode,
            metadata={"evidence_source": "real_self_owned_cli_revalidation"},
        )

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="TASK-CONFIG",
            workspace=str(tmp_path),
            source_tool="deterministic_npm_script_contract_repair",
            base_files=files,
            artifact_quality_errors=(error,),
            allowed_paths=("package.json",),
        ),
        writer=writer,
        revalidator=revalidate,
    )
    assert result.ok is True
    assert result.receipts[0].verifier_exit_code == 0
    assert result.receipts[0].files_changed == ("package.json",)
    assert json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))["scripts"]["start"] == (
        "node dist/src/entry/cli.js --frames 12"
    )
