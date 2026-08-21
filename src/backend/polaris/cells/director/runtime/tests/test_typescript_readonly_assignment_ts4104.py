"""Focused TS4104 coverage for Director Runtime readonly assignment repair."""

from __future__ import annotations

from polaris.cells.director.runtime.internal.repair_kernel import (
    PatchComposer,
    build_repair_coverage_report,
    build_typescript_readonly_assignment_plan,
    normalize_artifact_quality_errors,
)


def test_readonly_assignment_rule_covers_ts4104_and_copies_readonly_values() -> None:
    content = (
        "let fireflies: Firefly[] = [];\n"
        "let flowers: Flower[] = [];\n"
        "fireflies = next.fireflies;\n"
        "flowers = next.flowers;\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            "src/render/gardenCanvas.ts(3,1): error TS4104: The type 'readonly Firefly[]' "
            "is 'readonly' and cannot be assigned to the mutable type 'Firefly[]'.",
            "src/render/gardenCanvas.ts(4,1): error TS4104: The type 'readonly Flower[]' "
            "is 'readonly' and cannot be assigned to the mutable type 'Flower[]'.",
        ]
    )

    coverage = build_repair_coverage_report(diagnostics)
    assert coverage.covered_diagnostic_count == 2
    assert coverage.executable_runtime_plan_diagnostic_count == 2
    assert {item.matched_rules[0].rule_id for item in coverage.items} == {"typescript.readonly_assignment"}

    plan = build_typescript_readonly_assignment_plan(
        base_files={"src/render/gardenCanvas.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert len(plan.operations) == 2
    composition = PatchComposer().compose({"src/render/gardenCanvas.ts": content}, plan.operations)
    assert composition.ok
    after = composition.patches[0].content_after
    assert "fireflies = [...next.fireflies];" in after
    assert "flowers = [...next.flowers];" in after


def test_readonly_assignment_rule_ts4104_fails_closed_for_complex_expression() -> None:
    content = "fireflies = compute(next.fireflies);\n"
    diagnostics = normalize_artifact_quality_errors(
        [
            "src/render/gardenCanvas.ts(1,1): error TS4104: The type 'readonly Firefly[]' "
            "is 'readonly' and cannot be assigned to the mutable type 'Firefly[]'.",
        ]
    )

    plan = build_typescript_readonly_assignment_plan(
        base_files={"src/render/gardenCanvas.ts": content},
        diagnostics=diagnostics,
    )

    assert plan is None
