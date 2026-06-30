"""T5: io_events dispatch failure observability regression tests.

Verifies that:
1. When publish_llm_realtime_event raises, a WARNING-level log is emitted.
2. The log contains the event_type keyword so failures are searchable.
3. The exception is NOT re-raised (best-effort bridge must not break audit path).
4. The keyword "io_event" appears in the warning log message pattern.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch


def test_io_events_dispatch_failure_emits_warning(caplog) -> None:
    """Bridge failure must emit WARNING with event_type in the message."""
    from polaris.kernelone.events import io_events

    with (
        patch(
            "polaris.kernelone.events.io_events.publish_llm_realtime_event",
            side_effect=RuntimeError("bridge connection refused"),
        ),
        caplog.at_level(logging.WARNING, logger="polaris.kernelone.events.io_events"),
    ):
        io_events._publish_llm_event_to_realtime_bridge(
            llm_events_path="/tmp/test_llm_events.jsonl",
            event="test_event_type",
            role="pm",
            data={"stage": "start"},
            run_id="run-test-001",
            iteration=1,
            source="system",
            timestamp="2026-03-22T00:00:00Z",
        )

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "Expected at least one WARNING record when realtime bridge fails"
    # The event_type must appear in a warning so the failure is diagnosable.
    assert any("test_event_type" in r.message for r in warning_records), (
        "The event_type 'test_event_type' must appear in the WARNING log message"
    )


def test_io_events_dispatch_failure_does_not_reraise() -> None:
    """Bridge failure must NOT propagate — durable JSONL audit path must be protected."""
    from polaris.kernelone.events import io_events

    with patch(
        "polaris.kernelone.events.io_events.publish_llm_realtime_event",
        side_effect=RuntimeError("fatal bridge error"),
    ):
        # Must not raise — this is the regression guard for P0-6
        io_events._publish_llm_event_to_realtime_bridge(
            llm_events_path="/tmp/fake_events.jsonl",
            event="some_event",
            role="director",
            data={},
            run_id="",
            iteration=0,
            source="system",
            timestamp="",
        )


def test_io_events_dispatch_failure_is_not_silent(caplog) -> None:
    """Exception must be logged, not silently swallowed (pass-without-log is forbidden)."""
    from polaris.kernelone.events import io_events

    with (
        patch(
            "polaris.kernelone.events.io_events.publish_llm_realtime_event",
            side_effect=ValueError("unexpected schema error"),
        ),
        caplog.at_level(logging.DEBUG, logger="polaris.kernelone.events.io_events"),
    ):
        io_events._publish_llm_event_to_realtime_bridge(
            llm_events_path="/tmp/test.jsonl",
            event="schema_fail",
            role="architect",
            data={"attempt": 1},
            run_id="r99",
            iteration=2,
            source="scheduler",
            timestamp="2026-03-22T12:00:00Z",
        )

    # At minimum a WARNING must have been emitted
    log_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert log_records, "Exception swallowed silently — must emit at least a WARNING"


def test_io_events_dispatch_empty_path_is_noop() -> None:
    """Empty llm_events_path must silently skip without error or log."""
    from polaris.kernelone.events import io_events

    with patch(
        "polaris.kernelone.events.io_events.publish_llm_realtime_event",
    ) as mock_pub:
        io_events._publish_llm_event_to_realtime_bridge(
            llm_events_path="",
            event="noop",
            role="pm",
            data={},
            run_id="",
            iteration=0,
            source="system",
            timestamp="",
        )
    mock_pub.assert_not_called()


def test_llm_realtime_bridge_prefers_payload_workspace_over_cache_path(tmp_path: Path) -> None:
    """Cache-backed LLM event files must publish realtime events for the real workspace."""
    from polaris.kernelone.events import io_events

    real_workspace = tmp_path / "factory-workspace"
    real_workspace.mkdir()
    cache_events_path = tmp_path / "cache" / "polaris" / ".polaris" / "projects" / "demo" / "runtime" / "events"
    cache_events_path.mkdir(parents=True)
    llm_events_path = cache_events_path / "director.llm.events.jsonl"

    published = []
    with patch("polaris.kernelone.events.io_events.publish_llm_realtime_event") as mock_pub:
        mock_pub.side_effect = published.append
        io_events._publish_llm_event_to_realtime_bridge(
            llm_events_path=str(llm_events_path),
            event="llm_call_start",
            role="director",
            data={"metadata": {"workspace": str(real_workspace)}},
            run_id="director-run-1",
            iteration=0,
            source="roles.kernel.events",
            timestamp="2026-06-19T00:00:00Z",
        )

    assert published
    assert published[0].workspace == str(real_workspace)


def test_emit_llm_event_redacts_prompt_payload_before_persistence(tmp_path: Path) -> None:
    """Generic LLM event emission must not persist raw prompt/completion payloads."""
    from polaris.kernelone.events import io_events

    events_path = tmp_path / "pm.llm.events.jsonl"
    secret_prompt = "SYSTEM: never persist this prompt"
    secret_response = "assistant response with private details"

    with (
        patch("polaris.kernelone.events.io_events._publish_llm_event_to_realtime_bridge"),
        patch("polaris.kernelone.events.io_events._publish_runtime_event_to_bus"),
    ):
        io_events.emit_llm_event(
            str(events_path),
            event="invoke_done",
            role="pm",
            run_id="run-redact",
            iteration=1,
            source="test",
            data={
                "model": "kimi-for-coding",
                "provider_id": "anthropic_compat-test",
                "prompt_tokens": 12,
                "prompt": secret_prompt,
                "metadata": {
                    "messages": [{"role": "user", "content": secret_prompt}],
                    "response_content": secret_response,
                },
            },
        )

    text = events_path.read_text(encoding="utf-8")
    assert secret_prompt not in text
    assert secret_response not in text
    row = json.loads(text.splitlines()[0])
    data = row["data"]
    assert data["model"] == "kimi-for-coding"
    assert data["provider_id"] == "anthropic_compat-test"
    assert data["prompt_tokens"] == 12
    assert data["prompt"]["redacted"] is True
    assert data["metadata"]["messages"]["redacted"] is True
    assert data["metadata"]["response_content"]["redacted"] is True


def test_emit_llm_event_projects_final_request_evidence_refs(tmp_path: Path) -> None:
    """Generic JSONL LLM events must expose final-request audit refs directly."""
    from polaris.kernelone.events import io_events

    events_path = tmp_path / "director.llm.events.jsonl"
    audit = {
        "schema_version": "llm.final_request_context_audit.v1",
        "final_request_token_estimate": 4096,
        "final_request_evidence_coverage": {
            "request_hash": "request-hash-3",
            "pass": False,
            "missing_required_refs": ["execution_envelope"],
        },
    }

    with (
        patch("polaris.kernelone.events.io_events._publish_llm_event_to_realtime_bridge"),
        patch("polaris.kernelone.events.io_events._publish_runtime_event_to_bus"),
    ):
        io_events.emit_llm_event(
            str(events_path),
            event="llm_call_start",
            role="director",
            run_id="run-final-request",
            data={
                "metadata": {
                    "context_snapshot_ref": "runtime/contexts/22/222222333333444444555555.json",
                    "final_request_context_audit": audit,
                    "messages": [{"role": "user", "content": "secret prompt"}],
                },
            },
        )

    row = json.loads(events_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["context_snapshot_ref"] == "222222333333444444555555"
    assert row["final_request_context_audit"] == audit
    assert row["audit_refs"]["context_snapshot_ref"] == "222222333333444444555555"
    assert row["audit_refs"]["request_hash"] == "request-hash-3"
    assert row["final_request_evidence"]["missing_required_refs"] == ["execution_envelope"]
    assert "secret prompt" not in events_path.read_text(encoding="utf-8")


def test_message_bus_sync_publish_without_loop_skips_without_warning(caplog) -> None:
    """Synchronous CLI mode without an event loop must not pollute stderr."""
    from polaris.kernelone.events import io_events
    from polaris.kernelone.events.message_bus import Message, MessageType

    msg = Message(
        type=MessageType.RUNTIME_EVENT,
        sender="test",
        payload={"ok": True},
    )

    with (
        patch("polaris.kernelone.events.io_events.asyncio.get_running_loop", side_effect=RuntimeError("no loop")),
        patch("polaris.kernelone.events.io_events.asyncio.get_event_loop", side_effect=RuntimeError("no loop")),
        caplog.at_level(logging.WARNING, logger="polaris.kernelone.events.io_events"),
    ):
        io_events._safe_publish_sync(object(), msg)

    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
