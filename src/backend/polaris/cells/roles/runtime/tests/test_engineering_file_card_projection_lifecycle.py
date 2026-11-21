from __future__ import annotations

import json

import pytest
from polaris.cells.roles.runtime.internal.engineering_file_card_projection import fold_file_card_previews
from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card


def _message(path: str, preview: str, **extra: object) -> dict[str, object]:
    card = build_file_card(
        path,
        preview,
        revision=1,
        preview=preview,
        truncated=False,
        start_line=1,
        end_line=1,
        snapshot_ref=f"filecard:{'a' * 64}",
    )
    card.update(extra)
    return {"role": "tool", "content": json.dumps({"file_card": card})}


def _envelope(path: str, preview: str) -> dict[str, object]:
    message = _message(path, preview)
    return {
        "role": "tool",
        "content": json.dumps({"call_id": "call-1", "tool": "read_file", "ok": True, "content": message["content"]}),
    }


def test_first_three_activities_keep_continuity() -> None:
    folded = fold_file_card_previews([_message("a.py", "a"), _message("b.py", "b"), _message("c.py", "c")])
    assert all(json.loads(message["content"])["file_card"]["fold_state"] == "ACTIVE" for message in folded)


def test_inactive_card_folds_after_configured_activities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "3")
    folded = fold_file_card_previews(
        [
            {"role": "assistant", "content": "step-1"},
            _message("a.py", "a" * 600),
            {"role": "assistant", "content": "step-2"},
            _message("b.py", "b"),
            {"role": "assistant", "content": "step-3"},
            _message("c.py", "c"),
            {"role": "assistant", "content": "step-4"},
            _message("d.py", "d"),
        ],
        semantic_pins={"a.py": "semantic_card"},
        semantic_summaries={"a.py": {"summary": "旧文件摘要"}},
    )
    first = next(message for message in folded if message["role"] == "tool")
    first = json.loads(first["content"])["file_card"]
    assert first["fold_state"] == "FOLDED" and first["content_preview"] == ""
    assert first["last_fold_activity_seq"] == 0


def test_inactive_threshold_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "1")
    folded = fold_file_card_previews(
        [
            {"role": "assistant", "content": "step-1"},
            _message("a.py", "a" * 600),
            {"role": "assistant", "content": "step-2"},
            _message("b.py", "b"),
        ],
        semantic_pins={"a.py": "semantic_card"},
        semantic_summaries={"a.py": {"summary": "旧文件摘要"}},
    )
    first = next(message for message in folded if message["role"] == "tool")
    assert json.loads(first["content"])["file_card"]["fold_state"] == "FOLDED"


def test_small_duplicate_payload_is_not_folded() -> None:
    folded = fold_file_card_previews(
        [
            {"role": "assistant", "content": "step-1"},
            _message("a.py", "a"),
            {"role": "assistant", "content": "step-2"},
            _message("a.py", "a"),
            {"role": "assistant", "content": "step-3"},
            _message("a.py", "a"),
            {"role": "assistant", "content": "step-4"},
            _message("a.py", "a"),
        ]
    )
    first = next(message for message in folded if message["role"] == "tool")
    assert json.loads(first["content"])["file_card"]["fold_state"] == "ACTIVE"


def test_large_duplicate_payload_can_fold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "3")
    large = "x" * 600
    folded = fold_file_card_previews(
        [
            {"role": "assistant", "content": "step-1"},
            _message("a.py", large),
            {"role": "assistant", "content": "step-2"},
            _message("a.py", large),
            {"role": "assistant", "content": "step-3"},
            _message("a.py", large),
            {"role": "assistant", "content": "step-4"},
            _message("a.py", large),
        ],
        semantic_pins={"a.py": "semantic_card"},
        semantic_summaries={"a.py": {"summary": "旧文件摘要"}},
    )
    first = next(message for message in folded if message["role"] == "tool")
    assert json.loads(first["content"])["file_card"]["fold_state"] == "FOLDED"


def test_current_edit_pin_survives_inactivity() -> None:
    large = "x" * 600
    folded = fold_file_card_previews(
        [
            {"role": "assistant", "content": "step-1"},
            _message("a.py", large),
            {"role": "assistant", "content": "step-2"},
            _message("b.py", "b"),
            {"role": "assistant", "content": "step-3"},
            _message("c.py", "c"),
            {"role": "assistant", "content": "step-4"},
            _message("d.py", "d"),
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "edit_file", "arguments": json.dumps({"file": "a.py"})}}],
            },
        ],
    )
    first = next(message for message in folded if message["role"] == "tool")
    assert json.loads(first["content"])["file_card"]["fold_state"] == "ACTIVE"


def test_acceptance_pin_survives_inactivity() -> None:
    large = "x" * 600
    folded = fold_file_card_previews(
        [
            {"role": "user", "content": "verify a.py"},
            {"role": "assistant", "content": "step-1"},
            _message("a.py", large),
            {"role": "assistant", "content": "step-2"},
            _message("b.py", "b"),
            {"role": "assistant", "content": "step-3"},
            _message("c.py", "c"),
            {"role": "assistant", "content": "step-4"},
            _message("d.py", "d"),
        ],
    )
    first = next(message for message in folded if message["role"] == "tool")
    assert json.loads(first["content"])["file_card"]["fold_state"] == "ACTIVE"


def test_current_reasoning_pin_survives_ten_activities() -> None:
    large = "x" * 600
    messages: list[dict[str, object]] = []
    for index in range(11):
        messages.append({"role": "assistant", "content": f"step-{index}"})
        messages.append(_message("a.py" if index == 0 else "b.py", large))
    messages.append({"role": "assistant", "content": "继续修复 a.py 的交互。"})
    folded = fold_file_card_previews(messages)
    first = next(message for message in folded if message["role"] == "tool")
    card = json.loads(first["content"])["file_card"]
    assert card["fold_state"] == "ACTIVE" and card["pin_reason"] == "current_reasoning"


def test_folded_card_does_not_fold_again() -> None:
    folded_once = _message("a.py", "", fold_state="FOLDED", last_fold_activity_seq=1)
    folded_twice = fold_file_card_previews([folded_once, _message("b.py", "b")])
    card = json.loads(folded_twice[0]["content"])["file_card"]
    assert card["last_fold_activity_seq"] == 1 and card["content_preview"] == ""


def test_rehydration_grace_blocks_immediate_refold() -> None:
    recovered = _message("a.py", "source", recovered_from="filecard:old", last_rehydrated_activity_seq=1)
    folded = fold_file_card_previews([recovered, _message("b.py", "b"), _message("c.py", "c")])
    assert json.loads(folded[0]["content"])["file_card"]["fold_state"] == "ACTIVE"


def test_nested_tool_envelope_is_folded_without_losing_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "3")
    large = "x" * 600
    folded = fold_file_card_previews(
        [
            {"role": "assistant", "content": "step-1"},
            _envelope("a.py", large),
            {"role": "assistant", "content": "step-2"},
            _envelope("a.py", large),
            {"role": "assistant", "content": "step-3"},
            _envelope("a.py", large),
            {"role": "assistant", "content": "step-4"},
            _envelope("a.py", large),
        ],
        semantic_pins={"a.py": "semantic_card"},
        semantic_summaries={"a.py": {"summary": "旧文件摘要"}},
    )
    first = next(message for message in folded if message["role"] == "tool")
    envelope = json.loads(first["content"])
    card = json.loads(envelope["content"])["file_card"]
    assert envelope["call_id"] == "call-1"
    assert card["fold_state"] == "FOLDED" and card["content_preview"] == ""
