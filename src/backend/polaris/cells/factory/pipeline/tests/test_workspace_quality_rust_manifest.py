"""Official rust quality must not skip cargo when sources exist without Cargo.toml.

Live L2-14: workspace had src/*.rs plus lowercase cargo.toml. The official
runner treated missing exact ``Cargo.toml`` as "not rust" and quality_gate
passed with only delivery_depth. Bench rust_compile then failed E0433 and
run-ledger/real-run gates saw no cargo receipt. Do not hand-edit generated
projects.
"""

from __future__ import annotations

from pathlib import Path

from polaris.cells.factory.pipeline.internal.factory_workspace_quality import WorkspaceQualityRunner
from polaris.cells.factory.pipeline.internal.native_validation_sandbox import (
    _ensure_sandbox_canonical_cargo_manifest,
    _resolve_cargo_manifest_path,
    _validate_cargo_project_contract,
)


def test_rust_quality_commands_empty_without_rust_sources(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("plain", encoding="utf-8")
    assert WorkspaceQualityRunner(tmp_path)._rust_workspace_quality_commands() == []


def test_rust_quality_commands_include_cargo_test_when_rs_exists_without_manifest(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")
    assert WorkspaceQualityRunner(tmp_path)._rust_workspace_quality_commands() == [["cargo", "test", "--quiet"]]


def test_rust_quality_commands_include_cargo_test_for_lowercase_cargo_toml(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (tmp_path / "cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    runner = WorkspaceQualityRunner(tmp_path)
    assert runner._rust_workspace_quality_commands() == [["cargo", "test", "--quiet"]]
    commands = runner.workspace_quality_commands({})
    assert ["cargo", "test", "--quiet"] in commands


def test_rust_quality_commands_include_cargo_test_when_canonical_manifest_exists(
    tmp_path: Path,
) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    assert WorkspaceQualityRunner(tmp_path)._rust_workspace_quality_commands() == [["cargo", "test", "--quiet"]]


def test_sandbox_contract_accepts_lowercase_cargo_toml_and_canonicalizes_copy(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")
    (tmp_path / "cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    resolved = _resolve_cargo_manifest_path(tmp_path)
    assert resolved is not None
    assert resolved.name == "cargo.toml"
    _validate_cargo_project_contract(tmp_path)
    _ensure_sandbox_canonical_cargo_manifest(tmp_path)
    assert (tmp_path / "Cargo.toml").is_file()
    assert (tmp_path / "Cargo.toml").read_text(encoding="utf-8") == (tmp_path / "cargo.toml").read_text(
        encoding="utf-8"
    )


def test_quality_plan_probe_includes_lowercase_cargo_and_lib_for_crate_rewrite(
    tmp_path: Path,
) -> None:
    """E0433 files alone are not enough; crate rewrite needs crate identity."""

    from polaris.cells.director.runtime.public import (
        QueryDirectorRepairPlanProbeV1,
        query_director_repair_plan_probe,
    )
    from polaris.cells.factory.pipeline.internal.factory_stage_executor._helpers import (
        resolve_workspace_quality_existing_file,
        workspace_quality_rust_plan_probe_companion_paths,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    cargo = '[package]\nname = "treasure_budget"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\npath = "src/lib.rs"\n'
    (tmp_path / "cargo.toml").write_text(cargo, encoding="utf-8")
    (tmp_path / "src" / "lib.rs").write_text("pub mod engine;\n", encoding="utf-8")
    main_rs = "use pirate_treasure_budgeter::engine::run;\nfn main() {}\n"
    product_rs = "use pirate_treasure_budgeter::engine::run;\n"
    (tmp_path / "src" / "main.rs").write_text(main_rs, encoding="utf-8")
    (tmp_path / "tests" / "product.rs").write_text(product_rs, encoding="utf-8")
    errors = [
        "error[E0433]: cannot find module or crate `pirate_treasure_budgeter` in this scope\n  --> src/main.rs:1:5\n",
        "error[E0433]: cannot find module or crate `pirate_treasure_budgeter` in this scope\n"
        "  --> tests/product.rs:1:5\n",
    ]

    assert resolve_workspace_quality_existing_file(tmp_path, "Cargo.toml") is not None
    assert resolve_workspace_quality_existing_file(tmp_path, "Cargo.toml").name == "cargo.toml"
    companions = workspace_quality_rust_plan_probe_companion_paths(
        tmp_path,
        artifact_quality_errors=errors,
    )
    assert "Cargo.toml" in companions
    assert "src/lib.rs" in companions

    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=tuple(errors),
            base_files={
                "cargo.toml": cargo,
                "src/lib.rs": "pub mod engine;\n",
                "src/main.rs": main_rs,
                "tests/product.rs": product_rs,
            },
        )
    )
    assert "deterministic_rust_crate_import_rewrite_repair" in probe.plannable_source_tools
    assert probe.status == "covered_plannable"


def test_crate_rewrite_expected_is_unique_use_line() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel import plan_runtime_repair

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_crate_import_rewrite_repair",
        base_files={
            "Cargo.toml": '[package]\nname = "treasure_budget"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\npath = "src/lib.rs"\n',
            "src/lib.rs": "pub mod engine;\n// pirate_treasure_budgeter::engine\n",
            "src/main.rs": "use pirate_treasure_budgeter::engine::run;\nfn main() {}\n",
            "tests/product.rs": "use pirate_treasure_budgeter::engine::run;\n",
        },
        artifact_quality_errors=(
            "error[E0433]: cannot find module or crate `pirate_treasure_budgeter` in this scope\n"
            "  --> src/main.rs:1:5\n",
        ),
        mode="shadow",
    )
    assert planning.plan is not None
    main_ops = [op for op in planning.plan.operations if op.path == "src/main.rs"]
    assert main_ops
    assert any("use pirate_treasure_budgeter::engine::run;" in str(op.expected) for op in main_ops)
    assert any("use treasure_budget::engine::run;" in str(op.replacement) for op in main_ops)
    assert all(op.expected != "pirate_treasure_budgeter" for op in planning.plan.operations)
    assert all(op.path != "src/lib.rs" for op in planning.plan.operations)


def test_quality_errors_filter_to_claimed_owner_targets() -> None:
    from polaris.cells.roles.adapters.internal.director.materialization_quality_callback_ports import (
        _filter_quality_errors_to_write_targets,
        _task_declared_write_targets,
    )

    task = {
        "target_files": ["src/engine/mod.rs", "src/main.rs"],
        "metadata": {"target_files": ["src/engine/mod.rs", "src/main.rs"]},
    }
    assert _task_declared_write_targets(task) == ("src/engine/mod.rs", "src/main.rs")
    scoped = _filter_quality_errors_to_write_targets(
        [
            "error[E0433]: crate `pirate_treasure_budgeter` --> src/main.rs:15:5",
            "error[E0433]: crate `pirate_treasure_budgeter` --> tests/product.rs:24:5",
        ],
        ("src/main.rs",),
    )
    assert scoped == ["error[E0433]: crate `pirate_treasure_budgeter` --> src/main.rs:15:5"]


def test_rust_planning_files_drop_other_owner_comment_paths() -> None:
    from polaris.cells.roles.adapters.internal.director.materialization_quality_callback_ports import (
        _scope_materialization_rust_planning_files,
    )

    scoped = _scope_materialization_rust_planning_files(
        {
            "Cargo.toml": '[package]\nname = "treasure_budget"\n',
            "src/lib.rs": "// `use pirate_treasure_budgeter::*;`\n",
            "src/main.rs": "use pirate_treasure_budgeter::engine::run;\n",
            "tests/product.rs": "//! pirate_treasure_budgeter::models\n",
        },
        allowed_paths=("src/main.rs", "Cargo.toml", "src/engine/mod.rs"),
    )
    assert set(scoped) == {"Cargo.toml", "src/lib.rs", "src/main.rs"}


def test_rust_planning_files_keep_enum_definitions_as_read_only_companions() -> None:
    from polaris.cells.roles.adapters.internal.director.materialization_quality_callback_ports import (
        _scope_materialization_rust_planning_files,
    )

    scoped = _scope_materialization_rust_planning_files(
        {
            "Cargo.toml": '[package]\nname = "treasure_budget"\n',
            "src/lib.rs": "pub mod models;\n",
            "src/engine/treasure_rules.rs": "match kind { TreasureKind::Silver => {} }\n",
            "src/models/treasure.rs": "pub enum TreasureKind {\n    Gold,\n    Jewels,\n    Artifact,\n}\n",
            "tests/product.rs": "mod models_mod;\n",
        },
        allowed_paths=("src/engine/treasure_rules.rs", "src/main.rs"),
    )
    assert "src/models/treasure.rs" in scoped
    assert "src/engine/treasure_rules.rs" in scoped
    assert "tests/product.rs" not in scoped


def test_hold_llm_when_crate_rewrite_is_still_plannable_without_mutation() -> None:
    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_impl import (
        _workspace_quality_hold_llm_for_plannable_deterministic,
    )

    probe = {"plannable_source_tools": ["deterministic_rust_crate_import_rewrite_repair"]}
    assert _workspace_quality_hold_llm_for_plannable_deterministic(probe, write_tool_evidence=False) is True
    assert _workspace_quality_hold_llm_for_plannable_deterministic(probe, write_tool_evidence=True) is True
    assert (
        _workspace_quality_hold_llm_for_plannable_deterministic(
            {"plannable_source_tools": ["deterministic_rust_lib_root_facade_repair"]},
            write_tool_evidence=True,
        )
        is True
    )
    assert (
        _workspace_quality_hold_llm_for_plannable_deterministic(
            {"plannable_source_tools": ["deterministic_rust_derive_repair"]},
            write_tool_evidence=False,
        )
        is False
    )
    assert (
        _workspace_quality_hold_llm_for_plannable_deterministic(
            {"plannable_source_tools": []},
            write_tool_evidence=False,
        )
        is False
    )
    assert (
        _workspace_quality_hold_llm_for_plannable_deterministic(
            {"plannable_source_tools": []},
            write_tool_evidence=True,
            residual_errors=["error[E0433]: cannot find `models` in `crate`\n  --> src/main.rs:1:12\n"],
        )
        is True
    )


def test_round_owner_paths_prefer_task_boundary_evidence() -> None:
    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_impl import (
        _workspace_quality_residuals_miss_mutated_paths,
        _workspace_quality_round_owner_paths,
    )

    summary = {
        "task_boundary_owner_evidence": {
            "owner_target_files": ["src/main.rs", "src/engine/mod.rs"],
        }
    }
    assert _workspace_quality_round_owner_paths(summary) == ["src/main.rs", "src/engine/mod.rs"]
    residual = [
        "error[E0433]: cannot find crate `x`\n  --> src/main.rs:19:5\n",
        "error[E0308]: bad args\n  --> tests/product.rs:65:5\n",
    ]
    mutated = [
        {
            "success": True,
            "tool": "edit_file",
            "result": {
                "file": "src/main.rs",
                "operation": "edit_file",
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
            },
        }
    ]
    assert (
        _workspace_quality_residuals_miss_mutated_paths(
            residual,
            mutated,
            owner_paths=_workspace_quality_round_owner_paths(summary),
        )
        is False
    )


def test_compiler_excerpt_keeps_early_rustc_help_instead_of_tail() -> None:
    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        compact_compiler_error_blocks,
    )

    prefix = "   Compiling treasure_budget v0.1.0\n" + ("note: checking\n" * 400)
    early = (
        "error[E0616]: field `name` of struct `port::Port` is private\n"
        "   --> src/engine/treasure_rules.rs:349:81\n"
        "    |\n"
        '349 |             format!("{}", port.name),\n'
        "    |                                 ^^^^ private field\n"
        "    |\n"
        "help: a method `name` also exists, call it with parentheses\n"
        "    |\n"
        '349 |             format!("{}", port.name()),\n'
        "    |                                       ++\n"
    )
    tail = (
        "error[E0599]: no method named `label` found for struct `treasure::Treasure`\n"
        "   --> src/engine/treasure_runner.rs:80:41\n"
        "    |\n"
        '80 |         format!("{}", self.treasure.label())\n'
        "error: could not compile `treasure_budget` (lib) due to 87 previous errors\n"
    )
    compact = compact_compiler_error_blocks(prefix + early + tail, limit=2_000)
    assert "help: a method `name` also exists" in compact
    assert "port.name()" in compact
    assert "could not compile" not in compact
