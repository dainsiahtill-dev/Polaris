from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from polaris.cells.director.runtime.public.repair_kernel_contracts import (
    RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
    RUST_MISSING_MODULE_FILE_STUB,
)
from polaris.cells.roles.adapters.internal.director.runtime_repair_tool_adapter import (
    run_runtime_repair_with_director_tools,
)
from polaris.cells.roles.adapters.tests.test_director_adapter_pure import (
    _project_deferred_repair_results_for_test,
    _test_execution_attempt,
)


def _run_runtime_rust_repair(
    workspace: Path,
    *,
    source_tool: str,
    artifact_quality_errors: list[str],
    relative_paths: tuple[str, ...],
    allowed_paths: tuple[str, ...] | None = None,
) -> list[dict[str, object]]:
    base_files = {
        relative_path: (workspace / relative_path).read_text(encoding="utf-8")
        for relative_path in relative_paths
        if (workspace / relative_path).is_file()
    }
    task_id = "factory-quality-gate:test"
    results = run_runtime_repair_with_director_tools(
        SimpleNamespace(workspace=str(workspace), _execution=SimpleNamespace(_message_bus=None)),
        workspace_path=workspace,
        task_id=task_id,
        source_tool=source_tool,
        execution_attempt=_test_execution_attempt(workspace, task_id),
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=allowed_paths or relative_paths,
        use_editor=True,
    )
    return _project_deferred_repair_results_for_test(workspace, results)


def _rust_e0583_missing_module_error(
    *,
    module_name: str,
    path: str,
    line: int,
    declaration: str,
    candidates: str,
) -> str:
    return (
        f"error[E0583]: file not found for module `{module_name}`\n"
        f" --> {path}:{line}:1\n"
        "  |\n"
        f"{line} | {declaration}\n"
        "  | ^^^^^^^^^^^^^^^\n"
        "  |\n"
        f"  = help: to create the module `{module_name}`, create file {candidates}\n"
    )


def _assert_comment_only_missing_module_stub(content: str) -> None:
    assert content == RUST_MISSING_MODULE_FILE_STUB
    assert "pub enum" not in content
    assert "pub struct" not in content
    assert "::" not in content


def test_deterministic_rust_dependency_repair_adds_serde_and_serde_json(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "kitchen-taste-palette"\n\n[dependencies]\n', encoding="utf-8"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text(
        "use serde::{Deserialize, Serialize};\nfn main() { let _ = serde_json::json!({}); }\n",
        encoding="utf-8",
    )

    results = _run_runtime_rust_repair(
        tmp_path,
        source_tool="deterministic_rust_dependency_repair",
        artifact_quality_errors=["error[E0432]: unresolved import `serde`"],
        relative_paths=("Cargo.toml", "src/main.rs"),
    )

    cargo = (tmp_path / "Cargo.toml").read_text(encoding="utf-8")
    assert results
    assert 'serde = { version = "1.0", features = ["derive"] }' in cargo
    assert 'serde_json = "1.0"' in cargo


def test_deterministic_rust_line_suggestion_repair_applies_rustc_field_hint(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "kitchen-flavor-palette"\n', encoding="utf-8")
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "plating_rules.rs").write_text(
        "use crate::models::Recipe;\n\n"
        "fn has(recipe: &Recipe, needle: &str) -> bool {\n"
        "    recipe\n"
        "        .ingredients\n"
        "        .iter()\n"
        "        .any(|i| i.eq_ignore_ascii_case(needle))\n"
        "}\n",
        encoding="utf-8",
    )

    results = _run_runtime_rust_repair(
        tmp_path,
        source_tool="deterministic_rust_line_suggestion_repair",
        artifact_quality_errors=[
            "error[E0599]: no method named `eq_ignore_ascii_case` found for reference `&Ingredient` in the current scope\n"
            "  --> src/engine/plating_rules.rs:7:20\n"
            "   |\n"
            "help: one of the expressions' fields has a method of the same name\n"
            "   |\n"
            "7 |         .any(|i| i.name.eq_ignore_ascii_case(needle))\n"
            "   |                    +++++\n"
        ],
        relative_paths=("Cargo.toml", "src/engine/plating_rules.rs"),
    )

    repaired = (tmp_path / "src" / "engine" / "plating_rules.rs").read_text(encoding="utf-8")
    assert results
    assert "i.name.eq_ignore_ascii_case(needle)" in repaired
    assert "i.eq_ignore_ascii_case(needle)" not in repaired


