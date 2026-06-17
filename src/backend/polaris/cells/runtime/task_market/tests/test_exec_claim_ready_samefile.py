"""Same-file serialization in _exec_claim_ready (review finding 1, I3-r29).

A fill chain (and any cross-parent edit_on_prior writer) must wait for the PRIOR
same-file writer to be fully RESOLVED, not merely at QA — else a QA bounce of the
predecessor would put two writers on the same file concurrently.
"""

from __future__ import annotations

from polaris.cells.runtime.task_market.internal.models import TaskWorkItemRecord, now_epoch
from polaris.cells.runtime.task_market.internal.service import TaskMarketService


def _item(
    task_id: str,
    *,
    target: str,
    stage: str = "pending_exec",
    status: str = "pending_exec",
    depends_on: tuple[str, ...] = (),
) -> TaskWorkItemRecord:
    return TaskWorkItemRecord(
        task_id=task_id,
        trace_id="t",
        run_id="r",
        workspace="/ws",
        stage=stage,
        status=status,
        priority="normal",
        payload={"construction_step": {"target_file": target}},
        metadata={},
        version=1,
        attempts=0,
        max_attempts=3,
        lease_token="",
        lease_expires_at=0.0,
        claimed_by="",
        claimed_role="",
        last_error={},
        depends_on=list(depends_on),
        is_leaf=True,
    )


class TestExecClaimReadySameFile:
    def test_same_file_dep_at_qa_blocks_dependent(self) -> None:
        fill1 = _item("PM-1-S4-fill1", target="main.js", stage="pending_qa", status="pending_qa")
        fill2 = _item("PM-1-S4-fill2", target="main.js", depends_on=("PM-1-S4-fill1",))
        items = {fill1.task_id: fill1, fill2.task_id: fill2}
        assert TaskMarketService._exec_claim_ready(fill2, items) is False

    def test_same_file_dep_resolved_unblocks(self) -> None:
        fill1 = _item("PM-1-S4-fill1", target="main.js", stage="done", status="resolved")
        fill2 = _item("PM-1-S4-fill2", target="main.js", depends_on=("PM-1-S4-fill1",))
        items = {fill1.task_id: fill1, fill2.task_id: fill2}
        assert TaskMarketService._exec_claim_ready(fill2, items) is True

    def test_same_file_active_writer_blocks_even_without_declared_dep(self) -> None:
        active = _item("PM-1-S4-a", target="main.js", stage="pending_exec", status="in_execution")
        active.lease_token = "active-lease"
        active.lease_expires_at = now_epoch() + 60
        pending = _item("PM-1-S4-b", target="main.js")
        items = {active.task_id: active, pending.task_id: pending}

        assert TaskMarketService._exec_claim_ready(pending, items) is False

    def test_same_file_expired_writer_does_not_block_recovery(self) -> None:
        expired = _item("PM-1-S4-a", target="main.js", stage="pending_exec", status="in_execution")
        expired.lease_token = "expired-lease"
        expired.lease_expires_at = now_epoch() - 60
        pending = _item("PM-1-S4-b", target="main.js")
        items = {expired.task_id: expired, pending.task_id: pending}

        assert TaskMarketService._exec_claim_ready(pending, items) is True

    def test_different_file_dep_at_qa_keeps_fastpath(self) -> None:
        # A dependency on a DIFFERENT file may still satisfy at pending_qa (throughput).
        dep = _item("PM-1-S2", target="index.html", stage="pending_qa", status="pending_qa")
        step = _item("PM-1-S4", target="main.js", depends_on=("PM-1-S2",))
        items = {dep.task_id: dep, step.task_id: step}
        assert TaskMarketService._exec_claim_ready(step, items) is True

    def test_normalizes_dot_slash_when_comparing_targets(self) -> None:
        dep = _item("PM-1-S4-fill1", target="./main.js", stage="pending_qa", status="pending_qa")
        step = _item("PM-1-S4-fill2", target="main.js", depends_on=("PM-1-S4-fill1",))
        items = {dep.task_id: dep, step.task_id: step}
        assert TaskMarketService._exec_claim_ready(step, items) is False
