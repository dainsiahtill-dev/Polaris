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
    unauthorized_helper_consumers: list[str] = []
    for method_name in sorted(TASK_RUNTIME_SERVICE_RAW_BOARD_ENTITY_CONSUMERS):
        method_def = methods[method_name]
        direct_call_offenders.extend(
            f"{rel}:{call.lineno} TaskRuntimeService.{method_name}()"
            for call in _direct_self_board_list_all_calls(method_def)
        )
        if not _method_body_directly_calls_self_method(method_def, helper_name):
            missing_helper_offenders.append(f"TaskRuntimeService.{method_name}()")

    for method_name, method_def in sorted(methods.items()):
        if method_name == helper_name or method_name in TASK_RUNTIME_SERVICE_RAW_BOARD_ENTITY_CONSUMERS:
            continue
        for call in _direct_self_method_calls(method_def, helper_name):
            unauthorized_helper_consumers.append(
                f"{rel}:{call.lineno} TaskRuntimeService.{method_name}() calls self.{helper_name}()"
            )

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
    assert not unauthorized_helper_consumers, (
        f"TaskRuntimeService.{helper_name}() is the reviewed raw Task entity "
        "bridge only for the four owner/path methods in "
        "TASK_RUNTIME_SERVICE_RAW_BOARD_ENTITY_CONSUMERS. Read-only projection "
        "consumers must use row projections instead of raw Task entities. "
        "Offenders:\n" + "\n".join(unauthorized_helper_consumers)
    )


def test_task_runtime_service_raw_board_entity_consumer_allowlist_is_locked() -> None:
    """WS2 fence: raw Task entity consumers cannot silently expand."""

    assert (
        TASK_RUNTIME_SERVICE_RAW_BOARD_ENTITY_CONSUMERS == TASK_RUNTIME_SERVICE_EXPECTED_RAW_BOARD_ENTITY_CONSUMERS
    ), (
        "TaskRuntimeService raw Task entity consumers must stay locked to the "
        "reviewed file-row projection bridge plus the four owner mutation "
        "boundaries. Add a dedicated owner-boundary AST requirement before "
        "changing this set."
    )


