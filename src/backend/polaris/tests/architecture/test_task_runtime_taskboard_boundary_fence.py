"""Architecture fence for task-runtime TaskBoard ownership.

Runtime task rows are the public execution-control projection.  Raw
``TaskBoard`` objects remain private to the ``runtime.task_runtime`` cell so
other production layers cannot bypass execution-event projection by reading or
writing task entities directly.
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
TASK_RUNTIME_OWNER = POLARIS_ROOT / "cells" / "runtime" / "task_runtime"
TASK_RUNTIME_INTERNAL_BOARD = TASK_RUNTIME_OWNER / "internal" / "task_board.py"
TASK_RUNTIME_INTERNAL_SERVICE = TASK_RUNTIME_OWNER / "internal" / "service.py"
TASK_RUNTIME_PUBLIC_BOARD_CONTRACT = TASK_RUNTIME_OWNER / "public" / "task_board_contract.py"
TASK_RUNTIME_DESCRIPTOR = TASK_RUNTIME_OWNER / "generated" / "descriptor.pack.json"
TASK_RUNTIME_INTERNAL_EXECUTION_SESSION_DESCRIPTOR_FILE = (
    "polaris/cells/runtime/task_runtime/internal/execution_session.py"
)
ROLE_WORKER_POOL = POLARIS_ROOT / "cells" / "roles" / "runtime" / "internal" / "worker_pool.py"
DELIVERY_PM_TASKBOARD = POLARIS_ROOT / "delivery" / "cli" / "pm" / "engine" / "taskboard.py"
DELIVERY_CLI_DIRECTOR_SERVICE = POLARIS_ROOT / "delivery" / "cli" / "director" / "director_service.py"
ROLE_ADAPTER_BASE = POLARIS_ROOT / "cells" / "roles" / "adapters" / "internal" / "base.py"
PM_ADAPTER = POLARIS_ROOT / "cells" / "roles" / "adapters" / "internal" / "pm_adapter.py"
PM_PLANNING_AGENT = POLARIS_ROOT / "cells" / "orchestration" / "pm_planning" / "internal" / "pm_agent.py"
DIRECTOR_ADAPTER = POLARIS_ROOT / "cells" / "roles" / "adapters" / "internal" / "director" / "adapter.py"
PM_BOARD_TASKS = POLARIS_ROOT / "cells" / "roles" / "adapters" / "internal" / "pm" / "board_tasks.py"
QA_ADAPTER = POLARIS_ROOT / "cells" / "roles" / "adapters" / "internal" / "qa_adapter.py"
DIRECTOR_EXECUTION_SERVICE = POLARIS_ROOT / "cells" / "director" / "execution" / "service.py"
RUNTIME_PROJECTION_SERVICE = (
    POLARIS_ROOT / "cells" / "runtime" / "projection" / "internal" / "runtime_projection_service.py"
)
FACTORY_HTTP_ROUTER = POLARIS_ROOT / "delivery" / "http" / "routers" / "factory.py"
EXECUTION_SESSION_MODULE = (
    BACKEND_ROOT / "polaris" / "cells" / "runtime" / "task_runtime" / "internal" / "execution_session.py"
)
EXECUTION_FACT_READ_PROJECTOR = "project_task_row_from_execution_fact_payload"
EXECUTION_FACT_LIST_READER = "list_task_rows_from_execution_facts"
EXECUTION_FACT_SEQ_KEY = "fact_event_seq"
EXECUTION_FACT_SEQ_SOURCE_KEY = "seq"
EXECUTION_FACT_DOT_SEQ_FILE_PATTERNS = (
    ".seq",
    ".seq.lock",
    "task_runtime.execution.jsonl.seq",
)
FACTORY_BENCH_RUNNER = BACKEND_ROOT / "scripts" / "factory_bench" / "run_factory_bench.py"
FACTORY_STAGE_EXECUTOR = POLARIS_ROOT / "cells" / "factory" / "pipeline" / "internal" / "factory_stage_executor.py"
RUNTIME_ARTIFACT_STORE_ARTIFACTS = POLARIS_ROOT / "cells" / "runtime" / "artifact_store" / "internal" / "artifacts.py"
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
RUNTIME_EXECUTION_MUTATING_METHODS = {"pop", "setdefault", "update"}
TASK_RUNTIME_UPDATE_ROW_METADATA_ONLY_ALLOWLIST = {
    "polaris/cells/roles/adapters/internal/pm/board_tasks.py",
    "polaris/cells/roles/adapters/internal/qa_adapter.py",
    "polaris/delivery/cli/pm/engine/taskboard.py",
    "polaris/delivery/http/routers/factory.py",
}
TASK_RUNTIME_OWNER_TRANSITION_CALL_ALLOWLIST = {
    "cancel_task_row_for_deduplication": {
        "polaris/cells/roles/adapters/internal/pm/board_tasks.py",
    },
    "complete_execution": {
        "polaris/cells/roles/adapters/internal/director/execute_method.py",
        "polaris/cells/roles/runtime/internal/worker_pool.py",
        "polaris/delivery/cli/pm/engine/taskboard.py",
    },
    "fail_execution": {
        "polaris/cells/roles/adapters/internal/director/execute_method.py",
        "polaris/cells/roles/runtime/internal/worker_pool.py",
        "polaris/delivery/cli/pm/engine/taskboard.py",
    },
    "fail_task_row_after_rework_exhausted": {
        "polaris/cells/roles/adapters/internal/qa_adapter.py",
    },
    "fail_task_row_from_role_adapter": {
        "polaris/cells/roles/adapters/internal/pm_adapter.py",
    },
    "reopen_task_row": {
        "polaris/cells/roles/adapters/internal/qa_adapter.py",
        "polaris/delivery/http/routers/factory.py",
    },
}
TASK_RUNTIME_EXECUTION_EVENT_CHECK_REQUIRED = {
    "polaris/cells/orchestration/pm_planning/internal/pm_agent.py": {
        "_tool_taskboard_create",
    },
    "polaris/cells/roles/adapters/internal/base.py": {
        "_update_board_task",
    },
    "polaris/cells/roles/adapters/internal/pm/board_tasks.py": {
        "_create_board_tasks",
    },
    "polaris/cells/roles/adapters/internal/pm_adapter.py": {
        "_run_pm_stage",
    },
    "polaris/cells/roles/adapters/internal/qa_adapter.py": {
        "_apply_taskboard_qa_verdict",
    },
    "polaris/delivery/cli/pm/engine/taskboard.py": {
        "_build_taskboard_runtime",
        "_select_taskboard_ready_batch",
    },
    "polaris/delivery/http/routers/factory.py": {
        "_apply_quality_gate_task_boundary_rework_requests",
    },
}
TASK_RUNTIME_EXECUTION_EVENT_CHECK_HELPERS = {
    (
        "polaris/cells/roles/adapters/internal/pm/board_tasks.py",
        "_create_board_tasks",
    ): {
        "_with_task_runtime_execution_event_failure",
    },
    (
        "polaris/cells/roles/adapters/internal/qa_adapter.py",
        "_apply_taskboard_qa_verdict",
    ): {
        "_record_qa_task_runtime_execution_event_failure",
    },
}
REVIEWED_TASK_RUNTIME_SERVICE_BOARD_WRITES = {
    ("_apply_dependency_completion_side_effects", "notify_ready_tasks"): 1,
    ("_apply_dependency_completion_side_effects", "update"): 1,
    ("_apply_reopen_downstream_reblocks", "update"): 1,
    ("_apply_reverse_dependency_links", "update_blocks"): 1,
    ("_apply_terminal_session_reconcile", "reconcile_terminal_status"): 1,
    ("_apply_terminal_session_reconcile", "update"): 2,
    ("_create_with_execution_event", "create"): 1,
    ("_reopen_with_execution_event", "reopen"): 1,
    ("_update_with_execution_event", "update"): 1,
    ("cancel_task_row_for_deduplication", "update"): 1,
    ("claim_execution", "update"): 2,
    ("complete_execution", "update"): 1,
    ("fail_execution", "update"): 1,
    ("fail_task_row_after_rework_exhausted", "update"): 1,
    ("fail_task_row_from_role_adapter", "update"): 1,
    ("heartbeat_execution", "update"): 1,
    ("refresh_dependency_unblocks", "update"): 2,
    ("suspend_active_executions_for_run", "update"): 1,
    ("suspend_execution", "update"): 1,
}
REVIEWED_TASK_RUNTIME_SERVICE_BOARD_READS = {
    ("_apply_reopen_downstream_reblocks", "get"): 1,
    ("_apply_reverse_dependency_links", "get"): 1,
    ("_apply_terminal_session_reconcile", "get"): 3,
    ("_find_terminal_session_snapshot", "get"): 1,
    ("_list_file_task_entities", "list_all"): 1,
    ("cancel_task_row_for_deduplication", "get"): 1,
    ("claim_execution", "get"): 1,
    ("complete_execution", "get"): 1,
    ("fail_execution", "get"): 1,
    ("fail_task_row_from_role_adapter", "get"): 1,
    ("suspend_execution", "get"): 1,
}
TASK_RUNTIME_SERVICE_RAW_BOARD_LIST_HELPER = "_list_file_task_entities"
TASK_RUNTIME_SERVICE_RAW_BOARD_ENTITY_CONSUMERS = frozenset(
    {
        "_list_file_task_rows",
        "refresh_dependency_unblocks",
        "reset_task_rows_for_reexecution",
        "suspend_active_executions_for_run",
    }
)
TASK_RUNTIME_SERVICE_RAW_BOARD_WRITE_METHODS = {
    "create",
    "notify_ready_tasks",
    "reconcile_terminal_status",
    "reopen",
    "update",
    "update_blocks",
    "update_status",
}
TASK_RUNTIME_SERVICE_RAW_BOARD_READ_METHODS = {
    "get",
    "get_blocked_tasks",
    "get_dependency_graph",
    "get_ready_tasks",
    "get_stats",
    "get_task",
    "list_all",
    "list_my_tasks",
    "list_ready",
}
TASK_RUNTIME_SERVICE_RETIRED_PUBLIC_METHODS = {
    "get_ready_tasks",
    "get_stats",
    "list_all",
    "list_ready",
}
TASK_RUNTIME_SERVICE_RETIRED_ENTITY_METHODS = {
    "create",
    "get",
    "reopen",
    "update",
    "update_task",
}
TASK_RUNTIME_SERVICE_NON_AGENT_DESCRIPTOR_METHODS = {
    "add_ready_listener",
    "board",
    "wait_ready",
}
TASK_RUNTIME_SERVICE_NON_OPERATION_DESCRIPTOR_METHODS = {
    "__init__",
    "workspace",
}
TASK_RUNTIME_SERVICE_UTILITY_DESCRIPTOR_METHODS = {
    "normalize_task_id",
    "task_exists",
}
TASK_RUNTIME_SERVICE_DESTRUCTIVE_DESCRIPTOR_METHODS = {
    "reset_records",
}
TASK_RUNTIME_SERVICE_OWNER_READ_DESCRIPTOR_METHODS = {
    "list_task_rows",
}
TASK_RUNTIME_SERVICE_OWNER_MAINTENANCE_DESCRIPTOR_METHODS = {
    "refresh_dependency_unblocks",
}
TASK_RUNTIME_SERVICE_PREVIEW_SELECTION_DESCRIPTOR_METHODS = {
    "select_next_task",
}
TASK_RUNTIME_SERVICE_REQUIRED_ROW_MUTATION_METHODS = {
    "create_task_row",
    "reopen_task_row",
    "update_task_row",
}
TASK_RUNTIME_SERVICE_REQUIRED_READ_MODEL_METHODS = {
    "get_task_row_stats",
    "list_observable_task_rows",
    "list_ready_task_rows",
}
TASK_RUNTIME_EXECUTION_STREAM = "task_runtime.execution"
TASK_RUNTIME_EXECUTION_EVENT_FILE = "task_runtime.execution.jsonl"
TASK_RUNTIME_EXECUTION_DIRECT_WRITE_METHODS = {
    "open",
    "write_bytes",
    "write_text",
}
TASK_RUNTIME_EXECUTION_DIRECT_READ_METHODS = {
    "open",
    "read_bytes",
    "read_text",
}
TASKBOARD_TERMINAL_EVENT_STREAM = "taskboard.terminal.events"
TASKBOARD_TERMINAL_EVENT_DIRECT_WRITE_METHODS = {
    "open",
    "write",
    "write_bytes",
    "write_text",
}


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


def _direct_task_row_file_globs(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "glob":
            continue
        pattern = node.args[0] if node.args else None
        if isinstance(pattern, ast.Constant) and pattern.value == "task_*.json":
            offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} directly globs runtime task rows")
    return offenders


TASK_ROW_FILE_ACCESS_METHODS = {
    "glob",
    "open",
    "read_bytes",
    "read_text",
    "rename",
    "unlink",
    "write_bytes",
    "write_text",
}


def _looks_like_task_row_file_literal(value: str) -> bool:
    return value == "task_*.json" or ("task_" in value and value.endswith(".json"))


def _contains_task_row_file_literal(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and _looks_like_task_row_file_literal(child.value)
        ):
            return True
        if isinstance(child, ast.JoinedStr):
            literal_text = "".join(
                part.value for part in child.values if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if "task_" in literal_text and ".json" in literal_text:
                return True
    return False


def _direct_task_row_file_accesses(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        method = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else "")
        if method not in TASK_ROW_FILE_ACCESS_METHODS:
            continue
        if _contains_task_row_file_literal(node):
            offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} directly accesses runtime task-row files")
    return offenders


def _contains_string_literal(node: ast.AST, value: str) -> bool:
    return any(
        isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value == value
        for child in ast.walk(node)
    )


def _task_runtime_execution_writer_violations(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = _call_name(node.func)
        if call == "AppendFactEventCommandV1" and _contains_string_literal(node, TASK_RUNTIME_EXECUTION_STREAM):
            offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} appends task_runtime.execution facts")
            continue
        method = call.rsplit(".", maxsplit=1)[-1]
        if method in TASK_RUNTIME_EXECUTION_DIRECT_WRITE_METHODS and _contains_string_literal(
            node,
            TASK_RUNTIME_EXECUTION_EVENT_FILE,
        ):
            offenders.append(
                f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} writes task_runtime.execution.jsonl directly"
            )
    return offenders


def _task_runtime_execution_reader_violations(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = _call_name(node.func)
        if call in {"QueryFactEventsV1", "query_fact_events"} and _contains_string_literal(
            node,
            TASK_RUNTIME_EXECUTION_STREAM,
        ):
            offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} reads task_runtime.execution facts")
            continue
        method = call.rsplit(".", maxsplit=1)[-1]
        if method in TASK_RUNTIME_EXECUTION_DIRECT_READ_METHODS and _contains_string_literal(
            node,
            TASK_RUNTIME_EXECUTION_EVENT_FILE,
        ):
            offenders.append(
                f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} reads task_runtime.execution.jsonl directly"
            )
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
        if isinstance(node, ast.Assign) and _is_task_runtime_constructor_call(node.value):
            for target_node in node.targets:
                target = _target_name(target_node)
                if target in {"taskboard", "_taskboard", "task_board"}:
                    offenders.append(
                        f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} names TaskRuntimeService as {target}"
                    )
        if isinstance(node, ast.AnnAssign):
            annotation = _annotation_name(node.annotation)
            target = _target_name(node.target)
            if annotation.endswith("TaskRuntimeService") and target in {"taskboard", "_taskboard", "task_board"}:
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} names TaskRuntimeService as {target}")
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


def _class_def(path: Path, name: str) -> ast.ClassDef:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(BACKEND_ROOT)}:{name} class not found")


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


def _parent_lookup(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_function_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return "<module>"


def _is_raw_taskboard_factory_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _call_name(node.func) in {"TaskBoard", "create_taskboard"}


def _taskboard_receiver_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _call_name(node)
    return ""


def _raw_taskboard_create_calls(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    taskboard_receivers: set[str] = set()

    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if value is None or not _is_raw_taskboard_factory_call(value):
            continue
        for target in targets:
            receiver = _taskboard_receiver_name(target)
            if receiver:
                taskboard_receivers.add(receiver)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "create":
            continue
        receiver = _taskboard_receiver_name(func.value)
        if receiver in taskboard_receivers or _is_raw_taskboard_factory_call(func.value):
            offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} calls raw TaskBoard.create()")
    return offenders


def _task_runtime_service_raw_board_write_calls() -> Counter[tuple[str, str]]:
    return _task_runtime_service_raw_board_calls(TASK_RUNTIME_SERVICE_RAW_BOARD_WRITE_METHODS)


def _task_runtime_service_raw_board_read_calls() -> Counter[tuple[str, str]]:
    return _task_runtime_service_raw_board_calls(TASK_RUNTIME_SERVICE_RAW_BOARD_READ_METHODS)


def _task_runtime_service_raw_board_calls(methods: set[str]) -> Counter[tuple[str, str]]:
    source = TASK_RUNTIME_INTERNAL_SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parent_lookup(tree)
    calls: Counter[tuple[str, str]] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if _call_name(func.value) != "self._board":
            continue
        if func.attr not in methods:
            continue
        calls[(_enclosing_function_name(node, parents), func.attr)] += 1
    return calls


def _task_runtime_service_method_defs() -> dict[str, ast.FunctionDef]:
    service_class = _class_def(TASK_RUNTIME_INTERNAL_SERVICE, "TaskRuntimeService")
    return {node.name: node for node in service_class.body if isinstance(node, ast.FunctionDef)}


def _walk_task_runtime_method_body(method_def: ast.FunctionDef) -> list[ast.AST]:
    """Walk one TaskRuntimeService method without entering nested scopes."""

    nodes: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        if node is not method_def and isinstance(
            node,
            ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef,
        ):
            return
        nodes.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(method_def)
    return nodes


def _direct_self_board_list_all_calls(method_def: ast.FunctionDef) -> list[ast.Call]:
    return [
        node
        for node in _walk_task_runtime_method_body(method_def)
        if isinstance(node, ast.Call) and _call_name(node.func) == "self._board.list_all"
    ]


def _method_body_directly_calls_self_method(method_def: ast.FunctionDef, method_name: str) -> bool:
    return any(
        isinstance(node, ast.Call) and _call_is_self_method(node, method_name)
        for node in _walk_task_runtime_method_body(method_def)
    )


def _function_def(path: Path, name: str) -> ast.FunctionDef:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(BACKEND_ROOT)}:{name}() not found")


def _function_call_counts(path: Path, function_name: str) -> Counter[str]:
    function_def = _function_def(path, function_name)
    calls: Counter[str] = Counter()
    for node in ast.walk(function_def):
        if isinstance(node, ast.Call):
            calls[_call_name(node.func)] += 1
    return calls


def _assigned_constant_tuple(path: Path, name: str) -> tuple[str, ...]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        if not isinstance(node.value, ast.Tuple):
            raise AssertionError(f"{name} must remain a literal tuple")
        values: list[str] = []
        for item in node.value.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                raise AssertionError(f"{name} entries must remain literal strings")
            values.append(item.value)
        return tuple(values)
    raise AssertionError(f"{path.relative_to(BACKEND_ROOT)}:{name} assignment not found")


def _assigned_frozenset_literal_strings(path: Path, name: str) -> set[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        return _literal_string_collection_values(node.value, context=name)
    raise AssertionError(f"{path.relative_to(BACKEND_ROOT)}:{name} assignment not found")


def _string_literal(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _literal_string_collection_values(node: ast.AST, *, context: str) -> set[str]:
    value_node = node
    if isinstance(value_node, ast.Call) and _call_name(value_node.func) == "frozenset":
        if len(value_node.args) != 1 or value_node.keywords:
            raise AssertionError(f"{context} must remain a one-argument frozenset literal")
        value_node = value_node.args[0]
    if not isinstance(value_node, (ast.Set, ast.List, ast.Tuple)):
        raise AssertionError(f"{context} must remain an AST-readable literal collection")

    values: set[str] = set()
    for item in value_node.elts:
        literal_value = _string_literal(item)
        if not literal_value:
            raise AssertionError(f"{context} entries must remain literal strings")
        values.add(literal_value)
    return values


def _enum_string_member_values(enum_class: ast.ClassDef) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in enum_class.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        value = _string_literal(node.value)
        if value:
            values[node.targets[0].id] = value
    if not values:
        raise AssertionError(f"{enum_class.name} must expose AST-readable string enum members")
    return values


def _task_status_member_value(node: ast.AST, enum_values: dict[str, str], *, context: str) -> str:
    literal_value = _string_literal(node)
    if literal_value:
        return literal_value
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "TaskStatus"
        and node.attr in enum_values
    ):
        return enum_values[node.attr]
    raise AssertionError(f"{context} must reference TaskStatus enum members or literal strings")


def _task_status_is_terminal_values() -> set[str]:
    task_status_class = _class_def(TASK_RUNTIME_INTERNAL_BOARD, "TaskStatus")
    enum_values = _enum_string_member_values(task_status_class)

    is_terminal = None
    for node in task_status_class.body:
        if isinstance(node, ast.FunctionDef) and node.name == "is_terminal":
            is_terminal = node
            break
    if is_terminal is None:
        raise AssertionError("TaskStatus.is_terminal property not found")

    returns = [node.value for node in ast.walk(is_terminal) if isinstance(node, ast.Return) and node.value is not None]
    if len(returns) != 1:
        raise AssertionError("TaskStatus.is_terminal must keep a single AST-readable return")

    expression = returns[0]
    if (
        not isinstance(expression, ast.Compare)
        or len(expression.ops) != 1
        or not isinstance(expression.ops[0], ast.In)
        or len(expression.comparators) != 1
    ):
        raise AssertionError("TaskStatus.is_terminal must remain a membership test")

    terminal_collection = expression.comparators[0]
    if not isinstance(terminal_collection, (ast.Set, ast.List, ast.Tuple)):
        raise AssertionError("TaskStatus.is_terminal must compare against a literal collection")

    return {
        _task_status_member_value(item, enum_values, context="TaskStatus.is_terminal")
        for item in terminal_collection.elts
    }


def _subscript_key(node: ast.AST) -> str:
    if not isinstance(node, ast.Subscript):
        return ""
    return _string_literal(node.slice)


def _contains_runtime_execution_subscript(node: ast.AST) -> bool:
    if isinstance(node, ast.Subscript) and _subscript_key(node) == "runtime_execution":
        return True
    return any(_contains_runtime_execution_subscript(child) for child in ast.iter_child_nodes(node))


def _runtime_execution_metadata_writes(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if _contains_runtime_execution_subscript(target):
                    offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} assigns runtime_execution")
        elif isinstance(node, ast.AnnAssign) and _contains_runtime_execution_subscript(node.target):
            offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} assigns runtime_execution")
        elif isinstance(node, ast.AugAssign) and _contains_runtime_execution_subscript(node.target):
            offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} mutates runtime_execution")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr not in RUNTIME_EXECUTION_MUTATING_METHODS:
                continue
            first_arg = node.args[0] if node.args else None
            if _string_literal(first_arg) == "runtime_execution":
                offenders.append(
                    f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} calls {node.func.attr}('runtime_execution')"
                )
    return offenders


def _literal_status_update_task_row_calls(path: Path, statuses: set[str]) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "update_task_row":
            continue
        for keyword in node.keywords:
            if keyword.arg != "status":
                continue
            status = _string_literal(keyword.value)
            if status in statuses:
                offenders.append(
                    f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} calls update_task_row(status={status!r})"
                )
    return offenders


def _literal_status_method_calls(path: Path, method_name: str, statuses: set[str]) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != method_name:
            continue
        for keyword in node.keywords:
            if keyword.arg != "status":
                continue
            status = _string_literal(keyword.value)
            if status in statuses:
                offenders.append(
                    f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} calls {method_name}(status={status!r})"
                )
    return offenders


def _update_task_row_boundary_violations(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rel = path.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "update_task_row":
            continue
        status_keyword = next((keyword for keyword in node.keywords if keyword.arg == "status"), None)
        if rel == "polaris/cells/roles/adapters/internal/base.py":
            if status_keyword is None:
                offenders.append(f"{rel}:{node.lineno} calls update_task_row() without the shared status guard")
            continue
        if status_keyword is not None:
            offenders.append(
                f"{rel}:{node.lineno} passes status= to update_task_row(); use a TaskRuntimeService owner transition"
            )
            continue
        if rel not in TASK_RUNTIME_UPDATE_ROW_METADATA_ONLY_ALLOWLIST:
            offenders.append(
                f"{rel}:{node.lineno} is not an approved metadata-only update_task_row() projection writer"
            )
    return offenders


def _function_body_references_name(path: Path, function_name: str, referenced_name: str) -> bool:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != function_name:
            continue
        return any(isinstance(child, ast.Name) and child.id == referenced_name for child in ast.walk(node))
    return False


def _function_body_references_any_name(
    path: Path,
    function_name: str,
    referenced_names: set[str],
) -> bool:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != function_name:
            continue
        return any(isinstance(child, ast.Name) and child.id in referenced_names for child in ast.walk(node))
    return False


def _owner_transition_call_boundary_violations(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rel = path.relative_to(BACKEND_ROOT).as_posix()
    owner_methods = set(TASK_RUNTIME_OWNER_TRANSITION_CALL_ALLOWLIST)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in owner_methods:
            continue
        allowed_paths = TASK_RUNTIME_OWNER_TRANSITION_CALL_ALLOWLIST[func.attr]
        if rel not in allowed_paths:
            offenders.append(f"{rel}:{node.lineno} calls {func.attr}() outside the reviewed WS2 allowlist")
    return offenders


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


def test_taskboard_row_creation_stays_in_task_runtime_service() -> None:
    offenders: list[str] = []
    for path in TASK_RUNTIME_OWNER.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        if path.resolve() in {
            TASK_RUNTIME_INTERNAL_BOARD.resolve(),
            TASK_RUNTIME_INTERNAL_SERVICE.resolve(),
        }:
            continue
        offenders.extend(_raw_taskboard_create_calls(path))

    assert not offenders, (
        "Raw TaskBoard.create() must stay behind TaskRuntimeService.create_task_row() "
        "so task creation always emits execution ledger facts and reverse dependency "
        "links from the same owner path:\n" + "\n".join(offenders)
    )


def test_update_task_row_writers_are_metadata_only_or_owner_guarded() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        if _is_allowed_owner_path(path):
            continue
        offenders.extend(_update_task_row_boundary_violations(path))

    assert not offenders, (
        "TaskRuntimeService.update_task_row() is a row projection helper, not a "
        "distributed task-state owner. Production callers outside "
        "runtime.task_runtime may only perform reviewed metadata-only updates; "
        "status changes must use owner transitions such as claim/complete/fail, "
        "dedup cancellation, QA rework failure, or role-adapter failure helpers:\n" + "\n".join(offenders)
    )


def test_pm_planning_taskboard_create_checks_execution_event_projection() -> None:
    assert _function_body_references_name(
        PM_PLANNING_AGENT,
        "_tool_taskboard_create",
        "task_row_execution_event_failure",
    ), (
        "PM planning _tool_taskboard_create() must inspect the row projection "
        "with task_row_execution_event_failure() before returning ok=True. "
        "TaskRuntime row writes can persist a row while failing to append the "
        "authoritative task_runtime.execution fact."
    )


def test_task_runtime_row_write_consumers_check_execution_event_projection() -> None:
    offenders: list[str] = []
    for rel, function_names in sorted(TASK_RUNTIME_EXECUTION_EVENT_CHECK_REQUIRED.items()):
        path = BACKEND_ROOT / rel
        for function_name in sorted(function_names):
            expected_names = {
                "task_row_execution_event_failure",
                *TASK_RUNTIME_EXECUTION_EVENT_CHECK_HELPERS.get((rel, function_name), set()),
            }
            if not _function_body_references_any_name(
                path,
                function_name,
                expected_names,
            ):
                offenders.append(f"{rel}:{function_name}()")

    assert not offenders, (
        "TaskRuntime row-write consumers that can advance downstream state must "
        "inspect execution-event projections with task_row_execution_event_failure(). "
        "A row can persist while the authoritative task_runtime.execution fact append "
        "fails:\n" + "\n".join(offenders)
    )


def test_task_runtime_owner_transition_callers_are_reviewed() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        if _is_allowed_owner_path(path):
            continue
        offenders.extend(_owner_transition_call_boundary_violations(path))

    assert not offenders, (
        "TaskRuntimeService owner transitions are the only reviewed terminal "
        "TaskRow state writers. New production callers must be audited and "
        "added to the WS2 allowlist with coverage, otherwise TaskRuntime stops "
        "being the execution-state SSoT:\n" + "\n".join(offenders)
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


def test_runtime_task_row_file_access_stays_in_task_runtime_owner() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for root in (POLARIS_ROOT, BACKEND_ROOT / "scripts"):
        for path in root.rglob("*.py"):
            if path.resolve() == this_file or "__pycache__" in path.parts:
                continue
            if "tests" in path.parts:
                continue
            if _is_allowed_owner_path(path):
                continue
            offenders.extend(_direct_task_row_file_accesses(path))

    assert not offenders, (
        "runtime/tasks/task_*.json files are a task-runtime storage detail. "
        "Production code outside runtime.task_runtime must use task-runtime "
        "public projections such as list_observable_task_rows() instead of "
        "direct task-row file access:\n" + "\n".join(offenders)
    )


def test_task_runtime_execution_fact_writer_stays_in_task_runtime_service() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for root in (POLARIS_ROOT, BACKEND_ROOT / "scripts"):
        for path in root.rglob("*.py"):
            if path.resolve() == this_file or "__pycache__" in path.parts:
                continue
            if "tests" in path.parts:
                continue
            if path.resolve() == TASK_RUNTIME_INTERNAL_SERVICE.resolve():
                continue
            offenders.extend(_task_runtime_execution_writer_violations(path))

    assert not offenders, (
        "task_runtime.execution is the execution-state fact stream. "
        "Only TaskRuntimeService may append to it; other production code must "
        "use task-runtime owner APIs and projections instead of creating fact "
        "events or writing the event file directly:\n" + "\n".join(offenders)
    )


def test_task_runtime_execution_fact_reader_stays_in_task_runtime_service() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for root in (POLARIS_ROOT, BACKEND_ROOT / "scripts"):
        for path in root.rglob("*.py"):
            if path.resolve() == this_file or "__pycache__" in path.parts:
                continue
            if "tests" in path.parts:
                continue
            if path.resolve() == TASK_RUNTIME_INTERNAL_SERVICE.resolve():
                continue
            offenders.extend(_task_runtime_execution_reader_violations(path))

    assert not offenders, (
        "task_runtime.execution read projections are owned by TaskRuntimeService. "
        "Production code outside runtime.task_runtime must use task-runtime "
        "read-model APIs such as list_observable_task_rows(), not direct fact "
        "stream queries or event-file reads:\n" + "\n".join(offenders)
    )


def test_runtime_execution_metadata_writes_stay_in_task_runtime_owner() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for root in (POLARIS_ROOT, BACKEND_ROOT / "scripts"):
        for path in root.rglob("*.py"):
            if path.resolve() == this_file or "__pycache__" in path.parts:
                continue
            if "tests" in path.parts:
                continue
            if _is_allowed_owner_path(path):
                continue
            offenders.extend(_runtime_execution_metadata_writes(path))

    assert not offenders, (
        "metadata['runtime_execution'] is a task-runtime-owned execution-state "
        "projection. Production code outside runtime.task_runtime may read it "
        "as a projection, but must not write, pop, setdefault, or update it:\n" + "\n".join(offenders)
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


def test_role_worker_pool_task_runtime_port_does_not_require_live_listener() -> None:
    source = ROLE_WORKER_POOL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    port_class: ast.ClassDef | None = None
    legacy_port_class: ast.ClassDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "TaskRuntimePort":
            port_class = node
        if isinstance(node, ast.ClassDef) and node.name == "_LegacyTaskRuntimePort":
            legacy_port_class = node

    assert port_class is not None, "TaskRuntimePort protocol not found"
    port_methods = {item.name for item in port_class.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}

    assert "add_ready_listener" not in port_methods, (
        "TaskRuntimePort must not require live listener utilities. Worker wakeup "
        "may use an optional duck-typed add_ready_listener() optimization, but "
        "the required port contract should stay on the atomic claim-next "
        "execution transition."
    )
    assert port_methods == {"claim_next_execution"}

    assert legacy_port_class is not None, "_LegacyTaskRuntimePort protocol not found"
    legacy_port_methods = {
        item.name for item in legacy_port_class.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {"list_ready_task_rows", "claim_execution"} <= legacy_port_methods


def test_delivery_pm_taskboard_mainline_uses_task_runtime_service() -> None:
    source = DELIVERY_PM_TASKBOARD.read_text(encoding="utf-8")
    blocked_tokens = (
        "importlib.util",
        "create_taskboard",
        "_load_role_taskboard_module",
        "_taskboard_priority_enum",
        'runtime.get("board")',
        'runtime.get("module")',
        ".list_ready(",
        ".claim(",
        "._save_task",
    )
    offenders = [token for token in blocked_tokens if token in source]

    assert not offenders, (
        "delivery PM taskboard mainline must use TaskRuntimeService row/session APIs, "
        "not the retired role taskboard loader or raw TaskBoard calls: " + ", ".join(offenders)
    )


def test_delivery_cli_director_does_not_finalize_with_row_updates() -> None:
    source = DELIVERY_CLI_DIRECTOR_SERVICE.read_text(encoding="utf-8")
    blocked_tokens = (
        "update_task_row(",
        'event_type") == "updated"',
        "event_type') == 'updated'",
    )
    offenders = [token for token in blocked_tokens if token in source]

    assert not offenders, (
        "delivery CLI Director must not finalize execution with sessionless "
        "row updates. It should consume director.execution / TaskRuntimeService "
        "execution transitions and only inspect the resulting projection: " + ", ".join(offenders)
    )


def test_qa_rework_exhaustion_uses_task_runtime_owner_failure_transition() -> None:
    source = QA_ADAPTER.read_text(encoding="utf-8")
    offenders = _literal_status_update_task_row_calls(QA_ADAPTER, {"failed"})

    assert "fail_task_row_after_rework_exhausted" in source, (
        "QA exhausted-rework routing must use TaskRuntimeService's owner "
        "transition so failure status, session projection, and execution "
        "events remain one fact chain."
    )
    assert not offenders, (
        "QA adapter must not finalize Director task rows with sessionless "
        "update_task_row(status='failed'). Use "
        "fail_task_row_after_rework_exhausted() instead:\n" + "\n".join(offenders)
    )


def test_pm_dedup_cancel_uses_task_runtime_owner_transition() -> None:
    source = PM_BOARD_TASKS.read_text(encoding="utf-8")
    offenders = _literal_status_update_task_row_calls(PM_BOARD_TASKS, {"cancelled"})

    assert "cancel_task_row_for_deduplication" in source, (
        "PM duplicate-task cleanup must use TaskRuntimeService's owner "
        "transition so cancelled rows emit task_runtime.execution facts."
    )
    assert not offenders, (
        "PM board task deduplication must not finalize duplicate task rows "
        "with sessionless update_task_row(status='cancelled'). Use "
        "cancel_task_row_for_deduplication() instead:\n" + "\n".join(offenders)
    )


def test_pm_role_failure_uses_task_runtime_owner_failure_transition() -> None:
    source = PM_ADAPTER.read_text(encoding="utf-8")
    offenders = _literal_status_method_calls(PM_ADAPTER, "_update_board_task", {"failed"})

    assert "fail_task_row_from_role_adapter" in source, (
        "PM planning failures must use TaskRuntimeService's role-adapter "
        "failure transition so failed rows emit task_runtime.execution facts."
    )
    assert not offenders, (
        "PM adapter must not finalize planning task rows with "
        "_update_board_task(status='failed'). Use "
        "fail_task_row_from_role_adapter() instead:\n" + "\n".join(offenders)
    )


def test_base_role_adapter_rejects_terminal_status_shortcuts() -> None:
    source = ROLE_ADAPTER_BASE.read_text(encoding="utf-8")

    assert "_TERMINAL_TASK_ROW_STATUSES" in source
    assert "terminal_task_status_requires_task_runtime_owner_transition" in source


def test_task_runtime_terminal_projection_covers_taskboard_terminal_statuses() -> None:
    """WS2 terminal status convergence fence.

    ``TaskBoard.TaskStatus.is_terminal`` is the canonical row-local terminal
    predicate, while ``execution_session._TERMINAL_TASK_ROW_STATUSES`` drives
    the TaskRuntime read-model overlay.  The read model may carry extra
    compatibility values, but it must cover every TaskBoard terminal status
    value so future terminal enum additions cannot be missed by runtime
    projection logic.

    This is intentionally source-level and AST-backed: importing either module
    would execute production code and could hide drift behind import-time
    aliases.
    """

    taskboard_terminal_values = _task_status_is_terminal_values()
    runtime_terminal_values = _assigned_frozenset_literal_strings(
        EXECUTION_SESSION_MODULE,
        "_TERMINAL_TASK_ROW_STATUSES",
    )
    missing_values = sorted(taskboard_terminal_values - runtime_terminal_values)

    assert not missing_values, (
        "WS2 terminal projection convergence fence: "
        "execution_session._TERMINAL_TASK_ROW_STATUSES must cover every "
        "TaskBoard.TaskStatus.is_terminal value. Missing runtime projection "
        f"terminal values: {missing_values}. "
        f"TaskBoard terminal values: {sorted(taskboard_terminal_values)}. "
        f"Runtime projection terminal values: {sorted(runtime_terminal_values)}."
    )


# ---------------------------------------------------------------------------
# WS2 execution-status row-write fence — execution-like status guard
# ---------------------------------------------------------------------------
#
# Role adapters must not write execution-like TaskRow statuses (running,
# in_progress, claimed) through _update_board_task().  Execution status is
# owned by TaskRuntimeService owner transitions (claim_execution,
# complete_execution, fail_execution).  Writing execution-like status
# through a role adapter would bypass execution-event projection.
#
# _update_board_task() already blocks terminal statuses (completed, failed,
# cancelled, timeout) via _is_terminal_task_row_status.  This fence extends
# the invariant to execution-like statuses so the SSoT boundary is
# comprehensive.

_EXECUTION_LIKE_TASK_ROW_STATUSES = frozenset({"running", "in_progress", "claimed"})

ROLE_ADAPTER_PATHS: dict[str, Path] = {
    "pm_adapter": PM_ADAPTER,
    "qa_adapter": QA_ADAPTER,
    "director_adapter": DIRECTOR_ADAPTER,
}


def _contains_execution_status_guard(path: Path) -> bool:
    """Return True if ``path`` contains a guard that references execution-like
    task-row statuses in ``_update_board_task``.

    Low false-positive: checks for the presence of the execution-status
    guard pattern (``_is_execution_task_row_status`` or
    ``execution_task_status_requires_task_runtime_owner_transition`` error
    message) inside the ``_update_board_task`` method body.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "_update_board_task":
            continue
        body_text = ast.dump(node)
        has_execution_guard = "_is_execution_task_row_status" in body_text
        has_execution_msg = "execution_task_status_requires_task_runtime_owner_transition" in body_text
        return has_execution_guard or has_execution_msg
    return False


