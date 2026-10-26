"""Reject test authority leakage while preserving exact integration-test grants."""

from __future__ import annotations

import copy
import json
from importlib.machinery import PathFinder
from pathlib import Path
from typing import Any

import pytest
import yaml
from docs.governance.ci.scripts import run_catalog_governance_gate as gate
from jsonschema import Draft202012Validator

BACKEND_ROOT = Path(__file__).resolve().parents[5]
TEST_A = "polaris/cells/sample/alpha/tests/test_first.py"
TEST_B = "polaris/cells/sample/alpha/tests/test_second.py"
PRODUCTION = "polaris/cells/sample/alpha/public/service.py"
PUBLIC_IMPORT = "from polaris.cells.sample.beta.public import Result\n"
SPAWN = "import subprocess\nsubprocess.run(['python', '-V'], check=True)\n"
AUTHORITY_RULE = "test_authority_invalid"
LEAK_RULE = "production_imports_test_module"
RELEVANT_RULES = {
    AUTHORITY_RULE,
    LEAK_RULE,
    "declared_cell_dependencies_match_imports",
    "undeclared_effects_forbidden",
    "no_cross_cell_internal_import",
}


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _cell(cell_id: str) -> dict[str, Any]:
    family, name = cell_id.split(".")
    return {
        "id": cell_id,
        "kind": "capability",
        "visibility": "public",
        "owner": "test-owner",
        "purpose": "Exercise independent test authority.",
        "owned_paths": [f"polaris/cells/{family}/{name}/**"],
        "public_contracts": {key: [] for key in ("commands", "queries", "events", "results", "errors")},
        "depends_on": [],
        "subgraphs": [],
        "state_owners": [],
        "effects_allowed": [],
        "verification": {},
    }


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, list[dict[str, Any]]]:
    for filename in ("cell.schema.yaml", "cell-catalog.schema.yaml", "subgraph.schema.yaml"):
        source = BACKEND_ROOT / "docs/governance/schemas" / filename
        _write(tmp_path, f"docs/governance/schemas/{filename}", source.read_text(encoding="utf-8"))
    for path in (TEST_A, TEST_B, PRODUCTION, "polaris/cells/sample/beta/public/__init__.py"):
        _write(tmp_path, path, "")
    return tmp_path, [_cell("sample.alpha"), _cell("sample.beta")]


def _save(root: Path, cells: list[dict[str, Any]]) -> None:
    payload = {
        "version": 1,
        "catalog_id": "authority-fixture",
        "description": "Test authority integration fixture.",
        "owners": ["test-owner"],
        "migration_status": "active",
        "cells": cells,
    }
    _write(root, "docs/graph/catalog/cells.yaml", yaml.safe_dump(payload))
    for cell in cells:
        family, name = cell["id"].split(".")
        _write(root, f"polaris/cells/{family}/{name}/cell.yaml", yaml.safe_dump(cell))


def _run(root: Path, cells: list[dict[str, Any]]) -> list[gate.GovernanceIssue]:
    _save(root, cells)
    report, _ = gate.run_governance_gate(
        workspace=str(root), mode="hard-fail", baseline_path=None, mismatch_baseline_path=None
    )
    return [issue for issue in report.issues if issue.rule_id in RELEVANT_RULES]


def _dependency(path: str = TEST_A) -> dict[str, Any]:
    return {"path": path, "depends_on": ["sample.beta"]}


def _effect(path: str = TEST_A) -> dict[str, Any]:
    return {"path": path, "effects_allowed": ["process.spawn:python/recovery_test"], "reason": "Recovery"}


def test_exact_public_test_dependency_does_not_create_production_cycle(workspace: Any) -> None:
    root, cells = workspace
    cells[0]["verification"]["test_dependencies"] = [_dependency()]
    cells[1]["depends_on"] = ["sample.alpha"]
    _write(root, TEST_A, PUBLIC_IMPORT)

    assert _run(root, cells) == []
    graph = gate._build_depends_on_graph(gate._build_cell_index({"cells": cells}))
    assert graph == {"sample.alpha": (), "sample.beta": ("sample.alpha",)}
    assert gate._strongly_connected_components(graph) == []