def test_raw_task_entity_owner_consumers_keep_required_mutation_and_event_boundaries() -> None:
    """WS2 fence: raw Task entity scans must stay paired with owner writes."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_raw_board_entity_consumer_owner_requirements()

    assert not offenders, (
        "TaskRuntimeService raw Task entity consumers that call "
        f"self.{TASK_RUNTIME_SERVICE_RAW_BOARD_LIST_HELPER}() must keep the "
        "reviewed owner boundary calls in the same method: reexecution reset "
        "must replace rows and append execution facts; dependency unblock "
        "refresh must use fact-overlaid dependency status, update the board, "
        "and append execution facts; bulk run suspend must suspend the locked "
        "session, update the board, and append execution facts. "
        f"{rel} offenders:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# WS2 projected runtime execution session - explicit legacy bridge boundary
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService._find_projected_runtime_execution_session()`` and the
# locked variant are read-only consumers that resolve execution facts first and
# then delegate file-backed ``metadata.runtime_execution`` compatibility lookup
# to one explicit legacy bridge. Keeping the fallback behind a named bridge
# makes the old metadata path auditable without deleting it.
#
# The only helper allowed to directly scan file rows for this legacy fallback is
# ``_find_projected_runtime_execution_session_from_file_rows``. It must request
# terminal rows explicitly and accept/pass ``augment_runtime_state`` so locked
# and unlocked callers cannot silently share the wrong projection mode.

PROJECTED_RUNTIME_EXECUTION_SESSION_HELPER = "_find_projected_runtime_execution_session"
PROJECTED_RUNTIME_EXECUTION_SESSION_LOCKED_HELPER = "_find_projected_runtime_execution_session_locked"
PROJECTED_RUNTIME_EXECUTION_SESSION_LEGACY_BRIDGE = "_find_projected_runtime_execution_session_from_file_rows"
PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_GATE = "_projected_runtime_execution_session_file_fallback_allowed"
PROJECTED_RUNTIME_EXECUTION_SESSION_ROW_SOURCE = "_list_file_task_rows"
PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_METHOD = "projected_runtime_execution_session_fallback_coverage"
PROJECTED_RUNTIME_EXECUTION_SESSION_PROJECTED_ROW_READER = "_runtime_execution_session_from_projected_row"
TRANSITIONAL_TASK_ROW_READ_MODEL_ROWS_METHOD = "_transitional_task_row_read_model_rows"
FACT_ONLY_TASK_ROW_READ_MODEL_ROWS_METHOD = "_fact_only_task_row_read_model_rows"
TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_METHOD = "task_row_read_model_fallback_coverage"
TASK_ROW_READ_MODEL_PROJECTION_PARITY_COVERAGE_METHOD = "task_row_read_model_projection_parity_coverage"
TASK_ROW_READ_MODEL_CUTOVER_READINESS_METHOD = "task_row_read_model_cutover_readiness"
OBSERVABLE_TASK_ROWS_METHOD = "list_observable_task_rows"
OBSERVABLE_TASK_ROWS_FILE_SOURCE = "_list_file_task_rows"
OBSERVABLE_TASK_ROWS_FACT_SOURCE = "list_task_rows_from_execution_facts"
OBSERVABLE_TASK_ROWS_PROJECTION_SOURCE = "_project_observable_task_rows"
TRANSITIONAL_TASK_ROW_READ_MODEL_REQUIRED_SELF_CALLS: frozenset[str] = frozenset(
    {
        OBSERVABLE_TASK_ROWS_FILE_SOURCE,
        OBSERVABLE_TASK_ROWS_FACT_SOURCE,
        OBSERVABLE_TASK_ROWS_PROJECTION_SOURCE,
    }
)
TRANSITIONAL_TASK_ROW_READ_MODEL_FORBIDDEN_SELF_CALLS: frozenset[str] = frozenset(
    {
        "_list_file_task_entities",
        "_overlay_execution_fact_rows",
        "list_task_rows",
        "refresh_dependency_unblocks",
        TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_METHOD,
    }
)
FACT_ONLY_TASK_ROW_READ_MODEL_ALLOWED_SELF_CALLS: frozenset[str] = frozenset(
    {
        OBSERVABLE_TASK_ROWS_FACT_SOURCE,
        OBSERVABLE_TASK_ROWS_PROJECTION_SOURCE,
    }
)
FACT_ONLY_TASK_ROW_READ_MODEL_FORBIDDEN_CALLS: dict[str, str] = {
    "self._list_file_task_rows": "file-backed rows are the transitional fallback source",
    "self._list_file_task_entities": "raw TaskBoard entities are outside observable fact-only projection",
    "self.refresh_dependency_unblocks": "dependency refresh mutates runtime task state",
    "self.list_task_rows": "list_task_rows() is the legacy refreshing compatibility entrypoint",
    "self.append_execution_event": "execution event append is a mutation path",
    "self._append_execution_event": "execution event append is a mutation path",
    "self.claim_execution": "claim_execution() mutates execution ownership",
    "self.claim_next_execution": "claim_next_execution() mutates execution ownership",
    "self.update_task_row": "update_task_row() mutates row projection state",
    "self.create_task_row": "create_task_row() mutates row projection state",
    "_list_file_task_rows": "file-backed rows are the transitional fallback source",
    "_list_file_task_entities": "raw TaskBoard entities are outside observable fact-only projection",
    "refresh_dependency_unblocks": "dependency refresh mutates runtime task state",
    "list_task_rows": "list_task_rows() is the legacy refreshing compatibility entrypoint",
    "append_execution_event": "execution event append is a mutation path",
    "_append_execution_event": "execution event append is a mutation path",
    "claim_execution": "claim_execution() mutates execution ownership",
    "claim_next_execution": "claim_next_execution() mutates execution ownership",
    "update_task_row": "update_task_row() mutates row projection state",
    "create_task_row": "create_task_row() mutates row projection state",
}
TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_REQUIRED_SELF_CALLS: frozenset[str] = frozenset(
    {
        OBSERVABLE_TASK_ROWS_FILE_SOURCE,
        OBSERVABLE_TASK_ROWS_FACT_SOURCE,
        OBSERVABLE_TASK_ROWS_PROJECTION_SOURCE,
    }
)
TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_ID_HELPERS: frozenset[str] = frozenset(
    {
        "_observable_row_task_id",
        "_task_row_read_model_task_id",
        "_task_row_read_model_task_id_set",
        "_task_row_read_model_task_id_sort_key",
        "normalize_task_id",
    }
)
TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_ALLOWED_SELF_CALLS: frozenset[str] = (
    TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_REQUIRED_SELF_CALLS | TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_ID_HELPERS
)
TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "self._append_execution_event",
        "self._board.create",
        "self._board.update",
        "self._write_session",
        "self.claim_execution",
        "self.complete_execution",
        "self.fail_execution",
        "self.refresh_dependency_unblocks",
    }
)
TASK_ROW_READ_MODEL_PROJECTION_PARITY_COVERAGE_REQUIRED_SELF_CALLS: frozenset[str] = frozenset(
    {
        OBSERVABLE_TASK_ROWS_FILE_SOURCE,
        OBSERVABLE_TASK_ROWS_FACT_SOURCE,
        OBSERVABLE_TASK_ROWS_PROJECTION_SOURCE,
    }
)
TASK_ROW_READ_MODEL_PROJECTION_PARITY_COVERAGE_ID_HELPERS: frozenset[str] = frozenset(
    {
        "_observable_row_task_id",
        "_task_row_read_model_task_id",
        "_task_row_read_model_task_id_set",
        "_task_row_read_model_task_id_sort_key",
        "normalize_task_id",
    }
)
TASK_ROW_READ_MODEL_PROJECTION_PARITY_COVERAGE_ALLOWED_SELF_CALLS: frozenset[str] = (
    TASK_ROW_READ_MODEL_PROJECTION_PARITY_COVERAGE_REQUIRED_SELF_CALLS
    | TASK_ROW_READ_MODEL_PROJECTION_PARITY_COVERAGE_ID_HELPERS
)
TASK_ROW_READ_MODEL_PROJECTION_PARITY_COVERAGE_FORBIDDEN_CALLS: dict[str, str] = {
    "self._list_file_task_entities": "raw Task entity reads are outside projection parity coverage",
    "self.refresh_dependency_unblocks": "dependency refresh mutates runtime task state",
    "self.append_execution_event": "execution event append is a mutation path",
    "self._append_execution_event": "execution event append is a mutation path",
    "self.claim_execution": "claim_execution() mutates execution ownership",
    "self.update_task_row": "update_task_row() mutates row projection state",
    "self.create_task_row": "create_task_row() mutates row projection state",
    "_list_file_task_entities": "raw Task entity reads are outside projection parity coverage",
    "refresh_dependency_unblocks": "dependency refresh mutates runtime task state",
    "append_execution_event": "execution event append is a mutation path",
    "_append_execution_event": "execution event append is a mutation path",
    "claim_execution": "claim_execution() mutates execution ownership",
    "update_task_row": "update_task_row() mutates row projection state",
    "create_task_row": "create_task_row() mutates row projection state",
}
PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_REQUIRED_SELF_CALLS: frozenset[str] = frozenset(
    {
        OBSERVABLE_TASK_ROWS_FACT_SOURCE,
        PROJECTED_RUNTIME_EXECUTION_SESSION_PROJECTED_ROW_READER,
        PROJECTED_RUNTIME_EXECUTION_SESSION_ROW_SOURCE,
    }
)
PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_ID_HELPERS: frozenset[str] = frozenset(
    {
        "_task_row_read_model_task_id",
        "_task_row_read_model_task_id_set",
        "_task_row_read_model_task_id_sort_key",
        "normalize_task_id",
    }
)
PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_ALLOWED_SELF_CALLS: frozenset[str] = (
    PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_REQUIRED_SELF_CALLS
    | PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_ID_HELPERS
)
PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "self._append_execution_event",
        "self._board.create",
        "self._board.update",
        "self._write_session",
        "self.claim_execution",
        "self.complete_execution",
        "self.fail_execution",
        "self.refresh_dependency_unblocks",
    }
)
PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_GATE_FORBIDDEN_CALLS: dict[str, str] = {
    f"self.{PROJECTED_RUNTIME_EXECUTION_SESSION_ROW_SOURCE}": "file-row reads must stay inside the compat fallback bridge",
    f"self.{TASK_RUNTIME_SERVICE_RAW_BOARD_LIST_HELPER}": "raw Task entity reads are outside the fallback gate",
    f"self.{PROJECTED_RUNTIME_EXECUTION_SESSION_LEGACY_BRIDGE}": "the gate decides whether fallback may run; it must not run fallback itself",
    "self._append_execution_event": "execution event append is a mutation path",
    "self._write_session": "session writes are a mutation path",
    "self.append_execution_event": "execution event append is a mutation path",
    "self.cancel_task_row_for_deduplication": "task-row cancellation is a mutation path",
    "self.claim_execution": "claim_execution() mutates execution ownership",
    "self.claim_next_execution": "claim_next_execution() mutates execution ownership",
    "self.complete_execution": "complete_execution() mutates execution state",
    "self.create_task_row": "create_task_row() mutates row projection state",
    "self.fail_execution": "fail_execution() mutates execution state",
    "self.heartbeat_execution": "heartbeat_execution() mutates execution state",
    "self.refresh_dependency_unblocks": "dependency refresh mutates runtime task state",
    "self.reopen_task_row": "reopen_task_row() mutates row projection state",
    "self.suspend_execution": "suspend_execution() mutates execution state",
    "self.update_task_row": "update_task_row() mutates row projection state",
}
TASK_ROW_READ_MODEL_CUTOVER_READINESS_ALLOWED_SELF_CALLS: frozenset[str] = frozenset(
    {
        TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_METHOD,
        TASK_ROW_READ_MODEL_PROJECTION_PARITY_COVERAGE_METHOD,
        PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_METHOD,
    }
)
TASK_ROW_READ_MODEL_CUTOVER_READINESS_FORBIDDEN_CALLS: dict[str, str] = {
    "self._list_file_task_rows": "file task-row reads must stay behind projection coverage methods",
    "self._list_file_task_entities": "raw TaskBoard entity reads are outside cutover readiness",
    "self.refresh_dependency_unblocks": "dependency refresh mutates runtime task state",
    "self.append_execution_event": "execution event append is a mutation path",
    "self._append_execution_event": "execution event append is a mutation path",
    "self.claim_execution": "claim_execution() mutates execution ownership",
    "self.update_task_row": "update_task_row() mutates row projection state",
    "self.create_task_row": "create_task_row() mutates row projection state",
    "_list_file_task_rows": "file task-row reads must stay behind projection coverage methods",
    "_list_file_task_entities": "raw TaskBoard entity reads are outside cutover readiness",
    "refresh_dependency_unblocks": "dependency refresh mutates runtime task state",
    "append_execution_event": "execution event append is a mutation path",
    "_append_execution_event": "execution event append is a mutation path",
    "claim_execution": "claim_execution() mutates execution ownership",
    "update_task_row": "update_task_row() mutates row projection state",
    "create_task_row": "create_task_row() mutates row projection state",
}
OBSERVABLE_TASK_ROWS_FORBIDDEN_SELF_CALLS = frozenset(
    {
        "append_execution_event",
        "claim_execution",
        "claim_next_execution",
        "create_task_row",
        "_append_execution_event",
        "_list_file_task_entities",
        "_overlay_execution_fact_rows",
        OBSERVABLE_TASK_ROWS_FACT_SOURCE,
        OBSERVABLE_TASK_ROWS_FILE_SOURCE,
        OBSERVABLE_TASK_ROWS_PROJECTION_SOURCE,
        "list_task_rows",
        "refresh_dependency_unblocks",
        "update_task_row",
    }
)
TASK_ROWS_COMPATIBILITY_METHOD = "list_task_rows"
TASK_ROWS_COMPATIBILITY_REFRESH_METHOD = "refresh_dependency_unblocks"
TASK_ROWS_COMPATIBILITY_ROW_SOURCE = "_list_file_task_rows"
READY_TASK_ROWS_COMPATIBILITY_METHOD = "list_ready_task_rows"
TASK_ROWS_COMPATIBILITY_SELF_CALL_ALLOWLIST: dict[str, str] = {}


def test_ws2_projected_runtime_execution_session_fallback_gate_helper_is_read_only() -> None:
    """WS2 projected runtime execution session fallback gate helper fence."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_projected_runtime_execution_session_fallback_gate_helper_boundary()

    assert not offenders, (
        "WS2 projected runtime execution session fallback gate helper fence: "
        f"{rel}:TaskRuntimeService.{PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_GATE}() "
        f"must exist, call self.{TASK_ROW_READ_MODEL_CUTOVER_READINESS_METHOD}(), "
        "and remain pure readiness policy. It must not read file rows, raw Task "
        "entities, run the fallback bridge, refresh dependencies, mutate runtime "
        "state, or call self._board.*. Offenders:\n" + "\n".join(offenders)
    )


