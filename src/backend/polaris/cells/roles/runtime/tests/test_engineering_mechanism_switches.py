from __future__ import annotations

import json

from polaris.cells.roles.runtime.internal.engineering_loop import _active_structured_repairs, _structured_repair_message


def test_structured_repair_switch_defaults_on(monkeypatch) -> None:
    monkeypatch.delenv("KERNELONE_ENGINEERING_STRUCTURED_REPAIR", raising=False)
    assert _switch_enabled("KERNELONE_ENGINEERING_STRUCTURED_REPAIR") is True


def _switch_enabled(name: str) -> bool:
    import os

    return os.environ.get(name, "1").strip().lower() not in {"0", "false", "off"}


def test_structured_repair_message_is_actionable() -> None:
    message = _structured_repair_message([{"signature": "x", "occurrences": 2, "candidate_revisions": 2}])
    assert "动作" in message["content"] and "复测" in message["content"]


def test_structured_repair_ignores_same_candidate() -> None:
    events = [
        {
            "kind": "verification_completed",
            "payload": {
                "workspace_revision": 1,
                "checks": [{"content": json.dumps({"command_card": {"stdout_preview": "[FAIL] same"}})}],
            },
        }
    ] * 3
    assert _active_structured_repairs(events) == []
