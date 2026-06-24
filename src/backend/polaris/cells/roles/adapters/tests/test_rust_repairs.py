from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from polaris.cells.roles.adapters.internal.director.deterministic_repairs.rust_repairs import (
    _apply_deterministic_rust_crate_import_repair,
    _apply_deterministic_rust_dependency_repair,
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
