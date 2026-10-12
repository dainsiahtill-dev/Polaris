"""Real SessionContinuity admission, no-input retention and persisted change tests."""

from __future__ import annotations

import copy
import json
from collections import UserDict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from polaris.infrastructure.llm.adapters.stub_embedding_adapter import StubEmbeddingAdapter
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.context.context_os import ContextOSInvariantViolationError, ContextOSSnapshot
from polaris.kernelone.context.session_continuity import (
    SessionContinuityEngine,
    SessionContinuityPack,
    SessionContinuityRequest,
)
from polaris.kernelone.fs import set_default_adapter
from polaris.kernelone.llm.embedding import set_default_embedding_port
from pydantic import ValidationError

KEY = "state_first_context_os"
PARTIAL_PLAN = '{"plan":"keep  \u3000 <tool_call>  '


def _snapshot() -> dict[str, Any]:
    """Hand-authored persisted data, including snapshot-only state and attribution."""
    entry = {
        "entry_id": "plan-private",
        "path": "task_state.accepted_plan",
        "value": PARTIAL_PLAN,
        "source_turns": ["source-private"],
        "confidence": 0.9,
        "updated_at": "2026-10-11T09:00:00Z",
        "supersedes": None,
    }
    return {
        "version": 1,
        "mode": "state_first_context_os_v1",
        "adapter_id": "generic",
        "transcript_log": [
            {
                "event_id": "prior-private",
                "sequence": 0,
                "role": "assistant",
                "kind": "assistant_turn",
                "route": "archive",
                "content": PARTIAL_PLAN,
                "source_turns": ["source-private"],
                "artifact_id": None,
                "created_at": "2026-10-11T09:00:00Z",
                "metadata": {"trace_id": "private", "nested": {"keep": ["雪", PARTIAL_PLAN]}},
            }
        ],
        "working_state": {
            "user_profile": {
                "preferences": [dict(entry, path="user_profile.preferences")],
                "style": [],
                "persistent_facts": [],
            },
            "task_state": {
                "current_goal": None,
                "accepted_plan": [entry],
                "open_loops": [],
                "blocked_on": [],
                "deliverables": [],
            },
            "decision_log": [],
            "active_entities": [],
            "active_artifacts": [],
            "temporal_facts": [],
            "state_history": [entry],
        },
        "artifact_store": [],
        "episode_store": [],
        "budget_plan": None,
        "pending_followup": None,
        "updated_at": "2026-10-11T09:00:00Z",
    }


def _request(
    tmp_path: Path,
    config: dict[str, Any] | None,
    *,
    messages: tuple[dict[str, Any], ...] = (),
    turn_events: tuple[dict[str, Any], ...] = (),
    incoming: dict[str, Any] | None = None,
    history_limit: int | None = None,
) -> SessionContinuityRequest:
    return SessionContinuityRequest(
        session_id="pol100-private",
        role="pm",
        workspace=str(tmp_path),
        messages=messages,
        turn_events=turn_events,
        session_context_config=config,
        incoming_context=incoming,
        history_limit=history_limit,
    )


