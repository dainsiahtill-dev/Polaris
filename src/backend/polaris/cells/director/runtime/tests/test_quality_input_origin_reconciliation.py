"""Same input origin refines opaque fallback without erasing distinct findings."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from polaris.cells.director.runtime.public import QueryDirectorRepairCoverageV1, query_director_repair_coverage
from polaris.kernelone.quality import artifact_quality_issues_from_errors

RAW = "Artifact quality scan failed: undeclared runtime import 'mongoose' in src/models/auditlog.ts"


def _typed(raw: str = RAW) -> dict[str, Any]:
    return {
        "source": "artifact_quality",
        "code": "undeclared_runtime_import",
        "message": "undeclared runtime import 'mongoose' in src/models/auditlog.ts",
        "path": "src/models/auditlog.ts",
        "raw": raw,
        "metadata": {"specifier": "mongoose", "package_root": "mongoose"},
    }


def _coverage(errors: tuple[str, ...], issues: tuple[dict[str, Any], ...]) -> Any:
    before = copy.deepcopy((errors, issues))
    result = query_director_repair_coverage(QueryDirectorRepairCoverageV1(errors, issues))
    assert before == (errors, issues)
    return result


def test_same_origin_typed_import_replaces_only_opaque_legacy_fallback() -> None:
    issues = tuple(artifact_quality_issues_from_errors((RAW,)))
    result = _coverage((RAW,), issues)
    assert result.total_diagnostics == 1
    assert result.covered_diagnostic_count == 1
    finding = result.items[0].diagnostic
    assert finding["code"] == "undeclared_runtime_import"
    assert finding["path"] == "src/models/auditlog.ts"
    assert finding["metadata"]["specifier"] == "mongoose"
    assert finding["raw"] == RAW


def test_refining_one_origin_does_not_remove_a_second_raw_input() -> None:
    other = "Artifact quality scan failed: undeclared runtime import 'express' in src/server.ts"
    result = _coverage((RAW, other), (_typed(),))
    assert result.total_diagnostics == 2
    assert {item.diagnostic["raw"] for item in result.items} == {RAW, other}
    assert {item.diagnostic["code"] for item in result.items} == {"undeclared_runtime_import", "artifact_quality_error"}


@pytest.mark.parametrize("conflict", ["path", "code", "source", "owner_path", "specifier", "explicit_id"])
@pytest.mark.parametrize("reverse", [False, True])
def test_shared_origin_does_not_merge_distinct_typed_identities(conflict: str, reverse: bool) -> None:
    first, second = _typed(), _typed()
    if conflict == "path":
        second["path"] = "src/other.ts"
    elif conflict == "code":
        second["code"] = "unsupported_import_observation"
    elif conflict == "source":
        second["source"] = "other_owner"
    elif conflict == "owner_path":
        first["metadata"]["owner_path"] = "src/first.ts"
        second["metadata"]["owner_path"] = "src/second.ts"
    elif conflict == "specifier":
        second["metadata"]["specifier"] = "Mongoose"
    else:
        first["diagnostic_id"] = "first-finding"
        second["diagnostic_id"] = "second-finding"
    issues = (second, first) if reverse else (first, second)
    # Typed-only evidence must retain the same identity distinctions as mixed
    # evidence; raw prose cannot erase a path/code/source/owner discriminator.
    typed = _coverage((), issues)
    assert typed.total_diagnostics == 2
    mixed = _coverage((RAW,), issues)
    assert mixed.total_diagnostics == 2
    if conflict == "code":
        assert {item.diagnostic["code"] for item in mixed.items} == {
            "undeclared_runtime_import",
            "unsupported_import_observation",
        }
    elif conflict == "source":
        assert {item.diagnostic["source"] for item in mixed.items} == {"artifact_quality", "other_owner"}
    elif conflict == "owner_path":
        assert {item.diagnostic["metadata"]["owner_path"] for item in mixed.items} == {"src/first.ts", "src/second.ts"}


@pytest.mark.parametrize("invalid", ["code_type", "path_type", "missing_specifier", "specifier_type", "no_origin"])
def test_invalid_or_unbound_typed_input_cannot_hide_legacy_finding(invalid: str) -> None:
    issue = _typed()
    if invalid == "code_type":
        issue["code"] = 123
    elif invalid == "path_type":
        issue["path"] = 123
    elif invalid == "missing_specifier":
        issue["metadata"].pop("specifier")
    elif invalid == "specifier_type":
        issue["metadata"]["specifier"] = 123
    else:
        issue.pop("raw")
    result = _coverage((RAW,), (issue,))
    fallback = [item.diagnostic for item in result.items if item.diagnostic["code"] == "artifact_quality_error"]
    assert len(fallback) == 1
    assert fallback[0]["raw"] == RAW
    assert fallback[0]["path"] is None


def test_supported_typed_origin_does_not_promote_an_unknown_independent_input() -> None:
    unknown = "unclassified artifact predicate failed [unknown-marker]"
    result = _coverage((RAW, unknown), (_typed(),))
    assert result.total_diagnostics == 2
    assert result.to_dict()["coverage_gap_count"] == 1
    uncovered = [item for item in result.items if not item.known_rule_matched]
    assert len(uncovered) == 1
    assert uncovered[0].diagnostic["raw"] == unknown


def test_specific_unsupported_typed_origin_remains_an_uncovered_finding() -> None:
    raw = "unclassified artifact predicate failed [unknown-marker]"
    issue = {
        "source": "artifact_quality",
        "code": "future_unregistered_quality_observation",
        "message": "unclassified artifact predicate failed",
        "path": "src/unknown.xyz",
        "raw": raw,
    }
    result = _coverage((raw,), (issue,))
    assert result.total_diagnostics == 1
    assert result.to_dict()["coverage_gap_count"] == 1
    assert result.items[0].known_rule_matched is False
    assert result.items[0].diagnostic["code"] == "future_unregistered_quality_observation"
    assert result.items[0].diagnostic["raw"] == raw
