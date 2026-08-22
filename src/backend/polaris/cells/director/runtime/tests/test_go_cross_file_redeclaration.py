"""Regression tests for conservative Go cross-file redeclaration repair."""

from __future__ import annotations

from itertools import pairwise

from polaris.cells.director.runtime.internal.repair_kernel import normalize_artifact_quality_errors
from polaris.cells.director.runtime.internal.repair_kernel.go_syntax import build_go_dedup_plan
from polaris.cells.director.runtime.public import (
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairPlanProbeV1,
    query_director_repair_coverage,
    query_director_repair_plan_probe,
)


def test_equivalent_cross_file_function_redeclaration_is_covered_and_plannable() -> None:
    errors = (
        "models/seed.go:3:6: ParseNote redeclared in this block",
        "models/model.go:3:6: other declaration of ParseNote",
    )
    base_files = {
        "models/seed.go": (
            "package models\n\n"
            "func ParseNote(raw string) (string, bool) {\n"
            "\tif raw == \"C\" {\n"
            "\t\treturn raw, true\n"
            "\t}\n"
            "\treturn \"\", false\n"
            "}\n"
        ),
        "models/model.go": (
            "package models\n\n"
            "func ParseNote(raw string) (string, bool) {\n"
            "\tif raw == \"C\" {\n"
            "\t\treturn raw, true\n"
            "\t}\n"
            "\treturn \"\", false\n"
            "}\n"
        ),
    }

    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=errors)
    ).to_dict()
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=("deterministic_go_dedup_repair",),
            artifact_quality_errors=errors,
            base_files=base_files,
        )
    ).to_dict()

    assert coverage["covered_diagnostic_count"] == 2
    assert coverage["uncovered_diagnostic_count"] == 0
    assert probe["status"] == "covered_plannable"
    assert probe["items"][0]["patch_count"] == 1
    assert probe["items"][0]["changed_paths"] == ["models/seed.go"]


def test_equivalent_cross_file_type_function_and_var_remove_only_primary_declarations() -> None:
    primary = (
        "package engine\n\n"
        "type Engine struct {\n"
        "\tFloorY float64\n"
        "}\n\n"
        "func New() *Engine {\n"
        "\treturn &Engine{FloorY: 0}\n"
        "}\n\n"
        "var ErrInvalidFloor = errors.New(\"engine: floor y must be finite\")\n"
    )
    companion = primary.replace("package engine\n\n", "package engine\n\n// canonical declarations\n")
    errors = (
        "engine/physics.go:3:6: Engine redeclared in this block",
        "engine/engine.go:4:6: other declaration of Engine",
        "engine/physics.go:7:6: New redeclared in this block",
        "engine/engine.go:8:6: other declaration of New",
        "engine/physics.go:11:5: ErrInvalidFloor redeclared in this block",
        "engine/engine.go:12:5: other declaration of ErrInvalidFloor",
    )

    plan = build_go_dedup_plan(
        base_files={"engine/physics.go": primary, "engine/engine.go": companion},
        diagnostics=normalize_artifact_quality_errors(errors),
        mode="shadow",
    )

    assert plan is not None
    assert plan.source_tool == "deterministic_go_dedup_repair"
    assert len(plan.operations) == 3
    assert {operation.path for operation in plan.operations} == {"engine/physics.go"}
    assert {operation.metadata["declaration_kind"] for operation in plan.operations} == {
        "func",
        "type",
        "var",
    }
    assert all(operation.kind == "text_replace" for operation in plan.operations)
    assert all(operation.replacement == "" for operation in plan.operations)
    spans = sorted((operation.span_start, operation.span_end) for operation in plan.operations)
    assert all(
        previous_end <= next_start
        for (_, previous_end), (next_start, _) in pairwise(spans)
    )


def test_cross_file_redeclaration_with_different_body_fails_closed() -> None:
    errors = (
        "models/seed.go:3:6: ParseNote redeclared in this block",
        "models/model.go:3:6: other declaration of ParseNote",
    )
    primary = "package models\n\nfunc ParseNote(raw string) string { return raw }\n"
    companion = 'package models\n\nfunc ParseNote(raw string) string { return "C" }\n'

    plan = build_go_dedup_plan(
        base_files={"models/seed.go": primary, "models/model.go": companion},
        diagnostics=normalize_artifact_quality_errors(errors),
        mode="shadow",
    )

    assert plan is None


