"""Tests for ``public/hitl_api.py``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.runtime.task_market.internal.human_review import (
    ESCALATION_CHAIN,
    RESOLUTION_ACTIONS,
    get_next_escalation_role,
)
from polaris.cells.runtime.task_market.public import hitl_api


class TestResolutionActions:
    """Unit tests for RESOLUTION_ACTIONS constant."""

    def test_resolution_actions_contains_expected_values(self) -> None:
        assert "requeue_design" in RESOLUTION_ACTIONS
        assert "requeue_exec" in RESOLUTION_ACTIONS
        assert "force_resolve" in RESOLUTION_ACTIONS
        assert "close_as_invalid" in RESOLUTION_ACTIONS
        assert "shadow_continue" in RESOLUTION_ACTIONS

    def test_resolution_actions_is_frozenset(self) -> None:
        assert isinstance(RESOLUTION_ACTIONS, frozenset)


class TestEscalationChain:
    """Unit tests for Tri-Council escalation chain."""

    def test_escalation_chain_order(self) -> None:
        assert list(ESCALATION_CHAIN) == [
            "director",
            "chief_engineer",
            "pm",
            "architect",
            "human",
        ]

    def test_get_next_escalation_role_director(self) -> None:
        assert get_next_escalation_role("director") == "chief_engineer"

    def test_get_next_escalation_role_chief_engineer(self) -> None:
        assert get_next_escalation_role("chief_engineer") == "pm"

    def test_get_next_escalation_role_pm(self) -> None:
        assert get_next_escalation_role("pm") == "architect"

    def test_get_next_escalation_role_architect(self) -> None:
        assert get_next_escalation_role("architect") == "human"

    def test_get_next_escalation_role_human_is_none(self) -> None:
        assert get_next_escalation_role("human") is None

    def test_get_next_escalation_role_unknown_is_none(self) -> None:
        assert get_next_escalation_role("unknown_role") is None


class TestGetEscalationChain:
    """Unit tests for ``get_escalation_chain``."""

    def test_get_escalation_chain_returns_list(self) -> None:
        chain = hitl_api.get_escalation_chain()
        assert isinstance(chain, list)
        assert chain == list(ESCALATION_CHAIN)


class TestGetNextRole:
    """Unit tests for ``get_next_role``."""

    def test_get_next_role_director(self) -> None:
        assert hitl_api.get_next_role("director") == "chief_engineer"

    def test_get_next_role_human(self) -> None:
        assert hitl_api.get_next_role("human") is None

    def test_get_next_role_unknown(self) -> None:
        assert hitl_api.get_next_role("unknown") is None


class TestResolveReview:
    """Unit tests for ``resolve_review``."""

    @pytest.fixture
    def mock_service(self) -> MagicMock:
        service = MagicMock()
        return service

    def test_resolve_review_invalid_resolution(self, mock_service: MagicMock) -> None:
        with patch.object(hitl_api, "get_task_market_service", return_value=mock_service):
            result = hitl_api.resolve_review(
                workspace="/tmp/ws",
                task_id="task-1",
                resolution="invalid_action",
            )

        assert result["ok"] is False
        assert "Invalid resolution" in result["reason"]

    def test_resolve_review_valid_action(self, mock_service: MagicMock) -> None:
        mock_service.resolve_human_review.return_value = MagicMock(
            ok=True,
            status="pending_design",
            stage="pending_design",
        )
        with patch.object(hitl_api, "get_task_market_service", return_value=mock_service):
            result = hitl_api.resolve_review(
                workspace="/tmp/ws",
                task_id="task-1",
                resolution="requeue_design",
                resolved_by="human",
            )

        assert result["ok"] is True
        assert result["task_id"] == "task-1"
        assert result["resolution"] == "requeue_design"
        assert result["status"] == "pending_design"

    def test_resolve_review_normalizes_resolution(self, mock_service: MagicMock) -> None:
        mock_service.resolve_human_review.return_value = MagicMock(
            ok=True,
            status="pending_design",
            stage="pending_design",
        )
        with patch.object(hitl_api, "get_task_market_service", return_value=mock_service):
            # Uppercase resolution should still work
            result = hitl_api.resolve_review(
                workspace="/tmp/ws",
                task_id="task-1",
                resolution="REQUEUE_DESIGN",
            )

        assert result["ok"] is True
        assert result["resolution"] == "requeue_design"


class TestEscalateToCouncil:
    """Unit tests for ``escalate_to_council``."""

    @pytest.fixture
    def mock_service(self) -> MagicMock:
        service = MagicMock()
        return service

    def test_escalate_to_council_success(self, mock_service: MagicMock) -> None:
        mock_service.request_human_review.return_value = MagicMock(
            ok=True,
            status="waiting_human",
            stage="waiting_human",
        )
        with patch.object(hitl_api, "get_task_market_service", return_value=mock_service):
            result = hitl_api.escalate_to_council(
                workspace="/tmp/ws",
                task_id="task-escalate",
                trace_id="trace-escalate",
            )

        assert result["ok"] is True
        assert result["task_id"] == "task-escalate"
        assert result["escalation"] == "tri_council"
        assert result["status"] == "waiting_human"


class TestListPendingReviews:
    """Unit tests for ``list_pending_reviews``."""

    def test_list_pending_reviews_returns_list(self) -> None:
        mock_service = MagicMock()
        mock_service.query_pending_human_reviews.return_value = (
            {"task_id": "task-1", "status": "waiting"},
            {"task_id": "task-2", "status": "waiting"},
        )

        with patch.object(hitl_api, "get_task_market_service", return_value=mock_service):
            reviews = hitl_api.list_pending_reviews("/tmp/ws", limit=50)

        assert len(reviews) == 2
        mock_service.query_pending_human_reviews.assert_called_once()

    def test_list_pending_reviews_default_limit(self) -> None:
        mock_service = MagicMock()
        mock_service.query_pending_human_reviews.return_value = ()

        with patch.object(hitl_api, "get_task_market_service", return_value=mock_service):
            hitl_api.list_pending_reviews("/tmp/ws")

        mock_service.query_pending_human_reviews.assert_called_once()


class TestAdvanceCouncilRole:
    def test_advance_council_role_success(self) -> None:
        mock_service = MagicMock()
        mock_service.advance_human_review_escalation.return_value = {
            "ok": True,
            "task_id": "task-1",
            "current_role": "chief_engineer",
            "next_role": "pm",
        }
        with patch.object(hitl_api, "get_task_market_service", return_value=mock_service):
            result = hitl_api.advance_council_role("/tmp/ws", "task-1", escalated_by="director")
        assert result["ok"] is True
        assert result["current_role"] == "chief_engineer"
