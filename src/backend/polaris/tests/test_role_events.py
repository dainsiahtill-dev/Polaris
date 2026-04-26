from __future__ import annotations

from polaris.cells.roles.kernel.internal.events import LLMEventType, emit_llm_event, get_global_emitter


def test_emit_llm_event_accepts_validation_errors_field() -> None:
    emitter = get_global_emitter()
    emitter.clear_history()

    emit_llm_event(
        event_type=LLMEventType.VALIDATION_FAIL,
        role="director",
        run_id="run-1",
        task_id="task-1",
        errors=["invalid output"],
    )

    events = emitter.get_events(run_id="run-1", task_id="task-1", role="director", limit=10)
    assert len(events) == 1
    assert events[0].errors == ["invalid output"]


def test_emit_llm_event_preserves_unknown_kwargs_in_metadata() -> None:
    emitter = get_global_emitter()
    emitter.clear_history()

    emit_llm_event(
        event_type=LLMEventType.TOOL_RESULT,
        role="director",
        run_id="run-2",
        task_id="task-2",
        tool_name="write_file",
        tool_success=False,
        tool_error="boom",
    )

    events = emitter.get_events(run_id="run-2", task_id="task-2", role="director", limit=10)
    assert len(events) == 1
    extra = events[0].metadata.get("extra_fields")
    assert isinstance(extra, dict)
    assert extra.get("tool_name") == "write_file"
    assert extra.get("tool_success") is False
    assert extra.get("tool_error") == "boom"
