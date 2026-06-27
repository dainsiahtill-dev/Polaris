from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from polaris.cells.roles.adapters.internal.director.deterministic_repairs.rust_repairs import (
    _apply_deterministic_rust_crate_import_repair,
    _apply_deterministic_rust_derive_repair,
    _apply_deterministic_rust_lib_root_facade_repair,
    _apply_deterministic_rust_line_suggestion_repair,
    _apply_deterministic_rust_missing_lib_target_repair,
    _apply_deterministic_rust_trait_import_repair,
    _apply_deterministic_rust_unresolved_pub_use_repair,
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
) -> list[dict[str, object]]:
    base_files = {
        relative_path: (workspace / relative_path).read_text(encoding="utf-8")
        for relative_path in relative_paths
    }
    return run_runtime_repair_with_director_tools(
        SimpleNamespace(workspace=str(workspace), _execution=SimpleNamespace(_message_bus=None)),
        workspace_path=workspace,
        task_id="factory-quality-gate:test",
        source_tool=source_tool,
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=relative_paths,
        use_editor=False,
    )


def test_deterministic_rust_crate_import_repair_aligns_import_to_cargo_crate_name(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "kitchen-flavor-palette"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text(
        "use kitchen_palette::engine::generate_palette;\n"
        "fn main() {\n"
        "    let _ = kitchen_palette::models::Recipe::default();\n"
        "    generate_palette();\n"
        "}\n",
        encoding="utf-8",
    )

    results = _apply_deterministic_rust_crate_import_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=[
            "cargo check failed: error[E0433]: cannot find module or crate `kitchen_palette` in this scope"
        ],
    )

    repaired = (tmp_path / "src" / "main.rs").read_text(encoding="utf-8")
    assert results
    assert results[0]["result"]["source_tool"] == "deterministic_rust_crate_import_repair"
    assert "use kitchen_flavor_palette::engine::generate_palette;" in repaired
    assert "kitchen_flavor_palette::models::Recipe" in repaired
    assert "kitchen_palette::" not in repaired


def test_deterministic_rust_crate_import_repair_does_not_rewrite_declared_dependency(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "kitchen-flavor-palette"\n\n[dependencies]\nkitchen_palette = "1"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text(
        "use kitchen_palette::engine::generate_palette;\n",
        encoding="utf-8",
    )

    results = _apply_deterministic_rust_crate_import_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=[
            "cargo check failed: error[E0433]: cannot find module or crate `kitchen_palette` in this scope"
        ],
    )

    assert results == []
    assert (tmp_path / "src" / "main.rs").read_text(encoding="utf-8") == (
        "use kitchen_palette::engine::generate_palette;\n"
    )


def test_deterministic_rust_crate_import_repair_handles_unlinked_crate_wording(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "kitchen-flavor-palette"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text(
        "use kitchen_flavor_colorizer::engine::palette_engine::palette_for_flavor;\n",
        encoding="utf-8",
    )

    results = _apply_deterministic_rust_crate_import_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=[
            "error[E0433]: failed to resolve: use of unresolved module or unlinked crate `kitchen_flavor_colorizer`"
        ],
    )

    repaired = (tmp_path / "src" / "main.rs").read_text(encoding="utf-8")
    assert results
    assert "use kitchen_flavor_palette::engine::palette_engine::palette_for_flavor;" in repaired
    assert "kitchen_flavor_colorizer::" not in repaired


def test_deterministic_rust_crate_import_repair_rewrites_bin_to_local_lib_crate(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "flavor-palette-lab"\n\n[lib]\nname = "flavor_palette_lab"\npath = "src/lib.rs"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn generate_palette_and_plating() {}\n", encoding="utf-8")
    (tmp_path / "src" / "main.rs").write_text(
        "use kitchen_color_composer::generate_palette_and_plating;\nfn main() { generate_palette_and_plating(); }\n",
        encoding="utf-8",
    )

    results = _apply_deterministic_rust_crate_import_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=[
            "error[E0432]: unresolved import `kitchen_color_composer`\n"
            "use of unresolved module or unlinked crate `kitchen_color_composer`"
        ],
    )

    repaired = (tmp_path / "src" / "main.rs").read_text(encoding="utf-8")
    assert results
    assert "use flavor_palette_lab::generate_palette_and_plating;" in repaired
    assert "kitchen_color_composer::" not in repaired


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


