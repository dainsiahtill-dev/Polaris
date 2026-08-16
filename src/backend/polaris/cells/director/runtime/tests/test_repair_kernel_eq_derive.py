"""Incompatible Eq derive must be removed, not re-added.

Live L2-14: ``#[derive(..., Eq)]`` on a struct with ``f64`` produced
``error[E0277]: the trait bound f64: Eq is not satisfied``. missing_trait_derive
only adds traits, so the plan was empty. Quality then treated materialization
writes as progress and never asked the LLM.
"""

from __future__ import annotations

from polaris.cells.director.runtime.internal.repair_kernel import normalize_artifact_quality_errors
from polaris.cells.director.runtime.internal.repair_kernel.rust_syntax import (
    build_rust_incompatible_copy_derive_plan,
)


def test_incompatible_eq_derive_removes_eq_from_budget_struct() -> None:
    relative_path = "src/models/budget.rs"
    content = "#[derive(Debug, Clone, PartialEq, Eq)]\npub struct Budget {\n    pub coins: f64,\n}\n"
    raw = (
        "error[E0277]: the trait bound `f64: Eq` is not satisfied\n"
        "  --> src/models/budget.rs:3:16\n"
        "   |\n"
        "1  | #[derive(Debug, Clone, PartialEq, Eq)]\n"
        "   |                                  -- in this derive macro expansion\n"
        "2  | pub struct Budget {\n"
        "3  |     pub coins: f64,\n"
        "   |                ^^^ the trait `Eq` is not implemented for `f64`\n"
    )
    plan = build_rust_incompatible_copy_derive_plan(
        base_files={relative_path: content},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )
    assert plan is not None
    assert plan.operations
    assert plan.operations[0].path == relative_path
    assert ", Eq" in plan.operations[0].expected
    assert ", Eq" not in plan.operations[0].replacement
    assert "PartialEq" in plan.operations[0].replacement


def test_incompatible_eq_derive_unique_when_two_identical_derive_lines() -> None:
    relative_path = "src/engine/treasure_runner.rs"
    content = (
        "#[derive(Debug, Clone, PartialEq, Eq)]\n"
        "pub struct Scenario {\n"
        "    pub treasure: Treasure,\n"
        "}\n"
        "#[derive(Debug, Clone, PartialEq, Eq)]\n"
        "pub struct ScenarioVerdict {\n"
        "    pub ok: bool,\n"
        "}\n"
    )
    raw = (
        "error[E0277]: the trait bound `treasure::Treasure: Eq` is not satisfied\n"
        "   --> src/engine/treasure_runner.rs:3:5\n"
        "    |\n"
        "1  | #[derive(Debug, Clone, PartialEq, Eq)]\n"
        "   |                                  -- in this derive macro expansion\n"
        "2  | pub struct Scenario {\n"
        "3  |     pub treasure: Treasure,\n"
        "   |     ^^^^^^^^^^^^^^^^^^^^^^ unsatisfied trait bound\n"
        "    |\n"
        "help: the trait `Eq` is not implemented for `treasure::Treasure`\n"
        "   --> src/models/treasure.rs:102:1\n"
        "    |\n"
        "102 | pub struct Treasure {\n"
    )
    plan = build_rust_incompatible_copy_derive_plan(
        base_files={
            relative_path: content,
            "src/models/treasure.rs": "#[derive(Debug, Clone, Copy, PartialEq)]\npub struct Treasure {\n    pub value: f64,\n}\n",
        },
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )
    assert plan is not None
    assert plan.operations
    assert {op.path for op in plan.operations} == {relative_path}
    assert any("pub struct Scenario" in str(op.expected) for op in plan.operations)
    assert any(", Eq" not in str(op.replacement).split("pub struct Scenario", 1)[0] for op in plan.operations)