def test_deterministic_rust_line_suggestion_repair_applies_borrow_hint(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "kitchen-flavor-palette"\n', encoding="utf-8")
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "mod.rs").write_text(
        "fn build() {\n    let palette = flavor_profile_to_palette(recipe.overall_flavor_profile());\n}\n",
        encoding="utf-8",
    )

    results = _run_runtime_rust_repair(
        tmp_path,
        source_tool="deterministic_rust_line_suggestion_repair",
        artifact_quality_errors=[
            "error[E0308]: mismatched types\n"
            "  --> src/engine/mod.rs:2:45\n"
            "   |\n"
            "2 |     let palette = flavor_profile_to_palette(recipe.overall_flavor_profile());\n"
            "   |                   ------------------------- ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ expected `&FlavorProfile`, found `FlavorProfile`\n"
            "help: consider borrowing here\n"
            "   |\n"
            "2 |     let palette = flavor_profile_to_palette(&recipe.overall_flavor_profile());\n"
            "   |                                             +\n"
        ],
        relative_paths=("Cargo.toml", "src/engine/mod.rs"),
    )

    repaired = (tmp_path / "src" / "engine" / "mod.rs").read_text(encoding="utf-8")
    assert results
    assert "flavor_profile_to_palette(&recipe.overall_flavor_profile())" in repaired


