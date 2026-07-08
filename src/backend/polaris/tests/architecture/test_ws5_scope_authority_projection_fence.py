"""Architecture fence for WS5 ScopeAuthority out-of-scope projection.

Out-of-scope materialization failures must not be reported as a local
``out_of_scope_diff`` detail only.  The Director adapter phase that handles
``_collect_workspace_out_of_scope_diff`` must route the failure evidence through
a ScopeAuthority-backed helper and project the resulting
``task_boundary_scope_filter`` metadata.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_EXECUTE_METHOD_PY = (
    _BACKEND_ROOT / "cells" / "roles" / "adapters" / "internal" / "director" / "execute_method.py"
)
_QUALITY_GATE_PY = _BACKEND_ROOT / "cells" / "roles" / "adapters" / "internal" / "director" / "quality_gate.py"

_TARGET_FUNCTION = "_phase_no_materialized_changes"
_OUT_OF_SCOPE_DIFF_HELPER = "_collect_workspace_out_of_scope_diff"
_CANONICAL_SCOPE_FILTER_HELPER = "_task_boundary_scope_filter_evidence"
_TASK_BOUNDARY_SCOPE_FILTER_KEY = "task_boundary_scope_filter"
_OWNERSHIP_HANDOFF_REQUESTS_KEY = "ownership_handoff_requests"


def _parse_python_file(path: Path) -> ast.Module:
    source = path.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(path))


def _find_function(module: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.iter_child_nodes(module):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in {_EXECUTE_METHOD_PY}")


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _node_lineno(node: ast.AST) -> int:
    return int(getattr(node, "lineno", 0) or 0)


def _string_constant(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dict_string_keys(function: ast.FunctionDef) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if key_name := _string_constant(key):
                keys.add(key_name)
    return keys


def _assigned_subscript_string_keys(function: ast.FunctionDef, *, after_line: int = 0) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(function):
        if _node_lineno(node) <= after_line:
            continue
        if not isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            continue
        targets: Iterable[ast.expr] = node.targets if isinstance(node, ast.Assign) else (node.target,)
        for target in targets:
            for child in ast.walk(target):
                if isinstance(child, ast.Subscript) and (key_name := _string_constant(child.slice)):
                    keys.add(key_name)
    return keys


def _dict_string_keys_after_line(function: ast.FunctionDef, *, after_line: int) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(function):
        if _node_lineno(node) <= after_line or not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if key_name := _string_constant(key):
                keys.add(key_name)
    return keys


def _first_call_line(function: ast.FunctionDef, name: str) -> int:
    call_lines = [
        _node_lineno(node)
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) == name
    ]
    if not call_lines:
        raise AssertionError(f"{name!r} call not found in {_TARGET_FUNCTION}")
    return min(call_lines)


def test_out_of_scope_diff_helper_is_scope_authority_backed() -> None:
    function = _find_function(_parse_python_file(_QUALITY_GATE_PY), _OUT_OF_SCOPE_DIFF_HELPER)
    call_names = {
        _call_name(node)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
    }
    projected_keys = _dict_string_keys(function) | _assigned_subscript_string_keys(function)

    assert _CANONICAL_SCOPE_FILTER_HELPER in call_names, (
        f"{_OUT_OF_SCOPE_DIFF_HELPER} must build out-of-scope materialization "
        f"evidence through {_CANONICAL_SCOPE_FILTER_HELPER}; local "
        "out_of_scope_diff fields alone cannot route ownership handoff."
    )
    assert _TASK_BOUNDARY_SCOPE_FILTER_KEY in projected_keys, (
        f"{_OUT_OF_SCOPE_DIFF_HELPER} must return {_TASK_BOUNDARY_SCOPE_FILTER_KEY!r} "
        "with the out-of-scope diff so Director failure metadata can project "
        "the ScopeAuthority decision."
    )


def test_out_of_scope_materialization_failure_projects_scope_authority_filter() -> None:
    function = _find_function(_parse_python_file(_EXECUTE_METHOD_PY), _TARGET_FUNCTION)
    out_of_scope_diff_line = _first_call_line(function, _OUT_OF_SCOPE_DIFF_HELPER)

    projected_keys = _dict_string_keys(function) | _assigned_subscript_string_keys(function)
    projected_after_diff = _dict_string_keys_after_line(
        function,
        after_line=out_of_scope_diff_line,
    ) | _assigned_subscript_string_keys(
        function,
        after_line=out_of_scope_diff_line,
    )

    assert _TASK_BOUNDARY_SCOPE_FILTER_KEY in projected_keys, (
        f"{_TARGET_FUNCTION} must project the ScopeAuthority decision under "
        f"{_TASK_BOUNDARY_SCOPE_FILTER_KEY!r}; a local out_of_scope_diff-only "
        "metadata field is not enough for WS5."
    )
    assert _TASK_BOUNDARY_SCOPE_FILTER_KEY in projected_after_diff, (
        f"{_TARGET_FUNCTION} must project the ScopeAuthority decision under "
        f"{_TASK_BOUNDARY_SCOPE_FILTER_KEY!r} after collecting out-of-scope "
        "diff evidence."
    )


def test_execute_method_does_not_locally_build_ownership_handoff_requests() -> None:
    module = _parse_python_file(_EXECUTE_METHOD_PY)

    local_dict_builders = [
        _node_lineno(node)
        for node in ast.walk(module)
        if isinstance(node, ast.Dict)
        and any(_string_constant(key) == _OWNERSHIP_HANDOFF_REQUESTS_KEY for key in node.keys)
    ]

    assert not local_dict_builders, (
        f"execute_method.py must not hand-write {_OWNERSHIP_HANDOFF_REQUESTS_KEY!r} "
        "dict payloads.  Reuse the quality_gate/KernelOne ScopeAuthority helper "
        f"instead.  Local builders found on lines: {local_dict_builders}"
    )
