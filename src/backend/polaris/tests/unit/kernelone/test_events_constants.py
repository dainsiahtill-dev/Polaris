"""Tests for polaris.kernelone.events.constants (unified event type constants)."""

from __future__ import annotations

from polaris.kernelone.events import constants as const


class TestEventTypeConstants:
    """Tests for event type string constants."""

    def test_tool_lifecycle_constants(self) -> None:
        assert const.EVENT_TYPE_TOOL_CALL == "tool_call"
        assert const.EVENT_TYPE_TOOL_RESULT == "tool_result"
        assert const.EVENT_TYPE_TOOL_ERROR == "tool_error"
        assert const.EVENT_TYPE_TOOL_START == "tool_start"
        assert const.EVENT_TYPE_TOOL_END == "tool_end"

    def test_content_chunk_constants(self) -> None:
        assert const.EVENT_TYPE_CONTENT_CHUNK == "content_chunk"
        assert const.EVENT_TYPE_THINKING_CHUNK == "thinking_chunk"
        assert const.EVENT_TYPE_COMPLETE == "complete"

    def test_llm_event_constants(self) -> None:
        assert const.EVENT_TYPE_LLM_START == "llm_start"
        assert const.EVENT_TYPE_LLM_END == "llm_end"
        assert const.EVENT_TYPE_LLM_ERROR == "llm_error"

    def test_llm_call_lifecycle_aliases(self) -> None:
        assert const.EVENT_TYPE_LLM_CALL_START == "llm_call_start"
        assert const.EVENT_TYPE_LLM_CALL_END == "llm_call_end"
        assert const.EVENT_TYPE_LLM_RETRY == "llm_retry"

    def test_llm_realtime_observer_constants(self) -> None:
        assert const.EVENT_TYPE_LLM_WAITING == "llm_waiting"
        assert const.EVENT_TYPE_LLM_COMPLETED == "llm_completed"
        assert const.EVENT_TYPE_LLM_FAILED == "llm_failed"

    def test_session_event_constants(self) -> None:
        assert const.EVENT_TYPE_SESSION_START == "session_start"
        assert const.EVENT_TYPE_SESSION_END == "session_end"

    def test_task_event_constants(self) -> None:
        assert const.EVENT_TYPE_TASK_CREATED == "task.created"
        assert const.EVENT_TYPE_TASK_UPDATED == "task.updated"
        assert const.EVENT_TYPE_TASK_COMPLETED == "task.completed"
        assert const.EVENT_TYPE_TASK_FAILED == "task.failed"

    def test_audit_event_constants(self) -> None:
        assert const.EVENT_TYPE_FINGERPRINT == "fingerprint"
        assert const.EVENT_TYPE_STATE_SNAPSHOT == "state.snapshot"
        assert const.EVENT_TYPE_ERROR == "error"


class TestAllExported:
    """Verify all items in __all__ are present."""

    def test_all_items_present(self) -> None:
        for name in const.__all__:
            assert hasattr(const, name), f"Missing export: {name}"

    def test_all_count(self) -> None:
        # Count the expected number of exports
        assert len(const.__all__) >= 20

    def test_no_duplicate_all_entries(self) -> None:
        assert len(const.__all__) == len(set(const.__all__))


class TestEventTypeUniqueness:
    """Ensure no duplicate event type values."""

    def test_all_event_types_unique(self) -> None:
        values = [getattr(const, name) for name in const.__all__]
        assert len(values) == len(set(values)), "Duplicate event type values found"

    def test_tool_call_not_tool_result(self) -> None:
        assert const.EVENT_TYPE_TOOL_CALL != const.EVENT_TYPE_TOOL_RESULT

    def test_llm_start_not_llm_end(self) -> None:
        assert const.EVENT_TYPE_LLM_START != const.EVENT_TYPE_LLM_END

    def test_task_events_use_dot_notation(self) -> None:
        """Task events should use dot notation for hierarchy."""
        for name in const.__all__:
            if "TASK" in name:
                val = getattr(const, name)
                assert "." in val, f"{name}={val!r} should use dot notation"