def test_deterministic_rust_derive_repair_adds_serde_derives_to_local_type_file(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "kitchen-flavor-palette"\n', encoding="utf-8")
    models_dir = tmp_path / "src" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "flavor.rs").write_text(
        "#[derive(Debug, Clone, PartialEq)]\n"
        "pub enum FlavorDimension { Sweet }\n\n"
        "#[derive(Debug, Clone, PartialEq)]\n"
        "pub struct FlavorNote { pub dimension: FlavorDimension, pub weight: f32 }\n\n"
        "#[derive(Debug, Clone, Default, PartialEq)]\n"
        "pub struct FlavorProfile { pub notes: Vec<FlavorNote> }\n",
        encoding="utf-8",
    )
    (models_dir / "ingredient.rs").write_text(
        "use super::flavor::FlavorProfile;\n\n"
        "#[derive(Debug, Clone, PartialEq)]\n"
        "pub struct Ingredient { pub profile: FlavorProfile }\n",
        encoding="utf-8",
    )

    results = _apply_deterministic_rust_derive_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=[
            "help: the trait `Serialize` is not implemented for `ingredient::Ingredient`\n"
            " = note: for local types consider adding `#[derive(serde::Serialize)]` "
            "to your `ingredient::Ingredient` type",
            "help: the trait `Deserialize<'_>` is not implemented for `flavor::FlavorProfile`\n"
            " = note: for local types consider adding `#[derive(serde::Deserialize)]` "
            "to your `flavor::FlavorProfile` type",
        ],
    )

    flavor_rs = (models_dir / "flavor.rs").read_text(encoding="utf-8")
    ingredient_rs = (models_dir / "ingredient.rs").read_text(encoding="utf-8")
    assert results
    assert "serde::Deserialize" in flavor_rs
    assert "serde::Serialize" not in flavor_rs
    assert flavor_rs.count("serde::Deserialize") == 3
    assert "serde::Serialize" in ingredient_rs
    assert "serde::Deserialize" not in ingredient_rs


def test_deterministic_rust_derive_repair_removes_eq_from_float_field_types(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "kitchen-flavor-palette"\n', encoding="utf-8")
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "models" / "mod.rs").write_text(
        "#[derive(Debug, Clone, PartialEq, Eq)]\n"
        "pub enum DomainError {\n"
        "    InvalidFlavorWeight { note: String, weight: f32 },\n"
        "}\n",
        encoding="utf-8",
    )

    results = _apply_deterministic_rust_derive_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=["error[E0277]: the trait bound `f32: Eq` is not satisfied"],
    )

    repaired = (tmp_path / "src" / "models" / "mod.rs").read_text(encoding="utf-8")
    assert results
    assert "#[derive(Debug, Clone, PartialEq)]" in repaired
    assert "PartialEq, Eq" not in repaired


def test_deterministic_rust_lib_root_facade_repair_reconnects_existing_engine_api(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "flavor-palette-lab"\n\n[lib]\nname = "flavor_palette_lab"\npath = "src/lib.rs"\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "lib.rs").write_text(
        "pub struct Recipe;\npub enum Flavor { Sweet }\npub struct Ingredient;\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "mod.rs").write_text(
        "use crate::lib::Recipe;\npub fn generate_palette_and_plating(_recipe: &Recipe) {}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "main.rs").write_text(
        "use flavor_palette_lab::lib::{Flavor, Ingredient, Recipe};\n"
        "use flavor_palette_lab::generate_palette_and_plating;\n",
        encoding="utf-8",
    )

    results = _apply_deterministic_rust_lib_root_facade_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=["AssertionError: lib.rs must expose generate_palette_and_plating API"],
    )

    lib_rs = (tmp_path / "src" / "lib.rs").read_text(encoding="utf-8")
    engine_rs = (tmp_path / "src" / "engine" / "mod.rs").read_text(encoding="utf-8")
    main_rs = (tmp_path / "src" / "main.rs").read_text(encoding="utf-8")
    assert results
    assert "pub mod engine;" in lib_rs
    assert "pub use engine::generate_palette_and_plating;" in lib_rs
    assert "use crate::Recipe;" in engine_rs
    assert "use flavor_palette_lab::{Flavor, Ingredient, Recipe};" in main_rs
    assert "::lib::" not in engine_rs
    assert "::lib::" not in main_rs


