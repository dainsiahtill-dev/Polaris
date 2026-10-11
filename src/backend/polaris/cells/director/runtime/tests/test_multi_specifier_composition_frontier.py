"""Original-base composition must preserve all proven import rewrites."""

from __future__ import annotations

import copy
import sys
from types import FrameType
from typing import Any

import pytest
from polaris.cells.director.runtime.internal.repair_kernel.composer import PatchComposer
from polaris.cells.director.runtime.internal.repair_kernel.contracts import RepairOperation, sha256_text
from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairPlanProbeV1,
    plan_director_repair,
    query_director_repair_plan_probe,
)

TOOL = "deterministic_typescript_unique_export_import_repair"
SOURCE = (
    '// 😀 中文\nimport { A } from "../build/a.js";\n'
    'import { B } from "../build/b.js";\n'
    '// intervening 😀\nimport { A as AA } from "../build/a.js";\n'
    'import { B as BB } from "../build/b.js";\nvoid [A,B,AA,BB];\n'
)
EXPECTED = (
    '// 😀 中文\nimport { A } from "../lib/a.ts";\n'
    'import { B } from "../lib/b.ts";\n'
    '// intervening 😀\nimport { A as AA } from "../lib/a.ts";\n'
    'import { B as BB } from "../lib/b.ts";\nvoid [A,B,AA,BB];\n'
)


def _public(
    files: dict[str, str],
    identities: list[tuple[str, str]],
    route: str,
    tool: str = TOOL,
) -> tuple[dict[str, Any], list[RepairOperation], dict[str, Any]]:
    errors = tuple(f"unresolved relative import '{s}' in {p}" for p, s in identities) if route != "typed" else ()
    issues = (
        tuple(
            {
                "source": "artifact_quality",
                "code": "unresolved_relative_import",
                "path": p,
                "message": "Missing module",
                "metadata": {"specifier": s},
                "raw": f"unresolved relative import '{s}' in checks/stale.ts",
            }
            for p, s in identities
        )
        if route != "raw"
        else ()
    )
    command = PlanDirectorRepairCommandV1(
        tool, base_files=files, artifact_quality_errors=errors, artifact_quality_issues=issues, mode="shadow"
    )
    before = copy.deepcopy((files, errors, issues, vars(command)))
    operations: list[RepairOperation] = []

    def profile(frame: FrameType, event: str, value: Any) -> None:
        if event == "return" and frame.f_code.co_name == "_build_relative_import_plan" and value is not None:
            operations.extend(value.operations)

    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        result = plan_director_repair(command).to_dict()
    finally:
        sys.setprofile(previous)
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(errors, artifact_quality_issues=issues, base_files=files, source_tools=(tool,))
    ).to_dict()
    assert before == (files, errors, issues, vars(command))
    return result, operations, probe


@pytest.mark.parametrize("route", ["raw", "typed", "mixed"])
@pytest.mark.parametrize("reverse", [False, True])
def test_interleaved_specifiers_compose_complete_original_base(route: str, reverse: bool) -> None:
    """Intermediate hashes or overlapping a/b spans must fail this public assertion."""
    files = {"checks/first.ts": SOURCE, "lib/a.ts": "export const A=1;", "lib/b.ts": "export const B=2;"}
    identities = [("checks/first.ts", "../build/a.js"), ("checks/first.ts", "../build/b.js")]
    if reverse:
        identities.reverse()
    result, operations, probe = _public(files, identities, route)
    assert result["planned"] is True
    composition = result["composition_summary"]
    assert composition["ok"] is True, composition["issues"]
    assert result["ok"] is True
    assert {p["path"]: p["content_after"] for p in composition["patches"]} == {"checks/first.ts": EXPECTED}
    assert len(operations) == 1
    operation = operations[0]
    assert operation.kind == "text_replace"
    assert operation.before_hash == sha256_text(SOURCE)
    assert operation.expected == SOURCE[operation.span_start : operation.span_end]
    assert probe["coverage_report"]["total_diagnostics"] == 2
    assert probe["plannable_source_tools"] == [TOOL]
    assert probe["items"][0]["planning_result"]["composition_summary"]["patches"] == composition["patches"]


@pytest.mark.parametrize("route", ["raw", "typed", "mixed"])
def test_duplicates_and_two_importers_keep_one_original_operation_per_file(route: str) -> None:
    files = {
        "checks/first.ts": SOURCE,
        "checks/second.ts": "// second importer 😀\n" + SOURCE,
        "lib/a.ts": "export const A=1;",
        "lib/b.ts": "export const B=2;",
    }
    identities = [(p, s) for p in ["checks/first.ts", "checks/second.ts"] for s in ["../build/a.js", "../build/b.js"]]
    result, operations, probe = _public(files, identities + identities, route)
    assert result["composition_summary"]["ok"] is True
    assert {p["path"]: p["content_after"] for p in result["composition_summary"]["patches"]} == {
        "checks/first.ts": EXPECTED,
        "checks/second.ts": "// second importer 😀\n" + EXPECTED,
    }
    assert len(operations) == 2
    assert {operation.path: operation.before_hash for operation in operations} == {
        "checks/first.ts": sha256_text(SOURCE),
        "checks/second.ts": sha256_text(files["checks/second.ts"]),
    }
    assert probe["coverage_report"]["total_diagnostics"] == 4


