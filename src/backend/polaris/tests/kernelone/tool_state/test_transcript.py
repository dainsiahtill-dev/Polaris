"""Tests for polaris.kernelone.tool_state.transcript module.

Covers state transitions, immutability, and all 11+ public functions.
"""

from __future__ import annotations

from dataclasses import is_dataclass
from typing import Any

import pytest
from polaris.kernelone.tool_state.transcript import (
    ToolCallEntry,
    ToolResultEntry,
    ToolTranscriptEntry,
    TranscriptLog,
)

# -----------------------------------------------------------------------------
# ToolTranscriptEntry
# -----------------------------------------------------------------------------


def test_tool_transcript_entry_defaults() -> None:
    entry = ToolTranscriptEntry(tool="read", args={"path": "/tmp/test"})
    assert entry.tool == "read"
    assert entry.args == {"path": "/tmp/test"}
    assert entry.result is None
    assert entry.success is True
    assert entry.error is None
    assert isinstance(entry.timestamp, str)
    assert len(entry.timestamp) > 0


def test_tool_transcript_entry_frozen() -> None:
    entry = ToolTranscriptEntry(tool="read", args={})
    with pytest.raises(AttributeError):
        entry.tool = "write"


def test_tool_transcript_entry_is_dataclass() -> None:
    assert is_dataclass(ToolTranscriptEntry)


# -----------------------------------------------------------------------------
# ToolCallEntry
# -----------------------------------------------------------------------------


def test_tool_call_entry_defaults() -> None:
    entry = ToolCallEntry(tool="read", args={"path": "/tmp/test"})
    assert entry.tool == "read"
    assert entry.call_id is None
    assert isinstance(entry.timestamp, str)


def test_tool_call_entry_with_call_id() -> None:
    entry = ToolCallEntry(tool="read", args={}, call_id="call-1")
    assert entry.call_id == "call-1"


def test_tool_call_entry_frozen() -> None:
    entry = ToolCallEntry(tool="read", args={})
    with pytest.raises(AttributeError):
        entry.tool = "write"


# -----------------------------------------------------------------------------
# ToolResultEntry
# -----------------------------------------------------------------------------


def test_tool_result_entry_defaults() -> None:
    entry = ToolResultEntry(tool="read", result="hello")
    assert entry.tool == "read"
    assert entry.result == "hello"
    assert entry.success is True
    assert entry.error is None


def test_tool_result_entry_failure() -> None:
    entry = ToolResultEntry(tool="read", result=None, success=False, error="not found")
    assert entry.success is False
    assert entry.error == "not found"


def test_tool_result_entry_frozen() -> None:
    entry = ToolResultEntry(tool="read", result="hello")
    with pytest.raises(AttributeError):
        entry.result = "goodbye"


# -----------------------------------------------------------------------------
# TranscriptLog — Initialization
# -----------------------------------------------------------------------------


def test_transcript_log_init_empty() -> None:
    log = TranscriptLog()
    assert len(log) == 0
    assert list(log) == []


# -----------------------------------------------------------------------------
# TranscriptLog — add_call
# -----------------------------------------------------------------------------


def test_add_call_increments_length() -> None:
    log = TranscriptLog()
    log.add_call("read", {"path": "/tmp/a"})
    assert len(log) == 1


def test_add_call_returns_tool_call_entry() -> None:
    log = TranscriptLog()
    entry = log.add_call("read", {"path": "/tmp/a"})
    assert isinstance(entry, ToolCallEntry)
    assert entry.tool == "read"


def test_add_call_stores_entry_with_none_result() -> None:
    log = TranscriptLog()
    log.add_call("read", {"path": "/tmp/a"})
    stored = log.entries[0]
    assert stored.result is None
    assert stored.success is True
    assert stored.error is None


def test_add_call_preserves_args() -> None:
    log = TranscriptLog()
    args: dict[str, Any] = {"path": "/tmp/a", "lines": 10}
    log.add_call("read", args)
    assert log.entries[0].args == args


# -----------------------------------------------------------------------------
# TranscriptLog — add_result
# -----------------------------------------------------------------------------


def test_add_result_increments_length() -> None:
    log = TranscriptLog()
    log.add_result("read", "content")
    assert len(log) == 1


def test_add_result_returns_tool_result_entry() -> None:
    log = TranscriptLog()
    entry = log.add_result("read", "content", success=False, error="err")
    assert isinstance(entry, ToolResultEntry)
    assert entry.error == "err"


def test_add_result_stores_success_and_error() -> None:
    log = TranscriptLog()
    log.add_result("read", None, success=False, error="failed")
    stored = log.entries[0]
    assert stored.success is False
    assert stored.error == "failed"
    assert stored.result is None


# -----------------------------------------------------------------------------
# TranscriptLog — add (complete entry)
# -----------------------------------------------------------------------------


