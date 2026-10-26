"""Architecture fence for task-runtime TaskBoard ownership.

Runtime task rows are the public execution-control projection.  Raw
``TaskBoard`` objects remain private to the ``runtime.task_runtime`` cell so
other production layers cannot bypass execution-event projection by reading or
writing task entities directly.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from polaris.tests.architecture._task_runtime_taskboard_boundary_fence_helpers import (
    _EXECUTION_LIKE_TASK_ROW_STATUSES,
    BACKEND_ROOT,
    DELIVERY_CLI_DIRECTOR_SERVICE,
    DELIVERY_PM_TASKBOARD,
    DIRECTOR_ADAPTER,
    DIRECTOR_EXECUTE_METHOD,
    EXECUTION_SESSION_MODULE,
    FACTORY_STAGE_EXECUTOR,
    KERNEL_DIRECTED_EFFECT_LIFECYCLE,
    KERNEL_TRANSACTION_FACTORY,
    PM_ADAPTER,
    PM_BOARD_TASKS,
    PM_PLANNING_AGENT,
    POLARIS_ROOT,
    QA_ADAPTER,
    REVIEWED_TASK_RUNTIME_SERVICE_BOARD_READS,
    REVIEWED_TASK_RUNTIME_SERVICE_BOARD_WRITES,
    ROLE_ADAPTER_BASE,
    ROLE_ADAPTER_PATHS,
    ROLE_WORKER_POOL,
    RUNTIME_ARTIFACT_STORE_ARTIFACTS,
    TASK_RUNTIME_EXECUTION_EVENT_CHECK_HELPERS,
    TASK_RUNTIME_EXECUTION_EVENT_CHECK_REQUIRED,
    TASK_RUNTIME_INTERNAL_BOARD,
    TASK_RUNTIME_INTERNAL_SERVICE,
    TASK_RUNTIME_OWNER,
    TASK_RUNTIME_PUBLIC_BOARD_CONTRACT,
    TASK_RUNTIME_PUBLIC_CONTRACTS,
    TASK_RUNTIME_PUBLIC_SERVICE,
    TASK_RUNTIME_SERVICE_CLAIM_EXECUTION_ENTITY_CONSUMERS,
    TASK_RUNTIME_SERVICE_CLAIM_EXECUTION_ENTITY_HELPER,
    TASK_RUNTIME_SERVICE_DEPENDENCY_FANOUT_ENTITY_CONSUMERS,
    TASK_RUNTIME_SERVICE_DEPENDENCY_FANOUT_ENTITY_HELPER,
    TASK_RUNTIME_SERVICE_EXECUTION_ENTITY_CONSUMERS,
    TASK_RUNTIME_SERVICE_EXECUTION_ENTITY_HELPER,
    TASK_RUNTIME_SERVICE_OWNER_TERMINAL_ENTITY_CONSUMERS,
    TASK_RUNTIME_SERVICE_OWNER_TERMINAL_ENTITY_HELPER,
    TASK_RUNTIME_SERVICE_RAW_BOARD_READ_METHODS,
    TASK_RUNTIME_SERVICE_TERMINAL_SESSION_RECONCILE_ENTITY_CONSUMERS,
    TASK_RUNTIME_SERVICE_TERMINAL_SESSION_RECONCILE_ENTITY_HELPER,
    TASK_RUNTIME_TYPED_SETTLEMENT_CONTRACT,
    TASK_RUNTIME_TYPED_SETTLEMENT_SERVICE,
    TASKBOARD_ROW_WRITE_RECEIPT_ANCHORS,
    TASKBOARD_TERMINAL_EVENT_STREAM,
    _assigned_constant_tuple,
    _assigned_frozenset_literal_strings,
    _call_name,
    _canonical_source_text,
    _check_task_exists_forbidden_raw_reads,
    _contains_execution_status_guard,
    _direct_self_board_get_calls,
    _direct_self_method_calls,
    _direct_task_row_file_accesses,
    _direct_task_row_file_globs,
    _director_update_task_progress_uses_metadata_only_delegate,
    _function_body_references_any_name,
    _function_body_references_name,
    _function_def,
    _function_definition,
    _function_uses_typed_public_settlement,
    _is_allowed_owner_path,
    _legacy_task_board_alias_references,
    _legacy_task_runtime_method_calls,
    _legacy_task_runtime_symbol_aliases,
    _literal_execution_status_update_board_task_calls,
    _literal_status_method_calls,
    _literal_status_update_task_row_calls,
    _method_body_directly_calls_self_method,
    _module_defines_symbol,
    _owner_transition_call_boundary_violations,
    _raw_task_row_read_calls,
    _raw_taskboard_create_calls,
    _raw_taskboard_imports,
    _retired_terminal_caller_violations,
    _runtime_execution_metadata_writes,
    _string_literal,
    _task_runtime_execution_reader_violations,
    _task_runtime_execution_writer_violations,
    _task_runtime_service_method_defs,
    _task_runtime_service_raw_board_read_calls,
    _task_runtime_service_raw_board_write_calls,
    _task_status_is_terminal_values,
    _taskboard_class,
    _taskboard_method,
    _taskboard_row_write_boundary_violations,
    _taskboard_save_task_local_cas_violations,
    _taskboard_save_task_row_file_lock_violations,
    _taskboard_save_task_row_write_receipt_violations,
    _update_task_row_boundary_violations,
    _walk_task_runtime_method_body,
    _write_terminal_event_fact_stream_cas_violations,
)


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
        source = _canonical_source_text(path)
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

    director_entry_tree = ast.parse(_canonical_source_text(DIRECTOR_ADAPTER))
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

    claim_source = _canonical_source_text(DIRECTOR_EXECUTE_METHOD)
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
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "authority"
            and node.func.attr == "settle"
            for node in calls
        ):
            offenders.append("_finalize_claimed_execution() must settle through the public authority")
        if any(isinstance(node.func, ast.Attribute) and node.func.attr == "settle_execution_attempt" for node in calls):
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
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
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
        if any(
            isinstance(node.func, ast.Attribute) and node.func.attr == "heartbeat_task_runtime_execution_attempt"
            for node in calls
        ):
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
    source = _canonical_source_text(ROLE_WORKER_POOL)
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
    source = _canonical_source_text(ROLE_WORKER_POOL)
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
    source = _canonical_source_text(DELIVERY_PM_TASKBOARD)
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
    source = _canonical_source_text(DELIVERY_CLI_DIRECTOR_SERVICE)
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
    source = _canonical_source_text(QA_ADAPTER)
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
    source = _canonical_source_text(PM_BOARD_TASKS)
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
    source = _canonical_source_text(PM_ADAPTER)
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
    source = _canonical_source_text(ROLE_ADAPTER_BASE)

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
    source = _canonical_source_text(DIRECTOR_ADAPTER)

    assert "super()._update_task_progress(" in source, (
        "Director progress events must use BaseRoleAdapter's metadata-only progress projection."
    )
    assert "return super()._update_board_task(task_id, status=status, metadata=metadata)" in source
    assert "self.task_runtime.update_task_row(" not in source


def test_task_runtime_raw_tool_factory_surface_is_removed() -> None:
    sources = {
        "internal": _canonical_source_text(TASK_RUNTIME_INTERNAL_BOARD),
        "public": _canonical_source_text(TASK_RUNTIME_PUBLIC_BOARD_CONTRACT),
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
    source = _canonical_source_text(TASK_RUNTIME_INTERNAL_BOARD)
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
        if TASKBOARD_TERMINAL_EVENT_STREAM in _canonical_source_text(path):
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
    factory_facade = importlib.import_module("polaris.delivery.http.routers.factory")
    bench_facade = importlib.import_module("scripts.factory_bench.run_factory_bench")
    concrete_sources: dict[str, Path] = {}
    for label, facade in (("factory_http_router", factory_facade), ("factory_bench_runner", bench_facade)):
        rehydrate = inspect.getsourcefile(facade._rehydrate_director_resume_taskboard)
        reset = inspect.getsourcefile(facade._reset_current_director_resume_taskboard)
        assert rehydrate is not None and reset == rehydrate
        concrete_sources[label] = Path(rehydrate)
        assert concrete_sources[label].is_relative_to(BACKEND_ROOT)
    sources = {label: _canonical_source_text(path) for label, path in concrete_sources.items()}
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


def test_resume_fence_detects_actual_helper_task_write(monkeypatch: pytest.MonkeyPatch) -> None:
    test_director_resume_preparation_uses_task_runtime_owner()
    facade = importlib.import_module("scripts.factory_bench.run_factory_bench")
    concrete = inspect.getsourcefile(facade._rehydrate_director_resume_taskboard)
    assert concrete is not None
    target = Path(concrete)
    read_text = Path.read_text

    def mutated(path: Path, *args: Any, **kwargs: Any) -> str:
        source = read_text(path, *args, **kwargs)
        return (
            source + "\ndef forbidden_resume(task_file):\n    task_file.write_text('raw')\n"
            if path == target
            else source
        )

    monkeypatch.setattr(Path, "read_text", mutated)
    with pytest.raises(AssertionError, match="must not rewrite"):
        test_director_resume_preparation_uses_task_runtime_owner()


def test_factory_stage_executor_reads_task_rows_through_task_runtime_projection() -> None:
    offenders = _direct_task_row_file_globs(FACTORY_STAGE_EXECUTOR)
    source = "\n".join(_canonical_source_text(path) for path in sorted(FACTORY_STAGE_EXECUTOR.glob("*.py")))

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


def test_factory_projection_fence_detects_active_mixin_glob(monkeypatch: pytest.MonkeyPatch) -> None:
    target = BACKEND_ROOT / "polaris/cells/factory/pipeline/internal/factory_stage_executor/_mixin_04.py"
    read_text = Path.read_text

    def mutated(path: Path, *args: Any, **kwargs: Any) -> str:
        source = read_text(path, *args, **kwargs)
        return (
            source + "\ndef forbidden_factory_read(root):\n    return root.glob('task_*.json')\n"
            if path == target
            else source
        )

    monkeypatch.setattr(Path, "read_text", mutated)
    with pytest.raises(AssertionError, match="must not scan runtime/tasks"):
        test_factory_stage_executor_reads_task_rows_through_task_runtime_projection()


def test_task_runtime_source_fence_reads_actual_composed_method_bodies() -> None:
    from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
    from polaris.tests.architecture._task_runtime_taskboard_boundary_fence_helpers import _class_def

    service = _class_def(TASK_RUNTIME_INTERNAL_SERVICE, "TaskRuntimeService")
    methods = {node.name: node for node in service.body if isinstance(node, ast.FunctionDef)}
    for name in ("claim_execution", "task_exists", "settle_execution_attempt"):
        actual_method = getattr(TaskRuntimeService, name)
        concrete = ast.parse("class ActualSource:\n" + inspect.getsource(actual_method)).body[0]
        assert isinstance(concrete, ast.ClassDef)
        expected = concrete.body[0]
        assert name in methods
        assert ast.dump(methods[name], include_attributes=False) == ast.dump(expected, include_attributes=False)
        assert "_mixin_" in str(inspect.getsourcefile(actual_method))


def test_task_exists_fence_detects_actual_mixin_raw_read(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _check_task_exists_forbidden_raw_reads() == []
    target = TASK_RUNTIME_OWNER / "internal/service/_mixin_recovery_reexec.py"
    read_text = Path.read_text

    def mutated(path: Path, *args: Any, **kwargs: Any) -> str:
        source = read_text(path, *args, **kwargs)
        if path != target:
            return source
        tree = ast.parse(source)
        method = next(
            node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "task_exists"
        )
        lines = source.splitlines(keepends=True)
        lines.insert(method.body[0].lineno - 1, "        return self._board.get(task_id)\n")
        return "".join(lines)

    monkeypatch.setattr(Path, "read_text", mutated)
    assert _check_task_exists_forbidden_raw_reads()


def test_claim_entity_fence_rejects_process_cached_row_read(monkeypatch: pytest.MonkeyPatch) -> None:
    test_claim_execution_routes_task_entity_read_through_claim_helper()
    target = TASK_RUNTIME_OWNER / "internal/service/_mixin_recovery_reexec.py"
    read_text = Path.read_text

    def cached(path: Path, *args: Any, **kwargs: Any) -> str:
        source = read_text(path, *args, **kwargs)
        return (
            source.replace("self._board.get_persisted(normalized)", "self._board.get(normalized)")
            if path == target
            else source
        )

    monkeypatch.setattr(Path, "read_text", cached)
    with pytest.raises(AssertionError, match="durable owner row"):
        test_claim_execution_routes_task_entity_read_through_claim_helper()


@pytest.mark.parametrize("operation", ("get", "reopen"))
def test_reviewed_raw_owner_fence_rejects_new_unreviewed_method(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    test_task_runtime_service_raw_board_reads_are_reviewed()
    test_task_runtime_service_raw_board_writes_are_reviewed()
    target = TASK_RUNTIME_OWNER / "internal/service/_mixin_recovery_reexec.py"
    read_text = Path.read_text

    def mutated(path: Path, *args: Any, **kwargs: Any) -> str:
        source = read_text(path, *args, **kwargs)
        return (
            source + f"\n    def _unreviewed_owner_bypass(self):\n        return self._board.{operation}(1)\n"
            if path == target
            else source
        )

    monkeypatch.setattr(Path, "read_text", mutated)
    checker = (
        test_task_runtime_service_raw_board_reads_are_reviewed
        if operation == "get"
        else test_task_runtime_service_raw_board_writes_are_reviewed
    )
    with pytest.raises(AssertionError, match="_unreviewed_owner_bypass"):
        checker()


def test_owner_transition_review_cannot_be_borrowed_by_another_function(monkeypatch: pytest.MonkeyPatch) -> None:
    test_task_runtime_owner_transition_callers_are_reviewed()
    target = POLARIS_ROOT / "cells/factory/pipeline/internal/factory_stage_executor/_mixin_03.py"
    read_text = Path.read_text

    def mutated(path: Path, *args: Any, **kwargs: Any) -> str:
        source = read_text(path, *args, **kwargs)
        return (
            source
            + "\n    def _unreviewed_reopen(self):\n        runtime.reopen_task_row(1, reason='canonical_delivery_verified_after_terminal_director_attempt')\n"
            if path == target
            else source
        )

    monkeypatch.setattr(Path, "read_text", mutated)
    with pytest.raises(AssertionError, match="outside the reviewed WS2"):
        test_task_runtime_owner_transition_callers_are_reviewed()


@pytest.mark.parametrize(
    ("body", "blocked"),
    (
        ("task_runtime = value if isinstance(value, Mapping) else {}\n    return task_runtime.get('rows')", False),
        ("task_runtime = TaskRuntimeService('workspace')\n    return task_runtime.get('task-1')", True),
        (
            "task_runtime = value if isinstance(value, Mapping) else {}\n    task_runtime = TaskRuntimeService('workspace')\n    return task_runtime.get('task-1')",
            True,
        ),
    ),
)
def test_service_receiver_fence_distinguishes_mapping_from_real_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, blocked: bool
) -> None:
    from polaris.tests.architecture import _task_runtime_taskboard_boundary_fence_helpers as fences

    path = tmp_path / "consumer.py"
    path.write_text("from collections.abc import Mapping\ndef probe(value):\n    " + body + "\n", encoding="utf-8")
    monkeypatch.setattr(fences, "BACKEND_ROOT", tmp_path)
    assert bool(_legacy_task_runtime_method_calls(path)) is blocked


def test_runtime_artifact_store_reads_task_rows_through_task_runtime_projection() -> None:
    offenders = _direct_task_row_file_globs(RUNTIME_ARTIFACT_STORE_ARTIFACTS)
    source = _canonical_source_text(RUNTIME_ARTIFACT_STORE_ARTIFACTS)

    assert not offenders, (
        "runtime.artifact_store must not scan runtime/tasks/task_*.json as an "
        "execution fact source. Task rows are owned by runtime.task_runtime; "
        "artifact-backed workflow status must use TaskRuntimeService observable rows:\n" + "\n".join(offenders)
    )
    assert "list_observable_task_rows" in source, (
        "runtime.artifact_store workflow status must consume TaskRuntimeService.list_observable_task_rows()."
    )


def test_factory_bench_task_runtime_event_file_is_workspace_evidence_only() -> None:
    facade = importlib.import_module("scripts.factory_bench.run_factory_bench")
    concrete = inspect.getsourcefile(facade._file_mentions_workspace)
    assert concrete is not None and concrete == inspect.getsourcefile(facade._runtime_dir_matches_workspace)
    source_path = Path(concrete)
    assert source_path.is_relative_to(BACKEND_ROOT)
    evidence_paths = _assigned_constant_tuple(source_path, "_RUNTIME_WORKSPACE_EVIDENCE_RELATIVE_PATHS")
    source = _canonical_source_text(source_path)
    workspace_matcher = _function_def(source_path, "_file_mentions_workspace")
    runtime_matcher = _function_def(source_path, "_runtime_dir_matches_workspace")
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


def test_bench_workspace_evidence_fence_detects_actual_status_decoder(monkeypatch: pytest.MonkeyPatch) -> None:
    test_factory_bench_task_runtime_event_file_is_workspace_evidence_only()
    facade = importlib.import_module("scripts.factory_bench.run_factory_bench")
    concrete = inspect.getsourcefile(facade._file_mentions_workspace)
    assert concrete is not None
    target = Path(concrete)
    read_text = Path.read_text

    def mutated(path: Path, *args: Any, **kwargs: Any) -> str:
        source = read_text(path, *args, **kwargs)
        if path != target:
            return source
        method = next(
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef) and node.name == "_file_mentions_workspace"
        )
        lines = source.splitlines(keepends=True)
        lines.insert(method.body[0].lineno - 1, "    status = json.loads('{}')\n")
        return "".join(lines)

    monkeypatch.setattr(Path, "read_text", mutated)
    with pytest.raises(AssertionError, match="bounded text evidence"):
        test_factory_bench_task_runtime_event_file_is_workspace_evidence_only()


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
    source = _canonical_source_text(TASK_RUNTIME_INTERNAL_SERVICE)
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
    assert all(
        isinstance(call.func, ast.Attribute) and call.func.attr == "get_persisted" for call in helper_get_calls
    ), "Claim authority must reload the durable owner row; process-local TaskBoard.get() cache is insufficient."
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
