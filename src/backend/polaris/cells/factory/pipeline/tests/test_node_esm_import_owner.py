"""An ESM loader failure keeps the importing test as the repair owner."""

import json
import subprocess
from pathlib import Path

import pytest
from polaris.cells.director.runtime.public import normalize_director_repair_diagnostics
from polaris.cells.director.runtime.public.contracts import (
    DirectorRepairRevalidationInputV1,
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairCoverageV1,
    RunDirectorRepairCommandV1,
)
from polaris.cells.director.runtime.public.service import (
    plan_director_repair,
    query_director_repair_coverage,
    run_director_repair,
)
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.internal.factory_workspace_quality_impl import (
    _workspace_quality_causal_repair_target_files,
    _workspace_quality_llm_claim_target_files,
)

SOURCE_TOOL = "deterministic_typescript_local_js_import_repair"
ERROR = (
    "artifact_quality_error: npm test failed (exit=1): Error [ERR_MODULE_NOT_FOUND]: "
    "Cannot find module '/workspace/src/value' imported from /workspace/tests/value.test.ts"
)


def _files() -> dict[str, str]:
    return {
        "package.json": json.dumps({"type": "module", "scripts": {"test": "node --test tests/value.test.ts"}}),
        "src/value.ts": "export function value(): number { return 7; }\n",
        "tests/value.test.ts": (
            "import { value } from '../src/value';\n"
            "import test from 'node:test'; import assert from 'node:assert/strict';\n"
            "test('actual value', () => assert.equal(value(), 7));\n"
        ),
    }


@pytest.mark.parametrize("wrapper", [False, True])
def test_extensionless_esm_error_retains_importer_in_plain_and_wrapped_output(wrapper: bool) -> None:
    raw = ERROR if wrapper else ERROR.split("Error [ERR_MODULE_NOT_FOUND]: ", 1)[1]

    diagnostics = normalize_director_repair_diagnostics((raw,))

    assert len(diagnostics) == 1
    assert diagnostics[0].path == "/workspace/tests/value.test.ts"
    assert diagnostics[0].metadata["module_error_kind"] == "node_esm_typescript_source_import"
    assert diagnostics[0].metadata["missing_module_path"] == "/workspace/src/value"


def test_sandbox_import_failure_routes_to_owned_test_without_widening_scope(tmp_path: Path) -> None:
    for name, text in _files().items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)

    targets = _workspace_quality_causal_repair_target_files(executor, artifact_quality_errors=[ERROR])
    claim = _workspace_quality_llm_claim_target_files(
        owner_target_files=None, diagnostic_target_files=targets, fallback_target_files=[]
    )

    assert targets == ["tests/value.test.ts"]
    assert claim == ["tests/value.test.ts"]
    foreign = ERROR.replace("/workspace/tests/value.test.ts", "/foreign/tests/value.test.ts")
    assert executor._workspace_quality_repair_diagnostic_target_files([foreign]) == []


def test_existing_runtime_rule_plans_only_the_extensionless_importer() -> None:
    files = _files()
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(ERROR,)))
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=SOURCE_TOOL, base_files=files, artifact_quality_errors=(ERROR,), mode="shadow"
        )
    ).to_dict()

    assert SOURCE_TOOL in coverage.to_dict()["items"][0]["matched_source_tools"]
    assert planning["planned"] is True
    patches = planning["composition_summary"]["patches"]
    assert [patch["path"] for patch in patches] == ["tests/value.test.ts"]
    assert "from '../src/value.ts'" in patches[0]["content_after"]
    assert "from '../src/value'" in files["tests/value.test.ts"]


def test_registered_import_repair_revalidates_real_node_tests(tmp_path: Path) -> None:
    files = _files()
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    argv = ("node", "--test", "tests/value.test.ts")
    before = subprocess.run(argv, cwd=tmp_path, capture_output=True, timeout=15, check=False)
    assert before.returncode != 0
    error = "artifact_quality_error: npm test failed (exit=1): " + (before.stdout + before.stderr).decode("utf-8")

    def writer(path: str, content: str) -> dict:
        assert path == "tests/value.test.ts"
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    def revalidate(_request: object) -> DirectorRepairRevalidationInputV1:
        after = subprocess.run(argv, cwd=tmp_path, capture_output=True, timeout=15, check=False)
        assert b"# pass 1" in after.stdout
        return DirectorRepairRevalidationInputV1(
            residual_artifact_quality_errors=() if after.returncode == 0 else (after.stderr.decode("utf-8"),),
            command=argv,
            exit_code=after.returncode,
            metadata={"evidence_source": "real_self_owned_node_test"},
        )

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="TASK-TEST",
            workspace=str(tmp_path),
            source_tool=SOURCE_TOOL,
            base_files=files,
            artifact_quality_errors=(error,),
            allowed_paths=("tests/value.test.ts",),
        ),
        writer=writer,
        revalidator=revalidate,
    )

    assert result.ok is True
    assert result.receipts[0].verifier_exit_code == 0
    assert result.receipts[0].files_changed == ("tests/value.test.ts",)
    assert (tmp_path / "src/value.ts").read_text(encoding="utf-8") == files["src/value.ts"]


def test_test_import_uses_compiled_module_without_changing_production_imports(tmp_path: Path) -> None:
    files = _files()
    files["package.json"] = json.dumps({"scripts": {"build": "tsc", "test": "node --test tests/value.test.ts"}})
    files["tsconfig.json"] = json.dumps(
        {
            "compilerOptions": {"target": "ES2022", "module": "CommonJS", "rootDir": "src", "outDir": "dist"},
            "include": ["src/**/*.ts"],
        }
    )
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    build = subprocess.run(("tsc", "-p", "tsconfig.json"), cwd=tmp_path, capture_output=True, timeout=15, check=False)
    assert build.returncode == 0
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=SOURCE_TOOL, base_files=files, artifact_quality_errors=(ERROR,), mode="shadow"
        )
    ).to_dict()

    patch = planning["composition_summary"]["patches"][0]
    assert patch["path"] == "tests/value.test.ts"
    assert "from '../dist/value.js'" in patch["content_after"]
    (tmp_path / patch["path"]).write_text(patch["content_after"], encoding="utf-8")
    executed = subprocess.run(
        ("node", "--test", "tests/value.test.ts"), cwd=tmp_path, capture_output=True, timeout=15, check=False
    )
    assert executed.returncode == 0, executed.stderr.decode("utf-8")
    assert b"# pass 1" in executed.stdout
    assert (tmp_path / "src/value.ts").read_text(encoding="utf-8") == files["src/value.ts"]
