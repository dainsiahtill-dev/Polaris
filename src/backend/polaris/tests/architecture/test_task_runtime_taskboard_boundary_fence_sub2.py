"""Architecture fence for task-runtime TaskBoard ownership.

Runtime task rows are the public execution-control projection.  Raw
``TaskBoard`` objects remain private to the ``runtime.task_runtime`` cell so
other production layers cannot bypass execution-event projection by reading or
writing task entities directly.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from polaris.tests.architecture._task_runtime_taskboard_boundary_fence_helpers import (
    BACKEND_ROOT,
    DIRECTED_EFFECT_RECOVERY_ENTRYPOINT,
    DIRECTED_EFFECT_RECOVERY_UNDER_LEASE_METHOD,
    PM_DELIVERY_DISPATCH_STATS,
    SESSION_BULK_SUSPEND_METHOD,
    SESSION_WRITE_CAS_HELPER,
    TASK_RUNTIME_INTERNAL_SERVICE,
    TEST_FILE_RAW_LIST_TASK_ROWS_TARGETS,
    _check_last_session_write_receipt_accessor_is_safe,
    _check_role_projection_forbidden_stats_calls,
    _check_session_write_receipt_event_projection,
    _check_session_write_receipt_owner_boundary,
    _check_suspend_active_executions_for_run_uses_locked_session_rmw,
    _check_task_runtime_service_initializes_session_write_receipt_anchor,
    _check_write_session_locked_asserts_payload_unchanged_before_writes,
    _check_write_session_updates_session_write_receipts,
    _directed_effect_recovery_lease_lock_boundary_violations,
    _directed_effect_recovery_session_lock_boundary_violations,
    _read_session_lock_boundary_violations,
    _role_projection_observable_stats_call_lines,
    _test_file_raw_list_task_rows_call_violations,
    _write_session_lock_boundary_violations,
)


@pytest.mark.parametrize("readonly", (False, True))
def test_read_session_fence_rejects_missing_file_lock_in_each_branch(
    monkeypatch: pytest.MonkeyPatch, readonly: bool
) -> None:
    from polaris.tests.architecture import _task_runtime_taskboard_boundary_fence_helpers as fences

    assert _read_session_lock_boundary_violations() == []
    target = fences.TASK_RUNTIME_OWNER / "internal/service/_mixin_dependency_session.py"
    read_text = Path.read_text

    def mutated(path: Path, *args: Any, **kwargs: Any) -> str:
        source = read_text(path, *args, **kwargs)
        if path != target:
            return source
        method = next(
            n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == "_read_session"
        )
        lines = source.splitlines(keepends=True)
        start, end = method.lineno - 1, method.end_lineno
        fragment = "".join(lines[start:end])
        lock = (
            "self._board._file_lock(self._session_file_lock_path(task_id), read_only=True)"
            if readonly
            else "self._board._file_lock(self._session_file_lock_path(task_id))"
        )
        assert lock in fragment
        fragment = fragment.replace(lock, "nullcontext()", 1)
        return "".join(lines[:start]) + fragment + "".join(lines[end:])

    monkeypatch.setattr(Path, "read_text", mutated)
    assert _read_session_lock_boundary_violations()


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
