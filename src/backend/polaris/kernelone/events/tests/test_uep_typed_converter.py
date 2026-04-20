"""Unit tests for UEPToTypedEventConverter."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.kernelone.events.uep_typed_converter import UEPToTypedEventConverter


class TestUEPToTypedEventConverter:
    """Test suite for UEP to TypedEvent conversion."""

    @pytest.fixture
    def converter(self) -> UEPToTypedEventConverter:
        """Create a converter instance."""
        return UEPToTypedEventConverter()

    # -------------------------------------------------------------------------
    # Stream Events
    # -------------------------------------------------------------------------

    def test_convert_tool_call_to_tool_invoked(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that tool_call UEP event converts to ToolInvoked."""
        payload: dict[str, Any] = {
            "topic": "runtime.event.stream",
            "event_type": "tool_call",
            "run_id": "run-123",
            "workspace": "/workspace",
            "role": "director",
            "turn_id": "turn-456",
            "payload": {
                "tool": "read_file",
                "args": {"path": "test.py"},
                "call_id": "call-789",
            },
        }

        result = converter.convert(payload)

        assert result is not None
        assert result.event_name == "tool_invoked"
        assert result.payload.tool_name == "read_file"
        assert result.payload.tool_call_id == "call-789"
        assert result.payload.arguments == {"path": "test.py"}

    def test_convert_tool_result_to_tool_completed(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that tool_result UEP event converts to ToolCompleted."""
        payload: dict[str, Any] = {
            "topic": "runtime.event.stream",
            "event_type": "tool_result",
            "run_id": "run-123",
            "workspace": "/workspace",
            "role": "director",
            "payload": {
                "tool": "read_file",
                "result": "file content here",
                "call_id": "call-789",
            },
        }

        result = converter.convert(payload)

        assert result is not None
        assert result.event_name == "tool_completed"
        assert result.payload.tool_name == "read_file"
        assert result.payload.tool_call_id == "call-789"

    def test_convert_tool_error_to_tool_error(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that tool_error UEP event converts to ToolError."""
        payload: dict[str, Any] = {
            "topic": "runtime.event.stream",
            "event_type": "tool_error",
            "run_id": "run-123",
            "workspace": "/workspace",
            "role": "director",
            "payload": {
                "tool": "read_file",
                "error": {"message": "File not found", "traceback": "..."},
                "call_id": "call-789",
            },
        }

        result = converter.convert(payload)

        assert result is not None
        assert result.event_name == "tool_error"
        assert result.payload.tool_name == "read_file"
        assert result.payload.error == "File not found"

    def test_convert_content_chunk_to_turn_started(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that content_chunk UEP event converts to TurnStarted."""
        payload: dict[str, Any] = {
            "topic": "runtime.event.stream",
            "event_type": "content_chunk",
            "run_id": "run-123",
            "workspace": "/workspace",
            "role": "director",
            "turn_id": "turn-456",
            "payload": {"content": "Hello world"},
        }

        result = converter.convert(payload)

        assert result is not None
        assert result.event_name == "turn_started"
        assert result.payload.agent == "director"

    def test_convert_complete_to_turn_completed(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that complete UEP event converts to TurnCompleted."""
        payload: dict[str, Any] = {
            "topic": "runtime.event.stream",
            "event_type": "complete",
            "run_id": "run-123",
            "workspace": "/workspace",
            "role": "director",
            "turn_id": "turn-456",
            "payload": {"finish_reason": "stop"},
            "metadata": {"usage": {"total_tokens": 100}},
        }

        result = converter.convert(payload)

        assert result is not None
        assert result.event_name == "turn_completed"

    # -------------------------------------------------------------------------
    # LLM Lifecycle Events
    # -------------------------------------------------------------------------

    def test_convert_call_start_to_instance_started(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that llm_call_start UEP event converts to InstanceStarted."""
        payload: dict[str, Any] = {
            "topic": "runtime.event.llm",
            "event_type": "llm_call_start",
            "run_id": "run-123",
            "workspace": "/workspace",
            "role": "director",
            "metadata": {"model": "gpt-4", "provider": "openai"},
        }

        result = converter.convert(payload)

        assert result is not None
        assert result.event_name == "instance_started"
        assert result.payload.instance_type == "llm.director"

    def test_convert_call_end_to_instance_disposed(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that llm_call_end UEP event converts to InstanceDisposed."""
        payload: dict[str, Any] = {
            "topic": "runtime.event.llm",
            "event_type": "llm_call_end",
            "run_id": "run-123",
            "workspace": "/workspace",
            "role": "director",
        }

        result = converter.convert(payload)

        assert result is not None
        assert result.event_name == "instance_disposed"
        assert result.payload.reason == "llm_call_end"

    # -------------------------------------------------------------------------
    # Fingerprint Events
    # -------------------------------------------------------------------------

    def test_convert_fingerprint_to_plan_created(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that fingerprint UEP event converts to PlanCreated."""
        payload: dict[str, Any] = {
            "topic": "runtime.event.fingerprint",
            "event_type": "fingerprint",
            "run_id": "run-123",
            "workspace": "/workspace",
            "role": "director",
            "payload": {
                "profile_id": "director-v1",
                "bundle_id": "test-bundle",
            },
        }

        result = converter.convert(payload)

        assert result is not None
        assert result.event_name == "plan_created"
        assert result.payload.plan_id == "director-v1"

    # -------------------------------------------------------------------------
    # Audit Events
    # -------------------------------------------------------------------------

    def test_convert_audit_to_audit_completed(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that audit UEP event converts to AuditCompleted.

        Audit events from _handle_journal_events have verdict/data at top level
        (not nested under payload), matching the UEP publisher output format.
        """
        payload: dict[str, Any] = {
            "topic": "runtime.event.audit",
            "event_type": "security_check",
            "run_id": "run-123",
            "workspace": "/workspace",
            "role": "director",
            "verdict": "pass",
            "data": {"issue_count": 0},
        }

        result = converter.convert(payload)

        assert result is not None
        assert result.event_name == "audit_completed"
        assert result.payload.verdict == "pass"
        assert result.payload.issue_count == 0

    def test_convert_audit_with_nested_payload_structure(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test audit conversion with legacy nested payload structure.

        Old UEP payloads may have verdict/data inside a 'payload' sub-dict.
        The converter should handle both structures.
        """
        payload: dict[str, Any] = {
            "topic": "runtime.event.audit",
            "event_type": "security_check",
            "run_id": "run-123",
            "workspace": "/workspace",
            "role": "director",
            "payload": {
                "verdict": "fail",
                "data": {"issue_count": 2},
            },
        }

        result = converter.convert(payload)

        assert result is not None
        assert result.event_name == "audit_completed"
        assert result.payload.verdict == "fail"
        assert result.payload.issue_count == 2

    # -------------------------------------------------------------------------
    # Edge Cases
    # -------------------------------------------------------------------------

    def test_convert_unknown_topic_returns_none(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that unknown topic returns None."""
        payload: dict[str, Any] = {
            "topic": "unknown.topic",
            "event_type": "test",
            "run_id": "run-123",
            "workspace": "/workspace",
        }

        result = converter.convert(payload)

        assert result is None

    def test_convert_unknown_event_type_returns_none(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that unknown event_type returns None."""
        payload: dict[str, Any] = {
            "topic": "runtime.event.stream",
            "event_type": "unknown_event",
            "run_id": "run-123",
            "workspace": "/workspace",
        }

        result = converter.convert(payload)

        assert result is None

    def test_convert_preserves_run_id_and_workspace(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that run_id and workspace are preserved in conversion."""
        payload: dict[str, Any] = {
            "topic": "runtime.event.stream",
            "event_type": "tool_call",
            "run_id": "my-run-id",
            "workspace": "/my/workspace",
            "role": "director",
            "payload": {"tool": "test_tool", "call_id": "call-1"},
        }

        result = converter.convert(payload)

        assert result is not None
        assert result.run_id == "my-run-id"
        assert result.workspace == "/my/workspace"

    def test_convert_handles_missing_optional_fields(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test that missing optional fields are handled gracefully."""
        payload: dict[str, Any] = {
            "topic": "runtime.event.stream",
            "event_type": "tool_call",
            # Missing run_id, workspace, role, turn_id
            "payload": {"tool": "test_tool"},
        }

        result = converter.convert(payload)

        assert result is not None
        assert result.run_id == ""
        assert result.workspace == ""

    # -------------------------------------------------------------------------
    # Duration Extraction
    # -------------------------------------------------------------------------

    def test_extract_duration_ms_from_duration_ms(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test duration extraction from duration_ms field."""
        data = {"duration_ms": 1500}
        result = converter._extract_duration_ms(data)
        assert result == 1500

    def test_extract_duration_ms_from_latency_ms(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test duration extraction from latency_ms field."""
        data = {"latency_ms": 2000}
        result = converter._extract_duration_ms(data)
        assert result == 2000

    def test_extract_duration_ms_returns_none_when_missing(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test duration extraction returns None when no duration field."""
        data = {"other_field": "value"}
        result = converter._extract_duration_ms(data)
        assert result is None

    # -------------------------------------------------------------------------
    # Error Classification
    # -------------------------------------------------------------------------

    def test_classify_timeout_error(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test timeout error classification."""
        result = converter._classify_tool_error("Request timeout after 30s")
        assert result.value == "timeout"

    def test_classify_permission_error(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test permission error classification."""
        result = converter._classify_tool_error("Permission denied")
        assert result.value == "permission"

    def test_classify_unknown_error(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test unknown error classification."""
        result = converter._classify_tool_error("Something went wrong")
        assert result.value == "exception"

    def test_classify_none_error(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test None error returns UNKNOWN."""
        result = converter._classify_tool_error(None)
        assert result.value == "unknown"

    def test_classify_not_found_error(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test not found error classification."""
        result = converter._classify_tool_error("File does not exist")
        assert result.value == "not_found"

    def test_classify_validation_error(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test validation error classification."""
        result = converter._classify_tool_error("Invalid argument: path is required")
        assert result.value == "validation"

    def test_classify_cancelled_error(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test cancelled error classification."""
        result = converter._classify_tool_error("Operation was cancelled")
        assert result.value == "cancelled"

    def test_extract_duration_ms_from_elapsed_ms(
        self,
        converter: UEPToTypedEventConverter,
    ) -> None:
        """Test duration extraction from elapsed_ms field."""
        data = {"elapsed_ms": 3000}
        result = converter._extract_duration_ms(data)
        assert result == 3000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
