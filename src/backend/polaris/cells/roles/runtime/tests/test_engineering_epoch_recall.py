"""Director recall follows current evidence, preserving the historical extent of each epoch."""

import io
import json

from polaris.cells.roles.runtime.internal.engineering_epoch_kimi import KimiEpochPool
from polaris.cells.roles.runtime.internal.engineering_epoch_runtime import EPOCH_CARD_SCHEMA, facts_for_epoch
from polaris.cells.roles.runtime.internal.engineering_memory_cards import build_context_memory_messages


def _event(seq, kind, **payload):
    return {"seq": seq, "kind": kind, "role": "director", "payload": payload}


def _operation(seq, path, ok=True):
    key = {"tool": "read_file", "call_id": f"read-{seq}", "turn_seq": seq}
    return [
        _event(seq, "tool_started", **key, arguments={"file": path}),
        _event(seq + 1, "tool_observed", **key, ok=ok),
    ]


def _card(seq, ordinal, summary, start, end, *, question=False):
    refs = [f"event:{end}"]
    card = {
        "schema_version": EPOCH_CARD_SCHEMA,
        "id": f"epoch-{ordinal:04d}-{ordinal + 9:04d}",
        "summary": summary,
        "source_seq_range": [start, end],
        "evidence_refs": refs,
    }
    if question:
        card.update(
            exploration_status="proposed",
            exploration={
                "open_question": "Is the fixture readable?",
                "discriminating_check": "Inspect the fixture binding.",
                "evidence_refs": refs,
            },
        )
    return _event(seq, "epoch_card_completed", **card)


def _memory(run, events):
    return "\n".join(m["content"] for m in build_context_memory_messages(run, events))


def test_recall_prefers_an_old_exact_file_in_the_current_decision():
    events = [_event(1, "run_updated", workspace_revision=2)]
    for i, name in enumerate(("src/sum.py", "notes/sum.py", "docs/readme.md", "tools/build.py")):
        seq = 3 * i + 2
        events += _operation(seq, name)
        events.append(_card(seq + 2, 1 + 10 * i, f"Investigated {name}", seq, seq + 1))
    events += _operation(20, "src/sum.py")
    text = _memory({"role": "director", "workspace_revision": 2}, events)
    assert "Investigated src/sum.py" in text and "event:3" in text
    assert "历史" in text and "Investigated notes/sum.py" not in text
    assert text.count("Epoch 记忆") <= 2


def test_grounded_question_survives_only_while_its_specific_evidence_is_current():
    events = [
        _event(1, "run_updated", workspace_revision=2),
        *_operation(2, "fixture.json", False),
        _card(4, 1, "Read of fixture.json failed", 2, 3, question=True),
    ]
    run = {"role": "director", "workspace_revision": 2, "instruction": "Investigate fixture.json"}
    original = _memory(run, events)
    assert "Is the fixture readable?" in original and "event:3" in original
    for changed, journal in (
        ({**run, "workspace_revision": 3}, events),
        (run, [*events, *_operation(5, "fixture.json")]),
    ):
        result = _memory(changed, journal)
        assert "Read of fixture.json failed" in result and "历史" in result
        assert "Is the fixture readable?" not in result


def test_unbound_historical_claim_never_promotes_an_old_probe_or_biases_qa():
    card = _card(8, 1, "Historical host observations", 2, 3, question=True)
    card["payload"].pop("source_seq_range")
    run = {"role": "director", "workspace_revision": 2}
    text = _memory(run, [card])
    assert "Historical host observations" in text and "Is the fixture readable?" not in text
    assert "Epoch 记忆" not in _memory({**run, "role": "qa"}, [card])


def test_verify_phase_recalls_the_historical_verifier_with_bounded_scope():
    check = _event(
        2,
        "verification_completed",
        verification="passed",
        workspace_revision=1,
        verification_commands=["pytest"],
        checks=[{"call_id": "v2", "ok": True, "exit_code": 0}],
    )
    phase = _event(10, "request_prepared", request={"context": {"phase": "verify"}})
    events = [
        check,
        _card(3, 1, "Previous verifier details" + "x" * 4000, 2, 2),
        *_operation(4, "other.py"),
        _card(6, 11, "Other history", 4, 5),
        *_operation(7, "current.py"),
        phase,
    ]
    text = _memory({"role": "director", "workspace_revision": 2}, events)
    assert "Previous verifier details" in text and "event:2" in text
    assert "Other history" not in text and len(text) <= 1200 + 100


