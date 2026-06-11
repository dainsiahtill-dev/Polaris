"""G4-pattern wiring test: ClaimQaTaskCommandV1 → task market targeted claim.

The contract was declared with no consumer; the handler delegates to the same
market service the polling QAConsumer uses, with task_id-targeted claiming.
"""

from __future__ import annotations

from pathlib import Path

from polaris.cells.qa.audit_verdict.public.contracts import ClaimQaTaskCommandV1
from polaris.cells.qa.audit_verdict.public.service import claim_qa_task
from polaris.cells.runtime.task_market.public.contracts import TaskWorkItemResultV1


class TestClaimQaTask:
    def test_claim_on_empty_market_fails_closed(self, tmp_path: Path) -> None:
        command = ClaimQaTaskCommandV1(
            task_id="task-404",
            workspace=str(tmp_path),
            worker_id="qa_worker_test",
        )

        result = claim_qa_task(command)

        assert isinstance(result, TaskWorkItemResultV1)
        assert result.ok is False

    def test_claim_routes_task_identity_through(self, tmp_path: Path) -> None:
        command = ClaimQaTaskCommandV1(
            task_id="task-405",
            workspace=str(tmp_path),
            worker_id="qa_worker_test",
        )

        result = claim_qa_task(command)

        # Even a failed targeted claim must reference the requested task
        # (or carry an explicit reason) — never silently claim something else.
        assert result.ok is False
        assert result.task_id in ("", "task-405")
