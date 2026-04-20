"""Tests for QA task-market consumer routing and queue transitions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from polaris.cells.qa.audit_verdict.internal.qa_consumer import QAConsumer, _resolve_qa_route


class TestResolveQARoute:
    def test_pass_maps_to_resolved_terminal(self) -> None:
        verdict, next_stage, terminal_status = _resolve_qa_route({"verdict": "PASS"})
        assert verdict == "PASS"
        assert next_stage == ""
        assert terminal_status == "resolved"

    def test_requeue_exec_maps_to_pending_exec(self) -> None:
        verdict, next_stage, terminal_status = _resolve_qa_route({"verdict": "REQUEUE_EXEC"})
        assert verdict == "REQUEUE_EXEC"
        assert next_stage == "pending_exec"
        assert terminal_status == ""

    def test_explicit_next_stage_overrides_verdict(self) -> None:
        verdict, next_stage, terminal_status = _resolve_qa_route({"verdict": "FAIL", "next_stage": "waiting_human"})
        assert verdict == "FAIL"
        assert next_stage == "waiting_human"
        assert terminal_status == ""


class TestQAConsumerPollOnce:
    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_pass_verdict_acks_resolved(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-1"
        claim_result.lease_token = "lease-1"
        claim_result.payload = {"title": "QA task"}

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.acknowledge_task_stage.return_value = MagicMock(ok=True, status="resolved")

        consumer = QAConsumer(workspace="/test", worker_id="qa-1")
        with patch.object(consumer, "_run_qa_audit", return_value={"verdict": "PASS", "audit_id": "a1"}):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["status"] == "resolved"

        ack_call = mock_svc.acknowledge_task_stage.call_args[0][0]
        assert ack_call.terminal_status == "resolved"
        assert ack_call.next_stage is None

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_requeue_exec_verdict_routes_to_pending_exec(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-2"
        claim_result.lease_token = "lease-2"
        claim_result.payload = {"title": "QA task"}

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.acknowledge_task_stage.return_value = MagicMock(ok=True, status="pending_exec")

        consumer = QAConsumer(workspace="/test", worker_id="qa-2")
        with patch.object(consumer, "_run_qa_audit", return_value={"verdict": "REQUEUE_EXEC", "audit_id": "a2"}):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["status"] == "pending_exec"

        ack_call = mock_svc.acknowledge_task_stage.call_args[0][0]
        assert ack_call.next_stage == "pending_exec"
        assert ack_call.terminal_status is None

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_audit_exception_requeues_pending_qa(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-3"
        claim_result.lease_token = "lease-3"
        claim_result.payload = {"title": "QA task"}

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True)

        consumer = QAConsumer(workspace="/test", worker_id="qa-3")
        with patch.object(consumer, "_run_qa_audit", side_effect=RuntimeError("qa failed")):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is False
        assert "qa failed" in results[0]["reason"]

        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_qa"
        assert fail_call.error_code == "QA_audit_failed"
