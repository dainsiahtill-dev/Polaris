from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from polaris.cells.roles.adapters.internal.director.deterministic_repairs.rust_repairs import (
    _apply_deterministic_rust_crate_import_repair,
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
