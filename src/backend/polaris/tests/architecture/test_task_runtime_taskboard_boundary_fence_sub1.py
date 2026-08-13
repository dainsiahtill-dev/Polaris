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


def test_task_runtime_terminal_settlement_uses_two_phase_fence() -> None:
    """WS2 terminal transition fence for canonical settlement and projection."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _two_phase_terminal_settlement_violations()

    assert not offenders, (
        "WS2 terminal settlement fence: "
        f"{rel}:TaskRuntimeService must expose settle_execution_attempt() as the "
        "only terminal entrypoint, commit the winner under session locks, then append "
        "terminal facts only from the separately locked projection phase. Offenders:\n" + "\n".join(offenders)
    )


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


def test_task_runtime_append_event_projects_row_write_receipt_evidence() -> None:
    """WS2 row-write receipt -> execution-event evidence fence.

    ``TaskRuntimeService._append_execution_event`` is the bridge between a
    durable TaskBoard row mutation and the append-only ``task_runtime.execution``
    fact. The method must project the row-write receipt for the event task
    identity into the event details before constructing the payload, and that
    projection must go through keyed TaskBoard receipt lookup rather than the
    global latest receipt anchor.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _append_execution_event_row_write_receipt_projection_violations()

    assert not offenders, (
        "WS2 row-write receipt evidence fence: "
        f"{rel}:TaskRuntimeService._append_execution_event must call a "
        "row-write receipt projection helper before constructing the "
        "execution-event payload, pass the projected details into "
        "build_task_runtime_execution_event_payload(details=...), and the "
        f"helper must call self._board.{TASK_RUNTIME_ROW_WRITE_RECEIPT_KEYED_LOOKUP_METHOD}(). "
        "The append path must not read global row receipt anchors directly. "
        "Offenders:\n" + "\n".join(offenders)
    )


def test_task_runtime_append_event_projects_session_write_receipt_evidence() -> None:
    """WS2 session-write receipt -> execution-event evidence fence.

    ``TaskRuntimeService._append_execution_event`` must not hand-read
    ``_last_session_write_receipt`` or recompose receipt fields. It must merge
    a session receipt projection helper into the same event-details mapping as
    the row receipt helper. The keyed session lookup helper owns the match
    guard: event details may only return ``session_write_receipt`` from a
    task/session identity lookup, and projection must use ``receipt.to_dict()``.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = (
        _append_execution_event_receipt_helper_projection_violations()
        + _session_write_receipt_projection_helper_contract_violations()
    )

    assert not offenders, (
        "WS2 session-write receipt evidence fence: "
        f"{rel}:TaskRuntimeService._append_execution_event must consume both "
        "row and session receipt helpers before building execution-event "
        "details. The session details helper must call keyed session identity "
        "lookup, return key 'session_write_receipt', and project "
        "receipt.to_dict() without reconstructing receipt fields. "
        "Offenders:\n" + "\n".join(offenders)
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
TASK_RUNTIME_SERVICE_SELECTION_REFRESH_METHODS = TASK_RUNTIME_SERVICE_SELECTION_READINESS_METHODS


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

    ``list_task_rows`` is a legacy compatibility entrypoint with an explicit
    dependency refresh side effect. A dedicated service-wide fence bans direct
    ``self.list_task_rows()`` calls from non-owner methods; this narrower fence
    keeps the selection/readiness expectation close to the methods that choose
    executable rows. Existing ``_task_runtime_service_raw_board_*`` fences and
    assertion-based invariants continue to apply.
    """

    offenders = _check_selection_readiness_uses_observable_rows()

    assert not offenders, (
        "WS2 selection/readiness fence: "
        "TaskRuntimeService.select_next_task(), claim_next_execution(), and "
        "list_ready_task_rows() must consume the observable row projection "
        "(self.list_observable_task_rows(...)) instead of the raw file-backed "
        "self.list_task_rows(...), so the task_runtime.execution Fact Stream "
        "overlay stays part of the read-model SSoT for selection and "
        "readiness. Direct list_task_rows() calls from other "
        "TaskRuntimeService methods are blocked by the service-wide "
        "compatibility-entrypoint fence. Offenders:\n" + "\n".join(offenders)
    )