@pytest.mark.parametrize("source", (PRODUCTION, TEST_B))
def test_test_dependency_cannot_authorize_other_source(workspace: Any, source: str) -> None:
    root, cells = workspace
    cells[0]["verification"]["test_dependencies"] = [_dependency()]
    _write(root, TEST_A, PUBLIC_IMPORT)
    _write(root, source, PUBLIC_IMPORT)

    issues = _run(root, cells)

    assert [(issue.rule_id, issue.path) for issue in issues] == [("declared_cell_dependencies_match_imports", source)]


def test_each_unauthorized_import_is_checked_after_granted_import(workspace: Any) -> None:
    root, cells = workspace
    cells[0]["verification"]["test_dependencies"] = [_dependency()]
    _write(root, TEST_A, PUBLIC_IMPORT)
    _write(root, TEST_B, PUBLIC_IMPORT + PUBLIC_IMPORT)

    issues = _run(root, cells)

    assert [(issue.path, issue.line) for issue in issues] == [(TEST_B, 1), (TEST_B, 2)]


def test_production_dependency_remains_available_to_tests(workspace: Any) -> None:
    root, cells = workspace
    cells[0]["depends_on"] = ["sample.beta"]
    _write(root, TEST_A, PUBLIC_IMPORT)
    assert _run(root, cells) == []


def test_exact_process_test_effect_is_allowed_without_production_permission(workspace: Any) -> None:
    root, cells = workspace
    cells[0]["verification"]["test_effects_allowed"] = [_effect()]
    _write(root, TEST_A, SPAWN)
    assert _run(root, cells) == []
    assert gate._build_cell_index({"cells": cells})[0].effects_allowed == ()


@pytest.mark.parametrize("source", (TEST_A, TEST_B))
def test_unlisted_test_spawn_cannot_borrow_production_effect(workspace: Any, source: str) -> None:
    root, cells = workspace
    cells[0]["effects_allowed"] = ["process.spawn:production_worker"]
    _write(root, source, SPAWN)

    assert [(issue.rule_id, issue.path) for issue in _run(root, cells)] == [("undeclared_effects_forbidden", source)]


@pytest.mark.parametrize("source", (PRODUCTION, TEST_B))
def test_test_effect_cannot_authorize_other_source(workspace: Any, source: str) -> None:
    root, cells = workspace
    cells[0]["verification"]["test_effects_allowed"] = [_effect()]
    _write(root, TEST_A, SPAWN)
    _write(root, source, SPAWN)

    assert [(issue.rule_id, issue.path) for issue in _run(root, cells)] == [("undeclared_effects_forbidden", source)]


def test_all_seven_process_occurrences_remain_visible(workspace: Any) -> None:
    root, cells = workspace
    _write(root, TEST_A, "import subprocess\n" + "subprocess.Popen(['python', '-V'])\n" * 7)

    issues = _run(root, cells)

    assert [issue.line for issue in issues] == [2, 3, 4, 5, 6, 7, 8]
    assert all(issue.rule_id == "undeclared_effects_forbidden" for issue in issues)


@pytest.mark.parametrize(
    "statement",
    (
        "import subprocess as child\nchild.run(['python', '-V'])\n",
        "from subprocess import Popen as launch\nlaunch(['python', '-V'])\n",
        "from subprocess import check_output\ncheck_output(['python', '-V'])\n",
    ),
)
def test_process_import_alias_cannot_borrow_production_permission(workspace: Any, statement: str) -> None:
    root, cells = workspace
    cells[0]["effects_allowed"] = ["process.spawn:production_worker"]
    _write(root, TEST_A, statement)
    assert [(issue.rule_id, issue.path) for issue in _run(root, cells)] == [("undeclared_effects_forbidden", TEST_A)]


def test_legacy_alias_cannot_silently_accept_test_authority(workspace: Any) -> None:
    root, cells = workspace
    cells[0]["verify_targets"] = {"test_dependencies": [_dependency()]}
    assert any(issue.rule_id == AUTHORITY_RULE for issue in _run(root, cells))


@pytest.mark.parametrize(
    "statement",
    (
        "from polaris.cells.sample.beta.internal.secret import value\n",
        "from polaris.cells.sample.beta.internal import value\n",
        "import polaris.cells.sample.beta.internal.secret as secret\n",
        "from polaris.cells.sample.beta.public import _private_value\n",
        "import importlib\nimportlib.import_module('polaris.cells.sample.beta.internal.secret')\n",
    ),
)
@pytest.mark.parametrize("source", (TEST_A, PRODUCTION))
def test_test_grant_never_authorizes_foreign_private_import(workspace: Any, statement: str, source: str) -> None:
    root, cells = workspace
    cells[0]["verification"]["test_dependencies"] = [_dependency()]
    if source == PRODUCTION:
        cells[0]["depends_on"] = ["sample.beta"]
    _write(root, source, statement)

    issues = _run(root, cells)

    assert len(issues) == 1
    assert issues[0].rule_id == "no_cross_cell_internal_import"
    assert issues[0].severity == "blocker"


