"""Materialization quality must rescan Cargo.toml and allow declared bin creates."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.materialization_quality_callback_ports import (
    _collect_materialization_rust_base_files,
    _declared_rust_binary_paths_from_cargo,
    _rust_missing_binary_paths_from_quality_issues,
)
from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _filter_missing_workspace_file_errors_to_task_write_scope,
    _materialization_quality_scan_paths_with_package_manifest,
)
from polaris.kernelone.quality import scan_workspace_artifact_quality


def test_materialization_scan_paths_include_cargo_toml_for_rust_sources(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text(
        "\n".join(
            [
                "[package]",
                'name = "demo"',
                'version = "0.1.0"',
                'edition = "2021"',
                "[[bin]]",
                'name = "demo"',
                'path = "src/main.rs"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    paths = _materialization_quality_scan_paths_with_package_manifest(
        workspace_full=str(tmp_path),
        affected_files=["src/lib.rs"],
    )
    assert "Cargo.toml" in paths
    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=paths)
    assert any("find bin" in error and "demo" in error for error in errors)


def test_declared_bin_paths_extend_allowed_paths_without_faking_base_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")
    cargo = "\n".join(
        [
            "[package]",
            'name = "demo"',
            'version = "0.1.0"',
            'edition = "2021"',
            "[[bin]]",
            'name = "demo"',
            'path = "src/main.rs"',
            "",
        ]
    )
    (tmp_path / "Cargo.toml").write_text(cargo, encoding="utf-8")

    base = _collect_materialization_rust_base_files(tmp_path)
    assert "src/main.rs" not in base
    declared = _declared_rust_binary_paths_from_cargo(base["Cargo.toml"])
    assert declared == ("src/main.rs",)
    allowed = tuple(dict.fromkeys((*base.keys(), *declared)))
    assert "src/main.rs" in allowed


def test_rust_task_retains_missing_bin_error_outside_literal_write_scope() -> None:
    err = "error: can't find bin `demo` at path `/tmp/ws/src/main.rs`"
    retained = _filter_missing_workspace_file_errors_to_task_write_scope(
        [err],
        task={"target_files": ["src/lib.rs", "src/models/flavor.rs"]},
        workspace_full="/tmp/ws",
        workspace_name="",
        issue_payloads=(
            {
                "code": "rust_missing_binary_entrypoint",
                "message": err,
                "path": "src/main.rs",
                "metadata": {"raw": err, "bin_path": "src/main.rs"},
            },
        ),
    )
    assert err in retained


def test_quality_issue_paths_extract_declared_bin() -> None:
    paths = _rust_missing_binary_paths_from_quality_issues(
        (
            {
                "code": "rust_missing_binary_entrypoint",
                "path": "src/main.rs",
                "metadata": {"bin_path": "src/main.rs"},
            },
        )
    )
    assert paths == ("src/main.rs",)
