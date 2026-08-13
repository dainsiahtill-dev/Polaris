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


# ---------------------------------------------------------------------------
# WS2 execution ledger SSoT - session write receipt anchor fence
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService._write_session()`` is the only owner of durable
# execution-session writes. Claim / heartbeat and the locked settlement phase
# may mutate a ``TaskExecutionSession`` and delegate persistence to the locked
# write helper, but they must not hand-build write receipts. This keeps the
# execution-ledger anchor coupled to the actual ``write_json_atomic()`` success
# path instead of letting terminal settlement or projection drift into
# independent receipt writers.

SESSION_WRITE_RECEIPT_ANCHOR = "_last_session_write_receipt"
SESSION_WRITE_RECEIPT_ACCESSOR = "last_session_write_receipt"
SESSION_WRITE_RECEIPT_CLASS = "TaskExecutionSessionWriteReceipt"
SESSION_WRITE_RECEIPT_DETAIL_KEY = "session_write_receipt"
SESSION_WRITE_RECEIPT_DETAILS_HELPER = "_session_write_receipt_details_for_session"
SESSION_WRITE_RECEIPT_OWNER_METHOD = "_write_session"
SESSION_WRITE_RECEIPT_LOCKED_OWNER_METHOD = "_write_session_locked"
SESSION_WRITE_LOCK_HELPER = "_get_session_lock"
SESSION_WRITE_FILE_LOCK_HELPER = "_file_lock"
SESSION_WRITE_FILE_LOCK_PATH_HELPER = "_session_file_lock_path"
SESSION_WRITE_CAS_HELPER = "_assert_session_payload_unchanged"
SESSION_WRITE_RECEIPT_RECORD_HELPER = "_record_session_write_receipt"
SESSION_READ_OWNER_METHOD = "_read_session"
SESSION_READ_LOCKED_HELPER_METHOD = "_read_session_locked"
SESSION_TERMINAL_SNAPSHOT_METHOD = "_find_terminal_session_snapshot"
SESSION_TERMINAL_SNAPSHOT_LOCKED_METHOD = "_find_terminal_session_snapshot_locked"
SESSION_BULK_SUSPEND_METHOD = "suspend_active_executions_for_run"
SESSION_BULK_SUSPEND_LOCKED_HELPER_METHOD = "_suspend_active_session_for_run_locked"
TYPED_HEARTBEAT_LOCKED_METHOD = "_heartbeat_execution_attempt_locked"
TYPED_HEARTBEAT_OWNER_METHOD = "heartbeat_execution_attempt"
DIRECTED_EFFECT_RECOVERY_ENTRYPOINT = "reconcile_ambiguous_directed_effects"
DIRECTED_EFFECT_RECOVERY_UNDER_LEASE_METHOD = "_reconcile_ambiguous_directed_effects_under_lease"
DIRECTED_EFFECT_RECOVERY_TASK_METHOD = "_reconcile_directed_effect_recovery_task"
DIRECTED_EFFECT_RECOVERY_FILE_LOCKED_HELPER = "_reconcile_directed_effect_recovery_task_file_locked"
DIRECTED_EFFECT_RECOVERY_SESSION_LOCKED_HELPER = "_reconcile_ambiguous_directed_effect_session_locked"
DIRECTED_EFFECT_RECOVERY_LEASE_LOCK_PATH_HELPER = "_directed_effect_recovery_lease_file_lock_path"
DIRECTED_EFFECT_RECOVERY_LEASE_READ_HELPER = "_read_directed_effect_recovery_lease_locked"
DIRECTED_EFFECT_RECOVERY_LEASE_WRITE_HELPERS = frozenset(
    {
        "_claim_directed_effect_recovery_lease_locked",
        "_release_directed_effect_recovery_lease_locked",
    }
)
SESSION_READ_LOCKED_HELPER_METHODS = frozenset({SESSION_READ_LOCKED_HELPER_METHOD})
SESSION_WRITE_RECEIPT_TRANSITION_METHODS = frozenset(
    {
        "claim_execution",
        "heartbeat_execution",
        TASK_RUNTIME_SETTLEMENT_ENTRYPOINT,
        TASK_RUNTIME_SETTLEMENT_LOCKED_PHASE,
        TASK_RUNTIME_SETTLEMENT_PROJECTION_PHASE,
        TASK_RUNTIME_SETTLEMENT_PROJECTION_LOCKED_PHASE,
        "suspend_active_executions_for_run",
    }
)
SESSION_WRITE_RECEIPT_SAFE_COPY_CALLS = frozenset(
    {
        "copy.copy",
        "copy.deepcopy",
        "dataclasses.replace",
        "replace",
    }
)
SESSION_WRITE_RECEIPT_PRESERVED_KEY = "preserved_terminal_session"