def test_selection_and_readiness_methods_refresh_before_observable_rows() -> None:
    """WS2 selection/readiness explicit-refresh fence."""

    offenders = _check_selection_readiness_refreshes_before_observable_rows()

    assert not offenders, (
        "WS2 selection/readiness refresh fence: observable rows are now a "
        "read-only projection, so TaskRuntimeService.select_next_task(), "
        "claim_next_execution(), and list_ready_task_rows() must explicitly "
        "call self.refresh_dependency_unblocks() before reading "
        "self.list_observable_task_rows(). Offenders:\n" + "\n".join(offenders)
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


def test_settled_execution_dependency_events_stay_on_projection_path() -> None:
    """WS2 dependency-event failure projection fence.

    The completed settlement projection emits dependency-row events for rows it
    unblocks. The projection must retain those event results in the canonical
    settlement result so callers can distinguish a dependency effect failure
    from a missing projection.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = [
        *_settled_execution_dependency_projection_violations(),
        *_with_dependency_execution_events_fail_closed_violations(),
    ]

    assert not offenders, (
        "WS2 dependency-event settlement projection fence: "
        f"{rel}:TaskRuntimeService.{TASK_RUNTIME_SETTLEMENT_PROJECTION_LOCKED_PHASE}() "
        "must retain dependency completion evidence on the canonical settled path, and "
        "_with_dependency_execution_events() must continue to fail-close projected "
        "dependency append failures. Offenders:\n" + "\n".join(offenders)
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
# The fences are structural: they walk the ``claim_execution`` AST, locate
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
# Execution Fact Query Gateway: the only TaskRuntimeService method allowed
# to query the ``task_runtime.execution`` Fact Stream directly. Read-model
# methods must call this private gateway instead of constructing
# ``QueryFactEventsV1`` or invoking ``query_fact_events`` themselves.
TASK_RUNTIME_EXECUTION_FACT_QUERY_GATEWAY = "_query_execution_fact_events"
CLAIM_EXECUTION_FACT_STREAM_READER_ALLOWLIST: frozenset[str] = frozenset(
    {
        TASK_RUNTIME_EXECUTION_FACT_QUERY_GATEWAY,
    }
)


def test_claim_execution_refreshes_dependency_unblocks_before_claim_entity_read() -> None:
    """WS2 claim refresh fence.

    ``TaskRuntimeService.claim_execution()`` is the direct claim entry point
    used by the worker pool (``TaskRuntimePort.claim_execution``) and the
    director adapter. It must call ``self.refresh_dependency_unblocks()``
    *before* calling the raw entity helper, so late
    ``task_runtime.execution`` completion facts (and any in-flight execution
    overlay) wake BLOCKED rows before the claim reads the raw Task entity.

    The fence is structural: it walks the claim AST, locates the first
    ``self._task_entity_for_claim_execution(...)`` call site, and verifies that
    at least one ``self.refresh_dependency_unblocks()`` call precedes it. It
    does not weaken the existing raw-board read/write allowlists; the claim raw
    read is reviewed on the dedicated helper, not on ``claim_execution()``
    itself.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_claim_execution_refreshes_dependency_unblocks_before_claim_entity_read()

    assert not offenders, (
        "WS2 claim refresh fence: "
        f"{rel}:TaskRuntimeService._task_entity_for_claim_execution() must "
        "call self.refresh_dependency_unblocks() before self._board.get(...) "
        "so dependency unblock evidence from task_runtime.execution facts is "
        "consulted before the claim reads the raw Task entity. "
        "Offenders:\n" + "\n".join(offenders)
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


def test_task_runtime_execution_fact_queries_route_through_private_gateway() -> None:
    """WS2 Execution Ledger SSoT fence: execution fact reads have one gateway.

    ``TaskRuntimeService`` may query ``task_runtime.execution`` directly only
    inside ``_query_execution_fact_events()``. All other read-model methods
    must call that gateway so pagination, stream identity, error wrapping, and
    future projection policy stay centralized.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _execution_fact_query_gateway_violations()

    assert not offenders, (
        "ECC-WS2-232 Execution Fact Query Gateway fence: "
        f"{rel}:TaskRuntimeService must keep direct task_runtime.execution "
        "Fact Stream queries behind the single private "
        f"{TASK_RUNTIME_EXECUTION_FACT_QUERY_GATEWAY}() gateway. Append/CAS "
        "write paths such as _append_execution_fact_with_cas() remain outside "
        "this read-query fence. Offenders:\n" + "\n".join(offenders)
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

FACT_OVERLAID_DEPENDENCY_STATUS_METHOD = "_fact_overlaid_dependency_status_rows"
DEPENDENCY_STATUS_READ_MODEL_ROWS_METHOD = "_dependency_status_read_model_rows"
FACT_OVERLAID_DEPENDENCY_REQUIRED_SELF_CALLS: frozenset[str] = frozenset(
    {
        DEPENDENCY_STATUS_READ_MODEL_ROWS_METHOD,
    }
)
FACT_OVERLAID_DEPENDENCY_FORBIDDEN_SELF_CALLS: frozenset[str] = frozenset(
    {
        "_list_file_task_entities",
        "_list_file_task_rows",
        "_overlay_execution_fact_rows",
        "_project_observable_task_rows",
        TRANSITIONAL_TASK_ROW_READ_MODEL_ROWS_METHOD,
        "list_task_rows_from_execution_facts",
        "list_observable_task_rows",
    }
)
DEPENDENCY_STATUS_READ_MODEL_REQUIRED_SELF_CALLS: frozenset[str] = frozenset(
    {
        TRANSITIONAL_TASK_ROW_READ_MODEL_ROWS_METHOD,
    }
)
DEPENDENCY_STATUS_READ_MODEL_FORBIDDEN_SELF_CALLS: frozenset[str] = frozenset(
    {
        "_list_file_task_entities",
        "_overlay_execution_fact_rows",
        OBSERVABLE_TASK_ROWS_FACT_SOURCE,
        OBSERVABLE_TASK_ROWS_FILE_SOURCE,
        OBSERVABLE_TASK_ROWS_PROJECTION_SOURCE,
        "list_observable_task_rows",
        "list_task_rows",
        "refresh_dependency_unblocks",
    }
)
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


def test_fact_overlaid_dependency_status_rows_delegates_to_observable_projection_helper() -> None:
    """The dependency status map must delegate transitional row loading."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_fact_overlaid_dependency_status_rows_delegates_to_observable_projection()

    assert not offenders, (
        "WS2 fact-overlaid dependency status fence: "
        f"{rel}:TaskRuntimeService.{FACT_OVERLAID_DEPENDENCY_STATUS_METHOD}() "
        f"must call self.{DEPENDENCY_STATUS_READ_MODEL_ROWS_METHOD}() and must "
        "not directly load file rows, execution fact rows, observable projection "
        "rows, TaskBoard entities, or self._board.* rows. Dependency status map "
        "materialization must consume one explicit transitional read-model "
        "helper without reintroducing a second overlay branch. Offenders:\n" + "\n".join(offenders)
    )


def test_dependency_status_read_model_rows_delegates_to_transitional_task_row_read_model() -> None:
    """The dependency status read-model helper must delegate transitional rows."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_dependency_status_read_model_rows_loads_transitional_rows()

    assert not offenders, (
        "WS2 dependency-status read-model fence: "
        f"{rel}:TaskRuntimeService.{DEPENDENCY_STATUS_READ_MODEL_ROWS_METHOD}() "
        f"must call self.{TRANSITIONAL_TASK_ROW_READ_MODEL_ROWS_METHOD}() and "
        "must not directly load file rows, execution fact rows, observable "
        "projection rows, TaskBoard entities, or self._board.* rows. The "
        "transitional helper is the single assembly boundary shared by "
        "observable rows and dependency-status read-model rows. Offenders:\n" + "\n".join(offenders)
    )


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
    "self.task_row_read_model_fallback_coverage": (
        "get_task_row_stats() must delegate to get_observable_task_row_stats(), "
        "not independently project fallback coverage"
    ),
    "self.projected_runtime_execution_session_fallback_coverage": (
        "get_task_row_stats() must delegate to get_observable_task_row_stats(), "
        "not independently project projected runtime-execution session fallback coverage"
    ),
    "self.task_row_read_model_cutover_readiness": (
        "get_task_row_stats() must delegate to get_observable_task_row_stats(), "
        "not independently project read-model cutover readiness"
    ),
    "task_row_status_counts": (
        "get_task_row_stats() must delegate to get_observable_task_row_stats(), not independently compute status counts"
    ),
}
STATS_FALLBACK_COVERAGE_FIELD = "read_model_fallback_coverage"
STATS_PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_FIELD = (
    "projected_runtime_execution_session_fallback_coverage"
)
STATS_READ_MODEL_CUTOVER_READINESS_FIELD = "read_model_cutover_readiness"
STATS_EXIT_METHODS: frozenset[str] = frozenset(
    {
        "get_observable_task_row_stats",
        "get_task_row_stats",
    }
)
STATS_EXIT_FORBIDDEN_MUTATION_TARGETS: dict[str, str] = {
    "self.refresh_dependency_unblocks": "dependency refresh is a side-effecting maintenance path",
    "self._append_execution_event": "execution fact append is a mutation path",
    "self._board.update": "raw TaskBoard.update() mutates task rows",
    "self._board.create": "raw TaskBoard.create() mutates task rows",
    "self._write_session": "session writes must stay outside stats projections",
    "self.claim_execution": "claim_execution() mutates execution ownership",
    "self.complete_execution": "complete_execution() mutates execution state",
    "self.fail_execution": "fail_execution() mutates execution state",
}


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


def test_observable_task_row_stats_projects_read_model_fallback_coverage() -> None:
    """WS2 stats projection fence for fallback coverage.

    ``TaskRuntimeService.get_observable_task_row_stats()`` must expose
    ``task_row_read_model_fallback_coverage()`` as a nested stats field. The
    coverage method is the read-only audit surface for file-row fallback while
    the task-row read model is still transitional; leaving it out of the stats
    exit would make downstream observers call a second method or miss fallback
    drift entirely.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_observable_task_row_stats_projects_fallback_coverage()

    assert not offenders, (
        "WS2 stats fallback-coverage fence: "
        f"{rel}:TaskRuntimeService.get_observable_task_row_stats() must call "
        f"self.{TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_METHOD}() and project "
        f"the result under stats[{STATS_FALLBACK_COVERAGE_FIELD!r}]. "
        "Offenders:\n" + "\n".join(offenders)
    )


def test_observable_task_row_stats_projects_projected_runtime_execution_session_fallback_coverage() -> None:
    """WS2 stats projection fence for projected session fallback coverage."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_observable_task_row_stats_projects_projected_runtime_execution_session_fallback_coverage()

    assert not offenders, (
        "WS2 stats projected runtime-execution session fallback-coverage fence: "
        f"{rel}:TaskRuntimeService.get_observable_task_row_stats() must call "
        f"self.{PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_METHOD}() "
        "and project the result under "
        f"stats[{STATS_PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_FIELD!r}]. "
        "Offenders:\n" + "\n".join(offenders)
    )


def test_ws2_observable_task_row_stats_cutover_readiness_boundary_projects_readiness() -> None:
    """WS2 cutover readiness boundary: observable stats expose readiness."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_observable_task_row_stats_projects_cutover_readiness()

    assert not offenders, (
        "WS2 observable stats cutover readiness boundary: "
        f"{rel}:TaskRuntimeService.get_observable_task_row_stats() must call "
        f"self.{TASK_ROW_READ_MODEL_CUTOVER_READINESS_METHOD}() and project "
        f"the result under stats[{STATS_READ_MODEL_CUTOVER_READINESS_FIELD!r}]. "
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


def test_stats_entrypoints_do_not_mutate_task_runtime_state() -> None:
    """WS2 stats projection fence: stats exits are read-only."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_stats_entrypoints_do_not_mutate_task_runtime_state()

    assert not offenders, (
        "WS2 stats read-only fence: "
        f"{rel}:TaskRuntimeService.get_observable_task_row_stats() and "
        "get_task_row_stats() must not call dependency refresh, execution "
        "fact append, raw TaskBoard create/update, session writes, or "
        "claim/complete/fail execution transitions. Stats exits must remain "
        "read-only projections over observable task rows and nested fallback "
        "coverage. Offenders:\n" + "\n".join(offenders)
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


def test_task_row_stats_is_pure_observable_task_row_stats_delegate() -> None:
    """WS2 stats delegation fence: compatibility stats is a pure shim."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_task_row_stats_is_pure_observable_stats_delegate()

    assert not offenders, (
        "WS2 stats delegation fence: "
        f"{rel}:TaskRuntimeService.get_task_row_stats() must remain a pure "
        "compatibility shim whose only executable statement is "
        "return self.get_observable_task_row_stats(). Offenders:\n" + "\n".join(offenders)
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


