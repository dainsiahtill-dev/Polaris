"""Rust module topology repair tests for Director Runtime."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from polaris.cells.director.runtime.internal.repair_kernel import (
    PatchComposer,
    default_repair_rule_registry,
    normalize_artifact_quality_errors,
    plan_runtime_repair,
    run_runtime_repair,
    runtime_repair_source_tools,
)
from polaris.cells.director.runtime.internal.repair_kernel.contracts import FILE_ABSENT_HASH, sha256_text
from polaris.cells.director.runtime.internal.repair_kernel.rust_syntax import (
    RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
    RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
    RUST_MISSING_MODULE_FILE_STUB,
    build_rust_duplicate_module_file_plan,
    build_rust_missing_module_file_plan,
)

_FORBIDDEN_STUB_ITEM_RE = re.compile(
    r"(?m)^\s*(?:pub\s+)?(?:struct|enum|fn|type|trait|impl|use|mod|macro(?:_rules!)?)\b"
)


def _rust_e0583_missing_module_error(
    *,
    module_name: str = "models",
    path: str = "src/lib.rs",
    line: int = 1,
    declaration: str = "pub mod models;",
    candidates: str = '"src/models.rs" or "src/models/mod.rs"',
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


def _rust_e0761_duplicate_module_error(
    *,
    module_name: str = "models",
    first_path: str = "src/models.rs",
    second_path: str = "src/models/mod.rs",
) -> str:
    return (
        f'error[E0761]: file for module `{module_name}` found at both "{first_path}" and "{second_path}"\n'
        " --> src/lib.rs:1:1\n"
        "  |\n"
        "1 | pub mod models;\n"
        "  | ^^^^^^^^^^^^^^^\n"
    )


def _diagnostics(raw: str):
    return normalize_artifact_quality_errors([raw])


def _assert_comment_only_marker_stub(content: str) -> None:
    assert content == RUST_MISSING_MODULE_FILE_STUB
    assert _FORBIDDEN_STUB_ITEM_RE.search(content) is None
    assert "pub use" not in content


def test_rust_missing_module_file_rule_builds_comment_only_create_file_plan() -> None:
    raw = _rust_e0583_missing_module_error()
    diagnostics = _diagnostics(raw)

    plan = build_rust_missing_module_file_plan(
        base_files={"src/lib.rs": "pub mod models;\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.missing_module_file"
    assert plan.source_tool == RUST_MISSING_MODULE_FILE_SOURCE_TOOL
    assert plan.priority == 1
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "write_file"
    assert operation.path == "src/models.rs"
    assert operation.metadata["repair_kind"] == "rust_missing_module_file"
    assert operation.metadata["write_file_reason"] == "new_file_or_empty_file"
    assert operation.metadata["module_name"] == "models"
    assert operation.metadata["rustc_help_candidate"] is True
    assert operation.metadata["symbol_generation"] is False
    _assert_comment_only_marker_stub(str(operation.content))

    composition = PatchComposer().compose({"src/lib.rs": "pub mod models;\n"}, plan.operations)
    assert composition.ok is True
    patch = composition.patches[0]
    assert patch.path == "src/models.rs"
    assert patch.exists_before is False
    assert patch.exists_after is True
    assert patch.metadata["created_file"] is True
    assert patch.metadata["created_or_deleted"] == "created"
    _assert_comment_only_marker_stub(patch.content_after)


def test_rust_missing_module_file_rule_requires_diagnostic_line_to_declare_module() -> None:
    raw = _rust_e0583_missing_module_error()

    plan = build_rust_missing_module_file_plan(
        base_files={
            "src/lib.rs": "fn main() {}\n",
            "src/other.rs": "pub mod models;\n",
        },
        diagnostics=_diagnostics(raw),
        mode="shadow",
    )

    assert plan is None


def test_rust_missing_module_file_rule_rejects_existing_base_file() -> None:
    raw = _rust_e0583_missing_module_error(candidates='"src/models.rs"')

    plan = build_rust_missing_module_file_plan(
        base_files={
            "src/lib.rs": "pub mod models;\n",
            "src/models.rs": "",
        },
        diagnostics=_diagnostics(raw),
        mode="shadow",
    )

    assert plan is None


@pytest.mark.parametrize(
    "candidate",
    (
        '"../src/models.rs"',
        '"/tmp/project/src/models.rs"',
        '"C:/tmp/project/src/models.rs"',
        '"target/models.rs"',
        '"build/models.rs"',
        '"out/models.rs"',
        '"src/models.txt"',
    ),
)
def test_rust_missing_module_file_rule_rejects_unsafe_help_candidates(candidate: str) -> None:
    raw = _rust_e0583_missing_module_error(candidates=candidate)

    plan = build_rust_missing_module_file_plan(
        base_files={"src/lib.rs": "pub mod models;\n"},
        diagnostics=_diagnostics(raw),
        mode="shadow",
    )

    assert plan is None


def test_rust_missing_module_file_rule_matches_coverage_and_runtime_dispatch() -> None:
    raw = _rust_e0583_missing_module_error()
    diagnostics = _diagnostics(raw)

    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool=RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
        base_files={"src/lib.rs": "pub mod models;\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert RUST_MISSING_MODULE_FILE_SOURCE_TOOL in runtime_repair_source_tools()
    assert "deterministic_rust_post_repair" not in runtime_repair_source_tools()
    assert "deterministic_rust_module_stub_symbol_repair" not in runtime_repair_source_tools()
    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.missing_module_file" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert RUST_MISSING_MODULE_FILE_SOURCE_TOOL in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == RUST_MISSING_MODULE_FILE_SOURCE_TOOL
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_missing_module_file_runtime_binding_writes_new_marker_file(tmp_path: Path) -> None:
    relative_path = "src/lib.rs"
    source_path = tmp_path / relative_path
    source_path.parent.mkdir(parents=True)
    source_path.write_text("pub mod models;\n", encoding="utf-8")
    raw = _rust_e0583_missing_module_error()
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        write_calls.append((path, content))
        return {"ok": True, "file": path, "operation": "write_file"}

    result = run_runtime_repair(
        source_tool=RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
        workspace=tmp_path,
        base_files={relative_path: "pub mod models;\n"},
        artifact_quality_errors=(raw,),
        writer=writer,
    )

    assert result.ok is True
    assert write_calls == [("src/models.rs", RUST_MISSING_MODULE_FILE_STUB)]
    _assert_comment_only_marker_stub((tmp_path / "src/models.rs").read_text(encoding="utf-8"))
    assert result.execution_result is not None
    receipt = result.execution_result.receipt
    assert receipt.rule_id == "rust.missing_module_file"
    assert receipt.source_tool == RUST_MISSING_MODULE_FILE_SOURCE_TOOL
    assert receipt.files_changed == ("src/models.rs",)
    record = receipt.metadata["execution_records"][0]
    assert record["operation"] == "write_file"
    assert record["created_file"] is True
    assert record["created_or_deleted"] == "created"
    assert record["exists_before"] is False
    assert record["exists_after"] is True
    assert record["write_file_reason"] == "new_file_or_empty_file"


def test_rust_duplicate_module_file_rule_builds_delete_file_plan_for_generated_side() -> None:
    raw = _rust_e0761_duplicate_module_error()
    generated = "// Polaris generated module stub\n"
    real = "pub struct Model;\n"

    plan = build_rust_duplicate_module_file_plan(
        base_files={
            "src/models.rs": generated,
            "src/models/mod.rs": real,
        },
        diagnostics=_diagnostics(raw),
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.duplicate_module_file"
    assert plan.source_tool == RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL
    assert plan.metadata["runtime_plan_available"] is True
    assert plan.metadata["execution_authority"] == "director_runtime"
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "delete_file"
    assert operation.path == "src/models.rs"
    assert operation.before_hash == sha256_text(generated)
    assert operation.metadata["delete_candidate_evidence"] == "polaris_marker"
    assert operation.metadata["sibling_path"] == "src/models/mod.rs"
    assert operation.metadata["execution_authority"] == "director_runtime"

    composition = PatchComposer().compose({"src/models.rs": generated}, plan.operations)
    assert composition.ok is True
    patch = composition.patches[0]
    assert patch.path == "src/models.rs"
    assert patch.exists_before is True
    assert patch.exists_after is False
    assert patch.before_hash == sha256_text(generated)
    assert patch.after_hash == FILE_ABSENT_HASH


def test_rust_duplicate_module_file_rule_rejects_both_real_code() -> None:
    raw = _rust_e0761_duplicate_module_error()

    plan = build_rust_duplicate_module_file_plan(
        base_files={
            "src/models.rs": "pub struct Model;\n",
            "src/models/mod.rs": "pub struct Other;\n",
        },
        diagnostics=_diagnostics(raw),
        mode="shadow",
    )

    assert plan is None


def test_rust_duplicate_module_file_rule_matches_coverage_and_runtime_dispatch() -> None:
    raw = _rust_e0761_duplicate_module_error()
    generated = "// Polaris generated module stub\n"
    real = "pub struct Model;\n"
    diagnostics = _diagnostics(raw)

    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool=RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
        base_files={
            "src/models.rs": generated,
            "src/models/mod.rs": real,
        },
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL in runtime_repair_source_tools()
    assert "deterministic_rust_post_repair" not in runtime_repair_source_tools()
    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["metadata_only_match"] is False
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.duplicate_module_file" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_duplicate_module_file_runtime_binding_deletes_generated_file_with_receipt(
    tmp_path: Path,
) -> None:
    raw = _rust_e0761_duplicate_module_error()
    generated = "// Polaris generated module stub\n"
    real = "pub struct Model;\n"
    generated_path = tmp_path / "src/models.rs"
    real_path = tmp_path / "src/models/mod.rs"
    generated_path.parent.mkdir(parents=True)
    real_path.parent.mkdir(parents=True)
    generated_path.write_text(generated, encoding="utf-8")
    real_path.write_text(real, encoding="utf-8")
    write_calls: list[tuple[str, str]] = []
    delete_calls: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append((path, content))
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "write_file"}

    def deleter(path: str) -> dict[str, object]:
        delete_calls.append(path)
        (tmp_path / path).unlink()
        return {"ok": True, "file": path, "operation": "delete_file"}

    result = run_runtime_repair(
        source_tool=RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
        workspace=tmp_path,
        base_files={
            "src/models.rs": generated,
            "src/models/mod.rs": real,
        },
        artifact_quality_errors=(raw,),
        writer=writer,
        deleter=deleter,
        allowed_paths=("src/models.rs", "src/models/mod.rs"),
    )

    assert result.ok is True
    assert write_calls == []
    assert delete_calls == ["src/models.rs"]
    assert not generated_path.exists()
    assert real_path.read_text(encoding="utf-8") == real
    assert result.execution_result is not None
    receipt = result.execution_result.receipt
    assert receipt.rule_id == "rust.duplicate_module_file"
    assert receipt.source_tool == RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL
    assert receipt.files_changed == ("src/models.rs",)
    assert receipt.before_hashes["src/models.rs"] == sha256_text(generated)
    assert receipt.after_hashes["src/models.rs"] == FILE_ABSENT_HASH
    record = receipt.metadata["execution_records"][0]
    assert record["operation"] == "delete_file"
    assert record["before_hash"] == sha256_text(generated)
    assert record["after_hash"] == FILE_ABSENT_HASH
    assert record["exists_before"] is True
    assert record["exists_after"] is False
    assert record["deleted_file"] is True
    assert record["created_or_deleted"] == "deleted"
    assert record["rollback_strategy"] == "write_file_full_restore"
    assert record["rollback_restore_strategy"] == "write_file_full_restore"


def test_rust_duplicate_module_file_runtime_binding_without_deleter_fails_closed(
    tmp_path: Path,
) -> None:
    raw = _rust_e0761_duplicate_module_error()
    generated = "// Polaris generated module stub\n"
    real = "pub struct Model;\n"
    generated_path = tmp_path / "src/models.rs"
    generated_path.parent.mkdir(parents=True)
    generated_path.write_text(generated, encoding="utf-8")
    real_path = tmp_path / "src/models/mod.rs"
    real_path.parent.mkdir(parents=True)
    real_path.write_text(real, encoding="utf-8")

    def writer(path: str, content: str) -> dict[str, object]:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "write_file"}

    result = run_runtime_repair(
        source_tool=RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
        workspace=tmp_path,
        base_files={
            "src/models.rs": generated,
            "src/models/mod.rs": real,
        },
        artifact_quality_errors=(raw,),
        writer=writer,
        allowed_paths=("src/models.rs", "src/models/mod.rs"),
    )

    assert result.ok is False
    assert result.error_code == "repair_execution_failed"
    assert result.error_message is not None
    assert "policy-gated deleter" in result.error_message
    assert generated_path.read_text(encoding="utf-8") == generated
    assert real_path.read_text(encoding="utf-8") == real
    assert result.execution_result is not None
    receipt = result.execution_result.receipt
    assert receipt.status == "failed"
    assert receipt.before_hashes["src/models.rs"] == sha256_text(generated)
    assert receipt.after_hashes["src/models.rs"] == FILE_ABSENT_HASH
    assert receipt.metadata["error"].endswith("policy-gated deleter for src/models.rs")
