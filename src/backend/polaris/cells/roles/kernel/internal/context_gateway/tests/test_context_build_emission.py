"""Tests for RoleContextGateway per-run ``context.build`` observation emission.

The realtime ContextOS dashboard surfaces projection / in-window item counts from
``context.build`` events on the per-run ``runtime.events.jsonl`` (WS runtime_events
channel). The live role turn path (RoleContextGateway) must emit these so the
dashboard lights up for every role turn — not just PM planning's ``prompt_context``.
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.roles.kernel.internal.context_gateway.gateway import RoleContextGateway
from polaris.cells.roles.kernel.internal.context_gateway.gateway_telemetry import GatewayTelemetry
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


def _telemetry(tmp_path) -> GatewayTelemetry:
    return GatewayTelemetry(workspace=tmp_path, role_id="director")


class TestContextBuildEmission:
    def test_emits_to_explicit_events_path(self, tmp_path) -> None:
        telemetry = _telemetry(tmp_path)
        events_path = str(tmp_path / "events" / "runtime.events.jsonl")
        request = ContextRequest(message="hi", run_id="pm-00001", events_path=events_path)

        telemetry.emit_context_build_observation(
            request, items_count=5, total_tokens=3200, message_count=4, projection_id="proj-1"
        )

        rows = _read_events(events_path)
        assert len(rows) == 1
        event = rows[0]
        assert event["name"] == "context.build"
        assert event["kind"] == "observation"
        assert event["output"]["items_count"] == 5
        assert event["output"]["total_tokens"] == 3200
        assert event["output"]["message_count"] == 4
        assert event["refs"]["run_id"] == "pm-00001"
        assert event["refs"]["role"] == "director"

    def test_self_resolves_per_run_file_when_run_dir_exists(self, tmp_path) -> None:
        telemetry = _telemetry(tmp_path)
        run_dir = tmp_path / "runs" / "pm-00001"
        (run_dir / "events").mkdir(parents=True, exist_ok=True)
        request = ContextRequest(message="hi", run_id="pm-00001")  # no events_path → self-resolve

        with patch(f"{_GATEWAY_MODULE}.resolve_run_dir", return_value=str(run_dir)):
            telemetry.emit_context_build_observation(
                request, items_count=7, total_tokens=999, message_count=3, projection_id="p2"
            )

        rows = _read_events(str(run_dir / "events" / "runtime.events.jsonl"))
        assert len(rows) == 1
        assert rows[0]["output"]["items_count"] == 7

    def test_failsafe_skips_when_run_dir_missing(self, tmp_path) -> None:
        """Resolution mismatch (run dir absent) → skip rather than write a phantom file."""
        telemetry = _telemetry(tmp_path)
        missing_run_dir = tmp_path / "runs" / "ghost"  # intentionally not created
        request = ContextRequest(message="hi", run_id="ghost")

        with patch(f"{_GATEWAY_MODULE}.resolve_run_dir", return_value=str(missing_run_dir)):
            telemetry.emit_context_build_observation(
                request, items_count=5, total_tokens=1, message_count=1, projection_id="p"
            )

        assert not list(tmp_path.rglob("runtime.events.jsonl"))

    def test_no_run_id_no_emit(self, tmp_path) -> None:
        telemetry = _telemetry(tmp_path)
        request = ContextRequest(message="hi", run_id="")

        telemetry.emit_context_build_observation(
            request, items_count=5, total_tokens=3200, message_count=4, projection_id="p"
        )

        assert not list(tmp_path.rglob("runtime.events.jsonl"))

    def test_emit_never_raises_on_io_error(self, tmp_path) -> None:
        telemetry = _telemetry(tmp_path)
        request = ContextRequest(message="hi", run_id="pm-1", events_path=str(tmp_path / "e.jsonl"))

        with patch(f"{_GATEWAY_MODULE}.emit_event", side_effect=OSError("disk full")):
            # Observability must never break a turn.
            telemetry.emit_context_build_observation(
                request, items_count=1, total_tokens=1, message_count=1, projection_id="p"
            )

    def test_explicit_path_bypasses_run_dir_existence_guard(self, tmp_path) -> None:
        """An explicitly provided events_path is trusted (no run-dir existence guard)."""
        telemetry = _telemetry(tmp_path)
        events_path = str(tmp_path / "explicit" / "runtime.events.jsonl")
        request = ContextRequest(message="hi", run_id="pm-1", events_path=events_path)

        telemetry.emit_context_build_observation(
            request, items_count=2, total_tokens=10, message_count=1, projection_id="p"
        )

        assert os.path.isfile(events_path)


class TestContextBuildEmittedByBuildContext:
    """Integration: the real build_context() path emits context.build end-to-end."""

    @pytest.mark.asyncio
    async def test_build_context_emits_context_build(self, tmp_path) -> None:
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

        events_path = str(tmp_path / "events" / "runtime.events.jsonl")
        request = ContextRequest(
            message="hi",
            history=[("user", "hello")],
            context_os_snapshot=None,
            run_id="pm-00001",
            events_path=events_path,
        )

        with patch.object(gateway._context_os, "project", return_value=mock_projection):
            await gateway.build_context(request)

        rows = _read_events(events_path)
        builds = [r for r in rows if r.get("name") == "context.build"]
        assert len(builds) == 1
        # items_count mirrors the projection's active-window size (1 event here).
        assert builds[0]["output"]["items_count"] == 1
        assert builds[0]["refs"]["run_id"] == "pm-00001"
