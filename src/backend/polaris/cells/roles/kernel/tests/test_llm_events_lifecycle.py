from __future__ import annotations

from polaris.cells.roles.kernel.internal.events import (
    LLMEventType,
    emit_llm_event,
    get_global_emitter,
    get_lifecycle_snapshot,
)


def _reset_emitter() -> None:
    emitter = get_global_emitter()
    emitter.clear_history()


def test_lifecycle_start_end_closes_run() -> None:
    _reset_emitter()

    emit_llm_event(
        event_type=LLMEventType.CALL_START,
        role="director",
        run_id="run-001",
        model="gpt-5",
    )
    emit_llm_event(
        event_type=LLMEventType.CALL_END,
        role="director",
        run_id="run-001",
        model="gpt-5",
    )

    snapshot = get_lifecycle_snapshot()
    assert snapshot["stats"]["open_runs_count"] == 0
    assert snapshot["stats"]["closed_without_start_count"] == 0


def test_lifecycle_close_without_start_records_warning_counter() -> None:
    _reset_emitter()

    emit_llm_event(
        event_type=LLMEventType.CALL_ERROR,
        role="director",
        run_id="run-missing-start",
        model="gpt-5",
        error_category="network",
        error_message="network reset",
    )

    snapshot = get_lifecycle_snapshot()
    assert snapshot["stats"]["closed_without_start_count"] == 1
    assert snapshot["stats"]["open_runs_count"] == 0


def test_lifecycle_reopen_without_close_records_warning_counter() -> None:
    _reset_emitter()

    emit_llm_event(
        event_type=LLMEventType.CALL_START,
        role="director",
        run_id="run-reopen",
        model="gpt-5",
    )
    emit_llm_event(
        event_type=LLMEventType.CALL_START,
        role="director",
        run_id="run-reopen",
        model="gpt-5",
    )

    snapshot = get_lifecycle_snapshot()
    assert snapshot["stats"]["reopened_without_close_count"] == 1
    assert snapshot["stats"]["open_runs_count"] == 1


def test_lifecycle_snapshot_includes_unclosed_run_details() -> None:
    _reset_emitter()

    emit_llm_event(
        event_type=LLMEventType.CALL_START,
        role="director",
        run_id="run-open",
        model="gpt-5",
        attempt=2,
        task_id="task-123",
    )

    snapshot = get_lifecycle_snapshot()
    assert snapshot["stats"]["open_runs_count"] == 1
    assert snapshot["unclosed_runs"]
    unclosed = snapshot["unclosed_runs"][0]
    assert unclosed["run_id"] == "run-open"
    assert unclosed["role"] == "director"
    assert unclosed["attempt"] == 2