def test_ws2_projected_runtime_execution_session_fallback_gate_wraps_file_rows_bridge() -> None:
    """WS2 projected runtime execution session fallback gate routing fence."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_projected_runtime_execution_session_fallback_gate_wraps_legacy_bridge()

    assert not offenders, (
        "WS2 projected runtime execution session fallback gate routing fence: "
        f"{rel}:TaskRuntimeService.{PROJECTED_RUNTIME_EXECUTION_SESSION_HELPER}() "
        f"and {PROJECTED_RUNTIME_EXECUTION_SESSION_LOCKED_HELPER}() must not "
        f"call self.{PROJECTED_RUNTIME_EXECUTION_SESSION_ROW_SOURCE}() directly. "
        "After fact projection misses, the unlocked helper must call "
        f"self.{PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_GATE}() before "
        "delegating allowed metadata.runtime_execution file-row fallback to "
        f"self.{PROJECTED_RUNTIME_EXECUTION_SESSION_LEGACY_BRIDGE}() with "
        "augment_runtime_state=True. The locked helper must preserve the "
        "non-augmenting file-row fallback with augment_runtime_state=False "
        "without evaluating readiness while locks are held. Offenders:\n" + "\n".join(offenders)
    )


def test_projected_runtime_execution_session_legacy_bridge_scans_terminal_file_rows() -> None:
    """WS2 projected runtime execution-session legacy bridge contract."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_projected_runtime_execution_session_legacy_bridge_contract()

    assert not offenders, (
        "WS2 projected runtime execution-session legacy bridge fence: "
        f"{rel}:TaskRuntimeService.{PROJECTED_RUNTIME_EXECUTION_SESSION_LEGACY_BRIDGE}() "
        f"is the explicit legacy metadata.runtime_execution file-row fallback. "
        f"It must call self.{PROJECTED_RUNTIME_EXECUTION_SESSION_ROW_SOURCE}"
        "(include_terminal=True, augment_runtime_state=augment_runtime_state). "
        "Offenders:\n" + "\n".join(offenders)
    )


