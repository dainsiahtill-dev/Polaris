"""Architecture fence for task-runtime TaskBoard ownership.

Runtime task rows are the public execution-control projection.  Raw
``TaskBoard`` objects remain private to the ``runtime.task_runtime`` cell so
other production layers cannot bypass execution-event projection by reading or
writing task entities directly.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
TASK_RUNTIME_OWNER = POLARIS_ROOT / "cells" / "runtime" / "task_runtime"
TASK_RUNTIME_INTERNAL_BOARD = TASK_RUNTIME_OWNER / "internal" / "task_board.py"
TASK_RUNTIME_INTERNAL_SERVICE = TASK_RUNTIME_OWNER / "internal" / "service.py"
TASK_RUNTIME_PUBLIC_BOARD_CONTRACT = TASK_RUNTIME_OWNER / "public" / "task_board_contract.py"
TASK_RUNTIME_DESCRIPTOR = TASK_RUNTIME_OWNER / "generated" / "descriptor.pack.json"
ROLE_WORKER_POOL = POLARIS_ROOT / "cells" / "roles" / "runtime" / "internal" / "worker_pool.py"
DELIVERY_PM_TASKBOARD = POLARIS_ROOT / "delivery" / "cli" / "pm" / "engine" / "taskboard.py"
DIRECTOR_EXECUTION_SERVICE = POLARIS_ROOT / "cells" / "director" / "execution" / "service.py"
RUNTIME_PROJECTION_SERVICE = POLARIS_ROOT / "cells" / "runtime" / "projection" / "internal" / "runtime_projection_service.py"
RAW_TASKBOARD_MODULES = {
    "polaris.cells.runtime.task_runtime.internal.task_board",
    "polaris.cells.runtime.task_runtime.public.task_board_contract",
}
RAW_TASKBOARD_NAMES = {"TaskBoard", "TaskBoardToolInterface", "create_taskboard"}
LEGACY_TASK_RUNTIME_METHODS = {
    "create",
    "update",
    "get",
    "reopen",
    "update_task",
    "list_all",
    "list_ready",
    "get_ready_tasks",
    "get_stats",
}
RAW_TASK_ROW_READ_METHODS = {"list_task_rows"}
TASK_RUNTIME_RECEIVER_NAMES = {"task_runtime", "task_board"}


def _is_allowed_owner_path(path: Path) -> bool:
    try:
        path.relative_to(TASK_RUNTIME_OWNER)
    except ValueError:
        return False
    return True


def _raw_taskboard_imports(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if module not in RAW_TASKBOARD_MODULES:
            continue
        imported_names = {alias.name for alias in node.names}
        blocked_names = sorted(imported_names & RAW_TASKBOARD_NAMES)
        if blocked_names:
            offenders.append(f"{path.relative_to(BACKEND_ROOT)} imports {module}:{','.join(blocked_names)}")
    return offenders


def _is_task_runtime_constructor_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == "TaskRuntimeService")
        or (isinstance(node.func, ast.Attribute) and node.func.attr == "TaskRuntimeService")
    )


def _is_legacy_task_runtime_receiver(node: ast.AST) -> bool:
    if _is_task_runtime_constructor_call(node):
        return True
    if isinstance(node, ast.Name):
        return node.id in TASK_RUNTIME_RECEIVER_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in TASK_RUNTIME_RECEIVER_NAMES
    return False


def _legacy_task_runtime_method_calls(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in LEGACY_TASK_RUNTIME_METHODS:
            continue
        if not _is_legacy_task_runtime_receiver(func.value):
            continue
        offenders.append(
            f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} calls legacy task-runtime method {func.attr}()"
        )
    return offenders


def _raw_task_row_read_calls(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in RAW_TASK_ROW_READ_METHODS:
            continue
        if not _is_legacy_task_runtime_receiver(func.value):
            continue
        offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} calls raw {func.attr}()")
    return offenders


def _legacy_task_board_alias_references(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr != "task_board":
            continue
        offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} references legacy task_board alias")
    return offenders


def _annotation_name(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _annotation_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Subscript):
        return _annotation_name(node.value)
    if isinstance(node, ast.Constant):
        return str(node.value or "")
    return ""


def _target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _legacy_task_runtime_symbol_aliases(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            annotation = _annotation_name(node.annotation)
            target = _target_name(node.target)
            if annotation.endswith("TaskRuntimeService") and target in {"taskboard", "_taskboard", "task_board"}:
                offenders.append(
                    f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} names TaskRuntimeService as {target}"
                )
        if isinstance(node, ast.FunctionDef):
            returns = _annotation_name(node.returns)
            if node.name == "taskboard" and returns.endswith("TaskRuntimeService"):
                offenders.append(
                    f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} exposes TaskRuntimeService as taskboard"
                )
    return offenders


def _taskboard_class() -> ast.ClassDef:
    source = TASK_RUNTIME_INTERNAL_BOARD.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "TaskBoard":
            return node
    raise AssertionError("TaskBoard class not found")


def _taskboard_method(name: str) -> ast.FunctionDef:
    taskboard = _taskboard_class()
    for node in taskboard.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"TaskBoard.{name}() not found")


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def test_raw_taskboard_is_private_to_task_runtime_cell() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        if _is_allowed_owner_path(path):
            continue
        offenders.extend(_raw_taskboard_imports(path))

    assert not offenders, (
        "Raw TaskBoard access is private to polaris.cells.runtime.task_runtime. "
        "Use TaskRuntimeService task-row APIs outside the owner cell:\n" + "\n".join(offenders)
    )


def test_production_code_uses_task_runtime_row_apis() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        if _is_allowed_owner_path(path):
            continue
        offenders.extend(_legacy_task_runtime_method_calls(path))

    assert not offenders, (
        "Production task-runtime consumers must use row APIs such as "
        "create_task_row(), update_task_row(), get_task(), list_observable_task_rows(), "
        "list_ready_task_rows(), or get_task_row_stats():\n" + "\n".join(offenders)
    )


def test_production_read_side_uses_observable_task_rows() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        if _is_allowed_owner_path(path):
            continue
        offenders.extend(_raw_task_row_read_calls(path))

    assert not offenders, (
        "Production read-side task-runtime consumers must use "
        "list_observable_task_rows() so execution facts remain the status "
        "projection SSoT. Claim/write paths should use explicit ready/session "
        "APIs instead:\n" + "\n".join(offenders)
    )


def test_production_code_uses_task_runtime_alias() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        if _is_allowed_owner_path(path):
            continue
        offenders.extend(_legacy_task_board_alias_references(path))
        offenders.extend(_legacy_task_runtime_symbol_aliases(path))

    assert not offenders, (
        "Production code must access TaskRuntimeService through task_runtime, "
        "not legacy taskboard/task_board aliases:\n" + "\n".join(offenders)
    )


def test_role_worker_pool_uses_task_runtime_port_not_raw_taskboard() -> None:
    source = ROLE_WORKER_POOL.read_text(encoding="utf-8")
    blocked_tokens = (
        "TaskBoardPort",
        "ReadyTaskLike",
        "taskboard",
        "list_ready(",
        ".claim(",
        ".complete(",
        ".fail(",
    )
    offenders = [token for token in blocked_tokens if token in source]

    assert not offenders, (
        "roles.runtime worker pool must consume TaskRuntimeService row/session APIs, "
        "not raw TaskBoard protocols: " + ", ".join(offenders)
    )


def test_delivery_pm_taskboard_mainline_uses_task_runtime_service() -> None:
    source = DELIVERY_PM_TASKBOARD.read_text(encoding="utf-8")
    blocked_tokens = (
        "importlib.util",
        "create_taskboard",
        "_load_role_taskboard_module",
        "_taskboard_priority_enum",
        "runtime.get(\"board\")",
        "runtime.get(\"module\")",
        ".list_ready(",
        ".claim(",
        "._save_task",
    )
    offenders = [token for token in blocked_tokens if token in source]

    assert not offenders, (
        "delivery PM taskboard mainline must use TaskRuntimeService row/session APIs, "
        "not the retired role taskboard loader or raw TaskBoard calls: " + ", ".join(offenders)
    )


def test_task_runtime_raw_tool_factory_surface_is_removed() -> None:
    sources = {
        "internal": TASK_RUNTIME_INTERNAL_BOARD.read_text(encoding="utf-8"),
        "public": TASK_RUNTIME_PUBLIC_BOARD_CONTRACT.read_text(encoding="utf-8"),
    }
    blocked_tokens = (
        "class TaskBoardToolInterface",
        "def create_taskboard",
        '"TaskBoardToolInterface"',
        '"create_taskboard"',
    )
    offenders = [
        f"{source_name}:{token}"
        for source_name, source in sources.items()
        for token in blocked_tokens
        if token in source
    ]

    assert not offenders, (
        "TaskBoard LLM tool/factory compatibility surface is retired; "
        "use TaskRuntimeService row/session APIs instead:\n" + "\n".join(offenders)
    )


def test_raw_taskboard_has_no_workflow_state_bridge_hook() -> None:
    source = TASK_RUNTIME_INTERNAL_BOARD.read_text(encoding="utf-8")
    blocked_tokens = (
        "state_bridge",
        "notify_task_created",
        "notify_task_updated",
        "notify_task_completed",
    )
    offenders = [token for token in blocked_tokens if token in source]

    assert not offenders, (
        "Raw TaskBoard must not dual-write to workflow runtime state; "
        "execution-control state must flow through TaskRuntimeService row/session APIs:\n"
        + "\n".join(offenders)
    )


def test_taskboard_terminal_event_stream_is_owner_only_compatibility_projection() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    terminal_stream = "taskboard.terminal.events"
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        if _is_allowed_owner_path(path):
            continue
        if terminal_stream in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(BACKEND_ROOT)))

    assert not offenders, (
        "`taskboard.terminal.events` is a task_runtime-owned compatibility "
        "projection, not an execution-control fact source. Production code "
        "outside task_runtime must consume TaskRuntimeService / execution "
        "ledger projections instead:\n" + "\n".join(offenders)
    )


def test_raw_taskboard_dependency_state_changes_are_row_local() -> None:
    """Guard the WS2 invariant that TaskBoard does not mutate dependency peers."""

    taskboard = _taskboard_class()
    method_names = {"create", "update_status", "reopen"}
    offenders: list[str] = []

    if any(isinstance(node, ast.FunctionDef) and node.name == "_unblock_dependent_tasks" for node in taskboard.body):
        offenders.append("TaskBoard._unblock_dependent_tasks() must not be restored")

    for method_name in method_names:
        method = _taskboard_method(method_name)
        for node in ast.walk(method):
            if isinstance(node, ast.Call):
                called = _call_name(node.func)
                if called == "self._save_task":
                    first_arg = node.args[0] if node.args else None
                    if not isinstance(first_arg, ast.Name) or first_arg.id != "task":
                        offenders.append(
                            f"TaskBoard.{method_name}():{node.lineno} saves a non-local task row"
                        )
                if called.endswith(".append") or called.endswith(".remove"):
                    receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
                    if isinstance(receiver, ast.Attribute) and receiver.attr in {"blocks", "blocked_by"}:
                        offenders.append(
                            f"TaskBoard.{method_name}():{node.lineno} mutates dependency links directly"
                        )
            if isinstance(node, ast.For):
                iter_name = _call_name(node.iter)
                if iter_name in {"task.blocks", "task.blocked_by"}:
                    offenders.append(
                        f"TaskBoard.{method_name}():{node.lineno} iterates dependency links for peer mutation"
                    )

    assert not offenders, (
        "Raw TaskBoard create/update_status/reopen must stay row-local. "
        "Cross-row dependency link/unblock/reblock mutations belong in "
        "TaskRuntimeService so every side effect has task_runtime.execution "
        "facts:\n" + "\n".join(offenders)
    )


def test_task_runtime_service_avoids_raw_taskboard_convenience_writes() -> None:
    source = TASK_RUNTIME_INTERNAL_SERVICE.read_text(encoding="utf-8")
    blocked_tokens = (
        "self._board.claim(",
        "self._board.complete(",
        "self._board.fail(",
        "self._board.assign(",
    )
    offenders = [token for token in blocked_tokens if token in source]

    assert not offenders, (
        "TaskRuntimeService must own execution facts around task-state writes. "
        "Do not call raw TaskBoard convenience write methods that mutate rows "
        "without task_runtime.execution evidence:\n" + "\n".join(offenders)
    )


def test_public_task_board_contract_does_not_export_raw_taskboard_types() -> None:
    source = TASK_RUNTIME_PUBLIC_BOARD_CONTRACT.read_text(encoding="utf-8")
    blocked_tokens = (
        "from polaris.cells.runtime.task_runtime.internal.task_board import",
        '"TaskBoard"',
        '"Task"',
        '"TaskStatus"',
        '"TaskPriority"',
        '"InvalidTaskStateTransitionError"',
    )
    offenders = [token for token in blocked_tokens if token in source]

    assert not offenders, (
        "public.task_board_contract is retired as a raw TaskBoard facade; "
        "public consumers must use TaskRuntimeService:\n" + "\n".join(offenders)
    )


def test_task_runtime_descriptor_does_not_advertise_raw_taskboard_surface() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders = [
        str(item.get("name") or "")
        for item in capabilities
        if isinstance(item, dict)
        and item.get("defined_in") == "polaris/cells/runtime/task_runtime/internal/task_board.py"
    ]

    assert not offenders, (
        "task_runtime descriptor must not advertise raw internal TaskBoard symbols; "
        "public consumers must use TaskRuntimeService row/session APIs:\n" + "\n".join(offenders)
    )


def test_director_status_uses_task_runtime_projection() -> None:
    source = DIRECTOR_EXECUTION_SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    get_status: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "DirectorService":
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "get_status":
                get_status = item
                break
    assert get_status is not None, "DirectorService.get_status() not found"

    offenders: list[str] = []
    for node in ast.walk(get_status):
        if not isinstance(node, ast.Attribute) or node.attr not in {"get_tasks", "get_ready_task_count"}:
            continue
        receiver = node.value
        if isinstance(receiver, ast.Attribute) and receiver.attr == "_task_service":
            offenders.append(f"line {node.lineno}: self._task_service.{node.attr}()")

    assert not offenders, (
        "DirectorService.get_status() must project tasks from TaskRuntimeService "
        "rows, not Director's internal TaskService snapshot:\n" + "\n".join(offenders)
    )


def test_active_orchestration_status_uses_runtime_task_rows() -> None:
    source = RUNTIME_PROJECTION_SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    target: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == (
            "get_active_director_orchestration_status"
        ):
            target = node
            break
    assert target is not None, "get_active_director_orchestration_status() not found"

    task_payload_assignments: list[str] = []
    for node in ast.walk(target):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(item, ast.Name) and item.id == "tasks_payload" for item in node.targets):
            continue
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            task_payload_assignments.append(node.value.func.id)

    assert task_payload_assignments == ["_runtime_task_rows_payload"], (
        "Active Director orchestration status must expose task-runtime rows as "
        f"the top-level tasks payload, got {task_payload_assignments!r}"
    )


def test_runtime_projection_never_selects_workflow_archive_tasks_as_live_rows() -> None:
    source = RUNTIME_PROJECTION_SERVICE.read_text(encoding="utf-8")
    blocked_tokens = (
        "TaskSource.WORKFLOW",
        "build_workflow_task_rows(",
    )
    offenders = [token for token in blocked_tokens if token in source]

    assert not offenders, (
        "RuntimeProjection.task_rows must come from runtime.task_runtime rows, "
        "not workflow archive task rows. Archive tasks may remain under "
        "workflow_archive/raw_workflow_status as read-only evidence:\n" + "\n".join(offenders)
    )
