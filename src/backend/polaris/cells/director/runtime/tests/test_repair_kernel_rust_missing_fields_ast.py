"""Tests for Rust missing-fields AST extraction and shadow planning."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.director.runtime.internal.repair_kernel.contracts import RepairDiagnostic, RepairOperation
from polaris.cells.director.runtime.internal.repair_kernel.runtime_dispatch import runtime_repair_source_tools
from polaris.cells.director.runtime.internal.repair_kernel.rust_ast import (
    RUST_MISSING_FIELDS_SOURCE_TOOL,
    RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL,
    extract_rust_missing_fields_ast,
    plan_rust_missing_fields_shadow,
)
from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    RunDirectorRepairCommandV1,
    plan_director_repair,
    run_director_repair,
)


def test_rust_missing_fields_ast_extractor_reads_record_struct_literals_and_diagnostic_access() -> None:
    content = """// Polaris marker: rust.missing_fields
struct Flight {
    pub name: String,
    duration: u32,
}

fn build() -> Flight {
    Flight { name: String::new(), duration: 3 }
}

fn show(flight: &Flight) {
    let _ = flight.altitude;
}
"""
    line = _line_number(content, "flight.altitude")
    diagnostic = RepairDiagnostic(
        source="rustc",
        code="rust_e0609",
        message="no field `altitude` on type `&Flight`",
        path="src/lib.rs",
        line=line,
        raw="error[E0609]: no field `altitude` on type `&Flight`",
    )

    index = extract_rust_missing_fields_ast(base_files={"src/lib.rs": content}, diagnostics=(diagnostic,))

    assert index.parse_blockers == ()
    assert [struct.name for struct in index.structs] == ["Flight"]
    struct = index.structs[0]
    assert struct.generated_or_marker_file is True
    assert [(field.name, field.visibility, field.type_text) for field in struct.fields] == [
        ("name", "pub", "String"),
        ("duration", "", "u32"),
    ]
    assert [(field.name, field.value_text) for field in index.struct_literals[0].fields] == [
        ("name", "String::new()"),
        ("duration", "3"),
    ]
    assert len(index.diagnostic_field_accesses) == 1
    access = index.diagnostic_field_accesses[0]
    assert access.receiver_text == "flight"
    assert access.field_name == "altitude"
    assert access.line == line


def test_rust_missing_fields_shadow_candidate_is_read_only_for_marker_struct_declaration() -> None:
    content = """// Polaris marker: rust.missing_fields
struct Flight {
    name: String,
}

fn show(flight: &Flight) {
    let _ = flight.duration;
}
"""
    diagnostic = RepairDiagnostic(
        source="rustc",
        code="rust_e0609",
        message="no field `duration` on type `&Flight`",
        path="src/lib.rs",
        line=_line_number(content, "flight.duration"),
        raw="error[E0609]: no field `duration` on type `&Flight`",
    )

    shadow = plan_rust_missing_fields_shadow(base_files={"src/lib.rs": content}, diagnostics=(diagnostic,))

    assert shadow.runtime_executable is False
    assert shadow.writes_allowed is False
    assert _blocker_reasons(shadow) == {"type_inference_required"}
    blocker = shadow.blockers[0]
    assert blocker.source_tool == RUST_MISSING_FIELDS_SOURCE_TOOL
    assert blocker.path == "src/lib.rs"
    assert blocker.field_name == "duration"
    assert blocker.metadata["field_type_source"] == "not_inferred"
    assert blocker.metadata["type_guessing_allowed"] is False
    assert blocker.metadata["runtime_executable"] is False
    assert len(shadow.candidates) == 1
    candidate = shadow.candidates[0]
    assert candidate.source_tool == RUST_MISSING_FIELDS_SOURCE_TOOL
    assert candidate.candidate_kind == "missing_struct_field_declaration"
    assert candidate.path == "src/lib.rs"
    assert candidate.struct_name == "Flight"
    assert candidate.field_name == "duration"
    assert candidate.field_type_text is None
    assert candidate.field_type_source == "not_inferred"
    assert candidate.metadata["type_guessing_allowed"] is False
    assert candidate.runtime_executable is False
    assert candidate.writes_allowed is False


def test_rust_struct_literal_missing_field_shadow_candidate_uses_declared_type_only() -> None:
    content = """// Polaris marker: rust.struct_literal_missing_field
struct Flight {
    name: String,
    duration: u32,
}

