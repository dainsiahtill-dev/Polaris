"""Architecture fence for WS6 task-boundary failure evidence projection.

``project_task_boundary_failure_to_metadata`` may keep its legacy
``task_boundary_*`` compatibility fields, but structured failure evidence must
flow through the Run Ledger public helper.  The helper owns
``failure_evidence_summary`` generation, so the role-result projection must not
hand-write that summary locally.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ROLE_RESULT_PROJECTION_PY = (
    _BACKEND_ROOT / "cells" / "roles" / "kernel" / "internal" / "kernel" / "role_result_projection.py"
)

_TARGET_FUNCTION = "project_task_boundary_failure_to_metadata"
_RUN_LEDGER_APPEND_HELPER = "append_failure_evidence_to_metadata"
_RUN_LEDGER_PUBLIC_MODULE = "polaris.cells.control_plane.run_ledger.public"
_TASK_BOUNDARY_ROW_HELPER = "task_boundary_failure_evidence_from_verdict"
_TASK_BOUNDARY_ROW_LOCAL = "failure_evidence_row"
_FAILURE_EVIDENCE_SUMMARY_KEY = "failure_evidence_summary"
_LOCAL_SUMMARY_HELPERS = {
    "summarize_failure_evidence_rows",
    "summarize_failed_gate_evidence_context_slot",
}


def _parse_python_file(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _find_function(module: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.iter_child_nodes(module):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in {_ROLE_RESULT_PROJECTION_PY}")


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _string_constant(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _target_expressions(node: ast.Assign | ast.AnnAssign | ast.AugAssign) -> Iterable[ast.expr]:
    if isinstance(node, ast.Assign):
        return node.targets
    return (node.target,)


def _metadata_subscript_key(node: ast.AST) -> str:
    if not isinstance(node, ast.Subscript):
        return ""
    if not isinstance(node.value, ast.Name) or node.value.id != "metadata":
        return ""
    return _string_constant(node.slice)


def _literal_mapping_keys(node: ast.AST | None) -> set[str]:
    if not isinstance(node, ast.Dict):
        return set()
    return {_string_constant(key) for key in node.keys if _string_constant(key)}


def _assigned_metadata_keys(function: ast.FunctionDef) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        for target in _target_expressions(node):
            for child in ast.walk(target):
                key = _metadata_subscript_key(child)
                if key:
                    keys.add(key)
    return keys


def _metadata_mutation_keys(function: ast.FunctionDef) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "metadata":
            continue
        if node.func.attr in {"setdefault", "pop", "__setitem__"}:
            keys.update(_string_constant(argument) for argument in node.args[:1])
        if node.func.attr == "update":
            for argument in node.args:
                keys.update(_literal_mapping_keys(argument))
            keys.update(keyword.arg or "" for keyword in node.keywords)
    return {key for key in keys if key}


def _imported_names_from_public(module: ast.Module) -> set[str]:
    imported: set[str] = set()
    for node in ast.iter_child_nodes(module):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != _RUN_LEDGER_PUBLIC_MODULE:
            continue
        imported.update(alias.asname or alias.name for alias in node.names)
    return imported


def _assigned_from_call(function: ast.FunctionDef, *, target_name: str, call_name: str) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == target_name for target in node.targets):
            continue
        if isinstance(node.value, ast.Call) and _call_name(node.value) == call_name:
            return True
    return False


def test_task_boundary_failure_projection_uses_run_ledger_failure_evidence_helper() -> None:
    module = _parse_python_file(_ROLE_RESULT_PROJECTION_PY)
    function = _find_function(module, _TARGET_FUNCTION)
    call_names = {
        _call_name(node)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
    }

    assert _RUN_LEDGER_APPEND_HELPER in _imported_names_from_public(module), (
        f"{_TARGET_FUNCTION} must use {_RUN_LEDGER_APPEND_HELPER} from "
        f"{_RUN_LEDGER_PUBLIC_MODULE}; TaskBoundary failure evidence projection "
        "must not be re-owned by the role-result projection layer."
    )
    assert _TASK_BOUNDARY_ROW_HELPER in _imported_names_from_public(module), (
        f"{_TARGET_FUNCTION} must use {_TASK_BOUNDARY_ROW_HELPER} from "
        f"{_RUN_LEDGER_PUBLIC_MODULE}; TaskBoundary -> FailureEvidence row "
        "construction belongs to Run Ledger public, not roles.kernel."
    )
    assert _RUN_LEDGER_APPEND_HELPER in call_names, (
        f"{_TARGET_FUNCTION} must append structured task-boundary failures via "
        f"{_RUN_LEDGER_APPEND_HELPER}; local task_boundary_* fields alone do not "
        "feed Run Ledger/ContextOS failure evidence projections."
    )


def test_task_boundary_failure_projection_passes_task_boundary_row_to_run_ledger_helper() -> None:
    function = _find_function(_parse_python_file(_ROLE_RESULT_PROJECTION_PY), _TARGET_FUNCTION)
    helper_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) == _RUN_LEDGER_APPEND_HELPER
    ]

    assert _assigned_from_call(
        function,
        target_name=_TASK_BOUNDARY_ROW_LOCAL,
        call_name=_TASK_BOUNDARY_ROW_HELPER,
    ), (
        f"{_TARGET_FUNCTION} must build TaskBoundary failure evidence through "
        f"Run Ledger public {_TASK_BOUNDARY_ROW_HELPER} before handing it to Run Ledger."
    )
    assert any(
        len(call.args) >= 2
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "metadata"
        and isinstance(call.args[1], ast.Name)
        and call.args[1].id == _TASK_BOUNDARY_ROW_LOCAL
        for call in helper_calls
    ), (
        f"{_TARGET_FUNCTION} must pass metadata and the TaskBoundary failure "
        f"row to {_RUN_LEDGER_APPEND_HELPER}; helper calls found: {len(helper_calls)}"
    )


def test_task_boundary_failure_projection_does_not_write_failure_summary_locally() -> None:
    function = _find_function(_parse_python_file(_ROLE_RESULT_PROJECTION_PY), _TARGET_FUNCTION)
    assigned_keys = _assigned_metadata_keys(function)
    mutated_keys = _metadata_mutation_keys(function)
    local_summary_calls = sorted(
        {
            _call_name(node)
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and _call_name(node) in _LOCAL_SUMMARY_HELPERS
        }
    )

    assert _FAILURE_EVIDENCE_SUMMARY_KEY not in assigned_keys, (
        f"{_TARGET_FUNCTION} must not assign metadata[{_FAILURE_EVIDENCE_SUMMARY_KEY!r}] "
        f"directly.  {_RUN_LEDGER_APPEND_HELPER} owns failure evidence summary "
        "generation and de-duplication."
    )
    assert _FAILURE_EVIDENCE_SUMMARY_KEY not in mutated_keys, (
        f"{_TARGET_FUNCTION} must not mutate metadata[{_FAILURE_EVIDENCE_SUMMARY_KEY!r}] "
        "through metadata.update/setdefault/pop/__setitem__. "
        f"{_RUN_LEDGER_APPEND_HELPER} owns that projection."
    )
    assert not local_summary_calls, (
        f"{_TARGET_FUNCTION} must not route through local failure-evidence summary "
        f"seed helpers.  {_RUN_LEDGER_APPEND_HELPER} owns summary generation; "
        f"local summary calls found: {local_summary_calls}"
    )
