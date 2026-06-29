from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.infrastructure.log_pipeline.llm_realtime_bridge import (
    LogPipelineLLMRealtimeBridge,
)
from polaris.kernelone.events.realtime_bridge import LLMRealtimeEvent


@pytest.fixture()
def bridge() -> LogPipelineLLMRealtimeBridge:
    return LogPipelineLLMRealtimeBridge()


def _make_event(**data_overrides: Any) -> LLMRealtimeEvent:
    base_data: dict[str, Any] = {
        "call_id": "call-abc-123",
        "turn_id": "turn-42",
        "context_snapshot_ref": "snapshot://ws/run/context-99",
        "model": "gpt-5.4",
        "provider": "openai",
        "task_id": "task-7",
    }
    base_data.update(data_overrides)
    return LLMRealtimeEvent(
        workspace="/tmp/test-ws",
        run_id="run-001",
        role="agent",
        event_type="llm_call_start",
        data=base_data,
    )


class TestBuildRefsRichFields:
    """_build_refs must populate call_id, turn_id, context_snapshot_ref, model, provider."""

    def test_refs_include_all_rich_fields(self, bridge: LogPipelineLLMRealtimeBridge) -> None:
        writer = MagicMock()
        event = _make_event()
        with patch(
            "polaris.infrastructure.log_pipeline.llm_realtime_bridge.get_writer",
            return_value=writer,
        ):
            bridge.publish(event)

        writer.write_event.assert_called_once()
        call_kwargs = writer.write_event.call_args
        refs = call_kwargs.kwargs.get("refs") or call_kwargs[1].get("refs")

        assert refs["task_id"] == "task-7"
        assert refs["call_id"] == "call-abc-123"
        assert refs["turn_id"] == "turn-42"
        assert refs["context_snapshot_ref"] == "snapshot://ws/run/context-99"
        assert refs["model"] == "gpt-5.4"
        assert refs["provider"] == "openai"

    def test_refs_use_provider_id_when_provider_missing(self, bridge: LogPipelineLLMRealtimeBridge) -> None:
        writer = MagicMock()
        event = _make_event(provider="", provider_id="azure-openai")
        with patch(
            "polaris.infrastructure.log_pipeline.llm_realtime_bridge.get_writer",
            return_value=writer,
        ):
            bridge.publish(event)

        refs = writer.write_event.call_args.kwargs.get("refs") or writer.write_event.call_args[1].get("refs")
        assert refs["provider"] == "azure-openai"

    def test_refs_omit_empty_fields(self, bridge: LogPipelineLLMRealtimeBridge) -> None:
        writer = MagicMock()
        event = _make_event(
            call_id="",
            turn_id="",
            context_snapshot_ref="",
            model="",
            provider="",
            provider_id="",
        )
        with patch(
            "polaris.infrastructure.log_pipeline.llm_realtime_bridge.get_writer",
            return_value=writer,
        ):
            bridge.publish(event)

        refs = writer.write_event.call_args.kwargs.get("refs") or writer.write_event.call_args[1].get("refs")
        assert "call_id" not in refs
        assert "turn_id" not in refs
        assert "context_snapshot_ref" not in refs
        assert "model" not in refs
        assert "provider" not in refs

    def test_refs_extract_from_metadata(self, bridge: LogPipelineLLMRealtimeBridge) -> None:
        writer = MagicMock()
        event = LLMRealtimeEvent(
            workspace="/tmp/test-ws",
            run_id="run-002",
            role="agent",
            event_type="llm_call_start",
            data={
                "metadata": {
                    "call_id": "meta-call-1",
                    "turn_id": "meta-turn-1",
                    "context_snapshot_ref": "meta-snap-1",
                    "model": "claude-4",
                    "provider": "anthropic",
                    "task_id": "meta-task-1",
                },
            },
        )
        with patch(
            "polaris.infrastructure.log_pipeline.llm_realtime_bridge.get_writer",
            return_value=writer,
        ):
            bridge.publish(event)

        refs = writer.write_event.call_args.kwargs.get("refs") or writer.write_event.call_args[1].get("refs")
        assert refs["task_id"] == "meta-task-1"
        assert refs["call_id"] == "meta-call-1"
        assert refs["turn_id"] == "meta-turn-1"
        assert refs["context_snapshot_ref"] == "meta-snap-1"
        assert refs["model"] == "claude-4"
        assert refs["provider"] == "anthropic"

    def test_refs_include_final_request_audit_projection(self, bridge: LogPipelineLLMRealtimeBridge) -> None:
        writer = MagicMock()
        audit = {
            "schema_version": "llm.final_request_context_audit.v1",
            "final_request_evidence_coverage": {
                "request_hash": "request-hash-4",
                "pass": False,
                "missing_required_refs": ["execution_envelope"],
                "missing_required_tools": ["write_file"],
            },
        }
        event = LLMRealtimeEvent(
            workspace="/tmp/test-ws",
            run_id="run-final-request",
            role="director",
            event_type="llm_call_start",
            data={
                "metadata": {
                    "context_snapshot_ref": "runtime/contexts/aa/bbbb.json",
                    "final_request_context_audit": audit,
                }
            },
        )
        with patch(
            "polaris.infrastructure.log_pipeline.llm_realtime_bridge.get_writer",
            return_value=writer,
        ):
            bridge.publish(event)

        refs = writer.write_event.call_args.kwargs.get("refs") or writer.write_event.call_args[1].get("refs")
        assert refs["context_snapshot_ref"] == "runtime/contexts/aa/bbbb.json"
        assert refs["final_request_evidence_hash"]
        assert refs["final_request_context_audit_hash"]
        assert refs["final_request_evidence_coverage_pass"] is False
        assert refs["missing_required_refs"] == ["execution_envelope"]
        assert refs["missing_required_tools"] == ["write_file"]


