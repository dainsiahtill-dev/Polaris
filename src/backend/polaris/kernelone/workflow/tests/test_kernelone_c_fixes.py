"""Tests for KernelOne-C P0/P1 fixes.

Covers:
- Fix 1 (P0-3): ActivityExecution.attempt field presence and retry increment
- Fix 2 (P0-4): apply_patch_with_broadcast failure observability via logger
- Fix 3 (P0-6): io_events dispatch failure is logged, not silently swallowed
- Fix 4 (P1):   message_bus asyncio task leak on timeout
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import fields
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fix 1: ActivityExecution.attempt field
# ---------------------------------------------------------------------------


def test_activity_execution_attempt_field_default() -> None:
    """attempt field must exist and default to 0."""
    from polaris.kernelone.workflow.activity_runner import ActivityExecution

    exec_ = ActivityExecution(
        activity_id="a1",
        activity_name="test_act",
        workflow_id="wf1",
        input={},
    )
    assert exec_.attempt == 0, "attempt should default to 0"


def test_activity_execution_attempt_field_declared() -> None:
    """attempt must be a declared dataclass field, not a dynamic attribute."""
    from polaris.kernelone.workflow.activity_runner import ActivityExecution

    field_names = {f.name for f in fields(ActivityExecution)}
    assert "attempt" in field_names, "attempt must be a declared dataclass field"


@pytest.mark.asyncio
async def test_activity_execution_attempt_increments_on_retry() -> None:
    """Retry loop must increment execution.attempt correctly."""
    from polaris.kernelone.workflow.activity_runner import ActivityConfig, ActivityRunner

    call_count = 0

    async def flaky_handler() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("transient error")
        return "ok"

    runner = ActivityRunner(max_concurrent=5)
    runner.register_handler("flaky", flaky_handler)
    await runner.start()

    try:
        await runner.submit_activity(
            activity_id="act-retry",
            activity_name="flaky",
            workflow_id="wf-retry",
            input={},
            config=ActivityConfig(
                timeout_seconds=5,
                retry_policy={"max_attempts": 3, "initial_interval_seconds": 0.01, "backoff_coefficient": 1.0},
            ),
        )

        # Allow retry loop to complete
        for _ in range(50):
            await asyncio.sleep(0.05)
            status = await runner.get_activity_status("act-retry")
            if status and status.status in ("completed", "failed"):
                break

        status = await runner.get_activity_status("act-retry")
        assert status is not None
        assert status.status == "completed", f"Expected completed, got {status.status}: {status.error}"
        assert status.attempt == 3, f"Expected attempt=3, got {status.attempt}"
        # Verify no AttributeError occurred (field was always present)
    finally:
        await runner.stop()


# ---------------------------------------------------------------------------
# Fix 2: apply_patch_with_broadcast failure is logged
# ---------------------------------------------------------------------------


def test_apply_patch_failure_is_logged(tmp_path, caplog) -> None:
    """apply_patch_with_broadcast must log errors, not silently swallow them."""
    from polaris.kernelone.events.file_event_broadcaster import apply_patch_with_broadcast

    # Create a real workspace with a real file
    workspace = str(tmp_path)
    target = tmp_path / "target.txt"
    target.write_text("hello world\n", encoding="utf-8")

    # Force a failure by patching fs.workspace_write_text to raise
    with (
        patch(
            "polaris.kernelone.events.file_event_broadcaster.KernelFileSystem.workspace_write_text",
            side_effect=OSError("disk full"),
        ),
        caplog.at_level(logging.ERROR, logger="polaris.kernelone.events.file_event_broadcaster"),
    ):
        result = apply_patch_with_broadcast(
            workspace=workspace,
            target_file="target.txt",
            patch="+added line\n",
        )

    assert result["ok"] is False
    assert "disk full" in result.get("error", "")
    # The error must have been logged (not silently swallowed)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "Expected an ERROR log record for the patch failure"
    assert any("apply_patch_with_broadcast" in r.message for r in error_records)


# ---------------------------------------------------------------------------
# Fix 3: io_events dispatch failure is observable via logger.warning
# ---------------------------------------------------------------------------


def test_io_events_dispatch_failure_is_observable(caplog) -> None:
    """Realtime bridge failure must be logged as warning, not silently passed."""
    from polaris.kernelone.events import io_events

    with (
        patch(
            "polaris.kernelone.events.io_events.publish_llm_realtime_event",
            side_effect=RuntimeError("bridge connection refused"),
        ),
        caplog.at_level(logging.WARNING, logger="polaris.kernelone.events.io_events"),
    ):
        # Should not raise
        io_events._publish_llm_event_to_realtime_bridge(
            llm_events_path="/tmp/fake_llm_events.jsonl",
            event="test_event",
            role="pm",
            data={"stage": "start"},
            run_id="run-123",
            iteration=1,
            source="system",
            timestamp="2026-03-22T00:00:00Z",
        )

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "Expected a WARNING log record for bridge failure"
    # Verify the event_type is logged
    assert any("test_event" in r.message for r in warning_records)


def test_io_events_dispatch_failure_does_not_reraise() -> None:
    """Realtime bridge failure must not propagate (audit path must be protected)."""
    from polaris.kernelone.events import io_events

    with patch(
        "polaris.kernelone.events.io_events.publish_llm_realtime_event",
        side_effect=RuntimeError("fatal bridge error"),
    ):
        # Must not raise - function is best-effort
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


# ---------------------------------------------------------------------------
# Fix 4: message_bus timeout does not leak asyncio tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_bus_timeout_no_task_leak() -> None:
    """On handler timeout, all spawned asyncio tasks must be cancelled."""
    from polaris.kernelone.events.message_bus import MessageBus, MessageType

    bus = MessageBus()
    task_refs: list[asyncio.Task] = []

    async def slow_handler(msg) -> None:
        t = asyncio.current_task()
        if t:
            task_refs.append(t)
        # Never finishes within normal timeout
        await asyncio.sleep(60)

    await bus.subscribe(MessageType.TASK_SUBMITTED, slow_handler)

    # Use a very short timeout to trigger the path quickly
    with patch(
        "polaris.kernelone.events.message_bus._ASYNC_HANDLER_TIMEOUT_SECONDS",
        0.05,
    ):
        from polaris.kernelone.events.message_bus import Message

        msg = Message(
            type=MessageType.TASK_SUBMITTED,
            sender="test",
        )
        await bus.publish(msg)

    # After publish returns, allow event loop to process cancellations
    await asyncio.sleep(0.1)

    # All tasks spawned by the slow handler should be cancelled/done
    leaked = [t for t in task_refs if not t.done()]
    assert not leaked, f"Leaked {len(leaked)} asyncio task(s) after timeout"
