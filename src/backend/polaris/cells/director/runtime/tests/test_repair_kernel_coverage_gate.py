"""Coverage-gate tests for runtime repair convergence."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.director.runtime.internal.repair_kernel.diagnostics import normalize_artifact_quality_errors
from polaris.cells.director.runtime.internal.repair_kernel.registry import build_repair_coverage_report
from polaris.cells.director.runtime.internal.repair_kernel.runtime_dispatch import (
    run_runtime_repair_convergence,
    runtime_repair_source_tools,
)
from polaris.cells.director.runtime.internal.repair_kernel.rust_syntax import (
    RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
)
from polaris.cells.director.runtime.internal.repair_kernel.scheduler import RepairVerifierSnapshot
from polaris.cells.director.runtime.public import (
    QueryDirectorRepairCoverageV1,
    query_director_repair_coverage,
)
from polaris.kernelone.quality import artifact_quality_issues_from_errors

TS_COMMA_SOURCE_TOOL = "deterministic_typescript_return_object_semicolon_repair"
RUST_MISSING_FIELDS_SOURCE_TOOL = "deterministic_rust_missing_fields_repair"
RUST_MISSING_LIB_TARGET_SOURCE_TOOL = "deterministic_rust_missing_lib_target_repair"
RUST_LIB_ROOT_FACADE_SOURCE_TOOL = "deterministic_rust_lib_root_facade_repair"
RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL = "deterministic_rust_struct_literal_missing_field_repair"


def test_convergence_gate_records_report_and_fails_all_uncovered_diagnostics(tmp_path: Path) -> None:
    raw_error = "declared target file missing app/models/widget.rb is missing"

    def verifier(_round_number: int, _receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        raise AssertionError("coverage gate should stop before scheduler verifier")

    result = run_runtime_repair_convergence(
        source_tools=(TS_COMMA_SOURCE_TOOL,),
        workspace=tmp_path,
        base_files={},
        artifact_quality_errors=(raw_error,),
        verifier=verifier,
        max_rounds=2,
    )

    assert result.status == "coverage_gap_uncovered_diagnostics"
    assert result.converged is False
    assert result.receipts == ()
    assert result.metadata["coverage_report"]["total_diagnostics"] == 1
    assert result.metadata["coverage_gap_count"] == 1
    assert result.metadata["coverage_report"]["coverage_gap_recommended_routes"] == ["runtime_rule"]
    assert result.metadata["coverage_report"]["coverage_gap_slot_statuses"] == ["reserved_slot_available"]
    assert result.metadata["uncovered_diagnostics"][0]["code"] == "declared_target_missing"
    gap = result.metadata["coverage_gaps"][0]
    assert gap["known_rule_matched"] is False
    assert gap["metadata_only_match"] is False
    assert gap["executable_runtime_plan_matched"] is False
    assert gap["language"] == "ruby"
    assert gap["reserved_language_slot_matched"] is True
    assert gap["reserved_slot_available"] is True
    assert gap["slot_status"] == "reserved_slot_available"
    assert gap["reserved_language_slot"]["language"] == "ruby"
    assert gap["recommended_route"] == "runtime_rule"
    assert gap["recommended_next_owner"] == "runtime_rule"
    assert gap["coverage_status"] == "coverage_gap"
    assert gap["audit_reason"] == "known_rule_matched=false"


def test_public_coverage_projects_top_level_gap_report_for_audit_consumers() -> None:
    raw_error = "declared target file missing app/models/widget.rb is missing"

    payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(raw_error,))
    ).to_dict()

    assert payload["coverage_gap_count"] == 1
    assert payload["rule_discovery_required"] is True
    assert payload["coverage_gap_languages"] == ["ruby"]
    assert payload["coverage_gap_archetypes"] == ["missing_declared_target"]
    assert payload["coverage_gap_diagnostic_codes"] == ["declared_target_missing"]
    assert payload["coverage_gap_recommended_routes"] == ["runtime_rule"]
    assert payload["coverage_gap_slot_statuses"] == ["reserved_slot_available"]
    assert payload["uncovered_diagnostics"][0]["code"] == "declared_target_missing"
    assert payload["uncovered_diagnostics"][0]["metadata"]["target_file"] == "app/models/widget.rb"
    gap = payload["coverage_gaps"][0]
    assert gap["known_rule_matched"] is False
    assert gap["audit_reason"] == "known_rule_matched=false"
    assert gap["reserved_language_slot_matched"] is True
    assert gap["reserved_language_slot"]["language"] == "ruby"
    assert gap["recommended_next_owner"] == "runtime_rule"


def test_public_coverage_routes_node_typescript_configuration_diagnostics_to_runtime_rules() -> None:
    payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(
                "Artifact quality scan failed: TypeScript node builtin import 'node:url' "
                "requires '@types/node' in src/main.ts",
            "src/main.ts(43,5): error TS2580: Cannot find name 'process'. "
            "Do you need to install type definitions for node? Try npm i --save-dev @types/node.",
            "error TS2688: Cannot find type definition file for 'node'.",
            "src/main.ts(152,16): error TS1343: The 'import.meta' meta-property is only "
            "allowed when '--module' is es2020/es2022/esnext/system/node16/nodenext.",
                "src/verify.ts(201,40): error TS2550: Property 'replaceAll' does not exist "
                "on type 'string'. Do you need to change your target library? Try changing "
                "the 'lib' compiler option to 'es2021' or later.",
                "error TS6059: File '/tmp/workspace/tests/verify.test.ts' is not under 'rootDir' "
                "'/tmp/workspace/src'. 'rootDir' is expected to contain all source files.",
            )
        )
    ).to_dict()

    assert payload["coverage_gap_count"] == 0
    assert payload["covered_diagnostic_count"] == 6
    assert payload["executable_runtime_plan_diagnostic_count"] == 6

    matched_tools = [tuple(item["matched_source_tools"]) for item in payload["items"]]
    assert ("deterministic_runtime_dependency_repair",) in matched_tools
    assert ("deterministic_typescript_tsconfig_lib_repair",) in matched_tools
    assert ("deterministic_typescript_tsconfig_rootdir_repair",) in matched_tools
    for item in payload["items"]:
        assert item["known_rule_matched"] is True
        assert item["executable_runtime_plan_matched"] is True
        assert item["coverage_status"] == "executable_runtime"


def test_public_coverage_routes_typed_undeclared_runtime_import_issue_to_dependency_repair() -> None:
    raw_error = "Artifact quality scan failed: undeclared runtime import 'mongoose' in src/models/auditlog.ts"
    typed_issues = artifact_quality_issues_from_errors((raw_error,))

    payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(raw_error,),
            artifact_quality_issues=typed_issues,
        )
    ).to_dict()

    assert payload["coverage_gap_count"] == 0
    assert payload["covered_diagnostic_count"] == 1
    assert payload["executable_runtime_plan_diagnostic_count"] == 1
    item = payload["items"][0]
    assert item["matched_source_tools"] == ["deterministic_runtime_dependency_repair"]
    assert item["coverage_status"] == "executable_runtime"


def test_convergence_gate_distinguishes_unselected_executable_runtime_match_from_metadata_only(
    tmp_path: Path,
) -> None:
    unselected_executable_error = (
        "error[E0277]: the trait bound `Widget: Copy` is not satisfied\n  --> src/lib.rs:12:10"
    )

    def verifier(_round_number: int, _receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        raise AssertionError("unselected executable coverage should stop before scheduler verifier")

    unselected_executable = run_runtime_repair_convergence(
        source_tools=("deterministic_rust_dependency_repair",),
        workspace=tmp_path,
        base_files={},
        artifact_quality_errors=(unselected_executable_error,),
        verifier=verifier,
    )

    assert unselected_executable.status == "stuck_no_executable_runtime_plan"
    assert unselected_executable.metadata["coverage_gap_count"] == 0
    assert unselected_executable.metadata["metadata_only_diagnostic_count"] == 0
    assert unselected_executable.metadata["executable_runtime_plan_diagnostic_count"] == 1
    assert unselected_executable.metadata["selected_executable_runtime_plan_diagnostic_count"] == 0
    item = unselected_executable.metadata["coverage_report"]["items"][0]
    assert item["known_rule_matched"] is True
    assert item["metadata_only_match"] is False
    assert item["executable_runtime_plan_matched"] is True
    assert item["coverage_status"] == "executable_runtime"
    assert item["recommended_route"] == "runtime_rule"
    assert item["matched_rule_ids"] == ["rust.missing_trait_derive"]
    assert item["matched_source_tools"] == ["deterministic_rust_derive_repair"]
    assert item["runtime_plan_rule_ids"] == ["rust.missing_trait_derive"]

    executable = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected.",
            )
        )
    ).to_dict()

    assert executable["metadata_only_diagnostic_count"] == 0
    assert executable["executable_runtime_plan_diagnostic_count"] == 1
    assert executable["items"][0]["metadata_only_match"] is False
    assert executable["items"][0]["executable_runtime_plan_matched"] is True
    assert executable["items"][0]["coverage_status"] == "executable_runtime"
    assert executable["items"][0]["recommended_route"] == "runtime_rule"


def test_public_coverage_matches_rust_e0761_duplicate_module_as_executable_runtime() -> None:
    raw_error = (
        'error[E0761]: file for module `models` found at both "src/models.rs" and "src/models/mod.rs"\n'
        " --> src/lib.rs:1:1\n"
        "  |\n"
        "1 | pub mod models;\n"
        "  | ^^^^^^^^^^^^^^^\n"
    )

    payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(raw_error,))
    ).to_dict()
    item = payload["items"][0]

    assert RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL in runtime_repair_source_tools()
    assert "deterministic_rust_post_repair" not in runtime_repair_source_tools()
    assert payload["covered_diagnostic_count"] == 1
    assert payload["metadata_only_diagnostic_count"] == 0
    assert payload["executable_runtime_plan_diagnostic_count"] == 1
    assert item["known_rule_matched"] is True
    assert item["metadata_only_match"] is False
    assert item["executable_runtime_plan_matched"] is True
    assert item["matched_rule_ids"] == ["rust.duplicate_module_file"]
    assert item["matched_source_tools"] == [RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL]
    assert item["runtime_plan_rule_ids"] == ["rust.duplicate_module_file"]


def test_public_coverage_matches_rust_missing_fields_as_executable_runtime_with_type_inference_blocker() -> None:
    e0609_without_similar_name_help = (
        "error[E0609]: no field `duration` on type `&Flight`\n"
        " --> src/lib.rs:8:22\n"
        "  |\n"
        '8 |     println!("{}", flight.duration);\n'
        "  |                      ^^^^^^^^ unknown field\n"
    )

    payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(e0609_without_similar_name_help,))
    ).to_dict()
    e0609_item = payload["items"][0]

    assert RUST_MISSING_FIELDS_SOURCE_TOOL in runtime_repair_source_tools()
    assert "deterministic_rust_post_repair" not in runtime_repair_source_tools()
    assert payload["covered_diagnostic_count"] == 1
    assert payload["uncovered_diagnostic_count"] == 0
    assert payload["metadata_only_diagnostic_count"] == 0
    assert payload["executable_runtime_plan_diagnostic_count"] == 1

    assert e0609_item["known_rule_matched"] is True
    assert e0609_item["metadata_only_match"] is False
    assert e0609_item["executable_runtime_plan_matched"] is True
    assert e0609_item["coverage_status"] == "executable_runtime"
    assert e0609_item["recommended_route"] == "runtime_rule"
    assert e0609_item["reserved_slot_available"] is True
    assert e0609_item["slot_status"] == "reserved_slot_available"
    assert e0609_item["matched_rule_ids"] == ["rust.missing_struct_field_declaration"]
    assert e0609_item["matched_source_tools"] == [RUST_MISSING_FIELDS_SOURCE_TOOL]
    assert e0609_item["runtime_plan_rule_ids"] == ["rust.missing_struct_field_declaration"]
    internal_coverage_payload = build_repair_coverage_report(
        normalize_artifact_quality_errors([e0609_without_similar_name_help])
    ).to_dict()
    internal_e0609_item = internal_coverage_payload["items"][0]
    assert internal_e0609_item["runtime_blocker_reasons"] == []
    assert internal_e0609_item["runtime_blockers"] == []

    e0609_with_similar_name_help = (
        "error[E0609]: no field `duraton` on type `&Flight`\n"
        " --> src/lib.rs:8:22\n"
        "  |\n"
        '8 |     println!("{}", flight.duraton);\n'
        "  |                      ^^^^^^^ unknown field\n"
        "  |\n"
        "help: a field with a similar name exists\n"
        "  |\n"
        '8 -     println!("{}", flight.duraton);\n'
        '8 +     println!("{}", flight.duration);'
    )
    similar_name_payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(e0609_with_similar_name_help,))
    ).to_dict()
    similar_name_item = similar_name_payload["items"][0]

    assert similar_name_item["metadata_only_match"] is False
    assert "rust.field_rename_suggestion" in similar_name_item["matched_rule_ids"]
    assert "rust.missing_struct_field_declaration" not in similar_name_item["matched_rule_ids"]
    assert RUST_MISSING_FIELDS_SOURCE_TOOL not in similar_name_item["matched_source_tools"]


def test_public_coverage_matches_rust_struct_literal_missing_field_as_executable_runtime() -> None:
    e0063_missing_initializer = (
        "error[E0063]: missing field `duration` in initializer of `Flight`\n"
        " --> src/lib.rs:4:18\n"
        "  |\n"
        "4 |     let flight = Flight { name: String::new() };\n"
        "  |                  ^^^^^^ missing `duration`\n"
    )

    payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(e0063_missing_initializer,))
    ).to_dict()
    e0063_item = payload["items"][0]

    assert RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL in runtime_repair_source_tools()
    assert RUST_MISSING_FIELDS_SOURCE_TOOL in runtime_repair_source_tools()
    assert payload["covered_diagnostic_count"] == 1
    assert payload["uncovered_diagnostic_count"] == 0
    assert payload["metadata_only_diagnostic_count"] == 0
    assert payload["executable_runtime_plan_diagnostic_count"] == 1

    assert e0063_item["known_rule_matched"] is True
    assert e0063_item["metadata_only_match"] is False
    assert e0063_item["executable_runtime_plan_matched"] is True
    assert e0063_item["matched_rule_ids"] == ["rust.struct_literal_missing_field_initializer"]
    assert e0063_item["matched_source_tools"] == [RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL]
    assert e0063_item["runtime_plan_rule_ids"] == ["rust.struct_literal_missing_field_initializer"]


def test_public_coverage_matches_rust_missing_lib_target_src_lib_as_runtime_subset_not_e0583() -> None:
    rustc_missing_lib_path = (
        "error: can't find library `palette_kit` at path `src/lib.rs`\n"
        "  |\n"
        "  = note: the configured library target file does not exist\n"
    )
    manifest_lib_path_missing = "Cargo manifest [lib].path src/custom_lib.rs is missing for Rust library target"
    e0583_module_missing = (
        "error[E0583]: file not found for module `models`\n"
        " --> src/lib.rs:1:1\n"
        "  |\n"
        "1 | pub mod models;\n"
        "  | ^^^^^^^^^^^^^^^\n"
        "  |\n"
        '  = help: to create the module `models`, create file "src/models.rs" or "src/models/mod.rs"\n'
    )

    payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(
                rustc_missing_lib_path,
                manifest_lib_path_missing,
                e0583_module_missing,
            )
        )
    ).to_dict()
    rustc_item, manifest_item, e0583_item = payload["items"]

    assert RUST_MISSING_LIB_TARGET_SOURCE_TOOL in runtime_repair_source_tools()
    assert payload["covered_diagnostic_count"] == 3
    assert payload["metadata_only_diagnostic_count"] == 1
    assert payload["executable_runtime_plan_diagnostic_count"] == 2

    assert rustc_item["known_rule_matched"] is True
    assert rustc_item["metadata_only_match"] is False
    assert rustc_item["executable_runtime_plan_matched"] is True
    assert rustc_item["matched_rule_ids"] == ["rust.missing_lib_target", "rust.missing_lib_target_src_lib"]
    assert rustc_item["matched_source_tools"] == [RUST_MISSING_LIB_TARGET_SOURCE_TOOL]
    assert rustc_item["runtime_plan_rule_ids"] == ["rust.missing_lib_target_src_lib"]

    assert manifest_item["known_rule_matched"] is True
    assert manifest_item["metadata_only_match"] is True
    assert manifest_item["executable_runtime_plan_matched"] is False
    assert manifest_item["matched_rule_ids"] == ["rust.missing_lib_target"]
    assert manifest_item["matched_source_tools"] == [RUST_MISSING_LIB_TARGET_SOURCE_TOOL]
    assert manifest_item["runtime_plan_rule_ids"] == []

    assert e0583_item["known_rule_matched"] is True
    assert e0583_item["metadata_only_match"] is False
    assert e0583_item["executable_runtime_plan_matched"] is True
    assert "rust.missing_module_file" in e0583_item["matched_rule_ids"]
    assert "rust.missing_lib_target" not in e0583_item["matched_rule_ids"]
    assert RUST_MISSING_LIB_TARGET_SOURCE_TOOL not in e0583_item["matched_source_tools"]


def test_public_coverage_matches_rust_lib_root_facade_signals_with_path_rewrite_runtime_subset() -> None:
    root_unresolved_import = (
        "error[E0432]: unresolved import `palette_kit::generate_palette`\n"
        " --> tests/smoke.rs:1:5\n"
        "  |\n"
        "1 | use palette_kit::generate_palette;\n"
        "  |     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ no `generate_palette` in the root\n"
    )
    export_signal = "AssertionError: lib.rs must expose generate_palette API for Rust lib root facade"
    path_rewrite_signal = (
        "Rust lib-root path rewrite required: replace `crate::lib::engine::Palette` with "
        "`crate::engine::Palette` before publishing the root facade"
    )

    payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(root_unresolved_import, export_signal, path_rewrite_signal)
        )
    ).to_dict()
    root_item, export_item, path_rewrite_item = payload["items"]

    assert RUST_LIB_ROOT_FACADE_SOURCE_TOOL in runtime_repair_source_tools()
    assert payload["covered_diagnostic_count"] == 3
    assert payload["uncovered_diagnostic_count"] == 0
    assert payload["metadata_only_diagnostic_count"] == 0
    assert payload["executable_runtime_plan_diagnostic_count"] == 3

    expected_rule_ids = (
        "rust.lib_root_facade_root_import",
        "rust.lib_root_facade_export",
        "rust.lib_root_facade_path_rewrite",
    )
    for item, expected_rule_id in zip((root_item, export_item, path_rewrite_item), expected_rule_ids, strict=True):
        assert item["known_rule_matched"] is True
        assert item["metadata_only_match"] is False
        assert item["executable_runtime_plan_matched"] is True
        assert expected_rule_id in item["matched_rule_ids"]
        assert RUST_LIB_ROOT_FACADE_SOURCE_TOOL in item["matched_source_tools"]
        assert expected_rule_id in item["runtime_plan_rule_ids"]
    assert "rust.unresolved_pub_use" not in root_item["matched_rule_ids"]


def test_public_coverage_matches_rust_missing_canonical_root_module() -> None:
    raw_error = (
        "error[E0433]: cannot find `engine` in `fantasy_restaurant_queue_ai`\n"
        " --> tests/product.rs:22:34\n"
    )
    payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(raw_error,))
    ).to_dict()
    item = payload["items"][0]

    assert item["known_rule_matched"] is True
    assert item["executable_runtime_plan_matched"] is True
    assert "rust.lib_root_module_declaration" in item["matched_rule_ids"]
    assert RUST_LIB_ROOT_FACADE_SOURCE_TOOL in item["matched_source_tools"]


def test_public_coverage_gap_projects_reserved_slot_and_recommended_owner_fields() -> None:
    payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=("declared target file missing app/models/widget.rb is missing",)
        )
    ).to_dict()

    gap = payload["coverage_gaps"][0]
    item = payload["items"][0]

    assert payload["agi_execution_authority"] is False
    assert payload["coverage_gap_archetypes"] == ["missing_declared_target"]
    assert payload["coverage_gap_diagnostic_codes"] == ["declared_target_missing"]
    assert payload["coverage_gap_handoff_recommendations"] == ["runtime_rule_backlog"]
    assert payload["coverage_gap_recommended_routes"] == ["runtime_rule"]
    assert payload["coverage_gap_slot_statuses"] == ["reserved_slot_available"]
    assert gap["language"] == "ruby"
    assert gap["diagnostic_language"] == "ruby"
    assert gap["diagnostic_code"] == "declared_target_missing"
    assert gap["phase_suggestion"] == "target_contract"
    assert gap["archetype_suggestion"] == "missing_declared_target"
    assert gap["reserved_slot_available"] is True
    assert gap["slot_status"] == "reserved_slot_available"
    assert gap["reserved_language_slot_matched"] is True
    assert gap["reserved_language_slot"]["language"] == "ruby"
    assert gap["reserved_repairer_module"].endswith(".ruby_runtime")
    assert gap["reserved_slot_registration_policy"] == "bench_verified_rule_required"
    assert gap["recommended_next_owner"] == "runtime_rule"
    assert gap["recommended_route"] == "runtime_rule"
    assert gap["handoff_recommendation"] == "runtime_rule_backlog"
    assert gap["llm_advisory_recommended"] is False
    assert gap["agi_advisory_recommended"] is False
    assert gap["authoritative_rule_registration_allowed"] is False
    assert gap["recommended_registration_path"] == "bench_verified_rule_required"
    assert item["reserved_language_slot_matched"] is True
    assert item["reserved_slot_available"] is True
    assert item["slot_status"] == "reserved_slot_available"
    assert item["reserved_language_slot"]["language"] == "ruby"
    assert item["recommended_next_owner"] == "runtime_rule"
    assert item["recommended_route"] == "runtime_rule"
    assert item["handoff_recommendation"] == "runtime_rule_backlog"
    assert item["coverage_status"] == "coverage_gap"
    assert item["authoritative_rule_registration_allowed"] is False


def test_partial_coverage_executes_planned_diagnostics_and_preserves_residual_gap_evidence(
    tmp_path: Path,
) -> None:
    relative_path = "src/models/Flight.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    original = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )
    target.write_text(original, encoding="utf-8")
    comma_error = "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected."
    unknown_error = "src/models/Flight.ts(7,1): error TS9999: Unknown future compiler error."
    comma_diagnostic, unknown_diagnostic = normalize_artifact_quality_errors([comma_error, unknown_error])

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del receipts
        diagnostics = (comma_diagnostic, unknown_diagnostic) if round_number == 0 else (unknown_diagnostic,)
        return RepairVerifierSnapshot(
            diagnostics=diagnostics,
            command=("tsc", "--noEmit"),
            exit_code=1,
            raw_output_ref=f"runtime/verifier/tsc-round-{round_number}.log",
        )

    def writer(path: str, content: str) -> dict[str, object]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True, "file": path}

    result = run_runtime_repair_convergence(
        source_tools=(TS_COMMA_SOURCE_TOOL,),
        workspace=tmp_path,
        base_files={relative_path: original},
        artifact_quality_errors=(comma_error, unknown_error),
        verifier=verifier,
        writer=writer,
        allowed_paths=(relative_path,),
        max_rounds=2,
    )

    assert result.status == "coverage_gap_uncovered_diagnostics"
    assert len(result.receipts) == 1
    assert result.receipts[0].rule_id == "typescript.object_literal_missing_comma"
    assert "flightTime, landed:" in target.read_text(encoding="utf-8")
    assert result.metadata["coverage_report"]["total_diagnostics"] == 2
    assert result.metadata["coverage_gap_count"] == 1
    assert result.metadata["selected_executable_runtime_plan_diagnostic_count"] == 1
    assert result.metadata["coverage_gaps"][0]["diagnostic"]["code"] == "typescript_ts9999"
    assert result.metadata["final_coverage_report"]["total_diagnostics"] == 1
    assert result.metadata["residual_coverage_gap_count"] == 1
    assert result.metadata["residual_coverage_fully_covered"] is False
    assert result.metadata["residual_coverage_gaps"][0]["diagnostic"]["code"] == "typescript_ts9999"
    assert result.metadata["scheduler_status"] == "stuck_no_plans"
