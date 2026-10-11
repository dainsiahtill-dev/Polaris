"""Public regressions for canonical identity versus repeated quality evidence."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest
from polaris.cells.director.runtime.public import (
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairPlanProbeV1,
    normalize_director_repair_issue_diagnostics,
    query_director_repair_coverage,
    query_director_repair_plan_probe,
)
from polaris.kernelone.quality import artifact_quality_issues_from_errors

TOOL = "deterministic_typescript_unique_export_import_repair"
SOURCE = (
    '// 😀\nimport { A } from "../build/a.js";\n'
    'import { B } from "../build/b.js";\n'
    'import { A as AA } from "../build/a.js";\n'
    'import { B as BB } from "../build/b.js";\nvoid [A,B,AA,BB];\n'
)


def _raw(specifier: str, path: str = "checks/first.ts") -> str:
    return f"unresolved relative import '{specifier}' in {path}"


def _issue(
    specifier: str | None,
    *,
    path: str = "checks/first.ts",
    raw: str = "Missing module",
    diagnostic_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = dict(metadata or {})
    if specifier is not None:
        evidence["specifier"] = specifier
    issue: dict[str, Any] = {
        "source": "artifact_quality",
        "code": "unresolved_relative_import",
        "message": "Missing module",
        "path": path,
        "raw": raw,
        "metadata": evidence,
    }
    if diagnostic_id:
        issue["diagnostic_id"] = diagnostic_id
    return issue


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("route", ["raw", "typed", "mixed"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("raw_style", ["current", "stale", "shared"])
def test_distinct_specifiers_survive_public_coverage_and_probe(route: str, reverse: bool, raw_style: str) -> None:
    """Raw or structural equality must never discard the second canonical import."""
    specs = ["../build/a.js", "../build/b.js"]
    if reverse:
        specs.reverse()
    errors = tuple(_raw(spec) for spec in specs) if route != "typed" else ()
    issues = (
        tuple(
            _issue(
                spec,
                raw=_raw(spec, "checks/stale.ts")
                if raw_style == "stale"
                else ("same raw evidence" if raw_style == "shared" else _raw(spec)),
            )
            for spec in specs
        )
        if route != "raw"
        else ()
    )
    files = {
        "checks/first.ts": SOURCE,
        "checks/stale.ts": SOURCE,
        "lib/a.ts": "export const A=1;",
        "lib/b.ts": "export const B=2;",
    }
    query = QueryDirectorRepairPlanProbeV1(
        errors, artifact_quality_issues=issues, base_files=files, source_tools=(TOOL,)
    )
    normalized = normalize_director_repair_issue_diagnostics(issues)
    before = _hash((errors, issues, files, vars(query), [vars(d) for d in normalized]))
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(errors, issues))
    assert coverage.total_diagnostics == 2
    assert {item.diagnostic["metadata"]["specifier"] for item in coverage.items} == set(specs)
    assert {item.diagnostic["path"] for item in coverage.items} == {"checks/first.ts"}
    assert len({item.diagnostic["diagnostic_id"] for item in coverage.items}) == 2
    probe = query_director_repair_plan_probe(query)
    assert probe.coverage_report.total_diagnostics == 2
    assert probe.items[0].matched_diagnostic_count == 2
    # Both canonical findings must produce one complete original-base patch.
    expected = (
        '// 😀\nimport { A } from "../lib/a.ts";\n'
        'import { B } from "../lib/b.ts";\n'
        'import { A as AA } from "../lib/a.ts";\n'
        'import { B as BB } from "../lib/b.ts";\nvoid [A,B,AA,BB];\n'
    )
    assert probe.plannable_source_tools == (TOOL,)
    composition = probe.items[0].planning_result.composition_summary.to_dict()
    assert composition["ok"] is True
    assert composition["issues"] == []
    assert len(composition["patches"]) == 1
    patch = composition["patches"][0]
    assert patch["path"] == "checks/first.ts"
    assert patch["content_after"] == expected
    assert patch["before_hash"] == hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
    assert files["checks/stale.ts"] == SOURCE
    assert before == _hash((errors, issues, files, vars(query), [vars(d) for d in normalized]))


@pytest.mark.parametrize("route", ["raw", "typed", "mixed"])
@pytest.mark.parametrize("reverse", [False, True])
def test_true_duplicates_coalesce_without_losing_complementary_evidence(route: str, reverse: bool) -> None:
    """Deduplication still yields one finding and a complete concrete patch."""
    raw = _raw("../build/a.js")
    issues = [
        _issue("../build/a.js", raw=raw, metadata={"confidence": "high"}),
        _issue("../build/a.js", raw="other provenance", metadata={"language": "typescript"}),
    ]
    if reverse:
        issues.reverse()
    errors = (raw, raw) if route != "typed" else ()
    typed = tuple(issues) if route != "raw" else ()
    files = {"checks/first.ts": 'import { A } from "../build/a.js";\nvoid A;\n', "lib/a.ts": "export const A=1;"}
    before = copy.deepcopy((errors, typed, files))
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(errors, artifact_quality_issues=typed, base_files=files, source_tools=(TOOL,))
    )
    assert probe.coverage_report.total_diagnostics == 1
    if route != "raw":
        metadata = probe.coverage_report.items[0].diagnostic["metadata"]
        assert metadata["confidence"] == "high"
        assert metadata["language"] == "typescript"
    assert probe.plannable_source_tools == (TOOL,)
    patches = probe.items[0].planning_result.composition_summary.to_dict()["patches"]
    assert {patch["path"]: patch["content_after"] for patch in patches} == {
        "checks/first.ts": 'import { A } from "../lib/a.ts";\nvoid A;\n'
    }
    assert before == (errors, typed, files)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("conflict", ["specifier", "case", "path", "missing", "metadata", "explicit_id"])
def test_raw_equality_does_not_override_canonical_conflicts(conflict: str, reverse: bool) -> None:
    """The raw shortcut must check identity, including absent and explicit evidence."""
    first = _issue("../build/a.js", raw="same raw evidence")
    second = _issue("../build/a.js", raw="same raw evidence")
    if conflict == "specifier":
        second["metadata"]["specifier"] = "../build/b.js"
    elif conflict == "case":
        second["metadata"]["specifier"] = "../build/A.js"
    elif conflict == "path":
        second["path"] = "checks/second.ts"
    elif conflict == "missing":
        second["metadata"].pop("specifier")
    elif conflict == "metadata":
        first["metadata"]["imported_symbol"] = "A"
        second["metadata"]["imported_symbol"] = "B"
    else:
        first["diagnostic_id"] = "explicit-a"
        second["diagnostic_id"] = "explicit-b"
    issues = (second, first) if reverse else (first, second)
    normalized = normalize_director_repair_issue_diagnostics(issues)
    before = _hash((issues, [vars(d) for d in normalized]))
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1((), issues))
    assert coverage.total_diagnostics == 2
    if conflict == "explicit_id":
        assert {item.diagnostic["diagnostic_id"] for item in coverage.items} == {"explicit-a", "explicit-b"}
    assert before == _hash((issues, [vars(d) for d in normalized]))


def test_mixed_raw_collision_searches_all_canonical_candidates() -> None:
    """A colliding raw bucket must retain and enrich each matching specifier."""
    raw = _raw("../build/a.js")
    issues = (
        _issue("../build/b.js", raw=raw, metadata={"language": "typescript"}),
        _issue("../build/a.js", raw=raw, metadata={"confidence": "high"}),
        _issue("../build/b.js", raw="other provenance", metadata={"confidence": "medium"}),
    )
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1((raw,), issues))
    assert coverage.total_diagnostics == 2
    metadata = {item.diagnostic["metadata"]["specifier"]: item.diagnostic["metadata"] for item in coverage.items}
    assert metadata["../build/a.js"]["confidence"] == "high"
    assert metadata["../build/b.js"]["language"] == "typescript"
    assert metadata["../build/b.js"]["confidence"] == "medium"


def test_unknown_import_identity_is_not_filled_from_stale_raw() -> None:
    """An absent canonical specifier must remain visible without guessed identity."""
    raw = _raw("../build/a.js")
    issue = _issue(None, raw=raw)
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1((raw,), (issue,)))
    assert coverage.total_diagnostics == 2
    unknown = [item for item in coverage.items if "specifier" not in item.diagnostic["metadata"]]
    assert len(unknown) == 1
    assert unknown[0].diagnostic["message"] == "Missing module"


def test_explicit_id_survives_mixed_legacy_refinement() -> None:
    raw = _raw("../build/a.js")
    issue = _issue("../build/a.js", raw=raw, diagnostic_id="explicit-canonical")
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1((raw,), (issue,)))
    assert coverage.total_diagnostics == 1
    assert coverage.items[0].diagnostic["diagnostic_id"] == "explicit-canonical"


@pytest.mark.parametrize("route", ["raw", "typed", "mixed"])
def test_different_importers_keep_complete_public_proposals(route: str) -> None:
    files = {
        "checks/first.ts": 'import { A } from "../build/a.js";\nvoid A;\n',
        "checks/second.ts": 'import { B } from "../build/b.js";\nvoid B;\n',
        "lib/a.ts": "export const A=1;",
        "lib/b.ts": "export const B=2;",
    }
    identities = (("../build/a.js", "checks/first.ts"), ("../build/b.js", "checks/second.ts"))
    errors = tuple(_raw(s, p) for s, p in identities) if route != "typed" else ()
    issues = tuple(_issue(s, path=p, raw=_raw(s, "checks/stale.ts")) for s, p in identities) if route != "raw" else ()
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(errors, artifact_quality_issues=issues, base_files=files, source_tools=(TOOL,))
    )
    assert probe.coverage_report.total_diagnostics == 2
    assert probe.plannable_source_tools == (TOOL,)
    assert {
        p["path"]: p["content_after"] for p in probe.items[0].planning_result.composition_summary.to_dict()["patches"]
    } == {
        "checks/first.ts": 'import { A } from "../lib/a.ts";\nvoid A;\n',
        "checks/second.ts": 'import { B } from "../lib/b.ts";\nvoid B;\n',
    }


def test_non_import_compiler_keeps_legacy_precise_code_and_location() -> None:
    raw = "src/main.ts(3,14): error TS2322: Type 'string' is not assignable to type 'number'."
    issues = tuple(artifact_quality_issues_from_errors((raw,)))
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1((raw,), issues))
    assert coverage.total_diagnostics == 1
    diagnostic = coverage.items[0].diagnostic
    assert diagnostic["code"] == "typescript_ts2322"
    assert diagnostic["path"] == "src/main.ts"
    assert diagnostic["line"] == 3
    assert diagnostic["column"] == 14


@pytest.mark.parametrize("missing", [None, "", 123])
def test_two_unproved_import_identities_remain_visible(missing: Any) -> None:
    """Equal generated IDs must not erase missing or invalid canonical evidence."""
    first = _issue(None, raw="first observation")
    second = _issue(None, raw="second observation")
    if missing is not None:
        first["metadata"]["specifier"] = missing
        second["metadata"]["specifier"] = missing
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1((), (first, second)))
    assert coverage.total_diagnostics == 2
    assert {item.diagnostic["raw"] for item in coverage.items} == {"first observation", "second observation"}
    assert all(item.diagnostic["metadata"].get("specifier") == missing for item in coverage.items)


def test_missing_importer_is_not_proved_by_matching_raw() -> None:
    first = _issue("../build/a.js", raw="same raw evidence")
    second = _issue("../build/a.js", raw="same raw evidence", diagnostic_id="without-importer")
    second.pop("path")
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1((), (first, second)))
    assert coverage.total_diagnostics == 2
    assert {item.diagnostic["path"] for item in coverage.items} == {None, "checks/first.ts"}


def test_shared_explicit_id_cannot_override_conflicting_specifiers() -> None:
    issues = tuple(_issue(spec, diagnostic_id="shared-id") for spec in ("../build/a.js", "../build/b.js"))
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1((), issues))
    assert coverage.total_diagnostics == 2
    assert {item.diagnostic["metadata"]["specifier"] for item in coverage.items} == {"../build/a.js", "../build/b.js"}
    assert all(item.diagnostic["diagnostic_id"] == "shared-id" for item in coverage.items)


@pytest.mark.parametrize("field", ["line", "column", "span_start", "span_end"])
def test_conflicting_canonical_locations_do_not_merge(field: str) -> None:
    issues = [_issue("../build/a.js"), _issue("../build/a.js")]
    issues[0][field] = 2
    issues[1][field] = 4
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1((), tuple(issues)))
    assert coverage.total_diagnostics == 2
    assert {item.diagnostic[field] for item in coverage.items} == {2, 4}


def test_non_import_raw_match_still_adds_location_to_precise_legacy_code() -> None:
    raw = "src/main.ts(3,14): error TS2322: Type 'string' is not assignable to type 'number'."
    issue = {
        "source": "artifact_quality",
        "code": "artifact_quality_issue",
        "message": "Workspace compiler failure",
        "path": "src/main.ts",
        "raw": raw,
        "metadata": {"confidence": "high"},
    }
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1((raw,), (issue,)))
    assert coverage.total_diagnostics == 1
    diagnostic = coverage.items[0].diagnostic
    assert (diagnostic["code"], diagnostic["line"], diagnostic["column"]) == ("typescript_ts2322", 3, 14)
    assert diagnostic["metadata"]["confidence"] == "high"
