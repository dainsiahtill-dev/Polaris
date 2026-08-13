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
from collections.abc import Iterable, Set as AbstractSet
from pathlib import Path
from typing import Any

from polaris.cells.runtime.task_runtime.public.contracts import TASK_RUNTIME_EXECUTION_STREAM_V1

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
DIRECTOR_EXECUTE_METHOD = POLARIS_ROOT / "cells" / "roles" / "adapters" / "internal" / "director" / "execute_method.py"
KERNEL_TRANSACTION_FACTORY = (
    POLARIS_ROOT / "cells" / "roles" / "kernel" / "internal" / "kernel" / "transaction_factory.py"
)
KERNEL_DIRECTED_EFFECT_LIFECYCLE = (
    POLARIS_ROOT / "cells" / "roles" / "kernel" / "internal" / "directed_effect_lifecycle.py"
)
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
    "polaris/cells/factory/pipeline/internal/factory_stage_executor.py",
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
        "polaris/cells/factory/pipeline/internal/factory_stage_executor.py",
        "polaris/cells/roles/adapters/internal/director/execute_method.py",
        "polaris/cells/roles/runtime/public/cli_runner.py",
        "polaris/cells/roles/runtime/internal/worker_pool.py",
        "polaris/delivery/cli/pm/engine/taskboard.py",
    },
    "fail_execution": {
        "polaris/cells/roles/adapters/internal/director/execute_method.py",
        "polaris/cells/roles/runtime/public/cli_runner.py",
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
TASK_RUNTIME_RETIRED_TERMINAL_METHODS = frozenset(
    {
        "complete_execution",
        "fail_execution",
        "suspend_execution",
    }
)
TASK_RUNTIME_TYPED_SETTLEMENT_CONTRACT = "SettleTaskRuntimeExecutionAttemptCommandV1"
TASK_RUNTIME_TYPED_SETTLEMENT_SERVICE = "settle_task_runtime_execution_attempt"
TASK_RUNTIME_PUBLIC_CONTRACTS = TASK_RUNTIME_OWNER / "public" / "contracts.py"
TASK_RUNTIME_PUBLIC_SERVICE = TASK_RUNTIME_OWNER / "public" / "service.py"
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
    ("cancel_task_row_for_factory_abort", "update"): 1,
    ("cancel_task_row_for_deduplication", "update"): 1,
    ("claim_execution", "update"): 1,
    ("_project_settled_execution_attempt_locked", "update"): 2,
    ("fail_task_row_after_rework_exhausted", "update"): 1,
    ("fail_task_row_from_role_adapter", "update"): 1,
    ("_heartbeat_execution_attempt_locked", "update"): 1,
    ("heartbeat_execution", "update"): 1,
    ("refresh_dependency_unblocks", "update"): 2,
    ("force_fail_active_session_for_factory_abort", "update"): 1,
    ("suspend_active_executions_for_run", "update"): 1,
    ("fence_expired_factory_run_sessions", "update"): 1,
}
REVIEWED_TASK_RUNTIME_SERVICE_BOARD_READS = {
    ("_task_entity_for_dependency_side_effect", "get"): 1,
    ("_list_file_task_entities", "list_all"): 1,
    ("_task_entity_for_transition", "get"): 1,
    ("_task_entity_for_owner_terminal_transition", "get"): 1,
    ("_task_entity_for_claim_execution", "get"): 1,
    ("_task_entity_for_terminal_session_reconcile", "get"): 1,
    ("_project_settled_execution_attempt_locked", "get"): 1,
}
TASK_RUNTIME_SERVICE_RAW_BOARD_LIST_HELPER = "_list_file_task_entities"
TASK_RUNTIME_SERVICE_CLAIM_EXECUTION_ENTITY_HELPER = "_task_entity_for_claim_execution"
TASK_RUNTIME_SERVICE_CLAIM_EXECUTION_ENTITY_CONSUMERS = frozenset({"claim_execution"})
TASK_RUNTIME_SERVICE_EXECUTION_ENTITY_HELPER = "_task_entity_for_transition"
TASK_RUNTIME_SERVICE_EXECUTION_ENTITY_CONSUMERS = frozenset(
    {
        "settle_execution_attempt",
    }
)
TASK_RUNTIME_SERVICE_OWNER_TERMINAL_ENTITY_HELPER = "_task_entity_for_owner_terminal_transition"
TASK_RUNTIME_SERVICE_OWNER_TERMINAL_ENTITY_CONSUMERS = frozenset(
    {
        "cancel_task_row_for_factory_abort",
        "cancel_task_row_for_deduplication",
        "fail_task_row_from_role_adapter",
        "force_fail_active_session_for_factory_abort",
    }
)
TASK_RUNTIME_SERVICE_DEPENDENCY_FANOUT_ENTITY_HELPER = "_task_entity_for_dependency_side_effect"
TASK_RUNTIME_SERVICE_DEPENDENCY_FANOUT_ENTITY_CONSUMERS = frozenset(
    {
        "_apply_reopen_downstream_reblocks",
        "_apply_reverse_dependency_links",
    }
)
TASK_RUNTIME_SERVICE_TERMINAL_SESSION_RECONCILE_ENTITY_HELPER = "_task_entity_for_terminal_session_reconcile"
TASK_RUNTIME_SERVICE_TERMINAL_SESSION_RECONCILE_ENTITY_CONSUMERS = frozenset(
    {
        "_apply_terminal_session_reconcile",
        "_terminal_projection_can_restore_pending_intent_locked",
    }
)
TASK_RUNTIME_SERVICE_RAW_BOARD_ENTITY_CONSUMERS = frozenset(
    {
        "_list_file_task_rows",
        "refresh_dependency_unblocks",
        "_reset_records_authorized",
        "reset_task_rows_for_reexecution",
        "suspend_active_executions_for_run",
    }
)
TASK_RUNTIME_SERVICE_EXPECTED_RAW_BOARD_ENTITY_CONSUMERS = frozenset(
    {
        "_list_file_task_rows",
        "refresh_dependency_unblocks",
        "_reset_records_authorized",
        "reset_task_rows_for_reexecution",
        "suspend_active_executions_for_run",
    }
)
TASK_RUNTIME_SERVICE_RAW_BOARD_ENTITY_OWNER_REQUIREMENTS = {
    "_reset_records_authorized": frozenset(
        {
            "self._append_execution_event",
        }
    ),
    "refresh_dependency_unblocks": frozenset(
        {
            "self._append_execution_event",
            "self._board.update",
            "self._fact_overlaid_dependency_status_rows",
        }
    ),
    "reset_task_rows_for_reexecution": frozenset(
        {
            "self._append_execution_event",
            "self._replace_task_row_for_reexecution",
        }
    ),
    "suspend_active_executions_for_run": frozenset(
        {
            "self._append_execution_event",
            "self._board.update",
            "self._suspend_active_session_for_run_locked",
        }
    ),
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
TASKBOARD_ROW_WRITE_RECEIPT_ANCHORS = {
    "_last_row_write_receipt",
    "TaskBoardRowWriteReceipt",
}
TASKBOARD_CURRENT_ROW_HASH_NAMES = {"current", "current_hash"}
TASK_RUNTIME_ROW_WRITE_RECEIPT_DETAILS_HELPER_PREFERRED_NAME = "_row_write_receipt_details_for_task"
TASK_RUNTIME_SESSION_WRITE_RECEIPT_DETAILS_HELPER_PREFERRED_NAME = "_session_write_receipt_details_for_session"
TASK_RUNTIME_ROW_WRITE_RECEIPT_KEYED_LOOKUP_METHOD = "row_write_receipt_for_task"
TASK_RUNTIME_SESSION_WRITE_RECEIPT_KEYED_LOOKUP_METHOD = "_session_write_receipt_for_session"
TASK_RUNTIME_SESSION_WRITE_RECEIPT_DETAILS_KEY = "session_write_receipt"
TASKBOARD_KERNEL_WRITE_TEXT_NON_ROW_ALLOWLIST = {"_save_max_id"}
TASKBOARD_DIRECT_ROW_WRITE_METHODS = {
    "open",
    "write_bytes",
    "write_text",
}
TASKBOARD_DIRECT_ROW_REPLACE_METHODS = {
    "rename",
    "replace",
}
TASKBOARD_FILE_LOCK_METHOD = "self._file_lock"
TASKBOARD_ROW_LOCK_PATH_HELPERS = {"self._task_row_lock_path"}
TASKBOARD_TASK_ID_LOCK_NAMES = {"task_id"}


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
        if call == "AppendFactEventCommandV1" and _contains_string_literal(node, TASK_RUNTIME_EXECUTION_STREAM_V1):
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
            TASK_RUNTIME_EXECUTION_STREAM_V1,
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


def _direct_self_board_get_calls(method_def: ast.FunctionDef) -> list[ast.Call]:
    return [
        node
        for node in _walk_task_runtime_method_body(method_def)
        if isinstance(node, ast.Call) and _call_name(node.func) == "self._board.get"
    ]


def _method_body_directly_calls_self_method(method_def: ast.FunctionDef, method_name: str) -> bool:
    return any(
        isinstance(node, ast.Call) and _call_is_self_method(node, method_name)
        for node in _walk_task_runtime_method_body(method_def)
    )


def _direct_self_method_calls(method_def: ast.FunctionDef, method_name: str) -> list[ast.Call]:
    return [
        node
        for node in _walk_task_runtime_method_body(method_def)
        if isinstance(node, ast.Call) and _call_is_self_method(node, method_name)
    ]


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


def _function_definition(path: Path, function_name: str) -> ast.FunctionDef | None:
    """Return one module-level function definition by name."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return node
    return None


def _module_defines_symbol(path: Path, symbol: str) -> bool:
    """Return whether a public module declares the required symbol."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == symbol for node in tree.body)


def _function_uses_typed_public_settlement(path: Path, function_name: str) -> list[str]:
    """Verify the PM caller flows retained identity through public settlement."""

    function = _function_definition(path, function_name)
    if function is None:
        return [f"{path.relative_to(BACKEND_ROOT)}:{function_name}() is missing"]
    assignments = [node for node in ast.walk(function) if isinstance(node, ast.Assign)]
    retained_identity_names = {
        target.elts[0].id
        for assignment in assignments
        if isinstance(assignment.value, ast.Call)
        and isinstance(assignment.value.func, ast.Name)
        and assignment.value.func.id == "_task_runtime_terminal_identity"
        for target in assignment.targets
        if isinstance(target, ast.Tuple) and target.elts and isinstance(target.elts[0], ast.Name)
    }
    if not retained_identity_names:
        return [
            f"{path.relative_to(BACKEND_ROOT)}:{function_name}() must retain identity from "
            "_task_runtime_terminal_identity()"
        ]

    constructors = [
        assignment
        for assignment in assignments
        if len(assignment.targets) == 1
        and isinstance(assignment.targets[0], ast.Name)
        and isinstance(assignment.value, ast.Call)
        and isinstance(assignment.value.func, ast.Name)
        and assignment.value.func.id == TASK_RUNTIME_TYPED_SETTLEMENT_CONTRACT
    ]
    required_keywords = {"workspace", "identity", "outcome", "summary", "metadata"}
    if not constructors:
        return [
            f"{path.relative_to(BACKEND_ROOT)}:{function_name}() must construct "
            f"{TASK_RUNTIME_TYPED_SETTLEMENT_CONTRACT}"
        ]
    valid_command_names: set[str] = set()
    for assignment in constructors:
        constructor = assignment.value
        assert isinstance(constructor, ast.Call)
        keywords = {keyword.arg: keyword.value for keyword in constructor.keywords if keyword.arg}
        if not required_keywords <= set(keywords):
            continue
        identity_value = keywords["identity"]
        if not isinstance(identity_value, ast.Name) or identity_value.id not in retained_identity_names:
            continue
        target = assignment.targets[0]
        assert isinstance(target, ast.Name)
        valid_command_names.add(target.id)
    if not valid_command_names:
        return [
            f"{path.relative_to(BACKEND_ROOT)}:{function_name}() must construct a complete typed settlement "
            "command whose identity is returned by _task_runtime_terminal_identity()"
        ]
    service_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == TASK_RUNTIME_TYPED_SETTLEMENT_SERVICE
    ]
    if not any(
        len(call.args) == 1 and isinstance(call.args[0], ast.Name) and call.args[0].id in valid_command_names
        for call in service_calls
    ):
        return [
            f"{path.relative_to(BACKEND_ROOT)}:{function_name}() must pass its typed settlement command variable "
            f"to {TASK_RUNTIME_TYPED_SETTLEMENT_SERVICE}()"
        ]
    return []