def test_same_owner_private_unit_test_remains_legal(workspace: Any) -> None:
    root, cells = workspace
    _write(root, TEST_A, "from polaris.cells.sample.alpha.internal import _unit\n")
    assert _run(root, cells) == []


@pytest.mark.parametrize(
    "statement",
    (
        "from polaris.cells.sample.alpha.public import tests\n",
        "from polaris.cells.sample.alpha.public.tests import route\n",
    ),
)
def test_production_module_named_tests_is_not_a_test_directory(workspace: Any, statement: str) -> None:
    root, cells = workspace
    _write(root, "polaris/cells/sample/alpha/public/tests.py", "route = None\n")
    _write(root, PRODUCTION, statement)
    assert _run(root, cells) == []


@pytest.mark.parametrize(
    "statement",
    (
        "import polaris.cells.sample.alpha.public.tests\n",
        "from polaris.cells.sample.alpha.public import tests\n",
        "from . import tests\n",
        "from .tests import helper\n",
        "import importlib\nimportlib.import_module('polaris.cells.sample.alpha.public.tests')\n",
        "__import__('polaris.cells.sample.alpha.public.tests')\n",
    ),
)
def test_test_package_wins_same_name_production_module(workspace: Any, statement: str) -> None:
    root, cells = workspace
    package_path = "polaris/cells/sample/alpha/public/tests/__init__.py"
    _write(root, "polaris/cells/sample/alpha/public/tests.py", "route = None\n")
    _write(root, package_path, SPAWN + "helper = None\n")
    _write(root, PRODUCTION, statement)
    cells[0]["verification"]["test_effects_allowed"] = [_effect(package_path)]
    spec = PathFinder.find_spec("tests", [str(root / "polaris/cells/sample/alpha/public")])
    assert spec is not None and spec.origin == str(root / package_path)

    assert [(issue.rule_id, issue.path) for issue in _run(root, cells)] == [(LEAK_RULE, PRODUCTION)]


def test_production_module_hides_unimportable_test_namespace_descendant(workspace: Any) -> None:
    root, cells = workspace
    _write(root, "polaris/cells/sample/alpha/public/tests.py", "route = None\n")
    _write(root, "polaris/cells/sample/alpha/public/tests/helper.py", "value = None\n")
    _write(root, PRODUCTION, "from .tests import helper\n")
    spec = PathFinder.find_spec("tests", [str(root / "polaris/cells/sample/alpha/public")])
    assert spec is not None and spec.origin == str(root / "polaris/cells/sample/alpha/public/tests.py")
    assert _run(root, cells) == []


@pytest.mark.parametrize(
    ("statement", "line"),
    (
        ("import subprocess as child\nchild.run(['python', '-V'])\ndef unrelated():\n    import math as child\n", 2),
        (
            "def first():\n    import subprocess as child\n    child.run(['python', '-V'])\ndef second():\n    import math as child\n",
            3,
        ),
        (
            "def outer():\n    import subprocess as child\n    def inner():\n        import math as child\n    child.run(['python', '-V'])\n",
            5,
        ),
        ("import subprocess as child\nchild.run(['python', '-V'])\nimport math as child\nchild.sqrt(4)\n", 2),
        (
            "import subprocess as child\nclass Example:\n    import math as child\n    def method(self):\n        child.run(['python', '-V'])\n",
            5,
        ),
        ("def closure():\n    child.run(['python', '-V'])\nimport subprocess as child\n", 2),
        ("import subprocess as child\nif conditional:\n    import math as child\nchild.run(['python', '-V'])\n", 4),
        (
            "import subprocess as child\nfor value in values:\n    import math as child\nchild.run(['python', '-V'])\n",
            4,
        ),
        (
            "import subprocess as child\ntry:\n    import math as child\nexcept Exception:\n    pass\nchild.run(['python', '-V'])\n",
            6,
        ),
        (
            "import subprocess as child\nfunction = lambda: child.run(['python', '-V'])\ndef unrelated():\n    import math as child\n",
            2,
        ),
        (
            "def outer():\n    import subprocess as child\n    class Example:\n        import math as child\n        def method(self):\n            child.run(['python', '-V'])\n",
            6,
        ),
        (
            "import subprocess as child\nmatch value:\n    case 1:\n        import math as child\nchild.run(['python', '-V'])\n",
            5,
        ),
        (
            "import subprocess as child\ntry:\n    import math as child\nexcept* Exception:\n    pass\nchild.run(['python', '-V'])\n",
            6,
        ),
    ),
)
def test_effect_alias_uses_lexical_scope_and_source_order(workspace: Any, statement: str, line: int) -> None:
    root, cells = workspace
    _write(root, PRODUCTION, statement)
    cells[0]["verification"]["test_effects_allowed"] = [_effect()]

    assert [(issue.rule_id, issue.path, issue.line) for issue in _run(root, cells)] == [
        ("undeclared_effects_forbidden", PRODUCTION, line)
    ]


