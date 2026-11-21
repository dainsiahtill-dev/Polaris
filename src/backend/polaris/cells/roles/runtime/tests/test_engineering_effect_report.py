from __future__ import annotations

import json

from polaris.cells.roles.runtime.internal.engineering_effect_report import build_effect_report


def _prepared_request(*states: str) -> dict[str, object]:
    return {
        "seq": 1,
        "kind": "request_prepared",
        "payload": {
            "request": {
                "options": {
                    "messages": [
                        {
                            "role": "tool",
                            "content": json.dumps(
                                {"content": json.dumps({"file_card": {"fold_state": state}})}
                            ),
                        }
                        for state in states
                    ]
                }
            }
        },
    }


def test_effect_report_counts_folds_and_injections() -> None:
    events = [
        {"seq": 1, "kind": "turn_started", "payload": {}},
        _prepared_request("FOLDED", "ACTIVE"),
        {
            "seq": 4,
            "kind": "request_prepared",
            "payload": {
                "request": {"options": {"messages": [{"role": "system", "content": "ContextOS 长期记忆（摘要）"}]}}
            },
        },
        {"seq": 5, "kind": "verification_completed", "payload": {"verification": "passed"}},
    ]
    report = build_effect_report({"session_id": "run", "workspace": "/ws", "status": "completed"}, events)
    assert report["schema_version"] == "contextos.effect_report.v1"
    assert report["cards_total"] == 2 and report["cards_folded"] == 1
    assert report["fold_rate"] == 0.5
    assert report["memory_injected_requests"] == 1
    assert report["verification_attempts"] == 1 and report["verification_passed"] == 1
    assert report["turns"] == 1


def test_effect_report_handles_empty_run() -> None:
    report = build_effect_report({"session_id": "run", "workspace": "/ws", "status": "failed"}, [])
    assert report["cards_total"] == 0 and report["fold_rate"] is None
    assert report["verification_attempts"] == 0
