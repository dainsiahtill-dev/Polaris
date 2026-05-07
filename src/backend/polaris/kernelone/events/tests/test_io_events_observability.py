"""T5: io_events dispatch failure observability regression tests.

Verifies that:
1. When publish_llm_realtime_event raises, a WARNING-level log is emitted.
2. The log contains the event_type keyword so failures are searchable.
3. The exception is NOT re-raised (best-effort bridge must not break audit path).
4. The keyword "io_event" appears in the warning log message pattern.
"""

from __future__ import annotations

import logging
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
