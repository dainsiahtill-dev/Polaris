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


def test_read_only_turns_are_real_work_not_stalls() -> None:
    """读源码、看渲染都是工作，守卫不该在此开火。

    实测：只认写操作的旧判据在 68 个真实 run 上触发 406 次停滞干预，其中
    包括以 repo_tree + repo_rg 结尾的轮次，以及模型思考两轮后才动手的轮次。
    """

    events = [
        _tool(1, 1, "read_file", path="a.js"),
        _tool(2, 2, "repo_rg"),
        _tool(3, 3, "browser_observe"),
    ]
    assert _turn_had_action(events, 1) is True
    assert _turn_had_action(events, 3) is True
    assert _stall_streak(events, [1, 2, 3]) == 0


def test_stall_streak_counts_turns_without_any_tool_call() -> None:
    events = [
        _tool(1, 1, "write_file"),
        {"seq": 2, "kind": "message", "payload": {"message": {"role": "assistant", "content": "thinking"}}},
        {"seq": 3, "kind": "message", "payload": {"message": {"role": "assistant", "content": "still thinking"}}},
    ]
    assert _stall_streak(events, [1, 2, 3]) == 2
    assert _turn_had_action(events, 1) is True


def test_a_single_write_resets_the_idle_streak() -> None:
    events = [
        {"seq": 1, "kind": "message", "payload": {}},
        {"seq": 2, "kind": "message", "payload": {}},
        _tool(3, 3, "write_file"),
    ]
    assert _stall_streak(events, [1, 2, 3]) == 0


def test_repeated_read_paths_threshold() -> None:
    events = [_tool(index, index, "read_file", path="core.js") for index in range(1, 4)]
    assert _repeated_read_paths(events, threshold=3) == ["core.js"]
    assert _repeated_read_paths(events, threshold=4) == []


def test_stall_message_demands_one_action() -> None:
    message = _stall_message(4, ["core.js", "app.js"])
    assert message["role"] == "system"
    assert "连续 4 轮" in message["content"]
    assert "唯一最小动作" in message["content"]
