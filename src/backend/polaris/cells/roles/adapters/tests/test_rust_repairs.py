from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from polaris.cells.roles.adapters.internal.director.deterministic_repairs.rust_repairs import (
    _apply_deterministic_rust_crate_import_repair,
    _apply_deterministic_rust_dependency_repair,
    _apply_deterministic_rust_lib_root_facade_repair,
    _apply_deterministic_rust_line_suggestion_repair,
    _apply_deterministic_rust_missing_lib_target_repair,
    _apply_deterministic_rust_trait_import_repair,
    _apply_deterministic_rust_unresolved_pub_use_repair,
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

    results = _apply_deterministic_rust_dependency_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="factory-quality-gate:test",
        artifact_quality_errors=["error[E0432]: unresolved import `serde`"],
    )

    cargo = (tmp_path / "Cargo.toml").read_text(encoding="utf-8")
    assert results
    assert 'serde = { version = "1.0", features = ["derive"] }' in cargo
    assert 'serde_json = "1.0"' in cargo


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
    assert repaired.count("use crate::engine::mapper::PaletteGenerator;") == 1
