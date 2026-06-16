"""Tests for RoleContextGateway ``context.prefix_drift`` observation (Headroom T1-B).

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

The prefix-drift observer is NON-MUTATING: it fingerprints the cache-hot prefix
(role system_prompt + leading frozen system messages) and emits a per-run
``context.prefix_drift`` observation so the ContextOS dashboard / RoleSignalPlane
can surface whether the prefix drifts across turns and busts the local prompt
cache. These tests mirror ``test_context_build_emission.py``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from polaris.cells.roles.kernel.internal.context_gateway.gateway import RoleContextGateway
from polaris.kernelone.context.cache_stability.drift_detector import get_prefix_drift_observer
from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest

_GATEWAY_MODULE = "polaris.cells.roles.kernel.internal.context_gateway.gateway"


def _mock_profile() -> MagicMock:
    profile = MagicMock()
    profile.context_policy = MagicMock()
    profile.context_policy.max_history_turns = 8
    profile.context_policy.max_context_tokens = 128000
    profile.context_policy.include_project_structure = False
    profile.context_policy.include_task_history = False
    profile.context_policy.compression_strategy = "truncate"
    profile.context_domain = None
    profile.provider_id = "test_provider"
    profile.model = "test_model"
    profile.role_id = "director"
    profile.display_name = "Director"
    return profile


def _read_events(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


_PREFIX_MESSAGES = [
    {"role": "system", "content": "You are the Director. Tools: read, write."},
    {"role": "user", "content": "build the thing"},
]


class TestPrefixDriftEmission:
    def setup_method(self) -> None:
        # Module-level observer is process-wide; isolate each test.
        get_prefix_drift_observer().reset()

    def test_emits_to_explicit_events_path(self, tmp_path) -> None:
        gateway = RoleContextGateway(_mock_profile(), workspace=str(tmp_path))
        events_path = str(tmp_path / "events" / "runtime.events.jsonl")
        request = ContextRequest(message="hi", run_id="pm-00001", events_path=events_path)

        gateway._emit_prefix_drift_observation(request, messages=_PREFIX_MESSAGES, system_prompt="ROLE PROMPT")

        rows = _read_events(events_path)
        assert len(rows) == 1
        event = rows[0]
        assert event["name"] == "context.prefix_drift"
        assert event["kind"] == "observation"
        assert event["refs"]["run_id"] == "pm-00001"
        assert event["refs"]["role"] == "director"
        out = event["output"]
        assert out["fingerprint"]  # non-empty sha256 hex
        assert out["drifted"] is False
        assert out["first_seen"] is True
        assert out["prefix_message_count"] == 1
        assert isinstance(out["volatile_findings"], list)

    def test_drift_flagged_on_second_assembly_with_changed_prefix(self, tmp_path) -> None:
        gateway = RoleContextGateway(_mock_profile(), workspace=str(tmp_path))
        events_path = str(tmp_path / "events" / "runtime.events.jsonl")
        request = ContextRequest(message="hi", run_id="pm-00001", events_path=events_path)

        gateway._emit_prefix_drift_observation(request, messages=_PREFIX_MESSAGES, system_prompt="ROLE PROMPT")
        changed = [
            {"role": "system", "content": "You are the Director. Tools: read, write, edit."},
            {"role": "user", "content": "build the thing"},
        ]
        gateway._emit_prefix_drift_observation(request, messages=changed, system_prompt="ROLE PROMPT")

        rows = _read_events(events_path)
        drifts = [r for r in rows if r["name"] == "context.prefix_drift"]
        assert len(drifts) == 2
        assert drifts[0]["output"]["drifted"] is False
        assert drifts[1]["output"]["drifted"] is True
        assert drifts[1]["output"]["previous_fingerprint"] == drifts[0]["output"]["fingerprint"]

    def test_no_drift_when_prefix_stable(self, tmp_path) -> None:
        gateway = RoleContextGateway(_mock_profile(), workspace=str(tmp_path))
        events_path = str(tmp_path / "events" / "runtime.events.jsonl")
        request = ContextRequest(message="hi", run_id="pm-00001", events_path=events_path)

        for _ in range(2):
            gateway._emit_prefix_drift_observation(request, messages=_PREFIX_MESSAGES, system_prompt="ROLE PROMPT")

        rows = _read_events(events_path)
        drifts = [r for r in rows if r["name"] == "context.prefix_drift"]
        assert drifts[1]["output"]["drifted"] is False
        assert drifts[1]["output"]["first_seen"] is False

    def test_self_resolves_when_run_dir_exists(self, tmp_path) -> None:
        gateway = RoleContextGateway(_mock_profile(), workspace=str(tmp_path))
        run_dir = tmp_path / "runs" / "pm-00001"
        (run_dir / "events").mkdir(parents=True, exist_ok=True)
        request = ContextRequest(message="hi", run_id="pm-00001")

        with patch(f"{_GATEWAY_MODULE}.resolve_run_dir", return_value=str(run_dir)):
            gateway._emit_prefix_drift_observation(request, messages=_PREFIX_MESSAGES, system_prompt="ROLE PROMPT")

        rows = _read_events(str(run_dir / "events" / "runtime.events.jsonl"))
        assert any(r["name"] == "context.prefix_drift" for r in rows)

    def test_failsafe_skips_when_run_dir_missing(self, tmp_path) -> None:
        gateway = RoleContextGateway(_mock_profile(), workspace=str(tmp_path))
        missing = tmp_path / "runs" / "ghost"  # not created
        request = ContextRequest(message="hi", run_id="ghost")

        with patch(f"{_GATEWAY_MODULE}.resolve_run_dir", return_value=str(missing)):
            gateway._emit_prefix_drift_observation(request, messages=_PREFIX_MESSAGES, system_prompt="ROLE PROMPT")

        assert not list(tmp_path.rglob("runtime.events.jsonl"))

    def test_no_run_id_no_emit(self, tmp_path) -> None:
        gateway = RoleContextGateway(_mock_profile(), workspace=str(tmp_path))
        request = ContextRequest(message="hi", run_id="")

        gateway._emit_prefix_drift_observation(request, messages=_PREFIX_MESSAGES, system_prompt="ROLE PROMPT")

        assert not list(tmp_path.rglob("runtime.events.jsonl"))

    def test_emit_never_raises_on_io_error(self, tmp_path) -> None:
        gateway = RoleContextGateway(_mock_profile(), workspace=str(tmp_path))
        request = ContextRequest(message="hi", run_id="pm-1", events_path=str(tmp_path / "e.jsonl"))

        with patch(f"{_GATEWAY_MODULE}.emit_event", side_effect=OSError("disk full")):
            # Observability must never break a turn.
            gateway._emit_prefix_drift_observation(request, messages=_PREFIX_MESSAGES, system_prompt="ROLE PROMPT")

    def test_volatile_token_in_prefix_is_flagged(self, tmp_path) -> None:
        gateway = RoleContextGateway(_mock_profile(), workspace=str(tmp_path))
        events_path = str(tmp_path / "events" / "runtime.events.jsonl")
        request = ContextRequest(message="hi", run_id="pm-1", events_path=events_path)

        volatile_messages = [
            {"role": "system", "content": "Session started 2026-06-16T00:00:00Z"},
        ]
        gateway._emit_prefix_drift_observation(request, messages=volatile_messages, system_prompt="ROLE PROMPT")

        rows = _read_events(events_path)
        findings = rows[0]["output"]["volatile_findings"]
        assert any(f["kind"] == "iso8601_timestamp" for f in findings)


class TestPrefixDriftEmittedByBuildContext:
    """Integration: the real build_context() path emits context.prefix_drift."""

    def setup_method(self) -> None:
        get_prefix_drift_observer().reset()

    async def _run(self, tmp_path, events_path: str) -> None:
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
            TranscriptEventV2 as TranscriptEvent,
        )

        gateway = RoleContextGateway(_mock_profile(), workspace=str(tmp_path))

        mock_snapshot = MagicMock(spec=ContextOSSnapshot)
        mock_snapshot.budget_plan = MagicMock()
        mock_snapshot.budget_plan.validation_error = ""

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = "head"
        mock_projection.tail_anchor = "tail"
        mock_projection.active_window = (
            TranscriptEvent(
                event_id="e1",
                sequence=1,
                role="user",
                kind="user_turn",
                route="clear",
                content="hi",
            ),
        )
        mock_projection.run_card = MagicMock()
        mock_projection.run_card.current_goal = "g"
        mock_projection.run_card.open_loops = ()
        mock_projection.run_card.latest_user_intent = ""
        mock_projection.run_card.pending_followup_action = ""
        mock_projection.run_card.last_turn_outcome = ""
        mock_projection.snapshot = mock_snapshot

        request = ContextRequest(
            message="hi",
            history=(("user", "hello"),),
            context_os_snapshot=None,
            run_id="pm-00001",
            events_path=events_path,
        )

        with patch.object(gateway._context_os, "project", return_value=mock_projection):
            await gateway.build_context(request, system_prompt="ROLE SYSTEM PROMPT")

    def test_build_context_emits_prefix_drift(self, tmp_path) -> None:
        import asyncio

        events_path = str(tmp_path / "events" / "runtime.events.jsonl")
        asyncio.run(self._run(tmp_path, events_path))

        rows = _read_events(events_path)
        drifts = [r for r in rows if r.get("name") == "context.prefix_drift"]
        assert len(drifts) == 1
        assert drifts[0]["refs"]["run_id"] == "pm-00001"
        assert drifts[0]["output"]["first_seen"] is True
        assert drifts[0]["output"]["fingerprint"]
