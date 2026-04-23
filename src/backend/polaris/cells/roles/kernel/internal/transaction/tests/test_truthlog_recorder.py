from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from polaris.cells.roles.kernel.internal.transaction.truthlog_recorder import (
    _TRUTHLOG_KEYS,
    TurnTruthLogRecorder,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []
    for line in content.splitlines():
        token = line.strip()
        if not token:
            continue
        parsed = json.loads(token)
        assert isinstance(parsed, dict)
        records.append(parsed)
    return records


def _new_case_log_path(prefix: str) -> tuple[Path, Path]:
    base_dir = Path(__file__).resolve().parent / "_truthlog_cases"
    case_dir = base_dir / f"{prefix}_{uuid4().hex}"
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir, case_dir / "runtime" / "truthlog" / "turn.events.jsonl"


@pytest.mark.asyncio
async def test_truthlog_recorder_happy_path_order_and_required_fields() -> None:
    case_dir, log_path = _new_case_log_path("truthlog_case_happy")
    try:
        recorder = TurnTruthLogRecorder(log_path)

        await recorder.record(
            turn_id="turn-1",
            turn_request_id="req-1",
            event_type="turn_started",
            payload={"step": 1},
        )
        await recorder.record(
            turn_id="turn-1",
            turn_request_id="req-1",
            event_type="tool_result",
            payload={"non_serializable": {1}},
        )
        await recorder.flush()
        await recorder.shutdown()

        rows = _read_jsonl(log_path)
        assert len(rows) == 2
        assert rows[0]["event_type"] == "turn_started"
        assert rows[1]["event_type"] == "tool_result"

        expected_keys = set(_TRUTHLOG_KEYS)
        for item in rows:
            assert set(item.keys()) == expected_keys
            assert isinstance(item["ts_iso"], str)
            assert isinstance(item["ts_epoch_ms"], int)
            assert item["turn_id"] == "turn-1"
            assert item["turn_request_id"] == "req-1"

        # Non-JSON-serializable payload is downgraded to repr string.
        assert isinstance(rows[1]["payload"], str)
        assert "non_serializable" in rows[1]["payload"]
    finally:
        shutil.rmtree(case_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_truthlog_recorder_flush_persists_records() -> None:
    case_dir, log_path = _new_case_log_path("truthlog_case_flush")
    try:
        recorder = TurnTruthLogRecorder(log_path)

        await recorder.record(
            turn_id="turn-2",
            turn_request_id="req-2",
            event_type="event_before_flush",
            payload={"ok": True},
        )
        await recorder.flush()

        assert log_path.exists()
        rows = _read_jsonl(log_path)
        assert len(rows) == 1
        assert rows[0]["event_type"] == "event_before_flush"

        await recorder.shutdown()
    finally:
        shutil.rmtree(case_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_truthlog_recorder_rejects_record_after_shutdown() -> None:
    case_dir, log_path = _new_case_log_path("truthlog_case_shutdown")
    try:
        recorder = TurnTruthLogRecorder(log_path)

        await recorder.record(
            turn_id="turn-3",
            turn_request_id="req-3",
            event_type="before_shutdown",
            payload={"value": 1},
        )
        await recorder.shutdown()

        with pytest.raises(RuntimeError, match="shut down"):
            await recorder.record(
                turn_id="turn-3",
                turn_request_id="req-3",
                event_type="after_shutdown",
                payload={"value": 2},
            )

        rows = _read_jsonl(log_path)
        assert len(rows) == 1
        assert rows[0]["event_type"] == "before_shutdown"
    finally:
        shutil.rmtree(case_dir, ignore_errors=True)