@pytest.mark.parametrize("route", ["raw", "typed"])
def test_noncode_and_unrelated_text_remain_exact(route: str) -> None:
    prefix = '// import { Fake } from "../build/a.js"; 😀\nconst label="中文";\n'
    suffix = 'const template = `import { Fake } from "../build/b.js"; 😀`;\nimport { C } from "../lib/c.ts"; void C;\n'
    files = {
        "checks/first.ts": prefix + SOURCE + suffix,
        "lib/a.ts": "export const A=1;",
        "lib/b.ts": "export const B=2;",
        "lib/c.ts": "export const C=3;",
    }
    result, _, _ = _public(files, [("checks/first.ts", "../build/a.js"), ("checks/first.ts", "../build/b.js")], route)
    assert result["composition_summary"]["ok"] is True
    assert result["composition_summary"]["patches"][0]["content_after"] == prefix + EXPECTED + suffix


@pytest.mark.parametrize("unsafe", ["missing", "type", "ambiguous", "namespace"])
def test_partial_proof_preserves_unresolved_second_specifier(unsafe: str) -> None:
    files = {"checks/first.ts": SOURCE, "lib/a.ts": "export const A=1;", "lib/b.ts": "export const B=2;"}
    if unsafe == "missing":
        files["lib/b.ts"] = "export const Other=2;"
    elif unsafe == "type":
        files["lib/b.ts"] = "export interface B {}"
    elif unsafe == "ambiguous":
        files["lib/other.ts"] = "export const B=3;"
    else:
        files["checks/first.ts"] += 'import * as NS from "../build/b.js"; void NS;\n'
    result, operations, probe = _public(
        files, [("checks/first.ts", "../build/a.js"), ("checks/first.ts", "../build/b.js")], "typed"
    )
    assert result["composition_summary"]["ok"] is True
    assert result["diagnostic_count"] == 2
    assert probe["coverage_report"]["total_diagnostics"] == 2
    assert len(operations) == 1
    assert operations[0].metadata["specifier"] == "../build/a.js"
    after = result["composition_summary"]["patches"][0]["content_after"]
    assert after == files["checks/first.ts"].replace('"../build/a.js"', '"../lib/a.ts"')
    assert '"../build/b.js"' in after


@pytest.mark.parametrize("mode", ["case", "unused"])
def test_shared_case_and_unused_builder_compose_original_base(mode: str) -> None:
    source = '// 😀\nimport { A } from "./a";\nimport { B } from "./b";\nconst keep=3;\n'
    files = {"checks/first.ts": source}
    tool = "deterministic_typescript_unused_import_repair"
    expected = "// 😀\nconst keep=3;\n"
    if mode == "case":
        files.update({"checks/A.ts": "export const A=1;", "checks/B.ts": "export const B=2;"})
        tool = "deterministic_typescript_relative_import_case_repair"
        expected = '// 😀\nimport { A } from "./A";\nimport { B } from "./B";\nconst keep=3;\n'
    result, operations, _ = _public(files, [("checks/first.ts", "./a"), ("checks/first.ts", "./b")], "raw", tool)
    assert result["composition_summary"]["ok"] is True
    assert result["composition_summary"]["patches"][0]["content_after"] == expected
    assert len(operations) == 1
    assert operations[0].before_hash == sha256_text(source)


def test_stale_source_and_overlapping_edits_still_fail_closed() -> None:
    files = {"checks/first.ts": SOURCE, "lib/a.ts": "export const A=1;", "lib/b.ts": "export const B=2;"}
    _, operations, _ = _public(
        files, [("checks/first.ts", "../build/a.js"), ("checks/first.ts", "../build/b.js")], "raw"
    )
    stale = PatchComposer().compose({**files, "checks/first.ts": SOURCE + "// changed\n"}, operations)
    assert stale.ok is False
    assert "before_hash_mismatch" in {issue.code for issue in stale.issues}
    overlap = RepairOperation(
        kind="text_replace",
        path="checks/first.ts",
        span_start=0,
        span_end=len(SOURCE),
        expected=SOURCE,
        replacement="// conflicting\n",
        before_hash=sha256_text(SOURCE),
    )
    composed = PatchComposer().compose(files, [*operations, overlap])
    assert composed.ok is False
    assert not composed.patches


def test_existing_targets_and_unknown_identity_emit_no_operation() -> None:
    files = {"checks/first.ts": EXPECTED, "lib/a.ts": "export const A=1;", "lib/b.ts": "export const B=2;"}
    result, operations, _ = _public(
        files, [("checks/first.ts", "../lib/a.ts"), ("checks/first.ts", "../lib/b.ts")], "typed"
    )
    assert result["planned"] is False
    assert operations == []
    unknown = plan_director_repair(
        PlanDirectorRepairCommandV1(
            TOOL,
            base_files=files,
            artifact_quality_issues=(
                {
                    "source": "artifact_quality",
                    "code": "unresolved_relative_import",
                    "path": "checks/first.ts",
                    "message": "Missing module",
                },
            ),
        )
    )
    assert unknown.planned is False


@pytest.mark.parametrize("unsafe", ["missing", "type", "ambiguous"])
def test_no_proven_candidate_emits_no_operation(unsafe: str) -> None:
    source = 'import { B } from "../build/b.js"; void B;\n'
    files = {"checks/first.ts": source, "lib/b.ts": "export const Other=1;"}
    if unsafe == "type":
        files["lib/b.ts"] = "export interface B {}"
    elif unsafe == "ambiguous":
        files["lib/b.ts"] = "export const B=1;"
        files["lib/other.ts"] = "export const B=2;"
    result, operations, _ = _public(files, [("checks/first.ts", "../build/b.js")], "typed")
    assert result["planned"] is False
    assert operations == []
