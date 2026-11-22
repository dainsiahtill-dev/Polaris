from __future__ import annotations

import json

from polaris.cells.roles.runtime.internal.engineering_loop import (
    _repeated_read_paths,
    _stall_message,
    _stall_streak,
    _turn_had_action,
)


def _tool(seq: int, turn: int, tool: str, ok: bool = True, path: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"turn_seq": turn, "tool": tool, "ok": ok}
    if path is not None:
        payload["content"] = json.dumps({"file_card": {"path": path}})
    return {"seq": seq, "kind": "tool_observed", "payload": payload}


def test_stall_streak_counts_read_only_turns() -> None:
    events = [
        _tool(1, 1, "read_file", path="a.js"),
        _tool(2, 2, "read_file", path="a.js"),
        _tool(3, 3, "execute_command", ok=False),
    ]
    assert _stall_streak(events, [1, 2, 3]) == 3
    assert _turn_had_action(events, 3) is False


def test_a_single_write_resets_streak() -> None:
    events = [
        _tool(1, 1, "read_file", path="a.js"),
        _tool(2, 2, "write_file"),
        _tool(3, 3, "read_file", path="a.js"),
    ]
    assert _stall_streak(events, [1, 2, 3]) == 1


def test_repeated_read_paths_threshold() -> None:
    events = [_tool(index, index, "read_file", path="core.js") for index in range(1, 4)]
    assert _repeated_read_paths(events, threshold=3) == ["core.js"]
    assert _repeated_read_paths(events, threshold=4) == []


def test_stall_message_demands_one_action() -> None:
    message = _stall_message(4, ["core.js", "app.js"])
    assert message["role"] == "system"
    assert "连续 4 轮" in message["content"]
    assert "唯一最小动作" in message["content"]
