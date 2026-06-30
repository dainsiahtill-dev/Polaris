from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from polaris.cells.roles.adapters.internal.director.deterministic_repairs.rust_repairs import (
    repair_rust_missing_module_files,
)
from polaris.cells.roles.adapters.internal.director.execution_tools import DirectorToolExecutor
from polaris.cells.roles.adapters.internal.director.runtime_repair_tool_adapter import (
    run_runtime_repair_with_director_tools,
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
    return run_runtime_repair_with_director_tools(
        SimpleNamespace(workspace=str(workspace), _execution=SimpleNamespace(_message_bus=None)),
        workspace_path=workspace,
        task_id="factory-quality-gate:test",
        source_tool=source_tool,
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=allowed_paths or relative_paths,
        use_editor=True,
    )


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


def test_rust_missing_module_generates_enum_from_variant_usage(tmp_path: Path) -> None:
    """L1-09 regression: missing element.rs should generate enum, not empty struct.

    When code uses Element::Stardust, Element::Fire etc., the repair must detect
    the enum usage pattern and generate a proper enum definition.
    """
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "stardust-alchemy"\n', encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    models_dir = src / "models"
    models_dir.mkdir()

    # main.rs declares mod models
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

    # models/mod.rs declares mod element and mod ingredient
    (models_dir / "mod.rs").write_text(
        "pub mod element;\n"
        "pub mod ingredient;\n",
        encoding="utf-8",
    )

    # element.rs is MISSING — this is the bug scenario
    # ingredient.rs imports Element and uses it
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

    repairs = repair_rust_missing_module_files(tmp_path)

    assert repairs, "Should create missing element.rs"
    element_rs = (models_dir / "element.rs").read_text(encoding="utf-8")
    # Must generate enum, NOT struct
    assert "pub enum Element" in element_rs, f"Expected enum, got:\n{element_rs}"
    assert "Crystal" in element_rs
    assert "Echo" in element_rs
    assert "Ember" in element_rs
    assert "Fire" in element_rs
    assert "Lumen" in element_rs
    assert "Stardust" in element_rs
    assert "Tide" in element_rs
    assert "Void" in element_rs
    # Must NOT generate empty struct
    assert "pub struct Element" not in element_rs


def test_rust_missing_module_updates_existing_stub(tmp_path: Path) -> None:
    """When element.rs exists but is just a comment stub, the repair should update it."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "alchemy"\n', encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    models_dir = src / "models"
    models_dir.mkdir()

    (src / "main.rs").write_text(
        "mod models;\n"
        "use crate::models::element::Element;\n"
        "fn main() { let _e = Element::Fire; }\n",
        encoding="utf-8",
    )
    (models_dir / "mod.rs").write_text("pub mod element;\n", encoding="utf-8")

    # element.rs exists but is just a stub comment
    (models_dir / "element.rs").write_text(
        "// Auto-generated stub module: element\n", encoding="utf-8"
    )

    repairs = repair_rust_missing_module_files(tmp_path)

    assert repairs, "Should update stub element.rs"
    element_rs = (models_dir / "element.rs").read_text(encoding="utf-8")
    assert "pub enum Element" in element_rs
    assert "Fire" in element_rs


def test_rust_missing_module_nested_grouped_import(tmp_path: Path) -> None:
    """Handle grouped imports with sub-paths: use crate::models::{element::Element, flavor::Flavor}"""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "alchemy"\n', encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    models_dir = src / "models"
    models_dir.mkdir()

    (src / "main.rs").write_text("mod models;\nfn main() {}\n", encoding="utf-8")
    (models_dir / "mod.rs").write_text(
        "pub mod element;\n"
        "pub mod flavor;\n",
        encoding="utf-8",
    )

    # recipe.rs uses nested grouped import
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

    # Also need mod recipe
    (models_dir / "mod.rs").write_text(
        "pub mod element;\n"
        "pub mod flavor;\n"
        "pub mod recipe;\n",
        encoding="utf-8",
    )

    repairs = repair_rust_missing_module_files(tmp_path)

    # element.rs and flavor.rs should be created
    assert repairs
    element_rs = (models_dir / "element.rs").read_text(encoding="utf-8")
    flavor_rs = (models_dir / "flavor.rs").read_text(encoding="utf-8")
    assert "pub enum Element" in element_rs
    assert "Fire" in element_rs
    assert "pub enum Flavor" in flavor_rs
    assert "Sweet" in flavor_rs
