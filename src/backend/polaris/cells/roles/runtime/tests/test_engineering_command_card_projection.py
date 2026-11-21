from __future__ import annotations

import json

import pytest
from polaris.cells.roles.runtime.internal.engineering_command_cards import build_command_card
from polaris.cells.roles.runtime.internal.engineering_file_card_projection import fold_file_card_previews


def _command_message(exit_code: int = 0) -> dict[str, object]:
    card = build_command_card("make test", "x" * 600, "", exit_code, "command:abc")
    return {"role": "tool", "content": json.dumps({"command_card": card})}


def test_command_card_folds_only_after_inactive_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "3")
    folded = fold_file_card_previews(
        [
            {"role": "assistant", "content": "step-1"},
            _command_message(),
            {"role": "assistant", "content": "step-2"},
            _command_message(),
            {"role": "assistant", "content": "step-3"},
            _command_message(),
            {"role": "assistant", "content": "step-4"},
            _command_message(),
        ],
        semantic_pins={"command:abc": "semantic_card"},
        semantic_summaries={"command:abc": {"summary": "旧命令摘要"}},
    )
    tool_messages = [message for message in folded if message["role"] == "tool"]
    first = json.loads(tool_messages[0]["content"])["command_card"]
    last = json.loads(tool_messages[-1]["content"])["command_card"]
    assert first["fold_state"] == "FOLDED" and first["stdout_preview"] == ""
    assert last["fold_state"] == "ACTIVE" and last["stdout_preview"] == "x" * 600


def test_failed_command_preview_is_pinned() -> None:
    folded = fold_file_card_previews(
        [_command_message(exit_code=1), _command_message(), _command_message(), _command_message()]
    )
    first = json.loads(folded[0]["content"])["command_card"]
    assert first["fold_state"] == "ACTIVE" and first["pin_reason"] == "unresolved_failure"
    assert first["stdout_preview"] == "x" * 600


def test_old_failed_command_folds_without_current_dependency() -> None:
    messages: list[dict[str, object]] = []
    for index in range(11):
        messages.append({"role": "assistant", "content": f"step-{index}"})
        messages.append(_command_message(exit_code=1 if index == 0 else 0))
    folded = fold_file_card_previews(messages)
    first = next(message for message in folded if message["role"] == "tool")
    assert json.loads(first["content"])["command_card"]["fold_state"] == "FOLDED"


def test_failed_command_current_reasoning_is_protected() -> None:
    messages: list[dict[str, object]] = []
    for index in range(11):
        messages.append({"role": "assistant", "content": f"step-{index}"})
        messages.append(_command_message(exit_code=1 if index == 0 else 0))
    messages.append({"role": "assistant", "content": "继续分析 command:abc 的失败原因"})
    folded = fold_file_card_previews(messages)
    first = next(message for message in folded if message["role"] == "tool")
    card = json.loads(first["content"])["command_card"]
    assert card["fold_state"] == "ACTIVE" and card["pin_reason"] == "current_reasoning"
