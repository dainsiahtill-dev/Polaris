from __future__ import annotations

import json
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_cartographer import current_facts, deterministic_run_card
from polaris.cells.roles.runtime.internal.engineering_command_cards import build_command_card
from polaris.cells.roles.runtime.internal.engineering_loop import _active_structured_repairs
from polaris.cells.roles.runtime.internal.engineering_memory_cards import build_failure_cluster_cards
from polaris.cells.roles.runtime.internal.engineering_validation_fingerprint import (
    build_validation_execution,
    failure_signatures,
    validation_key,
)
from polaris.cells.roles.runtime.public.contracts.engineering_run import ToolObservation, tool_observation_payload


def test_validation_key_separates_input_from_symptoms() -> None:
    binding = {"commands": ["node tests/run-tests.js"], "toolchain": "node18"}
    first = validation_key(commands=["node tests/run-tests.js"], workspace_revision=4, input_binding=binding)
    second = validation_key(commands=["node tests/run-tests.js"], workspace_revision=4, input_binding=binding)
    changed = validation_key(commands=["node tests/run-tests.js"], workspace_revision=5, input_binding=binding)
    assert first == second and first != changed


def test_validation_key_includes_commands_and_binding() -> None:
    base = validation_key(commands=["a"], workspace_revision=1, input_binding={"x": 1})
    assert base != validation_key(commands=["b"], workspace_revision=1, input_binding={"x": 1})
    assert base != validation_key(commands=["a"], workspace_revision=1, input_binding={"x": 2})


def test_failure_signatures_are_not_cache_keys() -> None:
    checks = (
        ToolObservation(
            "v1",
            "verify_project",
            True,
            json.dumps({"command_card": {"stdout_preview": "[FAIL] 交互：刹车无效"}}),
            1,
            False,
        ),
        ToolObservation(
            "v2",
            "verify_project",
            True,
            json.dumps({"command_card": {"stdout_preview": "[PASS] 启动正常"}}),
            0,
            False,
        ),
    )
    signatures = failure_signatures(checks)
    assert signatures == ["[FAIL] 交互：刹车无效"]


def _observed(call_id: str, output: str, exit_code: int, command: str = "node --test") -> ToolObservation:
    content = json.dumps({"command_card": build_command_card(command, output, "", exit_code, f"log:{call_id}")})
    return ToolObservation(call_id, "execute_command", True, content, exit_code, False)


def _completion(seq: int, verdict: str, *checks: ToolObservation) -> dict[str, Any]:
    return {
        "kind": "verification_completed",
        "seq": seq,
        "payload": {
            "workspace_revision": seq,
            "verification": verdict,
            "checks": [tool_observation_payload(check) for check in checks],
        },
    }


@pytest.mark.parametrize("verdict", ["passed", "unknown", "not_run"])
def test_validation_signatures_require_observed_failure(verdict: str) -> None:
    check = _observed("v", "ok 1 - handles error\n# fail 0", 0 if verdict == "passed" else 1)
    record = build_validation_execution(
        validation_key="k", commands=["node --test"], workspace_revision=1, verification=verdict, checks=(check,)
    )
    assert record["failure_signatures"] == []


def test_failure_in_a_command_does_not_turn_passing_tap_lines_into_symptoms() -> None:
    check = _observed("v", "ok 1 - handles error\n# fail 0\nAssertionError: later step failed", 1)
    assert failure_signatures([check]) == ["AssertionError: later step failed"]


def test_unknown_or_ambiguous_legacy_checks_cannot_inherit_another_checks_failure() -> None:
    event = _completion(1, "failed", _observed("real", "AssertionError: real", 1))
    event["payload"]["checks"].extend(
        [
            {"call_id": "interrupted", "ok": False, "interrupted": True, "content": "[FAIL] interrupted"},
            {"call_id": "unknown", "ok": False, "exit_code": None, "content": "[FAIL] no exit"},
            {"call_id": "legacy", "content": "[FAIL] no individual outcome"},
        ]
    )
    assert current_facts({"verification": "failed"}, [event])["failure_signatures"] == {"AssertionError: real"}


def test_mixed_checks_count_only_observed_failures_in_all_projections() -> None:
    passed = _observed("pass", "ok 1 - handles error\n# fail 0", 0)
    failed = _observed("fail", "AssertionError: bad value", 1)
    assert failure_signatures([passed, failed]) == ["AssertionError: bad value"]
    events = [_completion(1, "failed", passed, failed), _completion(2, "failed", passed, failed)]
    facts = current_facts({"verification": "failed"}, events)
    assert facts["failure_signatures"] == {"AssertionError: bad value"}
    cards = build_failure_cluster_cards(events)
    assert [(card["title"], card["count"], card["evidence_refs"]) for card in cards] == [
        ("AssertionError: bad value", 2, ["fail"])
    ]
    repairs = _active_structured_repairs(events)
    assert repairs == [{"signature": "AssertionError: bad value", "occurrences": 2, "candidate_revisions": 2}]


