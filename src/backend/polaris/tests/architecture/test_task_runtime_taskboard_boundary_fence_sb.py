"""Architecture fence for task-runtime TaskBoard ownership.

Runtime task rows are the public execution-control projection.  Raw
``TaskBoard`` objects remain private to the ``runtime.task_runtime`` cell so
other production layers cannot bypass execution-event projection by reading or
writing task entities directly.
"""

from __future__ import annotations

import json

from polaris.tests.architecture._task_runtime_taskboard_boundary_fence_helpers import (
    BACKEND_ROOT,
    FACT_ONLY_TASK_ROW_READ_MODEL_ROWS_METHOD,
    OBSERVABLE_TASK_ROWS_FACT_SOURCE,
    OBSERVABLE_TASK_ROWS_METHOD,
    OBSERVABLE_TASK_ROWS_PROJECTION_SOURCE,
    PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_COVERAGE_METHOD,
    PROJECTED_RUNTIME_EXECUTION_SESSION_FALLBACK_GATE,
    PROJECTED_RUNTIME_EXECUTION_SESSION_HELPER,
    PROJECTED_RUNTIME_EXECUTION_SESSION_LEGACY_BRIDGE,
    PROJECTED_RUNTIME_EXECUTION_SESSION_LOCKED_HELPER,
    PROJECTED_RUNTIME_EXECUTION_SESSION_ROW_SOURCE,
    READY_TASK_ROWS_COMPATIBILITY_METHOD,
    TASK_ROW_READ_MODEL_CUTOVER_READINESS_METHOD,
    TASK_ROW_READ_MODEL_FALLBACK_COVERAGE_METHOD,
    TASK_ROW_READ_MODEL_PROJECTION_PARITY_COVERAGE_METHOD,
    TASK_ROWS_COMPATIBILITY_METHOD,
    TASK_ROWS_COMPATIBILITY_REFRESH_METHOD,
    TASK_ROWS_COMPATIBILITY_ROW_SOURCE,
    TASK_RUNTIME_DESCRIPTOR,
    TASK_RUNTIME_INTERNAL_EXECUTION_SESSION_DESCRIPTOR_FILE,
    TASK_RUNTIME_INTERNAL_SERVICE,
    TASK_RUNTIME_PUBLIC_BOARD_CONTRACT,
    TASK_RUNTIME_SERVICE_DESTRUCTIVE_DESCRIPTOR_METHODS,
    TASK_RUNTIME_SERVICE_EXPECTED_RAW_BOARD_ENTITY_CONSUMERS,
    TASK_RUNTIME_SERVICE_NON_AGENT_DESCRIPTOR_METHODS,
    TASK_RUNTIME_SERVICE_NON_OPERATION_DESCRIPTOR_METHODS,
    TASK_RUNTIME_SERVICE_RAW_BOARD_ENTITY_CONSUMERS,
    TASK_RUNTIME_SERVICE_RAW_BOARD_LIST_HELPER,
    TASK_RUNTIME_SERVICE_REQUIRED_READ_MODEL_METHODS,
    TASK_RUNTIME_SERVICE_REQUIRED_ROW_MUTATION_METHODS,
    TASK_RUNTIME_SERVICE_RETIRED_ENTITY_METHODS,
    TASK_RUNTIME_SERVICE_RETIRED_PUBLIC_METHODS,
    TASK_RUNTIME_SERVICE_UTILITY_DESCRIPTOR_METHODS,
    TRANSITIONAL_TASK_ROW_READ_MODEL_ROWS_METHOD,
    _canonical_source_text,
    _check_fact_only_task_row_read_model_rows_projection_boundary,
    _check_list_task_rows_compatibility_entrypoint_contract,
    _check_observable_task_rows_projection_sources,
    _check_observable_task_rows_read_only_projection_boundary,
    _check_projected_runtime_execution_session_fallback_coverage_boundary,
    _check_projected_runtime_execution_session_fallback_gate_helper_boundary,
    _check_projected_runtime_execution_session_fallback_gate_wraps_legacy_bridge,
    _check_projected_runtime_execution_session_forbidden_raw_reads,
    _check_projected_runtime_execution_session_legacy_bridge_contract,
    _check_raw_board_entity_consumer_owner_requirements,
    _check_task_row_read_model_cutover_readiness_boundary,
    _check_task_row_read_model_fallback_coverage_projection_boundary,
    _check_task_row_read_model_projection_parity_coverage_boundary,
    _check_task_runtime_service_methods_do_not_call_list_task_rows,
    _check_transitional_task_row_read_model_rows_projection_sources,
    _direct_self_board_list_all_calls,
    _direct_self_method_calls,
    _function_call_counts,
    _method_body_directly_calls_self_method,
    _task_runtime_service_method_def,
    _task_runtime_service_method_defs,
    _transitional_task_row_read_model_rows_function_def,
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


def test_observer_cannot_borrow_mutation_owner_row_lookup(monkeypatch) -> None:
    from pathlib import Path

    from polaris.tests.architecture import _task_runtime_taskboard_boundary_fence_helpers as fences

    assert _check_task_runtime_service_methods_do_not_call_list_task_rows() == []
    target = fences.TASK_RUNTIME_OWNER / "internal/service/_mixin_recovery_reexec.py"
    read_text = Path.read_text

    def mutated(path, *args, **kwargs):
        source = read_text(path, *args, **kwargs)
        return (
            source + "\n    def _unreviewed_observer(self):\n        return self.bind_task_to_factory_run(command)\n"
            if path == target
            else source
        )

    monkeypatch.setattr(Path, "read_text", mutated)
    assert _check_task_runtime_service_methods_do_not_call_list_task_rows()


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
    source = _canonical_source_text(TASK_RUNTIME_PUBLIC_BOARD_CONTRACT)
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
    descriptor = json.loads(_canonical_source_text(TASK_RUNTIME_DESCRIPTOR))
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
    descriptor = json.loads(_canonical_source_text(TASK_RUNTIME_DESCRIPTOR))
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
    descriptor = json.loads(_canonical_source_text(TASK_RUNTIME_DESCRIPTOR))
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
            if method_name in TASK_RUNTIME_SERVICE_RETIRED_PUBLIC_METHODS:
                offenders.append(method_name)

    assert not offenders, (
        "task_runtime descriptor must not advertise retired TaskRuntimeService "
        "compatibility methods. Public consumers should use row/session APIs "
        "such as list_task_rows(), list_ready_task_rows(), get_task_row_stats(), "
        "and list_observable_task_rows():\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_retired_entity_methods() -> None:
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
            if method_name in TASK_RUNTIME_SERVICE_RETIRED_ENTITY_METHODS:
                offenders.append(method_name)

    assert not offenders, (
        "task_runtime descriptor must not advertise retired entity-returning "
        "TaskRuntimeService compatibility methods. Public consumers should use "
        "row/session APIs such as create_task_row(), update_task_row(), "
        "reopen_task_row(), and get_task():\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_advertises_current_row_mutation_methods() -> None:
    descriptor = json.loads(_canonical_source_text(TASK_RUNTIME_DESCRIPTOR))
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
    descriptor = json.loads(_canonical_source_text(TASK_RUNTIME_DESCRIPTOR))
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
            if method_name.startswith("_") and method_name != "__init__":
                offenders.append(method_name)

    assert not offenders, (
        "task_runtime descriptor must not advertise private TaskRuntimeService "
        "owner implementation methods. Descriptor context should expose stable "
        "row/session/read-model APIs, not storage, session, selection, or event "
        "internals:\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_non_operation_service_methods() -> None:
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
            if method_name in TASK_RUNTIME_SERVICE_NON_AGENT_DESCRIPTOR_METHODS:
                offenders.append(method_name)

    assert not offenders, (
        "task_runtime descriptor must not advertise raw-board access or live "
        "condition/listener utilities. Agent-facing context should use stable "
        "row/session/read-model methods instead:\n" + "\n".join(sorted(offenders))
    )


def test_task_runtime_descriptor_does_not_advertise_destructive_service_methods() -> None:
    descriptor = json.loads(_canonical_source_text(TASK_RUNTIME_DESCRIPTOR))
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
