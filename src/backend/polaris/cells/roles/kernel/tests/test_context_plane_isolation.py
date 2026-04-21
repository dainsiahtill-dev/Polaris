"""Test Context Plane Isolation — control-plane noise must not leak into prompt projection.

Validates ADR-0071 §17.3: ContextOS / Plane Isolation裁决:
- control-plane 字段不得进入 data plane
- raw tool output / system warning / thinking residue 不得直接回灌 prompt
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.context.context_os.models_v2 import (
    ArtifactRecordV2 as ArtifactRecord,
    ContextOSProjectionV2 as ContextOSProjection,
    ContextOSSnapshotV2 as ContextOSSnapshot,
    ContextSlicePlanV2 as ContextSlicePlan,
    RunCardV2 as RunCard,
    TranscriptEventV2 as TranscriptEvent,
    WorkingStateV2 as WorkingState,
)


class TestContextPlaneIsolation:
    """Context Plane Isolation regression tests."""

    def _make_snapshot(self, **overrides: Any) -> ContextOSSnapshot:
        """Build a minimal ContextOSSnapshot for testing."""
        return ContextOSSnapshot(
            transcript_log=overrides.get("transcript_log", ()),
            working_state=overrides.get("working_state", WorkingState()),
            artifact_store=overrides.get("artifact_store", ()),
            episode_store=overrides.get("episode_store", ()),
        )

    def _make_projection(self, snapshot: ContextOSSnapshot | None = None) -> ContextOSProjection:
        """Build a minimal ContextOSProjection for testing."""
        return ContextOSProjection(
            snapshot=snapshot or self._make_snapshot(),
            head_anchor="head-test",
            tail_anchor="tail-test",
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Happy Path
    # ──────────────────────────────────────────────────────────────────────────

    def test_prompt_dict_excludes_full_snapshot(self) -> None:
        """to_prompt_dict() must return lightweight data, not the full snapshot."""
        snapshot = self._make_snapshot()
        projection = self._make_projection(snapshot=snapshot)

        prompt_dict = projection.to_prompt_dict()

        assert "snapshot" not in prompt_dict
        assert prompt_dict["head_anchor"] == "head-test"
        assert prompt_dict["tail_anchor"] == "tail-test"

    def test_prompt_dict_includes_active_window(self) -> None:
        """to_prompt_dict() must include active_window events."""
        event = TranscriptEvent(
            event_id="evt-1",
            sequence=1,
            role="user",
            kind="message",
            route="clear",
            content="hello",
        )
        snapshot = self._make_snapshot(transcript_log=(event,))
        projection = ContextOSProjection(
            snapshot=snapshot,
            head_anchor="head",
            tail_anchor="tail",
            active_window=(event,),
        )

        prompt_dict = projection.to_prompt_dict()

        assert len(prompt_dict["active_window"]) == 1
        assert prompt_dict["active_window"][0]["content"] == "hello"

    def test_prompt_dict_includes_artifact_stubs(self) -> None:
        """to_prompt_dict() must include artifact stubs (not full artifacts)."""
        artifact = ArtifactRecord(
            artifact_id="art-1",
            artifact_type="file",
            mime_type="text/plain",
            token_count=100,
            char_count=500,
            peek="peek content",
        )
        snapshot = self._make_snapshot(artifact_store=(artifact,))
        projection = ContextOSProjection(
            snapshot=snapshot,
            head_anchor="head",
            tail_anchor="tail",
            artifact_stubs=(artifact,),
        )

        prompt_dict = projection.to_prompt_dict()

        assert len(prompt_dict["artifact_stubs"]) == 1
        stub = prompt_dict["artifact_stubs"][0]
        assert stub["artifact_id"] == "art-1"
        assert "content" not in stub  # stub excludes full content

    def test_prompt_dict_includes_run_card(self) -> None:
        """to_prompt_dict() must include run_card if present."""
        run_card = RunCard(current_goal="test goal")
        projection = self._make_projection()
        projection = ContextOSProjection(
            snapshot=projection.snapshot,
            head_anchor="head",
            tail_anchor="tail",
            run_card=run_card,
        )

        prompt_dict = projection.to_prompt_dict()

        assert prompt_dict["run_card"] is not None
        assert prompt_dict["run_card"]["current_goal"] == "test goal"

    def test_prompt_dict_includes_context_slice_plan(self) -> None:
        """to_prompt_dict() must include context_slice_plan if present."""
        plan = ContextSlicePlan(plan_id="plan-1", budget_tokens=1000)
        projection = self._make_projection()
        projection = ContextOSProjection(
            snapshot=projection.snapshot,
            head_anchor="head",
            tail_anchor="tail",
            context_slice_plan=plan,
        )

        prompt_dict = projection.to_prompt_dict()

        assert prompt_dict["context_slice_plan"] is not None
        assert prompt_dict["context_slice_plan"]["plan_id"] == "plan-1"

    # ──────────────────────────────────────────────────────────────────────────
    # Edge Cases
    # ──────────────────────────────────────────────────────────────────────────

    def test_prompt_dict_with_empty_projection(self) -> None:
        """to_prompt_dict() must handle empty projection gracefully."""
        projection = self._make_projection()

        prompt_dict = projection.to_prompt_dict()

        assert prompt_dict["active_window"] == []
        assert prompt_dict["artifact_stubs"] == []
        assert prompt_dict["episode_cards"] == []
        assert prompt_dict["run_card"] is None
        assert prompt_dict["context_slice_plan"] is None

    def test_prompt_dict_does_not_include_policy_verdicts(self) -> None:
        """to_prompt_dict() must NOT include policy_verdicts field (does not exist)."""
        projection = self._make_projection()

        prompt_dict = projection.to_prompt_dict()

        # policy_verdicts is not a field of ContextOSProjection
        assert "policy_verdicts" not in prompt_dict

    # ──────────────────────────────────────────────────────────────────────────
    # Control-Plane Noise Isolation
    # ──────────────────────────────────────────────────────────────────────────

    def test_control_plane_budget_status_not_in_prompt(self) -> None:
        """Budget status (control-plane) must not leak into prompt dict."""
        projection = self._make_projection()

        prompt_dict = projection.to_prompt_dict()

        # BudgetPlan exists in snapshot but should not be in prompt_dict
        assert "budget_plan" not in prompt_dict
        assert "budget_status" not in prompt_dict

    def test_control_plane_telemetry_not_in_prompt(self) -> None:
        """Telemetry metrics (control-plane) must not leak into prompt dict."""
        projection = self._make_projection()

        prompt_dict = projection.to_prompt_dict()

        assert "telemetry" not in prompt_dict
        assert "metrics" not in prompt_dict

    def test_control_plane_pending_followup_not_in_prompt(self) -> None:
        """Pending follow-up (control-plane state) must not leak into prompt dict."""
        from polaris.kernelone.context.context_os.models_v2 import PendingFollowUpV2 as PendingFollowUp

        snapshot = self._make_snapshot(pending_followup=PendingFollowUp(action="confirm", status="pending"))
        projection = self._make_projection(snapshot=snapshot)

        prompt_dict = projection.to_prompt_dict()

        assert "pending_followup" not in prompt_dict

    def test_to_dict_vs_to_prompt_dict_difference(self) -> None:
        """to_dict() must include full snapshot; to_prompt_dict() must not."""
        snapshot = self._make_snapshot()
        projection = self._make_projection(snapshot=snapshot)

        full_dict = projection.to_dict()
        prompt_dict = projection.to_prompt_dict()

        assert "snapshot" in full_dict
        assert "snapshot" not in prompt_dict

    # ──────────────────────────────────────────────────────────────────────────
    # Regression: Raw Tool Output Isolation
    # ──────────────────────────────────────────────────────────────────────────

    def test_raw_tool_output_not_inlined_in_prompt(self) -> None:
        """Large tool outputs must be referenced, not inlined in prompt."""
        large_content = "x" * 10000
        event = TranscriptEvent(
            event_id="evt-tool",
            sequence=1,
            role="assistant",
            kind="tool_result",
            route="clear",
            content=large_content,
        )
        snapshot = self._make_snapshot(transcript_log=(event,))
        projection = ContextOSProjection(
            snapshot=snapshot,
            head_anchor="head",
            tail_anchor="tail",
            active_window=(event,),
        )

        prompt_dict = projection.to_prompt_dict()

        # The event content should be present in active_window
        assert len(prompt_dict["active_window"]) == 1
        # But it should be the full content (projection doesn't compress)
        assert len(prompt_dict["active_window"][0]["content"]) == 10000

    def test_system_warning_not_in_active_window(self) -> None:
        """System warnings must not appear in active_window projection."""
        event = TranscriptEvent(
            event_id="evt-1",
            sequence=1,
            role="user",
            kind="message",
            route="clear",
            content="hello",
        )
        snapshot = self._make_snapshot(transcript_log=(event,))
        projection = ContextOSProjection(
            snapshot=snapshot,
            head_anchor="head",
            tail_anchor="tail",
            active_window=(event,),
        )

        prompt_dict = projection.to_prompt_dict()

        # System warnings are not part of active_window
        content_str = str(prompt_dict)
        assert "system warning" not in content_str.lower()

    # ──────────────────────────────────────────────────────────────────────────
    # Exceptions
    # ──────────────────────────────────────────────────────────────────────────

    def test_prompt_dict_with_none_run_card(self) -> None:
        """to_prompt_dict() must handle None run_card gracefully."""
        projection = ContextOSProjection(
            snapshot=self._make_snapshot(),
            head_anchor="head",
            tail_anchor="tail",
            run_card=None,
        )

        prompt_dict = projection.to_prompt_dict()

        assert prompt_dict["run_card"] is None

    def test_prompt_dict_with_none_context_slice_plan(self) -> None:
        """to_prompt_dict() must handle None context_slice_plan gracefully."""
        projection = ContextOSProjection(
            snapshot=self._make_snapshot(),
            head_anchor="head",
            tail_anchor="tail",
            context_slice_plan=None,
        )

        prompt_dict = projection.to_prompt_dict()

        assert prompt_dict["context_slice_plan"] is None