def test_task_runtime_service_initializes_session_write_receipt_anchor() -> None:
    """WS2 execution-ledger fence: service construction must anchor receipts."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_task_runtime_service_initializes_session_write_receipt_anchor()

    assert not offenders, (
        "WS2 execution ledger SSoT session receipt fence: "
        f"{rel}:TaskRuntimeService.__init__() must initialize the session "
        "write receipt anchor. Offenders:\n" + "\n".join(offenders)
    )


def test_write_session_updates_session_write_receipt_after_atomic_write() -> None:
    """WS2 execution-ledger fence: ``_write_session`` owns write receipts.

    Each successful ``self._kernel_fs.write_json_atomic(...)`` call must be
    followed by a receipt commit in the same protected write-scope block. The
    commit may assign ``self._last_session_write_receipt`` directly or route
    through the private ``self._record_session_write_receipt(...)`` helper, but
    the helper must remain callable only from ``_write_session()`` or its
    lock-scoped private body. The normal path must mark
    ``preserved_terminal_session=False`` and the preserved terminal session
    write-back path must mark it ``True``.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_write_session_updates_session_write_receipts()

    assert not offenders, (
        "WS2 execution ledger SSoT session receipt fence: "
        f"{rel}:TaskRuntimeService._write_session() must be the single owner "
        "that records the latest session write receipt immediately after "
        "durable write_json_atomic() success. Offenders:\n" + "\n".join(offenders)
    )


def test_write_session_holds_per_task_and_file_locks_around_write_owner() -> None:
    """WS2 execution-ledger fence: session write RMW work must be lock-guarded.

    ``_write_session()`` is the synchronization boundary. It must acquire a
    per-task in-process session lock derived from ``session.task_id`` plus a
    cooperative session file lock derived from the same task id, then keep
    durable session writes plus receipt commits inside both critical sections.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _write_session_lock_boundary_violations()

    assert not offenders, (
        "WS2 execution ledger SSoT session write lock fence: "
        f"{rel}:TaskRuntimeService._write_session() must acquire the per-task "
        "session lock and cooperative session file lock, then keep all durable "
        "session writes plus receipt commits in the same double-lock-scoped "
        "write body. Offenders:\n" + "\n".join(offenders)
    )


def test_read_session_holds_per_task_and_file_locks_around_locked_read_helper() -> None:
    """WS2 execution-ledger fence: session reads must stay lock-guarded.

    ``_read_session()`` is the public synchronization boundary. It must acquire
    the per-task in-process session lock and cooperative session file lock,
    then delegate durable JSON reads to a reviewed locked helper. Write-locked
    terminal snapshot lookup must call that helper directly instead of
    re-entering public ``_read_session()``.
    """

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _read_session_lock_boundary_violations()

    assert not offenders, (
        "WS2 execution ledger SSoT session read lock fence: "
        f"{rel}:TaskRuntimeService._read_session() must acquire the per-task "
        "session lock and cooperative session file lock, durable read_json() "
        "must stay inside _read_session_locked(), and write-locked terminal "
        "snapshot lookup must not re-enter public _read_session(). Offenders:\n" + "\n".join(offenders)
    )


def test_directed_effect_recovery_lease_has_its_own_lock_scoped_authority() -> None:
    """DEO-3 recovery lease I/O must remain inside its dedicated file-lock."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _directed_effect_recovery_lease_lock_boundary_violations()

    assert not offenders, (
        "DEO-3 directed-effect recovery lease fence: "
        f"{rel}:TaskRuntimeService.{DIRECTED_EFFECT_RECOVERY_ENTRYPOINT}() must keep "
        "durable lease claim, the complete recovery sweep, and exact release under one "
        "workspace recovery lease file-lock. Offenders:\n" + "\n".join(offenders)
    )


