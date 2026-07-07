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
from pathlib import Path

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
    ("_augment_task_row", "get"): 1,
    ("_dependent_rows_blocked_by", "list_all"): 1,
    ("_find_terminal_session_snapshot", "get"): 1,
    ("_get_task_by_external_task_id", "list_all"): 1,
    ("_task_has_unresolved_dependencies", "get"): 1,
    ("cancel_task_row_for_deduplication", "get"): 1,
    ("claim_execution", "get"): 1,
    ("complete_execution", "get"): 1,
    ("fail_execution", "get"): 1,
    ("fail_task_row_from_role_adapter", "get"): 1,
    ("get_task", "get"): 1,
    ("list_task_rows", "list_all"): 1,
    ("refresh_dependency_unblocks", "list_all"): 1,
    ("reset_task_rows_for_reexecution", "list_all"): 1,
    ("suspend_active_executions_for_run", "list_all"): 1,
    ("suspend_execution", "get"): 1,
    ("task_exists", "get"): 1,
}
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


def _function_def(path: Path, name: str) -> ast.FunctionDef:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(BACKEND_ROOT)}:{name}() not found")


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


def _string_literal(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


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
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "TaskRuntimePort":
            port_class = node
            break

    assert port_class is not None, "TaskRuntimePort protocol not found"
    port_methods = {item.name for item in port_class.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}

    assert "add_ready_listener" not in port_methods, (
        "TaskRuntimePort must not require live listener utilities. Worker wakeup "
        "may use an optional duck-typed add_ready_listener() optimization, but "
        "the required port contract should stay on ready-row reads and atomic "
        "claim/complete/fail execution transitions."
    )
    assert {"list_ready_task_rows", "claim_execution", "complete_execution", "fail_execution"} <= port_methods


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


def test_director_adapter_progress_does_not_finalize_task_rows() -> None:
    source = DIRECTOR_ADAPTER.read_text(encoding="utf-8")

    assert "_is_terminal_task_row_status(event_status)" in source, (
        "Director progress events may carry terminal-looking trace statuses, "
        "but TaskRow terminal writes must stay with TaskRuntimeService owner "
        "transitions."
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


def _list_task_rows_from_execution_facts_function_def() -> ast.FunctionDef:
    """Return the ``TaskRuntimeService.list_task_rows_from_execution_facts`` AST node."""

    source = TASK_RUNTIME_INTERNAL_SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parent_lookup(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != EXECUTION_FACT_LIST_READER:
            continue
        enclosing_class = parents.get(node)
        if isinstance(enclosing_class, ast.ClassDef) and enclosing_class.name == "TaskRuntimeService":
            return node
    raise AssertionError(
        f"TaskRuntimeService.{EXECUTION_FACT_LIST_READER}() not found in "
        f"{TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)}"
    )


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


def _fact_dict_assignments_in_loop(loop: ast.For | ast.AsyncFor) -> list[ast.AST]:
    """Return AST nodes inside ``loop`` that assign into a ``fact`` dict.

    Catches both ``fact["fact_event_seq"] = ...`` (ast.Assign on a Subscript)
    and ``fact.setdefault("fact_event_seq", ...)`` (ast.Call on a method).
    """

    result: list[ast.AST] = []
    for node in ast.walk(loop):
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
      1. Locate the ``for event in result.events:`` loop in the function.
      2. Within that loop, there must be an assignment (or ``setdefault``)
         of ``fact["fact_event_seq"]`` whose right-hand side reads the
         queried event's ``seq`` field (``event.get("seq")`` or
         ``event["seq"]``).
      3. The call to ``project_task_row_from_execution_fact_payload(fact)``
         must appear AFTER the ``fact_event_seq`` write in the same
         iteration, so the projector can see the propagated value.
    """

    function_def = _list_task_rows_from_execution_facts_function_def()
    loop = _iter_events_loop_in(function_def)
    if loop is None:
        return False, ["no `for event in result.events` loop found"]

    seq_assignments = _fact_dict_assignments_in_loop(loop)
    if not seq_assignments:
        return False, [
            "list_task_rows_from_execution_facts does not propagate fact_event_seq "
            "into the fact payload before projecting rows"
        ]

    projector_call: ast.Call | None = None
    for node in ast.walk(loop):
        if isinstance(node, ast.Call) and _call_name(node.func) == EXECUTION_FACT_READ_PROJECTOR:
            projector_call = node
            break

    if projector_call is None:
        return False, [
            f"list_task_rows_from_execution_facts does not call {EXECUTION_FACT_READ_PROJECTOR}() in the events loop"
        ]

    event_seq_names: set[str] = set()
    for node in ast.walk(loop):
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