class TestRawDataPreserved:
    """raw.data must be the original event data dict, unmodified."""

    def test_raw_data_preserves_original_payload(self, bridge: LogPipelineLLMRealtimeBridge) -> None:
        writer = MagicMock()
        original_data = {
            "call_id": "call-abc-123",
            "turn_id": "turn-42",
            "context_snapshot_ref": "snapshot://ws/run/context-99",
            "model": "gpt-5.4",
            "provider": "openai",
            "task_id": "task-7",
            "extra_field": "should-survive",
        }
        event = LLMRealtimeEvent(
            workspace="/tmp/test-ws",
            run_id="run-003",
            role="agent",
            event_type="llm_call_start",
            data=original_data,
        )
        with patch(
            "polaris.infrastructure.log_pipeline.llm_realtime_bridge.get_writer",
            return_value=writer,
        ):
            bridge.publish(event)

        call_kwargs = writer.write_event.call_args
        raw = call_kwargs.kwargs.get("raw") or call_kwargs[1].get("raw")
        assert raw["data"]["call_id"] == "call-abc-123"
        assert raw["data"]["turn_id"] == "turn-42"
        assert raw["data"]["context_snapshot_ref"] == "snapshot://ws/run/context-99"
        assert raw["data"]["model"] == "gpt-5.4"
        assert raw["data"]["provider"] == "openai"
        assert raw["data"]["task_id"] == "task-7"
        assert raw["data"]["extra_field"] == "should-survive"


class TestExistingRefsBehavior:
    """Existing task_id and iteration behavior must not regress."""

    def test_task_id_and_iteration_still_present(self, bridge: LogPipelineLLMRealtimeBridge) -> None:
        writer = MagicMock()
        event = LLMRealtimeEvent(
            workspace="/tmp/test-ws",
            run_id="run-004",
            role="agent",
            event_type="iteration",
            iteration=3,
            data={"task_id": "legacy-task", "stage": "started"},
        )
        with patch(
            "polaris.infrastructure.log_pipeline.llm_realtime_bridge.get_writer",
            return_value=writer,
        ):
            bridge.publish(event)

        refs = writer.write_event.call_args.kwargs.get("refs") or writer.write_event.call_args[1].get("refs")
        assert refs["task_id"] == "legacy-task"
        assert refs["iteration"] == 3
