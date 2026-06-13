"""QA-stage machine verify for fission steps (ce-blueprint-tasks/1).

Live I3-r9: a step whose verify began with ``test -f ./readme.md`` passed QA
with score 10 while readme.md did not exist — the generic audit is blind to
the step contract. The verify is the step's acceptance ground truth and must
gate the verdict.
"""

from __future__ import annotations

from pathlib import Path

from polaris.cells.qa.audit_verdict.internal.qa_consumer import QAConsumer
from polaris.cells.runtime.task_market.public.contracts import (
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service


class TestRunStepVerify:
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

    def test_failing_verify_returns_teaching_message(self, tmp_path: Path) -> None:
        consumer = self._consumer(tmp_path)
        payload = {"construction_step": {"verify": "test -f ./absent.md"}}
        failure = consumer._run_step_verify(payload)
        assert "step verify failed" in failure
        assert "absent.md" in failure

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
        assert "failing clause [3/3]:" in failure
        assert "wc -l" in failure.split("failing clause", 1)[1]

    def test_quoted_and_inside_pattern_aborts_clause_diagnosis(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("plain\n", encoding="utf-8")
        consumer = self._consumer(tmp_path)
        payload = {"construction_step": {"verify": "grep -q 'a && b' ./a.txt && test -f ./a.txt"}}
        failure = consumer._run_step_verify(payload)
        assert "step verify failed" in failure
        assert "failing clause" not in failure

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
            assert "failing clause" not in failure, verify

    def test_clause_detail_precedes_full_command_in_message(self, tmp_path: Path) -> None:
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        consumer = self._consumer(tmp_path)
        verify = 'test -f ./style.css && [ "$(wc -l < ./style.css)" -le 120 ]'
        failure = consumer._run_step_verify({"construction_step": {"verify": verify}})
        assert failure.index("failing clause") < failure.index("full:")


def test_failing_step_verify_requeues_to_pending_exec(tmp_path: Path) -> None:
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
    assert results and results[0]["reason"] == "step_verify_failed"

    status = service.query_status(QueryTaskMarketStatusV1(workspace=str(workspace)))
    row = {item["task_id"]: item for item in status.items}["PM-1-S1"]
    assert row["status"] == "pending_exec"
    assert row["stage"] == "pending_exec"


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
