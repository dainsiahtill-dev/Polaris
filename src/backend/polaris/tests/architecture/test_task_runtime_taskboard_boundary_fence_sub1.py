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
from typing import Any

import pytest
from polaris.tests.architecture._task_runtime_taskboard_boundary_fence_helpers import (
    BACKEND_ROOT,
    DEPENDENCY_HELPER_FORBIDDEN_RAW_READ_CALLS,
    DEPENDENCY_HELPER_REQUIRED_STATUS_CALL,
    DEPENDENCY_STATUS_READ_MODEL_ROWS_METHOD,
    DIRECTOR_EXECUTION_SERVICE,
    DIRECTOR_STATE_TRACKER,
    EXECUTION_FACT_READ_PROJECTOR,
    EXECUTION_FACT_SEQ_KEY,
    EXECUTION_SESSION_MODULE,
    EXTERNAL_TASK_ID_LOOKUP_HELPER,
    FACT_OVERLAID_DEPENDENCY_STATUS_METHOD,
    PM_PLANNING_AGENT_STATS,
    PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_METHOD,
    REFRESH_DEPENDENCY_UNBLOCKS_FACT_AWARE_HELPERS,
    RUNTIME_PROJECTION_SERVICE,
    STATS_FALLBACK_COVERAGE_FIELD,
    STATS_PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_FIELD,
    STATS_READ_MODEL_CUTOVER_READINESS_FIELD,
    TASK_ROW_READ_MODEL_CUTOVER_READINESS_METHOD,
    TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_METHOD,
    TASK_RUNTIME_DESCRIPTOR,
    TASK_RUNTIME_EXECUTION_FACT_QUERY_GATEWAY,
    TASK_RUNTIME_INTERNAL_SERVICE,
    TASK_RUNTIME_ROW_WRITE_RECEIPT_KEYED_LOOKUP_METHOD,
    TASK_RUNTIME_SERVICE_OWNER_MAINTENANCE_DESCRIPTOR_METHODS,
    TASK_RUNTIME_SERVICE_OWNER_READ_DESCRIPTOR_METHODS,
    TASK_RUNTIME_SERVICE_PREVIEW_SELECTION_DESCRIPTOR_METHODS,
    TASK_RUNTIME_SERVICE_RAW_BOARD_LIST_HELPER,
    TASK_RUNTIME_SETTLEMENT_PROJECTION_LOCKED_PHASE,
    TRANSITIONAL_TASK_ROW_READ_MODEL_ROWS_METHOD,
    _append_execution_event_calls_pass_fact_event_seq,
    _append_execution_event_receipt_helper_projection_violations,
    _append_execution_event_references_appended_seq,
    _append_execution_event_row_write_receipt_projection_violations,
    _append_execution_fact_with_cas_uses_expected_seq_contract,
    _build_task_runtime_execution_event_append_result_accepts_fact_event_seq,
    _call_name,
    _canonical_source_text,
    _check_claim_execution_consults_fact_aware_helper_and_terminal_veto,
    _check_claim_execution_refreshes_dependency_unblocks_before_claim_entity_read,
    _check_dependency_status_read_model_rows_loads_transitional_rows,
    _check_external_task_id_lookup_calls_list_observable_task_rows,
    _check_external_task_id_lookup_forbidden_raw_reads,
    _check_fact_overlaid_dependency_status_rows_delegates_to_observable_projection,
    _check_get_task_calls_list_observable_task_rows,
    _check_get_task_forbidden_raw_reads,
    _check_observable_task_row_stats_calls_list_observable_task_rows,
    _check_observable_task_row_stats_forbidden_raw_reads,
    _check_observable_task_row_stats_projects_cutover_readiness,
    _check_observable_task_row_stats_projects_fallback_coverage,
    _check_observable_task_row_stats_projects_projected_runtime_execution_session_fallback_coverage,
    _check_refresh_dependency_unblocks_uses_fact_aware_status_source,
    _check_role_projection_forbidden_stats_calls,
    _check_selection_readiness_refreshes_before_observable_rows,
    _check_selection_readiness_uses_observable_rows,
    _check_stats_entrypoints_do_not_mutate_task_runtime_state,
    _check_task_exists_calls_list_observable_task_rows,
    _check_task_exists_forbidden_raw_reads,
    _check_task_row_stats_delegates_to_observable_stats,
    _check_task_row_stats_is_pure_observable_stats_delegate,
    _check_task_row_stats_no_independent_reads,
    _execution_fact_query_gateway_violations,
    _function_reads_dot_seq_files,
    _list_reader_propagates_fact_event_seq,
    _list_reader_queries_latest_fact_window,
    _list_task_rows_from_execution_facts_function_def,
    _project_task_row_from_execution_fact_payload_function_def,
    _projector_assigns_fact_event_seq_to_row,
    _projector_projects_fact_event_seq_via_normalize_positive_int,
    _role_projection_observable_stats_call_lines,
    _session_write_receipt_projection_helper_contract_violations,
    _settled_execution_dependency_projection_violations,
    _task_has_unresolved_dependencies_function_def,
    _two_phase_terminal_settlement_violations,
    _with_dependency_execution_events_fail_closed_violations,
)