@pytest.mark.parametrize(
    "statement",
    (
        "import subprocess as child\nimport math as child\nchild.sqrt(4)\n",
        "import subprocess as child\ndef function(child):\n    child.run(['python', '-V'])\n",
        "import subprocess as child\ndef function():\n    import math as child\n    child.sqrt(4)\n",
        "import subprocess as child\nclass Example:\n    import math as child\n    child.sqrt(4)\n",
    ),
)
def test_definite_safe_scope_binding_does_not_inherit_process_alias(workspace: Any, statement: str) -> None:
    root, cells = workspace
    _write(root, PRODUCTION, statement)
    assert _run(root, cells) == []


@pytest.mark.parametrize(
    ("statement", "line"),
    (
        ("import subprocess\ndef function(value: subprocess.run(['python', '-V'])):\n    pass\n", 2),
        ("import subprocess\ndef function() -> subprocess.run(['python', '-V']):\n    pass\n", 2),
        ("import subprocess\nfunction = lambda value=subprocess.run(['python', '-V']): value\n", 2),
        ("import subprocess\nfor values[subprocess.run(['python', '-V'])] in values:\n    pass\n", 2),
        ("try:\n    import subprocess as child\nexcept child.run(['python', '-V']):\n    pass\n", 3),
    ),
)
def test_scope_resolution_preserves_calls_in_outer_expressions(workspace: Any, statement: str, line: int) -> None:
    root, cells = workspace
    _write(root, PRODUCTION, statement)
    assert [(issue.rule_id, issue.path, issue.line) for issue in _run(root, cells)] == [
        ("undeclared_effects_forbidden", PRODUCTION, line)
    ]


@pytest.mark.parametrize(
    "statement",
    (
        "from subprocess import run as launch\nfrom math import prod as launch\ndef later():\n    return launch([2, 3])\n",
        "def outer():\n    from subprocess import run as launch\n    from math import prod as launch\n    def later():\n        return launch([2, 3])\n    return later()\n",
        "from subprocess import run as launch\nfrom math import prod as launch\nclass Example:\n    def method(self):\n        return launch([2, 3])\n",
        "from subprocess import run as launch\nfrom math import prod as launch\nlater = lambda: launch([2, 3])\n",
    ),
)
def test_closure_does_not_restore_overwritten_safe_alias(workspace: Any, statement: str) -> None:
    root, cells = workspace
    _write(root, PRODUCTION, statement)
    assert _run(root, cells) == []


@pytest.mark.parametrize(
    "statement",
    (
        "from polaris.cells.sample.alpha.tests.test_first import helper\n",
        "import polaris.cells.sample.alpha.tests.test_first\n",
        "from ..tests import test_first\n",
        "import importlib\nimportlib.import_module('polaris.cells.sample.alpha.tests.test_first')\n",
        "from importlib import import_module as load\nload('polaris.cells.sample.alpha.tests.test_first')\n",
        "__import__('polaris.cells.sample.alpha.tests.test_first')\n",
        "import importlib\nimportlib.import_module('..tests.test_first', __package__)\n",
    ),
)
def test_production_cannot_launder_effect_through_test_helper(workspace: Any, statement: str) -> None:
    root, cells = workspace
    cells[0]["verification"]["test_effects_allowed"] = [_effect()]
    _write(root, TEST_A, SPAWN)
    _write(root, PRODUCTION, statement)

    assert [(issue.rule_id, issue.path) for issue in _run(root, cells)] == [(LEAK_RULE, PRODUCTION)]