def test_projected_runtime_execution_session_does_not_read_raw_taskboard() -> None:
    """WS2 projected runtime execution-session fence (negative invariant)."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_projected_runtime_execution_session_forbidden_raw_reads()

    assert not offenders, (
        "WS2 projected runtime execution-session fence: "
        f"{rel}:TaskRuntimeService.{PROJECTED_RUNTIME_EXECUTION_SESSION_HELPER}() "
        "is a read-only projection consumer and must not call "
        "self._list_file_task_entities() or self._board.*. Keep raw Task "
        "entity access limited to the four owner/path methods in "
        "TASK_RUNTIME_SERVICE_RAW_BOARD_ENTITY_CONSUMERS. Offenders:\n" + "\n".join(offenders)
    )


def test_transitional_task_row_read_model_rows_load_file_rows_execution_facts_and_projection() -> None:
    """WS2 transitional task-row read-model fence (positive invariant)."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_transitional_task_row_read_model_rows_projection_sources()

    assert not offenders, (
        "WS2 transitional task-row read-model fence: "
        f"{rel}:TaskRuntimeService.{TRANSITIONAL_TASK_ROW_READ_MODEL_ROWS_METHOD}() "
        "must directly load file-backed rows through self._list_file_task_rows(), "
        "load execution fact rows through self.list_task_rows_from_execution_facts(), "
        "and project them through self._project_observable_task_rows(...). "
        "This helper is the single transitional assembly boundary for observable "
        "rows and dependency-status read-model rows. Offenders:\n" + "\n".join(offenders)
    )