def _director_update_task_progress_uses_metadata_only_delegate(path: Path) -> bool:
    """Return True if Director progress delegates to Base metadata projection.

    ``BaseRoleAdapter._update_task_progress`` records ``event_status`` under
    ``adapter_event_status`` and calls ``_update_board_task`` with metadata only.
    Director must therefore call ``super()._update_task_progress(...)`` and must
    not directly forward ``event_status`` to ``_update_board_task(status=...)``.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "_update_task_progress":
            continue
        has_super_delegate = False
        forwards_status_to_board = False
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if isinstance(func, ast.Attribute) and func.attr == "_update_task_progress":
                value = func.value
                if isinstance(value, ast.Call) and _call_name(value.func) == "super":
                    has_super_delegate = True
            if isinstance(func, ast.Attribute) and func.attr == "_update_board_task":
                for keyword in child.keywords:
                    if keyword.arg == "status":
                        forwards_status_to_board = True
        return has_super_delegate and not forwards_status_to_board
    return False


def _literal_execution_status_update_board_task_calls(
    path: Path,
    statuses: AbstractSet[str],
) -> list[str]:
    """Detect literal ``_update_board_task(..., status=<literal>)`` calls
    where the status value is a string literal in the forbidden set.

    AST-only, low false-positive: only matches ``ast.Constant`` string
    keyword values, not variables or expressions.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rel = path.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "_update_board_task":
            continue
        for keyword in node.keywords:
            if keyword.arg != "status":
                continue
            status = _string_literal(keyword.value)
            if status in statuses:
                offenders.append(f"{rel}:{node.lineno} calls _update_board_task(status={status!r})")
    return offenders


def test_base_role_adapter_has_execution_status_guard() -> None:
    """WS2 execution-status row-write fence: BaseRoleAdapter._update_board_task()
    must contain a guard that blocks execution-like statuses.

    The guard ensures role adapters cannot write running/in_progress/claimed
    status through _update_board_task().  Execution status changes must use
    TaskRuntimeService owner transitions (claim_execution, complete_execution,
    fail_execution) so task_runtime.execution facts are always appended.
    """
    assert _contains_execution_status_guard(ROLE_ADAPTER_BASE), (
        "WS2 execution-status row-write fence: "
        "BaseRoleAdapter._update_board_task() must contain a guard that "
        "blocks execution-like statuses (running, in_progress, claimed). "
        "Execution status changes must use TaskRuntimeService owner "
        "transitions so task_runtime.execution facts are always appended."
    )


def test_director_progress_uses_metadata_only_status_projection() -> None:
    """WS2 execution-status row-write fence: DirectorAdapter._update_task_progress()
    must preserve progress statuses without writing TaskRow status.

    Director progress events carry trace statuses that may include
    execution-like values (running, in_progress, claimed) and terminal-looking
    values (failed/completed).  The progress path must store them through
    BaseRoleAdapter's metadata-only projection. Execution status changes must
    use TaskRuntimeService owner transitions.
    """
    assert _director_update_task_progress_uses_metadata_only_delegate(DIRECTOR_ADAPTER), (
        "WS2 execution-status row-write fence: "
        "DirectorAdapter._update_task_progress() must delegate to "
        "BaseRoleAdapter._update_task_progress() and must not call "
        "_update_board_task(status=event_status). Progress statuses are "
        "metadata evidence; TaskRow status writes must use TaskRuntimeService "
        "owner transitions."
    )


def test_production_adapters_do_not_write_execution_status_literals() -> None:
    """WS2 execution-status row-write fence: production role adapters must
    not call ``_update_board_task(status="running"/"in_progress"/"claimed")``
    with literal execution-like status values.

    Execution status is owned by TaskRuntimeService owner transitions.
    Role adapters calling _update_board_task() with execution-like statuses
    would bypass execution-event projection.  This fence detects literal
    calls; dynamic/variable status values are covered by the base-class
    guard fence.
    """
    offenders: list[str] = []
    for _adapter_name, path in sorted(ROLE_ADAPTER_PATHS.items()):
        if not path.is_file():
            continue
        offenders.extend(_literal_execution_status_update_board_task_calls(path, _EXECUTION_LIKE_TASK_ROW_STATUSES))

    assert not offenders, (
        "WS2 execution-status row-write fence: "
        "Production role adapters must not call _update_board_task() with "
        "literal execution-like status values (running, in_progress, "
        "claimed). Execution status changes must use TaskRuntimeService "
        "owner transitions so task_runtime.execution facts are always "
        "appended:\n" + "\n".join(offenders)
    )


def test_execution_status_fence_detects_literal_status_call() -> None:
    """Characterization: the AST detection catches
    ``_update_board_task(status="running")`` calls.

    Uses ``ast.parse`` on a synthetic fragment to prove the detection
    helper would flag a literal execution-like status keyword.
    """
    fragment = 'self._update_board_task(task_id, status="running")\n'
    tree = ast.parse(fragment)
    detected = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "_update_board_task":
            continue
        for keyword in node.keywords:
            if keyword.arg == "status" and _string_literal(keyword.value) == "running":
                detected = True
    assert detected, (
        "Characterization fence: AST detection must flag literal "
        '_update_board_task(status="running") calls. If this test '
        "fails, the execution-status fence can silently stop detecting "
        "violations."
    )


def test_execution_status_fence_allows_metadata_only_update() -> None:
    """Characterization: the AST detection does NOT flag
    ``_update_board_task(metadata={...})`` calls without a status keyword.

    Metadata-only updates are the reviewed pattern for role adapters;
    the fence must not block them.
    """
    fragment = 'self._update_board_task(task_id, metadata={"phase": "executing"})\n'
    tree = ast.parse(fragment)
    offender_count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "_update_board_task":
            continue
        for keyword in node.keywords:
            if keyword.arg == "status" and _string_literal(keyword.value) in _EXECUTION_LIKE_TASK_ROW_STATUSES:
                offender_count += 1
    assert offender_count == 0, (
        "Characterization fence: AST detection must NOT flag "
        "_update_board_task(metadata={...}) calls. The fence should "
        "only block literal execution-like status keywords."
    )


def test_director_adapter_progress_does_not_finalize_task_rows() -> None:
    source = DIRECTOR_ADAPTER.read_text(encoding="utf-8")

    assert "super()._update_task_progress(" in source, (
        "Director progress events must use BaseRoleAdapter's metadata-only progress projection."
    )
    assert "return super()._update_board_task(task_id, status=status, metadata=metadata)" in source
    assert "self.task_runtime.update_task_row(" not in source


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
        "execution-control state must flow through TaskRuntimeService row/session APIs:\n" + "\n".join(offenders)
    )


def _call_leaf_name(node: ast.AST) -> str:
    return _call_name(node).rsplit(".", maxsplit=1)[-1]


def _taskboard_methods_reachable_from(method_name: str) -> list[ast.FunctionDef]:
    taskboard = _taskboard_class()
    methods_by_name = {node.name: node for node in taskboard.body if isinstance(node, ast.FunctionDef)}
    root = methods_by_name.get(method_name)
    if root is None:
        raise AssertionError(f"TaskBoard.{method_name}() not found")

    reachable: list[ast.FunctionDef] = []
    pending = [root]
    seen: set[str] = set()
    while pending:
        function_def = pending.pop()
        if function_def.name in seen:
            continue
        seen.add(function_def.name)
        reachable.append(function_def)

        for node in ast.walk(function_def):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "self":
                continue
            helper = methods_by_name.get(func.attr)
            if helper is not None and helper.name not in seen:
                pending.append(helper)

    return reachable


