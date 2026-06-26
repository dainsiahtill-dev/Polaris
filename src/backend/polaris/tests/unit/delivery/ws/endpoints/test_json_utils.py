"""Tests for polaris.delivery.ws.endpoints.json_utils."""

from __future__ import annotations

import json

from polaris.delivery.ws.endpoints.json_utils import (
    elide_oversized_frame,
    parse_json_line,
    resolve_journal_event_channel,
    sanitize_snapshot_lines,
)


def _byte_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


class TestParseJsonLine:
    def test_valid_json(self) -> None:
        result = parse_json_line('{"key": "value"}')
        assert result == {"key": "value"}

    def test_invalid_json(self) -> None:
        assert parse_json_line("not json") is None

    def test_empty_string(self) -> None:
        assert parse_json_line("") is None

    def test_whitespace_only(self) -> None:
        assert parse_json_line("   ") is None

    def test_json_list(self) -> None:
        assert parse_json_line("[1, 2, 3]") is None

    def test_none_input(self) -> None:
        assert parse_json_line(None) is None


class TestSanitizeSnapshotLines:
    def test_non_llm_channel(self) -> None:
        lines = ["line1", "line2"]
        result = sanitize_snapshot_lines("system", lines)
        assert result == lines

    def test_llm_channel_no_brace(self) -> None:
        lines = ["header", '{"key": "value"}']
        result = sanitize_snapshot_lines("llm", lines)
        assert result == ['{"key": "value"}']

    def test_llm_channel_starts_with_brace(self) -> None:
        lines = ['{"key": "value"}', "line2"]
        result = sanitize_snapshot_lines("llm", lines)
        assert result == lines

    def test_empty_lines(self) -> None:
        result = sanitize_snapshot_lines("llm", [])
        assert result == []

    def test_llm_channel_with_whitespace(self) -> None:
        lines = ["  header", '{"key": "value"}']
        result = sanitize_snapshot_lines("llm", lines)
        assert result == ['{"key": "value"}']


class TestResolveJournalEventChannel:
    def test_valid_channel_field(self) -> None:
        result = resolve_journal_event_channel('{"channel": "llm"}')
        assert result == "llm"

    def test_valid_domain_llm(self) -> None:
        result = resolve_journal_event_channel('{"domain": "llm"}')
        assert result == "llm"

    def test_valid_domain_process(self) -> None:
        result = resolve_journal_event_channel('{"domain": "process"}')
        assert result == "process"

    def test_valid_domain_system(self) -> None:
        result = resolve_journal_event_channel('{"domain": "system"}')
        assert result == "system"

    def test_invalid_json(self) -> None:
        result = resolve_journal_event_channel("not json")
        assert result == "system"

    def test_empty_json(self) -> None:
        result = resolve_journal_event_channel("{}")
        assert result == "system"

    def test_unknown_domain(self) -> None:
        result = resolve_journal_event_channel('{"domain": "unknown"}')
        assert result == "system"

    def test_channel_takes_precedence(self) -> None:
        result = resolve_journal_event_channel('{"channel": "process", "domain": "llm"}')
        assert result == "process"


class TestElideOversizedFrame:
    def test_small_payload_is_returned_unchanged(self) -> None:
        payload = {"type": "EVENT", "channel": "event.factory", "payload": {"status": "running"}}
        assert elide_oversized_frame(payload, 900_000) == payload

    def test_oversized_payload_is_bounded_under_budget(self) -> None:
        payload = {"type": "EVENT", "blob": "x" * 2_000_000}
        budget = 100_000
        elided = elide_oversized_frame(payload, budget)
        assert _byte_size(elided) <= budget

    def test_factory_stage_completed_keeps_control_fields_elides_output(self) -> None:
        # Real shape that broke r05: stage_completed embeds the full StageResult
        # output twice (message + result.output) → frame > 1 MiB.
        huge_output = "GENERATED FILE CONTENT\n" * 80_000
        frame = {
            "type": "EVENT",
            "channel": "event.factory:run-1",
            "cursor": 7,
            "event": {
                "schema_version": "runtime.v2",
                "run_id": "run-1",
                "channel": "event.factory:run-1",
                "kind": "stage_completed",
                "payload": {
                    "type": "stage_completed",
                    "stage": "director",
                    "message": huge_output,
                    "result": {
                        "stage": "director",
                        "status": "success",
                        "output": huge_output,
                        "artifacts": ["src/main.py"],
                    },
                    "timestamp": "2026-06-27T00:00:00Z",
                },
            },
        }
        assert _byte_size(frame) > 1_048_576  # precondition: would trip the WS limit
        budget = 900_000
        elided = elide_oversized_frame(frame, budget)

        assert _byte_size(elided) <= budget
        # Control-plane fields the bench status extractor reads must survive verbatim.
        assert elided["type"] == "EVENT"
        assert elided["channel"] == "event.factory:run-1"
        assert elided["cursor"] == 7
        payload = elided["event"]["payload"]
        assert payload["type"] == "stage_completed"
        assert payload["stage"] == "director"
        assert payload["result"]["status"] == "success"
        assert payload["result"]["artifacts"] == ["src/main.py"]
        # The huge duplicated output must be truncated, not carried whole.
        assert "ws-elided" in payload["result"]["output"]
        assert len(payload["result"]["output"]) < len(huge_output)

    def test_llm_line_and_event_duplication_is_bounded(self) -> None:
        big_line = json.dumps({"prompt": "z" * 1_500_000})
        frame = {
            "type": "llm_stream",
            "channel": "llm",
            "line": big_line,
            "event": {"prompt": "z" * 1_500_000},
            "snapshot": True,
        }
        budget = 900_000
        elided = elide_oversized_frame(frame, budget)
        assert _byte_size(elided) <= budget
        assert elided["type"] == "llm_stream"
        assert elided["channel"] == "llm"
        assert elided["snapshot"] is True

    def test_oversized_list_items_are_truncated_and_bounded(self) -> None:
        frame = {"type": "EVENT", "items": ["payload-" + "q" * 6000 for _ in range(40)]}
        budget = 120_000
        elided = elide_oversized_frame(frame, budget)
        assert _byte_size(elided) <= budget
        assert elided["type"] == "EVENT"
        assert isinstance(elided["items"], list)
        assert elided["items"]  # list preserved (not hard-floored)
        assert any("ws-elided" in str(item) for item in elided["items"])

    def test_pathological_many_fields_falls_back_to_hard_floor(self) -> None:
        # Thousands of mid-size sibling fields cannot fit even after per-field
        # elision → the hard-floor net keeps only top-level control scalars.
        frame: dict[str, object] = {"type": "EVENT", "channel": "event.factory", "cursor": 5}
        for i in range(4000):
            frame[f"field_{i}"] = "v" * 300
        budget = 80_000
        elided = elide_oversized_frame(frame, budget)
        assert _byte_size(elided) <= budget
        assert elided["__ws_frame_elided__"] is True
        assert elided["type"] == "EVENT"
        assert elided["channel"] == "event.factory"
        assert elided["cursor"] == 5

    def test_idempotent_on_already_elided_payload(self) -> None:
        payload = {"type": "EVENT", "blob": "x" * 2_000_000}
        once = elide_oversized_frame(payload, 100_000)
        twice = elide_oversized_frame(once, 100_000)
        assert once == twice
