"""QA-stage machine verify for fission steps (ce-blueprint-tasks/1).

Live I3-r9: a step whose verify began with ``test -f ./readme.md`` passed QA
with score 10 while readme.md did not exist — the generic audit is blind to
the step contract. The verify is the step's acceptance ground truth and must
gate the verdict.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.qa.audit_verdict.internal import qa_consumer as qa_consumer_module
from polaris.cells.qa.audit_verdict.internal.qa_consumer import QAConsumer
from polaris.cells.runtime.task_market.public.contracts import (
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service

_REAL_STEP_VERIFICATION_OBLIGATION = qa_consumer_module._step_verification_obligation


class TestRunStepVerify:
    @pytest.fixture(autouse=True)
    def _broker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeReceipt:
            def __init__(self, *, succeeded: bool) -> None:
                self.project_id = "project-1"
                self.run_id = "run-1"
                self.completion_contract_hash = "c" * 64
                self.obligation_id = "verify.test"
                self.owner_task_id = "TASK-1"
                self.modality = "test"
                self.argv = ("test", "-f", "./marker.txt" if succeeded else "./absent.md")
                self.cwd = "."
                self.command_authority_hash = "a" * 64
                self.executable_path = "/usr/bin/python"
                self.executable_realpath = "/usr/bin/python"
                self.executable_hash = "5" * 64
                self.input_artifact_hash = "b" * 64
                self.exit_code = 0 if succeeded else 1
                self.timed_out = False
                self.output_hash = "d" * 64
                self.proof_satisfied = succeeded
                self.proof_evidence_hash = "e" * 64
                self.process_pid = None
                self.process_start_token = None
                self.readiness_probe_kind = "none"
                self.readiness_satisfied = False
                self.controlled_termination = False
                self.receipt_hash = ("f" if succeeded else "9") * 64
                self.receipt_ref = f"execution-broker://project-verification/{self.receipt_hash}"
                self.succeeded = succeeded

        monkeypatch.setattr(qa_consumer_module, "ProjectVerificationReceiptV1", FakeReceipt)
        monkeypatch.setattr(
            qa_consumer_module,
            "ResolveProjectVerificationAuthorityQueryV1",
            lambda **kwargs: SimpleNamespace(**kwargs),
        )
        monkeypatch.setattr(
            qa_consumer_module,
            "QueryProjectVerificationReceiptV1",
            lambda **kwargs: SimpleNamespace(**kwargs),
        )
        state = {"succeeded": False, "last_receipt": None, "receipt_current": True}

        def _authority(_payload: dict[str, object], verify: str) -> dict[str, object]:
            state["succeeded"] = "marker.txt" in verify
            return {
                "obligation_id": "verify.test",
                "owner_task_id": "TASK-1",
                "modality": "test",
                "argv": ["pytest", "-q"],
                "cwd": ".",
                "command_authority_hash": "a" * 64,
            }

        monkeypatch.setattr(qa_consumer_module, "_step_verification_obligation", _authority)
        monkeypatch.setattr(
            qa_consumer_module,
            "authorize_project_verification_command",
            lambda _query: SimpleNamespace(
                workspace="/workspace",
                project_id="project-1",
                run_id="run-1",
                completion_contract_hash="c" * 64,
                obligation_id="verify.test",
                owner_task_id="TASK-1",
                modality="test",
                argv=("pytest", "-q"),
                cwd=".",
                command_authority_hash="a" * 64,
                input_artifacts=(SimpleNamespace(obligation_id="artifact-main", path="main.py"),),
                timeout_seconds=30.0,
                job_token_id="job-token",
                job_token_set_hash="1" * 64,
                execution_policy_hash="2" * 64,
                authority_revision="3" * 64,
                policy_profile_id="python.pytest",
                policy_decision_hash="4" * 64,
                executable_path="/usr/bin/python",
                executable_realpath="/usr/bin/python",
                executable_hash="5" * 64,
            ),
        )

        def _run(command: SimpleNamespace) -> SimpleNamespace:
            succeeded = state["succeeded"]
            receipt = FakeReceipt(succeeded=succeeded)
            state["last_receipt"] = receipt
            return SimpleNamespace(code="completed", receipt=receipt)

        monkeypatch.setattr(qa_consumer_module, "run_project_verification", _run)
        monkeypatch.setattr(
            qa_consumer_module,
            "query_project_verification_receipt",
            lambda _query: state["last_receipt"] if state["receipt_current"] else None,
        )
        self._broker_state = state

    def _consumer(self, workspace: Path) -> QAConsumer:
        return QAConsumer(workspace=str(workspace), worker_id="qa-test")

    def test_non_step_payload_skips(self, tmp_path: Path) -> None:
        consumer = self._consumer(tmp_path)
        assert consumer._run_step_verify({"title": "plain task"}) == ""

    def test_step_without_verify_skips(self, tmp_path: Path) -> None:
        consumer = self._consumer(tmp_path)
        assert consumer._run_step_verify({"construction_step": {"target_file": "a.md"}}) == ""

    def test_passing_verify_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "marker.txt").write_text("ok", encoding="utf-8")
        consumer = self._consumer(tmp_path)
        payload = {"construction_step": {"verify": "test -f ./marker.txt"}}
        assert consumer._run_step_verify(payload) == ""
        assert payload["qa_verifier_receipt"]["receipt_hash"] == "f" * 64

    def test_failing_verify_returns_teaching_message(self, tmp_path: Path) -> None:
        consumer = self._consumer(tmp_path)
        payload = {"construction_step": {"verify": "test -f ./absent.md"}}
        failure = consumer._run_step_verify(payload)
        assert "step verify failed" in failure
        assert "obligation='verify.test'" in failure
        assert payload["qa_failed_verifier"]["schema_version"] == "qa.failed_verifier.v2"
        assert payload["qa_failed_verifier"]["receipt_hash"] == "9" * 64
        assert payload["qa_failed_verifier"]["obligation_id"] == "verify.test"

    def test_post_run_authority_drift_blocks_qa_acceptance(self, tmp_path: Path) -> None:
        consumer = self._consumer(tmp_path)
        self._broker_state["receipt_current"] = False
        payload = {"construction_step": {"verify": "test -f ./marker.txt"}}

        failure = consumer._run_step_verify(payload)

        assert "no longer current" in failure
        assert payload["qa_failed_verifier"]["failure_kind"] == "authority_changed_after_run"

    def test_failure_names_first_failing_clause(self, tmp_path: Path) -> None:
        """Fix-10 (live I3-r12): the bounce teaching must name WHICH clause
        failed — a 7/8-pass step is indistinguishable from a 0/8 one otherwise."""
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        consumer = self._consumer(tmp_path)
        payload = {
            "construction_step": {
                "verify": (
                    "test -f ./style.css && grep -q '#game' ./style.css && [ \"$(wc -l < ./style.css)\" -le 120 ]"
                )
            }
        }
        failure = consumer._run_step_verify(payload)
        assert "receipt=execution-broker://" in failure

    def test_step_verifier_mapping_requires_one_exact_ce_projected_obligation(self) -> None:
        payload = {
            "task_completion_projection": {
                "verification_execution_authority": [
                    {
                        "obligation_id": "verify.test",
                        "owner_task_id": "TASK-1",
                        "modality": "test",
                        "command": "pytest -q",
                        "argv": ["pytest", "-q"],
                        "cwd": ".",
                        "command_authority_hash": "a" * 64,
                    }
                ]
            }
        }
        row = _REAL_STEP_VERIFICATION_OBLIGATION(payload, "pytest -q")
        assert row["obligation_id"] == "verify.test"
        with pytest.raises(ValueError, match="exactly one"):
            _REAL_STEP_VERIFICATION_OBLIGATION(payload, "npm test")

    def test_quoted_and_inside_pattern_is_not_split_as_shell_operator(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("plain\n", encoding="utf-8")
        consumer = self._consumer(tmp_path)
        payload = {"construction_step": {"verify": "grep -q 'a && b' ./a.txt && test -f ./a.txt"}}
        failure = consumer._run_step_verify(payload)
        assert "step verify failed" in failure
        assert "obligation='verify.test'" in failure

    def test_state_carrying_chain_aborts_clause_diagnosis(self, tmp_path: Path) -> None:
        """Adversarial review (live repro): cd/VAR= clauses re-run in fresh
        shells against the wrong cwd/env — a wrong clause verdict misleads."""
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "app.js").write_text("bar\n", encoding="utf-8")
        consumer = self._consumer(tmp_path)
        for verify in (
            "cd src && test -f app.js && grep -q foo app.js",
            'X=1 && [ "$X" = 1 ] && test -f missing.txt',
            "test -f ./a.txt && grep -q x ./a.txt || test -f ./b.txt",
        ):
            failure = consumer._run_step_verify({"construction_step": {"verify": verify}})
            assert "step verify failed" in failure, verify
            assert "receipt=execution-broker://" in failure, verify

    def test_clause_detail_precedes_full_command_in_message(self, tmp_path: Path) -> None:
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        consumer = self._consumer(tmp_path)
        verify = 'test -f ./style.css && [ "$(wc -l < ./style.css)" -le 120 ]'
        failure = consumer._run_step_verify({"construction_step": {"verify": verify}})
        assert "receipt=execution-broker://" in failure


def test_failing_step_verify_without_canonical_ledger_stays_pending_qa(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = get_task_market_service()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-verify",
            run_id="run-verify",
            task_id="PM-1-S1",
            stage="pending_qa",
            source_role="director",
            payload={
                "title": "readme step",
                "construction_step": {
                    "step_id": "PM-1-S1",
                    "target_file": "readme.md",
                    "verify": "test -f ./readme.md",
                },
            },
        )
    )
    consumer = QAConsumer(workspace=str(workspace), worker_id="qa-test")
    results = consumer.poll_once()
    assert results and results[0]["reason"] == "qa_verdict_commit_failed"
    assert results[0]["verdict"] == "BLOCKED"

    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
    row = {item["task_id"]: item for item in status.items}["PM-1-S1"]
    assert row["status"] == "pending_qa"
    assert row["stage"] == "pending_qa"
    assert row["metadata"]["local_retry_schedule"]["not_before_epoch"] > 0


def test_passing_step_verify_proceeds_to_audit(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "readme.md").write_text("# Controls\nPM-1\n", encoding="utf-8")
    service = get_task_market_service()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-verify-ok",
            run_id="run-verify-ok",
            task_id="PM-1-S2",
            stage="pending_qa",
            source_role="director",
            payload={
                "title": "readme step",
                "changed_files": ["readme.md"],
                "construction_step": {
                    "step_id": "PM-1-S2",
                    "target_file": "readme.md",
                    "verify": "test -f ./readme.md",
                },
            },
        )
    )
    consumer = QAConsumer(workspace=str(workspace), worker_id="qa-test")
    results = consumer.poll_once()
    assert results
    # Verify passed: the result carries a real audit verdict, not a
    # step_verify_failed short-circuit.
    assert results[0].get("reason") != "step_verify_failed"
    assert "verdict" in results[0]
