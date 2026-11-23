from __future__ import annotations

import json

import pytest
from polaris.cells.roles.runtime.internal.engineering_file_card_projection import fold_file_card_previews
from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card


def _message(path: str, preview: str) -> dict[str, object]:
    card = build_file_card(
        path,
        preview,
        revision=1,
        preview=preview,
        truncated=False,
        start_line=1,
        end_line=2,
        snapshot_ref=f"filecard:{'a' * 64}",
    )
    return {"role": "tool", "content": json.dumps({"file_card": card})}


def test_duplicate_file_cards_keep_only_latest_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "3")
    folded = fold_file_card_previews(
        [
            {"role": "assistant", "content": "step-1"},
            _message("a.py", "o" * 1200),
            {"role": "assistant", "content": "step-2"},
            _message("a.py", "n" * 1200),
            {"role": "assistant", "content": "step-3"},
            _message("b.py", "keep"),
            {"role": "assistant", "content": "step-4"},
            _message("c.py", "keep"),
        ],
        semantic_summaries={"a.py": {"summary": "旧文件摘要"}},
    )
    tool_messages = [message for message in folded if message["role"] == "tool"]
    first, second, third, _fourth = (json.loads(message["content"])["file_card"] for message in tool_messages)
    assert first["content_preview"] == "" and first["fold_state"] == "FOLDED"
    assert second["content_preview"] == "n" * 1200 and second["fold_state"] == "ACTIVE"
    assert third["content_preview"] == "keep" and third["fold_state"] == "ACTIVE"


def test_current_edit_pin_keeps_preview_and_reason() -> None:
    messages = [
        _message("a.py", "source"),
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "edit_file",
                        "arguments": json.dumps({"file": "a.py", "search": "old", "replace": "new"}),
                    }
                }
            ],
        },
        _message("a.py", "new"),
    ]
    folded = fold_file_card_previews(messages)
    first = json.loads(folded[0]["content"])["file_card"]
    assert first["pin_reason"] == "current_edit"
    assert first["content_preview"] == "source" and first["fold_state"] == "ACTIVE"