@pytest.mark.parametrize(
    "path",
    (
        "/polaris/cells/sample/alpha/tests/test_first.py",
        "C:/polaris/cells/sample/alpha/tests/test_first.py",
        "polaris/cells/sample/alpha/tests/../tests/test_first.py",
        "polaris/cells/sample/alpha/tests/./test_first.py",
        "polaris//cells/sample/alpha/tests/test_first.py",
        "polaris/cells/sample/alpha/tests/test_*.py",
        "polaris/cells/sample/alpha/tests/missing.py",
        "polaris/cells/sample/alpha/tests",
        PRODUCTION,
        "polaris/cells/sample/beta/tests/test_foreign.py",
    ),
)
def test_invalid_test_path_is_blocker(workspace: Any, path: str) -> None:
    root, cells = workspace
    _write(root, "polaris/cells/sample/beta/tests/test_foreign.py", "")
    cells[0]["verification"]["test_dependencies"] = [_dependency(path)]

    issues = _run(root, cells)

    assert any(issue.rule_id == AUTHORITY_RULE and issue.severity == "blocker" for issue in issues)


def test_symlink_escape_cannot_receive_grant(workspace: Any) -> None:
    root, cells = workspace
    target = root / "outside.py"
    target.write_text(PUBLIC_IMPORT, encoding="utf-8")
    (root / TEST_A).unlink()
    (root / TEST_A).symlink_to(target)
    cells[0]["verification"]["test_dependencies"] = [_dependency()]

    assert any(issue.rule_id == AUTHORITY_RULE for issue in _run(root, cells))


@pytest.mark.parametrize("kind", ("test_dependencies", "test_effects_allowed"))
def test_duplicate_paths_cannot_expand_grant(workspace: Any, kind: str) -> None:
    root, cells = workspace
    entry = _dependency() if kind == "test_dependencies" else _effect()
    cells[0]["verification"][kind] = [entry, copy.deepcopy(entry)]
    assert any(issue.rule_id == AUTHORITY_RULE for issue in _run(root, cells))


def test_unknown_dependency_is_blocker(workspace: Any) -> None:
    root, cells = workspace
    cells[0]["verification"]["test_dependencies"] = [{"path": TEST_A, "depends_on": ["missing.cell"]}]
    assert any(issue.rule_id == AUTHORITY_RULE for issue in _run(root, cells))


@pytest.mark.parametrize("reason", ("", "  ", None))
def test_effect_reason_must_be_nonempty(workspace: Any, reason: Any) -> None:
    root, cells = workspace
    cells[0]["verification"]["test_effects_allowed"] = [{**_effect(), "reason": reason}]
    assert any(issue.rule_id == AUTHORITY_RULE for issue in _run(root, cells))


def test_legacy_verification_list_has_no_implicit_authority(workspace: Any) -> None:
    root, cells = workspace
    cells[0]["verification"] = [TEST_A]
    cells[0]["effects_allowed"] = ["process.spawn:production_worker"]
    _write(root, TEST_A, PUBLIC_IMPORT + SPAWN)
    assert {issue.rule_id for issue in _run(root, cells)} == {
        "declared_cell_dependencies_match_imports",
        "undeclared_effects_forbidden",
    }


@pytest.mark.parametrize("grant_in", ("manifest", "catalog"))
@pytest.mark.parametrize("kind", ("test_dependencies", "test_effects_allowed"))
def test_manifest_catalog_test_authority_drift_is_blocker(workspace: Any, grant_in: str, kind: str) -> None:
    root, cells = workspace
    _save(root, cells)
    target = (
        root / "polaris/cells/sample/alpha/cell.yaml"
        if grant_in == "manifest"
        else root / "docs/graph/catalog/cells.yaml"
    )
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    cell = payload if grant_in == "manifest" else payload["cells"][0]
    cell["verification"][kind] = [_dependency() if kind == "test_dependencies" else _effect()]
    target.write_text(yaml.safe_dump(payload), encoding="utf-8")

    report, mismatch = gate.run_governance_gate(
        workspace=str(root), mode="hard-fail", baseline_path=None, mismatch_baseline_path=None
    )

    assert any(item["field"] == kind for item in mismatch["mismatches"])
    assert any(issue.rule_id == AUTHORITY_RULE for issue in report.issues)


