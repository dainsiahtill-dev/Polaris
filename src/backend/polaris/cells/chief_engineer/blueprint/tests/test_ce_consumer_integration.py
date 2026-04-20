"""Integration tests for CEConsumer with DirectorPool and ADRStore."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from polaris.cells.chief_engineer.blueprint.internal.ce_consumer import CEConsumer
from polaris.cells.chief_engineer.blueprint.internal.director_pool import (
    DirectorPoolConflictError,
)


class TestCEConsumerDirectorPoolIntegration:
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.run_pre_dispatch_chief_engineer_ctx")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.DirectorPool")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.ADRStore")
    def test_ce_consumer_acks_with_director_pool_assignment(
        self,
        mock_adr_store_cls: MagicMock,
        mock_director_pool_cls: MagicMock,
        mock_run_preflight: MagicMock,
        mock_get_svc: MagicMock,
    ) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-42"
        claim_result.lease_token = "lease-abc"
        claim_result.payload = {"title": "Test task", "scope_paths": ["/src/main.py"]}

        no_claim_result = MagicMock()
        no_claim_result.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim_result]

        ack_result = MagicMock()
        ack_result.ok = True
        ack_result.status = "pending_exec"
        mock_svc.acknowledge_task_stage.return_value = ack_result

        mock_run_preflight.return_value = {
            "blueprint_id": "bp-task-42",
            "guardrails": ["rule1"],
            "no_touch_zones": ["zone1"],
        }

        mock_adr_store = MagicMock()
        mock_adr_store_cls.return_value = mock_adr_store

        async def _mock_assign(*args, **kwargs):
            return "director-1"

        mock_director_pool = MagicMock()
        mock_director_pool.assign_task.side_effect = _mock_assign
        mock_director_pool_cls.return_value = mock_director_pool

        consumer = CEConsumer(workspace="/test", worker_id="w1")
        results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-42"
        assert results[0]["ok"] is True
        assert results[0]["status"] == "pending_exec"

        mock_adr_store.create_blueprint.assert_called_once()
        create_call_args = mock_adr_store.create_blueprint.call_args
        assert create_call_args[0][0] == "bp-task-42"

        mock_adr_store.compile.assert_called_once_with("bp-task-42")
        mock_director_pool.assign_task.assert_called_once()

        ack_call_args = mock_svc.acknowledge_task_stage.call_args
        assert ack_call_args is not None
        cmd = ack_call_args[0][0]
        assert cmd.metadata.get("director_pool_assigned") is True
        assert cmd.metadata.get("blueprint_id") == "bp-task-42"

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.run_pre_dispatch_chief_engineer_ctx")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.DirectorPool")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.ADRStore")
    def test_ce_consumer_handles_director_pool_conflict(
        self,
        mock_adr_store_cls: MagicMock,
        mock_director_pool_cls: MagicMock,
        mock_run_preflight: MagicMock,
        mock_get_svc: MagicMock,
    ) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-99"
        claim_result.lease_token = "lease-xyz"
        claim_result.payload = {"title": "Conflicting task", "scope_paths": ["/src/main.py"]}

        no_claim_result = MagicMock()
        no_claim_result.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim_result]

        ack_result = MagicMock()
        ack_result.ok = True
        ack_result.status = "pending_exec"
        mock_svc.acknowledge_task_stage.return_value = ack_result

        mock_run_preflight.return_value = {
            "blueprint_id": "bp-task-99",
            "guardrails": [],
            "no_touch_zones": [],
        }

        mock_adr_store = MagicMock()
        mock_adr_store_cls.return_value = mock_adr_store

        mock_director_pool = MagicMock()
        mock_director_pool.assign_task.side_effect = DirectorPoolConflictError("task-99", ["/src/main.py"])
        mock_director_pool_cls.return_value = mock_director_pool

        consumer = CEConsumer(workspace="/test", worker_id="w1")
        results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-99"
        assert results[0]["ok"] is False
        assert results[0]["reason"] == "director_pool_conflict"

        mock_adr_store.create_blueprint.assert_called_once()
        mock_adr_store.compile.assert_called_once_with("bp-task-99")
        mock_director_pool.assign_task.assert_called_once()

        fail_call_args = mock_svc.fail_task_stage.call_args
        assert fail_call_args is not None
        cmd = fail_call_args[0][0]
        assert cmd.error_code == "CE_director_pool_conflict"
        assert cmd.requeue_stage == "pending_design"

        mock_svc.acknowledge_task_stage.assert_not_called()