fn build() -> Flight {
    Flight { name: String::new() }
}
"""
    diagnostic = RepairDiagnostic(
        source="rustc",
        code="rust_e0063",
        message="missing field `duration` in initializer of `Flight`",
        path="src/lib.rs",
        line=_line_number(content, "Flight { name"),
        raw="error[E0063]: missing field `duration` in initializer of `Flight`",
    )

    shadow = plan_rust_missing_fields_shadow(base_files={"src/lib.rs": content}, diagnostics=(diagnostic,))

    assert shadow.blockers == ()
    assert len(shadow.candidates) == 1
    candidate = shadow.candidates[0]
    assert candidate.source_tool == RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL
    assert candidate.candidate_kind == "struct_literal_missing_field_initializer"
    assert candidate.path == "src/lib.rs"
    assert candidate.field_name == "duration"
    assert candidate.field_type_text == "u32"
    assert candidate.field_type_source == "record_struct_field"
    assert candidate.metadata["initializer_field_names"] == ("name",)
    assert candidate.metadata["value_guessing_allowed"] is False


def test_rust_missing_fields_shadow_blocks_unknown_duplicate_non_marker_and_public_api() -> None:
    unknown = plan_rust_missing_fields_shadow(
        base_files={"src/lib.rs": "// Polaris marker: rust.missing_fields\nfn main() {}\n"},
        diagnostics=(
            RepairDiagnostic(
                source="rustc",
                code="rust_e0609",
                message="no field `duration` on type `&Flight`",
                path="src/lib.rs",
                raw="error[E0609]: no field `duration` on type `&Flight`",
            ),
        ),
    )
    assert _blocker_reasons(unknown) == {"unknown_type"}

    duplicate = plan_rust_missing_fields_shadow(
        base_files={
            "src/a.rs": "// Polaris marker: rust.missing_fields\nstruct Flight { name: String }\n",
            "src/b.rs": "// Polaris marker: rust.missing_fields\nstruct Flight { name: String }\n",
        },
        diagnostics=(
            RepairDiagnostic(
                source="rustc",
                code="rust_e0609",
                message="no field `duration` on type `&Flight`",
                path="src/a.rs",
                raw="error[E0609]: no field `duration` on type `&Flight`",
            ),
        ),
    )
    assert _blocker_reasons(duplicate) == {"duplicate_struct"}

    non_marker = plan_rust_missing_fields_shadow(
        base_files={"src/lib.rs": "struct Flight { name: String }\n"},
        diagnostics=(
            RepairDiagnostic(
                source="rustc",
                code="rust_e0609",
                message="no field `duration` on type `&Flight`",
                path="src/lib.rs",
                raw="error[E0609]: no field `duration` on type `&Flight`",
            ),
        ),
    )
    assert _blocker_reasons(non_marker) == {"non_marker_file"}

    public_api = plan_rust_missing_fields_shadow(
        base_files={"src/lib.rs": "// Polaris marker: rust.missing_fields\npub struct Flight { name: String }\n"},
        diagnostics=(
            RepairDiagnostic(
                source="rustc",
                code="rust_e0609",
                message="no field `duration` on type `&Flight`",
                path="src/lib.rs",
                raw="error[E0609]: no field `duration` on type `&Flight`",
            ),
        ),
    )
    assert _blocker_reasons(public_api) == {"public_api_expansion_needed"}


def test_public_missing_fields_metadata_source_tools_still_fail_closed(tmp_path: Path) -> None:
    base_files = {"src/lib.rs": "// Polaris marker: rust.missing_fields\nstruct Flight { name: String }\n"}
    raw_errors = {
        RUST_MISSING_FIELDS_SOURCE_TOOL: "error[E0609]: no field `duration` on type `&Flight`",
        "deterministic_rust_post_repair": "error[E0433]: failed to resolve: use of unresolved module `serde`",
    }
    writes: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append((path, content))
        raise AssertionError("metadata-only missing_fields source_tool must not write files")

    for source_tool, raw_error in raw_errors.items():
        planning = plan_director_repair(
            PlanDirectorRepairCommandV1(
                source_tool=source_tool,
                base_files=base_files,
                artifact_quality_errors=(raw_error,),
                mode="shadow",
            )
        )
        planning_payload = planning.to_dict()

        assert planning_payload["ok"] is False
        assert planning_payload["planned"] is False
        assert planning_payload["error_code"] == "unsupported_repair_source_tool"
        assert planning_payload["plan_summary"] is None
        assert planning_payload["composition_summary"]["ok"] is False

        run = run_director_repair(
            RunDirectorRepairCommandV1(
                task_id=f"task-{source_tool}",
                workspace=str(tmp_path),
                source_tool=source_tool,
                base_files=base_files,
                artifact_quality_errors=(raw_error,),
                allowed_paths=("src/lib.rs",),
            ),
            writer=writer,
        )

        assert run.ok is False
        assert run.error_code == "unsupported_repair_source_tool"
        assert run.receipts == ()
        assert run.metadata["planning"]["error_code"] == "unsupported_repair_source_tool"
    assert writes == []
    executable_source_tools = set(runtime_repair_source_tools())
    assert RUST_MISSING_FIELDS_SOURCE_TOOL not in executable_source_tools
    assert "deterministic_rust_post_repair" not in executable_source_tools
    assert RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL in executable_source_tools


def test_public_struct_literal_missing_field_plans_precise_generated_marker_edit() -> None:
    content = """// Polaris marker: rust.struct_literal_missing_field
