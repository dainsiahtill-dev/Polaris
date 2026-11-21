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


def test_first_three_activities_keep_continuity() -> None:
    folded = fold_file_card_previews([_message("a.py", "a"), _message("b.py", "b"), _message("c.py", "c")])
    assert all(json.loads(message["content"])["file_card"]["fold_state"] == "ACTIVE" for message in folded)


def test_inactive_card_folds_after_three_activities() -> None:
    folded = fold_file_card_previews(
        [
            {"role": "assistant", "content": "step-1"},
            _message("a.py", "a"),
            {"role": "assistant", "content": "step-2"},
            _message("b.py", "b"),
            {"role": "assistant", "content": "step-3"},
            _message("c.py", "c"),
            {"role": "assistant", "content": "step-4"},
            _message("d.py", "d"),
        ]
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
            _message("a.py", "a"),
            {"role": "assistant", "content": "step-2"},
            _message("b.py", "b"),
        ]
    )
    first = next(message for message in folded if message["role"] == "tool")
    assert json.loads(first["content"])["file_card"]["fold_state"] == "FOLDED"


def test_folded_card_does_not_fold_again() -> None:
    folded_once = _message("a.py", "", fold_state="FOLDED", last_fold_activity_seq=1)
    folded_twice = fold_file_card_previews([folded_once, _message("b.py", "b")])
    card = json.loads(folded_twice[0]["content"])["file_card"]
    assert card["last_fold_activity_seq"] == 1 and card["content_preview"] == ""


def test_rehydration_grace_blocks_immediate_refold() -> None:
    recovered = _message("a.py", "source", recovered_from="filecard:old", last_rehydrated_activity_seq=1)
    folded = fold_file_card_previews([recovered, _message("b.py", "b"), _message("c.py", "c")])
    assert json.loads(folded[0]["content"])["file_card"]["fold_state"] == "ACTIVE"