def _retired_terminal_caller_violations(path: Path) -> list[str]:
    """Return direct legacy terminal calls outside the TaskRuntime owner cell."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in TASK_RUNTIME_RETIRED_TERMINAL_METHODS:
            offenders.append(
                f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} directly calls retired terminal method "
                f"{node.func.attr}()"
            )
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


def test_production_terminal_callers_use_typed_public_settlement() -> None:
    """WS2-A: terminal PM paths use the public typed settlement contract only."""

    assert _module_defines_symbol(TASK_RUNTIME_PUBLIC_CONTRACTS, TASK_RUNTIME_TYPED_SETTLEMENT_CONTRACT)
    assert _module_defines_symbol(TASK_RUNTIME_PUBLIC_SERVICE, TASK_RUNTIME_TYPED_SETTLEMENT_SERVICE)

    offenders = _function_uses_typed_public_settlement(
        DELIVERY_PM_TASKBOARD,
        "_finalize_taskboard_runtime_entry",
    )
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts or "tests" in path.parts:
            continue
        if _is_allowed_owner_path(path):
            continue
        offenders.extend(_retired_terminal_caller_violations(path))

    assert not offenders, (
        "Production terminal callers must construct SettleTaskRuntimeExecutionAttemptCommandV1 and call "
        "settle_task_runtime_execution_attempt(); direct legacy terminal methods are not an allowed boundary:\n"
        + "\n".join(offenders)
    )


def test_ws2_b2_execution_attempt_authority_stays_task_runtime_public() -> None:
    """Director and Kernel share one public authority without a private holder."""

    offenders: list[str] = []
    for path in (DIRECTOR_ADAPTER, DIRECTOR_EXECUTE_METHOD, KERNEL_TRANSACTION_FACTORY):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        public_authority_import = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                imported = {alias.name for alias in node.names}
                if path in {DIRECTOR_ADAPTER, DIRECTOR_EXECUTE_METHOD} and module.startswith(
                    "polaris.cells.roles.kernel.internal"
                ):
                    offenders.append(f"{rel}:{node.lineno} imports roles.kernel.internal")
                if (
                    module == "polaris.cells.runtime.task_runtime.public"
                    and "TaskRuntimeExecutionAttemptAuthorityV1" in imported
                ):
                    public_authority_import = True
            elif isinstance(node, ast.ClassDef):
                normalized_name = node.name.lower().replace("_", "")
                if "holder" in normalized_name and (
                    "executionattempt" in normalized_name or "authority" in normalized_name
                ):
                    offenders.append(f"{rel}:{node.lineno} declares private execution-attempt holder {node.name}")
        if path in {DIRECTOR_EXECUTE_METHOD, KERNEL_TRANSACTION_FACTORY} and not public_authority_import:
            offenders.append(f"{rel} does not import TaskRuntimeExecutionAttemptAuthorityV1 from TaskRuntime public")

    director_entry_tree = ast.parse(DIRECTOR_ADAPTER.read_text(encoding="utf-8"))
    director_entry = next(
        (
            node
            for node in ast.walk(director_entry_tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "execute"
        ),
        None,
    )
    if director_entry is None:
        offenders.append("DirectorAdapter.execute() is missing")
    elif (
        sum(
            1
            for node in ast.walk(director_entry)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "execute_director_task"
        )
        != 1
    ):
        offenders.append("DirectorAdapter.execute() must delegate exactly once to its Director task entry")

    claim_source = DIRECTOR_EXECUTE_METHOD.read_text(encoding="utf-8")
    claim_tree = ast.parse(claim_source)
    factory_calls = [
        node
        for node in ast.walk(claim_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_task_runtime_execution_attempt_authority"
    ]
    if len(factory_calls) != 1:
        offenders.append("Director claim path must create exactly one public execution-attempt authority")
    authority_context_assignments = [
        node
        for node in ast.walk(claim_tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Name)
        and node.value.id == "task_execution_attempt_authority"
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "task_runtime_execution_attempt_authority"
            for target in node.targets
        )
    ]
    if len(authority_context_assignments) != 1:
        offenders.append("Director claim path must pass its public authority through request context exactly once")

    finalize = _function_definition(DIRECTOR_EXECUTE_METHOD, "_finalize_claimed_execution")
    if finalize is None:
        offenders.append("_finalize_claimed_execution() is missing")
    else:
        calls = [
            node for node in ast.walk(finalize) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        if not any(
            isinstance(node.func.value, ast.Name) and node.func.value.id == "authority" and node.func.attr == "settle"
            for node in calls
        ):
            offenders.append("_finalize_claimed_execution() must settle through the public authority")
        if any(node.func.attr == "settle_execution_attempt" for node in calls):
            offenders.append("_finalize_claimed_execution() bypasses authority.settle()")

    for helper_name in ("_finalize_claimed_execution", "_suspend_claimed_execution_for_cancellation"):
        helper_calls = [
            node
            for node in ast.walk(claim_tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == helper_name
        ]
        if not helper_calls:
            offenders.append(f"{helper_name}() is not reachable from the Director claim path")
            continue
        if any(
            not any(
                keyword.arg == "authority"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "task_execution_attempt_authority"
                for keyword in call.keywords
            )
            for call in helper_calls
        ):
            offenders.append(f"{helper_name}() must receive the single public claim authority")

    guard = _function_definition(KERNEL_TRANSACTION_FACTORY, "_assert_task_runtime_guard_allows_tool")
    if guard is None:
        offenders.append("_assert_task_runtime_guard_allows_tool() is missing")
    else:
        calls = [
            node for node in ast.walk(guard) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        direct_authority_heartbeat = any(
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "authority"
            and node.func.attr == "heartbeat"
            for node in calls
        )
        delegated_refresh = any(
            isinstance(node.func, ast.Name) and node.func.id == "_refresh_directed_effect_attempt"
            for node in ast.walk(guard)
            if isinstance(node, ast.Call)
        )
        refresh = _function_definition(KERNEL_DIRECTED_EFFECT_LIFECYCLE, "_refresh_directed_effect_attempt")
        refresh_heartbeats_authority = refresh is not None and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "authority"
            and node.func.attr == "heartbeat"
            for node in ast.walk(refresh)
        )
        if not direct_authority_heartbeat and not (delegated_refresh and refresh_heartbeats_authority):
            offenders.append("Kernel tool guard must heartbeat through the public authority")
        if any(node.func.attr == "heartbeat_task_runtime_execution_attempt" for node in calls):
            offenders.append("Kernel tool guard bypasses authority.heartbeat()")

    assert not offenders, "WS2-B2 TaskRuntime public authority fence:\n" + "\n".join(offenders)


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
        if path == POLARIS_ROOT / "cells" / "events" / "fact_stream" / "public" / "catalog.py":
            continue
        if TASKBOARD_TERMINAL_EVENT_STREAM in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(BACKEND_ROOT)))

    assert not offenders, (
        "`taskboard.terminal.events` is a task_runtime-owned compatibility "
        "projection, not an execution-control fact source. Production code "
        "outside task_runtime must consume TaskRuntimeService / execution "
        "ledger projections instead:\n" + "\n".join(offenders)
    )


def test_taskboard_row_json_writes_stay_behind_save_task_and_replace_helper() -> None:
    """WS2 row-write fence for TaskBoard row JSON persistence.

    Raw TaskBoard row persistence has two responsibilities:
    ``_save_task`` serializes one task row and stages it via
    ``KernelFileSystem.write_text``; ``_replace_task_file`` owns the atomic
    ``os.replace`` commit/retry loop. Other TaskBoard methods may decide row
    mutations, but they must not grow independent JSON write paths.
    """

    offenders = _taskboard_row_write_boundary_violations()

    assert not offenders, (
        "WS2 TaskBoard row-write fence: task row JSON persistence must stay "
        "behind TaskBoard._save_task(), with durable replace isolated in "
        "TaskBoard._replace_task_file(). Offenders:\n" + "\n".join(offenders)
    )


def test_taskboard_save_task_updates_row_write_receipt_after_commit() -> None:
    """WS2 row-write receipt fence for ``TaskBoard._save_task``.

    The row JSON write path needs a local receipt anchor so future callers can
    bind row-file persistence to execution/ledger evidence instead of relying
    on a silent file write. The receipt update must happen after the staged
    write and replace call; a pre-commit receipt would be misleading when the
    replace fails.
    """

    offenders = _taskboard_save_task_row_write_receipt_violations()

    assert not offenders, (
        "WS2 TaskBoard row-write receipt fence: TaskBoard._save_task() must "
        "construct or update a row-write receipt anchor after committing the "
        "row JSON write. Expected anchor names include "
        f"{sorted(TASKBOARD_ROW_WRITE_RECEIPT_ANCHORS)}. Offenders:\n" + "\n".join(offenders)
    )


def test_taskboard_save_task_wraps_row_replace_in_file_lock() -> None:
    """WS2 row-write file-lock fence for ``TaskBoard._save_task``.

    ``_save_task`` is the only TaskBoard method allowed to commit row JSON.
    Its durable replace and receipt publication must be guarded by a
    cross-process file lock scoped to the one task row being written. A
    process-local transaction lock is not enough, and a global board file lock
    would serialize unrelated rows while still obscuring which row owns the
    write receipt.
    """

    offenders = _taskboard_save_task_row_file_lock_violations()

    assert not offenders, (
        "WS2 TaskBoard row-write file-lock fence: TaskBoard._save_task() "
        "must wrap self._replace_task_file() and the row-write receipt update "
        "in the same with self._file_lock(<per-task-row-lock-path>) body. "
        "The lock path must derive from task.id/task_id or "
        "self._task_row_lock_path(task.id), not a global board lock. "
        "Offenders:\n" + "\n".join(offenders)
    )


def test_taskboard_save_task_checks_current_row_hash_before_replace() -> None:
    """WS2 local-CAS fence for ``TaskBoard._save_task`` row replacement.

    ``_save_task`` stages the next row payload before the durable replace.
    That gap must not blindly overwrite a concurrently changed row: after
    writing the temp file, the method must reread the current row hash,
    compare it with ``before_hash``, and raise on mismatch before calling
    ``_replace_task_file``. Receipt publication remains a post-replace
    concern and is covered by the row-write receipt fence above.
    """

    offenders = _taskboard_save_task_local_cas_violations()

    assert not offenders, (
        "WS2 TaskBoard local CAS fence: TaskBoard._save_task() must guard "
        "the row replace with a current-hash check between temp-file staging "
        "and self._replace_task_file(). Offenders:\n" + "\n".join(offenders)
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
        "execution fact source. Use the typed TaskRuntime observable projection "
        "so task_runtime.execution facts and their authority remain explicit:\n" + "\n".join(offenders)
    )
    assert "query_observable_task_rows_projection" in source, (
        "Factory stage executor must consume the typed TaskRuntime observable "
        "projection rather than a provenance-free row list."
    )
    assert ".authoritative" in source, (
        "Factory completion control flow must explicitly require an authoritative "
        "TaskRuntime projection before treating task rows as execution facts."
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


def test_claim_execution_routes_task_entity_read_through_claim_helper() -> None:
    """WS2 claim entity-read fence.

    ``claim_execution()`` needs the raw ``Task`` entity before it can create
    or renew a lease-backed execution session. That raw owner-cell read must
    stay centralized in ``_task_entity_for_claim_execution()`` so claim
    normalization, missing-row semantics, and future trace/log enrichment
    have a single audited bridge.
    """

    methods = _task_runtime_service_method_defs()
    helper_name = TASK_RUNTIME_SERVICE_CLAIM_EXECUTION_ENTITY_HELPER
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)
    required_methods = TASK_RUNTIME_SERVICE_CLAIM_EXECUTION_ENTITY_CONSUMERS | {helper_name}
    missing = sorted(required_methods.difference(methods))

    assert not missing, f"Missing expected TaskRuntimeService methods: {missing}"

    helper_get_calls = _direct_self_board_get_calls(methods[helper_name])
    direct_get_offenders: list[str] = []
    missing_helper_offenders: list[str] = []
    unauthorized_helper_consumers: list[str] = []

    for method_name in sorted(TASK_RUNTIME_SERVICE_CLAIM_EXECUTION_ENTITY_CONSUMERS):
        method_def = methods[method_name]
        direct_get_offenders.extend(
            f"{rel}:{call.lineno} TaskRuntimeService.{method_name}() calls self._board.get() directly"
            for call in _direct_self_board_get_calls(method_def)
        )
        helper_calls = _direct_self_method_calls(method_def, helper_name)
        if not helper_calls:
            missing_helper_offenders.append(f"TaskRuntimeService.{method_name}()")

    for method_name, method_def in sorted(methods.items()):
        if method_name == helper_name or method_name in TASK_RUNTIME_SERVICE_CLAIM_EXECUTION_ENTITY_CONSUMERS:
            continue
        for call in _direct_self_method_calls(method_def, helper_name):
            unauthorized_helper_consumers.append(
                f"{rel}:{call.lineno} TaskRuntimeService.{method_name}() calls self.{helper_name}()"
            )

    assert len(helper_get_calls) == 1, (
        f"TaskRuntimeService.{helper_name}() must be the single direct "
        "self._board.get() bridge for claim raw entity reads; "
        f"found {len(helper_get_calls)} direct calls."
    )
    assert not direct_get_offenders, (
        "TaskRuntimeService.claim_execution() must not call "
        f"self._board.get() directly; route raw Task entity reads through "
        f"self.{helper_name}() so claim normalization and missing-row "
        "semantics remain centralized. Offenders:\n" + "\n".join(direct_get_offenders)
    )
    assert not missing_helper_offenders, (
        "TaskRuntimeService.claim_execution() must call "
        f"self.{helper_name}() instead of owning raw TaskBoard.get() reads "
        "itself. Offenders:\n" + "\n".join(missing_helper_offenders)
    )
    assert not unauthorized_helper_consumers, (
        f"TaskRuntimeService.{helper_name}() is the reviewed raw Task entity "
        "bridge only for claim_execution(). New consumers must be explicitly "
        "reviewed in TASK_RUNTIME_SERVICE_CLAIM_EXECUTION_ENTITY_CONSUMERS. "
        "Offenders:\n" + "\n".join(unauthorized_helper_consumers)
    )


def test_execution_transition_methods_route_task_entity_reads_through_helper() -> None:
    """WS2 execution-transition entity-read fence.

    ``settle_execution_attempt()`` reads the owner Task row once before its
    double-locked session commit. The later TaskBoard idempotence lookup is
    owned by ``_project_settled_execution_attempt_locked()`` under a separate
    projection lock. The pre-commit raw owner-cell read must stay centralized
    in ``_task_entity_for_transition()`` so settlement cannot quietly grow a
    second normalization or missing-row path.
    """

    methods = _task_runtime_service_method_defs()
    helper_name = TASK_RUNTIME_SERVICE_EXECUTION_ENTITY_HELPER
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)
    required_methods = TASK_RUNTIME_SERVICE_EXECUTION_ENTITY_CONSUMERS | {helper_name}
    missing = sorted(required_methods.difference(methods))

    assert not missing, f"Missing expected TaskRuntimeService methods: {missing}"

    helper_get_calls = _direct_self_board_get_calls(methods[helper_name])
    direct_get_offenders: list[str] = []
    missing_helper_offenders: list[str] = []

    for method_name in sorted(TASK_RUNTIME_SERVICE_EXECUTION_ENTITY_CONSUMERS):
        method_def = methods[method_name]
        direct_get_offenders.extend(
            f"{rel}:{call.lineno} TaskRuntimeService.{method_name}() calls self._board.get() directly"
            for call in _direct_self_board_get_calls(method_def)
        )
        if not _method_body_directly_calls_self_method(method_def, helper_name):
            missing_helper_offenders.append(f"TaskRuntimeService.{method_name}()")

    assert len(helper_get_calls) == 1, (
        f"TaskRuntimeService.{helper_name}() must be the single direct "
        f"self._board.get() bridge for execution transitions; found "
        f"{len(helper_get_calls)} direct calls."
    )
    assert not direct_get_offenders, (
        "Execution transition methods must not call self._board.get() "
        f"directly; route raw Task entity reads through self.{helper_name}() "
        "so normalization and fallback semantics remain centralized. "
        "Offenders:\n" + "\n".join(direct_get_offenders)
    )
    assert not missing_helper_offenders, (
        "Execution settlement methods that need the pre-commit raw Task entity "
        f"must call self.{helper_name}() instead of owning raw TaskBoard.get() "
        "reads themselves. Offenders:\n" + "\n".join(missing_helper_offenders)
    )


def test_owner_terminal_transition_methods_route_task_entity_reads_through_helper() -> None:
    """WS2 owner-terminal row transition entity-read fence.

    ``cancel_task_row_for_deduplication()`` and
    ``fail_task_row_from_role_adapter()`` are owner-cell terminal row
    transitions that can run without a Director execution lease. They need one
    raw ``Task`` pre-read to preserve missing-row ``None`` semantics, but that
    direct ``TaskBoard.get`` boundary must stay centralized in
    ``_task_entity_for_owner_terminal_transition()`` so future CAS/version
    checks, normalization, and traceable missing-row handling have one owner.
    """

    methods = _task_runtime_service_method_defs()
    helper_name = TASK_RUNTIME_SERVICE_OWNER_TERMINAL_ENTITY_HELPER
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)
    required_methods = TASK_RUNTIME_SERVICE_OWNER_TERMINAL_ENTITY_CONSUMERS | {helper_name}
    missing = sorted(required_methods.difference(methods))

    assert not missing, f"Missing expected TaskRuntimeService methods: {missing}"

    helper_get_calls = _direct_self_board_get_calls(methods[helper_name])
    direct_get_offenders: list[str] = []
    missing_helper_offenders: list[str] = []
    unauthorized_helper_consumers: list[str] = []

    for method_name in sorted(TASK_RUNTIME_SERVICE_OWNER_TERMINAL_ENTITY_CONSUMERS):
        method_def = methods[method_name]
        direct_get_offenders.extend(
            f"{rel}:{call.lineno} TaskRuntimeService.{method_name}() calls self._board.get() directly"
            for call in _direct_self_board_get_calls(method_def)
        )
        if not _method_body_directly_calls_self_method(method_def, helper_name):
            missing_helper_offenders.append(f"TaskRuntimeService.{method_name}()")

    for method_name, method_def in sorted(methods.items()):
        if method_name == helper_name or method_name in TASK_RUNTIME_SERVICE_OWNER_TERMINAL_ENTITY_CONSUMERS:
            continue
        if _method_body_directly_calls_self_method(method_def, helper_name):
            unauthorized_helper_consumers.append(f"TaskRuntimeService.{method_name}()")

    assert len(helper_get_calls) == 1, (
        f"TaskRuntimeService.{helper_name}() must be the single direct "
        "self._board.get() bridge for owner-cell terminal row transitions; "
        f"found {len(helper_get_calls)} direct calls."
    )
    assert not direct_get_offenders, (
        "Owner-cell terminal row transitions must not call self._board.get() "
        f"directly; route raw Task entity reads through self.{helper_name}() "
        "so normalization, missing-row semantics, and future CAS/version checks "
        "remain centralized. Offenders:\n" + "\n".join(direct_get_offenders)
    )
    assert not missing_helper_offenders, (
        "Owner-cell terminal row transitions that need raw Task entity pre-read "
        f"must call self.{helper_name}() instead of owning raw TaskBoard.get() "
        "reads themselves. Offenders:\n" + "\n".join(missing_helper_offenders)
    )
    assert not unauthorized_helper_consumers, (
        f"TaskRuntimeService.{helper_name}() is the reviewed raw Task entity "
        "bridge only for dedup cancellation and role-adapter failure terminal "
        "row transitions. New consumers must be explicitly reviewed in "
        "TASK_RUNTIME_SERVICE_OWNER_TERMINAL_ENTITY_CONSUMERS. Offenders:\n" + "\n".join(unauthorized_helper_consumers)
    )


def test_dependency_fanout_methods_route_task_entity_reads_through_helper() -> None:
    """WS2 dependency fan-out raw entity-read fence.

    ``_apply_reverse_dependency_links()`` and
    ``_apply_reopen_downstream_reblocks()`` perform cross-row dependency
    mutations after create/reopen paths. They may need raw ``Task`` entities
    for the row being mutated, but the direct ``TaskBoard.get`` boundary must
    stay centralized in ``_task_entity_for_dependency_side_effect()`` so dependency
    fan-out keeps one audited raw-read bridge with one normalization and
    missing-row policy.
    """

    methods = _task_runtime_service_method_defs()
    helper_name = TASK_RUNTIME_SERVICE_DEPENDENCY_FANOUT_ENTITY_HELPER
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)
    required_methods = TASK_RUNTIME_SERVICE_DEPENDENCY_FANOUT_ENTITY_CONSUMERS | {helper_name}
    missing = sorted(required_methods.difference(methods))

    assert not missing, f"Missing expected TaskRuntimeService methods: {missing}"

    helper_get_calls = _direct_self_board_get_calls(methods[helper_name])
    direct_get_offenders: list[str] = []
    missing_helper_offenders: list[str] = []
    unauthorized_helper_consumers: list[str] = []

    for method_name in sorted(TASK_RUNTIME_SERVICE_DEPENDENCY_FANOUT_ENTITY_CONSUMERS):
        method_def = methods[method_name]
        direct_get_offenders.extend(
            f"{rel}:{call.lineno} TaskRuntimeService.{method_name}() calls self._board.get() directly"
            for call in _direct_self_board_get_calls(method_def)
        )
        helper_calls = _direct_self_method_calls(method_def, helper_name)
        if not helper_calls:
            missing_helper_offenders.append(f"TaskRuntimeService.{method_name}()")

    for method_name, method_def in sorted(methods.items()):
        if method_name == helper_name or method_name in TASK_RUNTIME_SERVICE_DEPENDENCY_FANOUT_ENTITY_CONSUMERS:
            continue
        for call in _direct_self_method_calls(method_def, helper_name):
            unauthorized_helper_consumers.append(
                f"{rel}:{call.lineno} TaskRuntimeService.{method_name}() calls self.{helper_name}()"
            )

    assert len(helper_get_calls) == 1, (
        f"TaskRuntimeService.{helper_name}() must be the single direct "
        "self._board.get() bridge for dependency fan-out raw entity reads; "
        f"found {len(helper_get_calls)} direct calls."
    )
    assert not direct_get_offenders, (
        "Dependency fan-out methods must not call self._board.get() directly; "
        f"route raw Task entity reads through self.{helper_name}() so "
        "normalization, missing-row semantics, and future tracing stay "
        "centralized. Offenders:\n" + "\n".join(direct_get_offenders)
    )
    assert not missing_helper_offenders, (
        "Dependency fan-out methods that need raw Task entity reads must call "
        f"self.{helper_name}() instead of owning raw TaskBoard.get() reads "
        "themselves. Offenders:\n" + "\n".join(missing_helper_offenders)
    )
    assert not unauthorized_helper_consumers, (
        f"TaskRuntimeService.{helper_name}() is the reviewed raw Task entity "
        "bridge only for dependency fan-out helpers. New consumers must be "
        "explicitly reviewed in "
        "TASK_RUNTIME_SERVICE_DEPENDENCY_FANOUT_ENTITY_CONSUMERS. Offenders:\n"
        + "\n".join(unauthorized_helper_consumers)
    )


def test_terminal_session_reconcile_routes_task_entity_reads_through_helper() -> None:
    """WS2 terminal-session reconcile raw entity-read fence.

    ``_apply_terminal_session_reconcile()`` may need the raw ``Task`` entity
    when the session is non-terminal, when the normal update path rejects a
    terminal transition, or when ``TaskBoard.update`` returns ``None``. Those
    reads must stay centralized in
    ``_task_entity_for_terminal_session_reconcile()`` so terminal-session
    reconcile keeps one reviewed raw-read bridge without weakening the
    claim, dependency fan-out, or execution-transition helper fences.
    """

    methods = _task_runtime_service_method_defs()
    helper_name = TASK_RUNTIME_SERVICE_TERMINAL_SESSION_RECONCILE_ENTITY_HELPER
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT)
    required_methods = TASK_RUNTIME_SERVICE_TERMINAL_SESSION_RECONCILE_ENTITY_CONSUMERS | {helper_name}
    missing = sorted(required_methods.difference(methods))

    assert not missing, f"Missing expected TaskRuntimeService methods: {missing}"

    helper_get_calls = _direct_self_board_get_calls(methods[helper_name])
    direct_get_offenders: list[str] = []
    missing_helper_offenders: list[str] = []
    unauthorized_helper_consumers: list[str] = []

    for method_name in sorted(TASK_RUNTIME_SERVICE_TERMINAL_SESSION_RECONCILE_ENTITY_CONSUMERS):
        method_def = methods[method_name]
        direct_get_offenders.extend(
            f"{rel}:{call.lineno} TaskRuntimeService.{method_name}() calls self._board.get() directly"
            for call in _direct_self_board_get_calls(method_def)
        )
        helper_calls = _direct_self_method_calls(method_def, helper_name)
        if not helper_calls:
            missing_helper_offenders.append(f"TaskRuntimeService.{method_name}()")

    for method_name, method_def in sorted(methods.items()):
        if (
            method_name == helper_name
            or method_name in TASK_RUNTIME_SERVICE_TERMINAL_SESSION_RECONCILE_ENTITY_CONSUMERS
        ):
            continue
        for call in _direct_self_method_calls(method_def, helper_name):
            unauthorized_helper_consumers.append(
                f"{rel}:{call.lineno} TaskRuntimeService.{method_name}() calls self.{helper_name}()"
            )

    assert len(helper_get_calls) == 1, (
        f"TaskRuntimeService.{helper_name}() must be the single direct "
        "self._board.get() bridge for terminal-session reconcile raw entity reads; "
        f"found {len(helper_get_calls)} direct calls."
    )
    assert not direct_get_offenders, (
        "TaskRuntimeService._apply_terminal_session_reconcile() must not call "
        f"self._board.get() directly; route raw Task entity reads through "
        f"self.{helper_name}() so terminal-session reconcile keeps one audited "
        "owner-cell raw-read boundary. Offenders:\n" + "\n".join(direct_get_offenders)
    )
    assert not missing_helper_offenders, (
        "TaskRuntimeService._apply_terminal_session_reconcile() must call "
        f"self.{helper_name}() instead of owning raw TaskBoard.get() reads "
        "itself. Offenders:\n" + "\n".join(missing_helper_offenders)
    )
    assert not unauthorized_helper_consumers, (
        f"TaskRuntimeService.{helper_name}() is the reviewed raw Task entity "
        "bridge only for terminal-session reconcile. New consumers must be "
        "explicitly reviewed in "
        "TASK_RUNTIME_SERVICE_TERMINAL_SESSION_RECONCILE_ENTITY_CONSUMERS. Offenders:\n"
        + "\n".join(unauthorized_helper_consumers)
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


def test_find_terminal_session_snapshot_does_not_read_raw_taskboard() -> None:
    """WS2 terminal-session snapshot fence.

    ``_find_terminal_session_snapshot()`` reconciles an incoming terminal
    execution session against the persisted execution-session projection.
    It must not fall back to raw ``TaskBoard`` metadata reads because that
    bypasses the task_runtime execution read model and reintroduces a second
    source of truth for terminal session state.
    """

    methods = _task_runtime_service_method_defs()
    method_name = "_find_terminal_session_snapshot"
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
        "TaskRuntimeService._find_terminal_session_snapshot() must resolve "
        "terminal session snapshots from the execution-session projection, "
        "not by re-reading raw TaskBoard metadata through self._board.*(). "
        "Offenders:\n" + "\n".join(offenders)
    )


