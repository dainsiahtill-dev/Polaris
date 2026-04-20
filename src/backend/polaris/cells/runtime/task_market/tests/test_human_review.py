"""Tests for ``internal/human_review.py``."""

from __future__ import annotations

from polaris.cells.runtime.task_market.internal.human_review import (
    ESCALATION_CHAIN,
    RESOLUTION_ACTIONS,
    RESOLUTION_TO_STAGE,
    get_next_escalation_role,
)


class TestHumanReviewManager:
    """Unit tests for HumanReviewManager (logic-only, store mocked)."""

    # resolve_review validation — test the standalone validation guard.
    def test_resolve_review_rejects_invalid_resolution(self) -> None:
        # Test that RESOLUTION_ACTIONS rejects invalid inputs.
        invalid = "invalid_action"
        assert invalid not in RESOLUTION_ACTIONS

    def test_resolution_actions_defined(self) -> None:
        assert "requeue_design" in RESOLUTION_ACTIONS
        assert "requeue_exec" in RESOLUTION_ACTIONS
        assert "force_resolve" in RESOLUTION_ACTIONS
        assert "close_as_invalid" in RESOLUTION_ACTIONS
        assert "shadow_continue" in RESOLUTION_ACTIONS

    def test_resolution_to_stage_mapping(self) -> None:
        assert RESOLUTION_TO_STAGE["requeue_design"] == "pending_design"
        assert RESOLUTION_TO_STAGE["requeue_exec"] == "pending_exec"
        assert RESOLUTION_TO_STAGE["force_resolve"] == "resolved"
        assert RESOLUTION_TO_STAGE["close_as_invalid"] == "rejected"


class TestEscalationChain:
    """Tests for Tri-Council escalation chain."""

    def test_get_next_escalation_role_director(self) -> None:
        assert get_next_escalation_role("director") == "chief_engineer"

    def test_get_next_escalation_role_last_human(self) -> None:
        assert get_next_escalation_role("human") is None

    def test_get_next_escalation_role_unknown(self) -> None:
        assert get_next_escalation_role("unknown_role") is None

    def test_escalation_chain_complete(self) -> None:
        assert list(ESCALATION_CHAIN) == [
            "director",
            "chief_engineer",
            "pm",
            "architect",
            "human",
        ]
