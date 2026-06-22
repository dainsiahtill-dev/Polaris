from __future__ import annotations

from polaris.cells.roles.kernel.internal.events import (
    LLMEventType,
    _resolve_lifecycle_max_age_seconds,
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


def test_lifecycle_max_age_defaults_to_director_timeout_plus_grace(monkeypatch) -> None:
    monkeypatch.delenv("KERNELONE_LLM_LIFECYCLE_MAX_AGE_SECONDS", raising=False)
    monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", "1800")
    monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS", "1800")
    monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS", "1800")

    assert _resolve_lifecycle_max_age_seconds() == 1860.0


def test_lifecycle_max_age_explicit_env_wins(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_LLM_LIFECYCLE_MAX_AGE_SECONDS", "120")
    monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", "1800")

    assert _resolve_lifecycle_max_age_seconds() == 120.0


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
        model="gpt-5-retry",
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


def test_lifecycle_tracks_parallel_calls_by_call_id() -> None:
    _reset_emitter()

    emit_llm_event(
        event_type=LLMEventType.CALL_START,
        role="director",
        run_id="run-parallel",
        task_id="task-a",
        model="qwen",
        metadata={"call_id": "call-a"},
    )
    emit_llm_event(
        event_type=LLMEventType.CALL_START,
        role="director",
        run_id="run-parallel",
        task_id="task-b",
        model="qwen",
        metadata={"call_id": "call-b"},
    )

    snapshot = get_lifecycle_snapshot()
    assert snapshot["stats"]["open_runs_count"] == 2
    assert snapshot["stats"]["reopened_without_close_count"] == 0

    emit_llm_event(
        event_type=LLMEventType.CALL_END,
        role="director",
        run_id="run-parallel",
        task_id="task-a",
        model="qwen",
        metadata={"call_id": "call-a"},
    )
    emit_llm_event(
        event_type=LLMEventType.CALL_END,
        role="director",
        run_id="run-parallel",
        task_id="task-b",
        model="qwen",
        metadata={"call_id": "call-b"},
    )

    snapshot = get_lifecycle_snapshot()
    assert snapshot["stats"]["open_runs_count"] == 0
    assert snapshot["stats"]["closed_without_start_count"] == 0
    assert snapshot["stats"]["reopened_without_close_count"] == 0


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
