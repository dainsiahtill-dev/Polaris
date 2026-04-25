"""Tests for polaris.kernelone.audit.failure_hops."""

from __future__ import annotations

import json
import os
from pathlib import Path

from polaris.kernelone.audit.failure_hops import (
    _build_hop3,
    _collect_events,
    _derive_failure_code,
    _extract_tool_paths,
    _get_int,
    _get_str,
    _is_dict,
    _is_failed_observation,
    _safe_int,
    build_failure_hops,
    write_failure_index,
)


class TestSafeInt:
    def test_int(self) -> None:
        assert _safe_int(42) == 42

    def test_string_int(self) -> None:
        assert _safe_int("42") == 42

    def test_none(self) -> None:
        assert _safe_int(None) == 0

    def test_invalid(self) -> None:
        assert _safe_int("abc") == 0
        assert _safe_int("abc", default=-1) == -1


class TestIsDict:
    def test_true(self) -> None:
        assert _is_dict({}) is True
        assert _is_dict({"a": 1}) is True

    def test_false(self) -> None:
        assert _is_dict([]) is False
        assert _is_dict(None) is False
        assert _is_dict("str") is False


class TestGetStr:
    def test_existing(self) -> None:
        assert _get_str({"name": "test"}, "name") == "test"

    def test_none(self) -> None:
        assert _get_str({"name": None}, "name") == ""

    def test_missing(self) -> None:
        assert _get_str({}, "name", "default") == "default"


class TestGetInt:
    def test_existing(self) -> None:
        assert _get_int({"seq": 5}, "seq") == 5

    def test_none(self) -> None:
        assert _get_int({"seq": None}, "seq") == 0

    def test_invalid(self) -> None:
        assert _get_int({"seq": "abc"}, "seq", -1) == -1


class TestIsFailedObservation:
    def test_ok_false(self) -> None:
        assert _is_failed_observation({"kind": "observation", "ok": False}) is True

    def test_error_present(self) -> None:
        assert _is_failed_observation({"kind": "observation", "error": "boom"}) is True

    def test_output_ok_false(self) -> None:
        assert _is_failed_observation({"kind": "observation", "output": {"ok": False}}) is True

    def test_output_error(self) -> None:
        assert _is_failed_observation({"kind": "observation", "output": {"error": "x"}}) is True

    def test_not_observation(self) -> None:
        assert _is_failed_observation({"kind": "action"}) is False

    def test_success(self) -> None:
        assert _is_failed_observation({"kind": "observation", "ok": True}) is False


