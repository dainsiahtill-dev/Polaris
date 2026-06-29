"""Tests for QA task-market consumer routing and queue transitions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import polaris.cells.qa.audit_verdict.internal.qa_consumer as qa_consumer_module
import pytest
from polaris.cells.qa.audit_verdict.internal.qa_consumer import (
    QAConsumer,
    _format_qa_findings_feedback,
    _qa_findings_are_actionable,
    _resolve_qa_route,
)
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
)
from polaris.cells.runtime.task_market.public.service import TaskMarketService


def _qa_job_token(*, run_id: str = "run-qa", token_id: str = "token-qa") -> dict[str, Any]:
    return {
        "token_id": token_id,
        "run_id": run_id,
        "project_id": "P-QA",
        "contract_hash": "contract-hash-qa",
        "blueprint_hash": "blueprint-hash-qa",
        "capability_audit": {"ok": True, "issues": []},
        "gate_policy": {
            "enabled_evidence_modalities": ["qa"],
            "required_evidence_modalities": [],
        },
    }


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


class TestQAFindingsRequeue:
    """RANK 1 (Reflexion/Actor-Critic): a content FAIL must hand its precise
    findings to the Director via the last_failure channel, not die in a terminal
    reject where the Director structurally cannot see them."""

    def test_actionable_predicate(self) -> None:
        assert _qa_findings_are_actionable(["[error] x: y"]) is True
        assert _qa_findings_are_actionable([]) is False
        assert _qa_findings_are_actionable(["", "  "]) is False
        assert _qa_findings_are_actionable("not a list") is False

    def test_feedback_quotes_findings_and_caps(self) -> None:
        findings = [f"[error] f{i}.js: issue {i}" for i in range(10)]
        msg = _format_qa_findings_feedback(findings, "FAIL")
        assert "QA rejected" in msg and "preserving all existing working code" in msg
        assert "[error] f0.js: issue 0" in msg
        # capped at 5 findings -> the 6th must not appear
        assert "issue 5" not in msg
        assert len(msg) <= 600

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_content_fail_with_findings_requeues_to_pending_exec(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-fail"
        claim_result.lease_token = "lease-fail"
        claim_result.payload = {"title": "QA task"}
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_exec")

        consumer = QAConsumer(workspace="/test", worker_id="qa-fail")
        audit = {"verdict": "FAIL", "audit_id": "a1", "findings": ["[error] main.js: missing Ball class"]}
        with patch.object(consumer, "_run_qa_audit", return_value=audit):
            results = consumer.poll_once()

        assert results[0]["reason"] == "qa_findings_requeued"
        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.error_code == "QA_audit_failed"
        assert fail_call.requeue_stage == "pending_exec"
        assert "missing Ball class" in fail_call.error_message

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_content_fail_without_findings_stays_terminal_reject(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-fail2"
        claim_result.lease_token = "lease-fail2"
        claim_result.payload = {"title": "QA task"}
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.acknowledge_task_stage.return_value = MagicMock(ok=True, status="rejected")

        consumer = QAConsumer(workspace="/test", worker_id="qa-fail2")
        with patch.object(
            consumer, "_run_qa_audit", return_value={"verdict": "FAIL", "audit_id": "a2", "findings": []}
        ):
            results = consumer.poll_once()

        # No actionable findings -> unchanged terminal-reject behavior.
        ack_call = mock_svc.acknowledge_task_stage.call_args[0][0]
        assert ack_call.terminal_status == "rejected"
        assert results[0]["verdict"] == "FAIL"

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_authoritative_verdict_engine_requeues_implementation_defect(
        self,
        mock_get_svc: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("KERNELONE_QA_VERDICT_ENGINE_MODE", "engine")
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-engine"
        claim_result.lease_token = "lease-engine"
        claim_result.payload = {"title": "QA task"}
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_exec")

        consumer = QAConsumer(workspace="/test", worker_id="qa-engine")
        with patch.object(
            consumer,
            "_run_qa_audit",
            return_value={"verdict": "FAIL", "audit_id": "a-engine", "findings": []},
        ):
            results = consumer.poll_once()

        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_exec"
        assert fail_call.metadata["qa_verdict_engine_shadow"]["authoritative"] is True
        assert results[0]["reason"] == "qa_requeue"

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_env_off_keeps_terminal_reject(self, mock_get_svc: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_QA_FINDINGS_REQUEUE", "off")
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-fail3"
        claim_result.lease_token = "lease-fail3"
        claim_result.payload = {"title": "QA task"}
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.acknowledge_task_stage.return_value = MagicMock(ok=True, status="rejected")

        consumer = QAConsumer(workspace="/test", worker_id="qa-fail3")
        audit = {"verdict": "FAIL", "audit_id": "a3", "findings": ["[error] main.js: x"]}
        with patch.object(consumer, "_run_qa_audit", return_value=audit):
            results = consumer.poll_once()

        mock_svc.fail_task_stage.assert_not_called()
        ack_call = mock_svc.acknowledge_task_stage.call_args[0][0]
        assert ack_call.terminal_status == "rejected"
        assert results[0]["verdict"] == "FAIL"

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_repeated_content_fail_terminates_at_bounce_cap(self, mock_get_svc: MagicMock) -> None:
        # A Director success-ack resets the market attempt budget between QA passes,
        # so the in-memory per-task cap (default 2) is what stops an unsatisfiable
        # critique from ping-ponging forever.
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        claim = MagicMock()
        claim.ok = True
        claim.task_id = "task-loop"
        claim.lease_token = "lease-loop"
        claim.payload = {"title": "QA task"}
        mock_svc.claim_work_item.return_value = claim  # same task claimed each pass
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_exec")
        mock_svc.acknowledge_task_stage.return_value = MagicMock(ok=True, status="rejected")

        consumer = QAConsumer(workspace="/test", worker_id="qa-loop")
        audit = {"verdict": "FAIL", "audit_id": "a", "findings": ["[error] main.js: unsatisfiable"]}
        with patch.object(consumer, "_run_qa_audit", return_value=audit):
            r1 = consumer._claim_and_process_one()
            r2 = consumer._claim_and_process_one()
            r3 = consumer._claim_and_process_one()

        # cap=2: first two requeue with findings, the third terminal-rejects.
        assert r1["reason"] == "qa_findings_requeued"
        assert r2["reason"] == "qa_findings_requeued"
        assert mock_svc.fail_task_stage.call_count == 2
        assert r3.get("reason") != "qa_findings_requeued"
        ack_call = mock_svc.acknowledge_task_stage.call_args[0][0]
        assert ack_call.terminal_status == "rejected"


class TestQAConsumerPollOnce:
    @pytest.mark.asyncio
    async def test_llm_review_invokes_qa_with_text_only_runtime(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        calls: list[Any] = []

        class FakeDialogueService:
            def __init__(self, settings: object) -> None:
                self.settings = settings

            async def invoke_role_dialogue(self, command: object) -> object:
                calls.append(command)
                return SimpleNamespace(
                    ok=True,
                    status="ok",
                    content='{"verdict":"PASS","findings":[],"summary":"ok"}',
                    metadata={"provider": "MiniMax-M3"},
                    error_code=None,
                    error_message=None,
                )

        monkeypatch.setattr(
            "polaris.cells.llm.dialogue.public.service.LlmDialogueService",
            FakeDialogueService,
        )
        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-llm", enable_llm_audit=True)

        result = await consumer._run_qa_llm_review_async(
            task_id="task-llm",
            task_subject="Review target",
            changed_files=["main.py"],
            audit_result={"verdict": "PASS", "findings": [], "metrics": {"files_audited": 1}},
            payload={"run_id": "run-llm"},
        )

        assert result["ok"] is True
        assert calls
        command = calls[0]
        context = command.context
        metadata = command.metadata
        assert context["disable_internal_tool_rounds"] is True
        assert context["_transaction_kernel_forced_tool_definitions"] == []
        assert context["_transaction_kernel_forced_tool_choice"] == "none"
        assert metadata["validate_output"] is False
        assert metadata["max_retries"] == 0

    @pytest.mark.asyncio
    async def test_llm_review_includes_payload_context_for_ce_blueprint_coverage(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        calls: list[Any] = []

        class FakeDialogueService:
            def __init__(self, settings: object) -> None:
                self.settings = settings

            async def invoke_role_dialogue(self, command: object) -> object:
                calls.append(command)
                return SimpleNamespace(
                    ok=True,
                    status="ok",
                    content='{"verdict":"PASS","findings":[],"summary":"ok"}',
                    metadata={},
                    error_code=None,
                    error_message=None,
                )

        monkeypatch.setattr(
            "polaris.cells.llm.dialogue.public.service.LlmDialogueService",
            FakeDialogueService,
        )
        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-llm", enable_llm_audit=True)

        result = await consumer._run_qa_llm_review_async(
            task_id="task-llm-context",
            task_subject="Review target",
            changed_files=[],
            audit_result={"verdict": "FAIL", "findings": ["npm run build failed"]},
            payload={
                "run_id": "run-llm",
                "input": (
                    'PM task contract: acceptance criteria and target_files ["src/main.ts"].\n'
                    "Chief Engineer blueprint evidence collected before QA judgement: blueprint_id=bp-1.\n"
                    'factory_workspace_quality: npm run build failed with exit_code=2; "factory_run_id": "run-llm"'
                ),
            },
        )

        assert result["ok"] is True
        assert calls
        message = calls[0].message
        assert "PM task contract" in message
        assert "Chief Engineer blueprint evidence" in message
        assert "factory_workspace_quality" in message
        assert '"factory_run_id"' not in message
        assert '"factory_run_ref"' in message

    @pytest.mark.asyncio
    async def test_malformed_llm_review_does_not_override_deterministic_pass(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class FakeDialogueService:
            def __init__(self, settings: object) -> None:
                self.settings = settings

            async def invoke_role_dialogue(self, command: object) -> object:
                return SimpleNamespace(
                    ok=True,
                    status="ok",
                    content="PASS: looks fine",
                    metadata={},
                    error_code=None,
                    error_message=None,
                )

        monkeypatch.setattr(
            "polaris.cells.llm.dialogue.public.service.LlmDialogueService",
            FakeDialogueService,
        )
        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-llm", enable_llm_audit=True)

        async def _deterministic_pass(**_kwargs: object) -> object:
            return SimpleNamespace(
                audit_id="audit-pass",
                verdict="PASS",
                issues=[],
                metrics={"files_audited": 1},
            )

        monkeypatch.setattr(consumer._qa_svc, "audit_task", _deterministic_pass)

        result = await consumer._run_qa_audit_async(
            "task-llm-malformed",
            {"run_id": "run-llm", "title": "Review target", "target_files": ["main.py"]},
        )

        assert result["verdict"] == "PASS"
        assert result["findings"] == []
        assert result["llm_review"]["ok"] is False
        assert "llm_review_warning" in result

    @pytest.mark.asyncio
    async def test_llm_needs_review_does_not_override_deterministic_pass(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class FakeDialogueService:
            def __init__(self, settings: object) -> None:
                self.settings = settings

            async def invoke_role_dialogue(self, command: object) -> object:
                return SimpleNamespace(
                    ok=True,
                    status="ok",
                    content='{"verdict":"NEEDS_REVIEW","findings":["too small"],"summary":"review"}',
                    metadata={},
                    error_code=None,
                    error_message=None,
                )

        monkeypatch.setattr(
            "polaris.cells.llm.dialogue.public.service.LlmDialogueService",
            FakeDialogueService,
        )
        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-llm", enable_llm_audit=True)

        async def _deterministic_pass(**_kwargs: object) -> object:
            return SimpleNamespace(
                audit_id="audit-pass",
                verdict="PASS",
                issues=[],
                metrics={"files_audited": 1},
            )

        monkeypatch.setattr(consumer._qa_svc, "audit_task", _deterministic_pass)

        result = await consumer._run_qa_audit_async(
            "task-llm-review",
            {"run_id": "run-llm", "title": "Review target", "target_files": ["main.py"]},
        )

        assert result["verdict"] == "PASS"
        assert result["findings"] == []
        assert result["llm_review"]["verdict"] == "NEEDS_REVIEW"
        assert result["llm_review_warning"] == ["[llm] too small"]

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_pass_verdict_acks_resolved(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        job_token = _qa_job_token(run_id="run-qa-pass", token_id="token-qa-pass")

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-1"
        claim_result.lease_token = "lease-1"
        claim_result.payload = {"title": "QA task", "job_token": job_token}

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.acknowledge_task_stage.return_value = MagicMock(ok=True, status="resolved")

        consumer = QAConsumer(workspace="/test", worker_id="qa-1")
        with (
            patch.object(
                consumer,
                "_run_qa_audit",
                return_value={"verdict": "PASS", "audit_id": "a1", "findings": [], "metrics": {}},
            ),
            patch.object(qa_consumer_module, "append_run_ledger_event") as append_event,
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["status"] == "resolved"

        ack_call = mock_svc.acknowledge_task_stage.call_args[0][0]
        assert ack_call.terminal_status == "resolved"
        assert ack_call.next_stage is None
        assert ack_call.metadata["job_token"]["token_id"] == "token-qa-pass"
        assert ack_call.metadata["contract_hash"] == "contract-hash-qa"
        ledger_command = append_event.call_args[0][0]
        assert ledger_command.run_id == "run-qa-pass"
        assert ledger_command.event["event_type"] == "gate_evaluated"
        assert ledger_command.event["stage"] == "qa"
        assert ledger_command.event["gate"]["name"] == "qa_verdict"
        assert ledger_command.event["gate"]["ok"] is True
        assert ledger_command.event["job_token"]["token_id"] == "token-qa-pass"
        assert ledger_command.event["physical_evidence"]["verdict"] == "PASS"
        assert ledger_command.event["physical_evidence"]["modalities"]["qa"]["ok"] is True

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_requeue_exec_verdict_routes_to_pending_exec(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        job_token = _qa_job_token(run_id="run-qa-requeue", token_id="token-qa-requeue")

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-2"
        claim_result.lease_token = "lease-2"
        claim_result.payload = {"title": "QA task", "job_token": job_token}

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_exec")

        consumer = QAConsumer(workspace="/test", worker_id="qa-2")
        with (
            patch.object(
                consumer,
                "_run_qa_audit",
                return_value={
                    "verdict": "REQUEUE_EXEC",
                    "audit_id": "a2",
                    "findings": ["missing artifact evidence"],
                    "metrics": {},
                },
            ),
            patch.object(qa_consumer_module, "append_run_ledger_event") as append_event,
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["status"] == "pending_exec"

        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_exec"
        assert "missing artifact evidence" in fail_call.error_message
        assert fail_call.metadata["job_token"]["token_id"] == "token-qa-requeue"
        ledger_command = append_event.call_args[0][0]
        assert ledger_command.run_id == "run-qa-requeue"
        assert ledger_command.event["gate"]["name"] == "qa_verdict"
        assert ledger_command.event["gate"]["ok"] is False
        assert ledger_command.event["job_token"]["token_id"] == "token-qa-requeue"
        assert ledger_command.event["physical_evidence"]["next_stage"] == "pending_exec"
        assert ledger_command.event["physical_evidence"]["findings_count"] == 1

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_requeue_design_verdict_preserves_findings_as_last_failure(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-design"
        claim_result.lease_token = "lease-design"
        claim_result.payload = {"title": "QA task"}

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_design")

        consumer = QAConsumer(workspace="/test", worker_id="qa-design")
        with patch.object(
            consumer,
            "_run_qa_audit",
            return_value={
                "verdict": "REQUEUE_DESIGN",
                "audit_id": "a-design",
                "findings": ["[error] blueprint lacks acceptance criteria"],
            },
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["status"] == "pending_design"
        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_design"
        assert "acceptance criteria" in fail_call.error_message

    def test_findings_bounce_cap_survives_new_consumer_instances(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("KERNELONE_QA_FINDINGS_MAX_BOUNCES", "2")
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service = TaskMarketService()
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=str(workspace),
                trace_id="trace-bounce",
                run_id="run-bounce",
                task_id="task-bounce",
                stage="pending_qa",
                source_role="director",
                payload={"title": "QA task"},
                max_attempts=5,
            )
        )
        monkeypatch.setattr(qa_consumer_module, "get_task_market_service", lambda: service)
        audit = {
            "verdict": "FAIL",
            "audit_id": "audit-bounce",
            "findings": ["[error] main.js: unsatisfiable critique"],
        }

        def _qa_pass(worker_id: str) -> dict[str, object]:
            consumer = QAConsumer(workspace=str(workspace), worker_id=worker_id)
            with patch.object(consumer, "_run_qa_audit", return_value=audit):
                return consumer.poll_once()[0]

        def _director_moves_back_to_qa(worker_id: str) -> None:
            claim = service.claim_work_item(
                ClaimTaskWorkItemCommandV1(
                    workspace=str(workspace),
                    stage="pending_exec",
                    worker_id=worker_id,
                    worker_role="director",
                )
            )
            assert claim.ok is True
            service.acknowledge_task_stage(
                AcknowledgeTaskStageCommandV1(
                    workspace=str(workspace),
                    task_id="task-bounce",
                    lease_token=claim.lease_token,
                    next_stage="pending_qa",
                    summary="Director retried after QA findings",
                )
            )

        assert _qa_pass("qa-1")["reason"] == "qa_findings_requeued"
        _director_moves_back_to_qa("director-1")
        assert _qa_pass("qa-2")["reason"] == "qa_findings_requeued"
        _director_moves_back_to_qa("director-2")

        third_result = _qa_pass("qa-3")

        assert third_result.get("reason") != "qa_findings_requeued"
        status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
        row = {item["task_id"]: item for item in status.items}["task-bounce"]
        assert row["status"] == "rejected"
        counters = row["payload"].get("feedback_counters")
        assert counters == {"qa_findings_to_pending_exec": 2}

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

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_audit_uses_director_changed_files_instead_of_target_files(
        self,
        mock_get_svc: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_get_svc.return_value = MagicMock()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "good.py").write_text("x = 1\n", encoding="utf-8")
        (src_dir / "bad.py").write_text("def broken(\n", encoding="utf-8")

        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-evidence")
        result = consumer._run_qa_audit(
            "task-qa-evidence",
            {
                "title": "Implement code",
                "blueprint_id": "bp-evidence",
                "target_files": ["src/bad.py"],
                "changed_files": ["src/good.py"],
            },
        )

        assert result["verdict"] == "PASS"
        assert result["metrics"]["files_audited"] == 1

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_verified_existing_scope_uses_existing_paths_without_changed_files(
        self,
        mock_get_svc: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_get_svc.return_value = MagicMock()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "good.py").write_text("x = 1\n", encoding="utf-8")
        (src_dir / "bad.py").write_text("def broken(\n", encoding="utf-8")

        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-existing")
        result = consumer._run_qa_audit(
            "task-qa-existing",
            {
                "title": "Implement code",
                "blueprint_id": "bp-existing",
                "target_files": ["src/bad.py"],
                "changed_files": [],
                "director_evidence_status": "verified_existing_workspace_scope",
                "director_adapter": {
                    "success": True,
                    "materialization_mode": "verified_existing_workspace_scope",
                    "existing_contract_evidence": {
                        "ok": True,
                        "existing_paths": ["src/good.py"],
                    },
                },
            },
        )

        assert result["verdict"] == "PASS"
        assert result["metrics"]["files_audited"] == 1

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_code_task_without_director_changed_files_routes_to_exec(
        self,
        mock_get_svc: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("x = 1\n", encoding="utf-8")

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-no-evidence"
        claim_result.lease_token = "lease-qa-no-evidence"
        claim_result.payload = {
            "title": "Implement code",
            "blueprint_id": "bp-no-evidence",
            "target_files": ["src/main.py"],
            "changed_files": [],
        }

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_exec")

        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-no-evidence")
        results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["status"] == "pending_exec"
        assert results[0]["verdict"] == "FAIL"

        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_exec"
        assert "Director changed_files evidence is required" in fail_call.error_message


class TestContractGate:
    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_rejects_placeholder_package_scripts(self, mock_get_svc: MagicMock, tmp_path: Path) -> None:
        mock_get_svc.return_value = MagicMock()
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc","start":"node dist/index.js","test":"echo \\"No tests yet\\" && exit 0"}}\n',
            encoding="utf-8",
        )

        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-contract")
        msg = consumer._run_contract_gate(
            {
                "construction_step": {"target_file": "package.json"},
                "acceptance_criteria": ["package_scripts"],
            }
        )

        assert msg
        assert "package script gate failed" in msg
        assert "placeholder" in msg

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_rejects_empty_package_scripts_when_required(self, mock_get_svc: MagicMock, tmp_path: Path) -> None:
        mock_get_svc.return_value = MagicMock()
        (tmp_path / "package.json").write_text('{"scripts":{}}\n', encoding="utf-8")

        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-contract")
        msg = consumer._run_contract_gate(
            {
                "construction_step": {"target_file": "package.json"},
                "acceptance_criteria": ["package_scripts"],
            }
        )

        assert msg
        assert "package script gate failed" in msg
        assert "no scripts" in msg

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_rejects_manifest_only_verify_script_for_declared_checks(
        self, mock_get_svc: MagicMock, tmp_path: Path
    ) -> None:
        mock_get_svc.return_value = MagicMock()
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (tmp_path / "package.json").write_text(
            (
                '{"scripts":{"build":"node scripts/verify.js",'
                '"start":"node scripts/verify.js","test":"node scripts/verify.js"}}\n'
            ),
            encoding="utf-8",
        )
        (scripts_dir / "verify.js").write_text(
            "\n".join(
                [
                    "const fs = require('fs');",
                    "const pkg = JSON.parse(fs.readFileSync('package.json', 'utf-8'));",
                    "if (!pkg.name || !pkg.version || !pkg.scripts) process.exit(1);",
                    "console.log('PASS');",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-contract")
        msg = consumer._run_contract_gate(
            {
                "title": "编写验收脚本 scripts/verify.js 输出 PASS/FAIL 并校验核心规则",
                "construction_step": {"target_file": "scripts/verify.js"},
                "acceptance_criteria": [
                    "scripts/verify.js 运行后输出 PASS/FAIL，并校验 ts_syntax、package_scripts、min_files:3、content_any:firefly|flower|moon|humidity"
                ],
            }
        )

        assert msg
        assert "verification script gate failed" in msg
        assert "ts_syntax" in msg
        assert "min_files:3" in msg
        assert "content_any:firefly|flower|moon|humidity" in msg

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_rejects_manifest_only_verify_script_for_multilanguage_declared_checks(
        self, mock_get_svc: MagicMock, tmp_path: Path
    ) -> None:
        mock_get_svc.return_value = MagicMock()
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "verify.js").write_text(
            "const fs = require('fs');\n"
            "const pkg = JSON.parse(fs.readFileSync('package.json', 'utf-8'));\n"
            "if (!pkg.name) process.exit(1);\n"
            "console.log('PASS');\n",
            encoding="utf-8",
        )
        (tmp_path / "package.json").write_text(
            '{"name":"demo","scripts":{"test":"node scripts/verify.js"}}\n', encoding="utf-8"
        )

        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-contract")
        msg = consumer._run_contract_gate(
            {
                "title": "创建 scripts/verify.js 多语言验收脚本",
                "construction_step": {"target_file": "scripts/verify.js"},
                "acceptance_criteria": [
                    (
                        "scripts/verify.js 必须校验 js_syntax、go_compile、rust_compile、"
                        "cpp_compile、java_compile、runnable_any、real_run"
                    )
                ],
                "changed_files": ["scripts/verify.js"],
            }
        )

        assert msg
        assert "js_syntax" in msg
        assert "go_compile" in msg
        assert "rust_compile" in msg
        assert "cpp_compile" in msg
        assert "java_compile" in msg
        assert "runnable_any" in msg
        assert "real_run" in msg

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_rejects_manifest_only_verify_script_when_contract_was_split_into_signatures(
        self, mock_get_svc: MagicMock, tmp_path: Path
    ) -> None:
        mock_get_svc.return_value = MagicMock()
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (tmp_path / "package.json").write_text(
            (
                '{"scripts":{"build":"node scripts/verify.js",'
                '"start":"node scripts/verify.js","test":"node scripts/verify.js"}}\n'
            ),
            encoding="utf-8",
        )
        (scripts_dir / "verify.js").write_text(
            "const fs = require('fs');\n"
            "const pkg = JSON.parse(fs.readFileSync('package.json', 'utf-8'));\n"
            "if (!pkg.scripts) process.exit(1);\n"
            "console.log('PASS');\n",
            encoding="utf-8",
        )

        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-contract")
        msg = consumer._run_contract_gate(
            {
                "title": "创建 scripts/verify.js — 自动验证核心规则",
                "construction_step": {
                    "target_file": "scripts/verify.js",
                    "signatures": [
                        "function verifyTsSyntax()",
                        "function verifyPackageScripts()",
                        "function verifyContentExists()",
                        "function verifyFileCount()",
                        "function main()",
                    ],
                },
                "changed_files": ["scripts/verify.js"],
            }
        )

        assert msg
        assert "ts_syntax" in msg
        assert "min_files:3" in msg
        assert "content_any:firefly|flower|moon|humidity" in msg

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_rejects_verify_script_that_recursively_invokes_itself(
        self, mock_get_svc: MagicMock, tmp_path: Path
    ) -> None:
        mock_get_svc.return_value = MagicMock()
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (tmp_path / "package.json").write_text(
            (
                '{"scripts":{"build":"node scripts/verify.js",'
                '"start":"node scripts/verify.js","test":"node scripts/verify.js"}}\n'
            ),
            encoding="utf-8",
        )
        (scripts_dir / "verify.js").write_text(
            "const { execSync } = require('child_process');\n"
            "execSync('node scripts/verify.js');\n"
            "console.log('PASS');\n",
            encoding="utf-8",
        )

        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-contract")
        msg = consumer._run_contract_gate(
            {
                "construction_step": {
                    "target_file": "scripts/verify.js",
                    "signatures": ["function verifyTsSyntax()"],
                },
                "changed_files": ["scripts/verify.js"],
            }
        )

        assert msg
        assert "recursively invokes itself" in msg

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_contract_gate_failure_requeues_to_pending_exec(self, mock_get_svc: MagicMock, tmp_path: Path) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        (tmp_path / "package.json").write_text(
            '{"scripts":{"test":"echo \\"No tests yet\\" && exit 0"}}\n',
            encoding="utf-8",
        )
        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-contract"
        claim_result.lease_token = "lease-contract"
        claim_result.payload = {
            "title": "Validate package scripts",
            "construction_step": {"target_file": "package.json"},
            "acceptance_criteria": ["package_scripts"],
            "changed_files": ["package.json"],
        }
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_exec")

        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-contract")
        with patch.object(consumer, "_run_qa_audit", return_value={"verdict": "PASS", "audit_id": "a"}):
            results = consumer.poll_once()

        assert results[0]["reason"] == "contract_gate_failed"
        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.error_code == "QA_contract_gate_failed"
        assert fail_call.requeue_stage == "pending_exec"
        assert "placeholder" in fail_call.error_message


class TestSyntaxGate:
    """I3-r18 fail-closed syntax gate: a non-parsing target is rejected, not shipped."""

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_rejects_broken_python_target(self, mock_get_svc: MagicMock, tmp_path: Path) -> None:
        # py_compile is always available, so this runs everywhere.
        mock_get_svc.return_value = MagicMock()
        (tmp_path / "mod.py").write_text("def f(:\n    pass\n", encoding="utf-8")
        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-1")
        msg = consumer._run_syntax_gate({"construction_step": {"target_file": "mod.py"}})
        assert msg
        assert "语法检查失败" in msg

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_clean_python_target_passes(self, mock_get_svc: MagicMock, tmp_path: Path) -> None:
        mock_get_svc.return_value = MagicMock()
        (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-1")
        assert consumer._run_syntax_gate({"construction_step": {"target_file": "mod.py"}}) == ""

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_missing_file_not_blocked(self, mock_get_svc: MagicMock, tmp_path: Path) -> None:
        mock_get_svc.return_value = MagicMock()
        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-1")
        assert consumer._run_syntax_gate({"construction_step": {"target_file": "absent.py"}}) == ""

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_broken_js_target_rejected_with_line(self, mock_get_svc: MagicMock, tmp_path: Path) -> None:
        import shutil

        import pytest

        if shutil.which("node") is None:
            pytest.skip("node not available")
        mock_get_svc.return_value = MagicMock()
        (tmp_path / "main.js").write_text("bricks.push({\n    alive: true;\n});\n", encoding="utf-8")
        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-1")
        msg = consumer._run_syntax_gate({"construction_step": {"target_file": "main.js"}})
        assert msg
        assert "main.js" in msg
        assert "SyntaxError" in msg or "Unexpected" in msg

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_broken_ts_target_rejected_with_tsc_error(self, mock_get_svc: MagicMock, tmp_path: Path) -> None:
        import shutil

        import pytest

        if shutil.which("tsc") is None:
            pytest.skip("tsc not available")
        mock_get_svc.return_value = MagicMock()
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "flower.ts").write_text(
            "\n".join(
                [
                    "interface FlowerState {",
                    "  wilted: boolean;",
                    "}",
                    "const state: FlowerState = {",
                    "  wilted: false;",
                    "};",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-1")
        msg = consumer._run_syntax_gate({"construction_step": {"target_file": "src/flower.ts"}})
        assert msg
        assert "flower.ts" in msg
        assert "TS1005" in msg or "',' expected" in msg
