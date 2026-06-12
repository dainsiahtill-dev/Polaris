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