@pytest_asyncio.fixture
async def engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncIterator[SessionContinuityEngine]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / ".polaris/runtime"))
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    monkeypatch.setenv("KERNELONE_CONTEXT_OS_ENABLED", "true")
    set_default_adapter(LocalFileSystemAdapter())
    set_default_embedding_port(StubEmbeddingAdapter())
    projector = SessionContinuityEngine()
    try:
        yield projector
    finally:
        for runtime in projector._context_os_cache.values():
            await runtime.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["not-a-snapshot", "", 0, 1, False, True, 1.5, [], [1], (), (1,), b"", b"snapshot", set()]
)
@pytest.mark.parametrize("with_message", [False, True])
async def test_rejects_supplied_malformed_snapshot_without_replacing_caller_data(
    engine: SessionContinuityEngine, tmp_path: Path, invalid: Any, with_message: bool
) -> None:
    config = {KEY: invalid, "unrelated": {"keep": ["雪"]}}
    before = copy.deepcopy(config)
    messages = ({"role": "user", "content": "Continue"},) if with_message else ()
    try:
        with pytest.raises(ContextOSInvariantViolationError):
            await engine.project(_request(tmp_path, config, messages=messages))
    finally:
        assert config == before


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [None, {}, {KEY: None}])
@pytest.mark.parametrize("with_message", [False, True])
async def test_absence_and_nullable_absence_keep_canonical_behavior(
    engine: SessionContinuityEngine, tmp_path: Path, config: dict[str, Any] | None, with_message: bool
) -> None:
    before = copy.deepcopy(config)
    messages = ({"role": "user", "content": "Continue"},) if with_message else ()
    result = await engine.project(_request(tmp_path, config, messages=messages))
    assert config == before
    assert result.changed is with_message
    if with_message:
        assert result.context_os_projection is not None
        assert len(result.context_os_projection.snapshot.transcript_log) == 1
    else:
        assert result.persisted_context_config == (config or {})
        assert result.context_os_projection is None


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [{}, _snapshot(), UserDict(_snapshot())])
@pytest.mark.parametrize("messages", [(), ({"role": "user", "content": "   "},)])
async def test_enabled_no_input_preserves_supplied_snapshot_exactly(
    engine: SessionContinuityEngine, tmp_path: Path, snapshot: Any, messages: tuple[dict[str, Any], ...]
) -> None:
    config = {KEY: snapshot, "unrelated": {"keep": ["雪"]}}
    incoming = {"role": "discard-reserved", "prompt_only": {"nested": [1]}}
    before, incoming_before = copy.deepcopy(config), copy.deepcopy(incoming)
    result = await engine.project(_request(tmp_path, config, messages=messages, incoming=incoming))
    assert config == before and incoming == incoming_before
    assert result.persisted_context_config == before
    assert result.changed is False
    assert result.context_os_projection is None
    assert KEY not in result.prompt_context
    assert "role" not in result.prompt_context
    assert result.prompt_context["prompt_only"] == incoming["prompt_only"]
    assert "prompt_only" not in result.persisted_context_config


@pytest.mark.asyncio
async def test_no_input_retains_actual_private_archive_export_and_restores_literal(
    engine: SessionContinuityEngine, tmp_path: Path
) -> None:
    content = json.dumps({"plan": [f"雪 {n}  <tool_call>   " for n in range(400)]}, ensure_ascii=False)
    payload = _snapshot()
    payload["transcript_log"][0]["content"] = content
    snapshot = ContextOSSnapshot.from_mapping(payload)
    assert snapshot is not None
    archived = snapshot.to_dict()
    assert archived["_receipt_store_export"]["evt_prior-private"] == content
    assert archived["transcript_log"][0]["content"] == "<receipt_ref:evt_prior-private>"
    config = {KEY: archived}
    before = copy.deepcopy(config)
    result = await engine.project(_request(tmp_path, config))
    assert result.persisted_context_config == before
    restored = ContextOSSnapshot.from_mapping(result.persisted_context_config[KEY])
    assert restored is not None
    assert restored.transcript_log[0].content == content
    assert restored.transcript_log[0].source_turns == ("source-private",)
    assert dict(restored.transcript_log[0].metadata)["nested"] == {"keep": ["雪", PARTIAL_PLAN]}
    assert config == before and result.changed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("with_message", [False, True])
async def test_disabled_state_clears_valid_snapshot_and_preserves_unrelated_keys(
    engine: SessionContinuityEngine, tmp_path: Path, with_message: bool
) -> None:
    config = {KEY: _snapshot(), "unrelated": {"keep": ["雪"]}}
    before = copy.deepcopy(config)
    messages = ({"role": "user", "content": "Continue"},) if with_message else ()
    result = await engine.project(
        _request(tmp_path, config, messages=messages, incoming={"state_first_context_os_enabled": False})
    )
    assert result.persisted_context_config == {"unrelated": {"keep": ["雪"]}}
    assert result.context_os_projection is None and KEY not in result.prompt_context
    assert result.changed is True and config == before


