"""Tests for the Director Runtime Repair Kernel contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.director.runtime.internal.repair_kernel import (
    PatchComposer,
    RepairDiagnostic,
    RepairOperation,
    build_go_error_string_helper_plan,
    build_java_accessor_alias_plan,
    build_rust_dependency_plan,
    build_rust_missing_binary_entrypoint_plan,
    build_typescript_object_literal_comma_plan,
    default_repair_rule_registry,
    normalize_artifact_quality_errors,
    plan_runtime_repair,
    repair_java_common_accessor_aliases_text,
    repair_typescript_object_literal_commas,
    run_runtime_repair,
    runtime_repair_source_tools,
)
from polaris.cells.director.runtime.internal.repair_kernel.contracts import (
    sha256_text,
)
from polaris.cells.director.runtime.internal.repair_kernel.rust_syntax import (
    build_rust_crate_import_rewrite_plan,
    build_rust_field_rename_suggestion_plan,
    build_rust_incompatible_copy_derive_plan,
    build_rust_line_suggestion_plan,
    build_rust_method_self_signature_plan,
    build_rust_missing_trait_derive_plan,
    build_rust_serde_derive_plan,
    build_rust_trait_import_plan,
    build_rust_unresolved_pub_use_plan,
    build_rust_unused_import_plan,
    build_rust_wrong_crate_path_plan,
)
from polaris.cells.director.runtime.public import (
    DirectorRepairPlanningResultV1,
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairPlanProbeV1,
    RepairAdvisoryV1,
    RunDirectorRepairCommandV1,
    plan_director_repair,
    query_director_repair_plan_probe,
    run_director_repair,
)


def test_go_error_string_helper_coverage_uses_typed_identifier_metadata() -> None:
    relative_path = "models/gallery.go"
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="go_compile_error",
        message="typed metadata only",
        path=relative_path,
        raw="typed metadata only",
        metadata={
            "language": "go",
            "diagnostic_kind": "undefined_identifier",
            "identifier": "errString",
        },
    )
    coverage = default_repair_rule_registry().coverage((diagnostic,)).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_go_error_string_helper_repair",
        base_files={
            relative_path: (
                'package models\n\nvar (\n    ErrDuplicateCapsule = errString("capsule id already exists")\n)\n'
            )
        },
        artifact_quality_errors=(),
        repair_diagnostics=(diagnostic,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "go.error_string_helper" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_go_error_string_helper_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_go_error_string_helper_repair"


def test_go_error_string_helper_coverage_matches_executable_runtime_plan() -> None:
    raw = "models/gallery.go:52:24: undefined: errString"
    diagnostics = normalize_artifact_quality_errors([raw])
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_go_error_string_helper_repair",
        base_files={
            "models/gallery.go": (
                'package models\n\nvar (\n    ErrDuplicateCapsule = errString("capsule id already exists")\n)\n'
            )
        },
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "go.error_string_helper" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_go_error_string_helper_repair" in coverage["items"][0]["matched_source_tools"]
    assert coverage["items"][0]["archetypes"] == ["missing_dependency"]
    assert "code_repair" in coverage["items"][0]["phases"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_go_error_string_helper_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_go_error_string_helper_rule_rejects_non_error_undefined_symbols() -> None:
    raw = "models/gallery.go:52:24: undefined: buildCapsule"
    diagnostics = normalize_artifact_quality_errors([raw])
    plan = build_go_error_string_helper_plan(
        base_files={"models/gallery.go": ('package models\n\nvar CapsuleFactory = buildCapsule("demo")\n')},
        diagnostics=diagnostics,
        mode="shadow",
    )
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()

    assert plan is None
    assert "deterministic_go_error_string_helper_repair" not in coverage["items"][0]["matched_source_tools"]


def test_go_missing_stdlib_import_covers_undefined_rand() -> None:
    raw = "engine/rules.go:106:66: undefined: rand"
    diagnostics = normalize_artifact_quality_errors([raw])
    source = (
        "package engine\n\n"
        'import (\n\t"fmt"\n\n\t"moodwheel/models"\n)\n\n'
        "func ApplyCompositionRule(m models.Mood, rng *rand.Rand) {}\n"
    )
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_go_missing_stdlib_import_repair",
        base_files={"engine/rules.go": source},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert "deterministic_go_missing_stdlib_import_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_go_missing_stdlib_import_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert '"math/rand"' in planning.composition.patches[0].content_after


def test_go_printf_stringer_rewrites_percent_s_to_existing_hex_method() -> None:
    raw = "./main.go:77:3: fmt.Printf format %s has arg c of wrong type moodwheel/models.Color"
    planning = plan_runtime_repair(
        source_tool="deterministic_go_printf_stringer_repair",
        base_files={
            "models/entity.go": (
                'package models\n\ntype Color struct{}\n\nfunc (c Color) Hex() string { return "#fff" }\n'
            ),
            "main.go": 'package main\n\nfunc render() {\n\tfmt.Printf("  [%d] %s\\n", i, c)\n}\n',
        },
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_go_printf_stringer_repair"
    assert planning.composition is not None
    assert "c.Hex()" in planning.composition.patches[0].content_after


def test_go_printf_stringer_does_not_rewrite_unrelated_letter_c() -> None:
    """Live L1-10: edit_file of arg ``c`` rewrote ``accepts`` into ``ac.Hex()cepts``."""
    raw = "./main.go:77:3: fmt.Printf format %s has arg c of wrong type moodwheel/models.Color"
    planning = plan_runtime_repair(
        source_tool="deterministic_go_printf_stringer_repair",
        base_files={
            "models/entity.go": (
                'package models\n\ntype Color struct{}\n\nfunc (c Color) Hex() string { return "#fff" }\n'
            ),
            "main.go": (
                "package main\n\n"
                "// Command moodwheel accepts a mood and an intensity.\n"
                "func render() {\n"
                '\tfmt.Printf("  [%d] %s\\n", i, c)\n'
                "}\n"
            ),
        },
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.composition is not None
    after = planning.composition.patches[0].content_after
    assert "ac.Hex()cepts" not in after
    assert "accepts" in after
    assert 'fmt.Printf("  [%d] %s\\n", i, c.Hex())' in after


def test_go_test_assertion_aligns_owned_input_without_touching_domain() -> None:
    raw = "main_test.go:124: BucketIntensity(0.34) = mid, want low"
    domain = (
        "package models\n\n"
        "type IntensityTier int\n\n"
        "const (\n\tTierLow IntensityTier = iota\n\tTierMid\n\tTierHigh\n)\n\n"
        "func (t IntensityTier) String() string {\n"
        "\tswitch t {\n"
        '\tcase TierLow:\n\t\treturn "low"\n'
        '\tcase TierMid:\n\t\treturn "mid"\n'
        '\tcase TierHigh:\n\t\treturn "high"\n'
        '\tdefault:\n\t\treturn "unknown"\n\t}\n}\n\n'
        "func BucketIntensity(intensity float64) (IntensityTier, error) {\n"
        "\tswitch {\n"
        "\tcase intensity < 0.33:\n\t\treturn TierLow, nil\n"
        "\tcase intensity < 0.66:\n\t\treturn TierMid, nil\n"
        "\tdefault:\n\t\treturn TierHigh, nil\n"
        "\t}\n}\n"
    )
    test_src = (
        "package moodwheel\n\n"
        "func TestBucketIntensity_Boundaries(t *testing.T) {\n"
        "\tcases := []struct {\n"
        "\t\tname string\n\t\tintensity float64\n\t\twantTier models.IntensityTier\n"
        "\t}{\n"
        '\t\t{"zero_is_low", 0.0, models.TierLow},\n'
        '\t\t{"just_under_mid", 0.34, models.TierLow},\n'
        '\t\t{"mid_floor", 0.35, models.TierMid},\n'
        "\t}\n}\n"
    )
    planning = plan_runtime_repair(
        source_tool="deterministic_go_test_assertion_align_repair",
        base_files={"models/state.go": domain, "main_test.go": test_src},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_go_test_assertion_align_repair"
    assert planning.composition is not None
    after = planning.composition.patches[0].content_after
    assert '{"just_under_mid", 0.32, models.TierLow}' in after
    assert '{"just_under_mid", 0.34, models.TierLow}' not in after
    assert "case intensity < 0.33:" in domain
    assert planning.composition.patches[0].path == "main_test.go"


def test_go_test_assertion_align_refuses_production_files() -> None:
    raw = "models/state.go:51: BucketIntensity(0.34) = mid, want low"
    planning = plan_runtime_repair(
        source_tool="deterministic_go_test_assertion_align_repair",
        base_files={
            "models/state.go": (
                "package models\n\nfunc BucketIntensity(intensity float64) int {\n"
                "\tswitch {\n\tcase intensity < 0.33:\n\t\treturn TierLow\n\t}\n\treturn TierMid\n}\n"
            )
        },
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert planning.plan is None


def test_go_undefined_selector_remaps_existing_bindings_without_inventing() -> None:
    raw_errors = (
        "main_test.go:44:10: undefined: models.MoodHappy",
        "main_test.go:55:27: undefined: engine.PaletteForMood",
        "main_test.go:146:23: undefined: engine.ClampIntensity",
        "main_test.go:48:10: undefined: models.MoodExcited",
    )
    diagnostics = normalize_artifact_quality_errors(list(raw_errors))
    base_files = {
        "models/entity.go": (
            "package models\n\n"
            'const (\n\tMoodJoyful Mood = "joyful"\n\tMoodCalm Mood = "calm"\n)\n\n'
            "func PaletteForMood(m Mood) {}\n"
        ),
        "engine/rules.go": (
            "package engine\n\n"
            "func ValidateIntensity(intensity float64) (float64, error) { return intensity, nil }\n"
            "func ApplyMoodRule(m models.Mood) {}\n"
        ),
        "main_test.go": (
            "package main\n\n"
            "func TestPalette() {\n"
            "\tmoods := []models.Mood{models.MoodHappy, models.MoodCalm, models.MoodExcited}\n"
            "\t_ = engine.PaletteForMood(models.MoodHappy)\n"
            "\t_, _ = engine.ClampIntensity(0.5)\n"
            '\t_, _ = svc.ComposeWheel("happy", 0.5)\n'
            "}\n"
        ),
    }
    planning = plan_runtime_repair(
        source_tool="deterministic_go_undefined_selector_repair",
        base_files=base_files,
        artifact_quality_errors=raw_errors,
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_go_undefined_selector_repair"
    assert planning.composition is not None
    repaired = planning.composition.patches[0].content_after
    assert "models.MoodJoyful" in repaired
    assert "models.MoodHappy" not in repaired
    assert "models.MoodExcited" not in repaired
    assert "models.PaletteForMood" in repaired
    assert "engine.ClampIntensity" not in repaired
    assert "engine.ValidateIntensity" in repaired
    assert '"joyful"' in repaired
    assert '"happy"' not in repaired


def test_rust_dependency_rule_builds_canonical_plan_from_diagnostics() -> None:
    cargo = '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\n'
    source = "use serde::Serialize;\nfn main() { let _ = serde_json::json!({}); }\n"
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="rust_e0432",
        message="unresolved import `serde`",
        path="src/main.rs",
        raw="error[E0432]: unresolved import `serde`",
    )

    plan = build_rust_dependency_plan(
        base_files={"./Cargo.toml": cargo, "src/main.rs": source},
        diagnostics=(diagnostic,),
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.unlinked_crate_dependency"
    assert plan.source_tool == "deterministic_rust_dependency_repair"
    assert plan.priority == 0
    assert plan.operations[0].path == "Cargo.toml"
    assert plan.operations[0].metadata["repair_kind"] == "rust_dependency"
    assert plan.operations[0].metadata["packages"] == ("serde", "serde_json")
    assert 'serde = { version = "1.0", features = ["derive"] }' in str(plan.operations[0].content)
    assert 'serde_json = "1.0"' in str(plan.operations[0].content)


def test_rust_dependency_e0432_known_crate_import_is_covered_plannable() -> None:
    result = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=("deterministic_rust_dependency_repair",),
            artifact_quality_errors=("error[E0432]: unresolved import `serde`",),
            base_files={
                "Cargo.toml": '[package]\nname = "kitchen-taste-palette"\n\n[dependencies]\n',
                "src/main.rs": ("use serde::{Deserialize, Serialize};\nfn main() { let _ = serde_json::json!({}); }\n"),
            },
        )
    )

    assert result.status == "covered_plannable"
    assert result.plannable_source_tools == ("deterministic_rust_dependency_repair",)
    assert result.items[0].status == "covered_plannable"
    assert result.items[0].patch_count == 1


def test_rust_missing_binary_entrypoint_rule_builds_create_file_plan() -> None:
    cargo = '[package]\nname = "demo-app"\nversion = "0.1.0"\n\n[[bin]]\nname = "demo-cli"\npath = "src/bin/demo.rs"\n'

    plan = build_rust_missing_binary_entrypoint_plan(
        base_files={"./Cargo.toml": cargo},
        diagnostics=(),
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.missing_binary_entrypoint"
    assert plan.source_tool == "deterministic_rust_missing_binary_entrypoint_repair"
    assert plan.priority == 1
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "write_file"
    assert operation.path == "src/bin/demo.rs"
    assert operation.before_hash == sha256_text("")
    assert operation.metadata["write_file_reason"] == "new_file_or_empty_file"
    assert operation.metadata["create_file_rollback_strategy"] == (
        "restore_empty_before_content_via_policy_gated_writer"
    )
    assert "fn main()" in str(operation.content)
    assert "demo_app binary entry point" in str(operation.content)


def test_rust_method_self_signature_rule_builds_precise_plan_from_diagnostics() -> None:
    relative_path = "src/lib.rs"
    content = "pub struct Demo;\nimpl Demo {\n    pub fn foo(&) -> i32 { 1 }\n    pub fn bar(&mut) { }\n}\n"
    errors = (
        "error: expected parameter name, found `)`\n --> src/lib.rs:3:17\n  |\n3 |     pub fn foo(&) -> i32 { 1 }",
        "error: expected parameter name, found `)`\n --> src/lib.rs:4:20\n  |\n4 |     pub fn bar(&mut) { }",
    )
    diagnostics = normalize_artifact_quality_errors(list(errors))

    plan = build_rust_method_self_signature_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.method_self_signature"
    assert plan.source_tool == "deterministic_rust_method_self_signature_repair"
    assert plan.metadata["edit_strategy"] == "span_text_replace"
    assert len(plan.operations) == 2
    assert [operation.kind for operation in plan.operations] == ["text_replace", "text_replace"]
    assert [operation.expected for operation in plan.operations] == ["(&)", "(&mut)"]
    assert [operation.replacement for operation in plan.operations] == ["(&self)", "(&mut self)"]
    assert all(operation.metadata["edit_strategy"] == "span_text_replace" for operation in plan.operations)

    composition = PatchComposer().compose({relative_path: content}, plan.operations)
    assert composition.ok is True
    assert "pub fn foo(&self)" in composition.patches[0].content_after
    assert "pub fn bar(&mut self)" in composition.patches[0].content_after


@pytest.mark.parametrize("unsafe_path", ("../src/lib.rs", "/tmp/x/src/lib.rs"))
def test_rust_method_self_signature_rule_rejects_unsafe_diagnostic_paths(unsafe_path: str) -> None:
    content = "impl Demo {\n    pub fn foo(&) -> i32 { 1 }\n}\n"
    diagnostics = normalize_artifact_quality_errors(
        [
            f"error: expected parameter name, found `)`\n --> {unsafe_path}:2:17\n  |\n2 |     pub fn foo(&) -> i32 {{ 1 }}",
        ]
    )

    plan = build_rust_method_self_signature_plan(
        base_files={unsafe_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_method_self_signature_rule_normalizes_dot_relative_paths() -> None:
    content = "impl Demo {\n    pub fn foo(&) -> i32 { 1 }\n}\n"
    diagnostics = normalize_artifact_quality_errors(
        [
            "error: expected parameter name, found `)`\n --> ./src/lib.rs:2:17\n  |\n2 |     pub fn foo(&) -> i32 { 1 }",
        ]
    )

    plan = build_rust_method_self_signature_plan(
        base_files={"./src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.operations[0].path == "src/lib.rs"


def test_rust_method_self_signature_coverage_matches_executable_runtime_plan() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "error: expected parameter name, found `)`\n --> src/lib.rs:2:17\n  |\n2 |     pub fn foo(&) -> i32 { 1 }",
        ]
    )
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_method_self_signature_repair",
        base_files={"src/lib.rs": "impl Demo {\n    pub fn foo(&) -> i32 { 1 }\n}\n"},
        artifact_quality_errors=(diagnostics[0].raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.method_self_signature" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_method_self_signature_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_method_self_signature_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_serde_derive_rule_extends_existing_derive_with_span_metadata() -> None:
    relative_path = "src/models.rs"
    content = "#[derive(Debug, Clone)]\npub struct Recipe { name: String }\n"
    raw = _rust_serde_derive_error()
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_serde_derive_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.serde_derive"
    assert plan.source_tool == "deterministic_rust_serde_derive_repair"
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "#[derive(Debug, Clone)]\n"
    assert operation.replacement == "#[derive(Debug, Clone, serde::Serialize)]\n"
    assert operation.metadata["repair_kind"] == "rust_serde_derive"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["unique_context"] == "#[derive(Debug, Clone)]\n"
    assert operation.metadata["traits_added"] == ("serde::Serialize",)

    runtime_planning = plan_runtime_repair(
        source_tool="deterministic_rust_serde_derive_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    composition = runtime_planning.composition
    assert composition is not None
    assert composition.ok is True
    assert "#[derive(Debug, Clone, serde::Serialize)]" in composition.patches[0].content_after


def test_rust_serde_derive_rule_inserts_derive_by_replacing_declaration_line() -> None:
    relative_path = "src/models.rs"
    content = "pub enum Recipe { Soup }\n"
    diagnostics = normalize_artifact_quality_errors([_rust_serde_derive_error(trait="Deserialize")])

    plan = build_rust_serde_derive_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.expected == "pub enum Recipe { Soup }\n"
    assert operation.replacement == "#[derive(serde::Deserialize)]\npub enum Recipe { Soup }\n"
    assert operation.metadata["derive_line_existing"] is False


def test_rust_serde_derive_coverage_matches_executable_runtime_plan() -> None:
    raw = _rust_serde_derive_error()
    diagnostics = normalize_artifact_quality_errors([raw])
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_serde_derive_repair",
        base_files={"src/models.rs": "pub struct Recipe { name: String }\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.serde_derive" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_serde_derive_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_serde_derive_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_serde_derive_runtime_binding_executes_public_edit(tmp_path: Path) -> None:
    relative_path = "src/models.rs"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = "#[derive(Debug)]\npub struct Recipe { name: String }\n"
    target.write_text(content, encoding="utf-8")
    edit_calls: list[tuple[str, str, str]] = []
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, replacement: str) -> dict[str, object]:
        write_calls.append((path, replacement))
        return {"ok": False, "file": path, "error": "write_file should not be used"}

    def editor(operation: RepairOperation) -> dict[str, object]:
        before = target.read_text(encoding="utf-8")
        assert operation.expected is not None
        assert operation.replacement is not None
        target.write_text(before.replace(operation.expected, operation.replacement, 1), encoding="utf-8")
        edit_calls.append((operation.path, operation.expected, operation.replacement))
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-serde-derive",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_serde_derive_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(_rust_serde_derive_error(),),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert [receipt.source_tool for receipt in result.receipts] == ["deterministic_rust_serde_derive_repair"]
    assert edit_calls == [
        (
            relative_path,
            "#[derive(Debug)]\n",
            "#[derive(Debug, serde::Serialize)]\n",
        )
    ]
    assert write_calls == []
    assert "#[derive(Debug, serde::Serialize)]" in target.read_text(encoding="utf-8")


def test_rust_missing_trait_derive_rule_extends_existing_derive_with_span_metadata() -> None:
    relative_path = "src/lib.rs"
    content = "#[derive(Debug, Clone)]\npub struct Widget { id: u64 }\n"
    raw = _rust_missing_trait_derive_error(trait="Hash")
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_missing_trait_derive_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.missing_trait_derive"
    assert plan.source_tool == "deterministic_rust_derive_repair"
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.span_start == 0
    assert operation.span_end == len("#[derive(Debug, Clone)]\n")
    assert operation.expected == "#[derive(Debug, Clone)]\n"
    assert operation.replacement == "#[derive(Debug, Clone, Hash)]\n"
    assert operation.metadata["repair_kind"] == "rust_missing_trait_derive"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["unique_context"] == "#[derive(Debug, Clone)]\npub struct Widget { id: u64 }\n"
    assert operation.metadata["traits_added"] == ("Hash",)

    runtime_planning = plan_runtime_repair(
        source_tool="deterministic_rust_derive_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    composition = runtime_planning.composition
    assert composition is not None
    assert composition.ok is True
    assert "#[derive(Debug, Clone, Hash)]" in composition.patches[0].content_after


def test_rust_missing_trait_derive_rule_does_not_take_serde_diagnostics() -> None:
    diagnostics = normalize_artifact_quality_errors([_rust_serde_derive_error()])

    plan = build_rust_missing_trait_derive_plan(
        base_files={"src/models.rs": "#[derive(Debug)]\npub struct Recipe { name: String }\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_missing_trait_derive_rule_covers_enum_ord_with_prerequisites() -> None:
    """L1-05 r92: BTreeMap keys are enums; Ord requires PartialOrd/Eq companions."""

    relative_path = "src/models/flavor.rs"
    content = "#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]\npub enum Flavor {\n    Sweet,\n}\n"
    raw = _rust_missing_trait_derive_error(
        symbol="Flavor",
        trait="Ord",
        path=relative_path,
        line=2,
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_missing_trait_derive_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.source_tool == "deterministic_rust_derive_repair"
    operation = plan.operations[0]
    assert operation.path == relative_path
    assert operation.metadata["item_kind"] == "enum"
    # Existing PartialEq/Eq stay; Ord expansion also adds PartialOrd.
    assert set(operation.metadata["traits_added"]) >= {"Ord", "PartialOrd"}
    assert "Ord" in (operation.replacement or "")
    assert "PartialOrd" in (operation.replacement or "")


def test_rust_missing_trait_derive_runtime_binding_executes_public_edit(tmp_path: Path) -> None:
    relative_path = "src/lib.rs"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = "#[derive(Debug)]\npub struct Widget { id: u64 }\n"
    target.write_text(content, encoding="utf-8")
    edit_calls: list[tuple[str, str, str]] = []
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, replacement: str) -> dict[str, object]:
        write_calls.append((path, replacement))
        return {"ok": False, "file": path, "error": "write_file should not be used"}

    def editor(operation: RepairOperation) -> dict[str, object]:
        before = target.read_text(encoding="utf-8")
        assert operation.expected is not None
        assert operation.replacement is not None
        target.write_text(before.replace(operation.expected, operation.replacement, 1), encoding="utf-8")
        edit_calls.append((operation.path, operation.expected, operation.replacement))
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-missing-trait-derive",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_derive_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(_rust_missing_trait_derive_error(),),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert [receipt.source_tool for receipt in result.receipts] == ["deterministic_rust_derive_repair"]
    assert edit_calls == [
        (
            relative_path,
            "#[derive(Debug)]\n",
            "#[derive(Debug, Eq, PartialEq)]\n",
        )
    ]
    assert write_calls == []
    assert "#[derive(Debug, Eq, PartialEq)]" in target.read_text(encoding="utf-8")


def test_rust_copy_derive_rule_removes_copy_with_text_replace(
    derive_line: str,
    replacement: str,
) -> None:
    relative_path = "src/lib.rs"
    content = f"{derive_line}pub struct Demo {{ value: String }}\n"
    raw = _rust_copy_derive_error()
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_incompatible_copy_derive_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.incompatible_copy_derive"
    assert plan.source_tool == "deterministic_rust_incompatible_copy_derive_repair"
    assert plan.priority == 1
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == derive_line
    assert operation.replacement == replacement
    assert operation.metadata["repair_kind"] == "rust_incompatible_copy_derive"
    assert operation.metadata["edit_strategy"] == "text_replace"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["line_number"] == 2
    assert operation.metadata["derive_line_number"] == 1
    assert operation.metadata["unique_context"] is True

    runtime_planning = plan_runtime_repair(
        source_tool="deterministic_rust_incompatible_copy_derive_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    composition = runtime_planning.composition
    assert composition is not None
    assert composition.ok is True
    assert replacement in composition.patches[0].content_after
    assert "Copy" not in composition.patches[0].content_after


@pytest.mark.parametrize(
    ("diagnostic_path", "base_files"),
    (
        ("", {"src/lib.rs": "#[derive(Copy)]\npub struct Demo { value: String }\n"}),
        (
            "/tmp/project/src/lib.rs",
            {"/tmp/project/src/lib.rs": "#[derive(Copy)]\npub struct Demo { value: String }\n"},
        ),
        ("../src/lib.rs", {"../src/lib.rs": "#[derive(Copy)]\npub struct Demo { value: String }\n"}),
        ("src/lib.txt", {"src/lib.txt": "#[derive(Copy)]\npub struct Demo { value: String }\n"}),
        ("src/lib.rs", {"src/other.rs": "#[derive(Copy)]\npub struct Demo { value: String }\n"}),
    ),
)
def test_rust_copy_derive_rule_rejects_unsafe_or_untracked_paths(
    diagnostic_path: str,
    base_files: dict[str, str],
) -> None:
    diagnostics = normalize_artifact_quality_errors([_rust_copy_derive_error(path=diagnostic_path, line=2)])

    plan = build_rust_incompatible_copy_derive_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_copy_derive_rule_does_not_plan_without_copy_token() -> None:
    content = "#[derive(Clone)]\npub struct Demo { value: String }\n"
    diagnostics = normalize_artifact_quality_errors([_rust_copy_derive_error()])

    plan = build_rust_incompatible_copy_derive_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_copy_derive_rule_rejects_non_unique_expected_line() -> None:
    content = (
        "#[derive(Clone, Copy)]\n"
        "pub struct Demo { value: String }\n"
        "#[derive(Clone, Copy)]\n"
        "pub struct Other { value: String }\n"
    )
    diagnostics = normalize_artifact_quality_errors([_rust_copy_derive_error()])

    plan = build_rust_incompatible_copy_derive_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_copy_derive_coverage_matches_executable_runtime_plan() -> None:
    raw = _rust_copy_derive_error()
    diagnostics = normalize_artifact_quality_errors([raw])
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_incompatible_copy_derive_repair",
        base_files={"src/lib.rs": "#[derive(Clone, Copy)]\npub struct Demo { value: String }\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert "deterministic_rust_post_repair" not in runtime_repair_source_tools()
    assert "deterministic_rust_derive_repair" in runtime_repair_source_tools()
    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.incompatible_copy_derive" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_incompatible_copy_derive_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_incompatible_copy_derive_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_copy_derive_runtime_binding_executes_public_edit(tmp_path: Path) -> None:
    relative_path = "src/lib.rs"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = "#[derive(Debug, Clone, Copy)]\npub struct Demo { value: String }\n"
    target.write_text(content, encoding="utf-8")
    edit_calls: list[tuple[str, str, str]] = []
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, replacement: str) -> dict[str, object]:
        write_calls.append((path, replacement))
        return {"ok": False, "file": path, "error": "write_file should not be used"}

    def editor(operation: RepairOperation) -> dict[str, object]:
        before = target.read_text(encoding="utf-8")
        assert operation.expected is not None
        assert operation.replacement is not None
        target.write_text(before.replace(operation.expected, operation.replacement, 1), encoding="utf-8")
        edit_calls.append((operation.path, operation.expected, operation.replacement))
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-copy-derive",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_incompatible_copy_derive_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(_rust_copy_derive_error(),),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert [receipt.source_tool for receipt in result.receipts] == [
        "deterministic_rust_incompatible_copy_derive_repair"
    ]
    assert edit_calls == [
        (
            relative_path,
            "#[derive(Debug, Clone, Copy)]\n",
            "#[derive(Debug, Clone)]\n",
        )
    ]
    assert write_calls == []
    assert "#[derive(Debug, Clone)]" in target.read_text(encoding="utf-8")


def test_rust_wrong_crate_path_rule_replaces_use_line_with_span_metadata() -> None:
    relative_path = "src/lib.rs"
    content = "    use crate::recipe::Recipe;\nfn main() {}\n"
    raw = _rust_wrong_crate_path_error()
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_wrong_crate_path_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.wrong_crate_path"
    assert plan.source_tool == "deterministic_rust_wrong_crate_path_repair"
    assert plan.depends_on == ("rust.unlinked_crate_dependency",)
    assert plan.priority == 0
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "    use crate::recipe::Recipe;\n"
    assert operation.replacement == "    use crate::models::recipe::Recipe;\n"
    assert operation.metadata["repair_kind"] == "rust_wrong_crate_path"
    assert operation.metadata["edit_strategy"] == "text_replace"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["line_number"] == 1
    assert operation.metadata["suggestion"] == "use crate::models::recipe::Recipe;"
    assert operation.metadata["unique_context"] is True

    runtime_planning = plan_runtime_repair(
        source_tool="deterministic_rust_wrong_crate_path_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    composition = runtime_planning.composition
    assert composition is not None
    assert composition.ok is True
    assert "    use crate::models::recipe::Recipe;" in composition.patches[0].content_after


@pytest.mark.parametrize(
    ("diagnostic_path", "base_files"),
    (
        ("", {"src/lib.rs": "use crate::recipe::Recipe;\n"}),
        ("/tmp/project/src/lib.rs", {"/tmp/project/src/lib.rs": "use crate::recipe::Recipe;\n"}),
        ("../src/lib.rs", {"../src/lib.rs": "use crate::recipe::Recipe;\n"}),
        ("src/lib.txt", {"src/lib.txt": "use crate::recipe::Recipe;\n"}),
        ("src/lib.rs", {"src/other.rs": "use crate::recipe::Recipe;\n"}),
    ),
)
def test_rust_wrong_crate_path_rule_rejects_unsafe_or_untracked_paths(
    diagnostic_path: str,
    base_files: dict[str, str],
) -> None:
    path_for_raw = diagnostic_path or "/tmp/empty.rs"
    diagnostics = normalize_artifact_quality_errors([_rust_wrong_crate_path_error(path=path_for_raw)])

    plan = build_rust_wrong_crate_path_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_wrong_crate_path_rule_rejects_non_use_target_line() -> None:
    content = "let recipe = Recipe::new();\n"
    diagnostics = normalize_artifact_quality_errors([_rust_wrong_crate_path_error()])

    plan = build_rust_wrong_crate_path_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_wrong_crate_path_rule_rejects_non_unique_expected_line() -> None:
    content = "use crate::recipe::Recipe;\nuse crate::recipe::Recipe;\n"
    diagnostics = normalize_artifact_quality_errors([_rust_wrong_crate_path_error()])

    plan = build_rust_wrong_crate_path_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_wrong_crate_path_rule_rejects_invalid_suggestion() -> None:
    content = "use crate::recipe::Recipe;\n"
    diagnostics = normalize_artifact_quality_errors(
        [
            _rust_wrong_crate_path_error(
                suggestion="crate::models::recipe::Recipe",
            ),
            _rust_wrong_crate_path_error(
                suggestion="use crate::models::recipe::Recipe",
            ),
        ]
    )

    plan = build_rust_wrong_crate_path_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_wrong_crate_path_rule_composes_multiple_same_file_text_replacements() -> None:
    relative_path = "src/lib.rs"
    content = "use crate::recipe::Recipe;\nuse crate::pantry::Pantry;\nfn main() {}\n"
    raw = _rust_wrong_crate_path_error(
        path=relative_path,
        line=1,
        original="use crate::recipe::Recipe;",
        suggestion="use crate::models::recipe::Recipe;",
    ) + _rust_wrong_crate_path_error(
        path=relative_path,
        line=2,
        original="use crate::pantry::Pantry;",
        suggestion="use crate::models::pantry::Pantry;",
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_wrong_crate_path_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert [operation.kind for operation in plan.operations] == ["text_replace", "text_replace"]
    assert len(plan.operations) == 2
    runtime_planning = plan_runtime_repair(
        source_tool="deterministic_rust_wrong_crate_path_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    composition = runtime_planning.composition
    assert composition is not None
    assert composition.ok is True
    assert composition.patches[0].content_after == (
        "use crate::models::recipe::Recipe;\nuse crate::models::pantry::Pantry;\nfn main() {}\n"
    )


def test_rust_crate_import_rewrite_rule_uses_span_based_editor_operations(tmp_path: Path) -> None:
    cargo = '[package]\nname = "garden-kit"\nversion = "0.1.0"\n'
    main_path = "src/main.rs"
    lib_path = "src/lib.rs"
    main_content = "use garden_app_kit::models::Recipe;\nextern crate garden_app_kit;\nfn main() {}\n"
    raw = "error[E0433]: failed to resolve: cannot find crate `garden_app_kit`\n --> src/main.rs:1:5\n"
    base_files = {
        "Cargo.toml": cargo,
        main_path: main_content,
        lib_path: "pub mod models;\n",
    }
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_crate_import_rewrite_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.crate_import_rewrite"
    assert plan.source_tool == "deterministic_rust_crate_import_rewrite_repair"
    assert plan.depends_on == ("rust.unlinked_crate_dependency",)
    assert plan.metadata["span_based"] is True
    assert [operation.kind for operation in plan.operations] == ["text_replace", "text_replace"]
    assert {operation.metadata["match_kind"] for operation in plan.operations} == {
        "crate_prefix",
        "extern_crate",
    }
    assert all(operation.expected for operation in plan.operations)
    assert all(operation.metadata["expected_context_after"] for operation in plan.operations)

    runtime_planning = plan_runtime_repair(
        source_tool="deterministic_rust_crate_import_rewrite_repair",
        base_files=base_files,
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    composition = runtime_planning.composition
    assert composition is not None
    assert composition.ok is True
    assert "use garden_kit::models::Recipe;" in composition.patches[0].content_after
    assert "extern crate garden_kit;" in composition.patches[0].content_after

    target = tmp_path / main_path
    target.parent.mkdir(parents=True)
    target.write_text(main_content, encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text(cargo, encoding="utf-8")
    (tmp_path / lib_path).write_text("pub mod models;\n", encoding="utf-8")
    edited_operations: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        raise AssertionError("span-based Rust crate import rewrite must use edit_file")

    def editor(operation: RepairOperation) -> dict[str, object]:
        edit_target = tmp_path / operation.path
        current = edit_target.read_text(encoding="utf-8")
        start = int(operation.span_start or 0)
        end = int(operation.span_end or 0)
        assert current[start:end] == operation.expected
        edit_target.write_text(
            current[:start] + str(operation.replacement or "") + current[end:],
            encoding="utf-8",
        )
        edited_operations.append(operation.operation_id)
        return {"ok": True}

    run = run_runtime_repair(
        source_tool="deterministic_rust_crate_import_rewrite_repair",
        workspace=tmp_path,
        base_files=base_files,
        artifact_quality_errors=(raw,),
        writer=writer,
        editor=editor,
        allowed_paths=(main_path, lib_path, "Cargo.toml"),
    )

    assert run.ok is True
    assert len(edited_operations) == 2
    assert target.read_text(encoding="utf-8") == (
        "use garden_kit::models::Recipe;\nextern crate garden_kit;\nfn main() {}\n"
    )
    assert run.execution_result is not None
    records = run.execution_result.receipt.metadata["execution_records"]
    assert records[0]["operation"] == "edit_file"
    assert records[0]["span_based"] is True


def test_rust_crate_import_legacy_source_tool_executes_through_runtime_editor(tmp_path: Path) -> None:
    cargo = '[package]\nname = "kitchen-flavor-palette"\nversion = "0.1.0"\n'
    main_path = "src/main.rs"
    main_content = (
        "use kitchen_palette::engine::generate_palette;\n"
        "fn main() {\n"
        "    let _ = kitchen_palette::models::Recipe::default();\n"
        "    generate_palette();\n"
        "}\n"
    )
    raw = "cargo check failed: error[E0433]: cannot find module or crate `kitchen_palette` in this scope"
    base_files = {
        "Cargo.toml": cargo,
        main_path: main_content,
    }
    target = tmp_path / main_path
    target.parent.mkdir(parents=True)
    target.write_text(main_content, encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text(cargo, encoding="utf-8")
    edited_operations: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        raise AssertionError("span-based Rust crate import repair must use edit_file")

    def editor(operation: RepairOperation) -> dict[str, object]:
        edit_target = tmp_path / operation.path
        current = edit_target.read_text(encoding="utf-8")
        start = int(operation.span_start or 0)
        end = int(operation.span_end or 0)
        assert current[start:end] == operation.expected
        edit_target.write_text(
            current[:start] + str(operation.replacement or "") + current[end:],
            encoding="utf-8",
        )
        edited_operations.append(operation.operation_id)
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    run = run_runtime_repair(
        source_tool="deterministic_rust_crate_import_repair",
        workspace=tmp_path,
        base_files=base_files,
        artifact_quality_errors=(raw,),
        writer=writer,
        editor=editor,
        allowed_paths=(main_path, "Cargo.toml"),
    )

    repaired = target.read_text(encoding="utf-8")
    assert run.ok is True
    assert edited_operations
    assert "use kitchen_flavor_palette::engine::generate_palette;" in repaired
    assert "kitchen_flavor_palette::models::Recipe" in repaired
    assert "kitchen_palette::" not in repaired
    assert run.execution_result is not None
    assert {record["operation"] for record in run.execution_result.receipt.metadata["execution_records"]} == {
        "edit_file"
    }


def test_rust_crate_import_legacy_source_tool_declines_declared_dependencies() -> None:
    cargo = '[package]\nname = "kitchen-flavor-palette"\nversion = "0.1.0"\n\n[dependencies]\nkitchen_palette = "1"\n'
    main_content = "use kitchen_palette::engine::generate_palette;\n"
    raw = "cargo check failed: error[E0433]: cannot find module or crate `kitchen_palette` in this scope"

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_crate_import_repair",
        base_files={
            "Cargo.toml": cargo,
            "src/main.rs": main_content,
        },
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert planning.error_code is None
    assert planning.plan is None
    assert planning.composition is None


def test_rust_wrong_crate_path_runtime_binding_executes_public_edit(tmp_path: Path) -> None:
    relative_path = "src/lib.rs"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    content = "use crate::recipe::Recipe;\nfn main() {}\n"
    target.write_text(content, encoding="utf-8")
    edit_calls: list[tuple[str, str, str]] = []
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, replacement: str) -> dict[str, object]:
        write_calls.append((path, replacement))
        return {"ok": False, "file": path, "error": "write_file should not be used"}

    def editor(operation: RepairOperation) -> dict[str, object]:
        before = target.read_text(encoding="utf-8")
        assert operation.expected is not None
        assert operation.replacement is not None
        target.write_text(before.replace(operation.expected, operation.replacement, 1), encoding="utf-8")
        edit_calls.append((operation.path, operation.expected, operation.replacement))
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-wrong-crate-path",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_wrong_crate_path_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(_rust_wrong_crate_path_error(),),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert [receipt.source_tool for receipt in result.receipts] == ["deterministic_rust_wrong_crate_path_repair"]
    assert edit_calls == [
        (
            relative_path,
            "use crate::recipe::Recipe;\n",
            "use crate::models::recipe::Recipe;\n",
        )
    ]
    assert write_calls == []
    assert "use crate::models::recipe::Recipe;" in target.read_text(encoding="utf-8")


def test_rust_unresolved_pub_use_rule_deletes_flat_pub_use_with_span_metadata() -> None:
    relative_path = "src/lib.rs"
    content = "pub use foo::Missing;\npub fn keep() {}\n"
    raw = (
        "error[E0432]: unresolved import `foo::Missing`\n"
        " --> src/lib.rs:1:9\n"
        "  |\n"
        "1 | pub use foo::Missing;\n"
        "  |         ^^^^^^^^^^^^ no `Missing` in `foo`"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_unresolved_pub_use_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.unresolved_pub_use"
    assert plan.source_tool == "deterministic_rust_unresolved_pub_use_repair"
    assert plan.metadata["span_based"] is True
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "pub use foo::Missing;\n"
    assert operation.replacement == ""
    assert operation.before_hash == sha256_text(content)
    assert operation.metadata["span_based"] is True
    assert operation.metadata["symbols_removed"] == ("Missing",)
    assert operation.metadata["unique_context"] == "pub use foo::Missing;\n"

    composition = PatchComposer().compose({relative_path: content}, plan.operations)
    assert composition.ok is True
    assert composition.patches[0].content_after == "pub fn keep() {}\n"


def test_rust_unresolved_pub_use_rule_removes_only_missing_group_symbol() -> None:
    relative_path = "src/lib.rs"
    content = "pub use foo::{A, Missing, B};\npub fn keep() {}\n"
    raw = (
        "error[E0432]: unresolved import `foo::Missing`\n"
        " --> src/lib.rs:1:18\n"
        "  |\n"
        "1 | pub use foo::{A, Missing, B};\n"
        "  |                  ^^^^^^^ no `Missing` in `foo`"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_unresolved_pub_use_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.expected == "pub use foo::{A, Missing, B};\n"
    assert operation.replacement == "pub use foo::{A, B};\n"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["symbols_removed"] == ("Missing",)

    composition = PatchComposer().compose({relative_path: content}, plan.operations)
    assert composition.ok is True
    assert "pub use foo::{A, B};" in composition.patches[0].content_after
    assert "Missing" not in composition.patches[0].content_after


def test_rust_unresolved_pub_use_rule_does_not_plan_without_matching_symbol() -> None:
    content = "pub use foo::{A, Missing, B};\n"
    diagnostics = normalize_artifact_quality_errors(
        [
            "error[E0432]: unresolved import `foo::Other`\n"
            " --> src/lib.rs:1:18\n"
            "  |\n"
            "1 | pub use foo::{A, Missing, B};\n"
            "  |                  ^^^^^ no `Other` in `foo`",
        ]
    )

    plan = build_rust_unresolved_pub_use_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


@pytest.mark.parametrize("unsafe_path", ("../src/lib.rs", "/tmp/demo/src/lib.rs"))
def test_rust_unresolved_pub_use_rule_rejects_unsafe_paths(unsafe_path: str) -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "error[E0432]: unresolved import `foo::Missing`\n"
            f" --> {unsafe_path}:1:9\n"
            "  |\n"
            "1 | pub use foo::Missing;\n"
            "  |         ^^^^^^^^^^^^ no `Missing` in `foo`",
        ]
    )

    plan = build_rust_unresolved_pub_use_plan(
        base_files={unsafe_path: "pub use foo::Missing;\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_unresolved_pub_use_coverage_matches_executable_runtime_plan() -> None:
    raw = (
        "error[E0432]: unresolved import `foo::Missing`\n"
        " --> src/lib.rs:1:9\n"
        "  |\n"
        "1 | pub use foo::Missing;\n"
        "  |         ^^^^^^^^^^^^ no `Missing` in `foo`"
    )
    diagnostics = normalize_artifact_quality_errors([raw])
    coverage = default_repair_rule_registry().coverage(diagnostics).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_unresolved_pub_use_repair",
        base_files={"src/lib.rs": "pub use foo::Missing;\npub fn keep() {}\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.unresolved_pub_use" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_unresolved_pub_use_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_unresolved_pub_use_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_unused_import_rule_removes_group_symbol_with_text_replace() -> None:
    relative_path = "src/lib.rs"
    content = "use foo::{A, B, C};\npub fn keep() {}\n"
    raw = "warning: unused import: `B`\n --> src/lib.rs:1:14\n  |\n1 | use foo::{A, B, C};\n  |              ^\n"
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_unused_import_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.unused_import"
    assert plan.source_tool == "deterministic_rust_unused_import_repair"
    assert plan.priority == 2
    assert plan.metadata["span_based"] is True
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "use foo::{A, B, C};\n"
    assert operation.replacement == "use foo::{A, C};\n"
    assert operation.before_hash == sha256_text(content)
    assert operation.metadata["repair_kind"] == "rust_unused_import"
    assert operation.metadata["edit_strategy"] == "text_replace"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["line_number"] == 1
    assert operation.metadata["symbol"] == "B"
    assert operation.metadata["unique_context"] is True

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_unused_import_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_unused_import_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert "use foo::{A, C};\npub fn keep() {}" in planning.composition.patches[0].content_after


def test_rust_unused_import_rule_comments_single_import_preserving_newline() -> None:
    content = "use foo::A;\r\npub fn keep() {}\r\n"
    raw = "warning: unused import: `A`\n --> src/lib.rs:1:10\n  |\n1 | use foo::A;\n"

    plan = build_rust_unused_import_plan(
        base_files={"src/lib.rs": content},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )

    assert plan is not None
    assert plan.operations[0].kind == "text_replace"
    assert plan.operations[0].replacement == "// [repair-unused] use foo::A;\r\n"


@pytest.mark.parametrize(
    "unsafe_path",
    ("", "../src/lib.rs", "/tmp/demo/src/lib.rs", "src/lib.txt", "src/missing.rs"),
)
def test_rust_unused_import_rule_rejects_unsafe_or_untracked_paths(unsafe_path: str) -> None:
    location = "" if unsafe_path == "" else f" --> {unsafe_path}:1:10\n"
    raw = f"warning: unused import: `A`\n{location}  |\n1 | use foo::A;\n"

    plan = build_rust_unused_import_plan(
        base_files={"src/lib.rs": "use foo::A;\n", "src/lib.txt": "use foo::A;\n"},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )

    assert plan is None


def test_rust_unused_import_rule_skips_non_unique_expected_line() -> None:
    content = "use foo::A;\nuse foo::A;\n"
    raw = "warning: unused import: `A`\n --> src/lib.rs:1:10\n  |\n1 | use foo::A;\n"

    plan = build_rust_unused_import_plan(
        base_files={"src/lib.rs": content},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )

    assert plan is None


def test_rust_unused_import_rule_composes_multiple_patches_in_same_file() -> None:
    content = "use foo::{A, B};\nuse bar::C;\npub fn keep() {}\n"
    raw = (
        "warning: unused import: `B`\n"
        " --> src/lib.rs:1:14\n"
        "  |\n"
        "1 | use foo::{A, B};\n"
        "warning: unused import: `C`\n"
        " --> src/lib.rs:2:10\n"
        "  |\n"
        "2 | use bar::C;\n"
    )

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_unused_import_repair",
        base_files={"src/lib.rs": content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert planning.plan is not None
    assert len(planning.plan.operations) == 2
    assert all(operation.kind == "text_replace" for operation in planning.plan.operations)
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert planning.composition.patches[0].content_after == (
        "use foo::{A};\n// [repair-unused] use bar::C;\npub fn keep() {}\n"
    )


def test_rust_unused_import_coverage_matches_executable_runtime_plan() -> None:
    raw = "warning: unused import: `A`\n --> src/lib.rs:1:10\n  |\n1 | use foo::A;\n"
    coverage = default_repair_rule_registry().coverage(normalize_artifact_quality_errors([raw])).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_unused_import_repair",
        base_files={"src/lib.rs": "use foo::A;\npub fn keep() {}\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.unused_import" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_unused_import_repair" in coverage["items"][0]["matched_source_tools"]
    assert coverage["items"][0]["archetypes"] == ["generated_residue"]
    assert coverage["items"][0]["phases"] == ["code_repair"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_unused_import_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_trait_import_rule_builds_precise_text_replace_plan_and_runs_with_editor(
    tmp_path: Path,
) -> None:
    relative_path = "src/lib.rs"
    content = "\n//! crate docs\n#![allow(dead_code)]\nuse crate::bar::Bar;\n\npub fn run() {}\n"
    raw = (
        "error[E0599]: no method named `render` found for struct `Widget` in the current scope\n"
        " --> src/lib.rs:6:12\n"
        "  |\n"
        "help: trait `Renderable` which provides `render` is implemented but not in scope; "
        "perhaps add a use for it:\n"
        "  |\n"
        "1 + use crate::render::Renderable;\n"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_trait_import_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.trait_import"
    assert plan.source_tool == "deterministic_rust_trait_import_repair"
    assert plan.metadata["span_based"] is True
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "pub fn run() {}\n"
    assert operation.replacement == "use crate::render::Renderable;\npub fn run() {}\n"
    assert operation.before_hash == sha256_text(content)
    assert operation.metadata["repair_kind"] == "rust_trait_import"
    assert operation.metadata["edit_strategy"] == "text_replace"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["import_line"] == "use crate::render::Renderable;"
    assert operation.metadata["insert_index"] == 5
    assert operation.metadata["unique_context"] is True

    planning = plan_runtime_repair(
        source_tool="deterministic_rust_trait_import_repair",
        base_files={relative_path: content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    assert planning.plan is not None
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert "use crate::render::Renderable;\npub fn run() {}" in planning.composition.patches[0].content_after

    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    edits: list[tuple[str, str, str]] = []
    writes: list[tuple[str, str]] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append((path, updated))
        raise AssertionError("rust trait imports must execute through editor")

    def editor(edit_operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert edit_operation.span_start is not None
        assert edit_operation.span_end is not None
        assert edit_operation.expected is not None
        assert edit_operation.replacement is not None
        assert current[edit_operation.span_start : edit_operation.span_end] == edit_operation.expected
        updated = current[: edit_operation.span_start] + edit_operation.replacement + current[edit_operation.span_end :]
        target.write_text(updated, encoding="utf-8")
        edits.append((edit_operation.path, edit_operation.expected, edit_operation.replacement))
        return {
            "ok": True,
            "file": edit_operation.path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "edit_file",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-trait-import",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_trait_import_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(raw,),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert result.error_code is None
    assert writes == []
    assert edits == [(relative_path, "pub fn run() {}\n", "use crate::render::Renderable;\npub fn run() {}\n")]
    assert "use crate::render::Renderable;\npub fn run() {}" in target.read_text(encoding="utf-8")
    assert result.receipts[0].source_tool == "deterministic_rust_trait_import_repair"


def test_rust_trait_import_rule_skips_existing_import() -> None:
    content = "use crate::render::Renderable;\npub fn run() {}\n"
    raw = (
        "error[E0599]: no method named `render` found\n"
        " --> src/lib.rs:2:12\n"
        "help: trait `Renderable` which provides `render` is implemented but not in scope; "
        "perhaps add a use for it:\n"
        "1 + use crate::render::Renderable;\n"
    )

    plan = build_rust_trait_import_plan(
        base_files={"src/lib.rs": content},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )

    assert plan is None


@pytest.mark.parametrize(
    "unsafe_path",
    ("", "../src/lib.rs", "/tmp/demo/src/lib.rs", "src/lib.txt", "src/missing.rs"),
)
def test_rust_trait_import_rule_rejects_unsafe_or_untracked_paths(unsafe_path: str) -> None:
    location = "" if unsafe_path == "" else f" --> {unsafe_path}:1:12\n"
    raw = (
        "error[E0599]: no method named `render` found\n"
        f"{location}"
        "help: trait `Renderable` which provides `render` is implemented but not in scope; "
        "perhaps add a use for it:\n"
        "1 + use crate::render::Renderable;\n"
    )

    plan = build_rust_trait_import_plan(
        base_files={"src/lib.rs": "pub fn run() {}\n", "src/lib.txt": "pub fn run() {}\n"},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )

    assert plan is None


def test_rust_trait_import_rule_skips_non_unique_anchor() -> None:
    content = "pub fn run() {}\npub fn run() {}\n"
    raw = (
        "error[E0599]: no method named `render` found\n"
        " --> src/lib.rs:1:12\n"
        "help: trait `Renderable` which provides `render` is implemented but not in scope; "
        "perhaps add a use for it:\n"
        "1 + use crate::render::Renderable;\n"
    )

    plan = build_rust_trait_import_plan(
        base_files={"src/lib.rs": content},
        diagnostics=normalize_artifact_quality_errors([raw]),
        mode="shadow",
    )

    assert plan is None


def test_rust_trait_import_coverage_matches_executable_runtime_plan() -> None:
    raw = (
        "error[E0599]: no method named `render` found\n"
        " --> src/lib.rs:1:12\n"
        "help: trait `Renderable` which provides `render` is implemented but not in scope; "
        "perhaps add a use for it:\n"
        "1 + use crate::render::Renderable;\n"
    )
    coverage = default_repair_rule_registry().coverage(normalize_artifact_quality_errors([raw])).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_trait_import_repair",
        base_files={"src/lib.rs": "pub fn run() {}\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.trait_import" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_trait_import_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_trait_import_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_line_suggestion_rule_builds_precise_text_replace_plan_and_runs_with_editor(
    tmp_path: Path,
) -> None:
    relative_path = "src/lib.rs"
    content = "fn takes(_: &String) {}\nfn main() {\n    takes(value)\n}\n"
    raw = (
        "error[E0308]: mismatched types\n"
        " --> src/lib.rs:3:11\n"
        "  |\n"
        "3 |     takes(value)\n"
        "  |           ^^^^^ expected `&String`, found `String`\n"
        "help: consider borrowing here\n"
        "  |\n"
        "3 |     takes(&value)"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_line_suggestion_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.line_suggestion"
    assert plan.source_tool == "deterministic_rust_line_suggestion_repair"
    assert plan.metadata["span_based"] is True
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "    takes(value)\n"
    assert operation.replacement == "    takes(&value)\n"
    assert operation.before_hash == sha256_text(content)
    assert operation.metadata["edit_strategy"] == "text_replace"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["line_number"] == 3
    assert operation.metadata["unique_context"] is True

    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    edits: list[tuple[str, str, str]] = []
    writes: list[tuple[str, str]] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append((path, updated))
        raise AssertionError("rust line suggestions must execute through editor")

    def editor(edit_operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert edit_operation.span_start is not None
        assert edit_operation.span_end is not None
        assert edit_operation.expected is not None
        assert edit_operation.replacement is not None
        assert current[edit_operation.span_start : edit_operation.span_end] == edit_operation.expected
        updated = current[: edit_operation.span_start] + edit_operation.replacement + current[edit_operation.span_end :]
        target.write_text(updated, encoding="utf-8")
        edits.append((edit_operation.path, edit_operation.expected, edit_operation.replacement))
        return {
            "ok": True,
            "file": edit_operation.path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "edit_file",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-line-suggestion",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_line_suggestion_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(raw,),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert result.error_code is None
    assert writes == []
    assert edits == [(relative_path, "    takes(value)\n", "    takes(&value)\n")]
    assert "takes(&value)" in target.read_text(encoding="utf-8")
    assert result.receipts[0].source_tool == "deterministic_rust_line_suggestion_repair"


@pytest.mark.parametrize("unsafe_path", ("../src/lib.rs", "/tmp/demo/src/lib.rs"))
def test_rust_line_suggestion_rule_rejects_unsafe_paths(unsafe_path: str) -> None:
    content = "fn main() {\n    takes(value)\n}\n"
    raw = (
        "error[E0308]: mismatched types\n"
        f" --> {unsafe_path}:2:11\n"
        "  |\n"
        "2 |     takes(value)\n"
        "help: consider borrowing here\n"
        "  |\n"
        "2 |     takes(&value)"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_line_suggestion_plan(
        base_files={unsafe_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_line_suggestion_rule_skips_non_unique_expected_line() -> None:
    content = "fn main() {\n    takes(value)\n    takes(value)\n}\n"
    raw = (
        "error[E0308]: mismatched types\n"
        " --> src/lib.rs:2:11\n"
        "  |\n"
        "2 |     takes(value)\n"
        "help: consider borrowing here\n"
        "  |\n"
        "2 |     takes(&value)"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_line_suggestion_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_line_suggestion_rule_skips_already_equal_line() -> None:
    content = "fn main() {\n    takes(&value)\n}\n"
    raw = (
        "error[E0308]: mismatched types\n"
        " --> src/lib.rs:2:11\n"
        "  |\n"
        "2 |     takes(value)\n"
        "help: consider borrowing here\n"
        "  |\n"
        "2 |     takes(&value)"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_line_suggestion_plan(
        base_files={"src/lib.rs": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is None


def test_rust_line_suggestion_rule_requires_rs_path_in_base_files() -> None:
    raw = (
        "error[E0308]: mismatched types\n"
        " --> src/lib.rs:2:11\n"
        "  |\n"
        "2 |     takes(value)\n"
        "help: consider borrowing here\n"
        "  |\n"
        "2 |     takes(&value)"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    missing_base_plan = build_rust_line_suggestion_plan(
        base_files={"src/other.rs": "fn main() {\n    takes(value)\n}\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )
    non_rs_plan = build_rust_line_suggestion_plan(
        base_files={"src/lib.txt": "fn main() {\n    takes(value)\n}\n"},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert missing_base_plan is None
    assert non_rs_plan is None


def test_rust_line_suggestion_coverage_matches_executable_runtime_plan() -> None:
    raw = (
        "error[E0308]: mismatched types\n"
        " --> src/lib.rs:3:11\n"
        "  |\n"
        "3 |     takes(value)\n"
        "  |           ^^^^^ expected `&String`, found `String`\n"
        "help: consider borrowing here\n"
        "  |\n"
        "3 |     takes(&value)"
    )
    coverage = default_repair_rule_registry().coverage(normalize_artifact_quality_errors([raw])).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_line_suggestion_repair",
        base_files={"src/lib.rs": "fn takes(_: &String) {}\nfn main() {\n    takes(value)\n}\n"},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.line_suggestion" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_line_suggestion_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_line_suggestion_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_rust_cargo_transcript_splits_independent_error_blocks() -> None:
    raw = (
        "error: expected identifier, found `<`\n"
        "   --> src/engine/alchemy_runner.rs:194:1\n"
        "    |\n"
        "194 | </Stardust></String></Stardust>\n"
        "    | ^ expected identifier\n"
        "\n"
        "error: invalid format string: field access isn't supported\n"
        "   --> src/engine/alchemy_rules.rs:132:57\n"
        "    |\n"
        '132 |     let mut combined = Alchemy::new(format!("combined::{head.name}"));\n'
        "    |                                                         ^^^^^^^^^ not supported in format string\n"
        "help: consider using a positional formatting argument instead\n"
        "    |\n"
        '132 -     let mut combined = Alchemy::new(format!("combined::{head.name}"));\n'
        '132 +     let mut combined = Alchemy::new(format!("combined::{0}", head.name));\n'
        "    |\n"
        "\n"
        "error[E0432]: unresolved import `thiserror`\n"
        "  --> src/engine/alchemy_rules.rs:17:5\n"
        "   |\n"
        "17 | use thiserror::Error;\n"
        "   |     ^^^^^^^^^ use of unresolved module or unlinked crate `thiserror`\n"
    )
    diagnostics = normalize_artifact_quality_errors([raw])
    codes = [item.code for item in diagnostics]
    assert "rust_diagnostic" in codes
    assert "rust_e0432" in codes
    assert len(diagnostics) >= 3


def test_rust_line_suggestion_applies_plus_help_and_strips_xml_residue() -> None:
    runner = "pub fn combine_alchemists() {}\n}\n</Stardust></String></Stardust>\n"
    rules = 'let mut combined = Alchemy::new(format!("combined::{head.name}"));\n'
    raw = (
        "error: expected identifier, found `<`\n"
        "   --> src/engine/alchemy_runner.rs:3:1\n"
        "    |\n"
        "3 | </Stardust></String></Stardust>\n"
        "    | ^ expected identifier\n"
        "\n"
        "error: invalid format string: field access isn't supported\n"
        "   --> src/engine/alchemy_rules.rs:1:57\n"
        "help: consider using a positional formatting argument instead\n"
        '1 +     let mut combined = Alchemy::new(format!("combined::{0}", head.name));\n'
    )
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_line_suggestion_repair",
        base_files={"src/engine/alchemy_runner.rs": runner, "src/engine/alchemy_rules.rs": rules},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    assert planning.plan is not None
    assert planning.composition is not None
    after = {patch.path: patch.content_after for patch in planning.composition.patches}
    assert "</Stardust>" not in after["src/engine/alchemy_runner.rs"]
    assert 'format!("combined::{0}", head.name)' in after["src/engine/alchemy_rules.rs"]


def test_cargo_test_transcript_keeps_one_verifier_island_per_failed_test() -> None:
    raw = (
        "---- engine::alchemy_runner::tests::combine_alchemists_returns_combined_recipe stdout ----\n"
        "\n"
        "thread 'engine::alchemy_runner::tests::combine_alchemists_returns_combined_recipe' "
        "(621) panicked at src/engine/alchemy_runner.rs:189:9:\n"
        'assertion failed: combined.name.starts_with("combined::")\n'
        "\n"
        "---- zero_mass_reagents_are_rejected_by_input_gate stdout ----\n"
        "\n"
        "thread 'zero_mass_reagents_are_rejected_by_input_gate' (653) panicked at tests/product.rs:226:5:\n"
        "zero-mass bag must be rejected by gate\n"
        "\n"
        "failures:\n"
        "    zero_mass_reagents_are_rejected_by_input_gate\n"
        "\n"
        "test result: FAILED. 12 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out\n"
    )
    diagnostics = normalize_artifact_quality_errors([raw])
    assert [item.code for item in diagnostics] == ["verifier_test_failure", "verifier_test_failure"]
    assert diagnostics[0].path == "src/engine/alchemy_runner.rs"
    assert diagnostics[1].path == "tests/product.rs"
    assert diagnostics[1].metadata["test_name"] == "zero_mass_reagents_are_rejected_by_input_gate"
    assert diagnostics[1].metadata["framework"] == "cargo_test"


def test_rust_zero_mass_gate_replaces_floored_effective_mass_predicate() -> None:
    rules = (
        "pub fn is_valid_input(alchemy: &Alchemy, reagents: &[Stardust]) -> bool {\n"
        "    if reagents.is_empty() {\n"
        "        return false;\n"
        "    }\n"
        "    reagents.iter().any(|r| r.effective_mass() > 0)\n"
        "}\n"
    )
    raw = (
        "---- zero_mass_reagents_are_rejected_by_input_gate stdout ----\n"
        "\n"
        "thread 'zero_mass_reagents_are_rejected_by_input_gate' (653) panicked at tests/product.rs:226:5:\n"
        "zero-mass bag must be rejected by gate\n"
        "\n"
        "test result: FAILED. 12 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out\n"
    )
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_line_suggestion_repair",
        base_files={
            "src/engine/alchemy_rules.rs": rules,
            "tests/product.rs": "assert!(!is_valid_input(&workbench, &bag));\n",
        },
        artifact_quality_errors=(raw,),
        mode="shadow",
    )
    assert planning.plan is not None
    assert planning.composition is not None
    after = {patch.path: patch.content_after for patch in planning.composition.patches}
    assert "r.effective_mass() > 0" not in after["src/engine/alchemy_rules.rs"]
    assert "(r.purity * r.quantity as f64).round() as u32 > 0" in after["src/engine/alchemy_rules.rs"]


def test_rust_field_rename_suggestion_rule_builds_span_text_replace_plan_and_runs_with_editor(
    tmp_path: Path,
) -> None:
    relative_path = "src/lib.rs"
    content = (
        "pub struct Recipe {\n"
        "    pub ingredient: Vec<String>,\n"
        "}\n"
        "pub fn collect(recipe: &Recipe) {\n"
        "    for ingredient in &recipe.ingredients {\n"
        '        println!("{}", ingredient);\n'
        "    }\n"
        "}\n"
    )
    raw = (
        "error[E0609]: no field `ingredients` on type `&Recipe`\n"
        " --> src/lib.rs:5:31\n"
        "  |\n"
        "5 |     for ingredient in &recipe.ingredients {\n"
        "  |                               ^^^^^^^^^^^ unknown field\n"
        "  |\n"
        "help: a field with a similar name exists\n"
        "  |\n"
        "5 -     for ingredient in &recipe.ingredients {\n"
        "5 +     for ingredient in &recipe.ingredient {"
    )
    diagnostics = normalize_artifact_quality_errors([raw])

    plan = build_rust_field_rename_suggestion_plan(
        base_files={relative_path: content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "rust.field_rename_suggestion"
    assert plan.source_tool == "deterministic_rust_field_rename_suggestion_repair"
    assert plan.metadata["span_based"] is True
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == relative_path
    assert operation.expected == "ingredients"
    assert operation.replacement == "ingredient"
    assert operation.before_hash == sha256_text(content)
    assert operation.span_start is not None
    assert operation.span_end is not None
    assert content[operation.span_start : operation.span_end] == "ingredients"
    assert operation.metadata["edit_strategy"] == "text_replace"
    assert operation.metadata["span_based"] is True
    assert operation.metadata["line_number"] == 5
    assert operation.metadata["wrong_field"] == "ingredients"
    assert operation.metadata["correct_field"] == "ingredient"
    assert operation.metadata["unique_context"]

    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    edits: list[tuple[str, str, str]] = []
    writes: list[tuple[str, str]] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append((path, updated))
        raise AssertionError("rust field rename suggestions must execute through editor")

    def editor(edit_operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert edit_operation.span_start is not None
        assert edit_operation.span_end is not None
        assert edit_operation.expected is not None
        assert edit_operation.replacement is not None
        assert current[edit_operation.span_start : edit_operation.span_end] == edit_operation.expected
        updated = current[: edit_operation.span_start] + edit_operation.replacement + current[edit_operation.span_end :]
        target.write_text(updated, encoding="utf-8")
        edits.append((edit_operation.path, edit_operation.expected, edit_operation.replacement))
        return {
            "ok": True,
            "file": edit_operation.path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "edit_file",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-field-rename-suggestion",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_field_rename_suggestion_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(raw,),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert result.error_code is None
    assert writes == []
    assert edits == [(relative_path, "ingredients", "ingredient")]
    repaired = target.read_text(encoding="utf-8")
    assert "&recipe.ingredient" in repaired
    assert "&recipe.ingredients" not in repaired
    assert result.receipts[0].source_tool == "deterministic_rust_field_rename_suggestion_repair"


def test_rust_field_rename_suggestion_coverage_matches_executable_runtime_plan() -> None:
    content = (
        "pub struct Recipe {\n"
        "    pub ingredient: Vec<String>,\n"
        "}\n"
        "pub fn collect(recipe: &Recipe) {\n"
        "    for ingredient in &recipe.ingredients {\n"
        "    }\n"
        "}\n"
    )
    raw = (
        "error[E0609]: no field `ingredients` on type `&Recipe`\n"
        " --> src/lib.rs:5:31\n"
        "  |\n"
        "5 |     for ingredient in &recipe.ingredients {\n"
        "  |\n"
        "help: a field with a similar name exists\n"
        "  |\n"
        "5 -     for ingredient in &recipe.ingredients {\n"
        "5 +     for ingredient in &recipe.ingredient {"
    )
    coverage = default_repair_rule_registry().coverage(normalize_artifact_quality_errors([raw])).to_dict()
    planning = plan_runtime_repair(
        source_tool="deterministic_rust_field_rename_suggestion_repair",
        base_files={"src/lib.rs": content},
        artifact_quality_errors=(raw,),
        mode="shadow",
    )

    assert coverage["items"][0]["known_rule_matched"] is True
    assert coverage["items"][0]["executable_runtime_plan_matched"] is True
    assert "rust.field_rename_suggestion" in coverage["items"][0]["runtime_plan_rule_ids"]
    assert "deterministic_rust_field_rename_suggestion_repair" in coverage["items"][0]["matched_source_tools"]
    assert planning.plan is not None
    assert planning.plan.source_tool == "deterministic_rust_field_rename_suggestion_repair"
    assert planning.composition is not None
    assert planning.composition.ok is True


def test_java_accessor_alias_rule_builds_canonical_plan_without_diagnostics() -> None:
    content = (
        "package demo;\n"
        "public final class RhythmMonster {\n"
        "    public int getTemperament() {\n"
        "        return 4;\n"
        "    }\n"
        "}\n"
    )

    repaired = repair_java_common_accessor_aliases_text(content)
    plan = build_java_accessor_alias_plan(
        base_files={"./src/main/java/demo/RhythmMonster.java": content},
        diagnostics=(),
        mode="shadow",
    )

    assert "public int temperament()" in repaired
    assert plan is not None
    assert plan.rule_id == "java.common_accessor_aliases"
    assert plan.source_tool == "deterministic_java_accessor_alias_repair"
    assert plan.priority == 1
    assert len(plan.operations) == 1
    assert plan.operations[0].path == "src/main/java/demo/RhythmMonster.java"
    assert plan.operations[0].metadata["repair_kind"] == "java_common_accessor_aliases"


def test_typescript_object_literal_comma_plan_dedupes_same_file_diagnostics() -> None:
    content = (
        "export function summarizeFlight() {\n  return {\n    range\n    maxAltitude: 2\n    landed: true,\n  };\n}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            "TypeScript syntax check failed: src/models/Flight.ts(4,5): error TS1005: ',' expected.",
            "TypeScript syntax check failed: src/models/Flight.ts(5,5): error TS1005: ',' expected.",
        ]
    )

    plan = build_typescript_object_literal_comma_plan(
        base_files={"src/models/Flight.ts": content},
        diagnostics=diagnostics,
    )

    assert plan is not None
    assert len(plan.operations) == 1
    assert len(plan.diagnostics) == 2
    composition = PatchComposer().compose({"src/models/Flight.ts": content}, plan.operations)
    assert composition.ok
    assert "    range,\n" in composition.patches[0].content_after
    assert "    maxAltitude: 2,\n" in composition.patches[0].content_after


def test_typescript_object_literal_comma_rule_repairs_previous_property_line() -> None:
    content = "export function summarizeFlight() {\n  return {\n    range\n    maxAltitude: 2,\n  };\n}\n"

    repaired = repair_typescript_object_literal_commas(content)

    assert "    range,\n" in repaired
    assert "    maxAltitude: 2,\n" in repaired


def test_typescript_object_literal_comma_rule_repairs_property_semicolons() -> None:
    content = (
        "export function summarizeFlower() {\n"
        "  return {\n"
        "    color;\n"
        "    [FlowerType.Moonflower]: 0.9;\n"
        "    wilted: false;\n"
        "  };\n"
        "}\n"
    )

    repaired = repair_typescript_object_literal_commas(content)

    assert "    color,\n" in repaired
    assert "    [FlowerType.Moonflower]: 0.9,\n" in repaired
    assert "    wilted: false,\n" in repaired
    assert "color;\n" not in repaired
    assert "0.9;\n" not in repaired


def test_public_typescript_object_literal_comma_planning_surface_builds_summary() -> None:
    content = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )
    advisory = RepairAdvisoryV1(
        advisor_source="agi",
        message="check the receipt after deterministic planning",
        confidence=0.4,
        metadata={"evidence_ref": "runtime/receipts/latest.json"},
    )

    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_return_object_semicolon_repair",
            base_files={"src/models/Flight.ts": content},
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected.",
            ),
            advisor_notes=(advisory,),
            mode="shadow",
        )
    )
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairPlanningResultV1)
    assert payload["schema_version"] == "director.repair_planning_result.v1"
    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["agi_execution_authority"] is False
    assert payload["advisory_authoritative"] is False
    assert payload["director_tool_execution_required"] is True
    assert payload["plan_summary"]["rule_id"] == "typescript.object_literal_missing_comma"
    assert payload["plan_summary"]["source_tool"] == "deterministic_typescript_return_object_semicolon_repair"
    assert payload["plan_summary"]["mode"] == "shadow"
    assert payload["plan_summary"]["operation_count"] == 1
    assert payload["plan_summary"]["advisor_note_count"] == 1
    assert payload["plan_summary"]["agi_execution_authority"] is False
    assert payload["plan_summary"]["advisory_authoritative"] is False
    assert payload["composition_summary"]["ok"] is True
    assert payload["composition_summary"]["patch_count"] == 1
    assert payload["composition_summary"]["issue_count"] == 0
    patch = payload["composition_summary"]["patches"][0]
    assert patch["path"] == "src/models/Flight.ts"
    assert patch["changed"] is True
    assert "flightTime, landed:" in patch["content_after"]
    assert payload["advisor_notes"] == [
        {
            "advisor_source": "agi",
            "message": "check the receipt after deterministic planning",
            "confidence": 0.4,
            "authoritative": False,
            "suggested_rules": [],
            "metadata": {"evidence_ref": "runtime/receipts/latest.json"},
        }
    ]


def test_public_typescript_object_literal_comma_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "src/models/Flight.ts"
    target = tmp_path / relative_path
    content = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-ts1005",
            workspace=str(tmp_path),
            source_tool="deterministic_typescript_return_object_semicolon_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected.",
            ),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_typescript_return_object_semicolon_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "flightTime, landed:" in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_go_bare_import_string_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "cmd/app/main.go"
    target = tmp_path / relative_path
    content = 'package main\n\n"fmt"\n\nfunc main() {}\n'
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-go-import",
            workspace=str(tmp_path),
            source_tool="deterministic_go_bare_import_string_repair",
            base_files={relative_path: content},
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_go_bare_import_string_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert 'import "fmt"' in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_go_subpath_run_uses_precise_editor_and_receipt(tmp_path: Path) -> None:
    relative_path = "cmd/app/main.go"
    package_path = "src/engine/engine.go"
    go_mod_path = "go.mod"
    content = 'package main\n\nimport (\n    "example.com/demo/pet-ascii/src/engine"\n)\n\nfunc main() {}\n'
    base_files = {
        go_mod_path: "module example.com/demo\n",
        relative_path: content,
        package_path: "package engine\n",
    }
    for path, text in base_files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    writes: list[str] = []
    edits: list[RepairOperation] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append(path)
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    def editor(operation: RepairOperation) -> dict[str, object]:
        edit_target = tmp_path / operation.path
        current = edit_target.read_text(encoding="utf-8")
        assert operation.span_start is not None
        assert operation.span_end is not None
        assert current[operation.span_start : operation.span_end] == operation.expected
        updated = current[: operation.span_start] + str(operation.replacement or "") + current[operation.span_end :]
        edit_target.write_text(updated, encoding="utf-8")
        edits.append(operation)
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-go-subpath",
            workspace=str(tmp_path),
            source_tool="deterministic_go_subpath_repair",
            base_files=base_files,
            allowed_paths=tuple(base_files),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert result.error_code is None
    assert writes == []
    assert [operation.metadata["repair_kind"] for operation in edits] == ["go_import_subpath"]
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.source_tool == "deterministic_go_subpath_repair"
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text((tmp_path / relative_path).read_text(encoding="utf-8"))
    assert '"example.com/demo/src/engine"' in (tmp_path / relative_path).read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["planning"]["plan_summary"]["rule_id"] == "go.import_subpath"
    assert result.metadata["planning"]["plan_summary"]["source_tool"] == "deterministic_go_subpath_repair"
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None
    assert receipt.metadata["execution_records"][0]["operation"] == "edit_file"
    assert receipt.metadata["precise_edit_strategy_by_path"][relative_path]["strategy"] == "span_based"
    assert receipt.metadata["precise_edit_strategy_by_path"][relative_path]["editor_used"] is True


def test_public_go_bare_local_import_missing_go_mod_fails_closed_without_writes(tmp_path: Path) -> None:
    relative_path = "cmd/app/main.go"
    content = 'package main\n\nimport "src/models"\n'
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    writes: list[str] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        writes.append(path)
        (tmp_path / path).write_text(updated, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "modify"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-go-bare-local-fail-closed",
            workspace=str(tmp_path),
            source_tool="deterministic_go_bare_import_repair",
            base_files={relative_path: content},
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is False
    assert result.error_code == "repair_not_planned"
    assert result.receipts == ()
    assert writes == []
    assert target.read_text(encoding="utf-8") == content
    assert result.metadata["planning"]["source_tool"] == "deterministic_go_bare_import_repair"
    assert result.metadata["planning"]["planned"] is False


def test_public_rust_dependency_run_executes_with_receipt(tmp_path: Path) -> None:
    cargo_path = "Cargo.toml"
    source_path = "src/main.rs"
    cargo = '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\n'
    source = "use serde::Serialize;\nfn main() { let _ = serde_json::json!({}); }\n"
    target = tmp_path / cargo_path
    source_target = tmp_path / source_path
    source_target.parent.mkdir(parents=True)
    target.write_text(cargo, encoding="utf-8")
    source_target.write_text(source, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-deps",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_dependency_repair",
            base_files={cargo_path: cargo, source_path: source},
            artifact_quality_errors=("error[E0432]: unresolved import `serde`",),
            allowed_paths=(cargo_path, source_path),
        ),
        writer=writer,
    )
    repaired = target.read_text(encoding="utf-8")

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_rust_dependency_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (cargo_path,)
    assert receipt.before_hashes[cargo_path] == sha256_text(cargo)
    assert receipt.after_hashes[cargo_path] == sha256_text(repaired)
    assert 'serde = { version = "1.0", features = ["derive"] }' in repaired
    assert 'serde_json = "1.0"' in repaired
    assert result.metadata["planning"]["plan_summary"]["rule_id"] == "rust.unlinked_crate_dependency"
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_rust_missing_binary_entrypoint_run_creates_file_with_receipt(tmp_path: Path) -> None:
    cargo_path = "Cargo.toml"
    binary_path = "src/bin/demo.rs"
    cargo = '[package]\nname = "demo-app"\nversion = "0.1.0"\n\n[[bin]]\nname = "demo-cli"\npath = "src/bin/demo.rs"\n'
    target = tmp_path / cargo_path
    target.write_text(cargo, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.parent.mkdir(parents=True, exist_ok=True)
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "create",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-rust-bin",
            workspace=str(tmp_path),
            source_tool="deterministic_rust_missing_binary_entrypoint_repair",
            base_files={cargo_path: cargo},
            allowed_paths=(cargo_path, binary_path),
        ),
        writer=writer,
    )
    created = (tmp_path / binary_path).read_text(encoding="utf-8")

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.rule_id == "rust.missing_binary_entrypoint"
    assert receipt.source_tool == "deterministic_rust_missing_binary_entrypoint_repair"
    assert receipt.files_changed == (binary_path,)
    assert receipt.before_hashes[binary_path] == sha256_text("")
    assert receipt.after_hashes[binary_path] == sha256_text(created)
    assert "demo_app binary entry point" in created
    assert receipt.metadata["write_file_reasons_by_path"][binary_path] == "new_file_or_empty_file"
    assert receipt.metadata["execution_records"][0]["write_file_reason"] == "new_file_or_empty_file"
    assert receipt.metadata["execution_records"][0]["rollback_restore_strategy"]
    assert result.metadata["planning"]["plan_summary"]["rule_id"] == "rust.missing_binary_entrypoint"
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_cpp_include_path_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "src/engine/generator.cpp"
    header_path = "src/models/postcard.hpp"
    target = tmp_path / relative_path
    header = tmp_path / header_path
    content = '#include "src/models/postcard.hpp"\n#include <string>\n'
    target.parent.mkdir(parents=True)
    header.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    header.write_text("#pragma once\n", encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-cpp-include",
            workspace=str(tmp_path),
            source_tool="deterministic_cpp_include_path_repair",
            base_files={relative_path: content, header_path: "#pragma once\n"},
            allowed_paths=(relative_path, header_path),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_cpp_include_path_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert '#include "../models/postcard.hpp"' in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_cpp_standard_include_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "src/models/seed.hpp"
    target = tmp_path / relative_path
    content = "#pragma once\nnamespace demo { std::uint32_t seed(); }\n"
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-cpp-standard-include",
            workspace=str(tmp_path),
            source_tool="deterministic_cpp_standard_include_repair",
            base_files={relative_path: content},
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_cpp_standard_include_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "#include <cstdint>" in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_cpp_missing_private_members_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "src/models/poem.hpp"
    target = tmp_path / relative_path
    content = (
        "#pragma once\n"
        "#include <string>\n"
        "namespace demo {\n"
        "class Poem {\n"
        "public:\n"
        "    const std::string& title() const noexcept { return title_; }\n"
        "};\n"
        "}\n"
    )
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-cpp-private-members",
            workspace=str(tmp_path),
            source_tool="deterministic_cpp_missing_private_members_repair",
            base_files={relative_path: content},
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_cpp_missing_private_members_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "private:\n    std::string title_;" in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None