def test_add_complete_entry() -> None:
    log = TranscriptLog()
    entry = log.add("read", {"path": "/tmp/a"}, "content", success=True)
    assert isinstance(entry, ToolTranscriptEntry)
    assert len(log) == 1
    assert log.entries[0].result == "content"


def test_add_with_error() -> None:
    log = TranscriptLog()
    log.add("write", {"path": "/tmp/a"}, None, success=False, error="denied")
    stored = log.entries[0]
    assert stored.success is False
    assert stored.error == "denied"


# -----------------------------------------------------------------------------
# TranscriptLog — clear
# -----------------------------------------------------------------------------


def test_clear_removes_all_entries() -> None:
    log = TranscriptLog()
    log.add_call("read", {})
    log.add_result("read", "ok")
    assert len(log) == 2
    log.clear()
    assert len(log) == 0
    assert log.entries == []


def test_clear_on_empty_log() -> None:
    log = TranscriptLog()
    log.clear()
    assert len(log) == 0


# -----------------------------------------------------------------------------
# TranscriptLog — get_calls
# -----------------------------------------------------------------------------


def test_get_calls_filters_result_none() -> None:
    log = TranscriptLog()
    log.add_call("read", {"path": "/a"})
    log.add_result("read", "ok")
    calls = log.get_calls()
    assert len(calls) == 1
    assert calls[0].tool == "read"


def test_get_calls_empty_when_no_calls() -> None:
    log = TranscriptLog()
    log.add_result("read", "ok")
    assert log.get_calls() == []


def test_get_calls_reconstructs_from_mixed_entries() -> None:
    log = TranscriptLog()
    log.add("read", {"path": "/a"}, "ok")  # complete entry, result not None
    log.add_call("write", {"path": "/b"})
    calls = log.get_calls()
    assert len(calls) == 1
    assert calls[0].tool == "write"


# -----------------------------------------------------------------------------
# TranscriptLog — get_results
# -----------------------------------------------------------------------------


def test_get_results_filters_result_not_none() -> None:
    log = TranscriptLog()
    log.add_call("read", {"path": "/a"})
    log.add_result("read", "ok")
    results = log.get_results()
    assert len(results) == 1
    assert results[0].result == "ok"


def test_get_results_empty_when_no_results() -> None:
    log = TranscriptLog()
    log.add_call("read", {})
    assert log.get_results() == []


def test_get_results_includes_complete_entries() -> None:
    log = TranscriptLog()
    log.add("read", {"path": "/a"}, "ok")
    results = log.get_results()
    assert len(results) == 1


# -----------------------------------------------------------------------------
# TranscriptLog — to_list
# -----------------------------------------------------------------------------


def test_to_list_format() -> None:
    log = TranscriptLog()
    log.add("read", {"path": "/a"}, "ok", success=True)
    lst = log.to_list()
    assert len(lst) == 1
    assert lst[0]["tool"] == "read"
    assert lst[0]["args"] == {"path": "/a"}
    assert lst[0]["result"] == "ok"
    assert lst[0]["success"] is True
    assert "timestamp" in lst[0]


def test_to_list_empty() -> None:
    log = TranscriptLog()
    assert log.to_list() == []


def test_to_list_multiple_entries() -> None:
    log = TranscriptLog()
    log.add_call("read", {"path": "/a"})
    log.add_result("read", "ok")
    lst = log.to_list()
    assert len(lst) == 2
    assert lst[0]["result"] is None
    assert lst[1]["result"] == "ok"


# -----------------------------------------------------------------------------
# TranscriptLog — iteration
# -----------------------------------------------------------------------------


def test_iteration_yields_entries() -> None:
    log = TranscriptLog()
    log.add("read", {}, "ok")
    log.add("write", {}, "done")
    tools = [entry.tool for entry in log]
    assert tools == ["read", "write"]


# -----------------------------------------------------------------------------
# TranscriptLog — complex state transitions
# -----------------------------------------------------------------------------


def test_full_conversation_turn() -> None:
    log = TranscriptLog()
    # Call phase
    log.add_call("read", {"path": "/tmp/a"}, call_id="c1")
    log.add_call("read", {"path": "/tmp/b"}, call_id="c2")
    # Result phase
    log.add_result("read", "content-a", success=True)
    log.add_result("read", "", success=False, error="not found")

    assert len(log) == 4
    calls = log.get_calls()
    results = log.get_results()
    assert len(calls) == 2
    assert len(results) == 2
    assert results[1].success is False


def test_mixed_add_and_call_result() -> None:
    log = TranscriptLog()
    log.add("complete", {"x": 1}, "result")
    log.add_call("partial", {"y": 2})
    log.add_result("partial", "result2")
    assert len(log) == 3
    assert len(log.get_calls()) == 1
    assert len(log.get_results()) == 2