def test_ws2_gated_fact_only_observable_rows_fact_only_helper_uses_execution_facts_only() -> None:
    """WS2 gated fact-only observable rows: helper is fact-only and read-only."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_fact_only_task_row_read_model_rows_projection_boundary()

    assert not offenders, (
        "WS2 gated fact-only observable rows fence: "
        f"{rel}:TaskRuntimeService.{FACT_ONLY_TASK_ROW_READ_MODEL_ROWS_METHOD}() "
        "must exist and may only call "
        f"self.{OBSERVABLE_TASK_ROWS_FACT_SOURCE}() plus "
        f"self.{OBSERVABLE_TASK_ROWS_PROJECTION_SOURCE}(). It must not call "
        "file-backed row/entity loaders, dependency refresh, list_task_rows(), "
        "claim/update/create APIs, execution-event append APIs, or self._board.*. "
        "Offenders:\n" + "\n".join(offenders)
    )


def test_task_row_read_model_fallback_coverage_is_read_only_projection() -> None:
    """WS2 fallback-coverage fence (existence and read-only boundary)."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_task_row_read_model_fallback_coverage_projection_boundary()

    assert not offenders, (
        "WS2 task-row read-model fallback coverage fence: "
        f"{rel}:TaskRuntimeService.{TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_METHOD}() "
        "must exist and remain a read-only coverage/projection method. It may "
        "load file-backed rows, load task_runtime.execution fact rows, project "
        "observable rows, and normalize task ids; it must not append execution "
        "events, write sessions, refresh dependencies, claim/complete/fail "
        "executions, or mutate the raw TaskBoard. Offenders:\n" + "\n".join(offenders)
    )


