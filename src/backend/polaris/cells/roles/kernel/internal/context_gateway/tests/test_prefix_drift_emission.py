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


class TestDriftEventConsumer:
    """T1-B sink is wired: the events file is consumed by ``summarize_drift_events``.

    This is the regression guard the third-pass audit found missing — the
    emitter wrote to JSONL but no consumer existed, so the data informed
    nothing. ``summarize_drift_events`` reads those rows and yields a per-run
    DriftSummary any UI / telemetry bridge can surface.
    """

    def setup_method(self) -> None:
        # The drift observer is a process-wide singleton (the cache_stability
        # design intentionally lets drift be detected across gateway instances
        # in the same process). Each test must start from a clean session-key
        # map so prior tests' fingerprints do not bleed into the first_seen /
        # drift / stable classification the consumer is asserting on. Without
        # this reset, the real-emit tests in this class collide on the second
        # emit (the singleton already has a ``previous_fingerprint`` for the
        # same session_key) and report drift where the test expects first_seen.
        # The emitter tests in ``TestPrefixDriftEmission`` and
        # ``TestPrefixDriftEmittedByBuildContext`` reset in their own
        # setup_method, but this class also drives the LIVE emit path
        # (``test_summarize_consumes_real_*``) and MUST reset to avoid
        # inheriting fingerprints from earlier tests in this or other classes.
        get_prefix_drift_observer().reset()

    def test_summarize_returns_zero_for_missing_file(self, tmp_path) -> None:
        from polaris.kernelone.context.cache_stability import summarize_drift_events

        summary = summarize_drift_events(str(tmp_path / "does-not-exist.jsonl"))
        assert summary.observed == 0
        assert summary.drifted == 0
        assert summary.stable == 0
        assert summary.first_seen == 0
        assert summary.by_role == {}
        assert summary.volatile_kinds == {}

    def test_summarize_reads_real_emitted_events(self, tmp_path) -> None:
        from polaris.kernelone.context.cache_stability import summarize_drift_events

        # Drive the LIVE emit path (the same path the gateway takes on a real turn)
        # so the consumer test reads what the emitter actually writes.
        events_path = str(tmp_path / "events" / "runtime.events.jsonl")
        gateway = RoleContextGateway(_mock_profile(), workspace=str(tmp_path))
        request = ContextRequest(message="hi", run_id="pm-00001", events_path=events_path)
        gateway._emit_prefix_drift_observation(request, messages=_PREFIX_MESSAGES, system_prompt="ROLE PROMPT")

        summary = summarize_drift_events(events_path)
        # The first emit is first_seen=True (not drifted); the consumer must classify
        # it correctly and never confuse it with a stable observation.
        assert summary.observed >= 1
        assert summary.first_seen + summary.drifted + summary.stable == summary.observed
        assert "director" in summary.by_role

    def test_summarize_classifies_drifted_and_volatile_kinds(self, tmp_path) -> None:
        """Synthetic events file: 1 first_seen + 1 drifted + volatile kinds."""
        from polaris.kernelone.context.cache_stability import summarize_drift_events

        events_path = tmp_path / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text(
            (
                '{"name":"context.prefix_drift","refs":{},"output":{"role":"director","first_seen":true,"drifted":false,"volatile_findings":[]}}\n'
                '{"name":"context.prefix_drift","refs":{},"output":{"role":"director","first_seen":false,"drifted":true,"volatile_findings":[{"kind":"iso8601","sample":"2026-06-16T12:00:00Z","count":1},{"kind":"uuidv4","sample":"abc-123","count":2}]}}\n'
                '{"name":"context.build","refs":{},"output":{}}\n'  # unrelated, must be ignored
            ),
            encoding="utf-8",
        )

        summary = summarize_drift_events(str(events_path))
        assert summary.observed == 2  # the unrelated context.build row is filtered
        assert summary.first_seen == 1
        assert summary.drifted == 1
        assert summary.stable == 0
        assert summary.by_role == {"director": 2}
        assert summary.volatile_kinds == {"iso8601": 1, "uuidv4": 2}
        # to_dict round-trips to a JSON-serializable shape suitable for the UI bridge.
        import json as _json

        _json.dumps(summary.to_dict())

    def test_summarize_consumes_real_drift_on_second_emit(self, tmp_path) -> None:
        """Adversarial: drive TWO real emits (the 2nd triggers real drift) and
        assert the consumer classifies drift+first_seen correctly.

        This is the "the consumer reads what the emitter emits" regression guard.
        The first ``_summarize_reads_real_emitted_events`` test only hits the
        first_seen path — if the consumer silently dropped the drifted row, no
        test would catch it. Here we exercise both branches through the real
        emitter to lock the contract.
        """
        from polaris.kernelone.context.cache_stability import summarize_drift_events

        events_path = str(tmp_path / "events" / "runtime.events.jsonl")
        gateway = RoleContextGateway(_mock_profile(), workspace=str(tmp_path))
        request = ContextRequest(message="hi", run_id="pm-00001", events_path=events_path)

        # First emit: first_seen=True (no drift)
        gateway._emit_prefix_drift_observation(request, messages=_PREFIX_MESSAGES, system_prompt="ROLE PROMPT")
        # Second emit with changed system prompt: should be drifted=True, first_seen=False
        changed_messages = [
            {"role": "system", "content": "You are the Director. Tools: read, write, edit."},
            {"role": "user", "content": "build the thing"},
        ]
        gateway._emit_prefix_drift_observation(request, messages=changed_messages, system_prompt="ROLE PROMPT")

        summary = summarize_drift_events(events_path)
        assert summary.observed == 2, (
            f"consumer dropped rows: expected 2 prefix_drift observations, got observed={summary.observed}"
        )
        assert summary.first_seen == 1, (
            "consumer failed to classify first emit as first_seen: "
            f"first_seen={summary.first_seen} drifted={summary.drifted}"
        )
        assert summary.drifted == 1, f"consumer failed to classify 2nd emit as drifted: drifted={summary.drifted}"
        assert summary.stable == 0
        assert summary.by_role == {"director": 2}

    def test_summarize_consumes_real_stable_on_repeat_emit(self, tmp_path) -> None:
        """Adversarial: two IDENTICAL real emits must yield first_seen+stable, not drift.

        Locks the stable branch (the third of the three classifications) through
        the real emitter, not just synthetic JSONL. A consumer that always
        reported first_seen=True (a plausible "lazy" implementation) would slip
        past the first_seen test but get caught here.
        """
        from polaris.kernelone.context.cache_stability import summarize_drift_events

        events_path = str(tmp_path / "events" / "runtime.events.jsonl")
        gateway = RoleContextGateway(_mock_profile(), workspace=str(tmp_path))
        request = ContextRequest(message="hi", run_id="pm-00001", events_path=events_path)

        for _ in range(2):
            gateway._emit_prefix_drift_observation(request, messages=_PREFIX_MESSAGES, system_prompt="ROLE PROMPT")

        summary = summarize_drift_events(events_path)
        assert summary.observed == 2
        assert summary.first_seen == 1
        assert summary.stable == 1, f"consumer failed to classify 2nd identical emit as stable: stable={summary.stable}"
        assert summary.drifted == 0

    def test_summarize_falls_back_to_refs_role_when_output_role_missing(self, tmp_path) -> None:
        """Adversarial: when ``output.role`` is absent, the consumer must use
        ``refs.role`` so role-bucketed telemetry still works on legacy/older
        emitter payloads. A consumer that only inspected ``output.role`` would
        silently drop role attribution here.
        """
        from polaris.kernelone.context.cache_stability import summarize_drift_events

        events_path = tmp_path / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text(
            (
                '{"name":"context.prefix_drift","refs":{"role":"chief_engineer"},"output":{"first_seen":true,"drifted":false,"volatile_findings":[]}}\n'
                # role from output takes precedence when both present
                '{"name":"context.prefix_drift","refs":{"role":"chief_engineer"},"output":{"role":"director","first_seen":false,"drifted":false,"volatile_findings":[]}}\n'
            ),
            encoding="utf-8",
        )

        summary = summarize_drift_events(str(events_path))
        assert summary.by_role == {"chief_engineer": 1, "director": 1}
        assert summary.observed == 2
        assert summary.first_seen == 1
        assert summary.stable == 1

    def test_summarize_tolerates_malformed_jsonl_line(self, tmp_path) -> None:
        """Adversarial: a corrupt JSONL line must NOT crash the consumer.

        Real per-run events files can be partially written or include legacy
        non-JSON diagnostics. The consumer must skip the bad line and keep
        reading — a single ValueError here would take out a UI dashboard.
        """
        from polaris.kernelone.context.cache_stability import summarize_drift_events

        events_path = tmp_path / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text(
            (
                "this line is not JSON at all\n"
                '{"name":"context.prefix_drift","refs":{},"output":{"role":"director","first_seen":true,"drifted":false,"volatile_findings":[]}}\n'
                '{"name":"context.prefix_drift", truncated...\n'  # malformed
                '{"name":"context.prefix_drift","refs":{},"output":{"role":"director","first_seen":false,"drifted":true,"volatile_findings":[{"kind":"uuidv4","sample":"x","count":3}]}}\n'
            ),
            encoding="utf-8",
        )

        summary = summarize_drift_events(str(events_path))
        assert summary.observed == 2  # the 2 malformed lines are skipped
        assert summary.first_seen == 1
        assert summary.drifted == 1
        assert summary.volatile_kinds == {"uuidv4": 3}

    def test_to_dict_round_trips_through_json(self, tmp_path) -> None:
        """Adversarial: ``to_dict()`` must be JSON-serializable end-to-end so a
        UI bridge can ``json.dumps(summary.to_dict())`` and ``json.loads()`` the
        result back without losing fidelity. The previous test only checked
        that ``dumps`` did not raise — here we parse it back and compare.
        """
        from polaris.kernelone.context.cache_stability import summarize_drift_events

        events_path = tmp_path / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text(
            (
                '{"name":"context.prefix_drift","refs":{},"output":{"role":"director","first_seen":true,"drifted":false,"volatile_findings":[]}}\n'
                '{"name":"context.prefix_drift","refs":{},"output":{"role":"pm","first_seen":false,"drifted":true,"volatile_findings":[{"kind":"iso8601","sample":"x","count":1}]}}\n'
            ),
            encoding="utf-8",
        )

        summary = summarize_drift_events(str(events_path))
        round_tripped = json.loads(json.dumps(summary.to_dict()))
        assert round_tripped == {
            "observed": 2,
            "drifted": 1,
            "first_seen": 1,
            "stable": 0,
            "by_role": {"director": 1, "pm": 1},
            "volatile_kinds": {"iso8601": 1},
        }