def test_missing_trait_derive_skips_eq_when_consumer_derive_expansion() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel.rust_syntax import (
        build_rust_missing_trait_derive_plan,
    )

    raw = (
        "error[E0277]: the trait bound `treasure::Treasure: Eq` is not satisfied\n"
        "   --> src/engine/treasure_runner.rs:3:5\n"
        "    |\n"
        "1  | #[derive(Debug, Clone, PartialEq, Eq)]\n"
        "   |                                  -- in this derive macro expansion\n"
        "help: the trait `Eq` is not implemented for `treasure::Treasure`\n"
        "   --> src/models/treasure.rs:2:1\n"
    )
    plan = build_rust_missing_trait_derive_plan(
        base_files={
            "src/engine/treasure_runner.rs": (
                "#[derive(Debug, Clone, PartialEq, Eq)]\npub struct Scenario {\n    pub treasure: Treasure,\n}\n"
            ),
            "src/models/treasure.rs": "#[derive(Debug, Clone, Copy, PartialEq)]\npub struct Treasure {\n    pub value: f64,\n}\n",
        },
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )
    assert plan is None


def test_incompatible_eq_derive_is_coverage_matched_for_materialization_probe() -> None:
    from polaris.cells.director.runtime.public import (
        QueryDirectorRepairCoverageV1,
        query_director_repair_coverage,
    )

    raw = "error[E0277]: the trait bound `f64: Eq` is not satisfied\n  --> src/models/budget.rs:15:20\n"
    payload = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(raw,))).to_dict()
    tools = payload["items"][0]["matched_source_tools"]
    assert "deterministic_rust_incompatible_copy_derive_repair" in tools


def test_crate_import_rewrite_covers_e0463_cant_find_crate() -> None:
    from polaris.cells.director.runtime.public import (
        QueryDirectorRepairCoverageV1,
        query_director_repair_coverage,
    )

    raw = (
        "error[E0463]: can't find crate for `pirate_treasure_budgeter`\n"
        "  --> tests/product.rs:22:1\n"
        "   |\n"
        "22 | extern crate pirate_treasure_budgeter;\n"
    )
    payload = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(raw,))).to_dict()
    tools = payload["items"][0]["matched_source_tools"]
    assert "deterministic_rust_crate_import_rewrite_repair" in tools


def test_crate_import_rewrite_plans_e0463_to_canonical_cargo_name() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel import plan_runtime_repair

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_crate_import_rewrite_repair",
        base_files={
            "Cargo.toml": '[package]\nname = "treasure_budget"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\npath = "src/lib.rs"\n',
            "src/lib.rs": "pub mod engine;\n",
            "src/main.rs": "use pirate_treasure_budgeter::engine::run;\nfn main() {}\n",
            "tests/product.rs": "extern crate pirate_treasure_budgeter;\n",
        },
        artifact_quality_errors=(
            "error[E0463]: can't find crate for `pirate_treasure_budgeter`\n  --> tests/product.rs:1:1\n",
        ),
        mode="shadow",
    )
    assert planning.plan is not None
    product_ops = [op for op in planning.plan.operations if op.path == "tests/product.rs"]
    assert product_ops
    assert any("treasure_budget" in str(op.replacement) for op in product_ops)
    assert any("pirate_treasure_budgeter" in str(op.expected) for op in product_ops)


def test_crate_import_rewrite_skips_comment_only_wrong_crate_tokens() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel import plan_runtime_repair

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_crate_import_rewrite_repair",
        base_files={
            "Cargo.toml": (
                '[package]\nname = "treasure_budget"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\npath = "src/lib.rs"\n'
            ),
            "src/lib.rs": "// `use pirate_treasure_budgeter::*;` and reach every primary entity plus\n",
            "src/main.rs": "use pirate_treasure_budgeter::models::prelude::{Budget};\nfn main() {}\n",
            "tests/product.rs": "//! pirate_treasure_budgeter::models::prelude is the only path that compiles\n",
        },
        artifact_quality_errors=(
            "error[E0433]: cannot find module or crate `pirate_treasure_budgeter` in this scope\n"
            "  --> src/main.rs:1:5\n",
        ),
        mode="shadow",
    )
    assert planning.plan is not None
    paths = {op.path for op in planning.plan.operations}
    assert paths == {"src/main.rs"}


