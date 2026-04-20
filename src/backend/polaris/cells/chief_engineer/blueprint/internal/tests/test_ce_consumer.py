"""Tests for CE consumer (ce_consumer.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.chief_engineer.blueprint.internal.ce_consumer import CEConsumer


class TestCEConsumerInit:
    def test_valid_construction(self) -> None:
        with patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service") as mock_get:
            mock_get.return_value = MagicMock()
            consumer = CEConsumer(workspace="/test/workspace", worker_id="w1")
            assert consumer._workspace == "/test/workspace"
            assert consumer._worker_id == "w1"
            assert consumer._visibility_timeout == 900
            assert consumer._poll_interval == 5.0

    def test_custom_params(self) -> None:
        with patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service") as mock_get:
            mock_get.return_value = MagicMock()
            consumer = CEConsumer(
                workspace="/test",
                worker_id="custom_worker",
                visibility_timeout_seconds=300,
                poll_interval=10.0,
            )
            assert consumer._visibility_timeout == 300
            assert consumer._poll_interval == 10.0

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            CEConsumer(workspace="", worker_id="w1")

    def test_whitespace_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            CEConsumer(workspace="   ", worker_id="w1")

    def test_empty_worker_id_raises(self) -> None:
        with pytest.raises(ValueError, match="worker_id"):
            CEConsumer(workspace="/test", worker_id="")

    def test_stop_event_initial_state(self) -> None:
        with patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service") as mock_get:
            mock_get.return_value = MagicMock()
            consumer = CEConsumer(workspace="/test", worker_id="w1")
            assert consumer._stop_event is not None
            assert not consumer._stop_event.is_set()


class TestCEConsumerPollOnce:
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_no_claimable_tasks_returns_empty_list(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        mock_result = MagicMock()
        mock_result.ok = False
        mock_result.task_id = ""
        mock_result.lease_token = ""
        mock_svc.claim_work_item.return_value = mock_result

        consumer = CEConsumer(workspace="/test", worker_id="w1")
        results = consumer.poll_once()
        assert results == []

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_successful_claim_and_ack(self, mock_get_svc: MagicMock) -> None:
        """Verify claim/ack flow by patching _run_ce_preflight to avoid actual CE analysis."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        # Claim returns a task on first call, then a mock with ok=False to break loop
        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-42"
        claim_result.lease_token = "lease-abc"
        claim_result.payload = {"title": "Test task", "scope_paths": ["/src/main.py"]}

        # Ack returns success
        ack_result = MagicMock()
        ack_result.ok = True
        ack_result.status = "pending_exec"

        # side_effect: first call returns claim_result, second call returns mock with ok=False
        no_claim_result = MagicMock()
        no_claim_result.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim_result]
        mock_svc.acknowledge_task_stage.return_value = ack_result

        consumer = CEConsumer(workspace="/test", worker_id="w1")

        with patch.object(
            consumer,
            "_run_ce_preflight",
            return_value={
                "blueprint_id": "bp-task-42",
                "guardrails": ["rule1"],
                "no_touch_zones": ["zone1"],
            },
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-42"
        assert results[0]["ok"] is True
        assert results[0]["status"] == "pending_exec"

        # Verify ack was called with correct next_stage
        ack_call_args = mock_svc.acknowledge_task_stage.call_args
        assert ack_call_args is not None
        cmd = ack_call_args[0][0]
        assert cmd.next_stage == "pending_exec"

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_claim_then_preflight_failure_requeues(self, mock_get_svc: MagicMock) -> None:
        """Verify failure path requeues to pending_design."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-99"
        claim_result.lease_token = "lease-xyz"
        claim_result.payload = {"title": "Failing task"}

        fail_result = MagicMock()
        fail_result.ok = False

        # First call returns the claim, second returns mock with ok=False to break loop
        no_claim_result = MagicMock()
        no_claim_result.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim_result]
        mock_svc.fail_task_stage.return_value = fail_result

        consumer = CEConsumer(workspace="/test", worker_id="w1")

        with patch.object(
            consumer,
            "_run_ce_preflight",
            side_effect=RuntimeError("analysis runner missing"),
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-99"
        assert results[0]["ok"] is False
        assert "analysis runner missing" in results[0]["reason"]

        # Verify requeue happened with correct error code
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_design"
        assert fail_call.error_code == "CE_design_failed"


class TestCEConsumerStop:
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_stop_sets_event(self, mock_get_svc: MagicMock) -> None:
        mock_get_svc.return_value = MagicMock()
        consumer = CEConsumer(workspace="/test", worker_id="w1")
        assert not consumer._stop_event.is_set()
        consumer.stop()
        assert consumer._stop_event.is_set()
