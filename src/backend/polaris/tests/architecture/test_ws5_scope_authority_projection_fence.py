"""Architecture fence for WS5 ScopeAuthority out-of-scope projection.

Out-of-scope materialization failures must not be reported as a local
``out_of_scope_diff`` detail only.  The Director adapter phase that handles
``_collect_workspace_out_of_scope_diff`` must route the failure evidence through
a ScopeAuthority-backed helper and project the resulting
``task_boundary_scope_filter`` metadata.

Factory router must not locally reconstruct owner-handoff matching, identifier
token extraction, routing-key derivation, or handoff-summary fields.  These
projections are defined in KernelOne ``scope_authority`` and must be consumed
through the canonical public functions.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_EXECUTE_METHOD_PY = _BACKEND_ROOT / "cells" / "roles" / "adapters" / "internal" / "director" / "execute_method.py"
_QUALITY_GATE_PY = _BACKEND_ROOT / "cells" / "roles" / "adapters" / "internal" / "director" / "quality_gate.py"
_FACTORY_ROUTER_PY = _BACKEND_ROOT / "delivery" / "http" / "routers" / "factory.py"

_TARGET_FUNCTION = "_phase_no_materialized_changes"
_OUT_OF_SCOPE_DIFF_HELPER = "_collect_workspace_out_of_scope_diff"
_CANONICAL_SCOPE_FILTER_HELPER = "_task_boundary_scope_filter_evidence"
_TASK_BOUNDARY_SCOPE_FILTER_KEY = "task_boundary_scope_filter"
_OWNERSHIP_HANDOFF_REQUESTS_KEY = "ownership_handoff_requests"

# Canonical KernelOne owner-handoff identifiers.  Factory must import these
# from ``polaris.kernelone.quality.scope_authority`` and must not locally
# define functions whose names collide with or reimplement them.
_OWNER_HANDOFF_CANONICAL_FUNCTIONS: tuple[str, ...] = (
    "build_owner_handoff_index",
    "owner_handoff_index_summary",
    "resolve_owner_handoff_routing",
    "task_record_routing_key",
    "matching_owner_handoff_request",
    "task_record_identifier_tokens",
    "owner_handoff_identifier_tokens",
    "ownership_handoff_requests_from_scope_payload",
    "owner_task_retry_handoff_requests_from_scope_payload",
    "unresolved_owner_handoff_requests_from_scope_payload",
)

# Fields that ``owner_handoff_index_summary`` computes.  Factory must not
# locally build these with inline count/list logic.
_OWNER_HANDOFF_SUMMARY_FIELDS: tuple[str, ...] = (
    "ownership_handoff_count",
    "matched_owner_handoff_count",
    "matched_owner_handoff_routes",
    "unmatched_owner_handoff_count",
    "unmatched_owner_handoff_requests",
    "unknown_owner_handoff_count",
    "unknown_owner_handoff_requests",
)


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
        _node_lineno(node) for node in ast.walk(function) if isinstance(node, ast.Call) and _call_name(node) == name
    ]
    if not call_lines:
        raise AssertionError(f"{name!r} call not found in {_TARGET_FUNCTION}")
    return min(call_lines)


def test_out_of_scope_diff_helper_is_scope_authority_backed() -> None:
    function = _find_function(_parse_python_file(_QUALITY_GATE_PY), _OUT_OF_SCOPE_DIFF_HELPER)
    call_names = {_call_name(node) for node in ast.walk(function) if isinstance(node, ast.Call)}
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


# ---------------------------------------------------------------------------
# Factory router fences: Factory must not locally rebuild owner-handoff
# matching, routing-key derivation, or summary-field computation.
# ---------------------------------------------------------------------------


def _module_top_level_function_names(module: ast.Module) -> set[str]:
    """Return the set of top-level ``def`` names in *module*."""
    return {
        node.name for node in ast.iter_child_nodes(module) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _module_imported_names(module: ast.Module) -> set[str]:
    """Return the set of names imported at module top level."""
    names: set[str] = set()
    for node in ast.iter_child_nodes(module):
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _module_scope_authority_import_names(module: ast.Module) -> set[str]:
    """Return names imported from scope_authority or kernelone.quality."""
    names: set[str] = set()
    for node in ast.iter_child_nodes(module):
        if not isinstance(node, ast.ImportFrom):
            continue
        module_name = node.module or ""
        if "scope_authority" not in module_name and "kernelone.quality" not in module_name:
            continue
        for alias in node.names:
            names.add(alias.asname or alias.name)
    return names


def test_factory_does_not_locally_define_canonical_scope_authority_functions() -> None:
    """Factory must not locally define functions that reimplement or shadow
    KernelOne ScopeAuthority owner-handoff logic.

    Canonical names like ``build_owner_handoff_index``,
    ``task_record_routing_key``, and ``matching_owner_handoff_request`` belong
    to ``polaris.kernelone.quality.scope_authority``.  If Factory needs them it
    must import them, not redefine owner-matching, identifier-token, or
    routing-key logic inline.
    """

    module = _parse_python_file(_FACTORY_ROUTER_PY)
    local_names = _module_top_level_function_names(module)
    collisions = local_names & set(_OWNER_HANDOFF_CANONICAL_FUNCTIONS)

    assert not collisions, (
        "Factory router must not locally define ScopeAuthority canonical "
        "functions.  Import them from polaris.kernelone.quality.scope_authority "
        f"instead.  Collisions found: {sorted(collisions)}"
    )


def test_factory_quality_gate_owner_handoff_routing_delegates_to_kernelone() -> None:
    """Factory owner-handoff routing must delegate to KernelOne.

    ``_quality_gate_owner_handoff_routing`` is Factory's thin adapter from
    task-row entries to ScopeAuthority's canonical routing projection. It must
    call ``resolve_owner_handoff_routing`` instead of rebuilding owner matching
    with local identifier-token, routing-key, index, or summary heuristics.
    """

    module = _parse_python_file(_FACTORY_ROUTER_PY)
    routing_function = _find_function(module, "_quality_gate_owner_handoff_routing")
    index_function = _find_function(module, "_quality_gate_owner_handoff_index")
    index_call_names = {_call_name(node) for node in ast.walk(index_function) if isinstance(node, ast.Call)}
    assert "_quality_gate_owner_handoff_routing" in index_call_names, (
        "_quality_gate_owner_handoff_index must be a compatibility projection "
        "from the canonical Factory routing adapter."
    )

    function = routing_function
    call_names = {_call_name(node) for node in ast.walk(function) if isinstance(node, ast.Call)}

    assert "resolve_owner_handoff_routing" in call_names, (
        "_quality_gate_owner_handoff_routing must call "
        "resolve_owner_handoff_routing from KernelOne scope_authority; local "
        "owner-matching or summary heuristics are forbidden by WS5."
    )


def test_factory_does_not_locally_build_owner_handoff_summary_fields() -> None:
    """Factory must not locally compute owner-handoff summary count/list fields.

    The canonical summary shape is defined by ``owner_handoff_index_summary``
    in KernelOne scope_authority.  Factory must call that function instead of
    locally counting matched/unmatched/unknown handoff requests.
    """

    module = _parse_python_file(_FACTORY_ROUTER_PY)
    factory_imports = _module_scope_authority_import_names(module)

    assert "owner_handoff_index_summary" in factory_imports, (
        "Factory must import owner_handoff_index_summary from polaris.kernelone.quality.scope_authority."
    )

    # Verify Factory does not hand-build any summary fields locally.  We check
    # that the canonical summary keys only appear in calls to
    # ``owner_handoff_index_summary`` spread-arguments, not in local dict
    # literals or assignments outside of that call context.
    summary_key_mentions: list[tuple[int, str]] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                key_name = _string_constant(key)
                if key_name in _OWNER_HANDOFF_SUMMARY_FIELDS:
                    summary_key_mentions.append((_node_lineno(node), key_name))

    # dict literals containing summary keys are only allowed inside calls to
    # ``owner_handoff_index_summary`` (which returns such a dict).  Since we
    # are walking the *Factory module*, any mention in a local dict literal is
    # a fence violation.
    assert not summary_key_mentions, (
        "Factory must not locally build owner-handoff summary fields.  "
        "Use owner_handoff_index_summary() from KernelOne instead.  "
        f"Local summary fields found: {summary_key_mentions}"
    )


def test_factory_does_not_locally_extract_handoff_payloads_or_identifier_tokens() -> None:
    """Factory must use KernelOne's payload extraction and identifier-token
    helpers rather than reimplementing scope-authority payload shape knowledge.
    """

    module = _parse_python_file(_FACTORY_ROUTER_PY)
    factory_imports = _module_scope_authority_import_names(module)
    local_names = _module_top_level_function_names(module)

    # Factory must import the canonical handoff extractor
    assert "ownership_handoff_requests_from_scope_payload" in factory_imports, (
        "Factory must import ownership_handoff_requests_from_scope_payload "
        "from KernelOne scope_authority for extracting handoff requests from "
        "scope payloads."
    )

    # Factory must import the canonical routing key
    assert "task_record_routing_key" in factory_imports, (
        "Factory must import task_record_routing_key from KernelOne "
        "scope_authority for task-record routing key derivation."
    )

    # Factory must not locally define functions that reimplement
    # handoff-payload extraction or identifier-token logic.
    forbidden_local_names = {
        "ownership_handoff_requests_from_scope_payload",
        "owner_task_retry_handoff_requests_from_scope_payload",
        "unresolved_owner_handoff_requests_from_scope_payload",
        "task_record_identifier_tokens",
        "owner_handoff_identifier_tokens",
        "matching_owner_handoff_request",
    }
    collisions = local_names & forbidden_local_names

    assert not collisions, (
        "Factory must not locally define handoff-payload extraction or "
        "identifier-token functions.  Import them from KernelOne scope_authority. "
        f"Collisions: {sorted(collisions)}"
    )
