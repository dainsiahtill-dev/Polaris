"""Real model/continuity/archive regressions for data and complete state preservation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.context.context_os import ContextOSSnapshot
from polaris.kernelone.context.context_os.models_v2 import (
    ArtifactRecordV2,
    DecisionEntryV2,
    StateEntryV2,
    TranscriptEventV2,
    WorkingStateV2,
)
from polaris.kernelone.context.receipt_store import ReceiptStore
from polaris.kernelone.context.session_continuity import (
    SessionContinuityEngine,
    SessionContinuityRequest,
    history_pairs_to_messages,
)
from polaris.kernelone.context.text_normalization import normalize_quoted_text
from pydantic import BaseModel, ValidationError

PARTIAL = '{"plan":"partial  <html>\u00a0 '
TEXT_CASES = [
    (PARTIAL, PARTIAL),
    (
        '  {"value":"雪  \u00a0\u2003\u2028 <html> <tool_call>"} \t',
        '{"value":"雪  \u00a0\u2003\u2028 <html> <tool_call>"}',
    ),
    (' \n {"value":"escaped \\"quote\\" and \\\\ path  "}\u2003', '{"value":"escaped \\"quote\\" and \\\\ path  "}'),
    (' \t {"value":"open\u2028 \t', '{"value":"open\u2028 \t'),
    ('  {"value":"dangling' + "\\", '{"value":"dangling' + "\\"),
    ("  prose  \n\t stays   spaced\u00a0 ", "prose  \n\t stays   spaced"),
    (' \n <thinking>  outside  </thinking> "data  \u00a0" \t', '<thinking>  outside  </thinking> "data  \u00a0"'),
    (" \n\t\u00a0 ", ""),
]


@pytest.fixture
def private_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # Avoid the parent conftest's repository-local tmp_path override.
    return tmp_path_factory.mktemp("model-preservation")


@pytest.mark.parametrize(("raw", "expected"), TEXT_CASES)
@pytest.mark.parametrize("field", ["state", "transcript", "decision", "artifact"])
def test_data_field_preserves_quoted_tail_and_model_prose_policy(raw: str, expected: str, field: str) -> None:
    if field == "state":
        direct = StateEntryV2(entry_id="entry", value=raw).value
        restored = StateEntryV2.from_mapping({"entry_id": "entry", "value": raw})
        assert restored is not None
        indirect = restored.value
    elif field == "transcript":
        direct = TranscriptEventV2(event_id="event", sequence=1, content=raw).content
        indirect = TranscriptEventV2.model_validate({"event_id": "event", "sequence": 1, "content": raw}).content
    elif field == "decision":
        direct = DecisionEntryV2(decision_id="decision", summary=raw).summary
        decision = DecisionEntryV2.from_mapping({"entry_id": "decision", "value": raw})
        assert decision is not None
        indirect = decision.summary
    else:
        direct = ArtifactRecordV2(artifact_id="artifact", content=raw).content
        indirect = ArtifactRecordV2.model_validate({"artifact_id": "artifact", "content": raw}).content
    assert direct == expected
    assert indirect == expected


def test_shared_normalizer_defaults_still_fold_prose_and_optional_tags() -> None:
    raw = ' \n <thinking>  outside \t </thinking> {"x":"data  \u00a0"} '
    assert normalize_quoted_text(raw) == '<thinking> outside </thinking> {"x":"data  \u00a0"}'
    assert normalize_quoted_text(raw, strip_external_tags=True) == 'outside {"x":"data  \u00a0"}'


def test_identity_and_provenance_keep_existing_validation() -> None:
    state = StateEntryV2(
        entry_id="  entry \t",
        path=" path ",
        value=PARTIAL,
        source_turns=(" turn ",),
        updated_at=" date ",
        supersedes=" old ",
    )
    assert (state.entry_id, state.path, state.source_turns, state.updated_at, state.supersedes) == (
        "entry",
        "path",
        ("turn",),
        "date",
        "old",
    )
    event = TranscriptEventV2(
        event_id=" event ",
        sequence="7",
        role=" pm ",
        content=PARTIAL,
        content_ref_hash=" hash ",
        metadata={" trace ": PARTIAL},
    )
    assert (event.event_id, event.sequence, event.role, event.content_ref_hash) == ("event", 7, "pm", "hash")
    assert dict(event.metadata) == {"trace": PARTIAL}
    invalid_cases: list[tuple[type[BaseModel], dict[str, Any]]] = [
        (StateEntryV2, {"entry_id": " \u00a0 "}),
        (StateEntryV2, {"entry_id": "entry", "confidence": 1.01}),
        (StateEntryV2, {"entry_id": "entry", "value": {"plan": "data"}}),
        (TranscriptEventV2, {"event_id": " \t ", "sequence": 0}),
        (TranscriptEventV2, {"event_id": "event", "sequence": -1}),
        (TranscriptEventV2, {"event_id": "event", "sequence": 1, "content": ["data"]}),
        (DecisionEntryV2, {"decision_id": " \t "}),
        (ArtifactRecordV2, {"artifact_id": " \n "}),
    ]
    for constructor, kwargs in invalid_cases:
        with pytest.raises(ValidationError):
            constructor(**kwargs)


def test_model_validation_leaves_caller_metadata_unchanged() -> None:
    payload = {
        "event_id": "event",
        "sequence": 1,
        "content": PARTIAL,
        "metadata": {"trace_id": "trace", "nested": {"values": ["x", PARTIAL]}},
    }
    before = copy.deepcopy(payload)
    event = TranscriptEventV2.model_validate(payload)
    assert payload == before
    assert isinstance(payload["metadata"], dict)
    assert dict(event.metadata) == before["metadata"]


def complete_working_state_payload() -> dict[str, Any]:
    def entry(name: str, index: int) -> dict[str, Any]:
        return {
            "entry_id": f"{name}-{index}",
            "path": f"state.{name}",
            "value": PARTIAL,
            "source_turns": ["turn-2", "turn-1"],
            "confidence": 0.75,
            "updated_at": "2026-10-11T01:00:00Z",
            "supersedes": f"old-{name}-{index}",
        }

    def entries(name: str) -> list[dict[str, Any]]:
        return [entry(name, 2), entry(name, 1)]

    return {
        "user_profile": {name: entries(name) for name in ["preferences", "style", "persistent_facts"]},
        "task_state": {
            "current_goal": entry("goal", 1),
            **{name: entries(name) for name in ["accepted_plan", "open_loops", "blocked_on", "deliverables"]},
        },
        "decision_log": [
            {
                "decision_id": f"decision-{i}",
                "summary": PARTIAL,
                "value": PARTIAL,
                "source_turns": ["turn-2", "turn-1"],
                "updated_at": "2026-10-11T01:00:00Z",
                "kind": "decision",
                "supersedes": f"old-decision-{i}",
                "basis_refs": ["ref-2", "ref-1"],
            }
            for i in [2, 1]
        ],
        "active_entities": entries("entity"),
        "active_artifacts": ["artifact-2", "artifact-1"],
        "temporal_facts": entries("temporal"),
        "state_history": entries("history"),
    }


@pytest.mark.parametrize("path", ["canonical", "snapshot_mapping", "serialized_roundtrip"])
def test_all_working_state_fields_survive_without_input_mutation(path: str, private_workspace: Path) -> None:
    expected = complete_working_state_payload()
    before = copy.deepcopy(expected)
    if path == "canonical":
        restored = WorkingStateV2.from_mapping(expected)
    elif path == "snapshot_mapping":
        snapshot = ContextOSSnapshot.from_mapping({"working_state": expected})
        assert snapshot is not None
        restored = snapshot.working_state
    else:
        original = ContextOSSnapshot(working_state=WorkingStateV2.model_validate(expected))
        file = private_workspace / "snapshot.json"
        file.write_text(json.dumps(original.to_dict(), ensure_ascii=False), encoding="utf-8")
        serialized = json.loads(file.read_text(encoding="utf-8"))
        serialized_before = copy.deepcopy(serialized)
        snapshot = ContextOSSnapshot.from_mapping(serialized)
        assert snapshot is not None
        assert serialized == serialized_before
        restored = snapshot.working_state
    assert expected == before
    assert restored.to_dict() == before


def test_snapshot_restoration_leaves_nested_metadata_and_input_unchanged() -> None:
    payload: dict[str, Any] = {
        "working_state": complete_working_state_payload(),
        "transcript_log": [
            {
                "event_id": "event",
                "sequence": 1,
                "content": PARTIAL,
                "metadata": {"trace_id": "trace", "nested": {"data": [PARTIAL]}},
            }
        ],
    }
    before = copy.deepcopy(payload)
    snapshot = ContextOSSnapshot.from_mapping(payload)
    assert snapshot is not None
    assert payload == before
    assert dict(snapshot.transcript_log[0].metadata) == before["transcript_log"][0]["metadata"]


def test_serialized_transcript_content_reference_provenance_survives_restoration() -> None:
    event = TranscriptEventV2(
        event_id="referenced",
        sequence=7,
        content=PARTIAL,
        source_turns=("turn-7",),
        metadata={"trace_id": "trace"},
        content_ref_hash="123456789abcdef012345678",
        content_ref_size=12580,
        content_ref_mime="application/json",
        content_ref_encoding="UTF-8",
    )
    payload = ContextOSSnapshot(transcript_log=(event,)).to_dict()
    before = copy.deepcopy(payload)
    assert payload["transcript_log"][0]["content_ref"] == {
        "hash": "123456789abcdef012345678",
        "size": 12580,
        "mime": "application/json",
        "encoding": "UTF-8",
    }
    restored = ContextOSSnapshot.from_mapping(payload)
    assert restored is not None
    assert payload == before
    assert restored.transcript_log[0] == event


def test_missing_and_legacy_working_state_fields_keep_canonical_contract() -> None:
    for payload in [{}, {"working_state": None}, {"working_state": {}}]:
        snapshot = ContextOSSnapshot.from_mapping(payload)
        assert snapshot is not None
        assert snapshot.working_state == WorkingStateV2()
    legacy = {
        "working_state": {
            "decision_log": [{"entry_id": "legacy", "value": PARTIAL}],
            "active_entities": [None, "legacy-invalid", {"entry_id": "valid", "value": PARTIAL}],
        }
    }
    before = copy.deepcopy(legacy)
    snapshot = ContextOSSnapshot.from_mapping(legacy)
    assert snapshot is not None
    assert snapshot.working_state.decision_log[0].decision_id == "legacy"
    assert snapshot.working_state.decision_log[0].summary == PARTIAL
    assert [item.entry_id for item in snapshot.working_state.active_entities] == ["valid"]
    assert legacy == before


@pytest.mark.parametrize(
    "collection", ["accepted_plan", "deliverables", "active_entities", "temporal_facts", "state_history"]
)
def test_invalid_declared_entries_are_rejected_by_snapshot(collection: str) -> None:
    state: dict[str, Any] = {}
    target = state.setdefault("task_state", {}) if collection in ["accepted_plan", "deliverables"] else state
    target[collection] = [{"entry_id": "entry", "value": PARTIAL, "confidence": 2.0}]
    with pytest.raises(ValidationError):
        ContextOSSnapshot.from_mapping({"working_state": state})


@pytest.mark.parametrize("input_kind", ["turn_events", "messages", "history"])
@pytest.mark.asyncio
async def test_real_continuity_retains_incomplete_data_without_approval(
    input_kind: str,
    private_workspace: Path,
) -> None:
    event = {
        "role": "assistant",
        "content": PARTIAL,
        "sequence": 7,
        "event_id": "partial",
        "source_turns": ["turn-7"],
        "metadata": {"trace_id": "trace"},
    }
    config = {"session_turn_events": [event]}
    before = copy.deepcopy(config)
    messages = tuple(history_pairs_to_messages([("assistant", PARTIAL)])) if input_kind == "history" else (event,)
    projection = await SessionContinuityEngine().project(
        SessionContinuityRequest(
            session_id="private-partial",
            role="pm",
            workspace=str(private_workspace),
            turn_events=(event,) if input_kind == "turn_events" else (),
            messages=messages if input_kind != "turn_events" else (),
            session_context_config=config,
        )
    )
    assert config == before
    assert projection.context_os_projection is not None
    snapshot = projection.context_os_projection.snapshot
    assert snapshot.transcript_log[0].content == PARTIAL
    assert any(item.value == PARTIAL for item in snapshot.working_state.task_state.accepted_plan)
    persisted = projection.persisted_context_config["state_first_context_os"]
    assert projection.persisted_context_config["session_turn_events"] == before["session_turn_events"]
    restored = ContextOSSnapshot.from_mapping(persisted)
    assert restored is not None
    assert restored.transcript_log[0].content == PARTIAL
    assert any(item.value == PARTIAL for item in restored.working_state.task_state.accepted_plan)
    with pytest.raises(json.JSONDecodeError):
        json.loads(restored.transcript_log[0].content)
    if input_kind != "history":
        assert restored.transcript_log[0].event_id == "partial"
        assert restored.transcript_log[0].source_turns == ("turn-7",)
        assert dict(restored.transcript_log[0].metadata)["trace_id"] == "trace"


@pytest.mark.parametrize("auto_archive", [False, True])
def test_large_private_receipt_archive_restores_exact_data_and_state(
    auto_archive: bool,
    private_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(private_workspace)
    content = '{"payload":"' + "雪  \u00a0 <tool_call> " * 500 + "\\"
    original = ContextOSSnapshot(
        working_state=WorkingStateV2.model_validate(complete_working_state_payload()),
        transcript_log=(
            TranscriptEventV2(
                event_id="large", sequence=7, content=content, source_turns=("turn-7",), metadata={"trace_id": "trace"}
            ),
        ),
    )
    store = ReceiptStore(workspace=str(private_workspace))
    archive = original.to_dict() if auto_archive else original.to_dict(receipt_store=store)
    if not auto_archive:
        assert store.get("evt_large") == content
        archive["_receipt_store_export"] = store.export_receipts()
    assert len(content) > 5000
    assert archive["transcript_log"][0]["content"] == "<receipt_ref:evt_large>"
    assert archive["_receipt_store_export"]["evt_large"] == content
    file = private_workspace / "archive.json"
    file.write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
    payload = json.loads(file.read_text(encoding="utf-8"))
    before = copy.deepcopy(payload)
    restored_store = ReceiptStore(workspace=str(private_workspace / "restore"))
    restored_store.import_receipts(payload["_receipt_store_export"])
    assert restored_store.get("evt_large") == content
    restored = ContextOSSnapshot.from_mapping(payload)
    assert restored is not None
    assert payload == before
    assert restored.transcript_log[0].content == content
    assert restored.transcript_log[0].event_id == "large"
    assert restored.transcript_log[0].source_turns == ("turn-7",)
    assert dict(restored.transcript_log[0].metadata) == {"trace_id": "trace"}
    assert restored.working_state.to_dict() == complete_working_state_payload()
    with pytest.raises(json.JSONDecodeError):
        json.loads(restored.transcript_log[0].content)