def test_ws2_projection_parity_task_row_read_model_coverage_boundary_is_read_only() -> None:
    """WS2 projection parity boundary: coverage is read-only and source-complete."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_task_row_read_model_projection_parity_coverage_boundary()

    assert not offenders, (
        "WS2 projection parity task-row read-model coverage boundary: "
        f"{rel}:TaskRuntimeService.{TASK_ROW_READ_MODEL_PROJECTION_PARITY_COVERAGE_METHOD}() "
        "must exist and remain a read-only projection coverage method. It must "
        "load file-backed task rows through self._list_file_task_rows(), load "
        "task_runtime.execution fact rows through "
        "self.list_task_rows_from_execution_facts(), and compare the observable "
        "projection from self._project_observable_task_rows(...). It may use "
        "pure task-id normalization helpers only; it must not refresh "
        "dependencies, append execution events, claim/update/create rows, read "
        "raw Task entities, or call self._board.*. Offenders:\n" + "\n".join(offenders)
    )


def test_projected_runtime_execution_session_fallback_coverage_is_read_only_projection() -> None:
    """WS2 projected runtime-execution session fallback-coverage fence."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_projected_runtime_execution_session_fallback_coverage_boundary()

    assert not offenders, (
        "WS2 projected runtime-execution session fallback-coverage fence: "
        f"{rel}:TaskRuntimeService.{PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_METHOD}() "
        "must exist and remain a read-only coverage projection. It must load "
        "file rows with include_terminal=True and augment_runtime_state=True, "
        "load task_runtime.execution fact rows, derive projected sessions from "
        "both sources, and compare their task-id coverage without refreshing "
        "dependencies, appending events, mutating TaskBoard rows, writing "
        "sessions, or claim/complete/fail transitions. Offenders:\n" + "\n".join(offenders)
    )


