"""Tests for polaris.cells.audit.diagnosis.internal.toolkit.hops.

Covers failure hops building and loading with temp files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.cells.audit.diagnosis.internal.toolkit.hops import (
    _collect_events,
    _derive_failure_code,
    _extract_tool_paths,
    _is_failed_observation,
    analyze_failure_chain,
    build_failure_hops,
    load_failure_hops,
    save_failure_hops,
)


class TestIsFailedObservation:
    """Failed observation detection."""

    def test_ok_false_is_failed(self) -> None:
        assert _is_failed_observation({"kind": "observation", "ok": False}) is True

    def test_error_field_is_failed(self) -> None:
        assert _is_failed_observation({"kind": "observation", "error": "boom"}) is True

    def test_output_ok_false_is_failed(self) -> None:
        assert _is_failed_observation({"kind": "observation", "output": {"ok": False}}) is True

    def test_output_error_is_failed(self) -> None:
        assert _is_failed_observation({"kind": "observation", "output": {"error": "fail"}}) is True

    def test_not_observation_is_not_failed(self) -> None:
        assert _is_failed_observation({"kind": "action"}) is False

    def test_ok_true_is_not_failed(self) -> None:
        assert _is_failed_observation({"kind": "observation", "ok": True}) is False

    def test_non_dict_is_not_failed(self) -> None:
        assert _is_failed_observation("not a dict") is False


class TestCollectEvents:
    """Event collection from JSONL files."""

    def test_collects_matching_events(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            json.dumps({"seq": 1, "kind": "action", "refs": {"run_id": "r1"}})
            + "\n"
            + json.dumps({"seq": 2, "kind": "observation", "refs": {"run_id": "r1"}})
            + "\n"
            + json.dumps({"seq": 3, "kind": "action", "refs": {"run_id": "r2"}})
            + "\n",
            encoding="utf-8",
        )

        result = _collect_events(str(events_file), run_id="r1", event_seq_start=0, event_seq_end=0)
        assert len(result) == 2
        assert result[0]["seq"] == 1
        assert result[1]["seq"] == 2

    def test_filters_by_seq_range(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        lines = "\n".join(json.dumps({"seq": i, "kind": "action", "refs": {"run_id": "r1"}}) for i in range(1, 6))
        events_file.write_text(lines + "\n", encoding="utf-8")

        result = _collect_events(str(events_file), run_id="r1", event_seq_start=2, event_seq_end=4)
        assert len(result) == 3
        assert result[0]["seq"] == 2
        assert result[-1]["seq"] == 4

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        result = _collect_events(str(tmp_path / "missing.jsonl"), run_id="r1", event_seq_start=0, event_seq_end=0)
        assert result == []

    def test_skips_bad_json(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            json.dumps({"seq": 1, "kind": "action", "refs": {"run_id": "r1"}}) + "\n"
            "not json\n" + json.dumps({"seq": 2, "kind": "action", "refs": {"run_id": "r1"}}) + "\n",
            encoding="utf-8",
        )

        result = _collect_events(str(events_file), run_id="r1", event_seq_start=0, event_seq_end=0)
        assert len(result) == 2

    def test_skips_non_dict_events(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            json.dumps({"seq": 1, "kind": "action", "refs": {"run_id": "r1"}})
            + "\n"
            + json.dumps(["not", "a", "dict"])
            + "\n",
            encoding="utf-8",
        )

        result = _collect_events(str(events_file), run_id="r1", event_seq_start=0, event_seq_end=0)
        assert len(result) == 1

    def test_negative_seq_skipped(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            json.dumps({"seq": -1, "kind": "action", "refs": {"run_id": "r1"}}) + "\n",
            encoding="utf-8",
        )

        result = _collect_events(str(events_file), run_id="r1", event_seq_start=0, event_seq_end=0)
        assert result == []


class TestExtractToolPaths:
    """Tool path extraction from events."""

    def test_extracts_from_output(self) -> None:
        event = {
            "output": {"tool_stdout_path": "/tmp/out", "tool_stderr_path": "/tmp/err"},
            "meta": {},
        }
        result = _extract_tool_paths(event)
        assert result["tool_stdout_path"] == "/tmp/out"
        assert result["tool_stderr_path"] == "/tmp/err"

    def test_extracts_from_meta(self) -> None:
        event = {
            "output": {},
            "meta": {"stdout_path": "/tmp/out", "error_path": "/tmp/err"},
        }
        result = _extract_tool_paths(event)
        assert result["stdout_path"] == "/tmp/out"
        assert result["error_path"] == "/tmp/err"

    def test_raw_output_paths_nested(self) -> None:
        event = {
            "output": {},
            "meta": {
                "raw_output_paths": {
                    "tool_stdout_path": "/tmp/raw_out",
                    "tool_stderr_path": "/tmp/raw_err",
                }
            },
        }
        result = _extract_tool_paths(event)
        assert result["tool_stdout_path"] == "/tmp/raw_out"
        assert result["tool_stderr_path"] == "/tmp/raw_err"

    def test_empty_event_returns_empty(self) -> None:
        assert _extract_tool_paths({}) == {}


class TestDeriveFailureCode:
    """Failure code derivation."""

    def test_fallback_used_when_provided(self) -> None:
        assert _derive_failure_code({}, "CUSTOM") == "CUSTOM"

    def test_output_error_code(self) -> None:
        event = {"output": {"error_code": "E123"}}
        assert _derive_failure_code(event, "") == "E123"

    def test_output_failure_code(self) -> None:
        event = {"output": {"failure_code": "F456"}}
        assert _derive_failure_code(event, "") == "F456"

    def test_event_error(self) -> None:
        event = {"error": "disk full"}
        assert _derive_failure_code(event, "") == "disk full"

    def test_output_error(self) -> None:
        event = {"output": {"error": "timeout"}}
        assert _derive_failure_code(event, "") == "timeout"

    def test_unknown_fallback(self) -> None:
        assert _derive_failure_code({}, "") == "UNKNOWN_FAILURE"


class TestBuildFailureHops:
    """Failure hops building."""

    def test_no_failures_returns_empty_payload(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            json.dumps({"seq": 1, "kind": "action", "name": "tool", "refs": {"run_id": "r1"}}) + "\n",
            encoding="utf-8",
        )

        result = build_failure_hops(str(events_file), run_id="r1")
        assert result["has_failure"] is False
        assert result["failure_code"] == ""

    def test_finds_last_failure(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        events = [
            {"seq": 1, "kind": "action", "name": "tool1", "refs": {"run_id": "r1", "phase": "plan"}},
            {
                "seq": 2,
                "kind": "observation",
                "name": "tool1",
                "ok": False,
                "error": "fail1",
                "refs": {"run_id": "r1", "phase": "plan"},
            },
            {"seq": 3, "kind": "action", "name": "tool2", "refs": {"run_id": "r1", "phase": "exec"}},
            {
                "seq": 4,
                "kind": "observation",
                "name": "tool2",
                "ok": False,
                "error": "fail2",
                "refs": {"run_id": "r1", "phase": "exec"},
            },
        ]
        events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

        result = build_failure_hops(str(events_file), run_id="r1")
        assert result["has_failure"] is True
        assert result["failure_code"] == "fail2"
        assert result["hop1_phase"]["phase"] == "exec"
        assert result["hop1_phase"]["seq"] == 4

    def test_hop3_with_tool_paths(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        events = [
            {"seq": 1, "kind": "action", "name": "tool1", "refs": {"run_id": "r1"}},
            {
                "seq": 2,
                "kind": "observation",
                "name": "tool1",
                "ok": False,
                "output": {"tool_stdout_path": "/tmp/out", "error": "boom"},
                "refs": {"run_id": "r1"},
            },
        ]
        events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

        result = build_failure_hops(str(events_file), run_id="r1")
        assert result["hop3_tool_output"]["source"] == "artifact_paths"
        assert "/tmp/out" in result["hop3_tool_output"]["paths"].values()

    def test_hop3_missing_artifacts(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        events = [
            {"seq": 1, "kind": "action", "name": "tool1", "refs": {"run_id": "r1"}},
            {"seq": 2, "kind": "observation", "name": "tool1", "ok": False, "refs": {"run_id": "r1"}},
        ]
        events_file.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

        result = build_failure_hops(str(events_file), run_id="r1")
        assert result["hop3_tool_output"]["missing_artifacts"] is True
        assert result["ready"] is False


class TestLoadFailureHops:
    """Failure hops loading from file."""

    def test_loads_existing_file(self, tmp_path: Path) -> None:
        hops_path = tmp_path / "artifacts" / "runs" / "r1"
        hops_path.mkdir(parents=True)
        data = {"schema_version": 1, "run_id": "r1", "has_failure": True}
        (hops_path / "failure_hops.json").write_text(json.dumps(data), encoding="utf-8")

        result = load_failure_hops(str(tmp_path), "r1")
        assert result is not None
        assert result["run_id"] == "r1"

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_failure_hops(str(tmp_path), "r1") is None

    def test_bad_json_returns_none(self, tmp_path: Path) -> None:
        hops_path = tmp_path / "artifacts" / "runs" / "r1"
        hops_path.mkdir(parents=True)
        (hops_path / "failure_hops.json").write_text("not json", encoding="utf-8")

        assert load_failure_hops(str(tmp_path), "r1") is None


class TestSaveFailureHops:
    """Failure hops saving to file."""

    def test_saves_successfully(self, tmp_path: Path) -> None:
        data: dict[str, Any] = {"schema_version": 1, "run_id": "r1"}
        assert save_failure_hops(str(tmp_path), "r1", data) is True

        hops_file = tmp_path / "artifacts" / "runs" / "r1" / "failure_hops.json"
        assert hops_file.exists()
        loaded = json.loads(hops_file.read_text(encoding="utf-8"))
        assert loaded["run_id"] == "r1"

    def test_save_failure_returns_false(self, tmp_path: Path) -> None:
        # Patch write_text_atomic to raise a caught exception so save_failure_hops returns False
        from unittest.mock import patch

        data: dict[str, Any] = {"run_id": "r1"}
        with patch(
            "polaris.cells.audit.diagnosis.internal.toolkit.hops.write_text_atomic",
            side_effect=RuntimeError("disk full"),
        ):
            assert save_failure_hops(str(tmp_path), "r1", data) is False


class TestAnalyzeFailureChain:
    """Failure chain analysis from events."""

    def test_empty_events(self) -> None:
        result = analyze_failure_chain([])
        assert result["total_failures"] == 0
        assert result["tool_errors"] == 0
        assert result["verification_failures"] == 0

    def test_detects_task_failed(self) -> None:
        events = [
            {"event_type": "task_failed", "action": {"result": "failure", "error": "timeout"}},
        ]
        result = analyze_failure_chain(events)
        assert result["total_failures"] == 1
        assert result["failures"][0]["error"] == "timeout"

    def test_detects_tool_execution_failure(self) -> None:
        events = [
            {
                "event_type": "tool_execution",
                "action": {"result": "failure", "error": "cmd failed"},
                "resource": {"path": "git"},
            },
        ]
        result = analyze_failure_chain(events)
        assert result["tool_errors"] == 1
        assert result["tool_errors_detail"][0]["tool"] == "git"

    def test_detects_verification_failure(self) -> None:
        events = [
            {
                "event_type": "verification",
                "action": {"result": "failure", "error": "assert failed"},
                "data": {"check": "test_x"},
            },
        ]
        result = analyze_failure_chain(events)
        assert result["verification_failures"] == 1
        assert result["verification_failures_detail"][0]["check"] == "test_x"

    def test_mixed_events(self) -> None:
        events = [
            {"event_type": "task_failed", "action": {"result": "failure", "error": "e1"}},
            {"event_type": "tool_execution", "action": {"result": "failure", "error": "e2"}, "resource": {"path": "x"}},
            {"event_type": "verification", "action": {"result": "failure", "error": "e3"}, "data": {"check": "c"}},
        ]
        result = analyze_failure_chain(events)
        assert result["total_failures"] == 3
        assert result["tool_errors"] == 1
        assert result["verification_failures"] == 1
