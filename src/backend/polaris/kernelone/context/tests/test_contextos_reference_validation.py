"""Snapshot reference failures must never return events stripped of provenance."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.context.context_os import ContextOSSnapshot, StateFirstContextOS
from polaris.kernelone.context.context_os.models_v2 import TranscriptEventV2
from polaris.kernelone.context.receipt_store import ReceiptStore
from polaris.kernelone.context.session_continuity import SessionContinuityEngine, SessionContinuityRequest
from pydantic import ValidationError

INVALID_FIELDS = [
    ("size", "bad-integer"),
    ("hash", {"invalid": True}),
    ("mime", ["text/plain"]),
    ("encoding", None),
]


def reference() -> dict[str, Any]:
    return {"hash": "b" * 24, "size": 4, "mime": "text/plain", "encoding": "utf-8"}


def event() -> dict[str, Any]:
    return {
        "event_id": "reference",
        "sequence": 5,
        "role": "assistant",
        "kind": "message",
        "route": "active_window",
        "content": "data",
        "source_turns": ["raw-turn-5"],
        "artifact_id": "artifact-5",
        "created_at": "2026-10-11T08:00:00Z",
        "metadata": {"trace_id": "valid-trace", "nested": {"keep": ["雪"]}},
    }


@pytest.fixture
def private_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("reference-validation")


@pytest.mark.parametrize(("field", "invalid"), INVALID_FIELDS)
@pytest.mark.parametrize("representation", ["nested", "flat"])
def test_invalid_reference_rejects_snapshot_without_erasing_evidence(
    field: str, invalid: Any, representation: str
) -> None:
    raw = event()
    ref = reference()
    ref[field] = invalid
    if representation == "nested":
        raw["content_ref"] = ref
    else:
        raw.update({f"content_ref_{key}": value for key, value in ref.items()})
    payload = {"transcript_log": [raw]}
    before = copy.deepcopy(payload)
    with pytest.raises(ValidationError) as caught:
        ContextOSSnapshot.from_mapping(payload)
    assert any(error["loc"] == (f"content_ref_{field}",) for error in caught.value.errors())
    assert payload == before
    assert raw["metadata"] == {"trace_id": "valid-trace", "nested": {"keep": ["雪"]}}


@pytest.mark.parametrize(("field", "invalid"), INVALID_FIELDS)
def test_invalid_winning_flat_reference_is_not_replaced_by_valid_nested_reference(field: str, invalid: Any) -> None:
    raw = event()
    raw["content_ref"] = reference()
    raw[f"content_ref_{field}"] = invalid
    payload = {"transcript_log": [raw]}
    before = copy.deepcopy(payload)
    with pytest.raises(ValidationError):
        ContextOSSnapshot.from_mapping(payload)
    assert payload == before


@pytest.mark.parametrize("invalid", [None, "not-a-mapping", ["hash", "size"], 4])
def test_supplied_invalid_reference_container_is_not_silently_ignored(invalid: Any) -> None:
    raw = event()
    raw["content_ref"] = invalid
    payload = {"transcript_log": [raw]}
    before = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="content_ref"):
        ContextOSSnapshot.from_mapping(payload)
    assert payload == before


@pytest.mark.parametrize("position", [0, 1, 2])
def test_invalid_event_prevents_partial_success_without_mutating_other_events(position: int) -> None:
    events = [
        {**event(), "event_id": f"event-{index}", "sequence": index, "content_ref": reference()} for index in range(3)
    ]
    events[position]["content_ref"]["size"] = "bad-integer"
    payload = {"transcript_log": events, "working_state": {"active_artifacts": ["keep-state"]}}
    before = copy.deepcopy(payload)
    with pytest.raises(ValidationError):
        ContextOSSnapshot.from_mapping(payload)
    assert payload == before


@pytest.mark.parametrize("representation", ["absent", "empty", "partial", "nested", "flat"])
def test_valid_legacy_reference_forms_keep_all_event_provenance_and_caller_state(representation: str) -> None:
    raw = event()
    if representation == "empty":
        raw["content_ref"] = {}
    elif representation == "partial":
        raw["content_ref"] = {"hash": "b" * 24}
    elif representation == "nested":
        raw["content_ref"] = reference()
    elif representation == "flat":
        raw.update({f"content_ref_{key}": value for key, value in reference().items()})
    payload = {"transcript_log": [raw], "working_state": {"active_artifacts": ["keep-state"]}}
    before = copy.deepcopy(payload)
    for _ in range(2):
        snapshot = ContextOSSnapshot.from_mapping(payload)
        assert snapshot is not None
        restored = snapshot.transcript_log[0]
        assert restored.event_id == "reference"
        assert restored.sequence == 5
        assert restored.content == "data"
        assert restored.source_turns == ("raw-turn-5",)
        assert restored.artifact_id == "artifact-5"
        assert restored.created_at == "2026-10-11T08:00:00Z"
        assert (restored.role, restored.kind, restored.route) == ("assistant", "message", "active_window")
        assert dict(restored.metadata) == {"trace_id": "valid-trace", "nested": {"keep": ["雪"]}}
        assert snapshot.working_state.active_artifacts == ("keep-state",)
        assert restored.content_ref_hash == ("" if representation in {"absent", "empty"} else "b" * 24)
        assert restored.content_ref_size == (4 if representation in {"nested", "flat"} else 0)
        assert restored.content_ref_mime == ("text/plain" if representation in {"nested", "flat"} else "")
        assert restored.content_ref_encoding == "utf-8"
        assert payload == before


@pytest.mark.parametrize(("field", "invalid"), INVALID_FIELDS)
def test_valid_flat_reference_keeps_precedence_over_shadowed_nested_field(field: str, invalid: Any) -> None:
    raw = event()
    ref = reference()
    ref[field] = invalid
    raw["content_ref"] = ref
    raw[f"content_ref_{field}"] = reference()[field]
    payload = {"transcript_log": [raw]}
    before = copy.deepcopy(payload)
    snapshot = ContextOSSnapshot.from_mapping(payload)
    assert snapshot is not None
    restored = snapshot.transcript_log[0]
    assert (
        restored.content_ref_hash,
        restored.content_ref_size,
        restored.content_ref_mime,
        restored.content_ref_encoding,
    ) == ("b" * 24, 4, "text/plain", "utf-8")
    assert dict(restored.metadata) == before["transcript_log"][0]["metadata"]
    assert payload == before


def test_explicit_empty_flat_hash_keeps_precedence_and_canonical_size_coercion() -> None:
    raw = event()
    raw.update(content_ref=reference(), content_ref_hash="", content_ref_size="7")
    payload = {"transcript_log": [raw]}
    before = copy.deepcopy(payload)
    snapshot = ContextOSSnapshot.from_mapping(payload)
    assert snapshot is not None
    restored = snapshot.transcript_log[0]
    assert (restored.content_ref_hash, restored.content_ref_size) == ("", 7)
    assert restored.content_ref_mime == "text/plain"
    assert payload == before


@pytest.mark.parametrize("auto_archive", [False, True])
def test_private_archived_content_restores_reference_provenance_and_state(
    auto_archive: bool, private_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(private_workspace)
    content = '{"payload":"' + "雪  \u00a0 <tool_call> " * 500 + "\\"
    raw = event()
    raw.update(content=content, **{f"content_ref_{key}": value for key, value in reference().items()})
    original = ContextOSSnapshot(transcript_log=(TranscriptEventV2.model_validate(raw),))
    store = ReceiptStore(workspace=str(private_workspace))
    archive = original.to_dict() if auto_archive else original.to_dict(receipt_store=store)
    if not auto_archive:
        assert store.get("evt_reference") == content
        archive["_receipt_store_export"] = store.export_receipts()
    assert len(content) > 5000
    assert archive["transcript_log"][0]["content"] == "<receipt_ref:evt_reference>"
    path = private_workspace / "archive.json"
    path.write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
    persisted = json.loads(path.read_text(encoding="utf-8"))
    before = copy.deepcopy(persisted)
    restored_store = ReceiptStore(workspace=str(private_workspace / "restored"))
    restored_store.import_receipts(persisted["_receipt_store_export"])
    assert restored_store.get("evt_reference") == content
    snapshot = ContextOSSnapshot.from_mapping(persisted)
    assert snapshot is not None
    restored = snapshot.transcript_log[0]
    assert restored.content == content
    assert restored.content_ref_hash == "b" * 24
    assert restored.content_ref_size == 4
    assert restored.content_ref_mime == "text/plain"
    assert restored.content_ref_encoding == "utf-8"
    assert dict(restored.metadata) == raw["metadata"]
    assert restored.source_turns == ("raw-turn-5",)
    assert snapshot.working_state.to_dict() == original.working_state.to_dict()
    assert persisted == before
    persisted["transcript_log"][0]["content_ref"]["size"] = "bad-integer"
    corrupted = copy.deepcopy(persisted)
    with pytest.raises(ValidationError):
        ContextOSSnapshot.from_mapping(persisted)
    assert persisted == corrupted
    assert restored_store.get("evt_reference") == content


@pytest.mark.asyncio
async def test_real_continuity_propagates_reference_validation_without_replacing_snapshot(
    private_workspace: Path,
) -> None:
    raw = event()
    raw["content_ref"] = {**reference(), "size": "bad-integer"}
    config = {"state_first_context_os": {"transcript_log": [raw]}}
    before = copy.deepcopy(config)
    with pytest.raises(ValidationError):
        await SessionContinuityEngine().project(
            SessionContinuityRequest(
                session_id="private-reference",
                role="pm",
                workspace=str(private_workspace),
                messages=({"role": "user", "content": "Continue"},),
                session_context_config=config,
            )
        )
    assert config == before


def test_real_memory_consumer_propagates_reference_validation_before_state_projection() -> None:
    raw = event()
    raw["content_ref"] = {**reference(), "size": "bad-integer"}
    payload = {"transcript_log": [raw]}
    before = copy.deepcopy(payload)
    with pytest.raises(ValidationError):
        StateFirstContextOS().get_state(payload, "run_card")
    assert payload == before
