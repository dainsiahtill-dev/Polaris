"""Real public routing must reach a source-proven existing import planner."""

from __future__ import annotations

from copy import deepcopy

import pytest
from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairMaterializationPlanProbeV1,
    normalize_director_repair_diagnostics,
    plan_director_repair,
    query_director_repair_coverage,
    query_director_repair_materialization_plan_probe,
)

SOURCE_TOOL = "deterministic_typescript_unique_export_import_repair"
IMPORTER = "tests/verify.test.ts"
ERROR = "unresolved relative import '../dist/math.js' in tests/verify.test.ts"


def _files() -> dict[str, str]:
    return {
        "src/math.ts": "export function add(a: number, b: number) { return a + b; }\n",
        IMPORTER: 'import { add } from "../dist/math.js";\nconsole.log(add(1, 2));\n',
    }


@pytest.mark.parametrize("structured", [False, True])
def test_unresolved_relative_import_coverage_routes_existing_unique_export_tool(structured: bool) -> None:
    errors = () if structured else (ERROR,)
    issues = (
        (
            {
                "source": "artifact_quality",
                "code": "unresolved_relative_import",
                "message": "Relative import target is unresolved.",
                "path": IMPORTER,
                "metadata": {"specifier": "../dist/math.js"},
            },
        )
        if structured
        else ()
    )

    report = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=errors, artifact_quality_issues=issues)
    )

    assert report.total_diagnostics == 1
    assert SOURCE_TOOL in report.items[0].matched_source_tools
    assert report.items[0].executable_runtime_plan_matched


@pytest.mark.parametrize("structured", [False, True])
def test_real_materialization_probe_finds_the_same_importer_patch_as_explicit_plan(structured: bool) -> None:
    files = _files()
    before = deepcopy(files)
    direct = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=SOURCE_TOOL,
            base_files=files,
            diagnostics=normalize_director_repair_diagnostics((ERROR,)),
            mode="shadow",
        )
    )
    assert direct.error_code is None
    assert direct.composition_summary.to_dict()["changed_paths"] == [IMPORTER]

    probe = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=() if structured else (ERROR,),
            artifact_quality_issues=(
                {
                    "source": "artifact_quality",
                    "code": "unresolved_relative_import",
                    "message": "Relative import target is unresolved.",
                    "path": IMPORTER,
                    "metadata": {"specifier": "../dist/math.js"},
                },
            )
            if structured
            else (),
            base_files=files,
            step_id="materialization.typescript_compiler",
            fallback_to_step_source_tools=True,
            mode="shadow",
        )
    )

    assert SOURCE_TOOL in probe.candidate_source_tools
    assert SOURCE_TOOL in probe.plannable_source_tools
    assert probe.status == "covered_plannable"
    assert probe.plan_probe_result is not None
    selected = next(item for item in probe.plan_probe_result.items if item.source_tool == SOURCE_TOOL)
    assert selected.changed_paths == (IMPORTER,)
    assert selected.patch_count == 1
    assert selected.matched_diagnostic_count == 1
    assert selected.planning_result.composition_summary.to_dict()["patches"][0]["content_after"] == (
        'import { add } from "../src/math.ts";\nconsole.log(add(1, 2));\n'
    )
    assert files == before  # shadow planning never mutates its authored input


def test_canonical_typed_import_does_not_follow_stale_raw_owner_path() -> None:
    files = _files()
    files["tests/stale.test.ts"] = files[IMPORTER]
    result = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=(),
            artifact_quality_issues=(
                {
                    "source": "artifact_quality",
                    "code": "unresolved_relative_import",
                    "message": "Relative import target is unresolved.",
                    "path": IMPORTER,
                    "metadata": {"specifier": "../dist/math.js"},
                    "raw": "unresolved relative import '../dist/math.js' in tests/stale.test.ts",
                },
            ),
            base_files=files,
            source_tools=(SOURCE_TOOL,),
            mode="shadow",
        )
    )
    assert result.plan_probe_result is not None
    selected = next(item for item in result.plan_probe_result.items if item.source_tool == SOURCE_TOOL)
    assert selected.changed_paths == (IMPORTER,)


@pytest.mark.parametrize("specifier", [None, 17, "node:fs", "/outside/math"])
def test_invalid_typed_relative_specifier_does_not_produce_patch(specifier: object) -> None:
    result = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=(),
            artifact_quality_issues=(
                {
                    "source": "artifact_quality",
                    "code": "unresolved_relative_import",
                    "message": "Relative import target is unresolved.",
                "path": IMPORTER,
                "metadata": {"specifier": specifier},
                "raw": ERROR,  # provenance cannot override malformed canonical fields
                },
            ),
            base_files=_files(),
            source_tools=(SOURCE_TOOL,),
            mode="shadow",
        )
    )
    assert result.plannable_source_tools == ()


@pytest.mark.parametrize("ambiguous", [False, True])
def test_missing_or_ambiguous_export_does_not_become_a_usable_patch(ambiguous: bool) -> None:
    files = _files()
    if ambiguous:
        files["src/other.ts"] = "export function add(a: number, b: number) { return a - b; }\n"
    else:
        files["src/math.ts"] = "export function subtract(a: number, b: number) { return a - b; }\n"
    before = deepcopy(files)
    result = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=(ERROR,),
            base_files=files,
            source_tools=(SOURCE_TOOL,),
            mode="shadow",
        )
    )
    assert result.plannable_source_tools == ()
    assert result.status != "covered_plannable"
    assert files == before


def test_step_fallback_cannot_claim_an_unrelated_diagnostic_is_covered() -> None:
    files = _files()
    before = deepcopy(files)
    result = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=("src/math.ts(1,1): error TS9999: Unsupported future diagnostic.",),
            base_files=files,
            source_tools=(SOURCE_TOOL,),
            fallback_to_step_source_tools=True,
            mode="shadow",
        )
    )
    assert result.coverage_report.uncovered_diagnostic_count == 1
    assert result.plannable_source_tools == ()
    assert files == before


def test_unknown_source_tool_still_fails_closed_without_effects() -> None:
    files = _files()
    before = deepcopy(files)
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(source_tool="unregistered_import_repair", base_files=files, mode="shadow")
    )
    assert result.error_code == "unsupported_repair_source_tool"
    assert result.composition_summary.to_dict()["patch_count"] == 0
    assert files == before


def test_existing_raw_unique_export_route_is_preserved() -> None:
    report = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=("unique export import repair needed",))
    )
    assert SOURCE_TOOL in report.items[0].matched_source_tools


@pytest.mark.parametrize("path", ["src/main.js", "src/main.py"])
def test_typed_route_does_not_claim_other_language_importers(path: str) -> None:
    report = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(),
            artifact_quality_issues=(
                {
                    "source": "artifact_quality",
                    "code": "unresolved_relative_import",
                    "message": "Relative import target is unresolved.",
                    "path": path,
                    "metadata": {"specifier": "../dist/math.js"},
                },
            ),
        )
    )
    assert SOURCE_TOOL not in report.items[0].matched_source_tools
