"""Runtime and shadow tests for Rust lib target and root facade repairs."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.director.runtime.internal.repair_kernel.diagnostics import normalize_artifact_quality_errors
from polaris.cells.director.runtime.internal.repair_kernel.runtime_dispatch import (
    plan_runtime_repair,
    run_runtime_repair,
    runtime_repair_source_tools,
)
from polaris.cells.director.runtime.internal.repair_kernel.rust_export_facade import (
    RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
    RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
    RUST_MISSING_LIB_TARGET_STUB,
    classify_rust_export_facade_shadow,
    classify_rust_lib_root_facade_shadow,
    classify_rust_missing_lib_target_shadow,
)
from polaris.cells.director.runtime.public.contracts import PlanDirectorRepairCommandV1
from polaris.cells.director.runtime.public.service import plan_director_repair


def _diagnostics(*errors: str):
    return normalize_artifact_quality_errors(list(errors))


def test_lib_root_facade_path_rewrite_is_executable_and_rust_post_repair_remains_unsupported() -> None:
    runtime_tools = runtime_repair_source_tools()

    assert RUST_MISSING_LIB_TARGET_SOURCE_TOOL in runtime_tools
    assert RUST_LIB_ROOT_FACADE_SOURCE_TOOL in runtime_tools
    assert "deterministic_rust_post_repair" not in runtime_tools

    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_rust_post_repair",
            artifact_quality_errors=("Cargo manifest [lib].path src/lib.rs is missing for Rust library target",),
        )
    )

    assert result.ok is False
    assert result.planned is False
    assert result.error_code == "unsupported_repair_source_tool"
    assert result.plan_summary is None


def test_missing_lib_target_shadow_identifies_explicit_paths_without_runtime_plan() -> None:
    rustc_base_files = {
        "Cargo.toml": '[package]\nname = "palette-kit"\nversion = "0.1.0"\nedition = "2021"\n',
        "src/main.rs": "fn main() {}\n",
    }
    rustc_shadow = classify_rust_missing_lib_target_shadow(
        base_files=rustc_base_files,
        diagnostics=_diagnostics(
            "error: can't find library `palette_kit` at path `src/lib.rs`\n"
            "  |\n"
            "  = note: the configured library target file does not exist\n"
        ),
    )

    manifest_base_files = {
        "Cargo.toml": (
            '[package]\nname = "palette-kit"\nversion = "0.1.0"\nedition = "2021"\n\n'
            '[lib]\npath = "src/custom_lib.rs"\n'
        ),
    }
    manifest_shadow = classify_rust_missing_lib_target_shadow(
        base_files=manifest_base_files,
        diagnostics=_diagnostics("Cargo manifest [lib].path src/custom_lib.rs is missing for Rust library target"),
    )

    candidates = (*rustc_shadow.candidates, *manifest_shadow.candidates)
    assert rustc_shadow.blockers == ()
    assert manifest_shadow.blockers == ()
    assert {candidate.target_path for candidate in candidates} == {"src/lib.rs", "src/custom_lib.rs"}
    assert all(candidate.rule_id == "rust.missing_lib_target" for candidate in candidates)
    assert all(candidate.source_tool == RUST_MISSING_LIB_TARGET_SOURCE_TOOL for candidate in candidates)
    by_path = {candidate.target_path: candidate for candidate in candidates}
    assert by_path["src/lib.rs"].metadata["runtime_plan_available"] is True
    assert by_path["src/custom_lib.rs"].metadata["runtime_plan_available"] is False
    assert rustc_shadow.runtime_plan_available is True
    assert manifest_shadow.runtime_plan_available is False
    assert rustc_shadow.executable is False


def test_missing_lib_target_runtime_plans_and_runs_src_lib_subset(tmp_path: Path) -> None:
    raw_error = (
        "error: can't find library `palette_kit` at path `src/lib.rs`\n"
        "  |\n"
        "  = note: the configured library target file does not exist\n"
    )
    base_files = {
        "Cargo.toml": '[package]\nname = "palette-kit"\nversion = "0.1.0"\nedition = "2021"\n',
        "src/main.rs": "fn main() {}\n",
    }
    planning = plan_runtime_repair(
        source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=(raw_error,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.error_code is None
    assert planning.plan.rule_id == "rust.missing_lib_target_src_lib"
    assert planning.plan.operations[0].kind == "write_file"
    assert planning.plan.operations[0].path == "src/lib.rs"
    assert planning.plan.operations[0].content == RUST_MISSING_LIB_TARGET_STUB
    assert planning.plan.operations[0].metadata["runtime_plan_scope"] == "src_lib_rs_missing_file_only"
    assert planning.composition is not None
    assert planning.composition.ok is True

    writes: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append((path, content))
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path}

    run = run_runtime_repair(
        source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
        workspace=tmp_path,
        base_files=base_files,
        artifact_quality_errors=(raw_error,),
        writer=writer,
    )

    assert run.ok is True
    assert writes == [("src/lib.rs", RUST_MISSING_LIB_TARGET_STUB)]
    assert (tmp_path / "src/lib.rs").read_text(encoding="utf-8") == RUST_MISSING_LIB_TARGET_STUB
    assert run.execution_result is not None
    receipt = run.execution_result.receipt
    assert receipt.status == "applied"
    assert receipt.metadata["execution_records"][0]["created_file"] is True


def test_missing_lib_target_runtime_fails_closed_for_custom_path_and_mixed_candidates(tmp_path: Path) -> None:
    base_files = {
        "Cargo.toml": (
            '[package]\nname = "palette-kit"\nversion = "0.1.0"\nedition = "2021"\n\n'
            '[lib]\npath = "src/custom_lib.rs"\n'
        ),
    }
    custom_error = "Cargo manifest [lib].path src/custom_lib.rs is missing for Rust library target"
    default_error = (
        "error: can't find library `palette_kit` at path `src/lib.rs`\n"
        "  |\n"
        "  = note: the configured library target file does not exist\n"
    )
    writes: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append((path, content))
        raise AssertionError("unsafe missing lib target cases must not write")

    custom_planning = plan_runtime_repair(
        source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=(custom_error,),
        mode="shadow",
    )
    mixed_planning = plan_runtime_repair(
        source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
        base_files={
            "Cargo.toml": '[package]\nname = "palette-kit"\nversion = "0.1.0"\nedition = "2021"\n',
        },
        artifact_quality_errors=(default_error, custom_error),
        mode="shadow",
    )
    run = run_runtime_repair(
        source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
        workspace=tmp_path,
        base_files=base_files,
        artifact_quality_errors=(custom_error,),
        writer=writer,
    )

    assert custom_planning.plan is None
    assert custom_planning.composition is None
    assert mixed_planning.plan is None
    assert mixed_planning.composition is None
    assert run.ok is False
    assert run.error_code == "repair_not_planned"
    assert writes == []


def test_export_facade_shadow_emits_export_and_span_rewrite_candidates() -> None:
    base_files = {
        "Cargo.toml": '[package]\nname = "palette-kit"\nversion = "0.1.0"\nedition = "2021"\n',
        "src/lib.rs": "mod engine;\n",
        "src/engine.rs": "pub struct Palette;\npub fn generate_palette() -> Palette { Palette }\n",
        "src/main.rs": "fn demo() {\n    let _value: crate::lib::engine::Palette;\n}\n",
    }
    shadow = classify_rust_export_facade_shadow(
        base_files=base_files,
        diagnostics=_diagnostics(
            "error[E0432]: unresolved import `palette_kit::generate_palette`\n"
            " --> tests/smoke.rs:1:5\n"
            "  |\n"
            "1 | use palette_kit::generate_palette;\n"
            "  |     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ no `generate_palette` in the root\n",
            "Rust lib-root path rewrite required: replace `crate::lib::engine::Palette` with "
            "`crate::engine::Palette` before publishing the root facade",
        ),
    )

    by_kind = {candidate.candidate_kind: candidate for candidate in shadow.candidates}
    assert shadow.blockers == ()
    assert set(by_kind) == {"lib_root_export", "path_rewrite"}

    export = by_kind["lib_root_export"]
    assert export.rule_id == "rust.lib_root_facade_export"
    assert export.source_tool == RUST_LIB_ROOT_FACADE_SOURCE_TOOL
    assert export.target_path == "src/lib.rs"
    assert export.source_path == "src/engine.rs"
    assert export.symbol == "generate_palette"
    assert export.module_path == "engine"
    assert export.metadata["candidate_export_line"] == "pub use crate::engine::generate_palette;"
    assert export.metadata["writes_allowed"] is False

    rewrite = by_kind["path_rewrite"]
    assert rewrite.rule_id == "rust.lib_root_facade_path_rewrite"
    assert rewrite.source_path == "src/main.rs"
    assert rewrite.expected == "crate::lib::engine::Palette"
    assert rewrite.replacement == "crate::engine::Palette"
    assert rewrite.span_start is not None
    assert rewrite.span_end is not None
    assert rewrite.metadata["runtime_plan_available"] is True
    assert shadow.runtime_plan_available is True
    assert shadow.executable is False


@pytest.mark.parametrize(
    ("expected", "replacement"),
    (
        ("crate::lib::engine::Palette", "crate::engine::Palette"),
        ("palette_kit::lib::engine::Palette", "palette_kit::engine::Palette"),
    ),
)
def test_lib_root_facade_path_rewrite_runtime_plans_span_replace(
    expected: str,
    replacement: str,
) -> None:
    raw_error = (
        f"Rust lib-root path rewrite required: replace `{expected}` with "
        f"`{replacement}` before publishing the root facade"
    )
    base_files = {
        "Cargo.toml": '[package]\nname = "palette-kit"\nversion = "0.1.0"\nedition = "2021"\n',
        "src/main.rs": f"fn demo() {{\n    let _value: {expected};\n}}\n",
    }

    planning = plan_runtime_repair(
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=(raw_error,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.error_code is None
    assert planning.plan.rule_id == "rust.lib_root_facade_path_rewrite"
    assert planning.plan.operations[0].kind == "text_replace"
    assert planning.plan.operations[0].path == "src/main.rs"
    assert planning.plan.operations[0].expected == expected
    assert planning.plan.operations[0].replacement == replacement
    assert planning.plan.operations[0].metadata["write_file_fallback_allowed"] is False
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_lib_root_facade_path_rewrite_runtime_runs_with_editor_only(tmp_path: Path) -> None:
    expected = "crate::lib::engine::Palette"
    replacement = "crate::engine::Palette"
    raw_error = (
        f"Rust lib-root path rewrite required: replace `{expected}` with "
        f"`{replacement}` before publishing the root facade"
    )
    source = f"fn demo() {{\n    let _value: {expected};\n}}\n"
    base_files = {
        "Cargo.toml": '[package]\nname = "palette-kit"\nversion = "0.1.0"\nedition = "2021"\n',
        "src/main.rs": source,
    }
    target = tmp_path / "src/main.rs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    writes: list[tuple[str, str]] = []
    edits: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append((path, content))
        raise AssertionError("lib-root path rewrite must not use write_file fallback")

    def editor(operation) -> dict[str, object]:
        edits.append(operation.operation_id)
        path = tmp_path / operation.path
        content = path.read_text(encoding="utf-8")
        start = int(operation.span_start)
        end = int(operation.span_end)
        assert content[start:end] == operation.expected
        path.write_text(content[:start] + str(operation.replacement) + content[end:], encoding="utf-8")
        return {"ok": True, "path": operation.path}

    run = run_runtime_repair(
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        workspace=tmp_path,
        base_files=base_files,
        artifact_quality_errors=(raw_error,),
        writer=writer,
        editor=editor,
    )

    assert run.ok is True
    assert writes == []
    assert len(edits) == 1
    assert target.read_text(encoding="utf-8") == source.replace(expected, replacement)
    assert run.execution_result is not None
    receipt = run.execution_result.receipt
    assert receipt.status == "applied"
    record = receipt.metadata["execution_records"][0]
    assert record["operation"] == "edit_file"
    assert record["span_based"] is True
    assert record["unique_context_checked"] is True


def test_lib_root_facade_export_runtime_runs_with_editor_only(tmp_path: Path) -> None:
    raw_error = (
        "error[E0432]: unresolved import `palette_kit::generate_palette`\n"
        " --> tests/smoke.rs:1:5\n"
        "  |\n"
        "1 | use palette_kit::generate_palette;\n"
        "  |     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ no `generate_palette` in the root\n"
    )
    base_files = {
        "Cargo.toml": '[package]\nname = "palette-kit"\nversion = "0.1.0"\nedition = "2021"\n',
        "src/lib.rs": "mod engine;\n",
        "src/engine.rs": "pub fn generate_palette() {}\n",
    }
    target = tmp_path / "src/lib.rs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(base_files["src/lib.rs"], encoding="utf-8")
    writes: list[tuple[str, str]] = []
    edits: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append((path, content))
        raise AssertionError("lib-root export insert must not use write_file fallback")

    def editor(operation) -> dict[str, object]:
        edits.append(operation.operation_id)
        path = tmp_path / operation.path
        content = path.read_text(encoding="utf-8")
        start = int(operation.span_start)
        end = int(operation.span_end)
        assert content[start:end] == operation.expected
        path.write_text(content[:start] + str(operation.replacement) + content[end:], encoding="utf-8")
        return {"ok": True, "path": operation.path}

    planning = plan_runtime_repair(
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=(raw_error,),
        mode="shadow",
    )
    run = run_runtime_repair(
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        workspace=tmp_path,
        base_files=base_files,
        artifact_quality_errors=(raw_error,),
        writer=writer,
        editor=editor,
    )

    assert planning.plan is not None
    assert planning.plan.rule_id == "rust.lib_root_facade_export"
    operation = planning.plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == "src/lib.rs"
    assert operation.expected == ""
    assert operation.replacement == "pub use crate::engine::generate_palette;\n"
    assert operation.metadata["edit_strategy"] == "span_text_insert"
    assert operation.metadata["write_file_fallback_allowed"] is False
    assert operation.metadata["unique_context"] == "mod engine;\n"
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert run.ok is True
    assert writes == []
    assert len(edits) == 1
    assert target.read_text(encoding="utf-8") == "mod engine;\npub use crate::engine::generate_palette;\n"
    assert run.execution_result is not None
    receipt = run.execution_result.receipt
    assert receipt.status == "applied"
    record = receipt.metadata["execution_records"][0]
    assert record["operation"] == "edit_file"
    assert record["span_based"] is True
    assert record["unique_context_checked"] is True


def test_lib_root_facade_export_blocks_existing_export_declarations() -> None:
    raw_error = (
        "error[E0432]: unresolved import `palette_kit::generate_palette`\n"
        " --> tests/smoke.rs:1:5\n"
        "  |\n"
        "1 | use palette_kit::generate_palette;\n"
        "  |     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ no `generate_palette` in the root\n"
    )
    base_files = {
        "Cargo.toml": '[package]\nname = "palette-kit"\nversion = "0.1.0"\nedition = "2021"\n',
        "src/lib.rs": "mod engine;\npub use crate::engine::Palette;\n",
        "src/engine.rs": "pub struct Palette;\npub fn generate_palette() {}\n",
    }

    shadow = classify_rust_lib_root_facade_shadow(
        base_files=base_files,
        diagnostics=_diagnostics(raw_error),
    )
    planning = plan_runtime_repair(
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=(raw_error,),
        mode="shadow",
    )

    assert shadow.candidates == ()
    assert {blocker.reason for blocker in shadow.blockers} == {"export_declaration_requires_symbol_contract"}
    assert all(blocker.metadata["runtime_plan_available"] is False for blocker in shadow.blockers)
    assert planning.plan is None
    assert planning.composition is None


@pytest.mark.parametrize(
    ("base_files", "expected_reason"),
    (
        (
            {
                "src/main.rs": ("pub use crate::lib::engine::Palette;\n"),
            },
            "export_declaration_context",
        ),
        (
            {
                "src/main.rs": ("pub mod crate::lib::engine::Palette;\n"),
            },
            "module_declaration_context",
        ),
        (
            {
                "src/main.rs": ("use crate::engine::*;\nfn demo() { let _value: crate::lib::engine::Palette; }\n"),
            },
            "ambiguous_glob_import",
        ),
        (
            {
                "src/main.rs": (
                    "use crate::engine::Palette as EnginePalette;\n"
                    "fn demo() { let _value: crate::lib::engine::Palette; }\n"
                ),
            },
            "ambiguous_alias_import",
        ),
        (
            {
                "src/main.rs": ('#[cfg(feature = "engine")]\nfn demo() { let _value: crate::lib::engine::Palette; }\n'),
            },
            "cfg_gated_context",
        ),
        (
            {
                "src/main.rs": ('include!("generated.rs");\nfn demo() { let _value: crate::lib::engine::Palette; }\n'),
            },
            "macro_context",
        ),
        (
            {
                "src/main.rs": "fn one() { let _value: crate::lib::engine::Palette; }\n",
                "src/other.rs": "fn two() { let _value: crate::lib::engine::Palette; }\n",
            },
            "multiple_span_matches",
        ),
    ),
)
def test_lib_root_facade_path_rewrite_blocks_unsafe_contexts(
    base_files: dict[str, str],
    expected_reason: str,
) -> None:
    raw_error = (
        "Rust lib-root path rewrite required: replace `crate::lib::engine::Palette` with "
        "`crate::engine::Palette` before publishing the root facade"
    )

    shadow = classify_rust_lib_root_facade_shadow(
        base_files=base_files,
        diagnostics=_diagnostics(raw_error),
    )
    planning = plan_runtime_repair(
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=(raw_error,),
        mode="shadow",
    )

    assert shadow.candidates == ()
    assert {blocker.reason for blocker in shadow.blockers} == {expected_reason}
    assert planning.plan is None
    assert planning.composition is None


@pytest.mark.parametrize(
    ("base_files", "expected_reason"),
    (
        (
            {
                "src/lib.rs": "pub use crate::engine::generate_palette as build_palette;\nmod engine;\n",
                "src/engine.rs": "pub fn generate_palette() {}\n",
            },
            "ambiguous_alias_import",
        ),
        (
            {
                "src/lib.rs": "pub use crate::engine::*;\nmod engine;\n",
                "src/engine.rs": "pub fn generate_palette() {}\n",
            },
            "ambiguous_glob_import",
        ),
        (
            {
                "src/lib.rs": '#[cfg(feature = "engine")]\nmod engine;\n',
                "src/engine.rs": "pub fn generate_palette() {}\n",
            },
            "cfg_gated_context",
        ),
        (
            {
                "src/lib.rs": 'include!("generated_exports.rs");\nmod engine;\n',
                "src/engine.rs": "pub fn generate_palette() {}\n",
            },
            "macro_context",
        ),
        (
            {
                "src/lib.rs": "mod engine;\nmod alternate;\n",
                "src/engine.rs": "pub fn generate_palette() {}\n",
                "src/alternate.rs": "pub fn generate_palette() {}\n",
            },
            "multiple_module_matches",
        ),
    ),
)
def test_lib_root_facade_shadow_blocks_ambiguous_contexts(
    base_files: dict[str, str],
    expected_reason: str,
) -> None:
    shadow = classify_rust_lib_root_facade_shadow(
        base_files=base_files,
        diagnostics=_diagnostics(
            "error[E0432]: unresolved import `palette_kit::generate_palette`\n"
            " --> tests/smoke.rs:1:5\n"
            "  |\n"
            "1 | use palette_kit::generate_palette;\n"
            "  |     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ no `generate_palette` in the root\n",
        ),
    )

    assert shadow.candidates == ()
    assert {blocker.reason for blocker in shadow.blockers} == {expected_reason}
    assert all(blocker.metadata["writes_allowed"] is False for blocker in shadow.blockers)
