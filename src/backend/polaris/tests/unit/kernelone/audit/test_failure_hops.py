"""Unit tests for polaris.kernelone.audit.failure_hops."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from polaris.kernelone.audit.failure_hops import (
    _collect_events,
    _derive_failure_code,
    _extract_tool_paths,
    _is_failed_observation,
    build_failure_hops,
    write_failure_index,
)


def _make_event(
    seq: int,
    kind: str = "observation",
    name: str = "test_tool",
    ok: bool = True,
    error: str = "",
    output: dict[str, Any] | None = None,
    refs: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "seq": seq,
        "kind": kind,
        "name": name,
        "actor": "director",
        "summary": "summary",
    }
    if refs:
        event["refs"] = refs
    if output is not None:
        event["output"] = output
    if meta is not None:
        event["meta"] = meta
    if error:
        event["error"] = error
    if not ok:
        event["ok"] = False
    return event


class TestIsFailedObservation:
    def test_ok_observation(self) -> None:
        assert _is_failed_observation(_make_event(1, ok=True)) is False

    def test_failed_observation_ok_false(self) -> None:
        assert _is_failed_observation(_make_event(1, ok=False)) is True

    def test_failed_observation_with_error(self) -> None:
        assert _is_failed_observation(_make_event(1, error="boom")) is True

    def test_failed_observation_output_error(self) -> None:
        assert _is_failed_observation(_make_event(1, output={"ok": False})) is True

    def test_non_observation(self) -> None:
        assert _is_failed_observation(_make_event(1, kind="action")) is False


class TestExtractToolPaths:
    def test_from_output(self) -> None:
        event = _make_event(1, output={"tool_stdout_path": "/tmp/out", "tool_stderr_path": "/tmp/err"})
        paths = _extract_tool_paths(event)
        assert paths["tool_stdout_path"] == "/tmp/out"
        assert paths["tool_stderr_path"] == "/tmp/err"

    def test_from_meta(self) -> None:
        event = _make_event(1, output={}, meta={"stdout_path": "/tmp/out"})
        paths = _extract_tool_paths(event)
        assert paths.get("stdout_path") == "/tmp/out"

    def test_raw_output_paths(self) -> None:
        event = _make_event(
            1,
            output={},
            meta={"raw_output_paths": {"tool_stdout_path": "/tmp/raw"}},
        )
        paths = _extract_tool_paths(event)
        assert paths["tool_stdout_path"] == "/tmp/raw"


class TestDeriveFailureCode:
    def test_fallback_used(self) -> None:
        assert _derive_failure_code({}, "CUSTOM") == "CUSTOM"

    def test_from_output_failure_code(self) -> None:
        event = _make_event(1, output={"failure_code": "FC1"})
        assert _derive_failure_code(event, "") == "FC1"

    def test_from_output_error_code(self) -> None:
        event = _make_event(1, output={"error_code": "EC1"})
        assert _derive_failure_code(event, "") == "EC1"

    def test_from_event_error(self) -> None:
        event = _make_event(1, error="EVENT_ERR")
        assert _derive_failure_code(event, "") == "EVENT_ERR"

    def test_from_output_error(self) -> None:
        event = _make_event(1, output={"error": "OUT_ERR"})
        assert _derive_failure_code(event, "") == "OUT_ERR"

    def test_unknown_fallback(self) -> None:
        assert _derive_failure_code({}, "") == "UNKNOWN_FAILURE"


class TestCollectEvents:
    def test_missing_file(self) -> None:
        assert _collect_events("/nonexistent", run_id="r1", event_seq_start=0, event_seq_end=0) == []

    def test_basic_collection(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        lines = [
            json.dumps(_make_event(1, kind="action", name="tool")),
            json.dumps(_make_event(2, ok=False, refs={"run_id": "r1"})),
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        events = _collect_events(str(path), run_id="r1", event_seq_start=0, event_seq_end=0)
        assert len(events) == 2
        assert events[0]["seq"] == 1
        assert events[1]["seq"] == 2

    def test_seq_filter(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        lines = [
            json.dumps(_make_event(1)),
            json.dumps(_make_event(5)),
            json.dumps(_make_event(10)),
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        events = _collect_events(str(path), run_id="", event_seq_start=4, event_seq_end=8)
        assert len(events) == 1
        assert events[0]["seq"] == 5

    def test_run_id_filter(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        lines = [
            json.dumps(_make_event(1, refs={"run_id": "r1"})),
            json.dumps(_make_event(2, refs={"run_id": "r2"})),
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        events = _collect_events(str(path), run_id="r1", event_seq_start=0, event_seq_end=0)
        assert len(events) == 1
        assert events[0]["refs"]["run_id"] == "r1"

    def test_malformed_json_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text('bad json\n{"seq":1}\n', encoding="utf-8")
        events = _collect_events(str(path), run_id="", event_seq_start=0, event_seq_end=0)
        assert len(events) == 1

    def test_non_dict_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text('[1, 2]\n{"seq":1}\n', encoding="utf-8")
        events = _collect_events(str(path), run_id="", event_seq_start=0, event_seq_end=0)
        assert len(events) == 1


class TestBuildFailureHops:
    def test_no_failures(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text(
            json.dumps(_make_event(1, kind="action", name="tool")) + "\n",
            encoding="utf-8",
        )
        result = build_failure_hops(
            str(path),
            run_id="r1",
            event_seq_start=0,
            event_seq_end=0,
        )
        assert result["has_failure"] is False
        assert result["ready"] is True

    def test_with_failure(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        lines = [
            json.dumps(_make_event(1, kind="action", name="tool", refs={"phase": "plan", "run_id": "r1"})),
            json.dumps(
                _make_event(2, ok=False, name="tool", refs={"phase": "plan", "run_id": "r1"}, output={"error": "fail"})
            ),
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        result = build_failure_hops(
            str(path),
            run_id="r1",
            event_seq_start=0,
            event_seq_end=0,
        )
        assert result["has_failure"] is True
        assert result["failure_event_seq"] == 2
        assert result["hop1_phase"]["phase"] == "plan"
        assert result["hop3_tool_output"]["source"] == "event_output"
        assert result["ready"] is True

    def test_missing_artifacts_marks_not_ready(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        lines = [
            json.dumps(_make_event(1, kind="action", name="tool", refs={"run_id": "r1"})),
            json.dumps(_make_event(2, ok=False, name="tool", refs={"run_id": "r1"})),
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        result = build_failure_hops(
            str(path),
            run_id="r1",
            event_seq_start=0,
            event_seq_end=0,
        )
        assert result["has_failure"] is True
        assert result["ready"] is False
        assert result["hop3_tool_output"]["missing_artifacts"] is True


class TestWriteFailureIndex:
    def test_empty_run_dir(self) -> None:
        assert write_failure_index("", {}) == ""

    def test_writes_file(self, tmp_path: Path) -> None:
        run_dir = str(tmp_path)
        payload = {"schema_version": 1}
        result = write_failure_index(run_dir, payload)
        assert result == os.path.join(run_dir, "failure_hops.json")
        assert Path(result).exists()