@pytest.mark.asyncio
async def test_disabled_state_does_not_hide_malformed_supplied_snapshot(
    engine: SessionContinuityEngine, tmp_path: Path
) -> None:
    config = {KEY: False}
    with pytest.raises(ContextOSInvariantViolationError):
        await engine.project(_request(tmp_path, config, incoming={"context_os_enabled": False}))
    assert config == {KEY: False}


@pytest.mark.asyncio
async def test_disabled_absent_snapshot_incremental_pack_is_not_a_persisted_change(
    engine: SessionContinuityEngine, tmp_path: Path
) -> None:
    result = await engine.project(
        _request(
            tmp_path,
            {"keep": "value"},
            messages=({"role": "user", "content": "Continue fixing the bug"},),
            incoming={"context_os_enabled": False},
        )
    )
    assert result.continuity_pack is not None
    assert result.persisted_context_config == {"keep": "value"}
    assert result.changed is False


@pytest.mark.asyncio
async def test_valid_message_projection_preserves_grounded_plan_and_event_attribution(
    engine: SessionContinuityEngine, tmp_path: Path
) -> None:
    config = {KEY: _snapshot(), "keep": {"nested": [1]}}
    events = (
        {
            "event_id": "turn-private",
            "sequence": 1,
            "role": "user",
            "content": "Continue",
            "source_turns": ["source-new"],
            "metadata": {"trace_id": "new-private"},
        },
    )
    messages = ({"sequence": 2, "role": "user", "content": "Ignored lower authority message"},)
    before = copy.deepcopy((config, events, messages))
    result = await engine.project(_request(tmp_path, config, messages=messages, turn_events=events))
    assert result.changed is True and result.context_os_projection is not None
    snapshot = result.context_os_projection.snapshot
    assert [event.event_id for event in snapshot.transcript_log] == ["prior-private", "turn-private"]
    prior, new = snapshot.transcript_log
    assert prior.content == PARTIAL_PLAN and prior.source_turns == ("source-private",)
    assert dict(prior.metadata)["nested"] == {"keep": ["雪", PARTIAL_PLAN]}
    assert new.content == "Continue" and new.source_turns == ("source-new",)
    assert dict(new.metadata)["trace_id"] == "new-private"
    assert any(
        entry.value == PARTIAL_PLAN and entry.source_turns == ("source-private",)
        for entry in snapshot.working_state.task_state.accepted_plan
    )
    assert result.persisted_context_config["keep"] == {"nested": [1]}
    assert (config, events, messages) == before


@pytest.mark.asyncio
async def test_nonempty_but_normalized_empty_turn_events_do_not_fall_back_to_messages(
    engine: SessionContinuityEngine, tmp_path: Path
) -> None:
    config = {KEY: _snapshot()}
    result = await engine.project(
        _request(
            tmp_path,
            config,
            messages=({"role": "user", "content": "Continue"},),
            turn_events=({"role": "user", "content": " "},),
        )
    )
    assert result.recent_messages == () and result.context_os_projection is None
    assert result.persisted_context_config == config and result.changed is False


@pytest.mark.asyncio
async def test_legacy_index_rehydration_preserves_raw_event_input_and_noop_config(
    engine: SessionContinuityEngine, tmp_path: Path
) -> None:
    payload = _snapshot()
    raw_event = payload.pop("transcript_log")[0]
    payload["transcript_log_index"] = [{"event_id": "prior-private", "role": "assistant"}]
    config = {KEY: payload, "session_turn_events": [raw_event]}
    before = copy.deepcopy(config)
    noop = await engine.project(_request(tmp_path, config))
    assert noop.persisted_context_config == before and noop.changed is False
    result = await engine.project(
        _request(tmp_path, config, messages=({"role": "user", "sequence": 1, "content": "Continue"},))
    )
    assert result.context_os_projection is not None
    restored = result.context_os_projection.snapshot.transcript_log[0]
    assert restored.event_id == "prior-private" and restored.content == PARTIAL_PLAN
    assert restored.source_turns == ("source-private",)
    assert config == before and result.changed is True