def test_ws2_projection_parity_cutover_readiness_boundary_composes_coverage_only() -> None:
    """WS2 projection parity cutover readiness boundary: compose coverage only."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_task_row_read_model_cutover_readiness_boundary()

    assert not offenders, (
        "WS2 projection parity cutover readiness boundary: "
        f"{rel}:TaskRuntimeService.{TASK_ROW_READ_MODEL_CUTOVER_READINESS_METHOD}() "
        "must exist and remain a read-only composition over "
        f"self.{TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_METHOD}(), "
        f"self.{TASK_ROW_READ_MODEL_PROJECTION_PARITY_COVERAGE_METHOD}(), and "
        f"self.{PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_METHOD}(). "
        "It must not call file TaskBoard row/entity readers, dependency refresh, "
        "execution event append, claim/update/create row APIs, raw TaskBoard "
        "methods, or any other TaskRuntimeService self-call. Offenders:\n" + "\n".join(offenders)
    )


def test_transitional_task_row_read_model_rows_not_replaced_by_fallback_coverage() -> None:
    """WS2 transitional helper remains the row assembly boundary."""

    method_def = _transitional_task_row_read_model_rows_function_def()
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    coverage_calls = _direct_self_method_calls(method_def, TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_METHOD)
    projection_offenders = _check_transitional_task_row_read_model_rows_projection_sources()
    offenders = [
        f"{rel}:TaskRuntimeService.{TRANSITIONAL_TASK_ROW_READ_MODEL_ROWS_METHOD}():"
        f"{call.lineno} calls self.{TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_METHOD}(); "
        "the transitional read-model helper must directly load file rows, "
        "execution fact rows, and projection rows instead of delegating to the "
        "coverage/reporting method."
        for call in coverage_calls
    ]
    offenders.extend(projection_offenders)

    assert not offenders, (
        "WS2 transitional task-row read-model ownership fence: "
        f"{rel}:TaskRuntimeService.{TRANSITIONAL_TASK_ROW_READ_MODEL_ROWS_METHOD}() "
        "must stay the direct assembly boundary for file-backed rows, "
        "task_runtime.execution fact rows, and observable projection. The "
        f"{TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_METHOD}() method is reporting "
        "coverage only and must not replace or pollute transitional row assembly. "
        "Offenders:\n" + "\n".join(offenders)
    )


def test_ws2_gated_fact_only_observable_rows_select_fact_only_or_transitional_read_model() -> None:
    """WS2 gated fact-only observable rows: gate selects read-model helpers."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_observable_task_rows_projection_sources()

    assert not offenders, (
        "WS2 gated fact-only observable rows fence: "
        f"{rel}:TaskRuntimeService.{OBSERVABLE_TASK_ROWS_METHOD}() must call "
        f"self.{TASK_ROW_READ_MODEL_CUTOVER_READINESS_METHOD}(), route ready=True "
        f"through self.{FACT_ONLY_TASK_ROW_READ_MODEL_ROWS_METHOD}(), and route "
        f"fallback reads through self.{TRANSITIONAL_TASK_ROW_READ_MODEL_ROWS_METHOD}(). "
        "It must not rebuild file/fact/projection inputs inline. Offenders:\n" + "\n".join(offenders)
    )