class TestCollectEvents:
    def test_empty_path(self) -> None:
        assert _collect_events("", run_id="r1", event_seq_start=0, event_seq_end=0) == []

    def test_nonexistent_path(self) -> None:
        assert _collect_events("/nonexistent", run_id="r1", event_seq_start=0, event_seq_end=0) == []

    def test_collect_and_filter(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        lines = [
            json.dumps({"seq": 1, "kind": "action", "refs": {"run_id": "r1"}}),
            json.dumps({"seq": 2, "kind": "observation", "refs": {"run_id": "r1"}}),
            json.dumps({"seq": 3, "kind": "observation", "refs": {"run_id": "r2"}}),
            json.dumps({"seq": 5, "kind": "observation", "refs": {"run_id": "r1"}}),
            "",  # empty line
            "not json",  # invalid line
        ]
        events_file.write_text("\n".join(lines), encoding="utf-8")

        result = _collect_events(str(events_file), run_id="r1", event_seq_start=1, event_seq_end=5)
        assert len(result) == 3
        assert result[0]["seq"] == 1
        assert result[-1]["seq"] == 5

    def test_no_run_id_filter(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        lines = [
            json.dumps({"seq": 1, "kind": "action", "refs": {"run_id": "r1"}}),
            json.dumps({"seq": 2, "kind": "observation", "refs": {}}),
        ]
        events_file.write_text("\n".join(lines), encoding="utf-8")
        result = _collect_events(str(events_file), run_id="", event_seq_start=0, event_seq_end=0)
        assert len(result) == 2


class TestExtractToolPaths:
    def test_from_output(self) -> None:
        event = {
            "output": {
                "tool_stdout_path": "/out",
                "tool_stderr_path": "/err",
            },
            "meta": {},
        }
        paths = _extract_tool_paths(event)
        assert paths["tool_stdout_path"] == "/out"
        assert paths["tool_stderr_path"] == "/err"

    def test_from_meta(self) -> None:
        event = {
            "output": {},
            "meta": {
                "stdout_path": "/meta_out",
            },
        }
        paths = _extract_tool_paths(event)
        assert paths["stdout_path"] == "/meta_out"

    def test_raw_output_paths(self) -> None:
        event = {
            "output": {},
            "meta": {
                "raw_output_paths": {
                    "tool_stdout_path": "/raw",
                },
            },
        }
        paths = _extract_tool_paths(event)
        assert paths["tool_stdout_path"] == "/raw"

    def test_no_paths(self) -> None:
        assert _extract_tool_paths({}) == {}


class TestDeriveFailureCode:
    def test_fallback(self) -> None:
        assert _derive_failure_code({}, "FALLBACK") == "FALLBACK"

    def test_from_output_failure_code(self) -> None:
        event = {"output": {"failure_code": "ERR_1"}}
        assert _derive_failure_code(event, "") == "ERR_1"

    def test_from_output_error_code(self) -> None:
        event = {"output": {"error_code": "ERR_2"}}
        assert _derive_failure_code(event, "") == "ERR_2"

    def test_from_event_error(self) -> None:
        event = {"error": "ERR_3"}
        assert _derive_failure_code(event, "") == "ERR_3"

    def test_from_output_error(self) -> None:
        event = {"output": {"error": "ERR_4"}}
        assert _derive_failure_code(event, "") == "ERR_4"

    def test_unknown(self) -> None:
        assert _derive_failure_code({}, "") == "UNKNOWN_FAILURE"


class TestBuildHop3:
    def test_with_paths(self) -> None:
        event = {
            "output": {
                "tool": "my_tool",
                "tool_stdout_path": "/out",
            },
        }
        hop3 = _build_hop3(event)
        assert hop3["source"] == "artifact_paths"
        assert hop3["tool"] == "my_tool"
        assert "paths" in hop3

    def test_with_output_error(self) -> None:
        event = {
            "output": {
                "tool": "my_tool",
                "error": "failed",
            },
        }
        hop3 = _build_hop3(event)
        assert hop3["source"] == "event_output"
        assert hop3["error"] == "failed"

    def test_with_event_error(self) -> None:
        event = {
            "output": {},
            "error": "event_err",
            "name": "my_tool",
        }
        hop3 = _build_hop3(event)
        assert hop3["source"] == "event_error"
        assert hop3["error"] == "event_err"

    def test_no_output(self) -> None:
        event = {"name": "my_tool"}
        hop3 = _build_hop3(event)
        assert hop3["source"] == "none"
        assert hop3.get("missing_artifacts") is True


class TestBuildFailureHops:
    def test_no_failed_events(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            json.dumps({"seq": 1, "kind": "action", "refs": {"run_id": "r1"}}) + "\n",
            encoding="utf-8",
        )
        result = build_failure_hops(
            str(events_file),
            run_id="r1",
            event_seq_start=0,
            event_seq_end=0,
        )
        assert result["has_failure"] is False
        assert result["ready"] is True
        assert result["failure_event_seq"] is None

    def test_with_failure(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        lines = [
            json.dumps({"seq": 1, "kind": "action", "name": "tool_a", "refs": {"run_id": "r1", "phase": "director"}}),
            json.dumps(
                {
                    "seq": 2,
                    "kind": "observation",
                    "name": "tool_a",
                    "ok": False,
                    "refs": {"run_id": "r1"},
                    "output": {"error": "boom"},
                }
            ),
        ]
        events_file.write_text("\n".join(lines), encoding="utf-8")
        result = build_failure_hops(
            str(events_file),
            run_id="r1",
            event_seq_start=0,
            event_seq_end=0,
        )
        assert result["has_failure"] is True
        assert result["failure_event_seq"] == 2
        assert result["hop1_phase"]["phase"] == "director"
        assert result["hop2_evidence"] is not None
        assert result["hop3_tool_output"] is not None
        assert result["hop3_tool_output"]["error"] == "boom"

    def test_missing_artifacts(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        lines = [
            json.dumps({"seq": 1, "kind": "action", "name": "tool_a", "refs": {"run_id": "r1"}}),
            json.dumps({"seq": 2, "kind": "observation", "name": "tool_a", "ok": False, "refs": {"run_id": "r1"}}),
        ]
        events_file.write_text("\n".join(lines), encoding="utf-8")
        result = build_failure_hops(
            str(events_file),
            run_id="r1",
            event_seq_start=0,
            event_seq_end=0,
        )
        assert result["has_failure"] is True
        assert result["ready"] is False
        assert result["hop3_tool_output"].get("missing_artifacts") is True


class TestWriteFailureIndex:
    def test_writes_file(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        payload = {"schema_version": 1}
        path = write_failure_index(str(run_dir), payload)
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            assert json.load(f) == payload

    def test_empty_run_dir(self) -> None:
        assert write_failure_index("", {}) == ""