def test_task_runtime_descriptor_does_not_advertise_owner_read_primitives() -> None:
    descriptor = json.loads(_canonical_source_text(TASK_RUNTIME_DESCRIPTOR))
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
    descriptor = json.loads(_canonical_source_text(TASK_RUNTIME_DESCRIPTOR))
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
    descriptor = json.loads(_canonical_source_text(TASK_RUNTIME_DESCRIPTOR))
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
    source = _canonical_source_text(DIRECTOR_EXECUTION_SERVICE)
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
    source = _canonical_source_text(RUNTIME_PROJECTION_SERVICE)
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
    source = _canonical_source_text(RUNTIME_PROJECTION_SERVICE)
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

    ``limit`` is a page size. The owner folds complete committed snapshots
    and later deltas so compact heartbeats keep their anchor and latest owner.
    Neither first-page-only nor tail-only projection preserves that contract.
    """

    service_rel = TASK_RUNTIME_INTERNAL_SERVICE.relative_to(BACKEND_ROOT).as_posix()
    passes, offenders = _list_reader_queries_latest_fact_window()

    assert passes, (
        "WS2 read-model pagination fence: "
        f"{service_rel}:TaskRuntimeService.list_task_rows_from_execution_facts "
        "must fold complete Fact Stream pages through total/offset pagination "
        "and merge compact heartbeat deltas instead of projecting only a first/tail page. Offenders:\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize("mutation", ("tail_only", "no_progress", "missing_seq"))
def test_fact_projection_fence_rejects_partial_fold_or_fabricated_sequence(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    from polaris.tests.architecture import _task_runtime_taskboard_boundary_fence_helpers as fences

    assert _list_reader_queries_latest_fact_window()[0]
    assert _list_reader_propagates_fact_event_seq()[0]
    target = fences.TASK_RUNTIME_OWNER / "internal/service/_mixin_task_rows.py"
    read_text = Path.read_text

    def mutated(path: Path, *args: Any, **kwargs: Any) -> str:
        source = read_text(path, *args, **kwargs)
        if path != target:
            return source
        method_name = (
            "_project_execution_fact_event_row" if mutation == "missing_seq" else "list_task_rows_from_execution_facts"
        )
        method = next(
            n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == method_name
        )
        lines = source.splitlines(keepends=True)
        start, end = method.lineno - 1, method.end_lineno
        fragment = "".join(lines[start:end])
        if mutation == "missing_seq":
            fragment = fragment.replace('fact["fact_event_seq"] = wrapper_seq', "pass")
        elif mutation == "tail_only":
            fragment = fragment.replace("offset = 0", "offset = max(0, total - event_limit)")
        else:
            fragment = fragment.replace("offset += len(page_events)", "offset += 0")
        return "".join(lines[:start]) + fragment + "".join(lines[end:])

    monkeypatch.setattr(Path, "read_text", mutated)
    checker = (
        _list_reader_propagates_fact_event_seq if mutation == "missing_seq" else _list_reader_queries_latest_fact_window
    )
    assert not checker()[0]


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
