"""npm repair must execute authored tests rather than replace them with build."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from polaris.cells.director.runtime.public.contracts import QueryDirectorRepairPlanProbeV1
from polaris.cells.director.runtime.public.service import query_director_repair_plan_probe


@pytest.mark.skipif(not shutil.which("tsc") or not shutil.which("npm"), reason="real tsc and npm required")
@pytest.mark.parametrize("passing", [True, False])
def test_npm_repair_executes_compiled_authored_assertions(tmp_path: Path, passing: bool) -> None:
    files = {
        "package.json": json.dumps({"scripts": {"build": "tsc -p tsconfig.json", "test": "npm run test"}}),
        "tsconfig.json": json.dumps(
            {"compilerOptions": {"rootDir": ".", "outDir": "dist", "module": "CommonJS"}, "include": ["tests/**/*.ts"]}
        ),
        "tests/math.test.ts": (
            "declare function require(name: string): any;\n"
            "require('node:test')('authored assertion', () => "
            f"require('node:assert/strict').equal(4, {4 if passing else 5}));\n"
        ),
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            ("npm package manifest script 'test' recursively invokes itself via test -> test in package.json",),
            base_files=files,
            source_tools=("deterministic_npm_script_contract_repair",),
        )
    )
    assert probe.status == "covered_plannable"
    effect = next(
        effect for effect in probe.items[0].planning_result.effect_plan.effects if effect.contingency_kind == "forward"
    )
    (tmp_path / "package.json").write_text(effect.to_dict()["arguments"]["content"], encoding="utf-8")
    result = subprocess.run(["npm", "test"], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert "# tests 1" in result.stdout, result.stdout + result.stderr
    assert (result.returncode == 0) is passing, result.stdout + result.stderr
    assert "authored assertion" in result.stdout
    assert (tmp_path / "tests/math.test.ts").read_text(encoding="utf-8") == files["tests/math.test.ts"]