def test_crate_import_rewrite_owner_scoped_files_cover_allowed_paths() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel import plan_runtime_repair

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_crate_import_rewrite_repair",
        base_files={
            "Cargo.toml": (
                '[package]\nname = "treasure_budget"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\npath = "src/lib.rs"\n'
            ),
            "src/lib.rs": "pub mod models;\npub mod engine;\n",
            "src/main.rs": (
                "use pirate_treasure_budgeter::models::prelude::{Budget};\n"
                "use pirate_treasure_budgeter::engine::{run_domain_rules};\n"
                "fn main() {}\n"
            ),
        },
        artifact_quality_errors=(
            "error[E0433]: cannot find module or crate `pirate_treasure_budgeter` in this scope\n"
            "  --> src/main.rs:1:5\n",
        ),
        mode="shadow",
    )
    assert planning.plan is not None
    assert {op.path for op in planning.plan.operations} == {"src/main.rs"}


def test_crate_import_rewrite_rewrites_bin_crate_root_to_lib_package() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel import plan_runtime_repair

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_crate_import_rewrite_repair",
        base_files={
            "Cargo.toml": (
                '[package]\nname = "treasure_budget"\nversion = "0.1.0"\nedition = "2021"\n\n[lib]\npath = "src/lib.rs"\n'
            ),
            "src/lib.rs": "pub mod models;\npub mod engine;\n",
            "src/main.rs": "use crate::models::prelude::{Budget};\nuse crate::engine::{run_domain_rules};\nfn main() {}\n",
        },
        artifact_quality_errors=(
            "error[E0433]: cannot find `models` in `crate`\n  --> src/main.rs:1:12\n",
            "error[E0432]: unresolved import `crate::engine`\n  --> src/main.rs:2:12\n",
        ),
        mode="shadow",
    )
    assert planning.plan is not None
    assert {op.path for op in planning.plan.operations} == {"src/main.rs"}
    expected = " ".join(str(op.expected) for op in planning.plan.operations)
    replacement = " ".join(str(op.replacement) for op in planning.plan.operations)
    assert "crate::models" in expected
    assert "crate::engine" in expected
    assert "treasure_budget::models" in replacement
    assert "treasure_budget::engine" in replacement


def test_lib_root_facade_replaces_empty_inline_engine_shadowing_disk_module() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel import plan_runtime_repair

    lib_rs = (
        "pub mod models;\npub mod engine {\n    //! reserved empty engine namespace\n}\npub use models::prelude::*;\n"
    )
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_lib_root_facade_repair",
        base_files={
            "Cargo.toml": '[package]\nname = "treasure_budget"\nversion = "0.1.0"\nedition = "2021"\n',
            "src/lib.rs": lib_rs,
            "src/engine/mod.rs": "pub mod treasure_rules;\npub use treasure_rules::*;\n",
            "src/main.rs": "use treasure_budget::engine::treasure_rules;\nfn main() {}\n",
        },
        artifact_quality_errors=(
            "error[E0432]: unresolved import `treasure_budget::engine::treasure_rules`\n  --> src/main.rs:1:5\n",
        ),
        mode="shadow",
    )
    assert planning.plan is not None
    assert planning.plan.operations[0].path == "src/lib.rs"
    assert planning.plan.operations[0].replacement == "pub mod engine;"
    assert "pub mod engine {" in str(planning.plan.operations[0].expected)


def test_line_suggestion_applies_private_field_method_parentheses() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel import normalize_artifact_quality_errors
    from polaris.cells.director.runtime.internal.repair_kernel.rust_syntax import (
        build_rust_line_suggestion_plan,
    )

    plan = build_rust_line_suggestion_plan(
        base_files={
            "src/engine/treasure_rules.rs": (
                "fn apply(reef: &Reef) {\n"
                "    let (message, approved) = match reef.hazard {\n"
                '        ReefHazard::Calm => ("ok", true),\n'
                "    };\n"
                "}\n"
            )
        },
        diagnostics=normalize_artifact_quality_errors(
            [
                "error[E0616]: field `hazard` of struct `reef::Reef` is private\n"
                "   --> src/engine/treasure_rules.rs:2:42\n"
                "    |\n"
                "2   |     let (message, approved) = match reef.hazard {\n"
                "    |                                          ^^^^^^ private field\n"
                "    |\n"
                "help: a method `hazard` also exists, call it with parentheses\n"
                "    |\n"
                "2   |     let (message, approved) = match reef.hazard() {\n"
                "    |                                                ++\n"
            ]
        ),
        mode="shadow",
    )
    assert plan is not None
    assert any("reef.hazard()" in str(op.replacement) for op in plan.operations)