def test_identical_browser_probe_is_grounded_until_its_new_observation_arrives():
    args = {"entry": "index.html", "viewport": {"width": 480, "height": 800}}

    def browser(seq, call, kind):
        return _event(
            seq,
            kind,
            turn_seq=3 if call == "old" else 8,
            tool="browser_observe",
            call_id=call,
            **({"arguments": args} if kind == "tool_started" else {"ok": True}),
        )

    events = [
        _event(1, "run_updated", workspace_revision=2),
        browser(3, "old", "tool_started"),
        browser(4, "old", "tool_observed"),
        _card(5, 1, "Observed the narrow page", 3, 4, question=True),
        browser(8, "new", "tool_started"),
    ]
    run = {"role": "director", "workspace_revision": 2}
    assert "Is the fixture readable?" in _memory(run, events)
    assert "Is the fixture readable?" not in _memory(run, [*events, browser(9, "new", "tool_observed")])


def test_exact_failure_surfaces_an_old_investigation_before_a_second_distinct_failure():
    def check(seq, command, symptom):
        return _event(
            seq,
            "verification_completed",
            verification="failed",
            workspace_revision=3,
            verification_commands=[command],
            checks=[{"ok": False, "exit_code": 1, "content": symptom, "call_id": f"v{seq}"}],
        )

    events = [
        check(1, "pytest sum.py", "AssertionError: addition mismatch"),
        _card(2, 1, "Earlier sum investigation", 1, 1),
        *_operation(3, "a.py"),
        _card(5, 11, "Intermediate file", 3, 4),
        *_operation(6, "b.py"),
        _card(8, 21, "Unrelated file", 6, 7),
        check(9, "pytest db.py", "TypeError: connection"),
    ]
    result = _memory({"role": "director", "workspace_revision": 3, "verification": "failed"}, events)
    assert "Earlier sum investigation" in result and 'event:1 "AssertionError: addition mismatch"' in result


def test_devourer_receives_only_the_matched_failed_dispatch_diagnostic_and_argument_presence():
    path = "src/core/simulation.ts"
    missing = "ValueError: write_file requires a content field with the complete file body"
    key = {"turn_seq": 1, "call_id": "w", "tool": "write_file"}
    events = [
        _event(1, "turn_started"),
        _event(2, "tool_started", **key, arguments={"file": path}),
        _event(3, "tool_observed", **key, ok=False, exit_code=None, content=missing + "\nPRIVATE RAW BODY"),
        _event(4, "tool_observed", **{**key, "call_id": "orphan"}, ok=False, content="ValueError: orphan details"),
        _event(
            5,
            "tool_started",
            **{**key, "call_id": "different-role"},
            arguments={"file": path, "content": "PRIVATE RAW BODY"},
        ),
        {
            **_event(
                6, "tool_observed", **{**key, "call_id": "different-role"}, ok=False, content="ValueError: other role"
            ),
            "role": "qa",
        },
    ]
    events.extend(_event(seq, "turn_started") for seq in range(7, 36))
    request = facts_for_epoch({}, events)[0]["candidates"][0]["content"]
    facts = json.loads(request)
    observed = next(row for row in facts["file_operations"] if row["evidence_ref"] == "event:3")
    assert observed["failure"] == missing and observed["content_argument_present"] is False
    assert "PRIVATE RAW BODY" not in request and "orphan details" not in request and "other role" not in request


def test_devourer_prompt_calls_for_a_read_only_discriminator_and_omits_already_answered_questions(monkeypatch):
    captured = {}

    def response(request, **kwargs):
        captured.update(json.loads(request.data))
        return io.BytesIO(b'{"content":[{"type":"text","text":"{}"}]}')

    monkeypatch.setattr("urllib.request.urlopen", response)
    KimiEpochPool(base_url="https://unit.invalid", api_key="test", model="test")._post([])
    system = captured["messages"][0]["content"].lower()
    assert "read-only" in system and "argument" in system and "read_file" in system
    assert "omit exploration" in system and "already" in system