def _node_references_name_or_attribute(node: ast.AST, expected_name: str) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id == expected_name:
            return True
        if isinstance(child, ast.Attribute) and child.attr == expected_name:
            return True
    return False


def _write_terminal_event_fact_stream_cas_violations() -> list[str]:
    """Validate TaskBoard terminal compatibility projection FactStream writes.

    ``taskboard.terminal.events`` is a compatibility projection, not the
    authority for execution control. The append still needs FactStream CAS so
    concurrent terminal projections converge instead of racing through the
    non-CAS append path. The fence follows direct ``self`` helpers reachable
    from ``_write_terminal_event()`` so a refactor may move the mechanics into
    a private helper without weakening the invariant.
    """

    function_defs = _taskboard_methods_reachable_from("_write_terminal_event")
    offenders: list[str] = []
    append_command_count = 0

    for function_def in function_defs:
        label = f"TaskBoard.{function_def.name}()"
        for node in ast.walk(function_def):
            if not isinstance(node, ast.Call):
                continue

            callee = _call_leaf_name(node.func)
            if callee == "AppendFactEventCommandV1":
                append_command_count += 1
                expected_seq = next(
                    (keyword for keyword in node.keywords if keyword.arg == "expected_seq"),
                    None,
                )
                if expected_seq is None:
                    offenders.append(
                        f"{label}:line {node.lineno} constructs AppendFactEventCommandV1 without expected_seq="
                    )
                elif isinstance(expected_seq.value, ast.Constant) and expected_seq.value.value is None:
                    offenders.append(
                        f"{label}:line {node.lineno} passes expected_seq=None; "
                        "terminal compatibility appends must opt into CAS"
                    )
                continue

            if callee in TASKBOARD_TERMINAL_EVENT_DIRECT_WRITE_METHODS:
                offenders.append(
                    f"{label}:line {node.lineno} calls {callee}(); "
                    f"{TASKBOARD_TERMINAL_EVENT_STREAM} must write through "
                    "FactStream CAS, not direct JSONL/file append"
                )

    if append_command_count == 0:
        offenders.append(
            "TaskBoard._write_terminal_event() must construct "
            "AppendFactEventCommandV1 directly or through a reachable "
            "TaskBoard helper"
        )

    handles_fact_stream_error = any(
        _node_references_name_or_attribute(function_def, "FactStreamError") for function_def in function_defs
    )
    if not handles_fact_stream_error:
        offenders.append(
            "TaskBoard._write_terminal_event() or a reachable helper must "
            "reference/catch FactStreamError so CAS conflicts and stream "
            "append failures are handled explicitly"
        )

    return offenders


def test_taskboard_terminal_event_append_uses_fact_stream_cas_and_error_handling() -> None:
    """WS2 CAS fence for ``taskboard.terminal.events`` compatibility writes.

    ``taskboard.terminal.events`` remains an internal TaskRuntime compatibility
    projection rather than an authority. Even so, terminal compatibility events
    must append through FactStream ``expected_seq`` CAS and explicitly handle
    ``FactStreamError`` so concurrent writers converge and failures are
    traceable. Direct JSONL/file append would bypass that convergence contract.
    """

    offenders = _write_terminal_event_fact_stream_cas_violations()

    assert not offenders, (
        "WS2 taskboard terminal compatibility stream CAS fence: "
        "TaskBoard._write_terminal_event() must append "
        f"{TASKBOARD_TERMINAL_EVENT_STREAM} via AppendFactEventCommandV1("
        "expected_seq=...), handle FactStreamError directly or through a "
        "reachable helper, and avoid direct JSONL/file append. Offenders:\n" + "\n".join(offenders)
    )


def test_taskboard_terminal_event_stream_is_owner_only_compatibility_projection() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        if _is_allowed_owner_path(path):
            continue
        if TASKBOARD_TERMINAL_EVENT_STREAM in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(BACKEND_ROOT)))

    assert not offenders, (
        "`taskboard.terminal.events` is a task_runtime-owned compatibility "
        "projection, not an execution-control fact source. Production code "
        "outside task_runtime must consume TaskRuntimeService / execution "
        "ledger projections instead:\n" + "\n".join(offenders)
    )


def test_director_resume_preparation_uses_task_runtime_owner() -> None:
    sources = {
        "factory_http_router": FACTORY_HTTP_ROUTER.read_text(encoding="utf-8"),
        "factory_bench_runner": FACTORY_BENCH_RUNNER.read_text(encoding="utf-8"),
    }
    blocked_tokens = (
        "_director_resume_reset_task_payload",
        'glob("task_*.json")',
        "glob('task_*.json')",
        "target_dir / task_file.name",
        "task_file.write_text(",
        "session_file.unlink(",
        "shutil.copy2(max_id",
    )
    offenders = [
        f"{source_name}:{token}"
        for source_name, source in sources.items()
        for token in blocked_tokens
        if token in source
    ]

    missing_owner_calls = [
        f"{source_name}:import_task_rows_for_reexecution"
        for source_name, source in sources.items()
        if "import_task_rows_for_reexecution" not in source
    ]
    missing_owner_calls.extend(
        f"{source_name}:reset_task_rows_for_reexecution"
        for source_name, source in sources.items()
        if "reset_task_rows_for_reexecution" not in source
    )
    missing_owner_calls.extend(
        f"{source_name}:inspect_reexecution_source_task_rows"
        for source_name, source in sources.items()
        if "inspect_reexecution_source_task_rows" not in source
    )

    assert not offenders, (
        "Director-resume preparation must not rewrite runtime/tasks rows or "
        "sessions directly. Use TaskRuntimeService owner APIs so every row "
        "mutation has task_runtime.execution evidence:\n" + "\n".join(offenders)
    )
    assert not missing_owner_calls, (
        "Director-resume preparation must route import/reset through "
        "TaskRuntimeService owner APIs:\n" + "\n".join(missing_owner_calls)
    )


def test_factory_stage_executor_reads_task_rows_through_task_runtime_projection() -> None:
    offenders = _direct_task_row_file_globs(FACTORY_STAGE_EXECUTOR)
    source = FACTORY_STAGE_EXECUTOR.read_text(encoding="utf-8")

    assert not offenders, (
        "Factory stage executor must not scan runtime/tasks/task_*.json as an "
        "execution fact source. Use TaskRuntimeService.list_observable_task_rows() "
        "so task_runtime.execution facts remain in the read projection:\n" + "\n".join(offenders)
    )
    assert "list_observable_task_rows" in source, (
        "Factory stage executor must consume TaskRuntimeService.list_observable_task_rows() "
        "for read-only task status projections."
    )


def test_runtime_artifact_store_reads_task_rows_through_task_runtime_projection() -> None:
    offenders = _direct_task_row_file_globs(RUNTIME_ARTIFACT_STORE_ARTIFACTS)
    source = RUNTIME_ARTIFACT_STORE_ARTIFACTS.read_text(encoding="utf-8")

    assert not offenders, (
        "runtime.artifact_store must not scan runtime/tasks/task_*.json as an "
        "execution fact source. Task rows are owned by runtime.task_runtime; "
        "artifact-backed workflow status must use TaskRuntimeService observable rows:\n" + "\n".join(offenders)
    )
    assert "list_observable_task_rows" in source, (
        "runtime.artifact_store workflow status must consume TaskRuntimeService.list_observable_task_rows()."
    )


def test_factory_bench_task_runtime_event_file_is_workspace_evidence_only() -> None:
    evidence_paths = _assigned_constant_tuple(FACTORY_BENCH_RUNNER, "_RUNTIME_WORKSPACE_EVIDENCE_RELATIVE_PATHS")
    source = FACTORY_BENCH_RUNNER.read_text(encoding="utf-8")
    workspace_matcher = _function_def(FACTORY_BENCH_RUNNER, "_file_mentions_workspace")
    runtime_matcher = _function_def(FACTORY_BENCH_RUNNER, "_runtime_dir_matches_workspace")
    matcher_calls = {_call_name(node.func) for node in ast.walk(workspace_matcher) if isinstance(node, ast.Call)}
    runtime_matcher_calls = {_call_name(node.func) for node in ast.walk(runtime_matcher) if isinstance(node, ast.Call)}

    assert "events/task_runtime.execution.jsonl" in evidence_paths
    assert source.count('"events/task_runtime.execution.jsonl"') == 1, (
        "factory_bench may list task_runtime.execution.jsonl only as runtime-dir "
        "workspace evidence. Execution status projections must use task-runtime "
        "owner/read-model APIs, not direct event-file parsing."
    )
    assert not ({"json.loads", "json.load"} & matcher_calls), (
        "_file_mentions_workspace must remain a bounded text evidence check, not "
        "a task_runtime.execution status reader."
    )
    assert "_file_mentions_workspace" in runtime_matcher_calls, (
        "_runtime_dir_matches_workspace should consume task-runtime events only through workspace evidence matching."
    )
    assert not any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in {"status", "execution_state", "resume_state", "task_row_snapshot"}
        for node in ast.walk(workspace_matcher)
    ), "Workspace evidence matching must not inspect task execution fields from task_runtime.execution events."


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
                        offenders.append(f"TaskBoard.{method_name}():{node.lineno} saves a non-local task row")
                if called.endswith(".append") or called.endswith(".remove"):
                    receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
                    if isinstance(receiver, ast.Attribute) and receiver.attr in {"blocks", "blocked_by"}:
                        offenders.append(f"TaskBoard.{method_name}():{node.lineno} mutates dependency links directly")
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


def test_task_runtime_service_raw_board_writes_are_reviewed() -> None:
    actual = _task_runtime_service_raw_board_write_calls()
    expected = Counter(REVIEWED_TASK_RUNTIME_SERVICE_BOARD_WRITES)
    offenders: list[str] = []
    for key in sorted(set(actual) | set(expected)):
        actual_count = actual.get(key, 0)
        expected_count = expected.get(key, 0)
        if actual_count != expected_count:
            method_name, board_method = key
            offenders.append(
                f"{method_name} -> self._board.{board_method}: expected {expected_count}, found {actual_count}"
            )

    assert not offenders, (
        "TaskRuntimeService is the only reviewed owner for raw TaskBoard writes. "
        "New raw Board write calls must be audited for execution-ledger evidence "
        "and recorded in REVIEWED_TASK_RUNTIME_SERVICE_BOARD_WRITES:\n" + "\n".join(offenders)
    )


def test_task_runtime_service_raw_board_reads_are_reviewed() -> None:
    actual = _task_runtime_service_raw_board_read_calls()
    expected = Counter(REVIEWED_TASK_RUNTIME_SERVICE_BOARD_READS)
    offenders: list[str] = []
    for key in sorted(set(actual) | set(expected)):
        actual_count = actual.get(key, 0)
        expected_count = expected.get(key, 0)
        if actual_count != expected_count:
            method_name, board_method = key
            offenders.append(
                f"{method_name} -> self._board.{board_method}: expected {expected_count}, found {actual_count}"
            )

    assert not offenders, (
        "TaskRuntimeService is the reviewed owner for raw TaskBoard reads. "
        "New raw Board read calls must be audited against the observable "
        "read-model boundary and recorded in REVIEWED_TASK_RUNTIME_SERVICE_BOARD_READS:\n" + "\n".join(offenders)
    )


def test_augment_task_row_does_not_read_raw_taskboard() -> None:
    methods = _task_runtime_service_method_defs()
    method_name = "_augment_task_row"
    method_def = methods.get(method_name)
    assert method_def is not None, f"TaskRuntimeService.{method_name}() must exist"

    raw_read_calls = {f"self._board.{board_method}" for board_method in TASK_RUNTIME_SERVICE_RAW_BOARD_READ_METHODS}
    offenders = [
        f"{TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)}:{node.lineno} "
        f"TaskRuntimeService.{method_name}() calls {_call_name(node.func)}()"
        for node in _walk_task_runtime_method_body(method_def)
        if isinstance(node, ast.Call) and _call_name(node.func) in raw_read_calls
    ]

    assert not offenders, (
        "TaskRuntimeService._augment_task_row() must enrich rows from the "
        "task_runtime execution read model it is given, not by re-reading raw "
        "TaskBoard state through self._board.*(). Offenders:\n" + "\n".join(offenders)
    )


def test_task_runtime_service_raw_list_all_is_centralized_in_file_task_entities() -> None:
    methods = _task_runtime_service_method_defs()
    helper_name = TASK_RUNTIME_SERVICE_RAW_BOARD_LIST_HELPER
    helper = methods.get(helper_name)
    assert helper is not None, f"TaskRuntimeService.{helper_name}() must exist"

    direct_calls_by_method = {
        method_name: _direct_self_board_list_all_calls(method_def) for method_name, method_def in methods.items()
    }
    offenders = [
        f"{TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)}:{call.lineno} TaskRuntimeService.{method_name}()"
        for method_name, calls in sorted(direct_calls_by_method.items())
        if method_name != helper_name
        for call in calls
    ]
    helper_call_count = len(direct_calls_by_method.get(helper_name, []))

    assert not offenders, (
        "TaskRuntimeService raw TaskBoard entity reads must be centralized in "
        f"self.{helper_name}(). Direct self._board.list_all() callers:\n" + "\n".join(offenders)
    )
    assert helper_call_count == 1, (
        f"TaskRuntimeService.{helper_name}() must be the single direct "
        f"self._board.list_all() bridge; found {helper_call_count} direct calls."
    )


def test_key_task_runtime_methods_route_raw_entities_through_file_task_entities() -> None:
    methods = _task_runtime_service_method_defs()
    helper_name = TASK_RUNTIME_SERVICE_RAW_BOARD_LIST_HELPER
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)
    missing = sorted(TASK_RUNTIME_SERVICE_RAW_BOARD_ENTITY_CONSUMERS.difference(methods))

    assert not missing, f"Missing expected TaskRuntimeService methods: {missing}"

    direct_call_offenders: list[str] = []
    missing_helper_offenders: list[str] = []
    for method_name in sorted(TASK_RUNTIME_SERVICE_RAW_BOARD_ENTITY_CONSUMERS):
        method_def = methods[method_name]
        direct_call_offenders.extend(
            f"{rel}:{call.lineno} TaskRuntimeService.{method_name}()"
            for call in _direct_self_board_list_all_calls(method_def)
        )
        if not _method_body_directly_calls_self_method(method_def, helper_name):
            missing_helper_offenders.append(f"TaskRuntimeService.{method_name}()")

    assert not direct_call_offenders, (
        "Critical TaskRuntimeService methods must not call self._board.list_all() "
        f"directly; route raw entity reads through self.{helper_name}(). "
        "Offenders:\n" + "\n".join(direct_call_offenders)
    )
    assert not missing_helper_offenders, (
        "Critical TaskRuntimeService methods that need raw file-backed Task "
        f"entities must call self.{helper_name}() so the raw TaskBoard boundary "
        "has one owner-cell bridge. Offenders:\n" + "\n".join(missing_helper_offenders)
    )