@pytest.mark.asyncio
async def test_invalid_nested_reference_still_propagates_canonical_error(
    engine: SessionContinuityEngine, tmp_path: Path
) -> None:
    payload = _snapshot()
    payload["transcript_log"][0]["content_ref"] = {"size": "bad-integer"}
    config = {KEY: payload}
    before = copy.deepcopy(config)
    with pytest.raises(ValidationError) as error:
        await engine.project(_request(tmp_path, config))
    assert error.value.errors()[0]["loc"] == ("content_ref_size",)
    assert config == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_pack, expected_changed",
    [
        (SessionContinuityPack(summary="Keep engineering facts.").to_dict(), False),
        ({"summary": "Keep engineering facts."}, True),
    ],
)
async def test_no_input_pack_preservation_reports_only_real_config_changes(
    engine: SessionContinuityEngine, tmp_path: Path, raw_pack: dict[str, Any], expected_changed: bool
) -> None:
    config = {KEY: _snapshot(), "session_continuity": raw_pack, "keep": {"nested": [1]}}
    before = copy.deepcopy(config)
    result = await engine.project(_request(tmp_path, config))
    assert result.persisted_context_config[KEY] == before[KEY]
    assert result.continuity_pack is not None and result.continuity_pack.summary == "Keep engineering facts."
    assert result.prompt_context["session_continuity"]["summary"] == "Keep engineering facts."
    assert result.changed is expected_changed
    assert result.persisted_context_config["keep"] == {"nested": [1]} and config == before


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_pack", [None, {"summary": "", "compacted_through_seq": 0}])
async def test_older_messages_build_or_rebuild_persisted_pack_with_accurate_changed(
    engine: SessionContinuityEngine, tmp_path: Path, raw_pack: dict[str, Any] | None
) -> None:
    config: dict[str, Any] = {"keep": "value"}
    if raw_pack is not None:
        config["session_continuity"] = raw_pack
    messages = (
        {"sequence": 0, "role": "system", "content": "hello"},
        {"sequence": 1, "role": "user", "content": "Continue fixing the bug"},
    )
    if raw_pack is None:
        messages = ({"sequence": 0, "role": "user", "content": "Please fix the bug in main.py"}, messages[1])
    before = copy.deepcopy(config)
    result = await engine.project(
        _request(tmp_path, config, messages=messages, incoming={"context_os_enabled": False}, history_limit=1)
    )
    assert result.changed is True and result.persisted_context_config["keep"] == "value"
    assert result.persisted_context_config["session_continuity"]["summary"]
    assert result.persisted_context_config["session_continuity"]["compacted_through_seq"] == 0
    assert config == before


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [{"mode": "other_namespace"}, {"messages": []}])
async def test_supplied_mapping_obeys_existing_persistence_invariants(
    engine: SessionContinuityEngine, tmp_path: Path, invalid: dict[str, Any]
) -> None:
    config = {KEY: invalid}
    before = copy.deepcopy(config)
    with pytest.raises(ContextOSInvariantViolationError):
        await engine.project(_request(tmp_path, config))
    assert config == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [{}, {KEY: None}, {"session_continuity": SessionContinuityPack(summary="Keep engineering facts.").to_dict()}],
)
async def test_disabled_no_input_does_not_report_unchanged_data_as_write(
    engine: SessionContinuityEngine, tmp_path: Path, config: dict[str, Any]
) -> None:
    before = copy.deepcopy(config)
    result = await engine.project(_request(tmp_path, config, incoming={"context_os_enabled": False}))
    assert config == before and result.persisted_context_config == before
    assert result.changed is False and result.context_os_projection is None


@pytest.mark.asyncio
async def test_existing_pack_does_not_hide_actual_disabled_snapshot_removal(
    engine: SessionContinuityEngine, tmp_path: Path
) -> None:
    config = {
        KEY: _snapshot(),
        "session_continuity": SessionContinuityPack(summary="Keep engineering facts.").to_dict(),
    }
    result = await engine.project(_request(tmp_path, config, incoming={"context_os_enabled": False}))
    assert KEY not in result.persisted_context_config
    assert result.persisted_context_config["session_continuity"] == config["session_continuity"]
    assert result.changed is True
