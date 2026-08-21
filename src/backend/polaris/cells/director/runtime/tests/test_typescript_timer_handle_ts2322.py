"""Focused TS2322 coverage for browser timer handles under Node typings."""

from __future__ import annotations

from polaris.cells.director.runtime.internal.repair_kernel import (
    PatchComposer,
    build_repair_coverage_report,
    build_typescript_timer_handle_plan,
    normalize_artifact_quality_errors,
    runtime_repair_bindings,
)

DIAGNOSTIC = (
    "src/render/gardenCanvas.ts(8,7): error TS2322: "
    "Type 'Timeout' is not assignable to type 'number'."
)


def _browser_source() -> str:
    return (
        "const canvas: HTMLCanvasElement | null = null;\n"
        "const schedule = (cb: () => void): number => {\n"
        "  if (typeof globalThis.requestAnimationFrame === 'function') {\n"
        "    return globalThis.requestAnimationFrame(cb);\n"
        "  }\n"
        "  return (\n"
        "    0 ||\n"
        "      globalThis.setTimeout(cb, 16)\n"
        "  );\n"
        "};\n"
    )


def test_timer_handle_rule_is_precise_and_executable() -> None:
    diagnostics = normalize_artifact_quality_errors([DIAGNOSTIC])
    coverage = build_repair_coverage_report(diagnostics)
    matched = {rule.rule_id for rule in coverage.items[0].matched_rules}

    assert matched == {"typescript.timer_handle"}
    binding = next(
        item
        for item in runtime_repair_bindings()
        if item["source_tool"] == "deterministic_typescript_timer_handle_repair"
    )
    assert binding["rule_id"] == "typescript.timer_handle"

    content = _browser_source()
    plan = build_typescript_timer_handle_plan(
        base_files={"src/render/gardenCanvas.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )
    assert plan is not None
    assert len(plan.operations) == 1
    composition = PatchComposer().compose({"src/render/gardenCanvas.ts": content}, plan.operations)
    assert composition.ok
    assert "window.setTimeout(cb, 16)" in composition.patches[0].content_after
    assert "globalThis.setTimeout" not in composition.patches[0].content_after


def test_timer_handle_rule_fails_closed_without_browser_authority() -> None:
    diagnostics = normalize_artifact_quality_errors([DIAGNOSTIC.replace("gardenCanvas", "worker")])
    content = "export const schedule = (cb: () => void): number => globalThis.setTimeout(cb, 16);\n"

    plan = build_typescript_timer_handle_plan(
        base_files={"src/render/worker.ts": content},
        diagnostics=diagnostics,
    )

    assert plan is None


def test_unrelated_ts2322_no_longer_claims_broad_rules() -> None:
    diagnostics = normalize_artifact_quality_errors(
        ["src/main.ts(1,1): error TS2322: Type 'boolean' is not assignable to type 'number'."]
    )
    coverage = build_repair_coverage_report(diagnostics)

    assert coverage.covered_diagnostic_count == 0