def test_deterministic_rust_lib_root_facade_repair_replaces_inline_engine_stub(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "kitchen-flavor-palette"\n\n[lib]\nname = "kitchen_flavor_palette"\npath = "src/lib.rs"\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "lib.rs").write_text(
        "pub mod models;\npub mod engine {\n    pub struct _Placeholder;\n}\npub use models::recipe::Recipe;\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "mod.rs").write_text(
        "pub struct Recipe;\npub struct FlavorProfile;\npub fn generate_palette_and_plating(_recipe: &Recipe) {}\n",
        encoding="utf-8",
    )

    results = _apply_deterministic_rust_lib_root_facade_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=[
            "error[E0432]: unresolved imports `kitchen_flavor_palette::engine::generate_palette_and_plating`, "
            "`kitchen_flavor_palette::engine::FlavorProfile`, `kitchen_flavor_palette::engine::Recipe`\n"
            "no `Recipe` in `engine`"
        ],
    )

    lib_rs = (tmp_path / "src" / "lib.rs").read_text(encoding="utf-8")
    assert results
    assert "pub mod engine;" in lib_rs
    assert "pub mod engine {" not in lib_rs
    assert "_Placeholder" not in lib_rs


def test_deterministic_rust_lib_root_facade_repair_replaces_conflicting_root_exports(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "kitchen-flavor-palette"\n\n[lib]\nname = "kitchen_flavor_palette"\npath = "src/lib.rs"\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "lib.rs").write_text(
        "pub mod models;\npub mod engine;\n"
        "pub use models::palette::{Palette, PaletteColor};\n"
        "pub use models::ingredient::{Ingredient, IngredientKind};\n"
        "pub use models::recipe::{Recipe, RecipeStep};\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "mod.rs").write_text(
        "pub use mapper::{FlavorProfile, Palette};\n"
        "pub use plating::{Ingredient, PlatingRule, Recipe};\n"
        "pub fn generate_palette_and_plating(_recipe: &Recipe) {}\n"
        "mod mapper { pub struct FlavorProfile; pub struct Palette { pub sweet: u8 } }\n"
        "mod plating { pub struct Ingredient; pub struct PlatingRule; pub struct Recipe { pub name: String } }\n",
        encoding="utf-8",
    )

    results = _apply_deterministic_rust_lib_root_facade_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=[
            "error[E0432]: unresolved imports `kitchen_flavor_palette::generate_palette_and_plating`, "
            "`kitchen_flavor_palette::FlavorProfile`, `kitchen_flavor_palette::PlatingRule`\n"
            "error[E0609]: no field `sweet` on type `&kitchen_flavor_palette::Palette`\n"
            "error[E0609]: no field `name` on type `kitchen_flavor_palette::Recipe`\n"
            "error[E0560]: struct `kitchen_flavor_palette::Ingredient` has no field named `flavor`"
        ],
    )

    lib_rs = (tmp_path / "src" / "lib.rs").read_text(encoding="utf-8")
    assert results
    assert "pub use engine::generate_palette_and_plating;" in lib_rs
    assert "pub use engine::FlavorProfile;" in lib_rs
    assert "pub use engine::PlatingRule;" in lib_rs
    assert "pub use engine::Palette;" in lib_rs
    assert "pub use engine::Recipe;" in lib_rs
    assert "pub use engine::Ingredient;" in lib_rs
    assert "pub use models::palette::{Palette, PaletteColor};" not in lib_rs
    assert "pub use models::palette::{PaletteColor};" in lib_rs
    assert "pub use models::recipe::{Recipe, RecipeStep};" not in lib_rs
    assert "pub use models::recipe::{RecipeStep};" in lib_rs


