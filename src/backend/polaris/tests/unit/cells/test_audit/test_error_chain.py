"""Tests for polaris.cells.audit.diagnosis.internal.toolkit.error_chain.

Covers ErrorChainLink, ErrorChain, ErrorMatcher, EventLoader, ChainBuilder,
and ErrorChainSearcher with temp files and pure logic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from polaris.cells.audit.diagnosis.internal.toolkit.error_chain import (
    ChainBuilder,
    ErrorChain,
    ErrorChainLink,
    ErrorChainSearcher,
    ErrorMatcher,
    EventLoader,
    _parse_event_datetime,
    search_error_chains,
)


class TestParseEventDatetime:
    """Timestamp parsing with microsecond and timezone handling."""

    def test_iso8601_basic(self) -> None:
        result = _parse_event_datetime("2024-01-15T10:30:00")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_iso8601_with_z(self) -> None:
        result = _parse_event_datetime("2024-01-15T10:30:00Z")
        assert result is not None
        assert result.tzinfo is not None

    def test_iso8601_with_offset(self) -> None:
        result = _parse_event_datetime("2024-01-15T10:30:00+08:00")
        assert result is not None
        assert result.utcoffset() is not None

    def test_iso8601_with_microseconds(self) -> None:
        result = _parse_event_datetime("2024-01-15T10:30:00.123456Z")
        assert result is not None
        assert result.microsecond == 123456

    def test_iso8601_with_many_microseconds_truncated(self) -> None:
        result = _parse_event_datetime("2024-01-15T10:30:00.123456789Z")
        assert result is not None
        assert result.microsecond == 123456

    def test_empty_returns_none(self) -> None:
        assert _parse_event_datetime("") is None
        assert _parse_event_datetime(None) is None  # type: ignore[arg-type]

    def test_invalid_returns_none(self) -> None:
        assert _parse_event_datetime("not a date") is None

    def test_naive_datetime_gets_utc(self) -> None:
        result = _parse_event_datetime("2024-01-15T10:30:00")
        assert result is not None
        assert result.tzinfo == timezone.utc


class TestErrorChainLink:
    """ErrorChainLink dataclass tests."""

    def test_from_runtime_event(self) -> None:
        event = {
            "event_id": "e1",
            "seq": 10,
            "ts": "2024-01-01T00:00:00Z",
            "ts_epoch": 1704067200.0,
            "kind": "observation",
            "actor": "director",
            "name": "tool_call",
            "refs": {"run_id": "r1"},
            "ok": False,
            "error": "timeout",
        }
        link = ErrorChainLink.from_event(event)
        assert link is not None
        assert link.event_id == "e1"
        assert link.seq == 10
        assert link.kind == "observation"
        assert link.ok is False
        assert link.error == "timeout"

    def test_from_factory_event_error(self) -> None:
        event = {
            "type": "error",
            "stage": "build",
            "message": "compile failed",
            "timestamp": "2024-01-01T00:00:00Z",
        }
        link = ErrorChainLink.from_event(event)
        assert link is not None
        assert link.kind == "observation"
        assert link.ok is False
        assert link.error == "compile failed"
        assert link.actor == "build"

    def test_from_factory_event_completed(self) -> None:
        event = {
            "type": "completed",
            "stage": "test",
            "result": {"status": "success"},
            "timestamp": "2024-01-01T00:00:00Z",
        }
        link = ErrorChainLink.from_event(event)
        assert link is not None
        assert link.kind == "state"
        assert link.ok is True

    def test_from_none_returns_none(self) -> None:
        assert ErrorChainLink.from_event(None) is None  # type: ignore[arg-type]

    def test_from_empty_dict(self) -> None:
        link = ErrorChainLink.from_event({})
        # Empty dict is falsy, so from_event returns None
        assert link is None


class TestErrorChain:
    """ErrorChain dataclass tests."""

    def test_to_dict(self) -> None:
        failure = ErrorChainLink(
            event_id="e1",
            seq=10,
            ts="2024-01-01T00:00:00Z",
            ts_epoch=1704067200.0,
            kind="observation",
            actor="director",
            name="tool_call",
        )
        chain = ErrorChain(
            chain_id="c1",
            failure_event=failure,
            tool_name="WRITE_FILE",
            failure_reason="timeout",
        )
        result = chain.to_dict()
        assert result["chain_id"] == "c1"
        assert result["tool_name"] == "WRITE_FILE"
        assert result["failure_reason"] == "timeout"
        assert result["failure_event"]["event_id"] == "e1"


class TestErrorMatcher:
    """Error pattern matching strategies."""

    def test_exact_match(self) -> None:
        matcher = ErrorMatcher.create_matcher("timeout", "exact")
        assert matcher("timeout") is True
        assert matcher("timeoutx") is False

    def test_substring_match(self) -> None:
        matcher = ErrorMatcher.create_matcher("timeout", "substring")
        assert matcher("connection timeout occurred") is True
        assert matcher("success") is False

    def test_regex_match(self) -> None:
        matcher = ErrorMatcher.create_matcher(r"time\w+", "regex")
        assert matcher("timeout") is True
        assert matcher("success") is False

    def test_invalid_regex_fallback(self) -> None:
        matcher = ErrorMatcher.create_matcher(r"[invalid", "regex")
        assert matcher("[invalid") is True

    def test_fuzzy_match(self) -> None:
        matcher = ErrorMatcher.create_matcher("connection timeout", "fuzzy")
        assert matcher("connection timeout error") is True
        assert matcher("completely different text here") is False

    def test_unknown_strategy_defaults_to_substring(self) -> None:
        matcher = ErrorMatcher.create_matcher("test", "unknown")
        assert matcher("testing") is True

    def test_match_event_error_field(self) -> None:
        matcher = ErrorMatcher.create_matcher("timeout", "substring")
        event = {"error": "connection timeout"}
        assert ErrorMatcher.match_event(event, matcher) is True

    def test_match_event_output_error(self) -> None:
        matcher = ErrorMatcher.create_matcher("timeout", "substring")
        event = {"output": {"error": "timeout occurred"}}
        assert ErrorMatcher.match_event(event, matcher) is True

    def test_match_event_message(self) -> None:
        matcher = ErrorMatcher.create_matcher("failed", "substring")
        event = {"message": "build failed"}
        assert ErrorMatcher.match_event(event, matcher) is True

    def test_match_event_result_output(self) -> None:
        matcher = ErrorMatcher.create_matcher("failed", "substring")
        event = {"result": {"output": "test failed"}}
        assert ErrorMatcher.match_event(event, matcher) is True

    def test_match_event_traceback(self) -> None:
        matcher = ErrorMatcher.create_matcher("Traceback", "substring")
        event = {"traceback": "Traceback (most recent call last)"}
        assert ErrorMatcher.match_event(event, matcher) is True

    def test_match_event_data_content_preview(self) -> None:
        matcher = ErrorMatcher.create_matcher("secret", "substring")
        event = {"data": {"content_preview": " leaked secret"}}
        assert ErrorMatcher.match_event(event, matcher) is True

    def test_no_match(self) -> None:
        matcher = ErrorMatcher.create_matcher("xyz123", "substring")
        event = {"error": "abc"}
        assert ErrorMatcher.match_event(event, matcher) is False


class TestEventLoader:
    """Event loading and caching."""

    def test_get_event_files(self, tmp_path: Path) -> None:
        # Create runtime events file
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        (events_dir / "runtime.events.jsonl").write_text("", encoding="utf-8")

        loader = EventLoader(tmp_path)
        files = loader.get_event_files()
        assert any("runtime.events.jsonl" in str(f) for f in files)

    def test_load_events_from_file(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            json.dumps({"seq": 1, "kind": "action"}) + "\n" + json.dumps({"seq": 2, "kind": "observation"}) + "\n",
            encoding="utf-8",
        )

        loader = EventLoader(tmp_path)
        events = loader.load_events_from_file(events_file)
        assert len(events) == 2
        assert events[0]["seq"] == 1

    def test_load_events_caching(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(json.dumps({"seq": 1}) + "\n", encoding="utf-8")

        loader = EventLoader(tmp_path)
        events1 = loader.load_events_from_file(events_file)
        events2 = loader.load_events_from_file(events_file)
        assert events1 is events2  # Same cached object

    def test_load_events_skips_bad_json(self, tmp_path: Path) -> None:
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            json.dumps({"seq": 1}) + "\nnot json\n" + json.dumps({"seq": 2}) + "\n",
            encoding="utf-8",
        )

        loader = EventLoader(tmp_path)
        events = loader.load_events_from_file(events_file)
        assert len(events) == 2

    def test_load_all_events(self, tmp_path: Path) -> None:
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        (events_dir / "runtime.events.jsonl").write_text(
            json.dumps({"seq": 1}) + "\n",
            encoding="utf-8",
        )

        loader = EventLoader(tmp_path)
        events = loader.load_all_events()
        assert len(events) == 1

    def test_cache_eviction(self, tmp_path: Path) -> None:
        loader = EventLoader(tmp_path)
        loader._max_cache_size = 2

        for i in range(3):
            f = tmp_path / f"events{i}.jsonl"
            f.write_text(json.dumps({"seq": i}) + "\n", encoding="utf-8")
            loader.load_events_from_file(f)

        assert len(loader._cache) == 2


class TestChainBuilder:
    """Error chain building from events."""

    def test_find_related_action(self, tmp_path: Path) -> None:
        loader = EventLoader(tmp_path)
        builder = ChainBuilder(loader)

        failed = {"seq": 10, "kind": "observation", "name": "tool1", "refs": {"task_id": "t1", "run_id": "r1"}}
        all_events = [
            {"seq": 5, "kind": "action", "name": "other", "refs": {"task_id": "t1", "run_id": "r1"}},
            {"seq": 8, "kind": "action", "name": "tool1", "refs": {"task_id": "t1", "run_id": "r1"}},
            {"seq": 12, "kind": "action", "name": "tool1", "refs": {"task_id": "t1", "run_id": "r1"}},
        ]

        result = builder.find_related_action(failed, all_events)
        assert result is not None
        assert result["seq"] == 8  # Most recent action before failure with same name

    def test_find_related_action_no_match(self) -> None:
        loader = EventLoader(Path("."))
        builder = ChainBuilder(loader)

        failed = {"seq": 10, "kind": "observation", "name": "tool1", "refs": {"task_id": "t1", "run_id": "r1"}}
        all_events = [
            {"seq": 12, "kind": "action", "name": "tool1", "refs": {"task_id": "t1", "run_id": "r1"}},
        ]

        assert builder.find_related_action(failed, all_events) is None

    def test_build_error_chain(self, tmp_path: Path) -> None:
        loader = EventLoader(tmp_path)
        builder = ChainBuilder(loader)

        failed = {
            "event_id": "e-fail",
            "seq": 10,
            "kind": "observation",
            "name": "tool1",
            "actor": "director",
            "refs": {"task_id": "t1", "run_id": "r1"},
            "error": "timeout",
        }
        all_events = [
            {
                "event_id": "e-action",
                "seq": 8,
                "kind": "action",
                "name": "tool1",
                "actor": "director",
                "refs": {"task_id": "t1", "run_id": "r1"},
                "input": {"args": ["a", "b"]},
            },
            {
                "event_id": "e-context",
                "seq": 9,
                "kind": "observation",
                "name": "tool0",
                "actor": "director",
                "refs": {"task_id": "t1", "run_id": "r1"},
            },
            failed,
        ]

        chain = builder.build_error_chain(failed, all_events, context_window=5)
        assert chain.chain_id == "e-fail"
        assert chain.failure_event.event_id == "e-fail"
        assert chain.related_action is not None
        assert chain.related_action.event_id == "e-action"
        assert chain.tool_args == ["a", "b"]
        assert chain.failure_reason == "timeout"
        assert len(chain.timeline) >= 2

    def test_build_error_chain_no_related_action(self, tmp_path: Path) -> None:
        loader = EventLoader(tmp_path)
        builder = ChainBuilder(loader)

        failed = {
            "event_id": "e-fail",
            "seq": 10,
            "kind": "observation",
            "name": "tool1",
            "actor": "director",
            "refs": {"task_id": "t1", "run_id": "r1"},
            "error": "timeout",
        }

        chain = builder.build_error_chain(failed, [], context_window=5)
        assert chain.related_action is None
        assert chain.tool_args == []

    def test_build_error_chain_with_factory_event(self, tmp_path: Path) -> None:
        loader = EventLoader(tmp_path)
        builder = ChainBuilder(loader)

        failed = {
            "event_id": "e-fail",
            "type": "error",
            "stage": "build",
            "message": "compile failed",
            "timestamp": "2024-01-01T00:00:00Z",
        }

        chain = builder.build_error_chain(failed, [], context_window=5)
        assert chain.failure_reason == "compile failed"


class TestErrorChainSearcher:
    """End-to-end error chain search."""

    def test_search_no_events(self, tmp_path: Path) -> None:
        searcher = ErrorChainSearcher(tmp_path)
        result = searcher.search("timeout")
        assert result == []
        assert searcher.last_search_stats["total_events"] == 0

    def test_search_finds_match(self, tmp_path: Path) -> None:
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        (events_dir / "runtime.events.jsonl").write_text(
            json.dumps({"seq": 1, "kind": "action", "name": "tool1"})
            + "\n"
            + json.dumps({"seq": 2, "kind": "observation", "name": "tool1", "error": "timeout"})
            + "\n",
            encoding="utf-8",
        )

        searcher = ErrorChainSearcher(tmp_path)
        result = searcher.search("timeout")
        assert len(result) == 1
        assert result[0].failure_reason == "timeout"

    def test_search_with_time_filter(self, tmp_path: Path) -> None:
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        (events_dir / "runtime.events.jsonl").write_text(
            json.dumps({"seq": 1, "kind": "observation", "error": "old", "ts": "2024-01-01T00:00:00Z"})
            + "\n"
            + json.dumps({"seq": 2, "kind": "observation", "error": "new", "ts": "2024-06-01T00:00:00Z"})
            + "\n",
            encoding="utf-8",
        )

        searcher = ErrorChainSearcher(tmp_path)
        since = datetime(2024, 5, 1, tzinfo=timezone.utc)
        result = searcher.search("new", since=since)
        assert len(result) == 1
        assert result[0].failure_reason == "new"

    def test_search_limit(self, tmp_path: Path) -> None:
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        lines = "\n".join(json.dumps({"seq": i, "kind": "observation", "error": f"err{i}"}) for i in range(10))
        (events_dir / "runtime.events.jsonl").write_text(lines + "\n", encoding="utf-8")

        searcher = ErrorChainSearcher(tmp_path)
        result = searcher.search("err", limit=3)
        assert len(result) == 3

    def test_search_excludes_actions(self, tmp_path: Path) -> None:
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        (events_dir / "runtime.events.jsonl").write_text(
            json.dumps({"seq": 1, "kind": "action", "error": "action error"})
            + "\n"
            + json.dumps({"seq": 2, "kind": "observation", "error": "obs error"})
            + "\n",
            encoding="utf-8",
        )

        searcher = ErrorChainSearcher(tmp_path)
        result = searcher.search("error")
        assert len(result) == 1
        assert result[0].failure_event.kind == "observation"

    def test_classify_event_source(self) -> None:
        assert ErrorChainSearcher._classify_event_source({"_run_id": "x"}) == "factory"
        assert ErrorChainSearcher._classify_event_source({"kind": "action"}) == "runtime"
        assert ErrorChainSearcher._classify_event_source({"role": "pm", "type": "msg"}) == "role"


class TestSearchErrorChainsConvenience:
    """Convenience function tests."""

    def test_empty_runtime(self, tmp_path: Path) -> None:
        result = search_error_chains(tmp_path, "timeout")
        assert result == []