def test_dependent_rows_blocked_by_uses_observable_read_model() -> None:
    calls = _function_call_counts(TASK_RUNTIME_INTERNAL_SERVICE, "_dependent_rows_blocked_by")
    expected_call = "self.list_observable_task_rows"
    forbidden_calls = {
        "self._board.get",
        "self._board.list_all",
        "self._list_file_task_rows",
        "self.list_task_rows",
    }
    offenders = [
        f"_dependent_rows_blocked_by() calls {call}() {count} time(s)"
        for call, count in sorted(calls.items())
        if call in forbidden_calls
    ]

    assert calls[expected_call] == 1, (
        "TaskRuntimeService._dependent_rows_blocked_by() must read dependency "
        "evidence through list_observable_task_rows() exactly once so "
        "task_runtime.execution facts remain the read-side SSoT before "
        f"dependency fan-out mutation. Found {calls[expected_call]} calls."
    )
    assert not offenders, (
        "TaskRuntimeService._dependent_rows_blocked_by() is a read-side "
        "dependency evidence helper and must not bypass the observable "
        "execution-ledger projection through raw TaskBoard reads, "
        "list_task_rows(), or _list_file_task_rows(). refresh_dependency_unblocks() "
        "is intentionally outside this fence because it still mutates raw Task "
        "objects. Offenders:\n" + "\n".join(offenders)
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


def test_task_runtime_descriptor_does_not_advertise_internal_execution_session_symbols() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders = [
        str(item.get("name") or "")
        for item in capabilities
        if isinstance(item, dict) and item.get("defined_in") == TASK_RUNTIME_INTERNAL_EXECUTION_SESSION_DESCRIPTOR_FILE
    ]

    assert not offenders, (
        "task_runtime descriptor must not advertise internal execution-session "
        "implementation symbols. Agent-facing context should use "
        "TaskRuntimeService row/session APIs instead of constructing, parsing, "
        "or mutating session implementation objects directly:\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_private_top_level_helpers() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders = [
        str(item.get("name") or "")
        for item in capabilities
        if isinstance(item, dict) and str(item.get("name") or "").startswith("_")
    ]

    assert not offenders, (
        "task_runtime descriptor must not advertise private top-level helper "
        "functions from implementation or contract modules. Generated context "
        "should expose public contracts and service APIs only:\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_retired_service_methods() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders: list[str] = []
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "TaskRuntimeService":
            continue
        for method in item.get("methods", []):
            if not isinstance(method, dict):
                continue
            method_name = str(method.get("name") or "")
            if method_name in TASK_RUNTIME_SERVICE_RETIRED_PUBLIC_METHODS:
                offenders.append(method_name)

    assert not offenders, (
        "task_runtime descriptor must not advertise retired TaskRuntimeService "
        "compatibility methods. Public consumers should use row/session APIs "
        "such as list_task_rows(), list_ready_task_rows(), get_task_row_stats(), "
        "and list_observable_task_rows():\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_retired_entity_methods() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders: list[str] = []
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "TaskRuntimeService":
            continue
        for method in item.get("methods", []):
            if not isinstance(method, dict):
                continue
            method_name = str(method.get("name") or "")
            if method_name in TASK_RUNTIME_SERVICE_RETIRED_ENTITY_METHODS:
                offenders.append(method_name)

    assert not offenders, (
        "task_runtime descriptor must not advertise retired entity-returning "
        "TaskRuntimeService compatibility methods. Public consumers should use "
        "row/session APIs such as create_task_row(), update_task_row(), "
        "reopen_task_row(), and get_task():\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_advertises_current_row_mutation_methods() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    advertised: set[str] = set()
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "TaskRuntimeService":
            continue
        for method in item.get("methods", []):
            if isinstance(method, dict):
                advertised.add(str(method.get("name") or ""))

    missing = sorted(TASK_RUNTIME_SERVICE_REQUIRED_ROW_MUTATION_METHODS - advertised)
    assert not missing, (
        "task_runtime descriptor must advertise current row mutation methods so "
        "agents use APIs that return projection and execution-event evidence:\n" + "\n".join(missing)
    )


def test_task_runtime_descriptor_advertises_current_read_model_methods() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    advertised: set[str] = set()
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "TaskRuntimeService":
            continue
        for method in item.get("methods", []):
            if isinstance(method, dict):
                advertised.add(str(method.get("name") or ""))

    missing = sorted(TASK_RUNTIME_SERVICE_REQUIRED_READ_MODEL_METHODS - advertised)
    assert not missing, (
        "task_runtime descriptor must advertise current read-model methods so "
        "agents are guided toward owner projections rather than retired "
        "TaskBoard compatibility methods:\n" + "\n".join(missing)
    )


def test_task_runtime_descriptor_does_not_advertise_private_service_methods() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders: list[str] = []
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "TaskRuntimeService":
            continue
        for method in item.get("methods", []):
            if not isinstance(method, dict):
                continue
            method_name = str(method.get("name") or "")
            if method_name.startswith("_") and method_name != "__init__":
                offenders.append(method_name)

    assert not offenders, (
        "task_runtime descriptor must not advertise private TaskRuntimeService "
        "owner implementation methods. Descriptor context should expose stable "
        "row/session/read-model APIs, not storage, session, selection, or event "
        "internals:\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_non_operation_service_methods() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders: list[str] = []
    advertised: set[str] = set()
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "TaskRuntimeService":
            continue
        for method in item.get("methods", []):
            if not isinstance(method, dict):
                continue
            method_name = str(method.get("name") or "")
            advertised.add(method_name)
            if method_name in TASK_RUNTIME_SERVICE_NON_OPERATION_DESCRIPTOR_METHODS:
                offenders.append(method_name)

    required_operations = (
        TASK_RUNTIME_SERVICE_REQUIRED_ROW_MUTATION_METHODS | TASK_RUNTIME_SERVICE_REQUIRED_READ_MODEL_METHODS
    )
    missing_operations = sorted(required_operations - advertised)

    assert not missing_operations, (
        "task_runtime descriptor must keep stable operation APIs while hiding "
        "construction and metadata methods:\n" + "\n".join(missing_operations)
    )
    assert not offenders, (
        "task_runtime descriptor must not advertise construction or metadata "
        "methods on TaskRuntimeService. Agent-facing context should expose only "
        "task execution, row mutation, and read-model operations:\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_live_listener_or_raw_board_methods() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders: list[str] = []
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "TaskRuntimeService":
            continue
        for method in item.get("methods", []):
            if not isinstance(method, dict):
                continue
            method_name = str(method.get("name") or "")
            if method_name in TASK_RUNTIME_SERVICE_NON_AGENT_DESCRIPTOR_METHODS:
                offenders.append(method_name)

    assert not offenders, (
        "task_runtime descriptor must not advertise raw-board access or live "
        "condition/listener utilities. Agent-facing context should use stable "
        "row/session/read-model methods instead:\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_destructive_service_methods() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders: list[str] = []
    service_seen = False
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        if item.get("name") == "reset_runtime_task_records":
            offenders.append("reset_runtime_task_records")
        if item.get("name") != "TaskRuntimeService":
            continue
        service_seen = True
        for method in item.get("methods", []):
            if not isinstance(method, dict):
                continue
            method_name = str(method.get("name") or "")
            if method_name in TASK_RUNTIME_SERVICE_DESTRUCTIVE_DESCRIPTOR_METHODS:
                offenders.append(method_name)

    assert service_seen, "task_runtime descriptor must still advertise TaskRuntimeService"
    assert not offenders, (
        "task_runtime descriptor must not advertise destructive reset methods "
        "or functions. Reset orchestration must remain an explicit owner-cell "
        "runtime call path, not an Agent-facing generated capability:\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_utility_service_methods() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders: list[str] = []
    advertised: set[str] = set()
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "TaskRuntimeService":
            continue
        for method in item.get("methods", []):
            if not isinstance(method, dict):
                continue
            method_name = str(method.get("name") or "")
            advertised.add(method_name)
            if method_name in TASK_RUNTIME_SERVICE_UTILITY_DESCRIPTOR_METHODS:
                offenders.append(method_name)

    assert "get_task" in advertised, (
        "task_runtime descriptor must keep get_task() as the observable task "
        "lookup API when utility helpers are hidden."
    )
    assert not offenders, (
        "task_runtime descriptor must not advertise helper-style utility "
        "methods. Agent-facing context should use get_task() and row/read-model "
        "APIs instead of task id normalization or raw existence checks:\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_owner_read_primitives() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders: list[str] = []
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "TaskRuntimeService":
            continue
        for method in item.get("methods", []):
            if not isinstance(method, dict):
                continue
            method_name = str(method.get("name") or "")
            if method_name in TASK_RUNTIME_SERVICE_OWNER_READ_DESCRIPTOR_METHODS:
                offenders.append(method_name)

    assert not offenders, (
        "task_runtime descriptor must not advertise owner read primitives. "
        "Agent-facing status, UI, and observer context should use "
        "list_observable_task_rows() so execution facts remain part of the "
        "read-model projection:\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_owner_maintenance_methods() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders: list[str] = []
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "TaskRuntimeService":
            continue
        for method in item.get("methods", []):
            if not isinstance(method, dict):
                continue
            method_name = str(method.get("name") or "")
            if method_name in TASK_RUNTIME_SERVICE_OWNER_MAINTENANCE_DESCRIPTOR_METHODS:
                offenders.append(method_name)

    assert not offenders, (
        "task_runtime descriptor must not advertise owner maintenance methods. "
        "Dependency unblock refresh is a side effect owned by TaskRuntimeService "
        "selection/read-model paths; Agent-facing context should use explicit "
        "read-model, select, claim, or terminal transition APIs instead:\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_preview_selection_methods() -> None:
    descriptor = json.loads(TASK_RUNTIME_DESCRIPTOR.read_text(encoding="utf-8"))
    capabilities = descriptor.get("capabilities")
    assert isinstance(capabilities, list), "task_runtime descriptor must expose a capabilities list"

    offenders: list[str] = []
    advertised: set[str] = set()
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "TaskRuntimeService":
            continue
        for method in item.get("methods", []):
            if not isinstance(method, dict):
                continue
            method_name = str(method.get("name") or "")
            advertised.add(method_name)
            if method_name in TASK_RUNTIME_SERVICE_PREVIEW_SELECTION_DESCRIPTOR_METHODS:
                offenders.append(method_name)

    assert "claim_next_execution" in advertised, (
        "task_runtime descriptor must keep the atomic select-and-claim API for execution consumers."
    )
    assert not offenders, (
        "task_runtime descriptor must not advertise preview-only selection "
        "methods. Concurrent execution consumers should use claim_next_execution() "
        "so selection and claim stay in one owner operation:\n" + "\n".join(sorted(offenders))
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


def _append_execution_event_function_def() -> ast.FunctionDef:
    source = TASK_RUNTIME_INTERNAL_SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parent_lookup(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_append_execution_event":
            continue
        enclosing_class = parents.get(node)
        if isinstance(enclosing_class, ast.ClassDef) and enclosing_class.name == "TaskRuntimeService":
            return node
    raise AssertionError(
        "TaskRuntimeService._append_execution_event() not found in "
        f"{TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)}"
    )


def _build_task_runtime_execution_event_append_result_function_def() -> ast.FunctionDef:
    source = (BACKEND_ROOT / "polaris/cells/runtime/task_runtime/internal/execution_session.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "build_task_runtime_execution_event_append_result":
            return node
    raise AssertionError(
        "build_task_runtime_execution_event_append_result() not found in "
        "polaris/cells/runtime/task_runtime/internal/execution_session.py"
    )


def _attribute_chain_matches(node: ast.AST, expected_chain: tuple[str, ...]) -> bool:
    """Match an attribute chain that ends at ``node``.

    ``expected_chain`` lists attribute names from the deepest attribute outward
    toward the terminal receiver (e.g. ``("appended_seq", "appended")`` for
    ``appended.appended_seq``). Each intermediate hop must be an ``ast.Attribute``
    whose ``attr`` matches the expected entry; the final hop may be either an
    ``ast.Attribute`` (longer chain) or an ``ast.Name`` (terminal receiver).
    """

    if not expected_chain:
        return False
    current: ast.AST = node
    last_index = len(expected_chain) - 1
    for index, expected in enumerate(expected_chain):
        if isinstance(current, ast.Name):
            return current.id == expected and index == last_index
        if isinstance(current, ast.Attribute):
            if current.attr != expected:
                return False
            current = current.value
            continue
        return False
    return True


def _append_execution_event_references_appended_seq() -> bool:
    function_def = _append_execution_event_function_def()
    expected_chain = ("appended_seq", "appended")
    return any(_attribute_chain_matches(node, expected_chain) for node in ast.walk(function_def))


def _build_task_runtime_execution_event_append_result_accepts_fact_event_seq() -> bool:
    function_def = _build_task_runtime_execution_event_append_result_function_def()
    return any(arg.arg == "fact_event_seq" for arg in function_def.args.args + function_def.args.kwonlyargs)


def _append_execution_event_calls_pass_fact_event_seq() -> tuple[bool, list[str]]:
    """Detect call sites that propagate ``appended.appended_seq`` to the append-result.

    The success-path call sites must propagate the append-only sequence
    projection by passing ``fact_event_seq=appended.appended_seq`` to
    ``build_task_runtime_execution_event_append_result``. Pre-append failure
    branches (where ``appended`` is not yet bound) are excluded by checking
    that the call is not enclosed by any ``ExceptHandler`` between it and the
    function body.
    """

    function_def = _append_execution_event_function_def()
    parents = _parent_lookup(function_def)
    missing: list[str] = []
    success_call_count = 0
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node.func) != "build_task_runtime_execution_event_append_result":
            continue
        # Skip call sites that are inside an ExceptHandler (pre-append failure branch).
        enclosing: ast.AST | None = parents.get(node)
        in_except = False
        while enclosing is not None and enclosing is not function_def:
            if isinstance(enclosing, ast.ExceptHandler):
                in_except = True
                break
            enclosing = parents.get(enclosing)
        if in_except:
            continue
        if not any(keyword.arg == "fact_event_seq" for keyword in node.keywords):
            missing.append(f"line {node.lineno} (success call without fact_event_seq=)")
            continue
        success_call_count += 1
        keyword_value_node = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "fact_event_seq"),
            None,
        )
        if keyword_value_node is None or not _attribute_chain_matches(
            keyword_value_node,
            ("appended_seq", "appended"),
        ):
            missing.append(f"line {node.lineno} (fact_event_seq= must source appended.appended_seq)")
    return success_call_count > 0 and not missing, missing


def _append_execution_fact_with_cas_uses_expected_seq_contract() -> tuple[bool, list[str]]:
    """Detect whether TaskRuntime execution facts opt into FactStream CAS."""

    try:
        function_def = _task_runtime_service_method_def("_append_execution_fact_with_cas")
    except AssertionError as exc:
        return False, [str(exc)]

    offenders: list[str] = []
    calls_next_seq = any(
        isinstance(node, ast.Call) and _call_name(node.func) == "self._next_execution_fact_expected_seq"
        for node in ast.walk(function_def)
    )
    if not calls_next_seq:
        offenders.append(
            "_append_execution_fact_with_cas must derive expected_seq via self._next_execution_fact_expected_seq()"
        )

    append_command_calls = [
        node
        for node in ast.walk(function_def)
        if isinstance(node, ast.Call) and _call_name(node.func) == "AppendFactEventCommandV1"
    ]
    if not append_command_calls:
        offenders.append("_append_execution_fact_with_cas must construct AppendFactEventCommandV1")
    for node in append_command_calls:
        expected_keyword = next((keyword for keyword in node.keywords if keyword.arg == "expected_seq"), None)
        if expected_keyword is None:
            offenders.append("AppendFactEventCommandV1 call must pass expected_seq=")
            continue
        if not isinstance(expected_keyword.value, ast.Name) or expected_keyword.value.id != "expected_seq":
            offenders.append("AppendFactEventCommandV1 expected_seq= must use the derived expected_seq local")

    return not offenders, offenders


def test_task_runtime_append_event_propagates_fact_stream_sequence_evidence() -> None:
    """WS2 append-only Fact Stream sequence evidence fence.

    ``TaskRuntimeService._append_execution_event`` must surface ``appended.appended_seq``
    from the ``FactEventAppendedV1`` return value into the execution-event append
    result. Otherwise the Fact Stream keeps the assigned sequence internally while
    every caller's ``build_task_runtime_execution_event_append_result`` projection
    silently drops it, breaking append-only evidence reconstruction for
    ``task_runtime.execution``.
    """

    service_rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    execution_session_rel = "polaris/cells/runtime/task_runtime/internal/execution_session.py"
    offender_blocks: list[str] = []

    projected_append_seq = _append_execution_event_references_appended_seq()
    if not projected_append_seq:
        offender_blocks.append(
            f"{service_rel}:TaskRuntimeService._append_execution_event does not "
            f"reference appended.appended_seq; the Fact Stream sequence number from "
            f"the FactEventAppendedV1 return value cannot be lost."
        )

    if not _build_task_runtime_execution_event_append_result_accepts_fact_event_seq():
        offender_blocks.append(
            f"{execution_session_rel}:build_task_runtime_execution_event_append_result "
            f"must accept a fact_event_seq keyword argument so "
            f"TaskRuntimeService._append_execution_event can surface the WS2 "
            f"append-only Fact Stream sequence projection."
        )

    calls_pass_kw, missing_calls = _append_execution_event_calls_pass_fact_event_seq()
    if not calls_pass_kw:
        detail = (
            "; no call sites found"
            if not missing_calls
            else "; missing fact_event_seq= kwarg at: " + ", ".join(missing_calls)
        )
        offender_blocks.append(
            f"{service_rel}:TaskRuntimeService._append_execution_event must call "
            f"build_task_runtime_execution_event_append_result with "
            f"fact_event_seq=<append-only sequence projection>" + detail + "."
        )

    assert not offender_blocks, (
        "WS2 append-only sequence evidence fence: "
        "TaskRuntimeService._append_execution_event must propagate "
        "appended.appended_seq from the Fact Stream append-result into the "
        "execution-event append-result/projection so the task_runtime.execution "
        "stream stays append-only reconstructable. Offenders:\n" + "\n".join(offender_blocks)
    )


def test_task_runtime_append_event_uses_fact_stream_expected_seq_cas() -> None:
    """WS2 CAS fence for TaskRuntime execution-event append operations."""

    service_rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    uses_expected_seq, offenders = _append_execution_fact_with_cas_uses_expected_seq_contract()

    assert uses_expected_seq, (
        "WS2 execution-ledger SSoT fence: TaskRuntimeService execution facts "
        "must opt into the FactStream expected_seq CAS contract before append. "
        "Without this, concurrent TaskRuntime writers can continue appending "
        "through the non-CAS path and the execution ledger remains a best-effort "
        f"projection instead of an append-only control-plane fact source. {service_rel} offenders:\n"
        + "\n".join(offenders)
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


# ---------------------------------------------------------------------------
# WS2 Fact Stream sequence evidence — read-model projection
# ---------------------------------------------------------------------------
#
# The append path (``TaskRuntimeService._append_execution_event``) already
# surfaces ``appended.appended_seq`` as ``fact_event_seq`` in
# ``build_task_runtime_execution_event_append_result``. The read path must
# carry that same fact-stream sequence number back into the projected task row
# so observers can correlate a row back to its ``task_runtime.execution``
# evidence line.
#
# These fences catch future drift where the read path drops the
# ``fact_event_seq`` projection — even though the writer still emits it — or
# where someone reintroduces a parallel ``.seq`` cursor / file read into the
# read model.


def _task_runtime_service_method_def(method_name: str) -> ast.FunctionDef:
    """Return a ``TaskRuntimeService`` method AST node by name."""

    source = TASK_RUNTIME_INTERNAL_SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parent_lookup(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != method_name:
            continue
        enclosing_class = parents.get(node)
        if isinstance(enclosing_class, ast.ClassDef) and enclosing_class.name == "TaskRuntimeService":
            return node
    raise AssertionError(
        f"TaskRuntimeService.{method_name}() not found in {TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)}"
    )


def _list_task_rows_from_execution_facts_function_def() -> ast.FunctionDef:
    """Return the ``TaskRuntimeService.list_task_rows_from_execution_facts`` AST node."""

    return _task_runtime_service_method_def(EXECUTION_FACT_LIST_READER)


def _project_task_row_from_execution_fact_payload_function_def() -> ast.FunctionDef:
    """Return the module-level ``project_task_row_from_execution_fact_payload`` AST node."""

    source = EXECUTION_SESSION_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == EXECUTION_FACT_READ_PROJECTOR:
            return node
    raise AssertionError(
        f"{EXECUTION_FACT_READ_PROJECTOR}() not found in {EXECUTION_SESSION_MODULE.relative_to(BACKEND_ROOT)}"
    )


def _iter_events_loop_in(function_def: ast.FunctionDef) -> ast.For | ast.AsyncFor | None:
    """Locate the ``for event in result.events:`` style loop in the read method."""

    for node in ast.walk(function_def):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        # The loop must iterate over ``result.events`` (the queried Fact Stream
        # events tuple). We match by attribute chain so a renamed local
        # variable does not break the fence.
        if _attribute_chain_matches(node.iter, ("events", "result")):
            return node
    return None


def _fact_dict_assignments_in_scope(scope: ast.AST) -> list[ast.AST]:
    """Return AST nodes inside ``scope`` that assign into a ``fact`` dict.

    Catches both ``fact["fact_event_seq"] = ...`` (ast.Assign on a Subscript)
    and ``fact.setdefault("fact_event_seq", ...)`` (ast.Call on a method).
    """

    result: list[ast.AST] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if _fact_subscript_key(target) == EXECUTION_FACT_SEQ_KEY:
                    result.append(node)
        elif isinstance(node, ast.AugAssign):
            if _fact_subscript_key(node.target) == EXECUTION_FACT_SEQ_KEY:
                result.append(node)
        elif isinstance(node, ast.Call):
            if _call_name(node.func) != "setdefault":
                continue
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
            if receiver is None or _call_name(receiver) != "fact":
                continue
            first_arg = node.args[0] if node.args else None
            if _string_literal(first_arg) == EXECUTION_FACT_SEQ_KEY:
                result.append(node)
    return result


def _fact_dict_assignments_in_loop(loop: ast.For | ast.AsyncFor) -> list[ast.AST]:
    """Return AST nodes inside ``loop`` that assign into a ``fact`` dict."""

    return _fact_dict_assignments_in_scope(loop)


def _fact_subscript_key(node: ast.AST) -> str:
    """If ``node`` is ``fact["<key>"]`` (or equivalent chain), return ``<key>``."""

    if not isinstance(node, ast.Subscript):
        return ""
    slice_value = node.slice
    if isinstance(slice_value, ast.Index):  # pragma: no cover - py<3.9 compat
        slice_value = slice_value.value  # type: ignore[attr-defined]
    return _string_literal(slice_value)


def _call_references_mapping_key(call_node: ast.Call, *, receiver_name: str, key_name: str) -> bool:
    """True if ``call_node`` reads ``<receiver>.get("<key>")``."""

    return (
        _attribute_chain_matches(call_node.func, ("get", receiver_name))
        and bool(call_node.args)
        and _string_literal(call_node.args[0]) == key_name
    )


def _call_references_event_seq(call_node: ast.Call) -> bool:
    """True if ``call_node`` reads the queried event's ``seq`` field."""

    return _call_references_mapping_key(
        call_node,
        receiver_name="event",
        key_name=EXECUTION_FACT_SEQ_SOURCE_KEY,
    )


def _subscript_references_mapping_key(node: ast.AST, *, receiver_name: str, key_name: str) -> bool:
    """True if ``node`` is ``<receiver>["<key>"]``."""

    if not isinstance(node, ast.Subscript):
        return False
    if _string_literal(node.slice) != key_name:
        return False
    receiver = node.value
    if isinstance(receiver, ast.Name) and receiver.id == "event":
        return receiver_name == "event"
    return isinstance(receiver, ast.Name) and receiver.id == receiver_name


def _node_sources_event_seq(node: ast.AST) -> bool:
    """True if ``node`` reads ``event.get("seq")`` or ``event["seq"]``."""

    if isinstance(node, ast.Call):
        if _call_references_event_seq(node):
            return True
        return any(_node_sources_event_seq(child) for child in ast.iter_child_nodes(node))
    return _subscript_references_mapping_key(
        node,
        receiver_name="event",
        key_name=EXECUTION_FACT_SEQ_SOURCE_KEY,
    )


def _node_sources_fact_event_seq(node: ast.AST) -> bool:
    """True if ``node`` reads ``fact.get("fact_event_seq")`` or ``fact["fact_event_seq"]``."""

    if isinstance(node, ast.Call):
        if _call_references_mapping_key(
            node,
            receiver_name="fact",
            key_name=EXECUTION_FACT_SEQ_KEY,
        ):
            return True
        return any(_node_sources_fact_event_seq(child) for child in ast.iter_child_nodes(node))
    return _subscript_references_mapping_key(
        node,
        receiver_name="fact",
        key_name=EXECUTION_FACT_SEQ_KEY,
    )


def _list_reader_propagates_fact_event_seq() -> tuple[bool, list[str]]:
    """Check that ``list_task_rows_from_execution_facts`` carries event seq into the fact payload.

    The check is structural rather than line-exact:
      1. Prefer the shared ``_project_execution_fact_event_row`` helper when
         the list reader delegates per-event projection.
      2. In the helper (or the list reader body for legacy inline projection),
         there must be an assignment of ``fact["fact_event_seq"]`` whose RHS
         reads the queried event's ``seq`` field.
      3. The call to ``project_task_row_from_execution_fact_payload(fact)`` must
         appear AFTER the ``fact_event_seq`` write so the projector can see the
         propagated value.
    """

    list_function_def = _list_task_rows_from_execution_facts_function_def()
    projection_scope: ast.AST = list_function_def
    projection_label = "list_task_rows_from_execution_facts"
    helper_name = "_project_execution_fact_event_row"
    if any(
        isinstance(node, ast.Call) and _call_name(node.func).rsplit(".", maxsplit=1)[-1] == helper_name
        for node in ast.walk(list_function_def)
    ):
        projection_scope = _task_runtime_service_method_def(helper_name)
        projection_label = helper_name
    else:
        loop = _iter_events_loop_in(list_function_def)
        if loop is None:
            return False, ["no `for event in result.events` loop found and no shared event projector helper used"]
        projection_scope = loop

    seq_assignments = _fact_dict_assignments_in_scope(projection_scope)
    if not seq_assignments:
        return False, [
            f"{projection_label} does not propagate fact_event_seq into the fact payload before projecting rows"
        ]

    projector_call: ast.Call | None = None
    for node in ast.walk(projection_scope):
        if isinstance(node, ast.Call) and _call_name(node.func) == EXECUTION_FACT_READ_PROJECTOR:
            projector_call = node
            break

    if projector_call is None:
        return False, [f"{projection_label} does not call {EXECUTION_FACT_READ_PROJECTOR}() while projecting fact rows"]

    event_seq_names: set[str] = set()
    for node in ast.walk(projection_scope):
        if not isinstance(node, ast.Assign):
            continue
        if not _node_sources_event_seq(node.value):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                event_seq_names.add(target.id)

    missing_source: list[str] = []
    order_violations: list[str] = []
    for assignment in seq_assignments:
        if not hasattr(assignment, "lineno") or not hasattr(assignment, "value"):
            continue
        rhs = assignment.value
        rhs_sources_event_seq = _node_sources_event_seq(rhs) or (
            isinstance(rhs, ast.Name) and rhs.id in event_seq_names
        )
        if not rhs_sources_event_seq:
            missing_source.append(
                f"line {assignment.lineno}: fact_event_seq source must read event.get('seq') or event['seq']"
            )
        elif assignment.lineno > projector_call.lineno:
            order_violations.append(
                f"line {assignment.lineno}: fact_event_seq assignment must precede "
                f"{EXECUTION_FACT_READ_PROJECTOR}() call at line {projector_call.lineno}"
            )

    offenders: list[str] = []
    if missing_source:
        offenders.extend(missing_source)
    if order_violations:
        offenders.extend(order_violations)
    return not offenders, offenders


def _list_reader_queries_latest_fact_window() -> tuple[bool, list[str]]:
    """Check fact-derived task rows read the latest event window, not the first page."""

    function_def = _list_task_rows_from_execution_facts_function_def()
    offenders: list[str] = []

    has_total_window_guard = False
    has_latest_offset_assignment = False
    has_offset_query = False

    for node in ast.walk(function_def):
        if isinstance(node, ast.Compare):
            expression = ast.unparse(node)
            if "result.total" in expression and "len(result.events)" in expression:
                has_total_window_guard = True
        elif isinstance(node, ast.Assign):
            target_names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if "latest_offset" in target_names:
                expression = ast.unparse(node.value)
                if "result.total" in expression and "event_limit" in expression:
                    has_latest_offset_assignment = True
        elif isinstance(node, ast.Call) and _call_name(node.func) == "QueryFactEventsV1":
            for keyword in node.keywords:
                if keyword.arg != "offset":
                    continue
                if isinstance(keyword.value, ast.Name) and keyword.value.id == "latest_offset":
                    has_offset_query = True

    if not has_total_window_guard:
        offenders.append("missing guard comparing result.total with len(result.events)")
    if not has_latest_offset_assignment:
        offenders.append("missing latest_offset assignment from result.total - event_limit")
    if not has_offset_query:
        offenders.append("missing second QueryFactEventsV1(..., offset=latest_offset) query")

    return not offenders, offenders


def _function_reads_dot_seq_files(function_def: ast.FunctionDef) -> list[str]:
    """Detect forbidden direct ``.seq`` cursor / file reads in a function body."""

    offenders: list[str] = []
    for node in ast.walk(function_def):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(pattern in node.value for pattern in EXECUTION_FACT_DOT_SEQ_FILE_PATTERNS):
                offenders.append(f"line {node.lineno}: literal {node.value!r} references .seq cursor/file")
        elif isinstance(node, ast.JoinedStr):
            literal_text = "".join(
                part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if any(pattern in literal_text for pattern in EXECUTION_FACT_DOT_SEQ_FILE_PATTERNS):
                offenders.append(f"line {node.lineno}: f-string literal references .seq cursor/file")
        elif isinstance(node, ast.Attribute) and node.attr.endswith(".seq"):
            # Allow attribute access whose target is the Fact Stream ``event``
            # envelope (``event.seq``), but flag explicit cursor handles such
            # as ``stream.seq``, ``path.seq``, ``file.seq`` etc.
            target = node.value
            if isinstance(target, ast.Name) and target.id == "event":
                continue
            offenders.append(f"line {node.lineno}: attribute access .{node.attr} may bypass fact stream seq")
    return offenders


def _projector_projects_fact_event_seq_via_normalize_positive_int() -> tuple[bool, list[str]]:
    """Check ``project_task_row_from_execution_fact_payload`` projects ``fact_event_seq`` through ``normalize_positive_int``.

    The fence accepts ``normalize_positive_int`` or ``_coerce_fact_event_seq``
    as the canonical positive-int helpers. Direct ``int(...)`` coercion is
    rejected because it can silently fabricate a positive seq from a missing
    field.
    """

    function_def = _project_task_row_from_execution_fact_payload_function_def()
    allowed_helpers = {"normalize_positive_int", "_coerce_fact_event_seq"}

    offenders: list[str] = []
    found_projection = False
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Call):
            continue
        callee = _call_name(node.func)
        if callee not in allowed_helpers:
            continue
        if not any(
            _node_sources_fact_event_seq(arg) for arg in (*node.args, *(keyword.value for keyword in node.keywords))
        ):
            continue
        found_projection = True

        if callee == "normalize_positive_int":
            # normalize_positive_int must be invoked with a non-None default
            # so a missing fact_event_seq still produces a deterministic int
            # (typically 0). Inspect kwargs for default= / minimum=.
            keyword_args = {keyword.arg for keyword in node.keywords}
            if "default" not in keyword_args:
                offenders.append(
                    f"line {node.lineno}: normalize_positive_int(fact_event_seq=...) must pass default= explicitly"
                )
        elif callee == "_coerce_fact_event_seq":
            # _coerce_fact_event_seq returns Optional[int] and never silently
            # fabricates a seq. Acceptable as-is.
            pass

    if not found_projection:
        offenders.append(
            "project_task_row_from_execution_fact_payload does not project fact_event_seq "
            "through normalize_positive_int or _coerce_fact_event_seq"
        )

    return not offenders, offenders


def _projector_assigns_fact_event_seq_to_row() -> tuple[bool, list[str]]:
    """Check the projector places ``fact_event_seq`` on the returned row.

    Accepts both ``row["fact_event_seq"] = ...`` style and inclusion in the
    ``row.update({...})`` dict literal at the bottom of the projector.
    """

    function_def = _project_task_row_from_execution_fact_payload_function_def()
    offenders: list[str] = []

    direct_assignment = False
    for node in ast.walk(function_def):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if _fact_subscript_key(target) == EXECUTION_FACT_SEQ_KEY:
                    direct_assignment = True
        elif isinstance(node, ast.AugAssign):
            if _fact_subscript_key(node.target) == EXECUTION_FACT_SEQ_KEY:
                direct_assignment = True
        elif isinstance(node, ast.Call) and _call_name(node.func) == "update":
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
            if receiver is None or _call_name(receiver) != "row":
                continue
            if not node.args:
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Dict) and any(
                isinstance(key, ast.Constant) and key.value == EXECUTION_FACT_SEQ_KEY
                for key in first_arg.keys
                if key is not None
            ):
                direct_assignment = True

    if not direct_assignment:
        offenders.append(
            "project_task_row_from_execution_fact_payload must place fact_event_seq on the "
            "returned row via direct subscript assignment or row.update({...})"
        )

    return not offenders, offenders


def test_list_task_rows_from_execution_facts_carries_event_seq_into_fact_payload() -> None:
    """WS2 read-model Fact Stream sequence propagation fence.

    ``TaskRuntimeService.list_task_rows_from_execution_facts`` is the read-side
    consumer of the ``task_runtime.execution`` Fact Stream. For every queried
    event, the read projection must carry the event's ``seq`` number into the
    fact payload as ``fact_event_seq`` *before* invoking
    ``project_task_row_from_execution_fact_payload``. Otherwise the projector
    can never emit the read-model ``fact_event_seq`` and observers cannot
    correlate projected rows back to their evidence line in
    ``task_runtime.execution.jsonl``.
    """

    service_rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    passes, offenders = _list_reader_propagates_fact_event_seq()
    seq_cursor_offenders = _function_reads_dot_seq_files(_list_task_rows_from_execution_facts_function_def())

    assert passes and not seq_cursor_offenders, (
        "WS2 read-model sequence evidence fence: "
        f"{service_rel}:TaskRuntimeService.list_task_rows_from_execution_facts "
        "must propagate the queried event's seq into the fact payload as "
        "fact_event_seq (sourced from event.get('seq') or event['seq']) before "
        "calling project_task_row_from_execution_fact_payload, and must not "
        "bypass the Fact Stream via .seq cursor/file reads. Offenders:\n" + "\n".join(offenders + seq_cursor_offenders)
    )


def test_list_task_rows_from_execution_facts_queries_latest_fact_window() -> None:
    """WS2 read-model pagination fence.

    ``JsonlEventStore.query`` returns the requested offset/limit window in
    append order. TaskRuntime's live read model must therefore re-query the
    tail window when the stream contains more events than the requested limit;
    otherwise status projection regresses to the oldest facts in long runs.
    """

    service_rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    passes, offenders = _list_reader_queries_latest_fact_window()

    assert passes, (
        "WS2 read-model pagination fence: "
        f"{service_rel}:TaskRuntimeService.list_task_rows_from_execution_facts "
        "must use Fact Stream total/offset pagination to query the latest "
        "event window instead of projecting the earliest page. Offenders:\n" + "\n".join(offenders)
    )


def test_project_task_row_from_execution_fact_payload_normalizes_fact_event_seq() -> None:
    """WS2 read-model projector positive-int normalization fence.

    ``project_task_row_from_execution_fact_payload`` must project the
    ``fact_event_seq`` field of the propagated fact payload into the returned
    row using the canonical positive-int helper (``normalize_positive_int`` or
    ``_coerce_fact_event_seq``). Direct ``int(...)`` coercion or bare
    passthrough must not be used, since missing or invalid input would
    silently fabricate a seq number. The function must also avoid parallel
    ``.seq`` cursor/file reads so the Fact Stream stays the single source of
    truth for sequence evidence.
    """

    session_rel = EXECUTION_SESSION_MODULE.relative_to(BACKEND_ROOT).as_posix()
    passes, projection_offenders = _projector_projects_fact_event_seq_via_normalize_positive_int()
    placement_passes, placement_offenders = _projector_assigns_fact_event_seq_to_row()
    cursor_offenders = _function_reads_dot_seq_files(_project_task_row_from_execution_fact_payload_function_def())

    all_offenders = projection_offenders + placement_offenders + cursor_offenders
    assert passes and placement_passes and not cursor_offenders, (
        "WS2 read-model projector sequence fence: "
        f"{session_rel}:{EXECUTION_FACT_READ_PROJECTOR} must project "
        f"{EXECUTION_FACT_SEQ_KEY!r} into the returned row through "
        "normalize_positive_int or _coerce_fact_event_seq, must place the "
        "value on the returned row, and must not bypass the Fact Stream via "
        ".seq cursor/file reads. Offenders:\n" + "\n".join(all_offenders)
    )


# ---------------------------------------------------------------------------
# WS2 selection/readiness — observable rows only
# ---------------------------------------------------------------------------
#
# TaskRuntimeService selection and ready-readiness paths must read through the
# observable row model (``list_observable_task_rows``) so the
# ``task_runtime.execution`` Fact Stream overlay remains the read-model SSoT.
# Direct calls to the raw file-backed ``list_task_rows`` API in those methods
# would silently drop late status reconciliation evidence for in-flight rows,
# regressing Director fanout and worker wakeup back to the pre-WS2 raw read.
#
# This fence is intentionally scoped to the three selection/readiness methods:
#   * ``select_next_task`` — preview selection
#   * ``claim_next_execution`` — atomic select-and-claim
#   * ``list_ready_task_rows`` — readiness projection
#
# It does NOT ban ``list_task_rows`` globally. Mutation/write APIs and
# ``list_observable_task_rows`` itself legitimately read through the raw API.

TASK_RUNTIME_SERVICE_SELECTION_READINESS_METHODS = (
    "select_next_task",
    "claim_next_execution",
    "list_ready_task_rows",
)


def _selection_readiness_method_function_def(name: str) -> ast.FunctionDef:
    """Return the AST node for ``TaskRuntimeService.<name>()`` if it is a sync method.

    Skips non-method top-level ``def`` entries and ignores methods that live on
    other classes; selectors and readiness helpers above only exist on the
    ``TaskRuntimeService`` class.
    """

    source = TASK_RUNTIME_INTERNAL_SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parent_lookup(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != name:
            continue
        enclosing = parents.get(node)
        if isinstance(enclosing, ast.ClassDef) and enclosing.name == "TaskRuntimeService":
            return node
    raise AssertionError(
        f"TaskRuntimeService.{name}() not found in {TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)}"
    )


def _call_is_self_method(node: ast.Call, method_name: str) -> bool:
    """True if ``node`` is ``self.<method_name>(...)`` regardless of keyword args."""

    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != method_name:
        return False
    receiver = func.value
    return isinstance(receiver, ast.Name) and receiver.id == "self"


def _method_body_calls_self_method(method_def: ast.FunctionDef, method_name: str) -> bool:
    """True if ``method_def`` contains at least one ``self.<method_name>(...)`` call."""

    return any(isinstance(node, ast.Call) and _call_is_self_method(node, method_name) for node in ast.walk(method_def))


def _collect_direct_list_task_rows_in_method(method_def: ast.FunctionDef) -> list[ast.Call]:
    """Return ``self.list_task_rows(...)`` call nodes inside ``method_def``.

    Only direct ``self.list_task_rows(...)`` calls are flagged. Indirect calls
    through locals/aliases or sub-helper methods are not targeted by this fence,
    and the legitimate callers (``list_observable_task_rows`` itself and the
    raw write/mutation API) live outside these selection/readiness methods.
    """

    return [
        node
        for node in ast.walk(method_def)
        if isinstance(node, ast.Call) and _call_is_self_method(node, "list_task_rows")
    ]


def _check_selection_readiness_uses_observable_rows() -> list[str]:
    """Walk the three selection/readiness methods and emit fence offenders.

    Each method must call ``self.list_observable_task_rows(...)`` at least
    once. Direct ``self.list_task_rows(...)`` calls are forbidden inside
    selection/readiness methods because they bypass the execution-fact overlay
    that keeps status projection authoritative for in-flight rows.
    """

    offenders: list[str] = []
    for method_name in TASK_RUNTIME_SERVICE_SELECTION_READINESS_METHODS:
        try:
            method_def = _selection_readiness_method_function_def(method_name)
        except AssertionError as exc:  # pragma: no cover - structural guard
            offenders.append(str(exc))
            continue

        if not _method_body_calls_self_method(method_def, "list_observable_task_rows"):
            offenders.append(
                f"{TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)}:"
                f"TaskRuntimeService.{method_name}() does not call "
                "self.list_observable_task_rows() to project task-runtime status "
                "through the task_runtime.execution Fact Stream overlay."
            )

        for bad_call in _collect_direct_list_task_rows_in_method(method_def):
            offenders.append(
                f"{TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)}:"
                f"TaskRuntimeService.{method_name}():{bad_call.lineno} calls "
                "self.list_task_rows() directly; selection/readiness paths must "
                "read through self.list_observable_task_rows() so execution "
                "facts remain part of the read-model projection."
            )
    return offenders


def test_selection_and_readiness_methods_use_observable_rows_not_raw_list() -> None:
    """WS2 selection/readiness fence.

    ``TaskRuntimeService.select_next_task()``, ``claim_next_execution()``, and
    ``list_ready_task_rows()`` are the public read-side entry points that
    Director fanout and worker wakeup consume to choose the next row. They
    must read through ``self.list_observable_task_rows(...)`` so late
    ``task_runtime.execution`` fact evidence (claim_renewed, completed, failed,
    QA rework, role-adapter failure) is part of the projection SSoT.

    The fence is structural: it walks the AST of each method and checks:

    1. The method calls ``self.list_observable_task_rows(...)`` at least once.
    2. The method does NOT call ``self.list_task_rows(...)`` directly.

    ``list_task_rows`` remains the legitimate primitive for
    ``list_observable_task_rows`` itself and for write/mutation/refresh paths;
    the fence only bans its direct appearance inside these three selection/
    readiness methods. Existing ``_task_runtime_service_raw_board_*`` fences
    and assertion-based invariants continue to apply.
    """

    offenders = _check_selection_readiness_uses_observable_rows()

    assert not offenders, (
        "WS2 selection/readiness fence: "
        "TaskRuntimeService.select_next_task(), claim_next_execution(), and "
        "list_ready_task_rows() must consume the observable row projection "
        "(self.list_observable_task_rows(...)) instead of the raw file-backed "
        "self.list_task_rows(...), so the task_runtime.execution Fact Stream "
        "overlay stays part of the read-model SSoT for selection and "
        "readiness. Direct list_task_rows() calls inside write/mutation paths "
        "and inside list_observable_task_rows() itself remain allowed. "
        "Offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# WS2 external task-id lookup — observable read-model projection
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService._get_task_by_external_task_id()`` is the private
# materialization lookup used by ``ensure_task_row()`` to deduplicate PM /
# orchestration contracts by external id. It used to walk raw ``TaskBoard``
# entities through ``self._board.list_all()``; this fence closes that residual
# by making the helper read the same observable row model as public snapshot /
# UI consumers.
#
# The check is intentionally narrow and mechanical:
#
#   1. The helper must call ``self.list_observable_task_rows()``.
#   2. The helper must not call raw board reads or the raw file-backed row list.
#
# ``_list_file_task_rows()`` remains a legitimate primitive inside
# ``list_observable_task_rows()``. This fence only protects the external-id
# lookup helper from bypassing the execution-fact overlay.

EXTERNAL_TASK_ID_LOOKUP_HELPER = "_get_task_by_external_task_id"
EXTERNAL_TASK_ID_LOOKUP_FORBIDDEN_RAW_READ_TARGETS: dict[str, str] = {
    "self._board.list_all": "raw TaskBoard.list_all() bypasses the execution-fact overlay",
    "self._board.get": "raw TaskBoard.get() bypasses the execution-fact overlay",
    "self.list_task_rows": "raw list_task_rows() regresses to file-backed status only",
}


def _external_task_id_lookup_function_def() -> ast.FunctionDef:
    """Return the ``TaskRuntimeService._get_task_by_external_task_id`` AST node."""

    return _task_runtime_service_method_def(EXTERNAL_TASK_ID_LOOKUP_HELPER)


def _check_external_task_id_lookup_calls_list_observable_task_rows() -> list[str]:
    """Emit offenders if external-id lookup does not read observable rows."""

    method_def = _external_task_id_lookup_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()

    if _method_body_calls_self_method(method_def, "list_observable_task_rows"):
        return []

    return [
        f"{rel}:TaskRuntimeService.{EXTERNAL_TASK_ID_LOOKUP_HELPER}() does not "
        "call self.list_observable_task_rows(); external-id materialization "
        "lookup must scan the observable row projection so the "
        "task_runtime.execution Fact Stream overlay remains part of the "
        "read-model SSoT."
    ]


def _check_external_task_id_lookup_forbidden_raw_reads() -> list[str]:
    """Emit offenders if external-id lookup regresses to raw row reads."""

    method_def = _external_task_id_lookup_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []

    for call_node in ast.walk(method_def):
        if not isinstance(call_node, ast.Call):
            continue
        callee = _call_name(call_node.func)
        reason = EXTERNAL_TASK_ID_LOOKUP_FORBIDDEN_RAW_READ_TARGETS.get(callee)
        if reason is None:
            continue
        offenders.append(
            f"{rel}:TaskRuntimeService.{EXTERNAL_TASK_ID_LOOKUP_HELPER}():"
            f"{call_node.lineno} calls {callee!r}; {reason}. External-id "
            "materialization lookup must read through "
            "self.list_observable_task_rows() so execution facts remain part "
            "of the row projection."
        )

    return offenders


def test_external_task_id_lookup_reads_through_observable_rows() -> None:
    """WS2 external-id lookup fence (positive invariant).

    ``TaskRuntimeService._get_task_by_external_task_id()`` deduplicates
    materialized external contracts before ``ensure_task_row()`` creates a
    new canonical row. The lookup must call ``self.list_observable_task_rows()``
    rather than walking raw ``TaskBoard`` entities, so late
    ``task_runtime.execution`` facts and in-flight overlays stay visible to
    deduplication.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_external_task_id_lookup_calls_list_observable_task_rows()

    assert not offenders, (
        "WS2 external-id lookup fence: "
        f"{rel}:TaskRuntimeService.{EXTERNAL_TASK_ID_LOOKUP_HELPER}() must "
        "call self.list_observable_task_rows() so external-id deduplication "
        "uses the observable row projection. Offenders:\n" + "\n".join(offenders)
    )


def test_external_task_id_lookup_does_not_regress_to_raw_row_reads() -> None:
    """WS2 external-id lookup fence (negative invariant).

    ``TaskRuntimeService._get_task_by_external_task_id()`` must not call
    ``self._board.list_all()``, ``self._board.get()``, or
    ``self.list_task_rows()``. Those reads bypass the observable read model and
    would reintroduce the raw ``TaskBoard`` residual that this fence retires.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_external_task_id_lookup_forbidden_raw_reads()

    assert not offenders, (
        "WS2 external-id lookup fence: "
        f"{rel}:TaskRuntimeService.{EXTERNAL_TASK_ID_LOOKUP_HELPER}() must not "
        "read raw TaskBoard rows or raw file-backed task rows. It must route "
        "through self.list_observable_task_rows() so the task_runtime.execution "
        "Fact Stream overlay stays part of the read-model SSoT. Offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# WS2 dependency refresh — fact-aware dependency-status projection
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService.refresh_dependency_unblocks()`` decides whether a
# BLOCKED row's dependencies are now resolvable. The decision depends on the
# authoritative completion status of every dependency. If that status is
# derived from the raw file-backed ``self._board.list_all()`` snapshot, late
# ``task_runtime.execution`` completion facts (and any in-flight execution
# overlay) are silently dropped — the very symptom WS2 is meant to eliminate.
#
# The function may still call ``self._list_file_task_entities()`` to walk the
# persisted ``Task`` objects for the mutation path, but the dependency-status
# source (``status_by_id`` / equivalent mapping) must come from a fact-aware
# helper such as ``_fact_overlaid_dependency_status_rows``,
# ``_list_dependency_status_rows``, or ``list_task_rows_from_execution_facts``.
#
# This fence is structural: it locates the mapping assignment by target name
# pattern (not line number) and checks that its RHS calls a fact-aware helper
# rather than deriving the mapping purely from the raw ``list_all()`` payload.

REFRESH_DEPENDENCY_UNBLOCKS_FACT_AWARE_HELPERS: frozenset[str] = frozenset(
    {
        "_fact_overlaid_dependency_status_rows",
        "_list_dependency_status_rows",
        "list_task_rows_from_execution_facts",
    }
)
REFRESH_DEPENDENCY_UNBLOCKS_STATUS_TARGET_TOKENS: frozenset[str] = frozenset(
    {
        "status_by_id",
        "dependency_status",
        "dependency_status_by_id",
        "fact_status_by_id",
        "overlay_status_by_id",
    }
)


def _refresh_dependency_unblocks_function_def() -> ast.FunctionDef:
    """Return the ``TaskRuntimeService.refresh_dependency_unblocks`` AST node."""

    source = TASK_RUNTIME_INTERNAL_SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parent_lookup(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "refresh_dependency_unblocks":
            continue
        enclosing = parents.get(node)
        if isinstance(enclosing, ast.ClassDef) and enclosing.name == "TaskRuntimeService":
            return node
    raise AssertionError(
        "TaskRuntimeService.refresh_dependency_unblocks() not found in "
        f"{TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)}"
    )


def _assignment_targets_token(node: ast.Assign, token: str) -> bool:
    """True if any target of ``node`` is a plain ``Name`` matching ``token``."""

    return any(isinstance(target, ast.Name) and target.id == token for target in node.targets)


def _collect_refresh_dependency_unblocks_status_assignments(
    method_def: ast.FunctionDef,
) -> list[ast.Assign]:
    """Return ``ast.Assign`` nodes whose plain-Name target is a dependency-status mapping."""

    matches: list[ast.Assign] = []
    for node in ast.walk(method_def):
        if not isinstance(node, ast.Assign):
            continue
        for token in REFRESH_DEPENDENCY_UNBLOCKS_STATUS_TARGET_TOKENS:
            if _assignment_targets_token(node, token):
                matches.append(node)
                break
    return matches


def _call_invokes_fact_aware_dependency_helper(node: ast.AST) -> bool:
    """True if ``node`` (or any child call) invokes a fact-aware helper by name."""

    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        callee = _call_name(child.func)
        if not callee:
            continue
        # Match the trailing segment so calls like
        # ``self._fact_overlaid_dependency_status_rows()`` qualify alongside
        # the bare module-level import path.
        leaf = callee.rsplit(".", maxsplit=1)[-1]
        if leaf in REFRESH_DEPENDENCY_UNBLOCKS_FACT_AWARE_HELPERS:
            return True
    return False


def _expression_derives_mapping_from_raw_list_all(
    expr: ast.AST,
    raw_list_all_target: str | None,
) -> bool:
    """True if ``expr`` is a dict-comprehension sourced from the ``list_all()`` result.

    Catches the pre-WS2 regression pattern::

        tasks = self._board.list_all()
        status_by_id = {int(task.id): task.status for task in tasks}

    by binding the variable on the LHS of the ``list_all()`` assignment and
    detecting a dict-comprehension / generator expression that iterates over
    that variable. We only flag a mapping whose values come from the raw
    ``Task`` row, not from a fact-aware helper. The separate raw list-all
    centralization fence rejects new direct callers before this fallback
    diagnostic becomes necessary.
    """

    if raw_list_all_target is None:
        return False

    if isinstance(expr, ast.DictComp):
        return _comprehension_iterates(expr, raw_list_all_target)
    if isinstance(expr, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
        return _comprehension_iterates(expr, raw_list_all_target)
    if isinstance(expr, ast.Call):
        callee = _call_name(expr.func)
        if callee.rsplit(".", maxsplit=1)[-1] in {"dict", "dict.fromkeys"}:
            for arg in expr.args:
                if _expression_derives_mapping_from_raw_list_all(arg, raw_list_all_target):
                    return True
    return False


def _find_list_all_target_name(method_def: ast.FunctionDef, list_all_call: ast.Call) -> str | None:
    """Find the plain-Name target of the assignment that produced ``list_all_call``."""

    for node in ast.walk(method_def):
        if not isinstance(node, ast.Assign):
            continue
        if node.value is list_all_call:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    return target.id
    return None


def _comprehension_iterates(node: ast.DictComp | ast.ListComp | ast.SetComp | ast.GeneratorExp, name: str) -> bool:
    """True if any comprehension clause iterates over the named variable."""

    return any(isinstance(generator.iter, ast.Name) and generator.iter.id == name for generator in node.generators)


def _check_refresh_dependency_unblocks_uses_fact_aware_status_source() -> list[str]:
    """Walk ``refresh_dependency_unblocks`` and emit fence offenders.

    For each mapping assignment whose target matches
    ``REFRESH_DEPENDENCY_UNBLOCKS_STATUS_TARGET_TOKENS`` the RHS must invoke a
    fact-aware helper. If the RHS instead derives the mapping from a raw
    ``self._board.list_all()`` snapshot (dict-comprehension over the
    ``list_all()`` result, or ``dict(...)`` wrapping the same), the function
    regresses to the pre-WS2 raw-only dependency view and the assignment is
    flagged.
    """

    method_def = _refresh_dependency_unblocks_function_def()
    offenders: list[str] = []
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()

    raw_list_all_call: ast.Call | None = None
    for node in ast.walk(method_def):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node.func) != "self._board.list_all":
            continue
        if raw_list_all_call is None:
            raw_list_all_call = node

    raw_list_all_target: str | None = None
    if raw_list_all_call is not None:
        raw_list_all_target = _find_list_all_target_name(method_def, raw_list_all_call)

    for assignment in _collect_refresh_dependency_unblocks_status_assignments(method_def):
        if _call_invokes_fact_aware_dependency_helper(assignment.value):
            continue
        if raw_list_all_call is not None and _expression_derives_mapping_from_raw_list_all(
            assignment.value, raw_list_all_target
        ):
            target_name = next(
                target.id
                for target in assignment.targets
                if isinstance(target, ast.Name) and target.id in REFRESH_DEPENDENCY_UNBLOCKS_STATUS_TARGET_TOKENS
            )
            offenders.append(
                f"{rel}:TaskRuntimeService.refresh_dependency_unblocks():"
                f"{assignment.lineno} derives {target_name!r} from raw "
                "self._board.list_all() instead of a fact-aware dependency "
                "status helper. Use one of "
                + ", ".join(sorted(REFRESH_DEPENDENCY_UNBLOCKS_FACT_AWARE_HELPERS))
                + " so latest task_runtime.execution completion facts stay "
                "authoritative for dependency unblock decisions."
            )
            continue
        # If neither path matches, the assignment uses some other fact-aware
        # construct we did not explicitly enumerate. Surface a softer reminder
        # so the change author can document it in the fence allowlist.
        target_name = next(
            target.id
            for target in assignment.targets
            if isinstance(target, ast.Name) and target.id in REFRESH_DEPENDENCY_UNBLOCKS_STATUS_TARGET_TOKENS
        )
        offenders.append(
            f"{rel}:TaskRuntimeService.refresh_dependency_unblocks():"
            f"{assignment.lineno} builds {target_name!r} without a recognized "
            "fact-aware dependency status helper. New fact-aware helpers must "
            "be added to REFRESH_DEPENDENCY_UNBLOCKS_FACT_AWARE_HELPERS so the "
            "WS2 dependency-status projection stays the read-model SSoT."
        )

    return offenders


def test_refresh_dependency_unblocks_uses_fact_aware_dependency_status_projection() -> None:
    """WS2 dependency refresh fence.

    ``TaskRuntimeService.refresh_dependency_unblocks()`` is the side-effect
    path that wakes BLOCKED rows whose dependencies are now complete. The
    dependency-status source must come from a fact-aware helper so late
    ``task_runtime.execution`` completion facts (and any in-flight execution
    overlay) unblock downstream rows even when file-backed rows are stale.

    The function may still iterate ``self._list_file_task_entities()`` to
    walk persisted ``Task`` objects for the mutation path; the fence forbids
    using the raw ``list_all()`` snapshot as the *sole* source of dependency
    status. A targeted structural check walks the mapping assignment by
    target name (not by line number) and verifies the RHS invokes a
    fact-aware helper from a small explicit set.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_refresh_dependency_unblocks_uses_fact_aware_status_source()

    assert not offenders, (
        "WS2 dependency refresh fence: "
        f"{rel}:TaskRuntimeService.refresh_dependency_unblocks() must build "
        "its dependency-status mapping from a fact-aware helper such as "
        + ", ".join(sorted(REFRESH_DEPENDENCY_UNBLOCKS_FACT_AWARE_HELPERS))
        + " rather than deriving the mapping from raw self._board.list_all(). "
        "Task-object mutation walks must route raw entity reads through "
        f"self.{TASK_RUNTIME_SERVICE_RAW_BOARD_LIST_HELPER}(), and the status "
        "source for dependency resolution must be fact-aware so latest "
        "task_runtime.execution completion facts remain authoritative. "
        "Offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# WS2 complete_execution() dependency event failures are fail-closed
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService.complete_execution()`` owns the parent completion write
# and the downstream dependency-row side effects. Each downstream side effect
# emits its own ``task_runtime.execution`` event. If one of those append
# projections fails, the parent row mutation has already happened, so the
# public result must be fail-closed: preserve the requested terminal reason,
# mark success false, and surface ``ledger_append_failed`` evidence for the
# worker/factory caller.

DEPENDENCY_EVENT_FAIL_CLOSED_KEYS: frozenset[str] = frozenset(
    {
        "failure_class",
        "reason",
        "requested_reason",
        "state_mutation_applied",
        "success",
    }
)


def _assignment_targets_name(node: ast.Assign, name: str) -> bool:
    """True if an assignment writes to a plain local name."""

    return any(isinstance(target, ast.Name) and target.id == name for target in node.targets)


def _assignment_to_subscript_key(node: ast.AST, key: str) -> ast.AST | None:
    """Return the assigned value when ``node`` writes ``[...]`` with ``key``."""

    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Subscript) and _subscript_key(target) == key:
                return node.value
    if isinstance(node, ast.AnnAssign):
        target = node.target
        if isinstance(target, ast.Subscript) and _subscript_key(target) == key:
            return node.value
    return None


def _block_assigns_subscript_key(
    nodes: list[ast.stmt],
    key: str,
    *,
    constant_value: object = ...,
) -> bool:
    """True if any statement in ``nodes`` writes the projected dict key."""

    for node in nodes:
        assigned_value = _assignment_to_subscript_key(node, key)
        if assigned_value is None:
            continue
        if constant_value is ...:
            return True
        if isinstance(assigned_value, ast.Constant) and assigned_value.value == constant_value:
            return True
    return False


def _with_dependency_execution_events_fail_closed_violations() -> list[str]:
    """Validate dependency-event append failures are projected fail-closed."""

    method_def = _task_runtime_service_method_def("_with_dependency_execution_events")
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []

    dependency_projection = [
        node
        for node in ast.walk(method_def)
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and (assigned := _assignment_to_subscript_key(node, "dependency_execution_events")) is not None
            and _node_references_name_or_attribute(assigned, "dependency_events")
        )
    ]
    if not dependency_projection:
        offenders.append(
            f"{rel}:TaskRuntimeService._with_dependency_execution_events() must "
            "project dependency_execution_events from dependency_events so "
            "downstream append evidence remains inspectable."
        )

    failed_event_assignments = [
        node
        for node in ast.walk(method_def)
        if (
            isinstance(node, ast.Assign)
            and _assignment_targets_name(node, "failed_events")
            and _node_references_name_or_attribute(node.value, "dependency_events")
            and _contains_string_literal(node.value, "ok")
        )
    ]
    if not failed_event_assignments:
        offenders.append(
            f"{rel}:TaskRuntimeService._with_dependency_execution_events() must "
            "derive failed_events from dependency_events where ok is false."
        )

    fail_closed_blocks = [
        node
        for node in ast.walk(method_def)
        if (
            isinstance(node, ast.If)
            and _node_references_name_or_attribute(node.test, "failed_events")
            and _contains_string_literal(node.test, "success")
        )
    ]
    if not fail_closed_blocks:
        offenders.append(
            f"{rel}:TaskRuntimeService._with_dependency_execution_events() must "
            "gate fail-closed projection on failed dependency events and an "
            "otherwise-successful parent result."
        )
        return offenders

    fail_closed_block = fail_closed_blocks[0]
    missing_keys = sorted(
        key
        for key in DEPENDENCY_EVENT_FAIL_CLOSED_KEYS
        if not _block_assigns_subscript_key(fail_closed_block.body, key)
    )
    if missing_keys:
        offenders.append(
            f"{rel}:TaskRuntimeService._with_dependency_execution_events() "
            "fail-closed block must assign "
            + ", ".join(missing_keys)
            + " so ledger append failure evidence is visible to callers."
        )
    if not _block_assigns_subscript_key(fail_closed_block.body, "success", constant_value=False):
        offenders.append(
            f"{rel}:TaskRuntimeService._with_dependency_execution_events() "
            "must set success=False when any dependency execution event append "
            "failed after the parent state mutation."
        )
    if not _block_assigns_subscript_key(
        fail_closed_block.body,
        "state_mutation_applied",
        constant_value=True,
    ):
        offenders.append(
            f"{rel}:TaskRuntimeService._with_dependency_execution_events() "
            "must set state_mutation_applied=True when reporting a post-mutation "
            "dependency event append failure."
        )

    reason_assignments = [
        _assignment_to_subscript_key(node, "reason")
        for node in fail_closed_block.body
        if _assignment_to_subscript_key(node, "reason") is not None
    ]
    if not any(
        value is not None and _node_references_name_or_attribute(value, "failed_events") for value in reason_assignments
    ):
        offenders.append(
            f"{rel}:TaskRuntimeService._with_dependency_execution_events() "
            "must source the fail-closed reason from the failed dependency "
            "event instead of fabricating an unrelated terminal reason."
        )

    return offenders


def _complete_execution_dependency_projection_violations() -> list[str]:
    """Validate ``complete_execution`` returns through the dependency projection."""

    method_def = _task_runtime_service_method_def("complete_execution")
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []

    dependency_calls = [
        node
        for node in ast.walk(method_def)
        if isinstance(node, ast.Call) and _call_name(node.func) == "self._apply_dependency_completion_side_effects"
    ]
    append_completed_calls = [
        node
        for node in ast.walk(method_def)
        if (
            isinstance(node, ast.Call)
            and _call_name(node.func) == "self._append_execution_event"
            and node.args
            and _string_literal(node.args[0]) == "completed"
        )
    ]
    projection_return_calls = [
        node.value
        for node in ast.walk(method_def)
        if (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Call)
            and _call_name(node.value.func) == "self._with_dependency_execution_events"
        )
    ]

    if not dependency_calls:
        offenders.append(
            f"{rel}:TaskRuntimeService.complete_execution() must apply "
            "dependency completion side effects; otherwise this WS2 fence is "
            "vacuous and should be retired with the dependency fan-out path."
        )
        return offenders
    if not append_completed_calls:
        offenders.append(
            f"{rel}:TaskRuntimeService.complete_execution() must append the "
            "parent completed execution event before projecting dependency "
            "event failures."
        )
        return offenders
    if not projection_return_calls:
        offenders.append(
            f"{rel}:TaskRuntimeService.complete_execution() must return "
            "self._with_dependency_execution_events(result, dependency_events) "
            "so dependency append failures cannot be hidden behind a successful "
            "parent completion result."
        )
        return offenders

    projection_call = projection_return_calls[0]
    call_arg_names = [arg.id for arg in projection_call.args if isinstance(arg, ast.Name)]
    if "result" not in call_arg_names or "dependency_events" not in call_arg_names:
        offenders.append(
            f"{rel}:TaskRuntimeService.complete_execution() must pass both "
            "result and dependency_events into _with_dependency_execution_events()."
        )
    if projection_call.lineno <= max(dependency_calls[0].lineno, append_completed_calls[0].lineno):
        offenders.append(
            f"{rel}:TaskRuntimeService.complete_execution() must call "
            "_with_dependency_execution_events() after dependency side effects "
            "and the parent completed event append have both been attempted."
        )

    return offenders


def test_complete_execution_dependency_event_failures_are_fail_closed() -> None:
    """WS2 dependency-event failure projection fence.

    ``complete_execution()`` can update the completed parent row and then emit
    dependency-row events for rows it unblocked. A downstream dependency event
    append failure must not leave the public finalization result as
    ``success=True``. The result must keep the dependency event evidence,
    preserve the requested terminal reason, set ``success=False``, and mark
    ``state_mutation_applied=True`` / ``failure_class=ledger_append_failed`` so
    worker/factory callers can surface the Execution Ledger SSoT failure.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = [
        *_complete_execution_dependency_projection_violations(),
        *_with_dependency_execution_events_fail_closed_violations(),
    ]

    assert not offenders, (
        "WS2 dependency-event failure projection fence: "
        f"{rel}:TaskRuntimeService.complete_execution() must route dependency "
        "completion side-effect events through _with_dependency_execution_events(), "
        "and that helper must fail-close otherwise-successful results when any "
        "dependency execution event append reports ok=False. Offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# WS2 claim_execution() — dependency refresh + fact-aware terminal veto
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService.claim_execution()`` is the direct claim entry point used
# by the worker pool and the director adapter. Unlike ``claim_next_execution``
# (which is a *preview* + atomic claim), ``claim_execution`` takes an explicit
# ``task_id`` and must still consult the WS2 fact-aware read path before it
# hands out a lease. Otherwise:
#
#   * Dependency unblocks driven by late ``task_runtime.execution`` completion
#     facts never reach the claim path, so a BLOCKED row can be claimed before
#     its dependencies are reconciled.
#   * The terminal fact veto (``_terminal_task_status_for_session``) is the
#     only place that can refuse a claim based on the latest execution fact,
#     so it must be consulted *before* the lease/session is created. A claim
#     that ignores the fact veto regresses to raw-file-only claim authority.
#
# Both fences are structural: they walk the ``claim_execution`` AST, locate
# the relevant call sites by name (not by line number), and verify ordering.

CLAIM_EXECUTION_FACT_AWARE_HELPERS: frozenset[str] = frozenset(
    {
        "list_task_rows_from_execution_facts",
        "_fact_overlaid_dependency_status_rows",
        "_list_dependency_status_rows",
        "list_observable_task_rows",
        "_find_latest_execution_fact_row_for_task",
    }
)
CLAIM_EXECUTION_TERMINAL_FACT_VETO_HELPERS: frozenset[str] = frozenset(
    {
        "_terminal_task_status_for_session",
        "is_terminal_session_status",
        "terminal_task_status_value_for_session_status",
        "is_terminal_task_row_status",
        "_row_authorizes_retry_over_terminal_session",
    }
)
CLAIM_EXECUTION_SESSION_CREATE_NAMES: frozenset[str] = frozenset(
    {
        "create",  # TaskExecutionSession.create(...)
    }
)
# Forward-looking allowlist for private fact-stream readers. New helpers
# added to ``TaskRuntimeService`` that query the ``task_runtime.execution``
# Fact Stream directly must be added here so the WS2 claim fence knows
# they are read-only projections rather than new task-state sources.
CLAIM_EXECUTION_FACT_STREAM_READER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "list_task_rows_from_execution_facts",
        "_fact_overlaid_dependency_status_rows",
        "_list_dependency_status_rows",
        "list_observable_task_rows",
        "_augment_task_row",
        "_find_latest_execution_fact_row_for_task",
    }
)


def _claim_execution_function_def() -> ast.FunctionDef:
    """Return the ``TaskRuntimeService.claim_execution`` AST node."""

    source = TASK_RUNTIME_INTERNAL_SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parent_lookup(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "claim_execution":
            continue
        enclosing = parents.get(node)
        if isinstance(enclosing, ast.ClassDef) and enclosing.name == "TaskRuntimeService":
            return node
    raise AssertionError(
        f"TaskRuntimeService.claim_execution() not found in {TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)}"
    )


def _iter_self_method_calls(method_def: ast.FunctionDef, method_name: str) -> list[ast.Call]:
    """Return ``self.<method_name>(...)`` call nodes in ``method_def``."""

    return [
        node for node in ast.walk(method_def) if isinstance(node, ast.Call) and _call_is_self_method(node, method_name)
    ]


def _iter_qualified_call_nodes_anywhere(
    method_def: ast.FunctionDef,
    *,
    leaf_method_names: frozenset[str] | set[str],
) -> list[ast.Call]:
    """Return call nodes whose trailing attribute (or Name) matches ``leaf_method_names``.

    Matches both ``ast.Attribute`` callees (e.g. ``self._foo.method(...)``,
    ``SomeClass.method(...)``) and bare ``ast.Name`` callees
    (e.g. ``module_helper(...)``) so the fence can detect module-level
    helpers as well as bound self-methods.
    """

    matches: list[ast.Call] = []
    for node in ast.walk(method_def):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in leaf_method_names:
                matches.append(node)
        elif isinstance(func, ast.Name) and func.id in leaf_method_names:
            matches.append(node)
    return matches


def _call_arg_references_local(call_node: ast.Call, local_name: str) -> bool:
    """True if any positional arg of ``call_node`` is the bare local ``local_name``."""

    return any(isinstance(arg, ast.Name) and arg.id == local_name for arg in call_node.args)


def _check_claim_execution_refreshes_dependency_unblocks_before_raw_claim() -> list[str]:
    """WS2 claim refresh fence.

    Locate the first ``self._board.get(normalized)`` call site in
    ``claim_execution`` and require that a ``self.refresh_dependency_unblocks()``
    call exists earlier in the function body. The dependency-unblock refresh
    is the WS2 path that consults the latest ``task_runtime.execution``
    completion facts to wake BLOCKED rows. Skipping it on the direct claim
    path lets a BLOCKED row be claimed before its dependencies are reconciled.
    """

    method_def = _claim_execution_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []

    raw_claim_calls = [
        call
        for call in ast.walk(method_def)
        if isinstance(call, ast.Call)
        and _call_name(call.func) == "self._board.get"
        and _call_arg_references_local(call, "normalized")
    ]
    if not raw_claim_calls:
        offenders.append(
            f"{rel}:TaskRuntimeService.claim_execution() no longer has the "
            "self._board.get(normalized) raw claim path. If the claim entry "
            "point was removed, the WS2 claim refresh fence becomes vacuous "
            "and the entire function should be retired."
        )
        return offenders

    first_raw_claim = raw_claim_calls[0]
    refresh_calls = _iter_self_method_calls(method_def, "refresh_dependency_unblocks")

    if not refresh_calls:
        offenders.append(
            f"{rel}:TaskRuntimeService.claim_execution():{first_raw_claim.lineno} "
            "is the raw self._board.get(normalized) claim path, but the function "
            "does not call self.refresh_dependency_unblocks() before it. Late "
            "task_runtime.execution completion facts can unblock dependencies "
            "that the raw file-backed row still reports as BLOCKED; the direct "
            "claim path must refresh dependency unblocks first."
        )
        return offenders

    if any(call.lineno < first_raw_claim.lineno for call in refresh_calls):
        return offenders

    offenders.append(
        f"{rel}:TaskRuntimeService.claim_execution() calls "
        "self.refresh_dependency_unblocks() only at line "
        f"{refresh_calls[0].lineno}, AFTER the raw self._board.get(normalized) "
        f"claim path at line {first_raw_claim.lineno}. The refresh must "
        "precede the raw claim so dependency unblock evidence from "
        "task_runtime.execution facts is consulted first."
    )
    return offenders


def _check_claim_execution_consults_fact_aware_helper_and_terminal_veto() -> list[str]:
    """WS2 claim fact-aware helper + terminal fact veto fence.

    Before ``TaskRuntimeService.claim_execution()`` constructs a
    ``TaskExecutionSession`` (lease creation), it must:

      1. Consult a fact-aware read helper so the latest
         ``task_runtime.execution`` events are part of the read projection.
      2. Consult a terminal fact veto helper
         (``_terminal_task_status_for_session``,
         ``is_terminal_session_status``, or
         ``terminal_task_status_value_for_session_status``) so a row that
         the Fact Stream has already marked terminal cannot be claimed.

    The fence is structural: it locates the first
    ``TaskExecutionSession.create(...)`` call, then verifies that at least
    one fact-aware helper call AND at least one terminal veto call appear
    earlier in the same function body.
    """

    method_def = _claim_execution_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []

    session_create_calls = _iter_qualified_call_nodes_anywhere(
        method_def,
        leaf_method_names=CLAIM_EXECUTION_SESSION_CREATE_NAMES,
    )
    session_create_calls = [
        call for call in session_create_calls if _call_name(call.func) == "TaskExecutionSession.create"
    ]
    if not session_create_calls:
        offenders.append(
            f"{rel}:TaskRuntimeService.claim_execution() no longer calls "
            "TaskExecutionSession.create(...). The fact-aware helper + "
            "terminal veto fence becomes vacuous without a session-creation "
            "boundary; if claim_execution was retired, drop this fence."
        )
        return offenders

    first_session_create = session_create_calls[0]

    fact_aware_calls = _iter_qualified_call_nodes_anywhere(
        method_def,
        leaf_method_names=set(CLAIM_EXECUTION_FACT_AWARE_HELPERS),
    )
    fact_aware_before_create = [
        call
        for call in fact_aware_calls
        if _call_name(call.func).rsplit(".", maxsplit=1)[-1] in CLAIM_EXECUTION_FACT_AWARE_HELPERS
        and call.lineno < first_session_create.lineno
    ]
    if not fact_aware_before_create:
        offenders.append(
            f"{rel}:TaskRuntimeService.claim_execution() must consult a "
            "fact-aware helper such as "
            + ", ".join(sorted(CLAIM_EXECUTION_FACT_AWARE_HELPERS))
            + f" before TaskExecutionSession.create(...) at line "
            f"{first_session_create.lineno}. A claim path that skips the "
            "Fact Stream regresses to raw-file-only claim authority."
        )

    terminal_veto_calls = _iter_qualified_call_nodes_anywhere(
        method_def,
        leaf_method_names=set(CLAIM_EXECUTION_TERMINAL_FACT_VETO_HELPERS),
    )
    terminal_veto_before_create = [
        call
        for call in terminal_veto_calls
        if _call_name(call.func).rsplit(".", maxsplit=1)[-1] in CLAIM_EXECUTION_TERMINAL_FACT_VETO_HELPERS
        and call.lineno < first_session_create.lineno
    ]
    if not terminal_veto_before_create:
        offenders.append(
            f"{rel}:TaskRuntimeService.claim_execution() must consult a "
            "terminal fact veto helper such as "
            + ", ".join(sorted(CLAIM_EXECUTION_TERMINAL_FACT_VETO_HELPERS))
            + f" before TaskExecutionSession.create(...) at line "
            f"{first_session_create.lineno}. The terminal fact veto is the "
            "only authority that can refuse a claim based on the latest "
            "task_runtime.execution evidence; without it, a terminal row can "
            "be silently re-leased."
        )

    return offenders


def test_claim_execution_refreshes_dependency_unblocks_before_raw_claim_path() -> None:
    """WS2 claim refresh fence.

    ``TaskRuntimeService.claim_execution()`` is the direct claim entry point
    used by the worker pool (``TaskRuntimePort.claim_execution``) and the
    director adapter. It must call ``self.refresh_dependency_unblocks()``
    *before* the raw ``self._board.get(normalized)`` claim path so late
    ``task_runtime.execution`` completion facts (and any in-flight execution
    overlay) wake BLOCKED rows before the claim commits.

    The fence is structural: it walks the ``claim_execution`` AST, locates
    the first ``self._board.get(normalized)`` call site, and verifies that
    at least one ``self.refresh_dependency_unblocks()`` call precedes it. It
    does not weaken the existing raw-board read/write allowlists; the
    reviewed call counts in ``REVIEWED_TASK_RUNTIME_SERVICE_BOARD_*`` are
    intentionally unchanged.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_claim_execution_refreshes_dependency_unblocks_before_raw_claim()

    assert not offenders, (
        "WS2 claim refresh fence: "
        f"{rel}:TaskRuntimeService.claim_execution() must call "
        "self.refresh_dependency_unblocks() before the raw "
        "self._board.get(normalized) claim path so dependency unblock "
        "evidence from task_runtime.execution facts is consulted before "
        "the claim commits. Offenders:\n" + "\n".join(offenders)
    )


def test_claim_execution_consults_fact_aware_helper_and_terminal_fact_veto() -> None:
    """WS2 claim fact-aware helper + terminal fact veto fence.

    ``TaskRuntimeService.claim_execution()`` must consult a fact-aware
    helper (``list_task_rows_from_execution_facts``,
    ``_fact_overlaid_dependency_status_rows``,
    ``_list_dependency_status_rows``, or ``list_observable_task_rows``) and
    a terminal fact veto helper (``_terminal_task_status_for_session``,
    ``is_terminal_session_status``, or
    ``terminal_task_status_value_for_session_status``) before constructing
    the lease-backed ``TaskExecutionSession``. Without these consultations,
    the direct claim path regresses to raw-file-only claim authority and
    can lease a row that the Fact Stream has already marked terminal.

    The fence is structural: it locates the first
    ``TaskExecutionSession.create(...)`` call, then verifies that at least
    one fact-aware helper call AND at least one terminal veto call appear
    earlier in the same function body.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_claim_execution_consults_fact_aware_helper_and_terminal_veto()

    assert not offenders, (
        "WS2 claim fact-aware helper + terminal fact veto fence: "
        f"{rel}:TaskRuntimeService.claim_execution() must consult a "
        "fact-aware helper and a terminal fact veto helper before "
        "TaskExecutionSession.create(...) so the lease/session is only "
        "created when the latest task_runtime.execution evidence allows it. "
        "Offenders:\n" + "\n".join(offenders)
    )


def _top_level_function_defs(path: Path) -> list[ast.FunctionDef]:
    """Return top-level ``def`` entries in ``path`` (no class/inner methods)."""

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return [node for node in tree.body if isinstance(node, ast.FunctionDef)]


def _function_queries_execution_fact_stream(method_def: ast.FunctionDef) -> bool:
    """True if ``method_def`` queries the ``task_runtime.execution`` Fact Stream.

    Catches ``QueryFactEventsV1(...)`` and ``query_fact_events(...)`` calls
    whose stream literal matches ``task_runtime.execution``. Imports or
    unrelated streams are ignored.
    """

    for node in ast.walk(method_def):
        if not isinstance(node, ast.Call):
            continue
        callee = _call_name(node.func)
        if callee not in {"QueryFactEventsV1", "query_fact_events"}:
            continue
        for arg in (*node.args, *(keyword.value for keyword in node.keywords)):
            if _contains_string_literal(arg, TASK_RUNTIME_EXECUTION_STREAM):
                return True
    return False


def test_new_fact_stream_readers_in_task_runtime_service_are_read_only_projections() -> None:
    """WS2 claim fact-stream reader containment fence.

    ``TaskRuntimeService`` may add private helpers that query the
    ``task_runtime.execution`` Fact Stream directly. Each such helper must
    be a read-only projection: its name must appear in
    ``CLAIM_EXECUTION_FACT_STREAM_READER_ALLOWLIST`` (a small curated list
    of reader helpers) and it must not contain any direct write to the
    ``task_runtime.execution.jsonl`` event file or to
    ``AppendFactEventCommandV1``.

    The fence is forward-looking: today every fact-stream reader is in the
    allowlist, so a name-only addition that violates the allowlist (or a
    helper that secretly writes the fact stream) is caught. The fence does
    not weaken any existing raw-board read/write allowlist; it only
    constrains new top-level helpers that touch the Fact Stream.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []
    known_owners = {"TaskRuntimeService"}  # helpers expected to live on this class

    # Top-level module functions: every fact-stream reader must be allowlisted.
    for func_def in _top_level_function_defs(TASK_RUNTIME_INTERNAL_SERVICE):
        if not _function_queries_execution_fact_stream(func_def):
            continue
        if func_def.name in CLAIM_EXECUTION_FACT_STREAM_READER_ALLOWLIST:
            continue
        offenders.append(
            f"{rel}:{func_def.name}() is a new top-level helper that queries "
            "the task_runtime.execution Fact Stream directly. Add it to "
            "CLAIM_EXECUTION_FACT_STREAM_READER_ALLOWLIST (read-only "
            "projection) or refactor it through an allowlisted reader."
        )

    # Bound methods on TaskRuntimeService: any new fact-stream reader must
    # also be allowlisted and must not bypass the reader projection.
    source = TASK_RUNTIME_INTERNAL_SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parent_lookup(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name in (
            "__init__",
            "claim_execution",
        ):
            continue
        enclosing = parents.get(node)
        if not isinstance(enclosing, ast.ClassDef) or enclosing.name not in known_owners:
            continue
        if node.name in CLAIM_EXECUTION_FACT_STREAM_READER_ALLOWLIST:
            continue
        if not _function_queries_execution_fact_stream(node):
            continue
        offenders.append(
            f"{rel}:TaskRuntimeService.{node.name}() is a new private helper "
            "that queries the task_runtime.execution Fact Stream directly. "
            "Add it to CLAIM_EXECUTION_FACT_STREAM_READER_ALLOWLIST "
            "(read-only projection) so the WS2 claim fence does not treat it "
            "as a new task-state source."
        )
        # Bound fact-stream readers must not write the event file or append
        # new fact events; that is the role of _append_execution_event only.
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                callee = _call_name(child.func)
                if callee == "AppendFactEventCommandV1" and _contains_string_literal(
                    child, TASK_RUNTIME_EXECUTION_STREAM
                ):
                    offenders.append(
                        f"{rel}:TaskRuntimeService.{node.name}() at line "
                        f"{child.lineno} appends task_runtime.execution facts; "
                        "fact-stream readers must remain read-only projections."
                    )
            elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                method = child.func.attr
                if method in TASK_RUNTIME_EXECUTION_DIRECT_WRITE_METHODS and _contains_string_literal(
                    child, TASK_RUNTIME_EXECUTION_EVENT_FILE
                ):
                    offenders.append(
                        f"{rel}:TaskRuntimeService.{node.name}() at line "
                        f"{child.lineno} writes task_runtime.execution.jsonl "
                        "directly; fact-stream readers must remain read-only."
                    )

    assert not offenders, (
        "WS2 claim fact-stream reader containment fence: "
        "New helpers in "
        f"{rel} that query the task_runtime.execution Fact Stream must be "
        "added to CLAIM_EXECUTION_FACT_STREAM_READER_ALLOWLIST as read-only "
        "projections, and must not append new fact events or write the "
        "event file directly. Do not weaken the existing raw-board "
        "read/write allowlists to satisfy this fence. Offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# WS2 get_task() — observable single-row read model
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService.get_task()`` is the public single-row read projection
# for status / snapshot / UI consumers. To stay consistent with the
# multi-row observable projection (``list_observable_task_rows``) it must
# funnel through that same projection so late ``task_runtime.execution``
# facts and the in-flight execution overlay stay part of the read model.
#
# This is a structural fence: it walks the AST of ``get_task()`` and any
# private ``TaskRuntimeService`` helpers it delegates to, then asserts:
#
#   1. The projection is reachable from ``get_task()`` (directly or through
#      a single layer of private ``self.<helper>()`` delegation).
#   2. ``get_task()`` does not regress to raw row-only reads by calling
#      ``self._board.get()``, ``self._board.list_all()``, or
#      ``self.list_task_rows()``. It also must not borrow the external-id
#      materialization lookup helper as its task-id read path.
#
# The fence is intentionally line-number-agnostic. New helper methods that
# ``get_task()`` delegates to (e.g. ``_resolve_observable_task_row``) are
# detected by walking the AST for ``self.<name>(...)`` call sites and
# resolving each helper to its ``ast.FunctionDef`` body.

GET_TASK_FORBIDDEN_RAW_READ_TARGETS: dict[str, str] = {
    "self._board.get": "raw TaskBoard.get() bypasses the execution-fact overlay",
    "self._board.list_all": "raw TaskBoard.list_all() bypasses the execution-fact overlay",
    "self.list_task_rows": "raw list_task_rows() regresses to file-backed status only",
    "self._get_task_by_external_task_id": (
        "_get_task_by_external_task_id() is an external-id materialization lookup, not the public task-id read path"
    ),
}

GET_TASK_DELEGATED_HELPER_DEPTH = 1


def _get_task_function_def() -> ast.FunctionDef:
    """Return the ``TaskRuntimeService.get_task`` AST node."""

    return _task_runtime_service_method_def("get_task")


def _iter_get_task_self_method_calls(method_def: ast.FunctionDef) -> list[ast.Call]:
    """Return ``self.<name>(...)`` call nodes in ``method_def``.

    The leaf method name is what the fence looks at when deciding whether to
    follow a helper (one level deep). Module-level helper calls (e.g.
    ``_augment_task_row(...)``) are intentionally ignored: the public read
    model must stay on the observable row projection, not on top-level
    mutation/lookup helpers.
    """

    matches: list[ast.Call] = []
    for node in ast.walk(method_def):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not _call_is_self_method(node, func.attr):
            continue
        matches.append(node)
    return matches


def _collect_get_task_delegated_helpers(root_def: ast.FunctionDef) -> dict[str, ast.FunctionDef]:
    """Return private ``TaskRuntimeService`` helpers reachable from ``root_def``.

    Walks ``root_def`` for ``self.<name>(...)`` call sites and resolves each
    unique name to its ``TaskRuntimeService`` method AST node. The depth
    bound is intentionally small (``GET_TASK_DELEGATED_HELPER_DEPTH``) so the
    fence does not silently walk the entire service graph.
    """

    helpers: dict[str, ast.FunctionDef] = {}
    pending: list[ast.FunctionDef] = [root_def]
    for _ in range(GET_TASK_DELEGATED_HELPER_DEPTH + 1):
        next_pending: list[ast.FunctionDef] = []
        for scope_def in pending:
            for call_node in _iter_get_task_self_method_calls(scope_def):
                if not isinstance(call_node.func, ast.Attribute):
                    continue
                name = call_node.func.attr
                if not name.startswith("_") or name == "__init__":
                    continue
                if name in helpers:
                    continue
                try:
                    helper_def = _task_runtime_service_method_def(name)
                except AssertionError:
                    # Helper is not on TaskRuntimeService (e.g. local import);
                    # the forbidden-call fence will still catch raw reads.
                    continue
                helpers[name] = helper_def
                next_pending.append(helper_def)
        pending = next_pending
        if not pending:
            break
    return helpers


def _check_get_task_calls_list_observable_task_rows() -> list[str]:
    """Emit offenders if ``get_task()`` cannot reach ``self.list_observable_task_rows()``.

    The fence allows either a direct call inside ``get_task()`` or a call
    inside any private ``TaskRuntimeService`` helper it delegates to. This
    mirrors how the production code already factors the read resolution into
    a small private helper while keeping the public surface tied to the
    observable read model.
    """

    get_task_def = _get_task_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()

    if _method_body_calls_self_method(get_task_def, "list_observable_task_rows"):
        return []

    for _helper_name, helper_def in _collect_get_task_delegated_helpers(get_task_def).items():
        if _method_body_calls_self_method(helper_def, "list_observable_task_rows"):
            return []

    return [
        f"{rel}:TaskRuntimeService.get_task() does not reach "
        "self.list_observable_task_rows(); the single-row read model must "
        "derive from the observable row projection so the "
        "task_runtime.execution Fact Stream overlay stays part of the read "
        "SSoT."
    ]


def _check_get_task_forbidden_raw_reads() -> list[str]:
    """Emit offenders if ``get_task`` or its private helper regresses to raw reads."""

    get_task_def = _get_task_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []
    scopes: list[tuple[str, ast.FunctionDef]] = [("get_task", get_task_def)]
    scopes.extend(
        (helper_name, helper_def)
        for helper_name, helper_def in sorted(_collect_get_task_delegated_helpers(get_task_def).items())
    )

    for scope_name, scope_def in scopes:
        for call_node in ast.walk(scope_def):
            if not isinstance(call_node, ast.Call):
                continue
            callee = _call_name(call_node.func)
            for forbidden in GET_TASK_FORBIDDEN_RAW_READ_TARGETS:
                if callee == forbidden:
                    offenders.append(
                        f"{rel}:TaskRuntimeService.{scope_name}():{call_node.lineno} "
                        f"calls {callee!r}; {GET_TASK_FORBIDDEN_RAW_READ_TARGETS[forbidden]}. "
                        "get_task() and its delegated single-row read helpers must read through "
                        "self.list_observable_task_rows() so the task_runtime.execution Fact Stream "
                        "overlay remains the read-model SSoT."
                    )
                    break

    return offenders


def test_get_task_reads_through_observable_rows() -> None:
    """WS2 single-row read model fence (positive invariant).

    ``TaskRuntimeService.get_task()`` is the public single-row lookup for
    status / snapshot / UI consumers. To stay consistent with
    ``list_observable_task_rows()``, it must reach the observable row
    projection directly or via a small private ``TaskRuntimeService``
    helper. Reading the raw ``TaskBoard`` snapshot would silently drop late
    ``task_runtime.execution`` completion facts and any in-flight execution
    overlay, regressing the read model to the pre-WS2 raw-row view.

    The fence is structural: it walks the ``get_task()`` AST and the AST of
    any private ``TaskRuntimeService`` helper it delegates to
    (``GET_TASK_DELEGATED_HELPER_DEPTH`` levels deep) and verifies the
    observable row projection is reachable. It does not assert on line
    numbers or call ordering, so refactors that extract the lookup into a
    helper stay compliant as long as the helper itself reads observable
    rows.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_get_task_calls_list_observable_task_rows()

    assert not offenders, (
        "WS2 single-row read model fence: "
        f"{rel}:TaskRuntimeService.get_task() must reach "
        "self.list_observable_task_rows() (directly or via a private "
        "TaskRuntimeService helper it delegates to) so the public single-row "
        "read projection stays consistent with list_observable_task_rows() "
        "and the task_runtime.execution Fact Stream overlay. Offenders:\n" + "\n".join(offenders)
    )


def test_get_task_does_not_regress_to_raw_row_only_reads() -> None:
    """WS2 single-row read model fence (negative invariant).

    ``TaskRuntimeService.get_task()`` must not call
    ``self._board.get(...)``, ``self._board.list_all(...)``,
    ``self.list_task_rows(...)``, or ``self._get_task_by_external_task_id()``
    because the raw-read primitives bypass the ``task_runtime.execution`` fact
    overlay, and the external-id lookup helper is not the public task-id read
    path. Allowing any of them inside ``get_task()`` would silently regress
    or confuse the public single-row read projection.

    The fence is structural and walks both the ``get_task()`` AST body and
    any private helper that ``get_task()`` delegates to (for example
    ``_resolve_observable_task_row``), so raw reads cannot be hidden one
    function call away.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_get_task_forbidden_raw_reads()

    assert not offenders, (
        "WS2 single-row read model fence: "
        f"{rel}:TaskRuntimeService.get_task() must not regress to raw "
        "row-only reads. The public single-row read projection must route "
        "through self.list_observable_task_rows() so the "
        "task_runtime.execution Fact Stream overlay stays part of the read "
        "SSoT. Offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# WS2 task_exists() — observable single-row existence check
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService.task_exists()`` is the public existence check used by
# role adapters (``_board_task_exists`` in ``roles.adapters.internal.base``)
# before applying task-row mutations. To stay consistent with the single-row
# read model (``get_task()``) and the multi-row observable projection, the
# existence check must derive from the observable row model rather than the
# raw ``TaskBoard.get()`` snapshot. Calling ``self._board.get()`` would
# silently drop late ``task_runtime.execution`` completion facts and any
# in-flight execution overlay, regressing existence to the pre-WS2 raw-row
# view.
#
# The fence mirrors the ``get_task()`` structural shape:
#
#   1. Positive invariant: ``task_exists()`` reaches
#      ``self.list_observable_task_rows()`` either directly or via a single
#      private ``TaskRuntimeService`` helper it delegates to.
#   2. Negative invariant: ``task_exists()`` and any delegated helper do
#      not call ``self._board.get(...)``, ``self._board.list_all(...)``, or
#      ``self.list_task_rows(...)``. They also must not borrow the external-id
#      materialization lookup helper as their task-id existence path.
#
# The fence is intentionally line-number-agnostic so future refactors that
# keep the helper delegation (e.g. ``_resolve_observable_task_row``) stay
# compliant as long as the helper continues to read observable rows.


def _task_exists_function_def() -> ast.FunctionDef:
    """Return the ``TaskRuntimeService.task_exists`` AST node."""

    return _task_runtime_service_method_def("task_exists")


def _check_task_exists_calls_list_observable_task_rows() -> list[str]:
    """Emit offenders if ``task_exists()`` cannot reach ``self.list_observable_task_rows()``.

    The fence allows either a direct call inside ``task_exists()`` or a call
    inside any private ``TaskRuntimeService`` helper it delegates to (within
    ``GET_TASK_DELEGATED_HELPER_DEPTH`` levels). This mirrors how the
    production code already factors existence resolution into
    ``_resolve_observable_task_row`` so the public surface stays tied to the
    observable row projection.
    """

    task_exists_def = _task_exists_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()

    if _method_body_calls_self_method(task_exists_def, "list_observable_task_rows"):
        return []

    for _helper_name, helper_def in _collect_get_task_delegated_helpers(task_exists_def).items():
        if _method_body_calls_self_method(helper_def, "list_observable_task_rows"):
            return []

    return [
        f"{rel}:TaskRuntimeService.task_exists() does not reach "
        "self.list_observable_task_rows(); the public existence check must "
        "derive from the observable row projection so the "
        "task_runtime.execution Fact Stream overlay stays part of the read "
        "SSoT."
    ]


def _check_task_exists_forbidden_raw_reads() -> list[str]:
    """Emit offenders if ``task_exists`` or its private helper regresses to raw reads."""

    task_exists_def = _task_exists_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []
    scopes: list[tuple[str, ast.FunctionDef]] = [("task_exists", task_exists_def)]
    scopes.extend(
        (helper_name, helper_def)
        for helper_name, helper_def in sorted(_collect_get_task_delegated_helpers(task_exists_def).items())
    )

    for scope_name, scope_def in scopes:
        for call_node in ast.walk(scope_def):
            if not isinstance(call_node, ast.Call):
                continue
            callee = _call_name(call_node.func)
            for forbidden in GET_TASK_FORBIDDEN_RAW_READ_TARGETS:
                if callee == forbidden:
                    offenders.append(
                        f"{rel}:TaskRuntimeService.{scope_name}():{call_node.lineno} "
                        f"calls {callee!r}; {GET_TASK_FORBIDDEN_RAW_READ_TARGETS[forbidden]}. "
                        "task_exists() and its delegated single-row read helpers must read through "
                        "self.list_observable_task_rows() so the task_runtime.execution Fact Stream "
                        "overlay remains the read-model SSoT."
                    )
                    break

    return offenders


def test_task_exists_reads_through_observable_rows() -> None:
    """WS2 single-row existence-check fence (positive invariant).

    ``TaskRuntimeService.task_exists()`` is the public existence check used
    by role adapters before applying task-row mutations. To stay consistent
    with ``get_task()`` and ``list_observable_task_rows()`` it must reach
    the observable row projection directly or via a small private
    ``TaskRuntimeService`` helper. Reading the raw ``TaskBoard`` snapshot
    would silently drop late ``task_runtime.execution`` completion facts
    and any in-flight execution overlay, regressing the public existence
    check to the pre-WS2 raw-row view.

    The fence is structural: it walks the ``task_exists()`` AST and the AST
    of any private ``TaskRuntimeService`` helper it delegates to
    (``GET_TASK_DELEGATED_HELPER_DEPTH`` levels deep) and verifies the
    observable row projection is reachable.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_task_exists_calls_list_observable_task_rows()

    assert not offenders, (
        "WS2 single-row existence-check fence: "
        f"{rel}:TaskRuntimeService.task_exists() must reach "
        "self.list_observable_task_rows() (directly or via a private "
        "TaskRuntimeService helper it delegates to) so the public existence "
        "check stays consistent with list_observable_task_rows() and "
        "get_task(), and the task_runtime.execution Fact Stream overlay "
        "stays part of the read-model SSoT. Offenders:\n" + "\n".join(offenders)
    )


def test_task_exists_does_not_regress_to_raw_row_only_reads() -> None:
    """WS2 single-row existence-check fence (negative invariant).

    ``TaskRuntimeService.task_exists()`` must not call
    ``self._board.get(...)``, ``self._board.list_all(...)``,
    ``self.list_task_rows(...)``, or ``self._get_task_by_external_task_id()``
    because the raw-read primitives bypass the ``task_runtime.execution`` fact
    overlay, and the external-id lookup helper is not the public task-id
    existence path. Allowing any of them inside ``task_exists()`` would
    silently regress or confuse the public existence check.

    The fence is structural and walks both the ``task_exists()`` AST body
    and any private helper that ``task_exists()`` delegates to (for example
    ``_resolve_observable_task_row``), so raw reads cannot be hidden one
    function call away.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_task_exists_forbidden_raw_reads()

    assert not offenders, (
        "WS2 single-row existence-check fence: "
        f"{rel}:TaskRuntimeService.task_exists() must not regress to raw "
        "row-only reads. The public existence check must route through "
        "self.list_observable_task_rows() so the task_runtime.execution "
        "Fact Stream overlay stays part of the read-model SSoT. "
        "Offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# WS2 _task_has_unresolved_dependencies() — fact-aware dependency decision
# ---------------------------------------------------------------------------

DEPENDENCY_HELPER_REQUIRED_STATUS_CALL = "self._fact_overlaid_dependency_status_rows"
DEPENDENCY_HELPER_FORBIDDEN_RAW_READ_CALLS: frozenset[str] = frozenset(
    {
        "self._board.get",
        "self._board.list_all",
        "self.list_task_rows",
        "self.list_observable_task_rows",
        "self._get_task_by_external_task_id",
    }
)


def _task_has_unresolved_dependencies_function_def() -> ast.FunctionDef:
    return _task_runtime_service_method_def("_task_has_unresolved_dependencies")


def test_task_has_unresolved_dependencies_uses_fact_overlay_projection() -> None:
    """The claim dependency probe must use the fact-overlaid status projection."""

    method_def = _task_has_unresolved_dependencies_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    found = any(
        isinstance(node, ast.Call) and _call_name(node.func) == DEPENDENCY_HELPER_REQUIRED_STATUS_CALL
        for node in ast.walk(method_def)
    )

    assert found, (
        "WS2 dependency-decision fence: "
        f"{rel}:TaskRuntimeService._task_has_unresolved_dependencies() must call "
        f"{DEPENDENCY_HELPER_REQUIRED_STATUS_CALL}() directly. Dependency claim "
        "decisions must stay anchored on the task_runtime.execution fact overlay."
    )


def test_task_has_unresolved_dependencies_has_no_raw_dependency_status_reads() -> None:
    """The dependency probe must not rebuild dependency status from raw rows."""

    method_def = _task_has_unresolved_dependencies_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []
    for node in ast.walk(method_def):
        if not isinstance(node, ast.Call):
            continue
        callee = _call_name(node.func)
        if callee in DEPENDENCY_HELPER_FORBIDDEN_RAW_READ_CALLS:
            offenders.append(
                f"{rel}:TaskRuntimeService._task_has_unresolved_dependencies():{node.lineno} calls {callee}"
            )

    assert not offenders, (
        "WS2 dependency-decision fence: "
        "TaskRuntimeService._task_has_unresolved_dependencies() must not read "
        "dependency status from raw TaskBoard rows or recursive observable-row "
        "projections. Use only the fact-overlaid dependency status projection. "
        "Offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# WS2 get_observable_task_row_stats() / get_task_row_stats() — stats
# delegation through the observable read model
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService.get_observable_task_row_stats()`` is the public
# stats projection. It must count rows through
# ``self.list_observable_task_rows()`` so the ``task_runtime.execution``
# Fact Stream overlay stays in the read model. Calling
# ``self._list_file_task_rows()`` or ``self.list_task_rows()`` directly
# would silently regress to raw-file status counts.
#
# ``TaskRuntimeService.get_task_row_stats()`` is the compatibility
# entrypoint. It must purely delegate to
# ``self.get_observable_task_row_stats()`` without independently
# computing counts, reading rows, or accessing the raw board.

STATS_OBSERVABLE_FORBIDDEN_RAW_READ_TARGETS: dict[str, str] = {
    "self._list_file_task_rows": ("raw _list_file_task_rows() bypasses the execution-fact overlay"),
    "self._board.list_all": ("raw TaskBoard.list_all() bypasses the execution-fact overlay"),
    "self.list_task_rows": ("raw list_task_rows() regresses to file-backed status only"),
    "self._board.get": ("raw TaskBoard.get() bypasses the execution-fact overlay"),
    "self._get_task_by_external_task_id": (
        "_get_task_by_external_task_id() is an external-id materialization lookup, not a stats projection path"
    ),
}

STATS_COMPAT_FORBIDDEN_RAW_READ_TARGETS: dict[str, str] = {
    **STATS_OBSERVABLE_FORBIDDEN_RAW_READ_TARGETS,
    "self.list_observable_task_rows": (
        "get_task_row_stats() must delegate to get_observable_task_row_stats(), "
        "not independently call list_observable_task_rows()"
    ),
}


def _get_observable_task_row_stats_function_def() -> ast.FunctionDef:
    """Return the ``TaskRuntimeService.get_observable_task_row_stats`` AST node."""

    return _task_runtime_service_method_def("get_observable_task_row_stats")


def _get_task_row_stats_function_def() -> ast.FunctionDef:
    """Return the ``TaskRuntimeService.get_task_row_stats`` AST node."""

    return _task_runtime_service_method_def("get_task_row_stats")


def _check_observable_task_row_stats_calls_list_observable_task_rows() -> list[str]:
    """Emit offenders if ``get_observable_task_row_stats`` does not call
    ``self.list_observable_task_rows()``.

    The fence checks both the direct method body and one level of private
    ``TaskRuntimeService`` helper delegation so the production pattern of
    factoring through a small helper stays compliant.
    """

    method_def = _get_observable_task_row_stats_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()

    if _method_body_calls_self_method(method_def, "list_observable_task_rows"):
        return []

    for _helper_name, helper_def in _collect_get_task_delegated_helpers(method_def).items():
        if _method_body_calls_self_method(helper_def, "list_observable_task_rows"):
            return []

    return [
        f"{rel}:TaskRuntimeService.get_observable_task_row_stats() does not "
        "call self.list_observable_task_rows(); the stats projection must "
        "count through the observable row model so the task_runtime.execution "
        "Fact Stream overlay remains part of the read-model SSoT."
    ]


def _check_observable_task_row_stats_forbidden_raw_reads() -> list[str]:
    """Emit offenders if ``get_observable_task_row_stats`` calls raw read methods."""

    method_def = _get_observable_task_row_stats_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []

    for call_node in ast.walk(method_def):
        if not isinstance(call_node, ast.Call):
            continue
        callee = _call_name(call_node.func)
        for forbidden, reason in STATS_OBSERVABLE_FORBIDDEN_RAW_READ_TARGETS.items():
            if callee == forbidden:
                offenders.append(
                    f"{rel}:TaskRuntimeService.get_observable_task_row_stats():"
                    f"{call_node.lineno} calls {callee!r}; {reason}. "
                    "get_observable_task_row_stats() must count through "
                    "self.list_observable_task_rows() so the "
                    "task_runtime.execution Fact Stream overlay remains the "
                    "read-model SSoT."
                )
                break

    return offenders


def _check_task_row_stats_delegates_to_observable_stats() -> list[str]:
    """Emit offenders if ``get_task_row_stats`` does not delegate to
    ``self.get_observable_task_row_stats()``.
    """

    method_def = _get_task_row_stats_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()

    if _method_body_calls_self_method(method_def, "get_observable_task_row_stats"):
        return []

    return [
        f"{rel}:TaskRuntimeService.get_task_row_stats() does not delegate to "
        "self.get_observable_task_row_stats(); the compatibility entrypoint "
        "must delegate entirely to the observable stats projection rather than "
        "independently reading or computing task-row status counts."
    ]


def _check_task_row_stats_no_independent_reads() -> list[str]:
    """Emit offenders if ``get_task_row_stats`` reads rows independently."""

    method_def = _get_task_row_stats_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []

    for call_node in ast.walk(method_def):
        if not isinstance(call_node, ast.Call):
            continue
        callee = _call_name(call_node.func)
        for forbidden, reason in STATS_COMPAT_FORBIDDEN_RAW_READ_TARGETS.items():
            if callee == forbidden:
                offenders.append(
                    f"{rel}:TaskRuntimeService.get_task_row_stats():"
                    f"{call_node.lineno} calls {callee!r}; {reason}. "
                    "get_task_row_stats() must delegate to "
                    "self.get_observable_task_row_stats() without reading "
                    "rows or computing stats independently."
                )
                break

    return offenders


def test_observable_task_row_stats_calls_list_observable_task_rows() -> None:
    """WS2 stats projection fence (positive invariant).

    ``TaskRuntimeService.get_observable_task_row_stats()`` is the public
    stats projection that Factory, Director, and UI consumers read. It must
    count through ``self.list_observable_task_rows()`` so late
    ``task_runtime.execution`` fact evidence and the in-flight execution
    overlay stay part of the status-count SSoT. Calling
    ``self._list_file_task_rows()`` or ``self.list_task_rows()`` directly
    would silently regress to raw-file status counts.

    The fence is structural: it walks the AST of ``get_observable_task_row_stats()``
    and any private helper it delegates to and verifies
    ``self.list_observable_task_rows()`` is reachable.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_observable_task_row_stats_calls_list_observable_task_rows()

    assert not offenders, (
        "WS2 stats projection fence: "
        f"{rel}:TaskRuntimeService.get_observable_task_row_stats() must call "
        "self.list_observable_task_rows() so the task_runtime.execution Fact "
        "Stream overlay remains part of the stats read-model SSoT. "
        "Offenders:\n" + "\n".join(offenders)
    )


def test_observable_task_row_stats_does_not_read_raw_rows() -> None:
    """WS2 stats projection fence (negative invariant).

    ``TaskRuntimeService.get_observable_task_row_stats()`` must not call
    ``self._list_file_task_rows()``, ``self._board.list_all()``,
    ``self.list_task_rows()``, or ``self._board.get(...)`` because each of
    those reads raw ``TaskBoard`` state without the ``task_runtime.execution``
    fact overlay. Allowing any of them inside the stats method would silently
    regress the stats projection to the pre-WS2 raw-row view.

    The fence is structural: it walks the ``get_observable_task_row_stats()``
    AST body.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_observable_task_row_stats_forbidden_raw_reads()

    assert not offenders, (
        "WS2 stats projection fence: "
        f"{rel}:TaskRuntimeService.get_observable_task_row_stats() must not "
        "read raw rows. The stats projection must route through "
        "self.list_observable_task_rows() so the task_runtime.execution Fact "
        "Stream overlay stays part of the read-model SSoT. Offenders:\n" + "\n".join(offenders)
    )


def test_task_row_stats_delegates_to_observable_task_row_stats() -> None:
    """WS2 stats delegation fence (positive invariant).

    ``TaskRuntimeService.get_task_row_stats()`` is the compatibility
    entrypoint for task-row status counts. It must delegate entirely to
    ``self.get_observable_task_row_stats()`` without independently reading
    rows or computing counts. A method that re-reads observable rows
    independently would bypass the single delegation chain and create
    two paths that can diverge if the observable stats computation evolves.

    The fence is structural: it walks the AST of ``get_task_row_stats()``
    and verifies ``self.get_observable_task_row_stats()`` is called.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_task_row_stats_delegates_to_observable_stats()

    assert not offenders, (
        "WS2 stats delegation fence: "
        f"{rel}:TaskRuntimeService.get_task_row_stats() must delegate to "
        "self.get_observable_task_row_stats() so the stats computation has "
        "a single authoritative path. Offenders:\n" + "\n".join(offenders)
    )


def test_task_row_stats_does_not_independently_read_rows() -> None:
    """WS2 stats delegation fence (negative invariant).

    ``TaskRuntimeService.get_task_row_stats()`` must not call
    ``self.list_observable_task_rows()``, ``self._list_file_task_rows()``,
    ``self._board.list_all()``, ``self.list_task_rows()``, or
    ``self._board.get(...)`` because the compatibility entrypoint must
    delegate all reads to ``get_observable_task_row_stats()``. Allowing
    direct observable-row reads would mean ``get_task_row_stats()`` could
    silently drift from the canonical stats computation.

    The fence is structural: it walks the ``get_task_row_stats()`` AST body.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_task_row_stats_no_independent_reads()

    assert not offenders, (
        "WS2 stats delegation fence: "
        f"{rel}:TaskRuntimeService.get_task_row_stats() must not independently "
        "read rows. The compatibility entrypoint must delegate to "
        "self.get_observable_task_row_stats() so the stats computation has a "
        "single authoritative path. Offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# WS2 stats read-model — role projections must use observable stats
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService.get_task_row_stats()`` is the compatibility entrypoint
# that delegates to ``get_observable_task_row_stats()``. Role projections
# (DirectorStateTracker, PM agent) must call
# ``get_observable_task_row_stats()`` directly so the execution-fact overlay
# stays part of the stats read-model. Calling ``get_task_row_stats()`` would
# add an extra delegation hop and make it easy for a future refactor to
# silently route stats through the compat path instead of the observable path.
#
# ``TaskRuntimeService.get_task_row_stats()`` itself is NOT forbidden — it
# remains the public compatibility API that delegates to the observable stats.

DIRECTOR_STATE_TRACKER = POLARIS_ROOT / "cells" / "roles" / "adapters" / "internal" / "director" / "state_tracking.py"
PM_PLANNING_AGENT_STATS = POLARIS_ROOT / "cells" / "orchestration" / "pm_planning" / "internal" / "pm_agent.py"
PM_DELIVERY_DISPATCH_STATS = POLARIS_ROOT / "delivery" / "cli" / "pm" / "engine" / "_dispatch.py"


def _check_role_projection_forbidden_stats_calls(
    path: Path,
    *,
    scope_check: Any = None,
) -> list[str]:
    """Emit offenders if ``path`` calls ``get_task_row_stats`` instead of
    ``get_observable_task_row_stats``.

    Detects two patterns:
      1. Direct attribute call: ``task_runtime.get_task_row_stats()``
         or ``self.task_runtime.get_task_row_stats()``
      2. Dynamic attribute read via ``getattr``:
         ``getattr(task_runtime, "get_task_row_stats", ...)``
         followed by a callable invocation.

    ``scope_check`` is an optional predicate ``(function_name: str) -> bool``
    that filters which enclosing function definitions to inspect. When
    ``None`` the entire module is checked.

    ``get_observable_task_row_stats`` is always allowed.
    """

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rel = path.relative_to(BACKEND_ROOT).as_posix()
    parents = _parent_lookup(tree)
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Call, ast.Attribute)):
            continue

        # Pattern 1: direct attribute call — ``<receiver>.get_task_row_stats()``
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "get_task_row_stats":
                enclosing = _enclosing_function_name(node, parents)
                if scope_check is not None and not scope_check(enclosing):
                    continue
                offenders.append(
                    f"{rel}:{node.lineno} calls get_task_row_stats() in "
                    f"{enclosing}(); role projections must use "
                    "get_observable_task_row_stats() so the execution-fact "
                    "overlay stays part of the stats read-model"
                )

        # Pattern 2: dynamic getattr read — ``getattr(<receiver>,
        # "get_task_row_stats", ...)``
        if isinstance(node, ast.Call):
            callee = _call_name(node.func)
            if callee == "getattr" and node.args:
                attr_name = _string_literal(node.args[1]) if len(node.args) > 1 else ""
                if attr_name == "get_task_row_stats":
                    enclosing = _enclosing_function_name(node, parents)
                    if scope_check is not None and not scope_check(enclosing):
                        continue
                    offenders.append(
                        f"{rel}:{node.lineno} reads get_task_row_stats via "
                        f"getattr() in {enclosing}(); role projections must "
                        "use get_observable_task_row_stats() directly so the "
                        "execution-fact overlay stays part of the stats "
                        "read-model"
                    )

    return offenders


def _role_projection_observable_stats_call_lines(
    path: Path,
    *,
    scope_check: Any = None,
) -> list[int]:
    """Return call/getattr line numbers for ``get_observable_task_row_stats``."""

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parent_lookup(tree)
    lines: list[int] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        enclosing = _enclosing_function_name(node, parents)
        if scope_check is not None and not scope_check(enclosing):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "get_observable_task_row_stats":
            lines.append(int(node.lineno))
            continue
        if _call_name(func) == "getattr" and len(node.args) > 1:
            attr_name = _string_literal(node.args[1])
            if attr_name == "get_observable_task_row_stats":
                lines.append(int(node.lineno))

    return lines


def test_director_state_tracker_uses_observable_task_row_stats() -> None:
    """WS2 stats read-model fence for DirectorStateTracker.

    ``DirectorStateTracker.build_taskboard_observation_snapshot()`` builds
    the taskboard snapshot that Director and Factory consumers read. It must
    call ``get_observable_task_row_stats()`` directly so the
    ``task_runtime.execution`` Fact Stream overlay stays in the stats
    read-model. Calling ``get_task_row_stats()`` (the compatibility
    entrypoint) adds an unnecessary delegation hop and makes it easy for a
    future refactor to silently route stats through the compat path instead
    of the observable path.

    ``TaskRuntimeService.get_task_row_stats()`` itself is not forbidden — it
    remains the public compatibility API that delegates to the observable
    stats. The fence only targets role-projection consumers outside the
    ``runtime.task_runtime`` owner cell.
    """

    rel = DIRECTOR_STATE_TRACKER.relative_to(BACKEND_ROOT).as_posix()

    def scope_check(function_name: str) -> bool:
        return function_name == "build_taskboard_observation_snapshot"

    offenders = _check_role_projection_forbidden_stats_calls(
        DIRECTOR_STATE_TRACKER,
        scope_check=scope_check,
    )
    observable_lines = _role_projection_observable_stats_call_lines(
        DIRECTOR_STATE_TRACKER,
        scope_check=scope_check,
    )

    assert not offenders, (
        "WS2 stats read-model fence: "
        f"{rel}:DirectorStateTracker.build_taskboard_observation_snapshot "
        "must use get_observable_task_row_stats() instead of "
        "get_task_row_stats(). Role projections must call the observable "
        "stats API directly so the task_runtime.execution Fact Stream "
        "overlay stays part of the stats read-model. "
        "TaskRuntimeService.get_task_row_stats() itself remains allowed as "
        "the compatibility entrypoint. Offenders:\n" + "\n".join(offenders)
    )
    assert observable_lines, (
        "WS2 stats read-model fence: "
        f"{rel}:DirectorStateTracker.build_taskboard_observation_snapshot "
        "must call get_observable_task_row_stats() directly; deleting stats "
        "projection or using only rows would weaken the observable owner API "
        "contract."
    )


def test_pm_planning_agent_uses_observable_task_row_stats() -> None:
    """WS2 stats read-model fence for PM planning agent.

    ``PMAgent._tool_taskboard_stats()`` returns taskboard statistics to the
    PM LLM context. It must call ``get_observable_task_row_stats()`` directly
    so the ``task_runtime.execution`` Fact Stream overlay stays in the stats
    read-model. Calling ``get_task_row_stats()`` (the compatibility
    entrypoint) adds an unnecessary delegation hop and makes it easy for a
    future refactor to silently route stats through the compat path instead
    of the observable path.

    ``TaskRuntimeService.get_task_row_stats()`` itself is not forbidden — it
    remains the public compatibility API that delegates to the observable
    stats. The fence only targets role-projection consumers outside the
    ``runtime.task_runtime`` owner cell.
    """

    rel = PM_PLANNING_AGENT_STATS.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_role_projection_forbidden_stats_calls(
        PM_PLANNING_AGENT_STATS,
    )
    observable_lines = _role_projection_observable_stats_call_lines(
        PM_PLANNING_AGENT_STATS,
    )

    assert not offenders, (
        "WS2 stats read-model fence: "
        f"{rel} must use get_observable_task_row_stats() instead of "
        "get_task_row_stats(). Role projections must call the observable "
        "stats API directly so the task_runtime.execution Fact Stream "
        "overlay stays part of the stats read-model. "
        "TaskRuntimeService.get_task_row_stats() itself remains allowed as "
        "the compatibility entrypoint. Offenders:\n" + "\n".join(offenders)
    )
    assert observable_lines, (
        "WS2 stats read-model fence: "
        f"{rel} must call get_observable_task_row_stats() directly; deleting "
        "stats projection or using only rows would weaken the observable "
        "owner API contract."
    )


def test_pm_delivery_dispatch_uses_observable_task_row_stats() -> None:
    """WS2 stats read-model fence for PM delivery dispatch summaries."""

    rel = PM_DELIVERY_DISPATCH_STATS.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_role_projection_forbidden_stats_calls(PM_DELIVERY_DISPATCH_STATS)
    observable_lines = _role_projection_observable_stats_call_lines(PM_DELIVERY_DISPATCH_STATS)

    assert not offenders, (
        "WS2 stats read-model fence: "
        f"{rel} must use get_observable_task_row_stats() instead of "
        "get_task_row_stats() when projecting taskboard summary stats. "
        "Offenders:\n" + "\n".join(offenders)
    )
    assert observable_lines, (
        "WS2 stats read-model fence: "
        f"{rel} must call get_observable_task_row_stats() directly; deleting "
        "taskboard summary stats or using only rows would weaken the "
        "observable owner API contract."
    )


# ---------------------------------------------------------------------------
# WS2 test-file observable read-model fence — raw list_task_rows() calls
# ---------------------------------------------------------------------------
#
# The production ``test_production_read_side_uses_observable_task_rows`` fence
# already prevents production code from calling ``.list_task_rows()``.  But it
# skips ``tests/`` directories entirely, so integration tests that construct
# real ``TaskRuntimeService`` instances can silently reintroduce direct
# ``list_task_rows()`` calls instead of the observable read-model
# (``list_observable_task_rows()``).  This section covers the two test files
# that previously violated the boundary.
#
# Mock-class method *definitions* (``def list_task_rows(self, ...)``) that
# raise ``AssertionError`` on purpose are excluded — they are regression
# guards, not calls.

ROLE_ADAPTERS_TASKBOARD_ALIGNMENT_TEST = POLARIS_ROOT / "tests" / "test_role_adapters_taskboard_alignment.py"
FACTORY_ROUTER_TEST = POLARIS_ROOT / "tests" / "test_factory_router.py"
TEST_FILE_RAW_LIST_TASK_ROWS_TARGETS = (
    ROLE_ADAPTERS_TASKBOARD_ALIGNMENT_TEST,
    FACTORY_ROUTER_TEST,
)


def _test_file_raw_list_task_rows_call_violations(path: Path) -> list[str]:
    """Detect ``.list_task_rows()`` attribute calls in a test file.

    Method *definitions* (``def list_task_rows(...)``) are excluded because
    mock classes in tests intentionally define this method as a regression
    guard (raising ``AssertionError``).  Only actual ``Call`` nodes where
    the function is an ``ast.Attribute`` with ``attr == "list_task_rows"``
    are flagged.

    Complexity:
        O(n) over AST nodes in the file.
    """

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rel = path.relative_to(BACKEND_ROOT).as_posix()
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "list_task_rows":
            continue
        offenders.append(f"{rel}:{node.lineno} calls raw .list_task_rows()")
    return offenders


def test_role_and_factory_tests_use_observable_task_rows() -> None:
    """WS2 test-file observable read-model fence for ``list_task_rows()``.

    Integration tests that construct ``TaskRuntimeService`` or mock it must
    consume the observable read-model (``list_observable_task_rows()``) so
    assertions validate the same fact-overlaid projection that production
    snapshot / UI consumers see.  Direct ``.list_task_rows()`` calls bypass
    execution-fact overlay and can silently pass assertions that would fail
    under the production read-model.

    Mock-class method *definitions* that raise ``AssertionError`` on purpose
    are excluded — they are regression guards, not calls.
    """

    offenders: list[str] = []
    for path in TEST_FILE_RAW_LIST_TASK_ROWS_TARGETS:
        if not path.is_file():
            continue
        offenders.extend(_test_file_raw_list_task_rows_call_violations(path))

    assert not offenders, (
        "WS2 test-file observable read-model fence: "
        "Integration tests must use list_observable_task_rows() instead of "
        "the raw list_task_rows() so assertions validate the fact-overlaid "
        "projection. Mock-class method definitions (def list_task_rows) that "
        "guard against regression are allowed:\n" + "\n".join(offenders)
    )


def test_role_stats_fence_detects_direct_get_task_row_stats_call() -> None:
    """Characterization: the AST detection catches direct ``get_task_row_stats()`` calls.

    Uses ``ast.parse`` on a synthetic fragment to prove the detection
    helper would flag a direct attribute call to ``get_task_row_stats``
    if it appeared in a production role projection. Without this test the
    fence is purely negative and could silently stop detecting violations
    if the AST helper drifted.
    """

    fragment = "x = task_runtime.get_task_row_stats()\n"
    tree = ast.parse(fragment)
    detected = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "get_task_row_stats":
            detected = True
    assert detected, (
        "Characterization fence: AST detection must flag direct "
        "get_task_row_stats() attribute calls. If this test fails, the "
        "role-projection fence can silently stop detecting violations."
    )


def test_role_stats_fence_detects_getattr_pattern() -> None:
    """Characterization: the AST detection catches ``getattr(..., "get_task_row_stats", ...)`` calls.

    Uses ``ast.parse`` on a synthetic fragment to prove the detection
    helper would flag a dynamic getattr-based read of ``get_task_row_stats``
    if it appeared in a production role projection. Without this test the
    fence could silently stop detecting the ``getattr`` pattern used by
    ``DirectorStateTracker.build_taskboard_observation_snapshot``.
    """

    fragment = (
        'getter = getattr(task_runtime, "get_task_row_stats", None)\nstats = getter() if callable(getter) else {}\n'
    )
    tree = ast.parse(fragment)
    detected = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "getattr"):
            continue
        if len(node.args) >= 2:
            arg1 = node.args[1]
            if isinstance(arg1, ast.Constant) and arg1.value == "get_task_row_stats":
                detected = True
    assert detected, (
        "Characterization fence: AST detection must flag "
        'getattr(..., "get_task_row_stats", ...) calls. If this test '
        "fails, the role-projection fence can silently stop detecting "
        "the dynamic getattr pattern used in state_tracking.py."
    )


def test_role_stats_fence_allows_get_observable_task_row_stats() -> None:
    """Characterization: the AST detection does NOT flag ``get_observable_task_row_stats()``.

    Uses ``ast.parse`` on a synthetic fragment to prove the detection
    helper accepts the correct API name. Without this test the fence
    could over-reject and block the production-allowed
    ``get_observable_task_row_stats`` pattern.
    """

    fragment = "x = task_runtime.get_observable_task_row_stats()\n"
    tree = ast.parse(fragment)
    offender_count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "get_task_row_stats":
            offender_count += 1
    assert offender_count == 0, (
        "Characterization fence: AST detection must NOT flag "
        "get_observable_task_row_stats() calls. The fence should only "
        "block get_task_row_stats(), not the observable API."
    )