struct Flight {
    name: String,
    duration: u32,
}

fn build() -> Flight {
    Flight { name: String::new() }
}
"""
    raw_error = (
        "error[E0063]: missing field `duration` in initializer of `Flight`\n"
        f" --> src/lib.rs:{_line_number(content, 'Flight { name')}:5\n"
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL,
            base_files={"src/lib.rs": content},
            artifact_quality_errors=(raw_error,),
            mode="shadow",
        )
    )
    payload = planning.to_dict()

    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["error_code"] is None
    assert payload["plan_summary"]["rule_id"] == "rust.struct_literal_missing_field_initializer"
    assert payload["plan_summary"]["operation_count"] == 1
    assert payload["composition_summary"]["ok"] is True
    assert payload["composition_summary"]["patches"][0]["path"] == "src/lib.rs"
    assert (
        "Flight { name: String::new(), duration: 0 }" in payload["composition_summary"]["patches"][0]["content_after"]
    )


def test_public_struct_literal_missing_field_run_uses_editor_not_writer(tmp_path: Path) -> None:
    relative_path = "src/lib.rs"
    content = """// Polaris marker: rust.struct_literal_missing_field
struct Flight {
    name: String,
    duration: u32,
}

fn build() -> Flight {
    Flight { name: String::new() }
}
"""
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    raw_error = (
        "error[E0063]: missing field `duration` in initializer of `Flight`\n"
        f" --> src/lib.rs:{_line_number(content, 'Flight { name')}:5\n"
    )
    writes: list[tuple[str, str]] = []
    edits: list[RepairOperation] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append((path, updated))
        raise AssertionError("span-based struct literal repair must use editor")

    def editor(operation: RepairOperation) -> dict[str, object]:
        edits.append(operation)
        edit_target = tmp_path / operation.path
        current = edit_target.read_text(encoding="utf-8")
        start = int(operation.span_start or 0)
        end = int(operation.span_end or 0)
        assert current[start:end] == str(operation.expected or "")
        edit_target.write_text(current[:start] + str(operation.replacement or "") + current[end:], encoding="utf-8")
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-struct-literal-missing-field",
            workspace=str(tmp_path),
            source_tool=RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL,
            base_files={relative_path: content},
            artifact_quality_errors=(raw_error,),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert writes == []
    assert len(edits) == 1
    assert "Flight { name: String::new(), duration: 0 }" in target.read_text(encoding="utf-8")
    assert len(result.receipts) == 1
    receipt_payload = result.receipts[0].to_dict()
    assert receipt_payload["source_tool"] == RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL
    assert receipt_payload["status"] == "applied"
    assert receipt_payload["files_changed"] == [relative_path]
    assert receipt_payload["metadata"]["execution_records"][0]["operation"] == "edit_file"
    assert receipt_payload["metadata"]["execution_records"][0]["span_based"] is True
    assert receipt_payload["metadata"]["execution_records"][0]["unique_context_checked"] is True


def test_public_struct_literal_missing_field_unknown_type_fails_closed(tmp_path: Path) -> None:
    content = """// Polaris marker: rust.struct_literal_missing_field
struct Flight {
    name: String,
    duration: Duration,
}

fn build() -> Flight {
    Flight { name: String::new() }
}
"""
    raw_error = (
        "error[E0063]: missing field `duration` in initializer of `Flight`\n"
        f" --> src/lib.rs:{_line_number(content, 'Flight { name')}:5\n"
    )
    writes: list[tuple[str, str]] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append((path, updated))
        raise AssertionError("unsafe missing-field case must not write")

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL,
            base_files={"src/lib.rs": content},
            artifact_quality_errors=(raw_error,),
            mode="shadow",
        )
    )
    run = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-struct-literal-missing-field-unsafe",
            workspace=str(tmp_path),
            source_tool=RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL,
            base_files={"src/lib.rs": content},
            artifact_quality_errors=(raw_error,),
            allowed_paths=("src/lib.rs",),
        ),
        writer=writer,
    )

    assert planning.ok is False
    assert planning.planned is False
    assert planning.plan_summary is None
    assert run.ok is False
    assert run.error_code == "repair_not_planned"
    assert run.receipts == ()
    assert writes == []


def _line_number(content: str, needle: str) -> int:
    for index, line in enumerate(content.splitlines(), start=1):
        if needle in line:
            return index
    raise AssertionError(f"needle not found: {needle}")


def _blocker_reasons(shadow: object) -> set[str]:
    return {blocker.reason for blocker in shadow.blockers}