def test_duplicate_command_preview_and_summary_count_once_per_verified_candidate() -> None:
    events = []
    for seq in (1, 3):
        failed = _observed(f"v{seq}", "AssertionError: repeated", 1)
        events.extend(
            [
                {"kind": "tool_observed", "seq": seq, "payload": {**tool_observation_payload(failed), "ok": False}},
                _completion(seq + 1, "failed", failed),
            ]
        )
    assert [(card["title"], card["count"]) for card in build_failure_cluster_cards(events)] == [
        ("AssertionError: repeated", 2)
    ]
    assert _active_structured_repairs(events)[0]["occurrences"] == 2


def test_failure_cluster_keeps_the_observed_command_ref_when_verification_consumes_it() -> None:
    check = _observed("v1", "unexpected result", 1)
    events = [
        {"kind": "tool_observed", "seq": 1, "payload": {**tool_observation_payload(check), "ok": False}},
        _completion(2, "failed", check),
    ]
    cards = build_failure_cluster_cards(events)
    assert [(card["title"], card["count"], set(card["evidence_refs"])) for card in cards] == [
        ("command failed", 1, {"log:v1", "v1"})
    ]


def test_pass_clears_current_blocker_and_repairs_but_retains_resolved_history() -> None:
    failed = _observed("fail", "AssertionError: once broken", 1)
    passed = _observed("pass", "ok 1 - now handles AssertionError: once broken\n# fail 0", 0)
    events = [_completion(1, "failed", failed), _completion(2, "failed", failed), _completion(3, "passed", passed)]
    assert deterministic_run_card({"verification": "passed"}, events, phase="RETEST")["blocker"] is None
    assert _active_structured_repairs(events) == []
    assert [(card["title"], card["count"], card["unresolved"]) for card in build_failure_cluster_cards(events)] == [
        ("AssertionError: once broken", 2, False)
    ]
    assert _active_structured_repairs([*events, _completion(4, "failed", failed)]) == []


def test_disjoint_verification_does_not_clear_a_failed_check_or_recommend_handoff() -> None:
    failure = _observed("sum", "AssertionError: addition mismatch", 1, "pytest test_sum.py")
    lint = _observed("lint", "clean", 0, "ruff check .")
    events = [_completion(1, "failed", failure), _completion(2, "passed", lint)]
    run = {"verification": "passed", "workspace_revision": 2}
    card = deterministic_run_card(run, events, phase="RETEST")
    assert card["blocker"]["signature"] == "AssertionError: addition mismatch"
    assert "QA" not in card["next_actions"][0]["action"]
    assert build_failure_cluster_cards(events)[0]["unresolved"] is True


def test_partial_success_resolves_only_the_matching_check_even_with_shared_symptoms() -> None:
    first = _observed("a1", "AssertionError: mismatch", 1, "pytest a.py")
    second = _observed("b1", "AssertionError: mismatch", 1, "pytest b.py")
    fixed_a = _observed("a2", "ok", 0, "pytest a.py")
    other = _observed("c1", "TypeError: bad argument", 1, "pytest c.py")
    events = [
        _completion(1, "failed", first),
        _completion(2, "failed", second),
        _completion(3, "failed", fixed_a, other),
    ]
    assert current_facts({"verification": "failed"}, events)["failure_revisions"] == {
        "AssertionError: mismatch": [2],
        "TypeError: bad argument": [3],
    }
    events.append(_completion(4, "passed", _observed("b2", "ok", 0, "pytest b.py")))
    assert {card["title"]: card["unresolved"] for card in build_failure_cluster_cards(events)} == {
        "AssertionError: mismatch": False,
        "TypeError: bad argument": True,
    }


def test_legacy_binding_requires_an_actual_exit_and_an_unambiguous_command_pair() -> None:
    events = []
    for seq, commands, check, verdict in (
        (1, ["check a"], {"ok": False, "exit_code": 1, "content": "Error: a"}, "failed"),
        (2, ["check a"], {"ok": True, "exit_code": None}, "passed"),
        (3, ["check a", "check b"], {"ok": True, "exit_code": 0}, "passed"),
        (4, ["check a"], {"ok": True, "exit_code": 0}, "passed"),
    ):
        event = _completion(seq, verdict)
        event["payload"].update(input_binding={"commands": commands}, checks=[check])
        events.append(event)
    assert build_failure_cluster_cards(events[:3])[0]["unresolved"] is True
    assert build_failure_cluster_cards(events)[0]["unresolved"] is False


def test_a_standalone_command_cannot_clear_a_read_only_verification_failure() -> None:
    failed = _observed("verified", "Error: fixture unavailable", 1, "pytest")
    other_failure = _observed("ordinary", "Error: trace unavailable", 1, "pytest")
    restored = _observed("ordinary-pass", "ok", 0, "pytest")
    events = [_completion(1, "failed", failed)]
    for seq, receipt in enumerate((other_failure, restored), 2):
        events.append({"kind": "tool_observed", "seq": seq, "payload": tool_observation_payload(receipt)})
    assert {card["title"]: card["unresolved"] for card in build_failure_cluster_cards(events)} == {
        "Error: fixture unavailable": True,
        "Error: trace unavailable": False,
    }
