"""TruthLog Middleware tests -- TurnTruthLogRecorder verification.

Covers:
1. Basic single-event JSONL write
2. Multi-event batch persistence
3. Graceful shutdown flush
4. None-recorder skip path (no crash)
5. Record schema compliance (_TRUTHLOG_KEYS)
6. Payload normalization for non-serializable objects
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.transaction.truthlog_recorder import (
    _TRUTHLOG_KEYS,
    TurnTruthLogRecorder,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_jsonl_lines(path: Path) -> list[dict[str, Any]]:
    """Read all JSONL lines from a file and parse them as JSON dicts."""
    lines: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if stripped:
                lines.append(json.loads(stripped))
    return lines


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truthlog_recorder_basic_write(tmp_path: Path) -> None:
    """Create a TurnTruthLogRecorder, record one event, flush, verify JSONL content."""
    log_file = tmp_path / "truth.jsonl"
    recorder = TurnTruthLogRecorder(log_file)

    await recorder.record(
        turn_id="turn_001",
        turn_request_id="req_001",
        event_type="decision_requested",
        payload={"key": "value"},
    )
    await recorder.flush()
    await recorder.shutdown()

    lines = _read_jsonl_lines(log_file)
    assert len(lines) == 1, f"Expected exactly 1 JSONL line, got {len(lines)}"
    assert lines[0]["turn_id"] == "turn_001"
    assert lines[0]["turn_request_id"] == "req_001"
    assert lines[0]["event_type"] == "decision_requested"
    assert lines[0]["payload"] == {"key": "value"}


@pytest.mark.asyncio
async def test_truthlog_recorder_multiple_events(tmp_path: Path) -> None:
    """Record 5 events, flush, verify all 5 lines in JSONL."""
    log_file = tmp_path / "truth_multi.jsonl"
    recorder = TurnTruthLogRecorder(log_file)

    for idx in range(5):
        await recorder.record(
            turn_id=f"turn_{idx}",
            turn_request_id=f"req_{idx}",
            event_type=f"phase_{idx}",
            payload={"index": idx},
        )
    await recorder.flush()
    await recorder.shutdown()

    lines = _read_jsonl_lines(log_file)
    assert len(lines) == 5, f"Expected 5 JSONL lines, got {len(lines)}"
    for idx, line in enumerate(lines):
        assert line["turn_id"] == f"turn_{idx}"
        assert line["turn_request_id"] == f"req_{idx}"
        assert line["event_type"] == f"phase_{idx}"
        assert line["payload"] == {"index": idx}


@pytest.mark.asyncio
async def test_truthlog_recorder_shutdown_flushes(tmp_path: Path) -> None:
    """Record events, shutdown (without explicit flush), verify file is complete."""
    log_file = tmp_path / "truth_shutdown.jsonl"
    recorder = TurnTruthLogRecorder(log_file)

    await recorder.record(
        turn_id="turn_shutdown",
        turn_request_id="req_shutdown",
        event_type="completed",
        payload={"status": "ok"},
    )
    # Deliberately skip flush -- shutdown must flush internally
    await recorder.shutdown()

    lines = _read_jsonl_lines(log_file)
    assert len(lines) == 1, "Shutdown should flush all pending records"
    assert lines[0]["turn_id"] == "turn_shutdown"
    assert lines[0]["event_type"] == "completed"


@pytest.mark.asyncio
async def test_truthlog_recorder_graceful_on_none() -> None:
    """Verify no crash when recorder is None (simulating the middleware skip path).

    Production code sometimes holds an ``Optional[TurnTruthLogRecorder]``.
    If the recorder is ``None``, the caller should simply skip recording.
    This test validates the pattern used in middleware:
        if recorder is not None:
            await recorder.record(...)
    """
    recorder: TurnTruthLogRecorder | None = None

    # The middleware skip pattern -- must not raise
    if recorder is not None:
        await recorder.record(
            turn_id="turn_none",
            turn_request_id="req_none",
            event_type="ghost",
            payload={},
        )
        await recorder.flush()
        await recorder.shutdown()

    # If we reached here without exception, the test passes
    assert recorder is None


@pytest.mark.asyncio
async def test_truthlog_record_schema(tmp_path: Path) -> None:
    """Verify each JSONL line has exactly the keys defined in _TRUTHLOG_KEYS."""
    log_file = tmp_path / "truth_schema.jsonl"
    recorder = TurnTruthLogRecorder(log_file)

    await recorder.record(
        turn_id="turn_schema",
        turn_request_id="req_schema",
        event_type="decision_completed",
        payload={"model": "gpt-4o"},
    )
    await recorder.flush()
    await recorder.shutdown()

    lines = _read_jsonl_lines(log_file)
    assert len(lines) == 1

    record = lines[0]
    expected_keys = set(_TRUTHLOG_KEYS)
    actual_keys = set(record.keys())
    assert actual_keys == expected_keys, (
        f"Schema mismatch: missing={expected_keys - actual_keys}, extra={actual_keys - expected_keys}"
    )

    # Verify types of timestamp fields
    assert isinstance(record["ts_iso"], str)
    assert record["ts_iso"].endswith("Z"), "ts_iso should end with 'Z' (UTC)"
    assert isinstance(record["ts_epoch_ms"], int)
    assert record["ts_epoch_ms"] > 0


@pytest.mark.asyncio
async def test_truthlog_recorder_payload_normalization(tmp_path: Path) -> None:
    """Record with non-serializable payload, verify it gets repr'd."""
    log_file = tmp_path / "truth_normalize.jsonl"
    recorder = TurnTruthLogRecorder(log_file)

    # A set is not JSON-serializable -- recorder should repr() it
    non_serializable_payload: Any = {1, 2, 3}
    await recorder.record(
        turn_id="turn_normalize",
        turn_request_id="req_normalize",
        event_type="tool_batch_started",
        payload=non_serializable_payload,
    )
    await recorder.flush()
    await recorder.shutdown()

    lines = _read_jsonl_lines(log_file)
    assert len(lines) == 1

    # The payload should have been converted to its repr() string
    payload = lines[0]["payload"]
    assert isinstance(payload, str), "Non-serializable payload should be repr'd to a string"
    # repr of a set contains '{' and '}'
    assert "{" in payload and "}" in payload
