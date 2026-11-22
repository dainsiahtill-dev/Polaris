from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card
from polaris.cells.roles.runtime.internal.engineering_loop import _bounded_request_messages


class _Store:
    def __init__(self) -> None:
        self.appended: list[dict[str, Any]] = []

    def load_events(self, *_args: object, **_kwargs: object) -> list[dict[str, Any]]:
        return []

    def load_run(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "session_id": "fold-events",
            "workspace": ".",
            "status": "running",
            "verification": "not_run",
            "workspace_revision": 0,
        }

    def append_event(self, _session_id: str, *, kind: str, role: str, payload: dict[str, Any]) -> int:
        self.appended.append({"kind": kind, "role": role, "payload": payload})
        return len(self.appended)


class _Advisor:
    def pin_reasons(self) -> dict[str, str]:
        return {"a.py": "semantic_card"}

    def fold_summaries(self) -> dict[str, dict[str, str]]:
        return {"a.py": {"summary": "旧文件摘要"}}


def _message(preview: str) -> dict[str, object]:
    card = build_file_card(
        "a.py",
        preview,
        revision=1,
        preview=preview,
        truncated=False,
        start_line=1,
        end_line=1,
        snapshot_ref="filecard:a",
    )
    return {
        "role": "tool",
        "content": json.dumps(
            {"call_id": "call-1", "tool": "read_file", "ok": True, "content": json.dumps({"file_card": card})}
        ),
    }


def test_first_fold_emits_realtime_event(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default fold window is 24 activities; this fixture has 11 turns, so
    # pin a smaller window to exercise the first-fold path.
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "4")
    store = _Store()
    messages: list[dict[str, Any]] = [{"role": "user", "content": "fix the project"}]
    for index in range(11):
        messages.append({"role": "assistant", "content": f"step-{index}"})
        # Above the fold byte floor, otherwise the card is never a fold candidate.
        messages.append(_message("x" * 1200))
    _bounded_request_messages(
        messages,
        store=store,
        command=SimpleNamespace(session_id="run", workspace="."),
        role="director",
        context_window_tokens=0,
        visual_binding=None,
        audit={},
        semantic_runtime=_Advisor(),
    )
    folds = [event for event in store.appended if event["kind"] == "context_projection_folded"]
    assert len(folds) == 1
    assert folds[0]["payload"]["projection_ref"] == "filecard:a"
    assert folds[0]["payload"]["fold_state"] == "FOLDED"
