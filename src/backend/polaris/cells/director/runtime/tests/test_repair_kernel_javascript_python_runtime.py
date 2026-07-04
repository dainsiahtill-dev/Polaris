"""Runtime repair coverage for JavaScript/Node and Python migrations."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from polaris.cells.director.runtime.internal.repair_kernel import (
    PatchComposer,
    RepairDiagnostic,
    RepairOperation,
    normalize_artifact_quality_errors,
    run_runtime_repair,
)
from polaris.cells.director.runtime.internal.repair_kernel.javascript_syntax import (
    build_javascript_node_smoke_test_content,
    build_npm_script_contract_plan,
    build_substantive_node_test_script,
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


def _workspace_editor(workspace: Path, edits: list[str]):
    def editor(operation: RepairOperation) -> dict[str, object]:
        path = str(operation.path)
        expected = str(operation.expected)
        replacement = str(operation.replacement)
        edits.append(path)
        target = workspace / path
        current = target.read_text(encoding="utf-8")
        if expected not in current:
            return {"ok": False, "error": "expected text not found"}
        target.write_text(current.replace(expected, replacement, 1), encoding="utf-8")
        return {"ok": True}

    return editor


def _read_base_files(workspace: Path, paths: tuple[str, ...]) -> dict[str, str]:
    return {path: (workspace / path).read_text(encoding="utf-8") for path in paths}


def _run_js_missing_method_runtime(
    workspace: Path,
    *,
    paths: tuple[str, ...],
    artifact_quality_errors: tuple[str, ...],
    allowed_paths: tuple[str, ...],
):
    writes: list[str] = []
    edits: list[str] = []
    result = run_runtime_repair(
        source_tool="deterministic_javascript_missing_method_runtime_repair",
        workspace=workspace,
        base_files=_read_base_files(workspace, paths),
        artifact_quality_errors=artifact_quality_errors,
        writer=_workspace_writer(workspace, writes),
        editor=_workspace_editor(workspace, edits),
        allowed_paths=allowed_paths,
    )
    return result, writes, edits


def test_javascript_node_smoke_test_uses_source_entrypoint_for_plain_javascript_package() -> None:
    content = build_javascript_node_smoke_test_content(
        "tests/product.test.js",
        {
            "package.json": json.dumps(
                {
                    "name": "plain-js-product",
                    "type": "module",
                    "main": "src/index.js",
                    "scripts": {
                        "build": "node --check src/index.js",
                        "test": "node --test tests/product.test.js",
                    },
                },
                ensure_ascii=False,
            ),
            "src/index.js": "console.log('ok');\n",
            "src/engine/rules.js": "export const rules = [];\n",
        },
    )

    assert 'const entrypoint = "src/index.js";' in content
    assert "dist/index.js" not in content
    assert "entrypoint missing: ${entrypoint}" in content


def test_javascript_missing_method_runtime_adds_aliases_with_precise_edit(tmp_path: Path) -> None:
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "index.js").write_text(
        'import { AlchemyEngine } from "./engine/AlchemyEngine.js";\n'
        "function main() {\n"
        "  const engine = new AlchemyEngine();\n"
        "  const notes = [{ id: 'n1' }];\n"
        "  engine.addRecipe({ name: 'moon' });\n"
        "  const { dreamCards, rituals } = engine.transmute(notes);\n"
        "  return { dreamCards, rituals };\n"
        "}\n"
        "main();\n",
        encoding="utf-8",
    )
    class_path = tmp_path / "src" / "engine" / "AlchemyEngine.js"
    class_path.write_text(
        "export class AlchemyEngine {\n"
        "  constructor({ recipes = [] } = {}) {\n"
        "    this.recipes = recipes;\n"
        "  }\n\n"
        "  refine(notes) {\n"
        "    return { dreamCards: notes, unconsumed: [] };\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    result, writes, edits = _run_js_missing_method_runtime(
        tmp_path,
        paths=("src/index.js", "src/engine/AlchemyEngine.js"),
        artifact_quality_errors=(
            "Artifact quality scan failed: workspace validation command failed (npm run start): "
            f"file://{tmp_path}/src/index.js:4\n"
            "  engine.addRecipe({ name: 'moon' });\n"
            "         ^\n\n"
            "TypeError: engine.addRecipe is not a function",
        ),
        allowed_paths=("src/engine/AlchemyEngine.js",),
    )

    assert result.ok is True
    assert writes == []
    assert edits == ["src/engine/AlchemyEngine.js"]
    repaired = class_path.read_text(encoding="utf-8")
    assert "addRecipe(recipe)" in repaired
    assert "this.recipes.push(recipe);" in repaired
    assert "transmute(notes)" in repaired
    assert "const result = this.refine(notes);" in repaired
    assert "dreamCards: result.dreamCards ?? result.cards ?? []" in repaired
    assert "rituals: result.rituals ?? []" in repaired
    assert result.execution_result is not None
    record = result.execution_result.receipt.metadata["execution_records"][0]
    assert record["operation"] == "edit_file"


def test_javascript_missing_method_runtime_aliases_collection_and_refine_shape(tmp_path: Path) -> None:
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "index.js").write_text(
        'import { AlchemyEngine } from "./engine/AlchemyEngine.js";\n'
        "function main() {\n"
        "  const engine = new AlchemyEngine({ recipes: [] });\n"
        "  const notes = [{ id: 'n1' }];\n"
        "  engine.listRecipes().length;\n"
        "  const { dreamCards, unmatched } = engine.transmute(notes);\n"
        "  return { dreamCards, unmatched };\n"
        "}\n"
        "main();\n",
        encoding="utf-8",
    )
    class_path = tmp_path / "src" / "engine" / "AlchemyEngine.js"
    class_path.write_text(
        "export class AlchemyEngine {\n"
        "  constructor({ recipes = [] } = {}) {\n"
        "    this.recipes = recipes;\n"
        "  }\n\n"
        "  registerRecipe(recipe) {\n"
        "    this.recipes.push(recipe);\n"
        "    return recipe;\n"
        "  }\n\n"
        "  refine(notes) {\n"
        "    return { cards: notes, unmatched: [] };\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    result, writes, edits = _run_js_missing_method_runtime(
        tmp_path,
        paths=("src/index.js", "src/engine/AlchemyEngine.js"),
        artifact_quality_errors=(
            "Artifact quality scan failed: workspace validation command failed (npm run start): "
            f"file://{tmp_path}/src/index.js:5\n"
            "  engine.listRecipes().length;\n"
            "         ^\n\n"
            "TypeError: engine.listRecipes is not a function",
        ),
        allowed_paths=("src/engine/AlchemyEngine.js",),
    )

    assert result.ok is True
    assert writes == []
    assert edits == ["src/engine/AlchemyEngine.js"]
    repaired = class_path.read_text(encoding="utf-8")
    assert "listRecipes()" in repaired
    assert "return Array.isArray(this.recipes) ? [...this.recipes] : [];" in repaired
    assert "transmute(notes)" in repaired
    assert "const result = this.refine(notes);" in repaired
    assert "dreamCards: result.dreamCards ?? result.cards ?? []" in repaired
    assert "unmatched: result.unmatched ?? result.unconsumed ?? []" in repaired


def test_javascript_missing_method_runtime_repairs_imported_loop_variable_class(tmp_path: Path) -> None:
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "index.js").write_text(
        'import { Recipe } from "./models/Recipe.js";\n'
        'import { AlchemyEngine } from "./engine/AlchemyEngine.js";\n'
        "const recipes = [new Recipe({ name: 'moon', keywords: ['moon'], absurdityBoost: 4, ritual: 'hum' })];\n"
        "new AlchemyEngine({ recipes }).transmute([{ content: 'moon', matchesAllTags: () => true, intensity: 1 }]);\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
        'import { Recipe } from "../models/Recipe.js";\n'
        "export class AlchemyEngine {\n"
        "  constructor({ recipes = [] } = {}) { this.recipes = recipes; }\n"
        "  pickRecipeFor(notes) {\n"
        "    for (const recipe of this.recipes) {\n"
        "      if (recipe.matchesAll(notes)) return recipe;\n"
        "    }\n"
        "    return null;\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    recipe_path = tmp_path / "src" / "models" / "Recipe.js"
    recipe_path.write_text(
        "export class Recipe {\n"
        "  constructor({ name, requiredTags = [] } = {}) {\n"
        "    this.name = name;\n"
        "    this.requiredTags = requiredTags;\n"
        "  }\n"
        "  isSatisfiedBy(notes) { return Array.isArray(notes); }\n"
        "}\n",
        encoding="utf-8",
    )

    result, writes, edits = _run_js_missing_method_runtime(
        tmp_path,
        paths=("src/index.js", "src/engine/AlchemyEngine.js", "src/models/Recipe.js"),
        artifact_quality_errors=(
            "Artifact quality scan failed: workspace validation command failed (npm run start): "
            "TypeError: recipe.matchesAll is not a function\n"
            f"    at AlchemyEngine.pickRecipeFor (file://{tmp_path}/src/engine/AlchemyEngine.js:6:18)",
        ),
        allowed_paths=("src/models/Recipe.js",),
    )

    assert result.ok is True
    assert writes == []
    assert edits == ["src/models/Recipe.js"]
    repaired = recipe_path.read_text(encoding="utf-8")
    assert "matchesAll(notes)" in repaired
    assert "return this.isSatisfiedBy(notes);" in repaired
    assert "keywords," in repaired
    assert "absurdityBoost," in repaired
    assert "ritual" in repaired
    assert "this.keywords = Array.isArray(keywords) ? keywords.map(String) : [];" in repaired
    assert "this.absurdityBoost = Number.isFinite(absurdityBoost) ? absurdityBoost : 0;" in repaired
    assert "this.ritual = ritual;" in repaired


def test_javascript_missing_method_runtime_repairs_constructor_object_contract(tmp_path: Path) -> None:
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    card_path = tmp_path / "src" / "models" / "DreamCard.js"
    card_path.write_text(
        "export class DreamCard {\n"
        "  constructor({ id, title, narrative, sourceNoteIds = [] } = {}) {\n"
        '    if (!id) throw new Error("DreamCard requires an id");\n'
        '    if (!title) throw new Error("DreamCard requires a title");\n'
        '    if (!narrative) throw new Error("DreamCard requires a narrative");\n'
        "    this.id = id;\n"
        "    this.title = title;\n"
        "    this.narrative = narrative;\n"
        "    this.sourceNoteIds = sourceNoteIds;\n"
        "  }\n"
        "  toJSON() {\n"
        "    return {\n"
        "      id: this.id,\n"
        "      title: this.title,\n"
        "      narrative: this.narrative,\n"
        "    };\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "smoke.test.js").write_text(
        'import { DreamCard } from "../src/models/DreamCard.js";\n'
        "new DreamCard({\n"
        '  title: "Library of Forgotten Names",\n'
        '  body: "Each book whispered a name I almost remembered.",\n'
        '  tags: ["memory", "library"],\n'
        "  createdAt: new Date(),\n"
        "});\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
        'import * as DreamCard from "../models/DreamCard.js";\n'
        "DreamCard.composeTitle(0.42);\n"
        "new DreamCard.DreamCard({ title: 'x', fragments: ['a'], absurdity: 4, ritual: 'hum' });\n",
        encoding="utf-8",
    )

    result, writes, edits = _run_js_missing_method_runtime(
        tmp_path,
        paths=("src/models/DreamCard.js", "tests/smoke.test.js", "src/engine/AlchemyEngine.js"),
        artifact_quality_errors=(
            "Artifact quality scan failed: workspace validation command failed (npm test): "
            "Error: DreamCard requires an id\n"
            f"    at new DreamCard (file://{tmp_path}/src/models/DreamCard.js:3:20)",
        ),
        allowed_paths=("src/models/DreamCard.js",),
    )

    assert result.ok is True
    assert writes == []
    assert edits == ["src/models/DreamCard.js"]
    repaired = card_path.read_text(encoding="utf-8")
    assert "body," in repaired
    assert "tags," in repaired
    assert "createdAt," in repaired
    assert "fragments," in repaired
    assert "absurdity," in repaired
    assert "ritual," in repaired
    assert "const normalizedId" in repaired
    assert "const normalizedNarrative" in repaired
    assert "this.id = normalizedId;" in repaired
    assert "this.narrative = normalizedNarrative;" in repaired
    assert "this.body =" in repaired
    assert "this.tags = Array.isArray(tags) ? tags.map(String) : [];" in repaired
    assert "createdAt: this.createdAt instanceof Date ? this.createdAt.toISOString() : this.createdAt" in repaired
    assert "body: this.body" in repaired
    assert "tags: this.tags" in repaired
    assert "this.fragments = Array.isArray(fragments) ? fragments.map(String) : [];" in repaired
    assert "this.absurdity = Number.isFinite(absurdity) ? absurdity : 0;" in repaired
    assert "this.ritual = ritual;" in repaired
    assert "export function composeTitle" in repaired


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


def test_python_unresolved_import_symbol_uses_typed_metadata_without_raw_message() -> None:
    diagnostics = (
        RepairDiagnostic(
            source="artifact_quality",
            code="unresolved_import_symbol",
            message="typed unresolved import symbol",
            path="shared/__init__.py",
            raw="typed metadata only",
            metadata={
                "symbol": "Registry",
                "module": "shared.registry",
                "importer_path": "shared/__init__.py",
            },
        ),
    )

    plan = build_python_unresolved_import_symbol_plan(
        base_files={
            "shared/__init__.py": "from shared.registry import Registry\n",
            "shared/registry.py": "class ServiceRegistry:\n    pass\n",
        },
        diagnostics=diagnostics,
    )

    assert plan is not None
    assert plan.operations[0].path == "shared/registry.py"
    assert "Registry = ServiceRegistry" in str(plan.operations[0].replacement)


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
            f"ImportError: cannot import name 'run' from 'src.engine' ({(package_dir / '__init__.py').as_posix()})",
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
            f"ImportError: cannot import name 'Engine' from 'src.engine' ({(package_dir / '__init__.py').as_posix()})",
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


def test_npm_script_contract_repairs_plain_javascript_placeholder_lint() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "type": "module",
            "scripts": {
                "start": "node src/index.js",
                "lint": 'echo "lint placeholder - wire eslint later" && exit 0',
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            (
                "Artifact quality scan failed: npm package manifest script 'lint' "
                'is a placeholder command: echo "lint placeholder - wire eslint later" && exit 0 in package.json'
            )
        ]
    )

    plan = build_npm_script_contract_plan(
        base_files={"package.json": package_text, "src/index.js": "export const ok = true;\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.source_tool == "deterministic_npm_script_contract_repair"
    assert [(operation.kind, operation.path, operation.json_path) for operation in plan.operations] == [
        ("json_set", "package.json", ("scripts", "lint"))
    ]
    assert plan.operations[0].value == "node --check src/index.js"

    composition = PatchComposer().compose(
        {"package.json": package_text, "src/index.js": "export const ok = true;\n"},
        plan.operations,
    )
    assert composition.ok
    repaired = json.loads(composition.patches[0].content_after)
    assert repaired["scripts"]["lint"] == plan.operations[0].value


def test_npm_script_contract_uses_typed_placeholder_script_metadata() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "type": "module",
            "scripts": {
                "start": "node src/index.js",
                "lint": 'echo "lint placeholder - wire eslint later" && exit 0',
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = (
        RepairDiagnostic(
            source="artifact_quality",
            code="npm_manifest_invalid",
            message="npm manifest script contract violation",
            path="package.json",
            metadata={
                "manifest_path": "package.json",
                "script_name": "lint",
                "script_issue": "placeholder_command",
            },
        ),
    )

    plan = build_npm_script_contract_plan(
        base_files={"package.json": package_text, "src/index.js": "export const ok = true;\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert [(operation.kind, operation.path, operation.json_path) for operation in plan.operations] == [
        ("json_set", "package.json", ("scripts", "lint"))
    ]
    assert plan.operations[0].value == "node --check src/index.js"


def test_npm_script_contract_uses_structured_top_level_script_metadata() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "type": "module",
            "scripts": {
                "start": "node src/index.js",
                "lint": 'echo "lint placeholder - wire eslint later" && exit 0',
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            {
                "source": "artifact_quality",
                "code": "npm_manifest_invalid",
                "message": "typed npm script issue",
                "path": "package.json",
                "manifest_path": "package.json",
                "script_name": "lint",
                "script_issue": "placeholder_command",
                "raw": "typed metadata only",
            }
        ]
    )

    plan = build_npm_script_contract_plan(
        base_files={"package.json": package_text, "src/index.js": "export const ok = true;\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.operations[0].json_path == ("scripts", "lint")
    assert plan.operations[0].value == "node --check src/index.js"


def test_npm_script_contract_uses_public_script_alias_without_raw_message() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "type": "module",
            "scripts": {
                "start": "node src/index.js",
                "lint": 'echo "lint placeholder - wire eslint later" && exit 0',
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = (
        RepairDiagnostic(
            source="artifact_quality",
            code="npm_manifest_invalid",
            message="typed npm script issue",
            path="package.json",
            raw="typed metadata only",
            metadata={
                "manifest_path": "package.json",
                "script": "lint",
                "script_issue": "placeholder_command",
            },
        ),
    )

    plan = build_npm_script_contract_plan(
        base_files={"package.json": package_text, "src/index.js": "export const ok = true;\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.operations[0].json_path == ("scripts", "lint")
    assert plan.operations[0].value == "node --check src/index.js"


def test_npm_script_contract_uses_typed_node_test_runner_contract_metadata() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "type": "module",
            "scripts": {"test": "node tests/product.test.js"},
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = (
        RepairDiagnostic(
            source="artifact_quality",
            code="npm_manifest_invalid",
            message="typed npm script issue",
            path="package.json",
            metadata={
                "manifest_path": "package.json",
                "script_name": "test",
                "script_issue": "node_test_runner_contract",
            },
        ),
    )

    plan = build_npm_script_contract_plan(
        base_files={
            "package.json": package_text,
            "tests/product.test.js": "import test from 'node:test';\ntest('ok', () => {});\n",
        },
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert [(operation.kind, operation.path, operation.json_path) for operation in plan.operations] == [
        ("json_set", "package.json", ("scripts", "test"))
    ]
    assert plan.operations[0].value == "node --test tests/product.test.js"


def test_npm_script_contract_uses_typed_fixed_port_conflict_metadata() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "scripts": {
                "start": "npx --yes http-server . -p 8080 -c-1",
                "serve": "npx --yes http-server . --port 8080 -c-1",
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = (
        RepairDiagnostic(
            source="artifact_quality",
            code="npm_manifest_invalid",
            message="typed npm script issue",
            path="package.json",
            metadata={
                "manifest_path": "package.json",
                "script_name": "serve",
                "script_issue": "fixed_port_conflict",
            },
        ),
    )

    plan = build_npm_script_contract_plan(
        base_files={"package.json": package_text},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert [(operation.kind, operation.path, operation.json_path) for operation in plan.operations] == [
        ("json_set", "package.json", ("scripts", "serve")),
        ("json_set", "package.json", ("scripts", "start")),
    ]
    assert {operation.value for operation in plan.operations} == {
        "npx --yes http-server . --port ${PORT:-0} -c-1",
        "npx --yes http-server . -p ${PORT:-0} -c-1",
    }


def test_npm_script_contract_repairs_python_commands_with_structured_json() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "type": "module",
            "scripts": {
                "test": "node --test tests/product.test.js",
                "test:py": "python -m unittest discover -s tests -p 'test_*.py' -v",
                "test:all": "node --test tests/product.test.js && python -m unittest discover -s tests",
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            (
                "Artifact quality scan failed: npm package manifest contains Python command "
                "in script 'test:py' in package.json"
            )
        ]
    )
    base_files = {
        "package.json": package_text,
        "tests/product.test.js": "import test from 'node:test';\ntest('ok', () => {});\n",
    }

    plan = build_npm_script_contract_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.source_tool == "deterministic_npm_script_contract_repair"
    assert [(operation.kind, operation.path, operation.json_path) for operation in plan.operations] == [
        ("json_set", "package.json", ("scripts", "test:all")),
        ("json_set", "package.json", ("scripts", "test:py")),
    ]
    assert {operation.value for operation in plan.operations} == {"node --test tests/product.test.js"}
    composition = PatchComposer().compose(base_files, plan.operations)
    assert composition.ok
    repaired = json.loads(composition.patches[0].content_after)
    assert repaired["scripts"]["test"] == "node --test tests/product.test.js"
    assert repaired["scripts"]["test:py"] == "node --test tests/product.test.js"
    assert repaired["scripts"]["test:all"] == "node --test tests/product.test.js"
    assert "python" not in composition.patches[0].content_after.lower()


def test_npm_script_contract_repairs_python_commands_from_typed_metadata() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "type": "module",
            "scripts": {
                "test": "node --test tests/product.test.js",
                "test:py": "python -m unittest discover -s tests",
                "test:all": "node --test tests/product.test.js && python -m unittest discover -s tests",
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = (
        RepairDiagnostic(
            source="artifact_quality",
            code="npm_manifest_invalid",
            message="npm manifest script contract violation",
            path="package.json",
            metadata={
                "manifest_path": "package.json",
                "script_name": "test:py",
                "script_issue": "python_command",
            },
        ),
    )
    base_files = {
        "package.json": package_text,
        "tests/product.test.js": "import test from 'node:test';\ntest('ok', () => {});\n",
    }

    plan = build_npm_script_contract_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert [(operation.kind, operation.path, operation.json_path) for operation in plan.operations] == [
        ("json_set", "package.json", ("scripts", "test:all")),
        ("json_set", "package.json", ("scripts", "test:py")),
    ]
    assert {operation.value for operation in plan.operations} == {"node --test tests/product.test.js"}


def test_npm_script_contract_declines_plain_javascript_placeholder_without_source() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "scripts": {
                "lint": 'echo "lint placeholder - wire eslint later" && exit 0',
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            (
                "Artifact quality scan failed: npm package manifest script 'lint' "
                'is a placeholder command: echo "lint placeholder - wire eslint later" && exit 0 in package.json'
            )
        ]
    )

    plan = build_npm_script_contract_plan(
        base_files={"package.json": package_text},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_npm_script_contract_does_not_wrap_placeholder_upstream_script() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "scripts": {
                "build": 'echo "build placeholder - wire bundler later" && exit 0',
                "verify": 'echo "verify placeholder - wire tests later" && exit 0',
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            (
                "Artifact quality scan failed: npm package manifest script 'verify' "
                'is a placeholder command: echo "verify placeholder - wire tests later" && exit 0 in package.json'
            )
        ]
    )

    plan = build_npm_script_contract_plan(
        base_files={"package.json": package_text, "src/index.js": "console.log('ok');\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert [(operation.kind, operation.path, operation.json_path) for operation in plan.operations] == [
        ("json_set", "package.json", ("scripts", "verify"))
    ]
    assert plan.operations[0].value == "node --check src/index.js"


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


def test_npm_script_contract_uses_typed_missing_entrypoint_metadata() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "type": "module",
            "devDependencies": {"typescript": "^5.0.0"},
            "scripts": {"start": "node src/index.js"},
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = (
        RepairDiagnostic(
            source="artifact_quality",
            code="npm_manifest_invalid",
            message="npm manifest script contract violation",
            path="package.json",
            metadata={
                "manifest_path": "package.json",
                "script_name": "start",
                "script_issue": "missing_local_entrypoint",
                "entrypoint": "src/index.js",
            },
        ),
    )

    plan = build_npm_script_contract_plan(
        base_files={
            "package.json": package_text,
            "tsconfig.json": "{}\n",
            "src/index.ts": "export {};\n",
        },
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert [(operation.kind, operation.path, operation.json_path) for operation in plan.operations] == [
        ("json_set", "package.json", ("scripts", "build")),
        ("json_set", "package.json", ("scripts", "start")),
    ]
    assert plan.operations[0].value == "tsc"
    assert plan.operations[1].value == "npm run build && node dist/index.js"


def test_npm_script_contract_uses_typed_missing_compiled_entrypoint_metadata() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "type": "module",
            "devDependencies": {"typescript": "^5.0.0"},
            "scripts": {
                "build": "tsc -p tsconfig.json",
                "start": "node dist/main.js",
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = (
        RepairDiagnostic(
            source="artifact_quality",
            code="javascript_module_error",
            message="node runtime failed",
            path=None,
            raw="typed metadata only",
            metadata={
                "script_name": "start",
                "script_issue": "missing_compiled_entrypoint",
                "entrypoint": "dist/main.js",
            },
        ),
    )

    plan = build_npm_script_contract_plan(
        base_files={
            "package.json": package_text,
            "tsconfig.json": "{}\n",
            "src/index.ts": "export {};\n",
        },
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert [(operation.kind, operation.path, operation.json_path) for operation in plan.operations] == [
        ("json_set", "package.json", ("scripts", "start"))
    ]
    assert plan.operations[0].value == "node dist/index.js"


def test_npm_script_contract_uses_typed_repairable_test_script_metadata() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "type": "module",
            "devDependencies": {"typescript": "^5.0.0"},
            "scripts": {
                "build": "tsc -p tsconfig.json",
                "test": "node -e \"console.log('unterminated)\"",
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = (
        RepairDiagnostic(
            source="artifact_quality",
            code="npm_manifest_invalid",
            message="npm manifest script contract violation",
            path="package.json",
            raw="typed metadata only",
            metadata={
                "manifest_path": "package.json",
                "script_name": "test",
                "script_issue": "invalid_node_eval_syntax",
            },
        ),
    )

    plan = build_npm_script_contract_plan(
        base_files={
            "package.json": package_text,
            "tsconfig.json": "{}\n",
            "src/verify.ts": "export {};\n",
        },
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert [(operation.kind, operation.path, operation.json_path) for operation in plan.operations] == [
        ("json_set", "package.json", ("scripts", "test"))
    ]
    assert plan.operations[0].value == "npm run build && node dist/verify.js"


def test_npm_script_contract_uses_typed_default_failing_test_script_metadata() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "type": "module",
            "devDependencies": {"typescript": "^5.0.0"},
            "scripts": {
                "build": "tsc -p tsconfig.json",
                "test": 'echo "Error: no test specified" && exit 1',
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = (
        RepairDiagnostic(
            source="artifact_quality",
            code="npm_manifest_invalid",
            message="typed npm script issue",
            path="package.json",
            raw="typed metadata only",
            metadata={
                "manifest_path": "package.json",
                "script_name": "test",
                "script_issue": "default_failing_test_script",
            },
        ),
    )

    plan = build_npm_script_contract_plan(
        base_files={
            "package.json": package_text,
            "tsconfig.json": "{}\n",
            "src/verify.ts": "export {};\n",
        },
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert [(operation.kind, operation.path, operation.json_path) for operation in plan.operations] == [
        ("json_set", "package.json", ("scripts", "test"))
    ]
    assert plan.operations[0].value == "npm run build && node dist/verify.js"


def test_npm_script_contract_uses_typed_typescript_source_loader_metadata() -> None:
    package_text = json.dumps(
        {
            "name": "sample",
            "version": "1.0.0",
            "type": "module",
            "devDependencies": {"typescript": "^5.0.0"},
            "scripts": {
                "build": "tsc -p tsconfig.json",
                "start": "node --loader ts-node/esm src/index.ts",
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    diagnostics = (
        RepairDiagnostic(
            source="artifact_quality",
            code="javascript_module_error",
            message="runtime module error",
            path="src/index.ts",
            raw="typed metadata only",
            metadata={
                "script_name": "start",
                "script_issue": "typescript_source_loader_require_cycle",
            },
        ),
    )

    plan = build_npm_script_contract_plan(
        base_files={
            "package.json": package_text,
            "tsconfig.json": "{}\n",
            "src/index.ts": "export {};\n",
        },
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert [(operation.kind, operation.path, operation.json_path) for operation in plan.operations] == [
        ("json_set", "package.json", ("scripts", "start"))
    ]
    assert plan.operations[0].value == "npm run build && node dist/index.js"


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


def test_node_test_script_contract_run_replaces_overstrict_script(tmp_path: Path) -> None:
    legacy_script = (
        "import { readFileSync } from 'node:fs';\n"
        "const text = readFileSync('src/analytics/match-analytics.ts', 'utf8');\n"
        "if (!/validate[A-Za-z]+Record/.test(text)) {\n"
        "  throw new Error('missing validation contract in src/analytics/match-analytics.ts');\n"
        "}\n"
    )
    script_path = tmp_path / "scripts" / "test.mjs"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(legacy_script, encoding="utf-8")
    written: dict[str, str] = {}

    def writer(path: str, content: str) -> dict[str, object]:
        written[path] = content
        return {"ok": True, "operation": "modify"}

    result = run_runtime_repair(
        source_tool="deterministic_node_test_script_contract_repair",
        workspace=tmp_path,
        base_files={"scripts/test.mjs": legacy_script},
        artifact_quality_errors=("scripts/test.mjs missing validation contract",),
        writer=writer,
        allowed_paths=("scripts/test.mjs",),
    )

    assert result.ok is True
    assert result.error_code is None
    assert result.execution_result is not None
    assert written == {"scripts/test.mjs": build_substantive_node_test_script()}
    receipt = result.execution_result.receipt
    assert receipt.source_tool == "deterministic_node_test_script_contract_repair"
    assert receipt.files_changed == ("scripts/test.mjs",)
    assert receipt.metadata["adapter_transform_migrated"] is True