def test_line_suggestion_collapses_same_file_ops_so_composer_accepts() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel.rust_runtime._plan import (
        plan_rust_line_suggestion_repair,
    )

    content = (
        "fn evaluate(treasure: &Treasure, port: &Port) {\n"
        "    match treasure.kind {\n"
        "        TreasureKind::Gold => {}\n"
        "    }\n"
        "    match port.kind {\n"
        "        PortKind::Harbor => {}\n"
        "    }\n"
        "}\n"
    )
    residuals = (
        "error[E0616]: field `kind` of struct `treasure::Treasure` is private\n"
        "   --> src/engine/treasure_rules.rs:2:21\n"
        "    |\n"
        "2   |     match treasure.kind {\n"
        "    |                     ^^^^ private field\n"
        "    |\n"
        "help: a method `kind` also exists, call it with parentheses\n"
        "    |\n"
        "2   |     match treasure.kind() {\n"
        "    |                        ++\n",
        "error[E0616]: field `kind` of struct `port::Port` is private\n"
        "   --> src/engine/treasure_rules.rs:5:17\n"
        "    |\n"
        "5   |     match port.kind {\n"
        "    |                 ^^^^ private field\n"
        "    |\n"
        "help: a method `kind` also exists, call it with parentheses\n"
        "    |\n"
        "5   |     match port.kind() {\n"
        "    |                    ++\n",
    )
    planning = plan_rust_line_suggestion_repair(
        base_files={"src/engine/treasure_rules.rs": content},
        artifact_quality_errors=residuals,
        mode="shadow",
    )
    assert planning.plan is not None
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert len(planning.plan.operations) == 1
    replacement = str(planning.plan.operations[0].replacement)
    assert "treasure.kind()" in replacement
    assert "port.kind()" in replacement


def test_unknown_enum_variant_rewrites_similar_and_wildcard_consumer_arms() -> None:
    from polaris.cells.director.runtime.internal.repair_kernel.rust_syntax._helpers import (
        rust_local_structure_operations,
    )

    models = "pub enum TreasureKind {\n    Gold,\n    Jewels,\n    Artifact,\n}\n"
    consumer = (
        "fn apply(kind: TreasureKind) {\n"
        "    match kind {\n"
        "        TreasureKind::Gold | TreasureKind::Silver => {}\n"
        "        TreasureKind::Relic => {}\n"
        "    }\n"
        "}\n"
        "fn reef(h: ReefHazard) {\n"
        "    match h {\n"
        "        ReefHazard::Shoal => {}\n"
        "        ReefHazard::Shallow => {}\n"
        "    }\n"
        "}\n"
    )
    reef = "pub enum ReefHazard {\n    Calm,\n    Shallow,\n    Treacherous,\n}\n"
    ops = rust_local_structure_operations(
        base_files={
            "src/models/treasure.rs": models,
            "src/models/reef.rs": reef,
            "src/engine/treasure_rules.rs": consumer,
        },
        diagnostics=normalize_artifact_quality_errors(
            [
                "error[E0599]: no variant, associated function, or constant named `Silver` found for enum `TreasureKind`\n  --> src/engine/treasure_rules.rs:3:40\n",
                "error[E0599]: no variant, associated function, or constant named `Relic` found for enum `TreasureKind`\n  --> src/engine/treasure_rules.rs:4:22\n",
                "error[E0599]: no variant, associated function, or constant named `Shoal` found for enum `ReefHazard`\n  --> src/engine/treasure_rules.rs:9:21\n",
            ]
        ),
    )
    assert ops
    repaired = next(str(op.replacement) for op in ops if op.path == "src/engine/treasure_rules.rs")
    assert "TreasureKind::Silver" not in repaired
    assert "TreasureKind::Gold =>" in repaired or "TreasureKind::Gold |" in repaired
    assert "ReefHazard::Shoal" not in repaired
    assert "ReefHazard::Shallow" in repaired
