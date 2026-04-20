"""Regression and unit tests for error_chain.py.

Covers all 6 identified bugs, edge cases, and serialization correctness.
Total: 27 test cases.

CRITICAL: All text file I/O must use UTF-8 encoding.
"""
from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.audit.diagnosis.public.service import (
    ChainBuilder,
    ErrorChain,
    ErrorChainLink,
    ErrorMatcher,
    EventLoader,
    _parse_event_datetime,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _runtime_obs(
    event_id: str = "ev-001",
    seq: int = 1,
    kind: str = "observation",
    actor: str = "director",
    name: str = "run_tests",
    ok: bool = False,
    error: str = "FileNotFoundError",
    run_id: str = "run-1",
    task_id: str = "task-1",
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "seq": seq,
        "ts": "2026-03-23T10:00:00+00:00",
        "ts_epoch": 1742724000.0,
        "kind": kind,
        "actor": actor,
        "name": name,
        "ok": ok,
        "error": error,
        "refs": {"run_id": run_id, "task_id": task_id},
    }


def _action_ev(
    event_id: str = "ev-000",
    seq: int = 0,
    name: str = "run_tests",
    run_id: str = "run-1",
    task_id: str = "task-1",
    input_args: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "seq": seq,
        "ts": "2026-03-23T09:59:59+00:00",
        "ts_epoch": 1742723999.0,
        "kind": "action",
        "actor": "director",
        "name": name,
        "refs": {"run_id": run_id, "task_id": task_id},
        "input": {"args": input_args or ["--verbose"]},
    }


def _factory_ev(event_type: str = "error", message: str = "boom") -> dict[str, Any]:
    return {
        "type": event_type,
        "message": message,
        "timestamp": "2026-03-23T10:01:00Z",
        "_run_id": "factory_run_001",
    }


def _make_builder(tmp_path: Path) -> ChainBuilder:
    return ChainBuilder(EventLoader(tmp_path))


# ---------------------------------------------------------------------------
# _parse_event_datetime
# ---------------------------------------------------------------------------

class TestParseEventDatetime:
    def test_iso_with_z_suffix(self):
        dt = _parse_event_datetime("2026-03-23T10:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_iso_with_offset(self):
        dt = _parse_event_datetime("2026-03-23T10:00:00+00:00")
        assert dt is not None

    def test_nanoseconds_truncated_to_microseconds(self):
        dt = _parse_event_datetime("2026-03-23T10:00:00.123456789+00:00")
        assert dt is not None

    def test_none_returns_none(self):
        assert _parse_event_datetime(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_event_datetime("") is None

    def test_invalid_string_returns_none(self):
        assert _parse_event_datetime("not-a-date") is None

    def test_naive_datetime_gets_utc(self):
        dt = _parse_event_datetime("2026-03-23T10:00:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_numeric_input_not_a_date(self):
        assert _parse_event_datetime(12345) is None


# ---------------------------------------------------------------------------
# ErrorChainLink.from_event
# ---------------------------------------------------------------------------

class TestErrorChainLinkFromEvent:
    def test_none_returns_none(self):
        assert ErrorChainLink.from_event(None) is None

    def test_empty_dict_returns_none(self):
        # {} is falsy → from_event returns None
        assert ErrorChainLink.from_event({}) is None

    def test_runtime_event_parsed_correctly(self):
        ev = _runtime_obs()
        link = ErrorChainLink.from_event(ev)
        assert link is not None
        assert link.event_id == "ev-001"
        assert link.kind == "observation"
        assert link.error == "FileNotFoundError"
        assert link.ok is False

    def test_factory_error_event_parsed(self):
        ev = _factory_ev("error", "factory boom")
        link = ErrorChainLink.from_event(ev)
        assert link is not None
        assert link.kind == "observation"
        assert link.ok is False
        assert link.error == "factory boom"

    def test_factory_completed_success(self):
        ev = {"type": "completed", "result": {"status": "success"}}
        link = ErrorChainLink.from_event(ev)
        assert link is not None
        assert link.ok is True

    def test_factory_stage_completed_failure(self):
        ev = {"type": "stage_completed", "result": {"status": "failure", "output": "build fail"}}
        link = ErrorChainLink.from_event(ev)
        assert link is not None
        assert link.ok is False
        assert link.error == "build fail"

    def test_factory_timestamp_z_suffix_handled(self):
        """FIX 4 regression: no local import needed; module-level datetime used."""
        ev = {"type": "error", "message": "oops", "timestamp": "2026-03-23T10:00:00Z"}
        link = ErrorChainLink.from_event(ev)
        assert link is not None
        assert link.ts_epoch > 0


# ---------------------------------------------------------------------------
# ErrorChain serialization
# ---------------------------------------------------------------------------

class TestErrorChainSerialization:
    def _make_chain(self) -> ErrorChain:
        failure = ErrorChainLink(
            event_id="ev-fail", seq=5,
            ts="2026-03-23T10:00:05+00:00", ts_epoch=1742724005.0,
            kind="observation", actor="director", name="write_file",
            ok=False, error="PermissionError",
        )
        action = ErrorChainLink(
            event_id="ev-act", seq=4,
            ts="2026-03-23T10:00:04+00:00", ts_epoch=1742724004.0,
            kind="action", actor="director", name="write_file",
            input={"args": ["output.txt"]},
        )
        return ErrorChain(
            chain_id="ev-fail",
            failure_event=failure,
            related_action=action,
            context_events=[],
            timeline=[action, failure],
            tool_name="write_file",
            tool_args=["output.txt"],
            failure_reason="PermissionError",
        )

    def test_to_dict_required_keys_present(self):
        d = self._make_chain().to_dict()
        for key in ("chain_id", "tool_name", "failure_reason", "failure_event", "timeline"):
            assert key in d

    def test_no_related_action_is_null_in_dict(self):
        chain = self._make_chain()
        chain.related_action = None
        assert chain.to_dict()["related_action"] is None

    def test_link_to_dict_omits_none_optional_fields(self):
        link = ErrorChainLink(
            event_id="x", seq=0, ts="", ts_epoch=0.0,
            kind="observation", actor="a", name="n",
        )
        d = ErrorChain._link_to_dict(link)
        for absent in ("input", "ok", "output", "error", "duration_ms"):
            assert absent not in d

    def test_to_dict_is_json_serializable(self):
        json.dumps(self._make_chain().to_dict())  # must not raise

    def test_timeline_order_preserved_in_serialized_form(self):
        chain = self._make_chain()
        seqs = [e["seq"] for e in chain.to_dict()["timeline"]]
        assert seqs == sorted(seqs)


# ---------------------------------------------------------------------------
# ErrorMatcher
# ---------------------------------------------------------------------------

class TestErrorMatcher:
    def test_exact_strategy(self):
        m = ErrorMatcher.create_matcher("hello", "exact")
        assert m("hello") is True
        assert m("hello world") is False

    def test_substring_strategy(self):
        m = ErrorMatcher.create_matcher("err", "substring")
        assert m("some error here") is True
        assert m("success") is False

    def test_regex_strategy(self):
        m = ErrorMatcher.create_matcher(r"error\s+\d+", "regex")
        assert m("error 42") is True
        assert m("error") is False

    def test_invalid_regex_falls_back_to_substring(self):
        m = ErrorMatcher.create_matcher("[invalid", "regex")
        assert m("[invalid string") is True

    def test_fuzzy_match_sufficient_overlap(self):
        m = ErrorMatcher.create_matcher("timeout connection", "fuzzy")
        assert m("connection timeout occurred") is True

    def test_fuzzy_match_insufficient_overlap(self):
        m = ErrorMatcher.create_matcher("timeout connection refused", "fuzzy")
        assert m("completely unrelated text") is False

    def test_fuzzy_empty_pattern_returns_false(self):
        m = ErrorMatcher.create_matcher("", "fuzzy")
        assert m("anything") is False

    def test_fuzzy_non_string_inputs_no_crash(self):
        """FIX 5 regression: non-string pattern/text must not raise AttributeError.
        str(None)="none" token-matches itself → True; int 42 token-matches "hello" → False.
        """
        # Must not raise regardless of return value
        try:
            ErrorMatcher._fuzzy_match(None, None)
            ErrorMatcher._fuzzy_match(42, "hello")
        except AttributeError:
            pytest.fail("_fuzzy_match raised AttributeError on non-string inputs")
        # 42 as string "42" has no token overlap with "hello" tokens
        assert ErrorMatcher._fuzzy_match(42, "hello") is False

    def test_unknown_strategy_falls_back_to_substring(self):
        m = ErrorMatcher.create_matcher("needle", "not_a_strategy")
        assert m("find the needle here") is True

    def test_match_event_checks_message_field(self):
        event = {"message": "connection refused", "kind": "observation"}
        m = ErrorMatcher.create_matcher("connection refused", "substring")
        assert ErrorMatcher.match_event(event, m) is True

    def test_match_event_no_match_returns_false(self):
        event = {"message": "all good", "kind": "observation"}
        m = ErrorMatcher.create_matcher("fatal error", "substring")
        assert ErrorMatcher.match_event(event, m) is False


# ---------------------------------------------------------------------------
# ChainBuilder — bug regression tests
# ---------------------------------------------------------------------------

class TestChainBuilderBugFixes:

    def test_bug1_empty_failed_event_no_crash(self, tmp_path):
        """FIX 1a: from_event returns None for empty dict; fallback link created."""
        chain = _make_builder(tmp_path).build_error_chain({}, [])
        assert chain is not None
        assert isinstance(chain.failure_event, ErrorChainLink)

    def test_bug1_falsy_context_events_skipped(self, tmp_path):
        """FIX 1b: empty-dict events in all_events must not appear in context."""
        failed = _runtime_obs(seq=5, run_id="r1", task_id="t1")
        ctx = _runtime_obs(event_id="ctx", seq=4, run_id="r1", task_id="t1")
        # empty dict is falsy → from_event returns None → must be dropped
        chain = _make_builder(tmp_path).build_error_chain(failed, [failed, {}, ctx])
        assert all(x is not None for x in chain.timeline)
        assert all(x is not None for x in chain.context_events)

    def test_bug6_timeline_sorted_no_crash(self, tmp_path):
        """FIX 6: timeline.sort must not crash and must be ascending by seq."""
        action = _action_ev(seq=2, run_id="r1", task_id="t1")
        failed = _runtime_obs(seq=5, run_id="r1", task_id="t1")
        ctx = _runtime_obs(event_id="ev-c", seq=4, run_id="r1", task_id="t1")
        chain = _make_builder(tmp_path).build_error_chain(failed, [action, failed, ctx])
        seqs = [link.seq for link in chain.timeline]
        assert seqs == sorted(seqs)

    def test_bug2_tool_args_from_dict_input(self, tmp_path):
        """FIX 2: normal dict input extracts args correctly."""
        action = _action_ev(seq=0, input_args=["--flag", "value"])
        failed = _runtime_obs(seq=1)
        chain = _make_builder(tmp_path).build_error_chain(failed, [action, failed])
        assert chain.tool_args == ["--flag", "value"]

    def test_bug2_string_input_no_crash(self, tmp_path):
        """FIX 2: string input (not dict) must not raise AttributeError."""
        action = _action_ev(seq=0)
        action["input"] = '{"args": ["x"]}'  # serialized string, not dict
        failed = _runtime_obs(seq=1)
        chain = _make_builder(tmp_path).build_error_chain(failed, [action, failed])
        assert chain.tool_args == []

    def test_bug3_none_refs_no_context_pollution(self, tmp_path):
        """FIX 3: events without refs must not match None==None and pollute context."""
        # failed_event has no refs → run_id/task_id are both None
        failed = {
            "event_id": "f1", "seq": 5, "ts": "", "ts_epoch": 0.0,
            "kind": "observation", "actor": "a", "name": "n", "ok": False,
        }
        unrelated = {
            "event_id": "u1", "seq": 4, "ts": "", "ts_epoch": 0.0,
            "kind": "observation", "actor": "b", "name": "m",
        }
        chain = _make_builder(tmp_path).build_error_chain(failed, [failed, unrelated])
        # unrelated must NOT appear: its refs also lack run_id/task_id
        ctx_ids = {e.event_id for e in chain.context_events}
        assert "u1" not in ctx_ids

    def test_single_node_chain_no_related_action(self, tmp_path):
        """Edge: single event, no matching action."""
        failed = _runtime_obs()
        chain = _make_builder(tmp_path).build_error_chain(failed, [failed])
        assert chain.failure_event is not None
        assert chain.related_action is None
        assert len(chain.timeline) >= 1

    def test_deep_context_window(self, tmp_path):
        """Edge: large context window captures all nearby events."""
        events = [
            _runtime_obs(event_id=f"ev-{i}", seq=i, run_id="r1", task_id="t1")
            for i in range(20)
        ]
        failed = events[10]
        chain = _make_builder(tmp_path).build_error_chain(failed, events, context_window=9)
        for link in chain.context_events:
            assert abs(link.seq - 10) <= 9

    def test_failure_reason_from_error_field(self, tmp_path):
        failed = _runtime_obs(error="FileNotFoundError: /tmp/x")
        chain = _make_builder(tmp_path).build_error_chain(failed, [failed])
        assert chain.failure_reason == "FileNotFoundError: /tmp/x"

    def test_failure_reason_from_output_error(self, tmp_path):
        failed = {
            "event_id": "e1", "seq": 1, "ts": "", "ts_epoch": 0.0,
            "kind": "observation", "actor": "a", "name": "n",
            "output": {"error": "disk full"},
        }
        chain = _make_builder(tmp_path).build_error_chain(failed, [failed])
        assert chain.failure_reason == "disk full"

    def test_failure_reason_unknown_when_only_ok_false(self, tmp_path):
        failed = {
            "event_id": "e1", "seq": 1, "ts": "", "ts_epoch": 0.0,
            "kind": "observation", "actor": "a", "name": "n", "ok": False,
        }
        chain = _make_builder(tmp_path).build_error_chain(failed, [failed])
        assert "Unknown error" in chain.failure_reason


# ---------------------------------------------------------------------------
# EventLoader
# ---------------------------------------------------------------------------

class TestEventLoader:
    def test_lru_evicts_oldest_entry(self, tmp_path):
        loader = EventLoader(tmp_path)
        loader._max_cache_size = 3
        for i in range(4):
            loader._add_to_cache(f"key{i}", [{"seq": i}])
        assert loader._get_from_cache("key0") is None
        assert loader._get_from_cache("key3") is not None

    def test_cache_hit_promotes_entry(self, tmp_path):
        loader = EventLoader(tmp_path)
        loader._max_cache_size = 2
        loader._add_to_cache("a", [])
        loader._add_to_cache("b", [])
        loader._get_from_cache("a")  # promote "a"
        loader._add_to_cache("c", [])  # evicts "b"
        assert loader._get_from_cache("a") is not None
        assert loader._get_from_cache("b") is None

    def test_load_file_utf8_and_non_ascii(self, tmp_path):
        f = tmp_path / "events.jsonl"
        f.write_text(
            '{"event_id": "e1", "seq": 1}\n'
            '{"event_id": "e2", "seq": 2, "error": "日本語エラー"}\n',
            encoding="utf-8",
        )
        loader = EventLoader(tmp_path)
        events = loader.load_events_from_file(f)
        assert len(events) == 2
        assert events[1]["error"] == "日本語エラー"

    def test_load_file_skips_invalid_json(self, tmp_path):
        f = tmp_path / "events.jsonl"
        f.write_text('{"ok": true}\n{BROKEN}\n{"ok": false}\n', encoding="utf-8")
        loader = EventLoader(tmp_path)
        events = loader.load_events_from_file(f)
        assert len(events) == 2

    def test_load_file_skips_comment_lines(self, tmp_path):
        f = tmp_path / "events.jsonl"
        f.write_text("# comment\n{\"seq\": 1}\n", encoding="utf-8")
        loader = EventLoader(tmp_path)
        events = loader.load_events_from_file(f)
        assert len(events) == 1