def test_deterministic_rust_unresolved_pub_use_repair_removes_invalid_exports(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "kitchen-taste-palette"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text(
        "pub mod engine;\npub mod models;\npub use engine::Engine;\npub use models::{Ingredient, Palette, Recipe};\n",
        encoding="utf-8",
    )

    results = _run_runtime_rust_repair(
        tmp_path,
        source_tool="deterministic_rust_unresolved_pub_use_repair",
        artifact_quality_errors=[
            "error[E0432]: unresolved import `engine::Engine`\nno `Engine` in `engine`",
            "error[E0432]: unresolved import `models::Palette`\nno `Palette` in `models`",
        ],
        relative_paths=("Cargo.toml", "src/lib.rs"),
    )

    repaired = (tmp_path / "src" / "lib.rs").read_text(encoding="utf-8")
    assert results
    assert "pub use engine::Engine;" not in repaired
    assert "pub use models::{Ingredient, Recipe};" in repaired


def test_deterministic_rust_trait_import_repair_uses_rustc_suggestion(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "kitchen-taste-palette"\n', encoding="utf-8")
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "mod.rs").write_text(
        "pub mod mapper;\n\n"
        "pub struct Engine {\n"
        "    generator: mapper::WeightedPaletteGenerator,\n"
        "}\n\n"
        "impl Engine {\n"
        "    pub fn generate(&self) {\n"
        "        self.generator.generate();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    results = _run_runtime_rust_repair(
        tmp_path,
        source_tool="deterministic_rust_trait_import_repair",
        artifact_quality_errors=[
            "error[E0599]: no method named `generate` found for struct `WeightedPaletteGenerator` in the current scope\n"
            "  --> src/engine/mod.rs:9:24\n"
            "   |\n"
            "help: trait `PaletteGenerator` which provides `generate` is implemented but not in scope; perhaps you want to import it\n"
            "   |\n"
            "1  + use crate::engine::mapper::PaletteGenerator;\n"
            "   |\n"
        ],
        relative_paths=("Cargo.toml", "src/engine/mod.rs"),
    )

    repaired = (tmp_path / "src" / "engine" / "mod.rs").read_text(encoding="utf-8")
    assert results
    result_payload = cast(dict[str, object], results[0]["result"])
    assert result_payload["source_tool"] == "deterministic_rust_trait_import_repair"
    assert repaired.startswith("use crate::engine::mapper::PaletteGenerator;\n")


def test_rust_missing_module_runtime_creates_comment_only_topology_stub(tmp_path: Path) -> None:
    """Missing module repair creates topology only, not guessed symbols."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "stardust-alchemy"\n', encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    models_dir = src / "models"
    models_dir.mkdir()

    (src / "main.rs").write_text(
        "mod models;\n"
        "use crate::models::element::Element;\n"
        "use crate::models::ingredient::Ingredient;\n"
        "fn main() {\n"
        "    let e = Element::Stardust;\n"
        "    let _i = Ingredient { element: e };\n"
        "}\n",
        encoding="utf-8",
    )

    (models_dir / "mod.rs").write_text(
        "pub mod element;\npub mod ingredient;\n",
        encoding="utf-8",
    )

    (models_dir / "ingredient.rs").write_text(
        "use crate::models::element::Element;\n"
        "\n"
        "pub struct Ingredient {\n"
        "    pub element: Element,\n"
        "}\n"
        "\n"
        "impl Ingredient {\n"
        "    pub fn new(element: Element) -> Self {\n"
        "        match element {\n"
        "            Element::Stardust => Ingredient { element },\n"
        "            Element::Fire => Ingredient { element },\n"
        "            Element::Crystal => Ingredient { element },\n"
        "            Element::Ember => Ingredient { element },\n"
        "            Element::Tide => Ingredient { element },\n"
        "            Element::Void => Ingredient { element },\n"
        "            Element::Echo => Ingredient { element },\n"
        "            Element::Lumen => Ingredient { element },\n"
        "        }\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    repairs = _run_runtime_rust_repair(
        tmp_path,
        source_tool=RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
        artifact_quality_errors=[
            _rust_e0583_missing_module_error(
                module_name="element",
                path="src/models/mod.rs",
                line=1,
                declaration="pub mod element;",
                candidates='"src/models/element.rs"',
            )
        ],
        relative_paths=(
            "Cargo.toml",
            "src/main.rs",
            "src/models/mod.rs",
            "src/models/ingredient.rs",
        ),
        allowed_paths=(
            "Cargo.toml",
            "src/main.rs",
            "src/models/mod.rs",
            "src/models/ingredient.rs",
            "src/models/element.rs",
        ),
    )

    assert repairs, "Should create missing element.rs"
    element_rs = (models_dir / "element.rs").read_text(encoding="utf-8")
    _assert_comment_only_missing_module_stub(element_rs)


def test_rust_missing_module_runtime_refuses_existing_stub_without_contract(tmp_path: Path) -> None:
    """Existing module files are not rewritten with guessed symbols."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "alchemy"\n', encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    models_dir = src / "models"
    models_dir.mkdir()

    (src / "main.rs").write_text(
        "mod models;\nuse crate::models::element::Element;\nfn main() { let _e = Element::Fire; }\n",
        encoding="utf-8",
    )
    (models_dir / "mod.rs").write_text("pub mod element;\n", encoding="utf-8")

    (models_dir / "element.rs").write_text("// Auto-generated stub module: element\n", encoding="utf-8")

    repairs = _run_runtime_rust_repair(
        tmp_path,
        source_tool=RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
        artifact_quality_errors=[
            _rust_e0583_missing_module_error(
                module_name="element",
                path="src/models/mod.rs",
                line=1,
                declaration="pub mod element;",
                candidates='"src/models/element.rs"',
            )
        ],
        relative_paths=(
            "Cargo.toml",
            "src/main.rs",
            "src/models/mod.rs",
            "src/models/element.rs",
        ),
        allowed_paths=(
            "Cargo.toml",
            "src/main.rs",
            "src/models/mod.rs",
            "src/models/element.rs",
        ),
    )

    assert repairs == []
    element_rs = (models_dir / "element.rs").read_text(encoding="utf-8")
    assert element_rs == "// Auto-generated stub module: element\n"


def test_rust_missing_module_nested_grouped_import(tmp_path: Path) -> None:
    """Multiple missing module diagnostics create multiple topology stubs."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "alchemy"\n', encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    models_dir = src / "models"
    models_dir.mkdir()

    (src / "main.rs").write_text("mod models;\nfn main() {}\n", encoding="utf-8")
    (models_dir / "mod.rs").write_text(
        "pub mod element;\npub mod flavor;\n",
        encoding="utf-8",
    )

    (models_dir / "recipe.rs").write_text(
        "use crate::models::{element::Element, flavor::Flavor};\n"
        "pub struct Recipe { pub element: Element, pub flavor: Flavor }\n",
        encoding="utf-8",
    )
    (src / "main.rs").write_text(
        "mod models;\n"
        "use crate::models::recipe::Recipe;\n"
        "fn main() { let _ = Recipe { element: models::element::Element::Fire, flavor: models::flavor::Flavor::Sweet }; }\n",
        encoding="utf-8",
    )

    (models_dir / "mod.rs").write_text(
        "pub mod element;\npub mod flavor;\npub mod recipe;\n",
        encoding="utf-8",
    )

    repairs = _run_runtime_rust_repair(
        tmp_path,
        source_tool=RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
        artifact_quality_errors=[
            _rust_e0583_missing_module_error(
                module_name="element",
                path="src/models/mod.rs",
                line=1,
                declaration="pub mod element;",
                candidates='"src/models/element.rs" or "src/models/element/mod.rs"',
            ),
            _rust_e0583_missing_module_error(
                module_name="flavor",
                path="src/models/mod.rs",
                line=2,
                declaration="pub mod flavor;",
                candidates='"src/models/flavor.rs" or "src/models/flavor/mod.rs"',
            ),
        ],
        relative_paths=(
            "Cargo.toml",
            "src/main.rs",
            "src/models/mod.rs",
            "src/models/recipe.rs",
        ),
        allowed_paths=(
            "Cargo.toml",
            "src/main.rs",
            "src/models/mod.rs",
            "src/models/recipe.rs",
            "src/models/element.rs",
            "src/models/flavor.rs",
        ),
    )

    assert repairs
    element_rs = (models_dir / "element.rs").read_text(encoding="utf-8")
    flavor_rs = (models_dir / "flavor.rs").read_text(encoding="utf-8")
    _assert_comment_only_missing_module_stub(element_rs)
    _assert_comment_only_missing_module_stub(flavor_rs)
