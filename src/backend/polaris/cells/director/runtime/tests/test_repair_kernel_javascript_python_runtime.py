"""Runtime repair coverage for JavaScript/Node and Python migrations."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from polaris.cells.director.runtime.internal.repair_kernel import (
    PatchComposer,
    normalize_artifact_quality_errors,
    run_runtime_repair,
)
from polaris.cells.director.runtime.internal.repair_kernel.javascript_syntax import (
    build_npm_script_contract_plan,
)
from polaris.cells.director.runtime.internal.repair_kernel.python_syntax import (
    build_python_unresolved_import_symbol_plan,
)
from polaris.cells.director.runtime.public import (
    DirectorRepairRevalidationInputV1,
    DirectorRepairRevalidationRequestV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairStrategyCatalogV1,
    RunDirectorRepairCommandV1,
    query_director_repair_coverage,
    query_director_repair_strategy_catalog,
    run_director_repair,
)


def _workspace_writer(workspace: Path, writes: list[str]):
    def writer(path: str, content: str) -> dict[str, object]:
        writes.append(path)
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True}

    return writer


def _read_base_files(workspace: Path, paths: tuple[str, ...]) -> dict[str, str]:
    return {path: (workspace / path).read_text(encoding="utf-8") for path in paths}


def test_python_unresolved_import_symbol_declines_empty_placeholder_stub() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            (
                "Artifact quality scan failed: unresolved import symbol "
                "'WeatherKind' from 'src.models.weather' in src/engine/forecast.py"
            )
        ]
    )

    plan = build_python_unresolved_import_symbol_plan(
        base_files={
            "src/engine/forecast.py": "from src.models.weather import WeatherKind\n",
            "src/models/weather.py": "class WeatherSnapshot:\n    pass\n",
        },
        diagnostics=diagnostics,
    )

    assert plan is None


def test_python_unresolved_import_symbol_allows_real_similar_alias_only() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            (
                "Artifact quality scan failed: unresolved import symbol "
                "'Registry' from 'shared.registry' in shared/__init__.py"
            )
        ]
    )

    plan = build_python_unresolved_import_symbol_plan(
        base_files={
            "shared/__init__.py": "from shared.registry import Registry\n",
            "shared/registry.py": "class ServiceRegistry:\n    pass\n",
        },
        diagnostics=diagnostics,
    )

    assert plan is not None
    assert plan.metadata["runtime_plan_scope"] == "append_alias_to_existing_similar_symbol_only"
    assert plan.metadata["empty_stub_generation_allowed"] is False
    assert plan.operations[0].replacement
    assert "Registry = ServiceRegistry" in str(plan.operations[0].replacement)
    assert "class Registry" not in str(plan.operations[0].replacement)


def test_python_unresolved_import_symbol_runtime_appends_alias_and_receipt(tmp_path: Path) -> None:
    (tmp_path / "shared").mkdir(parents=True)
    (tmp_path / "shared" / "__init__.py").write_text(
        "from shared.registry import Registry\n",
        encoding="utf-8",
    )
    (tmp_path / "shared" / "registry.py").write_text(
        "class ServiceRegistry:\n    pass\n",
        encoding="utf-8",
    )
    writes: list[str] = []

    result = run_runtime_repair(
        source_tool="deterministic_unresolved_import_symbol_repair",
        workspace=tmp_path,
        base_files=_read_base_files(
            tmp_path,
            ("shared/__init__.py", "shared/registry.py"),
        ),
        artifact_quality_errors=(
            "Artifact quality scan failed: unresolved import symbol "
            "'Registry' from 'shared.registry' in shared/__init__.py",
        ),
        writer=_workspace_writer(tmp_path, writes),
        allowed_paths=("shared/registry.py",),
    )

    assert result.ok is True
    assert writes == ["shared/registry.py"]
    assert "Registry = ServiceRegistry" in (tmp_path / "shared" / "registry.py").read_text(encoding="utf-8")
    assert result.execution_result is not None
    receipt = result.execution_result.receipt
    assert receipt.source_tool == "deterministic_unresolved_import_symbol_repair"
    assert receipt.status == "applied"
    assert receipt.files_changed == ("shared/registry.py",)
    assert receipt.metadata["empty_stub_generation_allowed"] is False


def test_python_unittest_runtime_failure_runtime_replaces_overstrict_test(tmp_path: Path) -> None:
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
    writes: list[str] = []

    result = run_runtime_repair(
        source_tool="deterministic_python_unittest_runtime_failure_repair",
        workspace=tmp_path,
        base_files=_read_base_files(
            tmp_path,
            ("guess_number.py", "tests/test_guess_number.py"),
        ),
        artifact_quality_errors=(
            "Artifact quality scan failed: python runtime smoke timed out for "
            "'tests/test_guess_number.py' after 5.0s; tail:\nwaiting for interactive input",
        ),
        writer=_workspace_writer(tmp_path, writes),
        allowed_paths=("tests/test_guess_number.py",),
    )

    assert result.ok is True
    assert writes == ["tests/test_guess_number.py"]
    content = failing_test.read_text(encoding="utf-8")
    assert "DeclaredPythonModuleSmokeTests" in content
    assert "Game should show congratulations" not in content
    assert result.execution_result is not None
    assert result.execution_result.receipt.metadata["replaced_test_targets"] == ["tests/test_guess_number.py"]


def test_python_package_shadow_bridge_runtime_exports_sibling_module_symbol(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    package_dir = src_dir / "engine"
    package_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "engine.py").write_text("def run() -> str:\n    return 'planet-weather-ok'\n", encoding="utf-8")
    (package_dir / "__init__.py").write_text('"""Renderer package."""\n', encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from src.engine import run\n\nif __name__ == '__main__':\n    print(run())\n",
        encoding="utf-8",
    )

    failed = subprocess.run(
        [sys.executable, "main.py"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert failed.returncode == 1
    writes: list[str] = []

    result = run_runtime_repair(
        source_tool="deterministic_python_package_shadow_bridge_repair",
        workspace=tmp_path,
        base_files=_read_base_files(
            tmp_path,
            ("src/engine.py", "src/engine/__init__.py"),
        ),
        artifact_quality_errors=(
            "ImportError: cannot import name 'run' from 'src.engine' "
            f"({(package_dir / '__init__.py').as_posix()})",
        ),
        writer=_workspace_writer(tmp_path, writes),
        allowed_paths=("src/engine/__init__.py",),
    )

    assert result.ok is True
    assert writes == ["src/engine/__init__.py"]
    completed = subprocess.run(
        [sys.executable, "main.py"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "planet-weather-ok"
    assert result.execution_result is not None
    assert result.execution_result.receipt.source_tool == "deterministic_python_package_shadow_bridge_repair"


def test_python_package_child_reexport_runtime_exports_child_symbol(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    package_dir = src_dir / "engine"
    package_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text(
        "from .engine import Engine\nfrom .weather import Weather\n\n__all__ = ['Engine', 'Weather']\n",
        encoding="utf-8",
    )
    (src_dir / "weather.py").write_text(
        "class Weather:\n    def summary(self) -> str:\n        return 'temperature=22C'\n",
        encoding="utf-8",
    )
    (package_dir / "core.py").write_text(
        "class Engine:\n"
        "    def __init__(self) -> None:\n"
        "        self.altitude = 0\n\n"
        "    def step(self) -> int:\n"
        "        self.altitude += 1\n"
        "        return self.altitude\n",
        encoding="utf-8",
    )
    (package_dir / "__init__.py").write_text(
        "from .renderer import RenderFrame\n\n__all__ = ['RenderFrame']\n",
        encoding="utf-8",
    )
    (package_dir / "renderer.py").write_text("class RenderFrame:\n    pass\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from src import Engine, Weather\nfrom src.engine import Engine as EngineFromEnginePkg\n\n"
        "if __name__ == '__main__':\n"
        "    engine = Engine()\n"
        "    weather = Weather()\n"
        "    print(f'altitude={engine.step()} {weather.summary()} alias={Engine is EngineFromEnginePkg}')\n",
        encoding="utf-8",
    )

    failed = subprocess.run(
        [sys.executable, "main.py"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert failed.returncode == 1
    writes: list[str] = []

    result = run_runtime_repair(
        source_tool="deterministic_python_package_child_reexport_repair",
        workspace=tmp_path,
        base_files=_read_base_files(
            tmp_path,
            (
                "src/__init__.py",
                "src/weather.py",
                "src/engine/core.py",
                "src/engine/__init__.py",
                "src/engine/renderer.py",
            ),
        ),
        artifact_quality_errors=(
            "ImportError: cannot import name 'Engine' from 'src.engine' "
            f"({(package_dir / '__init__.py').as_posix()})",
        ),
        writer=_workspace_writer(tmp_path, writes),
        allowed_paths=("src/engine/__init__.py",),
    )

    assert result.ok is True
    assert writes == ["src/engine/__init__.py"]
    init_text = (package_dir / "__init__.py").read_text(encoding="utf-8")
    assert "from .core import Engine" in init_text
    assert "__all__ = _polaris_existing_all" in init_text
    completed = subprocess.run(
        [sys.executable, "main.py"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "altitude=1 temperature=22C alias=True"


def test_npm_script_contract_uses_structured_json_plan_and_fail_closed_run(tmp_path: Path) -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "devDependencies": {"typescript": "^5.0.0"},
            "scripts": {"test": 'echo "Error: no test specified" && exit 1'},
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = normalize_artifact_quality_errors(
        ["Artifact quality scan failed: npm package manifest script 'test' is a placeholder command"]
    )

    plan = build_npm_script_contract_plan(
        base_files={"package.json": package_text, "tsconfig.json": "{}\n", "src/index.ts": "export {};\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.source_tool == "deterministic_npm_script_contract_repair"
    assert {operation.kind for operation in plan.operations} == {"json_set"}
    assert all(operation.path == "package.json" for operation in plan.operations)
    assert all(operation.kind != "write_file" for operation in plan.operations)
    composition = PatchComposer().compose(
        {"package.json": package_text, "tsconfig.json": "{}\n", "src/index.ts": "export {};\n"},
        plan.operations,
    )
    assert composition.ok
    assert composition.patches[0].metadata["structured_operation"] == "json"
    assert composition.patches[0].metadata["write_file_reason"] == "structured_json_serialization"

    write_calls: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append(path)
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    failed = run_runtime_repair(
        source_tool="deterministic_npm_script_contract_repair",
        workspace=tmp_path,
        base_files={"package.json": package_text},
        artifact_quality_errors=("future unrelated verifier failure",),
        writer=writer,
        allowed_paths=("package.json",),
    )

    assert failed.ok is False
    assert failed.error_code == "repair_not_planned"
    assert failed.execution_result is None
    assert write_calls == []


def test_npm_script_contract_repairs_recursive_build_script_with_structured_json() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "devDependencies": {"typescript": "^5.0.0"},
            "scripts": {
                "build": "npm run build",
                "verify": "npm run build",
                "test": "npm run verify",
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            (
                "Artifact quality scan failed: npm package manifest script 'build' "
                "recursively invokes itself via build -> build in package.json"
            )
        ]
    )

    base_files = {
        "package.json": package_text,
        "tsconfig.json": "{}\n",
        "src/index.ts": "export {};\n",
        "src/verify.ts": "export {};\n",
    }
    plan = build_npm_script_contract_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.source_tool == "deterministic_npm_script_contract_repair"
    assert [operation.kind for operation in plan.operations] == ["json_set"]
    assert plan.operations[0].path == "package.json"
    assert plan.operations[0].json_path == ("scripts", "build")
    assert plan.operations[0].value == "tsc -p tsconfig.json"
    composition = PatchComposer().compose(base_files, plan.operations)
    assert composition.ok
    repaired = json.loads(composition.patches[0].content_after)
    assert repaired["scripts"]["build"] == "tsc -p tsconfig.json"
    assert repaired["scripts"]["verify"] == "npm run build"
    assert repaired["scripts"]["test"] == "npm run verify"


def test_npm_script_contract_repairs_strip_types_test_runner_to_compiled_verifier() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "devDependencies": {"typescript": "^5.0.0"},
            "scripts": {
                "build": "tsc -p tsconfig.json",
                "test": "node --experimental-strip-types tests/verify.test.ts",
                "verify": "node --experimental-strip-types src/verify.ts",
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    raw_error = (
        "step verify failed (exit 1): npm run test :: "
        "node --experimental-strip-types tests/verify.test.ts\n\n"
        "FAIL: verifier must exit 0; verifier exit=1; stdoutTail=4\n"
        "[OK  ] source_target_coverage: src/**/*.ts covered with 10 file(s)\n"
        "FAIL - 5/6 checks passed; failed: ts_syntax"
    )
    diagnostics = normalize_artifact_quality_errors([raw_error])
    base_files = {
        "package.json": package_text,
        "tsconfig.json": "{}\n",
        "src/index.ts": "export {};\n",
        "src/verify.ts": "export {};\n",
        "tests/verify.test.ts": "export {};\n",
    }

    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(raw_error,))
    ).to_dict()
    assert coverage["covered_diagnostic_count"] == 1
    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert coverage["items"][0]["matched_source_tools"] == ["deterministic_npm_script_contract_repair"]

    plan = build_npm_script_contract_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.source_tool == "deterministic_npm_script_contract_repair"
    assert [operation.kind for operation in plan.operations] == ["json_set"]
    assert plan.operations[0].json_path == ("scripts", "test")
    assert plan.operations[0].value == "npm run build && node dist/verify.js"
    composition = PatchComposer().compose(base_files, plan.operations)
    assert composition.ok
    repaired = json.loads(composition.patches[0].content_after)
    assert repaired["scripts"]["test"] == "npm run build && node dist/verify.js"


def test_npm_script_contract_public_run_records_receipt_revalidation_evidence(tmp_path: Path) -> None:
    package_path = tmp_path / "package.json"
    tsconfig_path = tmp_path / "tsconfig.json"
    source_path = tmp_path / "src" / "index.ts"
    source_path.parent.mkdir()
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "devDependencies": {"typescript": "^5.0.0"},
            "scripts": {"test": 'echo "Error: no test specified" && exit 1'},
        },
        ensure_ascii=False,
        indent=2,
    )
    package_path.write_text(package_text, encoding="utf-8")
    tsconfig_path.write_text("{}\n", encoding="utf-8")
    source_path.write_text("export const ok = true;\n", encoding="utf-8")

    def writer(path: str, content: str) -> dict[str, object]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    def revalidator(request: DirectorRepairRevalidationRequestV1) -> DirectorRepairRevalidationInputV1:
        assert request.source_tool == "deterministic_npm_script_contract_repair"
        assert request.files_changed == ("package.json",)
        return DirectorRepairRevalidationInputV1(
            residual_artifact_quality_errors=(),
            command=("npm", "test"),
            exit_code=0,
            raw_output_ref="runtime/verifier/npm-test.log",
            metadata={"evidence_source": "unit_test_revalidator"},
        )

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-npm-script-contract",
            workspace=str(tmp_path),
            source_tool="deterministic_npm_script_contract_repair",
            base_files={
                "package.json": package_text,
                "tsconfig.json": "{}\n",
                "src/index.ts": "export const ok = true;\n",
            },
            artifact_quality_errors=(
                "Artifact quality scan failed: npm package manifest script 'test' is a placeholder command",
            ),
            allowed_paths=("package.json",),
        ),
        writer=writer,
        revalidator=revalidator,
    )

    assert result.ok is True
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_npm_script_contract_repair"
    assert receipt.status == "applied"
    assert receipt.authoritative is True
    assert receipt.files_changed == ("package.json",)
    assert receipt.evidence_status == "resolved_evidence"
    assert receipt.errors_before == 1
    assert receipt.errors_after == 0
    assert receipt.net_error_reduction == 1
    assert receipt.verifier_command == ("npm", "test")
    assert receipt.verifier_exit_code == 0
    assert receipt.revalidation_evidence["raw_output_ref"] == "runtime/verifier/npm-test.log"


def test_python_unittest_missing_target_runtime_creates_new_test_and_records_receipt(tmp_path: Path) -> None:
    source_path = tmp_path / "app.py"
    source_path.write_text("VALUE = 1\n", encoding="utf-8")
    writes: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append(path)
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True}

    result = run_runtime_repair(
        source_tool="deterministic_python_unittest_missing_target_repair",
        workspace=tmp_path,
        base_files={"app.py": "VALUE = 1\n"},
        artifact_quality_errors=("declared target file missing 'tests/test_app.py' is missing",),
        writer=writer,
        allowed_paths=("tests/test_app.py",),
    )

    assert result.ok is True
    assert writes == ["tests/test_app.py"]
    assert (
        (tmp_path / "tests" / "test_app.py")
        .read_text(encoding="utf-8")
        .startswith('"""Contract smoke tests for declared Python modules."""')
    )
    assert result.execution_result is not None
    receipt = result.execution_result.receipt
    assert receipt.source_tool == "deterministic_python_unittest_missing_target_repair"
    assert receipt.status == "applied"
    assert receipt.files_changed == ("tests/test_app.py",)
    assert receipt.metadata["requires_revalidation"] is True
    assert receipt.metadata["execution_records"][0]["operation"] == "write_file"
    assert receipt.metadata["write_file_reasons_by_path"] == {
        "tests/test_app.py": "new_python_unittest_contract_target"
    }


def test_javascript_python_migrations_are_executable_in_coverage_and_catalog() -> None:
    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(
                "Artifact quality scan failed: npm package manifest script 'test' is a placeholder command",
                "declared target file missing 'tests/test_app.py' is missing",
            )
        )
    ).to_dict()
    catalog = query_director_repair_strategy_catalog(
        QueryDirectorRepairStrategyCatalogV1(include_items=True, max_items=10_000)
    ).to_dict()
    items_by_source_tool = {item["source_tool"]: item for item in catalog["items"]}

    coverage_items = coverage["items"]
    assert coverage_items[0]["known_rule_matched"] is True
    assert coverage_items[0]["executable_runtime_plan_matched"] is True
    assert "deterministic_npm_script_contract_repair" in coverage_items[0]["matched_source_tools"]
    assert coverage_items[1]["known_rule_matched"] is True
    assert coverage_items[1]["executable_runtime_plan_matched"] is True
    assert "deterministic_python_unittest_missing_target_repair" in coverage_items[1]["matched_source_tools"]
    for source_tool in (
        "deterministic_node_test_script_contract_repair",
        "deterministic_npm_script_contract_repair",
        "deterministic_python_unittest_missing_target_repair",
    ):
        assert items_by_source_tool[source_tool]["implementation_status"] == "executable_runtime"
        assert items_by_source_tool[source_tool]["execution_owner"] == "director.runtime"
        assert items_by_source_tool[source_tool]["bench_driven_migration_required"] is False


def test_node_test_script_contract_run_fails_closed_without_content_match(tmp_path: Path) -> None:
    writes: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append(path)
        return {"ok": True}

    result = run_runtime_repair(
        source_tool="deterministic_node_test_script_contract_repair",
        workspace=tmp_path,
        base_files={"scripts/test.mjs": "console.log('ordinary test');\n"},
        artifact_quality_errors=("scripts/test.mjs missing validation contract",),
        writer=writer,
        allowed_paths=("scripts/test.mjs",),
    )

    assert result.ok is False
    assert result.error_code == "repair_not_planned"
    assert result.execution_result is None
    assert writes == []
