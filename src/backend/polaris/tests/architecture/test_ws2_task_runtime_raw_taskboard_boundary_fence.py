"""WS2 architecture fence for TaskRuntimeService raw TaskBoard access.

``TaskRuntimeService`` owns the raw file-backed ``TaskBoard`` entity boundary.
Direct calls to ``self._board.list_all()`` must stay centralized in one private
helper so mutation paths and projections cannot scatter raw TaskBoard reads
across service methods again.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_BACKEND_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_TASK_RUNTIME_SERVICE: Final[Path] = (
    _BACKEND_ROOT / "polaris" / "cells" / "runtime" / "task_runtime" / "internal" / "service.py"
)
_SERVICE_CLASS: Final[str] = "TaskRuntimeService"
_RAW_TASKBOARD_LIST_HELPER: Final[str] = "_list_file_task_entities"
_KEY_RAW_TASKBOARD_ENTITY_CONSUMERS: Final[frozenset[str]] = frozenset(
    {
        "_list_file_task_rows",
        "refresh_dependency_unblocks",
        "reset_task_rows_for_reexecution",
        "suspend_active_executions_for_run",
    }
)


@dataclass(frozen=True, slots=True)
class _DirectListAllCall:
    """Location of one direct ``self._board.list_all()`` call."""

    method_name: str
    line_number: int


def _parse_task_runtime_service() -> ast.Module:
    source = _TASK_RUNTIME_SERVICE.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(_TASK_RUNTIME_SERVICE))


def _task_runtime_service_class(tree: ast.Module) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == _SERVICE_CLASS:
            return node
    raise AssertionError(f"{_SERVICE_CLASS} was not found in {_TASK_RUNTIME_SERVICE}")


def _service_methods(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    service_class = _task_runtime_service_class(tree)
    return {item.name: item for item in service_class.body if isinstance(item, ast.FunctionDef)}


def _walk_method_body(method: ast.FunctionDef) -> list[ast.AST]:
    nodes: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        if node is not method and isinstance(
            node,
            ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef,
        ):
            return
        nodes.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(method)
    return nodes


def _is_self_board_list_all_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "list_all":
        return False
    board = node.func.value
    if not isinstance(board, ast.Attribute) or board.attr != "_board":
        return False
    return isinstance(board.value, ast.Name) and board.value.id == "self"


def _is_self_helper_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if not isinstance(node.func, ast.Attribute) or node.func.attr != _RAW_TASKBOARD_LIST_HELPER:
        return False
    return isinstance(node.func.value, ast.Name) and node.func.value.id == "self"


def _direct_self_board_list_all_calls_by_method(tree: ast.Module) -> list[_DirectListAllCall]:
    calls: list[_DirectListAllCall] = []
    for method_name, method in _service_methods(tree).items():
        for node in _walk_method_body(method):
            if _is_self_board_list_all_call(node):
                calls.append(_DirectListAllCall(method_name=method_name, line_number=node.lineno))
    return calls


def _direct_calls_in_method(method: ast.FunctionDef) -> list[int]:
    return [node.lineno for node in _walk_method_body(method) if _is_self_board_list_all_call(node)]


def _method_calls_raw_taskboard_helper(method: ast.FunctionDef) -> bool:
    return any(_is_self_helper_call(node) for node in _walk_method_body(method))


def test_raw_taskboard_list_all_is_centralized_in_private_helper() -> None:
    """Only the owner-cell helper may directly call ``TaskBoard.list_all()``."""

    calls = _direct_self_board_list_all_calls_by_method(_parse_task_runtime_service())
    offenders = [
        f"{_TASK_RUNTIME_SERVICE.relative_to(_BACKEND_ROOT)}:{call.line_number} {_SERVICE_CLASS}.{call.method_name}()"
        for call in calls
        if call.method_name != _RAW_TASKBOARD_LIST_HELPER
    ]
    helper_call_count = sum(1 for call in calls if call.method_name == _RAW_TASKBOARD_LIST_HELPER)

    assert not offenders, (
        f"{_SERVICE_CLASS} raw TaskBoard entity reads must be centralized in "
        f"{_RAW_TASKBOARD_LIST_HELPER}(). Direct self._board.list_all() callers:\n" + "\n".join(offenders)
    )
    assert helper_call_count == 1, (
        f"{_SERVICE_CLASS}.{_RAW_TASKBOARD_LIST_HELPER}() must be the single direct "
        f"self._board.list_all() bridge; found {helper_call_count} helper calls."
    )


def test_key_task_runtime_methods_do_not_directly_call_raw_taskboard_list_all() -> None:
    """Critical mutation/projection methods must not bypass the helper."""

    methods = _service_methods(_parse_task_runtime_service())
    missing = sorted(_KEY_RAW_TASKBOARD_ENTITY_CONSUMERS.difference(methods))
    assert not missing, f"Missing expected {_SERVICE_CLASS} methods: {missing}"

    offenders: list[str] = []
    for method_name in sorted(_KEY_RAW_TASKBOARD_ENTITY_CONSUMERS):
        for line_number in _direct_calls_in_method(methods[method_name]):
            offenders.append(
                f"{_TASK_RUNTIME_SERVICE.relative_to(_BACKEND_ROOT)}:{line_number} {_SERVICE_CLASS}.{method_name}()"
            )

    assert not offenders, (
        "Critical TaskRuntimeService methods must not call self._board.list_all() "
        f"directly; route raw entity reads through {_RAW_TASKBOARD_LIST_HELPER}(). "
        "Offenders:\n" + "\n".join(offenders)
    )


def test_key_task_runtime_methods_route_raw_taskboard_reads_through_helper() -> None:
    """Critical methods that need raw Task entities must call the private helper."""

    methods = _service_methods(_parse_task_runtime_service())
    missing = sorted(_KEY_RAW_TASKBOARD_ENTITY_CONSUMERS.difference(methods))
    assert not missing, f"Missing expected {_SERVICE_CLASS} methods: {missing}"

    offenders = [
        method_name
        for method_name in sorted(_KEY_RAW_TASKBOARD_ENTITY_CONSUMERS)
        if not _method_calls_raw_taskboard_helper(methods[method_name])
    ]

    assert not offenders, (
        "Critical TaskRuntimeService methods must consume raw file-backed Task "
        f"entities through {_RAW_TASKBOARD_LIST_HELPER}() so the raw TaskBoard "
        "boundary remains centralized. Offenders: " + ", ".join(offenders)
    )