def test_equivalent_cross_file_method_redeclaration_is_plannable() -> None:
    errors = (
        "models/seed.go:5:18: Validate redeclared in this block",
        "models/model.go:5:18: other declaration of Validate",
    )
    method = (
        "package models\n\n"
        "type Bubble struct{}\n\n"
        "func (b Bubble) Validate() error { return nil }\n"
    )

    plan = build_go_dedup_plan(
        base_files={"models/seed.go": method, "models/model.go": method},
        diagnostics=normalize_artifact_quality_errors(errors),
        mode="shadow",
    )

    assert plan is not None
    assert len(plan.operations) == 1
    assert plan.operations[0].metadata["declaration_kind"] == "method"


def test_cross_file_const_group_redeclaration_removes_group_once() -> None:
    errors = (
        "engine/rules.go:6:2: ChordSilence redeclared in this block",
        "engine/engine.go:6:2: other declaration of ChordSilence",
        "engine/rules.go:7:2: ChordUnknown redeclared in this block",
        "engine/engine.go:7:2: other declaration of ChordUnknown",
    )
    declarations = (
        "package engine\n\n"
        "type ChordQuality int\n\n"
        "const (\n"
        "\tChordSilence ChordQuality = iota\n"
        "\tChordUnknown\n"
        ")\n"
    )

    plan = build_go_dedup_plan(
        base_files={"engine/rules.go": declarations, "engine/engine.go": declarations},
        diagnostics=normalize_artifact_quality_errors(errors),
        mode="shadow",
    )

    assert plan is not None
    assert len(plan.operations) == 1
    assert plan.operations[0].metadata["declaration_kind"] == "const_block"
    assert plan.operations[0].expected.startswith("const (\n")


def test_cross_file_redeclaration_without_companion_diagnostic_fails_closed() -> None:
    errors = ("models/seed.go:3:6: ParseNote redeclared in this block",)
    declaration = "package models\n\nfunc ParseNote(raw string) string { return raw }\n"

    plan = build_go_dedup_plan(
        base_files={"models/seed.go": declaration, "models/model.go": declaration},
        diagnostics=normalize_artifact_quality_errors(errors),
        mode="shadow",
    )

    assert plan is None


def test_multiple_redeclaration_pairs_in_one_compiler_receipt_are_planned_together() -> None:
    compiler_output = (
        "# musicbubble/engine\n"
        "engine/physics.go:3:6: Engine redeclared in this block\n"
        "\tengine/engine.go:3:6: other declaration of Engine\n"
        "engine/physics.go:5:6: New redeclared in this block\n"
        "\tengine/engine.go:5:6: other declaration of New\n"
        "engine/physics.go:7:5: ErrInvalidFloor redeclared in this block\n"
        "\tengine/engine.go:7:5: other declaration of ErrInvalidFloor\n"
    )
    declarations = (
        "package engine\n\n"
        "type Engine struct{}\n\n"
        "func New() *Engine { return &Engine{} }\n\n"
        "var ErrInvalidFloor = errors.New(\"invalid floor\")\n"
    )

    plan = build_go_dedup_plan(
        base_files={"engine/physics.go": declarations, "engine/engine.go": declarations},
        diagnostics=normalize_artifact_quality_errors((compiler_output,)),
        mode="shadow",
    )

    assert plan is not None
    assert len(plan.operations) == 3
    assert {operation.metadata["declaration_name"] for operation in plan.operations} == {
        "Engine",
        "ErrInvalidFloor",
        "New",
    }


def test_go_method_already_declared_diagnostic_is_covered_and_plannable() -> None:
    error = (
        "engine/rules.go:5:23: method ChordQuality.String already declared at "
        "engine/engine.go:5:23"
    )
    declarations = (
        "package engine\n\n"
        "type ChordQuality int\n\n"
        "func (q ChordQuality) String() string { return \"major\" }\n"
    )

    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(error,))
    ).to_dict()
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            source_tools=("deterministic_go_dedup_repair",),
            artifact_quality_errors=(error,),
            base_files={"engine/rules.go": declarations, "engine/engine.go": declarations},
        )
    ).to_dict()

    assert coverage["uncovered_diagnostic_count"] == 0
    assert probe["status"] == "covered_plannable"
    assert probe["items"][0]["changed_paths"] == ["engine/rules.go"]
