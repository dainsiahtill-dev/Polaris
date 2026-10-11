"""Canonical missing identity must never authorize an import from raw provenance."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest
from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairMaterializationPlanProbeV1,
    QueryDirectorRepairPlanProbeV1,
    plan_director_repair,
    query_director_repair_coverage,
    query_director_repair_materialization_plan_probe,
    query_director_repair_plan_probe,
)

TOOL = "deterministic_typescript_unique_export_import_repair"
TOOLS = (
    TOOL,
    "deterministic_typescript_relative_import_case_repair",
    "deterministic_typescript_unused_import_repair",
)
SOURCE = '// canonical current 😀\nimport { Unit } from "../missing/unit.js"; void Unit;\n'
STALE = '// stale owner 😀\nimport { Unit } from "../missing/unit.js"; void Unit;\n'
EXPECTED = '// canonical current 😀\nimport { Unit } from "../lib/unit.ts"; void Unit;\n'


def _files() -> dict[str, str]:
    return {"checks/current.ts": SOURCE, "checks/stale.ts": STALE, "lib/unit.ts": "export const Unit=1;\n"}


def _issue(metadata: dict[str, Any], *, raw_path: str = "checks/stale.ts") -> dict[str, Any]:
    return {
        "source": "artifact_quality",
        "code": "unresolved_relative_import",
        "path": "checks/current.ts",
        "message": "Missing module",
        "metadata": metadata,
        "raw": f"unresolved relative import '../missing/unit.js' in {raw_path}",
    }


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _plan(
    interface: str,
    issues: tuple[dict[str, Any], ...],
    *,
    errors: tuple[str, ...] = (),
    files: dict[str, str] | None = None,
    tool: str = TOOL,
) -> dict[str, Any]:
    base = _files() if files is None else files
    command: Any
    if interface == "direct":
        command = PlanDirectorRepairCommandV1(
            tool, base_files=base, artifact_quality_errors=errors, artifact_quality_issues=issues, mode="shadow"
        )
        call = plan_director_repair
    elif interface == "probe":
        command = QueryDirectorRepairPlanProbeV1(
            errors, artifact_quality_issues=issues, base_files=base, source_tools=(tool,), mode="shadow"
        )
        call = query_director_repair_plan_probe
    else:
        command = QueryDirectorRepairMaterializationPlanProbeV1(
            errors, artifact_quality_issues=issues, base_files=base, source_tools=(tool,), mode="shadow"
        )
        call = query_director_repair_materialization_plan_probe
    before = copy.deepcopy((base, issues, errors, vars(command)))
    before_hash = _hash(before)
    result = call(command)
    assert before == (base, issues, errors, vars(command))
    assert before_hash == _hash((base, issues, errors, vars(command)))
    if interface == "materialization":
        if result.plan_probe_result is None:
            return dict(result.to_dict())
        result = result.plan_probe_result
    if interface != "direct":
        result = result.items[0].planning_result
    return dict(result.to_dict())


def _assert_no_plan(result: dict[str, Any]) -> None:
    if "runtime_plan_probe" in result:
        assert result["runtime_plan_probe"] is None
        assert result["plannable_source_tools"] == []
    else:
        assert result["planned"] is False
        assert result["composition_summary"]["patches"] == []
        assert result["plan_summary"] is None


@pytest.mark.parametrize("interface", ["direct", "probe", "materialization"])
@pytest.mark.parametrize("raw_path", ["checks/stale.ts", "checks/current.ts"])
@pytest.mark.parametrize("tool", TOOLS)
def test_missing_canonical_specifier_cannot_authorize_raw_owner(interface: str, raw_path: str, tool: str) -> None:
    """The old key-presence guard authorized a stale import; code must fail closed."""
    files = _files()
    issue = _issue({}, raw_path=raw_path)
    if tool.endswith("case_repair"):
        files = {
            "checks/current.ts": "import { Moon } from './Moon';\nexport class Garden { moon = new Moon(); }\n",
            "checks/stale.ts": "import { Moon } from './Moon';\nexport class Stale { moon = new Moon(); }\n",
            "checks/moon.ts": "export class Moon {}\n",
        }
        issue["raw"] = f"unresolved relative import './Moon' in {raw_path}"
    if tool.endswith("unused_import_repair"):
        files["checks/current.ts"] = SOURCE.replace(" void Unit;", "")
        files["checks/stale.ts"] = STALE.replace(" void Unit;", "")
    result = _plan(interface, (issue,), files=files, tool=tool)
    _assert_no_plan(result)


@pytest.mark.parametrize("interface", ["direct", "probe", "materialization"])
def test_explicit_case_identity_still_corrects_current_path(interface: str) -> None:
    files = {
        "checks/current.ts": "import { Moon } from './Moon';\nexport class Garden { moon = new Moon(); }\n",
        "checks/stale.ts": "import { Moon } from './Moon';\nexport class Stale { moon = new Moon(); }\n",
        "checks/moon.ts": "export class Moon {}\n",
    }
    issue = _issue({"specifier": "./Moon"})
    issue["raw"] = "unresolved relative import './Moon' in checks/stale.ts"
    result = _plan(interface, (issue,), files=files, tool=TOOLS[1])
    assert result["planned"] is True
    assert {p["path"]: p["content_after"] for p in result["composition_summary"]["patches"]} == {
        "checks/current.ts": "import { Moon } from './moon';\nexport class Garden { moon = new Moon(); }\n"
    }


@pytest.mark.parametrize("interface", ["direct", "probe", "materialization"])
@pytest.mark.parametrize("value", [None, "", " ", 123, [], {}, "pkg", ".broken"])
def test_invalid_canonical_specifier_stays_rejected(interface: str, value: Any) -> None:
    result = _plan(interface, (_issue({"specifier": value}),))
    _assert_no_plan(result)


@pytest.mark.parametrize("interface", ["direct", "probe", "materialization"])
@pytest.mark.parametrize(
    "raw",
    [
        "unresolved relative import '../missing/unit.js' in checks/stale.ts",
        "unresolved relative import '../wrong/other.js' in checks/stale.ts",
        "malformed import provenance",
    ],
)
def test_explicit_canonical_identity_repairs_current_only(interface: str, raw: str) -> None:
    issue = _issue({"specifier": "../missing/unit.js"})
    issue["raw"] = raw
    result = _plan(interface, (issue,))
    assert result["planned"] is True
    patches = result["composition_summary"]["patches"]
    assert {p["path"]: p["content_after"] for p in patches} == {"checks/current.ts": EXPECTED}
    assert patches[0]["before_hash"] == hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("interface", ["direct", "probe", "materialization"])
@pytest.mark.parametrize("path", [None, "", "checks/absent.ts"])
def test_invalid_canonical_path_does_not_borrow_stale_path(interface: str, path: str | None) -> None:
    issue = _issue({"specifier": "../missing/unit.js"})
    issue["path"] = path
    result = _plan(interface, (issue,))
    _assert_no_plan(result)


@pytest.mark.parametrize("interface", ["direct", "probe", "materialization"])
@pytest.mark.parametrize(
    "errors",
    [
        ("unresolved relative import '../missing/unit.js' in checks/current.ts",),
        (
            "checks/current.ts(2,22): error TS2307: Cannot find module '../missing/unit.js' or its corresponding type declarations.",
            "unresolved relative import '../missing/unit.js' in checks/current.ts",
        ),
    ],
)
def test_legacy_raw_only_still_repairs_exact_current_import(interface: str, errors: tuple[str, ...]) -> None:
    result = _plan(interface, (), errors=errors)
    assert result["planned"] is True
    assert {p["path"]: p["content_after"] for p in result["composition_summary"]["patches"]} == {
        "checks/current.ts": EXPECTED
    }


@pytest.mark.parametrize("interface", ["direct", "probe", "materialization"])
def test_bare_ts2307_keeps_existing_unplannable_boundary(interface: str) -> None:
    """The relative-import tool does not infer scanner identity from TS2307 alone."""
    result = _plan(
        interface,
        (),
        errors=(
            "checks/current.ts(2,22): error TS2307: Cannot find module '../missing/unit.js' or its corresponding type declarations.",
        ),
    )
    _assert_no_plan(result)


@pytest.mark.parametrize("interface", ["direct", "probe", "materialization"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
def test_unknown_duplicate_evidence_does_not_displace_valid_canonical_import(
    interface: str, reverse: bool, mixed: bool
) -> None:
    unknown = _issue({})
    explicit = _issue({"specifier": "../missing/unit.js"})
    issues: tuple[dict[str, Any], ...] = (unknown, explicit, unknown, explicit)
    if reverse:
        issues = tuple(reversed(issues))
    errors = ("unresolved relative import '../missing/unit.js' in checks/current.ts",) if mixed else ()
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(errors, issues))
    # Unknown identities conservatively remain separate coverage evidence;
    # the explicit duplicate coalesces into one proven canonical finding.
    assert coverage.total_diagnostics == 3
    assert sorted(item.diagnostic["metadata"].get("specifier", "") for item in coverage.items) == [
        "",
        "",
        "../missing/unit.js",
    ]
    assert {item.diagnostic["path"] for item in coverage.items} == {"checks/current.ts"}
    result = _plan(interface, issues, errors=errors)
    assert result["planned"] is True
    assert {p["path"]: p["content_after"] for p in result["composition_summary"]["patches"]} == {
        "checks/current.ts": EXPECTED
    }
    assert len(result["diagnostics"]) == (2 if interface == "direct" else 3)