def test_deterministic_rust_lib_root_facade_repair_expands_root_import_group_companions(
    tmp_path: Path,
) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "kitchen-flavor-palette"\n\n[lib]\nname = "kitchen_flavor_palette"\npath = "src/lib.rs"\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "lib.rs").write_text(
        "pub mod models;\npub mod engine;\n"
        "pub use models::ingredient::{Ingredient, IngredientKind};\n"
        "pub use models::recipe::{Recipe, RecipeStep};\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "mod.rs").write_text(
        "pub use plating::{Ingredient, Recipe};\n"
        "pub fn generate_palette_and_plating(_recipe: &Recipe) {}\n"
        "mod plating { pub struct Ingredient; pub struct Recipe; }\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "main.rs").write_text(
        "use kitchen_flavor_palette::{\n    generate_palette_and_plating, Ingredient, Recipe,\n};\n",
        encoding="utf-8",
    )

    results = _apply_deterministic_rust_lib_root_facade_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=[
            "error[E0432]: unresolved import `kitchen_flavor_palette::generate_palette_and_plating`\n"
            "no `generate_palette_and_plating` in the root"
        ],
    )

    lib_rs = (tmp_path / "src" / "lib.rs").read_text(encoding="utf-8")
    assert results
    assert "pub use engine::generate_palette_and_plating;" in lib_rs
    assert "pub use engine::Ingredient;" in lib_rs
    assert "pub use engine::Recipe;" in lib_rs
    assert "pub use models::ingredient::{Ingredient, IngredientKind};" not in lib_rs
    assert "pub use models::ingredient::{IngredientKind};" in lib_rs
    assert "pub use models::recipe::{Recipe, RecipeStep};" not in lib_rs
    assert "pub use models::recipe::{RecipeStep};" in lib_rs


def test_deterministic_rust_missing_lib_target_repair_creates_module_facade(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "kitchen-flavor-palette"\n\n[lib]\nname = "kitchen_flavor_palette"\npath = "src/lib.rs"\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "mod.rs").write_text("pub fn run() {}\n", encoding="utf-8")
    (tmp_path / "src" / "models" / "mod.rs").write_text("pub struct Recipe;\n", encoding="utf-8")
    (tmp_path / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")

    results = _apply_deterministic_rust_missing_lib_target_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=[
            "cargo check failed: error: can't find lib `kitchen_flavor_palette` at path `src/lib.rs`"
        ],
    )

    repaired = (tmp_path / "src" / "lib.rs").read_text(encoding="utf-8")
    assert results
    assert results[0]["result"]["source_tool"] == "deterministic_rust_missing_lib_target_repair"
    assert "pub mod engine;\n" in repaired
    assert "pub mod models;\n" in repaired
    assert "pub mod main;" not in repaired


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

    results = _apply_deterministic_rust_line_suggestion_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=[
            "error[E0599]: no method named `eq_ignore_ascii_case` found for reference `&Ingredient` in the current scope\n"
            "  --> src/engine/plating_rules.rs:7:20\n"
            "   |\n"
            "help: one of the expressions' fields has a method of the same name\n"
            "   |\n"
            "7 |         .any(|i| i.name.eq_ignore_ascii_case(needle))\n"
            "   |                    +++++\n"
        ],
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

    results = _apply_deterministic_rust_line_suggestion_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
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

    results = _apply_deterministic_rust_unresolved_pub_use_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=[
            "error[E0432]: unresolved import `engine::Engine`\nno `Engine` in `engine`",
            "error[E0432]: unresolved import `models::Palette`\nno `Palette` in `models`",
        ],
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

    results = _apply_deterministic_rust_trait_import_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=[
            "error[E0599]: no method named `generate` found for struct `WeightedPaletteGenerator` in the current scope\n"
            "  --> src/engine/mod.rs:9:24\n"
            "   |\n"
            "help: trait `PaletteGenerator` which provides `generate` is implemented but not in scope; perhaps you want to import it\n"
            "   |\n"
            "1  + use crate::engine::mapper::PaletteGenerator;\n"
            "   |\n"
        ],
    )

    repaired = (tmp_path / "src" / "engine" / "mod.rs").read_text(encoding="utf-8")
    assert results
    assert results[0]["result"]["source_tool"] == "deterministic_rust_trait_import_repair"
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
