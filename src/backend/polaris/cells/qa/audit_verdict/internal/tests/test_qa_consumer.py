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
    _canonical_route_from_projection,
)


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


def _canonical_engine_payload(
    *,
    verdict: str,
    next_stage: str = "",
    terminal_status: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": "qa.verdict_envelope.v1",
        "verdict": verdict,
        "ok": verdict == "PASS",
        "next_stage": next_stage,
        "terminal_status": terminal_status,
        "ledger": {"source": "run_ledger_projection", "available": True},
        "evidence": {
            "conflict_matrix": {
                "schema_version": "qa.verdict_conflict_matrix.v1",
                "conflicts": [],
            }
        },
        "content_hash": "canonical-envelope-hash",
        "classification": {
            "failure_class": None if verdict == "PASS" else "IMPLEMENTATION_DEFECT",
            "route": terminal_status or next_stage,
        },
    }


def _final_commit_receipt(*, verdict: str) -> SimpleNamespace:
    return SimpleNamespace(
        to_dict=lambda: {
            "run_id": "run-qa",
            "append_id": f"append-final-{verdict.lower()}",
            "event_hash": f"hash-final-{verdict.lower()}",
            "envelope_hash": "canonical-envelope-hash",
            "verdict": verdict,
        }
    )


def test_self_declared_authoritative_wrapper_cannot_resolve_task() -> None:
    route = _canonical_route_from_projection(
        engine_payload={
            "authoritative": True,
            "verdict": "PASS",
            "ok": True,
            "terminal_status": "resolved",
        }
    )

    assert route == ("BLOCKED", "pending_qa", "")


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
        call_order: list[str] = []

        def record_transition(_command: object) -> MagicMock:
            call_order.append("transition")
            return MagicMock(ok=True, status="resolved")

        def record_final_verdict_commit(**_kwargs: object) -> object:
            call_order.append("final_verdict_commit")
            return _final_commit_receipt(verdict="PASS")

        mock_svc.acknowledge_task_stage.side_effect = record_transition

        consumer = QAConsumer(workspace="/test", worker_id="qa-1")
        with (
            patch.object(
                consumer,
                "_run_qa_audit",
                return_value={"verdict": "PASS", "audit_id": "a1", "findings": [], "metrics": {}},
            ),
            patch.object(
                consumer,
                "_build_canonical_verdict_projection",
                return_value=_canonical_engine_payload(verdict="PASS", terminal_status="resolved"),
            ),
            patch.object(qa_consumer_module, "commit_qa_evidence") as commit_evidence,
            patch.object(qa_consumer_module, "commit_qa_verdict") as commit_verdict,
        ):
            commit_evidence.return_value.to_dict.return_value = {
                "run_id": "run-qa-pass",
                "append_id": "append-pass",
                "event_hash": "hash-pass",
            }
            commit_verdict.side_effect = record_final_verdict_commit
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["status"] == "resolved"

        ack_call = mock_svc.acknowledge_task_stage.call_args[0][0]
        assert ack_call.terminal_status == "resolved"
        assert ack_call.next_stage is None
        assert ack_call.metadata["job_token"]["token_id"] == "token-qa-pass"
        assert ack_call.metadata["contract_hash"] == "contract-hash-qa"
        assert ack_call.metadata["qa_verdict_projection"]["verdict"] == "PASS"
        commit_kwargs = commit_evidence.call_args.kwargs
        assert commit_kwargs["run_id"] == "run-qa-pass"
        assert commit_kwargs["gate_name"] == "qa_evidence"
        assert commit_kwargs["ok"] is True
        assert commit_kwargs["job_token"]["token_id"] == "token-qa-pass"
        assert commit_kwargs["verdict"] == "PASS"
        assert call_order == ["final_verdict_commit", "transition"]
        final_kwargs = commit_verdict.call_args.kwargs
        assert final_kwargs["envelope"]["verdict"] == "PASS"
        assert final_kwargs["evidence_commit_receipt"]["append_id"] == "append-pass"
        assert ack_call.metadata["qa_verdict_commit_receipt"]["verdict"] == "PASS"

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_terminal_verdict_without_run_ledger_evidence_requeues_qa(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-no-ledger"
        claim_result.lease_token = "lease-no-ledger"
        claim_result.payload = {"title": "QA task without job token"}

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_qa")

        consumer = QAConsumer(workspace="/test", worker_id="qa-no-ledger")
        with (
            patch.object(
                consumer,
                "_run_qa_audit",
                return_value={"verdict": "PASS", "audit_id": "a-no-ledger", "findings": [], "metrics": {}},
            ),
            patch.object(qa_consumer_module, "commit_qa_evidence") as commit_evidence,
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is False
        assert results[0]["status"] == "pending_qa"
        assert results[0]["reason"] == "qa_verdict_commit_failed"
        commit_evidence.assert_not_called()
        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_qa"
        assert fail_call.error_code == "QA_verdict_commit_failed"
        assert fail_call.metadata["qa_verdict_projection"]["verdict"] == "BLOCKED"

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_final_verdict_append_failure_never_transitions(
        self,
        mock_get_svc: MagicMock,
    ) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        claim_result = MagicMock(
            ok=True,
            task_id="task-qa-final-append-fails",
            lease_token="lease-final-append-fails",
            payload={
                "title": "QA task",
                "job_token": _qa_job_token(run_id="run-qa-final-append-fails"),
            },
        )
        mock_svc.claim_work_item.side_effect = [claim_result, MagicMock(ok=False)]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_qa")
        consumer = QAConsumer(workspace="/test", worker_id="qa-final-append-fails")

        with (
            patch.object(
                consumer,
                "_run_qa_audit",
                return_value={"verdict": "PASS", "findings": [], "metrics": {}},
            ),
            patch.object(
                consumer,
                "_build_canonical_verdict_projection",
                return_value=_canonical_engine_payload(
                    verdict="PASS",
                    terminal_status="resolved",
                ),
            ),
            patch.object(qa_consumer_module, "commit_qa_evidence") as commit_evidence,
            patch.object(
                qa_consumer_module,
                "commit_qa_verdict",
                side_effect=RuntimeError("ledger unavailable"),
            ),
        ):
            commit_evidence.return_value.to_dict.return_value = {
                "run_id": "run-qa-final-append-fails",
                "append_id": "append-evidence",
                "event_hash": "hash-evidence",
            }
            results = consumer.poll_once()

        assert results[0]["reason"] == "qa_verdict_commit_failed"
        assert results[0]["status"] == "pending_qa"
        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_qa"
        assert fail_call.error_code == "QA_verdict_commit_failed"

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
            patch.object(
                consumer,
                "_build_canonical_verdict_projection",
                return_value=_canonical_engine_payload(verdict="FAIL", next_stage="pending_exec"),
            ),
            patch.object(qa_consumer_module, "commit_qa_evidence") as commit_evidence,
            patch.object(qa_consumer_module, "commit_qa_verdict") as commit_verdict,
        ):
            commit_evidence.return_value.to_dict.return_value = {
                "run_id": "run-qa-requeue",
                "append_id": "append-requeue",
                "event_hash": "hash-requeue",
            }
            commit_verdict.return_value = _final_commit_receipt(verdict="FAIL")
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is False
        assert results[0]["status"] == "pending_exec"

        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_exec"
        assert "missing artifact evidence" in fail_call.error_message
        assert fail_call.metadata["job_token"]["token_id"] == "token-qa-requeue"
        commit_kwargs = commit_evidence.call_args.kwargs
        assert commit_kwargs["run_id"] == "run-qa-requeue"
        assert commit_kwargs["gate_name"] == "qa_evidence"
        assert commit_kwargs["ok"] is False
        assert commit_kwargs["job_token"]["token_id"] == "token-qa-requeue"
        assert len(commit_kwargs["audit_result"]["findings"]) == 1

    @patch("polaris.cells.qa.audit_verdict.internal.qa_consumer.get_task_market_service")
    def test_requeue_design_verdict_preserves_findings_as_last_failure(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-qa-design"
        claim_result.lease_token = "lease-design"
        claim_result.payload = {
            "title": "QA task",
            "job_token": _qa_job_token(run_id="run-qa-design"),
        }

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_design")

        consumer = QAConsumer(workspace="/test", worker_id="qa-design")
        with (
            patch.object(
                consumer,
                "_run_qa_audit",
                return_value={
                    "verdict": "REQUEUE_DESIGN",
                    "audit_id": "a-design",
                    "findings": ["[error] blueprint lacks acceptance criteria"],
                },
            ),
            patch.object(
                consumer,
                "_build_canonical_verdict_projection",
                return_value=_canonical_engine_payload(verdict="FAIL", next_stage="pending_design"),
            ),
            patch.object(qa_consumer_module, "commit_qa_evidence") as commit_evidence,
            patch.object(qa_consumer_module, "commit_qa_verdict") as commit_verdict,
        ):
            commit_evidence.return_value.to_dict.return_value = {
                "run_id": "run-qa-design",
                "append_id": "append-design-evidence",
                "event_hash": "hash-design-evidence",
            }
            commit_verdict.return_value = _final_commit_receipt(verdict="FAIL")
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is False
        assert results[0]["status"] == "pending_design"
        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_design"
        assert "acceptance criteria" in fail_call.error_message

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
            "job_token": _qa_job_token(run_id="run-qa-no-evidence"),
        }

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_qa")

        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-no-evidence")
        with (
            patch.object(
                consumer,
                "_build_canonical_verdict_projection",
                return_value=_canonical_engine_payload(
                    verdict="BLOCKED",
                    next_stage="pending_qa",
                ),
            ),
            patch.object(qa_consumer_module, "commit_qa_evidence") as commit_evidence,
            patch.object(qa_consumer_module, "commit_qa_verdict") as commit_verdict,
        ):
            commit_evidence.return_value.to_dict.return_value = {
                "run_id": "run-qa-no-evidence",
                "append_id": "append-no-evidence",
                "event_hash": "hash-no-evidence",
            }
            commit_verdict.return_value = _final_commit_receipt(verdict="BLOCKED")
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is False
        assert results[0]["status"] == "pending_qa"
        assert results[0]["verdict"] == "BLOCKED"

        committed_audit = commit_evidence.call_args.kwargs["audit_result"]
        assert committed_audit["failure_class"] == "EXECUTION_EVIDENCE_MISSING"
        assert committed_audit["responsible_layer"] == "execution_control_plane"
        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_qa"
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
            "job_token": _qa_job_token(run_id="run-qa-contract"),
        }
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_exec")

        consumer = QAConsumer(workspace=str(tmp_path), worker_id="qa-contract")
        with (
            patch.object(
                consumer,
                "_build_canonical_verdict_projection",
                return_value=_canonical_engine_payload(
                    verdict="BLOCKED",
                    next_stage="pending_qa",
                ),
            ),
            patch.object(qa_consumer_module, "commit_qa_evidence") as commit_evidence,
            patch.object(qa_consumer_module, "commit_qa_verdict") as commit_verdict,
        ):
            commit_evidence.return_value.to_dict.return_value = {
                "run_id": "run-qa-contract",
                "append_id": "append-contract",
                "event_hash": "hash-contract",
            }
            commit_verdict.return_value = _final_commit_receipt(verdict="BLOCKED")
            results = consumer.poll_once()

        assert results[0]["reason"] == "canonical_qa_route"
        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.error_code == "QA_BLOCKED_canonical_route"
        assert fail_call.requeue_stage == "pending_qa"
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
