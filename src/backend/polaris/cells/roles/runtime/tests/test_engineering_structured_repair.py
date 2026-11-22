from __future__ import annotations

import json

from polaris.cells.roles.runtime.internal.engineering_loop import (
    _active_structured_repairs,
    _structured_repair_message,
)


def _verification(seq: int, revision: int, line: str) -> dict[str, object]:
    return {
        "seq": seq,
        "kind": "verification_completed",
        "payload": {
            "workspace_revision": revision,
            "verification": "failed",
            "checks": [{"call_id": f"v{seq}", "content": json.dumps({"command_card": {"stdout_preview": line}})}],
        },
    }


def test_same_symptom_across_candidates_triggers_structured_repair() -> None:
    events = [
        _verification(1, 1, "[FAIL] 交互：刹车无效"),
        _verification(2, 2, "[FAIL] 交互：刹车无效"),
    ]
    repairs = _active_structured_repairs(events)
    assert len(repairs) == 1
    assert repairs[0]["occurrences"] == 2 and repairs[0]["candidate_revisions"] == 2


def test_same_candidate_repetition_does_not_trigger() -> None:
    events = [_verification(1, 1, "[FAIL] 交互：刹车无效"), _verification(2, 1, "[FAIL] 交互：刹车无效")]
    assert _active_structured_repairs(events) == []


def test_structured_repair_message_requires_falsifiable_plan() -> None:
    message = _structured_repair_message([{"signature": "刹车无效", "occurrences": 3, "candidate_revisions": 3}])
    assert message["role"] == "system"
    assert "可证伪假设" in message["content"]
    assert "区分方法" in message["content"] and "复测" in message["content"]
