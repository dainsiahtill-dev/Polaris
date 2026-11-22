from __future__ import annotations

import json

import pytest
from polaris.cells.roles.runtime.internal.engineering_cartographer import (
    NarrativeRejectedError,
    current_facts,
    facts_for_run_card,
    validate_run_card_narrative,
)


def _run() -> dict[str, object]:
    return {"verification": "failed", "workspace_revision": 7, "status": "running"}


def _events() -> list[dict[str, object]]:
    return [
        {
            "kind": "request_prepared",
            "payload": {"request": {"context": {"phase": "EDIT"}}},
        },
        {
            "kind": "verification_completed",
            "payload": {
                "verification": "failed",
                "checks": [{"content": json.dumps({"command_card": {"stdout_preview": "[FAIL] 交互：刹车无效"}})}],
            },
        },
    ]


def test_facts_read_phase_and_failure_signature() -> None:
    facts = current_facts(_run(), _events())
    assert facts["phase"] == "EDIT"
    assert "[FAIL] 交互：刹车无效" in facts["failure_signatures"]


def test_narrative_accepted_when_state_matches() -> None:
    facts = current_facts(_run(), _events())
    payload = {
        "schema_version": "run_card_narrative.v1",
        "phase": "EDIT",
        "intent": "修复交互",
        "completed": ["已确认接口"],
        "blocker": {"signature": "[FAIL] 交互：刹车无效", "occurrences": 1, "candidate_revisions": 1},
        "next_actions": [{"action": "检查控制绑定", "why": "定位", "expected": "车道变化"}],
        "evidence_refs": [],
        "uncertain": True,
        "reason": "证据有限",
    }
    validated = validate_run_card_narrative(payload, facts, allowed_refs=set())
    assert validated is not None and validated["phase"] == "EDIT"


def test_narrative_rejected_when_phase_outside_allowed_set() -> None:
    run = dict(_run(), verification="not_run")
    facts = current_facts(run, _events())
    payload = {
        "schema_version": "run_card_narrative.v1",
        "phase": "RETEST",
        "intent": "",
        "completed": [],
        "blocker": None,
        "next_actions": [],
        "evidence_refs": [],
        "uncertain": True,
        "reason": "",
    }
    with pytest.raises(NarrativeRejectedError):
        validate_run_card_narrative(payload, facts, allowed_refs=set())


def test_retest_phase_allowed_after_real_verification() -> None:
    facts = current_facts(_run(), _events())
    assert "RETEST" in facts["allowed_phases"]
    payload = {
        "schema_version": "run_card_narrative.v1",
        "phase": "RETEST",
        "intent": "",
        "completed": [],
        "blocker": None,
        "next_actions": [],
        "evidence_refs": [],
        "uncertain": True,
        "reason": "",
    }
    assert validate_run_card_narrative(payload, facts, allowed_refs=set()) is not None


def test_narrative_normalizes_model_drift() -> None:
    facts = current_facts(_run(), _events())
    payload = {
        "schema_version": "1.0",
        "phase": "EDIT",
        "intent": "修复",
        "completed": ["读取接口"],
        "blocker": "刹车无效",
        "next_actions": ["检查控制绑定"],
        "evidence_refs": [],
        "uncertain": "unknown",
        "reason": "证据有限",
    }
    validated = validate_run_card_narrative(payload, facts, allowed_refs=set())
    assert validated is not None
    assert validated["schema_version"] == "run_card_narrative.v1"
    assert validated["blocker"]["signature"] == "[FAIL] 交互：刹车无效"
    assert validated["next_actions"][0]["action"] == "检查控制绑定"
    assert validated["uncertain"] is True


def test_narrative_rejects_invented_blocker() -> None:
    facts = current_facts(_run(), _events())
    payload = {
        "schema_version": "run_card_narrative.v1",
        "phase": "EDIT",
        "intent": "",
        "completed": [],
        "blocker": {"signature": "虚构症状", "occurrences": 1, "candidate_revisions": 1},
        "next_actions": [],
        "evidence_refs": [],
        "uncertain": True,
        "reason": "",
    }
    with pytest.raises(NarrativeRejectedError):
        validate_run_card_narrative(payload, facts, allowed_refs=set())


def test_schedule_does_not_cancel_in_flight_draw() -> None:
    import asyncio

    from polaris.cells.roles.runtime.internal.engineering_cartographer import CartographerRuntime

    class _SlowPool:
        async def draw_many(self, requests):
            await asyncio.sleep(0.05)
            return ["drawing"]

    async def main() -> None:
        runtime = CartographerRuntime(_SlowPool())
        runtime.schedule({"a": 1}, fingerprint="one")
        runtime.schedule({"a": 2}, fingerprint="two")
        await asyncio.sleep(0.08)
        assert runtime.poll() == "drawing"

    asyncio.run(main())


def test_fingerprint_changes_with_stage() -> None:
    first, fingerprint_one = facts_for_run_card(_run(), _events())
    changed = dict(_run(), verification="passed")
    _, fingerprint_two = facts_for_run_card(changed, _events())
    assert fingerprint_one != fingerprint_two
    assert first["working_state"]["phase"] == "EDIT"
