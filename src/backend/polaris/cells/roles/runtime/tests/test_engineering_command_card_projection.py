from __future__ import annotations

import json

from polaris.cells.roles.runtime.internal.engineering_command_cards import build_command_card
from polaris.cells.roles.runtime.internal.engineering_file_card_projection import fold_file_card_previews


def _command_message(exit_code: int = 0) -> dict[str, object]:
    card = build_command_card("make test", "build log", "", exit_code, "command:abc")
    return {"role": "tool", "content": json.dumps({"command_card": card})}


def test_command_card_folds_only_after_inactive_window() -> None:
    folded = fold_file_card_previews([_command_message(), _command_message(), _command_message(), _command_message()])
    first = json.loads(folded[0]["content"])["command_card"]
    last = json.loads(folded[-1]["content"])["command_card"]
    assert first["fold_state"] == "FOLDED" and first["stdout_preview"] == ""
    assert last["fold_state"] == "ACTIVE" and last["stdout_preview"] == "build log"


def test_failed_command_preview_is_pinned() -> None:
    folded = fold_file_card_previews(
        [_command_message(exit_code=1), _command_message(), _command_message(), _command_message()]
    )
    first = json.loads(folded[0]["content"])["command_card"]
    assert first["fold_state"] == "ACTIVE" and first["pin_reason"] == "unresolved_failure"
    assert first["stdout_preview"] == "build log"
