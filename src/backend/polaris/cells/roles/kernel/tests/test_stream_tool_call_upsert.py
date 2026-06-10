"""ADR-0090 I2: keyed upsert + subset-supersede for streamed tool calls.

Layer-2 defence for the partial-emission bug: even if an upstream emits both a
placeholder (args={}) and the completed version of the same logical call, the
pending native batch must end up with exactly the completed call.
"""

from __future__ import annotations

import json
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import (
    supersede_partial_tool_calls,
    upsert_stream_native_tool_call,
)


def _native_call(name: str, args: Any, call_id: str = "c1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


class TestUpsertStreamNativeToolCall:
    def test_same_call_id_refines_in_place(self) -> None:
        calls: list[dict[str, Any]] = []
        index: dict[str, int] = {}

        upsert_stream_native_tool_call(calls, index, tool_name="repo_rg", tool_args={}, call_id="c1")
        upsert_stream_native_tool_call(
            calls,
            index,
            tool_name="repo_rg",
            tool_args={"pattern": "class ExpressionWrapper"},
            call_id="c1",
        )

        assert len(calls) == 1
        assert calls[0]["function"]["arguments"] == {"pattern": "class ExpressionWrapper"}
        assert calls[0]["id"] == "c1"

    def test_distinct_call_ids_append(self) -> None:
        calls: list[dict[str, Any]] = []
        index: dict[str, int] = {}

        upsert_stream_native_tool_call(calls, index, tool_name="repo_rg", tool_args={"pattern": "a"}, call_id="c1")
        upsert_stream_native_tool_call(calls, index, tool_name="repo_rg", tool_args={"pattern": "b"}, call_id="c2")

        assert len(calls) == 2
        assert [c["function"]["arguments"]["pattern"] for c in calls] == ["a", "b"]

    def test_refinement_preserves_slot_order(self) -> None:
        calls: list[dict[str, Any]] = []
        index: dict[str, int] = {}

        upsert_stream_native_tool_call(calls, index, tool_name="repo_rg", tool_args={}, call_id="c1")
        upsert_stream_native_tool_call(calls, index, tool_name="read_file", tool_args={"file": "a.py"}, call_id="c2")
        upsert_stream_native_tool_call(calls, index, tool_name="repo_rg", tool_args={"pattern": "x"}, call_id="c1")

        assert [c["function"]["name"] for c in calls] == ["repo_rg", "read_file"]
        assert calls[0]["function"]["arguments"] == {"pattern": "x"}

    def test_empty_call_id_appends_without_index_tracking(self) -> None:
        calls: list[dict[str, Any]] = []
        index: dict[str, int] = {}

        upsert_stream_native_tool_call(calls, index, tool_name="repo_rg", tool_args={}, call_id="")
        upsert_stream_native_tool_call(calls, index, tool_name="repo_rg", tool_args={"pattern": "x"}, call_id="")

        assert len(calls) == 2
        assert index == {}


class TestSupersedePartialToolCalls:
    def test_placeholder_superseded_by_completed_call(self) -> None:
        calls = [
            _native_call("repo_rg", {}, call_id="stream_tool_call_1"),
            _native_call("repo_rg", {"pattern": "class X"}, call_id="stream_tool_call_2"),
        ]

        kept = supersede_partial_tool_calls(calls)

        assert len(kept) == 1
        assert kept[0]["function"]["arguments"] == {"pattern": "class X"}

    def test_strict_subset_superseded(self) -> None:
        calls = [
            _native_call("repo_read_slice", {"file": "a.py"}, call_id="c1"),
            _native_call("repo_read_slice", {"file": "a.py", "start": 1, "end": 80}, call_id="c2"),
        ]

        kept = supersede_partial_tool_calls(calls)

        assert len(kept) == 1
        assert kept[0]["function"]["arguments"]["start"] == 1

    def test_distinct_same_tool_calls_preserved(self) -> None:
        calls = [
            _native_call("repo_rg", {"pattern": "alpha"}, call_id="c1"),
            _native_call("repo_rg", {"pattern": "beta"}, call_id="c2"),
        ]

        assert supersede_partial_tool_calls(calls) == calls

    def test_equal_args_both_preserved(self) -> None:
        calls = [
            _native_call("repo_tree", {}, call_id="c1"),
            _native_call("repo_tree", {}, call_id="c2"),
        ]

        assert supersede_partial_tool_calls(calls) == calls

    def test_different_tools_untouched(self) -> None:
        calls = [
            _native_call("repo_rg", {}, call_id="c1"),
            _native_call("read_file", {"file": "a.py"}, call_id="c2"),
        ]

        assert supersede_partial_tool_calls(calls) == calls

    def test_arguments_encoded_as_json_string(self) -> None:
        calls = [
            _native_call("edit_blocks", json.dumps({}), call_id="c1"),
            _native_call(
                "edit_blocks",
                json.dumps({"file": "a.py", "start": 3, "end": 5, "replace": "pass"}),
                call_id="c2",
            ),
        ]

        kept = supersede_partial_tool_calls(calls)

        assert len(kept) == 1
        assert kept[0]["id"] == "c2"

    def test_single_call_list_unchanged(self) -> None:
        calls = [_native_call("repo_rg", {}, call_id="c1")]

        assert supersede_partial_tool_calls(calls) == calls