def test_observable_task_rows_do_not_refresh_dependencies_or_read_raw_board() -> None:
    """WS2 observable task-row projection fence (negative invariant)."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_observable_task_rows_read_only_projection_boundary()

    assert not offenders, (
        "WS2 observable task-row projection fence: "
        f"{rel}:TaskRuntimeService.{OBSERVABLE_TASK_ROWS_METHOD}() is a "
        "read-only projection and must not call lower-level row loaders, "
        "projection helpers, refresh_dependency_unblocks(), list_task_rows(), "
        "self._list_file_task_entities(), or self._board.*. Keep transitional "
        "assembly behind the dedicated helper and dependency refresh behavior "
        "behind list_task_rows() and owner maintenance flows. Offenders:\n" + "\n".join(offenders)
    )


def test_list_task_rows_retains_refresh_then_file_row_compatibility_boundary() -> None:
    """WS2 compatibility fence for the existing refreshing row read."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_list_task_rows_compatibility_entrypoint_contract()

    assert not offenders, (
        "WS2 task-row compatibility fence: "
        f"{rel}:TaskRuntimeService.{TASK_ROWS_COMPATIBILITY_METHOD}() is the "
        "legacy compatibility entrypoint. It must call "
        f"self.{TASK_ROWS_COMPATIBILITY_REFRESH_METHOD}() before "
        f"self.{TASK_ROWS_COMPATIBILITY_ROW_SOURCE}(...), and it must delegate "
        "row construction to that private file-backed helper instead of "
        "recursively reading through itself. Observable read-only projections "
        "are fenced separately. Offenders:\n" + "\n".join(offenders)
    )


def test_task_runtime_service_methods_do_not_call_list_task_rows_compatibility_entrypoint() -> None:
    """WS2 compatibility fence: no non-owner method may call ``list_task_rows``."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_task_runtime_service_methods_do_not_call_list_task_rows()

    assert not offenders, (
        "WS2 task-row compatibility fence: "
        f"{rel}:TaskRuntimeService.{TASK_ROWS_COMPATIBILITY_METHOD}() is the "
        "only owner of refresh-before-file-read compatibility behavior. Other "
        "TaskRuntimeService methods must not call self.list_task_rows(); use "
        "side-effect-free observable, transitional, dependency-status, or "
        "file-row helpers as appropriate. Any deliberate exception must be "
        "encoded in TASK_ROWS_COMPATIBILITY_SELF_CALL_ALLOWLIST with a narrow "
        "method name and justification. Offenders:\n" + "\n".join(offenders)
    )


def test_list_ready_task_rows_refreshes_before_observable_projection() -> None:
    """WS2 compatibility fence for legacy worker ready-row selection."""

    method_def = _task_runtime_service_method_def(READY_TASK_ROWS_COMPATIBILITY_METHOD)
    refresh_calls = _direct_self_method_calls(method_def, "refresh_dependency_unblocks")
    observable_calls = _direct_self_method_calls(method_def, OBSERVABLE_TASK_ROWS_METHOD)
    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()

    assert refresh_calls, (
        "WS2 ready-row compatibility fence: "
        f"{rel}:TaskRuntimeService.{READY_TASK_ROWS_COMPATIBILITY_METHOD}() must "
        "explicitly call self.refresh_dependency_unblocks() because "
        "list_observable_task_rows() is a pure read projection."
    )
    assert observable_calls, (
        "WS2 ready-row compatibility fence: "
        f"{rel}:TaskRuntimeService.{READY_TASK_ROWS_COMPATIBILITY_METHOD}() must "
        "consume self.list_observable_task_rows() after refreshing dependency "
        "unblocks so task_runtime.execution facts still participate in ready "
        "selection."
    )
    assert min(call.lineno for call in refresh_calls) < min(call.lineno for call in observable_calls), (
        "WS2 ready-row compatibility fence: "
        f"{rel}:TaskRuntimeService.{READY_TASK_ROWS_COMPATIBILITY_METHOD}() must "
        "refresh dependency unblocks before reading observable task rows."
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


