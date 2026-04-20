from __future__ import annotations

import pytest
from polaris.kernelone.context.context_os import (
    ContextOSInvariantViolation,
    validate_context_os_persisted_projection,
)


def test_validate_context_os_persisted_projection_accepts_derived_payload() -> None:
    payload = {
        "version": 1,
        "mode": "state_first_context_os_v1",
        "adapter_id": "generic",
        "working_state": {},
        "artifact_store": [],
        "episode_store": [],
        "budget_plan": None,
        "updated_at": "2026-03-27T00:00:00Z",
    }

    validated = validate_context_os_persisted_projection(payload)

    assert isinstance(validated, dict)
    assert validated["mode"] == "state_first_context_os_v1"


def test_validate_context_os_persisted_projection_rejects_raw_truth_keys() -> None:
    payload = {
        "mode": "state_first_context_os_v1",
        "messages": [{"role": "user", "content": "hello"}],
    }

    with pytest.raises(ContextOSInvariantViolation, match="forbidden truth keys"):
        validate_context_os_persisted_projection(payload)


def test_validate_context_os_persisted_projection_rejects_non_state_first_mode() -> None:
    payload = {
        "mode": "session_continuity_engine_v1",
        "working_state": {},
    }

    with pytest.raises(ContextOSInvariantViolation, match="state_first_context_os"):
        validate_context_os_persisted_projection(payload)


class TestDeepForbiddenKeyValidation:
    """Tests for recursive forbidden key detection in nested structures."""

    def test_rejects_nested_forbidden_key_in_dict(self) -> None:
        """Forbidden key nested inside a dict should be detected."""
        payload = {
            "mode": "state_first_context_os_v1",
            "working_state": {
                "user_profile": {
                    "history": [{"turn": 1}],
                },
            },
        }

        with pytest.raises(ContextOSInvariantViolation, match="working_state.user_profile.history"):
            validate_context_os_persisted_projection(payload)

    def test_rejects_nested_forbidden_key_in_list(self) -> None:
        """Forbidden key nested inside a list item should be detected."""
        payload = {
            "mode": "state_first_context_os_v1",
            "transcript_log": [
                {
                    "event_id": "e1",
                    "metadata": {
                        "messages": ["should", "not", "be", "here"],
                    },
                },
            ],
        }

        with pytest.raises(ContextOSInvariantViolation) as exc_info:
            validate_context_os_persisted_projection(payload)

        error_message = str(exc_info.value)
        # Check path components since [0] is hard to match in regex
        assert "transcript_log" in error_message
        assert "metadata.messages" in error_message

    def test_rejects_deeply_nested_forbidden_key(self) -> None:
        """Forbidden key nested 3+ levels deep should be detected."""
        payload = {
            "mode": "state_first_context_os_v1",
            "deep": {
                "nested": {
                    "structure": {
                        "conversation": "this is forbidden",
                    },
                },
            },
        }

        with pytest.raises(ContextOSInvariantViolation, match="deep.nested.structure.conversation"):
            validate_context_os_persisted_projection(payload)

    def test_rejects_multiple_nested_forbidden_keys(self) -> None:
        """Multiple forbidden keys at different nesting levels should all be reported."""
        payload = {
            "mode": "state_first_context_os_v1",
            "history": "top-level forbidden",
            "nested": {
                "messages": "also forbidden",
            },
        }

        with pytest.raises(ContextOSInvariantViolation) as exc_info:
            validate_context_os_persisted_projection(payload)

        error_message = str(exc_info.value)
        assert "history" in error_message
        assert "nested.messages" in error_message