def test_invalid_authority_cannot_be_suppressed_by_fail_on_new_baseline(workspace: Any) -> None:
    root, cells = workspace
    cells[0]["verification"]["test_dependencies"] = [_dependency(PRODUCTION)]
    _save(root, cells)
    first, _ = gate.run_governance_gate(
        workspace=str(root), mode="audit-only", baseline_path=None, mismatch_baseline_path=None
    )
    baseline = root / "baseline.json"
    baseline.write_text(json.dumps(first.to_dict()), encoding="utf-8")

    report, _ = gate.run_governance_gate(
        workspace=str(root), mode="fail-on-new", baseline_path=baseline, mismatch_baseline_path=None
    )

    assert report.new_issue_count == 0
    assert report.exit_code == 1


@pytest.mark.parametrize(
    "declaration",
    (
        {"test_dependencies": [{"path": TEST_A, "depends_on": []}]},
        {"test_dependencies": [{"path": TEST_A, "depends_on": ["sample.beta"], "extra": True}]},
        {"test_effects_allowed": [{"path": TEST_A, "effects_allowed": ["process.spawn:*"], "reason": "test"}]},
        {"test_effects_allowed": [{"path": TEST_A, "effects_allowed": ["process.spawn:x"], "reason": " "}]},
    ),
)
def test_schema_rejects_malformed_test_grants(declaration: dict[str, Any]) -> None:
    schema = yaml.safe_load((BACKEND_ROOT / "docs/governance/schemas/cell.schema.yaml").read_text(encoding="utf-8"))
    cell = _cell("sample.alpha")
    cell["verification"] = declaration
    assert list(Draft202012Validator(schema).iter_errors(cell))


def test_schema_accepts_short_meaningful_effect_reason() -> None:
    schema = yaml.safe_load((BACKEND_ROOT / "docs/governance/schemas/cell.schema.yaml").read_text(encoding="utf-8"))
    cell = _cell("sample.alpha")
    cell["verification"] = {"test_effects_allowed": [_effect()]}
    assert list(Draft202012Validator(schema).iter_errors(cell)) == []


@pytest.mark.parametrize("suffix", [".py", ".source"])
@pytest.mark.parametrize("boundary", ["private", "dependency", "effect", "test_helper"])
def test_python_source_artifact_boundaries_are_checked(workspace: Any, suffix: str, boundary: str) -> None:
    """Exec-loaded source fragments carry the same owner and effects as Python modules."""
    root, cells = workspace
    source = f"polaris/cells/sample/alpha/internal/fragment{suffix}"
    if boundary == "private":
        cells[0]["depends_on"] = ["sample.beta"]
        statement = "from polaris.cells.sample.beta.internal import hidden\n"
        expected = "no_cross_cell_internal_import"
    elif boundary == "dependency":
        statement = "from polaris.cells.sample.beta.public import PublicValue\n"
        expected = "declared_cell_dependencies_match_imports"
    elif boundary == "effect":
        statement = "import subprocess\nsubprocess.run(['must-not-execute'])\n"
        expected = "undeclared_effects_forbidden"
    else:
        _write(root, "polaris/cells/sample/alpha/tests/helper.py", "VALUE = 1\n")
        statement = "from polaris.cells.sample.alpha.tests.helper import VALUE\n"
        expected = "production_imports_test_module"
    _write(root, source, statement + "raise RuntimeError('scanner must never execute audited source')\n")

    assert [(issue.rule_id, issue.path) for issue in _run(root, cells)] == [(expected, source)]


@pytest.mark.parametrize("suffix", [".py", ".source"])
def test_python_source_artifact_legal_public_dependency_can_progress(workspace: Any, suffix: str) -> None:
    root, cells = workspace
    cells[0]["depends_on"] = ["sample.beta"]
    _write(
        root,
        f"polaris/cells/sample/alpha/internal/fragment{suffix}",
        "from polaris.cells.sample.beta.public import PublicValue\n"
        "raise RuntimeError('scanner must never execute audited source')\n",
    )
    assert _run(root, cells) == []


def test_python_source_artifact_does_not_interpret_documentation_as_code(workspace: Any) -> None:
    root, cells = workspace
    _write(
        root,
        "polaris/cells/sample/alpha/internal/notes.md",
        "from polaris.cells.sample.beta.internal import hidden\nimport subprocess\nsubprocess.run(['never'])\n",
    )
    assert _run(root, cells) == []