def test_directed_effect_recovery_mutation_stays_under_session_file_lock() -> None:
    """DEO-3 session recovery must not race settle/reclaim/heartbeat cross-process."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _directed_effect_recovery_session_lock_boundary_violations()

    assert not offenders, (
        "DEO-3 directed-effect session recovery lock fence: "
        f"{rel}:TaskRuntimeService.{DIRECTED_EFFECT_RECOVERY_UNDER_LEASE_METHOD}() must route each "
        "per-session sweep through the unique task owner, cooperative file-locked helper, and "
        "session-locked repository helper. Offenders:\n" + "\n".join(offenders)
    )


def test_suspend_active_executions_for_run_uses_locked_session_rmw() -> None:
    """WS2 execution-ledger fence: bulk suspend must not re-enter public session RMW."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_suspend_active_executions_for_run_uses_locked_session_rmw()

    assert not offenders, (
        "WS2 execution ledger SSoT bulk suspend lock fence: "
        f"{rel}:TaskRuntimeService.{SESSION_BULK_SUSPEND_METHOD}() must keep "
        "session read-modify-write in one explicit per-task session lock plus "
        "cooperative file-lock domain. It must not call public _read_session() "
        "or _write_session(); use the private locked suspend helper or direct "
        "_read_session_locked()/_write_session_locked() calls inside that same "
        "lock domain. Offenders:\n" + "\n".join(offenders)
    )


def test_write_session_locked_asserts_payload_unchanged_before_each_atomic_write() -> None:
    """WS2 execution-ledger fence: durable session writes must be CAS-guarded."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_write_session_locked_asserts_payload_unchanged_before_writes()

    assert not offenders, (
        "WS2 execution ledger SSoT session write CAS fence: "
        f"{rel}:TaskRuntimeService._write_session_locked() must call "
        f"self.{SESSION_WRITE_CAS_HELPER}(...) before each durable "
        "session write_json_atomic() path. Offenders:\n" + "\n".join(offenders)
    )


def test_last_session_write_receipt_accessor_does_not_leak_mutable_internal_state() -> None:
    """WS2 execution-ledger fence: accessor must be immutable or defensive."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_last_session_write_receipt_accessor_is_safe()

    assert not offenders, (
        "WS2 execution ledger SSoT session receipt fence: "
        f"{rel}:TaskRuntimeService.last_session_write_receipt() must not leak "
        "a mutable internal receipt reference. Direct return is allowed only "
        "when TaskExecutionSessionWriteReceipt is a frozen dataclass; otherwise "
        "return a defensive copy/projection. Offenders:\n" + "\n".join(offenders)
    )


def test_session_write_receipt_is_only_written_by_write_session_owner() -> None:
    """WS2 execution-ledger fence: settlement phases must not write receipts."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_session_write_receipt_owner_boundary()

    assert not offenders, (
        "WS2 execution ledger SSoT session receipt fence: "
        f"{rel}:claim/heartbeat/settle/projection paths may only delegate session "
        "persistence to _write_session_locked(); they must not assign or construct "
        "session write receipts themselves. Offenders:\n" + "\n".join(offenders)
    )


def test_append_execution_event_projects_session_write_receipt_through_matching_helper() -> None:
    """WS2 execution-ledger fence: event details must use matched session receipts."""

    rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    offenders = _check_session_write_receipt_event_projection()

    assert not offenders, (
        "WS2 execution ledger SSoT session receipt projection fence: "
        f"{rel}:TaskRuntimeService._append_execution_event() must project the "
        "session write receipt through a read-only details helper backed by "
        "keyed task/session identity lookup before exposing it in event "
        "details. Offenders:\n" + "\n".join(offenders)
    )
