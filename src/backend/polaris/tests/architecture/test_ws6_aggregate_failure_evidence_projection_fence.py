"""Architecture fence for WS6 aggregate failure-evidence projection ownership.

Aggregate chat may build failure-evidence metadata for an aggregate role plan,
but Run Ledger public owns the canonical merge/projection semantics.  The
runtime must not reintroduce local shape assumptions such as
``dict(plan.metadata.get("failure_evidence") or {})`` when consuming an
aggregate plan.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_POLARIS_ROOT = Path(__file__).resolve().parents[2]
_AGGREGATE_CHAT_PY = _POLARIS_ROOT / "cells" / "roles" / "runtime" / "public" / "aggregate_chat.py"

_RUN_LEDGER_PUBLIC_MODULE = "polaris.cells.control_plane.run_ledger.public"
_RUN_LEDGER_MERGE_HELPER = "merge_failure_evidence_payload"
_EXTRACT_FAILURE_EVIDENCE = "_extract_failure_evidence"
_AGGREGATE_PLAN_FAILURE_EVIDENCE_PAYLOAD = "_aggregate_plan_failure_evidence_payload"
_FAILURE_EVIDENCE_KEY = "failure_evidence"
_ALLOWED_PLAN_METADATA_FAILURE_EVIDENCE_ACCESSORS = frozenset({_AGGREGATE_PLAN_FAILURE_EVIDENCE_PAYLOAD})


@dataclass(frozen=True)
class _ShapeAssumptionOffender:
    """Local failure-evidence shape assumption discovered in aggregate_chat."""

    function_name: str
    line: int
    expression: str


def _parse_python_file(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _find_function(module: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.iter_child_nodes(module):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in {_AGGREGATE_CHAT_PY}")


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


def _is_plan_metadata_attribute(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "metadata"
        and isinstance(node.value, ast.Name)
        and node.value.id == "plan"
    )


def _is_plan_metadata_failure_evidence_get(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and _is_plan_metadata_attribute(node.func.value)
        and bool(node.args)
        and _string_constant(node.args[0]) == _FAILURE_EVIDENCE_KEY
    )


def _is_plan_metadata_failure_evidence_subscript(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and _is_plan_metadata_attribute(node.value)
        and _string_constant(node.slice) == _FAILURE_EVIDENCE_KEY
    )


def _contains_plan_metadata_failure_evidence_lookup(node: ast.AST) -> bool:
    return any(
        _is_plan_metadata_failure_evidence_get(child) or _is_plan_metadata_failure_evidence_subscript(child)
        for child in ast.walk(node)
    )


def _dict_call_wraps_plan_metadata_failure_evidence(call: ast.Call) -> bool:
    if _call_name(call) != "dict":
        return False
    return any(_contains_plan_metadata_failure_evidence_lookup(argument) for argument in call.args) or any(
        keyword.value is not None and _contains_plan_metadata_failure_evidence_lookup(keyword.value)
        for keyword in call.keywords
    )


def _top_level_functions(module: ast.Module) -> tuple[ast.FunctionDef, ...]:
    return tuple(node for node in ast.iter_child_nodes(module) if isinstance(node, ast.FunctionDef))


def _local_failure_evidence_shape_assumptions(module: ast.Module) -> list[_ShapeAssumptionOffender]:
    offenders: list[_ShapeAssumptionOffender] = []
    for function in _top_level_functions(module):
        if function.name == _EXTRACT_FAILURE_EVIDENCE:
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            if not _dict_call_wraps_plan_metadata_failure_evidence(node):
                continue
            offenders.append(
                _ShapeAssumptionOffender(
                    function_name=function.name,
                    line=node.lineno,
                    expression=ast.unparse(node),
                )
            )
    return offenders


def _direct_plan_metadata_failure_evidence_accesses(module: ast.Module) -> list[_ShapeAssumptionOffender]:
    offenders: list[_ShapeAssumptionOffender] = []
    for function in _top_level_functions(module):
        if function.name in _ALLOWED_PLAN_METADATA_FAILURE_EVIDENCE_ACCESSORS:
            continue
        for node in ast.walk(function):
            if not (_is_plan_metadata_failure_evidence_get(node) or _is_plan_metadata_failure_evidence_subscript(node)):
                continue
            offenders.append(
                _ShapeAssumptionOffender(
                    function_name=function.name,
                    line=node.lineno,
                    expression=ast.unparse(node),
                )
            )
    return offenders


def _imported_names_from_run_ledger_public(module: ast.Module) -> set[str]:
    imported: set[str] = set()
    for node in ast.iter_child_nodes(module):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != _RUN_LEDGER_PUBLIC_MODULE:
            continue
        imported.update(alias.asname or alias.name for alias in node.names)
    return imported


def test_aggregate_chat_imports_run_ledger_failure_evidence_merge_helper() -> None:
    """Aggregate chat must depend on the Run Ledger public merge boundary."""

    module = _parse_python_file(_AGGREGATE_CHAT_PY)

    assert _RUN_LEDGER_MERGE_HELPER in _imported_names_from_run_ledger_public(module), (
        f"aggregate_chat must import {_RUN_LEDGER_MERGE_HELPER} from "
        f"{_RUN_LEDGER_PUBLIC_MODULE}; local aggregate metadata consumers must "
        "not own failure-evidence payload shape or merge semantics."
    )


def test_extract_failure_evidence_uses_run_ledger_merge_helper_for_plan_construction() -> None:
    """The plan-construction entrypoint may extract evidence, but via Run Ledger."""

    function = _find_function(_parse_python_file(_AGGREGATE_CHAT_PY), _EXTRACT_FAILURE_EVIDENCE)
    helper_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) == _RUN_LEDGER_MERGE_HELPER
    ]

    assert helper_calls, (
        f"{_EXTRACT_FAILURE_EVIDENCE} must call {_RUN_LEDGER_MERGE_HELPER}; "
        "aggregate plan construction may gather failure_evidence, but the "
        "Run Ledger public helper owns projection, overlay, row merge and "
        "de-duplication behavior."
    )


def test_aggregate_plan_failure_evidence_accessor_uses_run_ledger_merge_helper() -> None:
    """The only plan.metadata failure_evidence accessor must normalize via Run Ledger."""

    function = _find_function(_parse_python_file(_AGGREGATE_CHAT_PY), _AGGREGATE_PLAN_FAILURE_EVIDENCE_PAYLOAD)
    helper_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) == _RUN_LEDGER_MERGE_HELPER
    ]

    assert helper_calls, (
        f"{_AGGREGATE_PLAN_FAILURE_EVIDENCE_PAYLOAD} must call "
        f"{_RUN_LEDGER_MERGE_HELPER}; aggregate_chat consumers may use this "
        "accessor, but must not own failure-evidence payload shape locally."
    )


def test_aggregate_chat_consumers_do_not_read_plan_metadata_failure_evidence_directly() -> None:
    """Aggregate plan consumers must route failure_evidence through the accessor."""

    module = _parse_python_file(_AGGREGATE_CHAT_PY)
    offenders = _direct_plan_metadata_failure_evidence_accesses(module)

    assert offenders == [], (
        "aggregate_chat may read plan.metadata['failure_evidence'] only inside "
        f"{_AGGREGATE_PLAN_FAILURE_EVIDENCE_PAYLOAD}. Consumers must route "
        "through that accessor so Run Ledger public merge semantics stay central. "
        "Offenders: "
        + ", ".join(f"{offender.function_name}:{offender.line}:{offender.expression}" for offender in offenders)
    )


def test_aggregate_chat_does_not_shape_cast_plan_metadata_failure_evidence_locally() -> None:
    """Aggregate plan consumers must not assume failure_evidence is a local dict."""

    module = _parse_python_file(_AGGREGATE_CHAT_PY)
    offenders = _local_failure_evidence_shape_assumptions(module)

    assert offenders == [], (
        "aggregate_chat must consume plan failure_evidence through the Run "
        f"Ledger public {_RUN_LEDGER_MERGE_HELPER} boundary instead of local "
        "dict(plan.metadata.get(...)) shape assumptions. Offenders: "
        + ", ".join(f"{offender.function_name}:{offender.line}:{offender.expression}" for offender in offenders)
    )
