from __future__ import annotations

import time

from polaris.cells.roles.kernel.internal.speculation.models import (
    SalvageDecision,
    ShadowTaskRecord,
    ShadowTaskState,
    ToolSpecPolicy,
)
from polaris.cells.roles.kernel.internal.speculation.salvage import SalvageGovernor


class TestSalvageGovernor:
    def _make_record(
        self,
        state: ShadowTaskState = ShadowTaskState.RUNNING,
        cost: str = "cheap",
        cancellability: str = "cooperative",
        elapsed_ms: float = 0.0,
        timeout_ms: int = 1000,
    ) -> ShadowTaskRecord:
        policy = ToolSpecPolicy(
            tool_name="read_file",
            side_effect="readonly",
            cost=cost,
            cancellability=cancellability,
            reusability="adoptable",
            speculate_mode="speculative_allowed",
            timeout_ms=timeout_ms,
        )
        started_at = time.monotonic() - (elapsed_ms / 1000.0)
        return ShadowTaskRecord(
            task_id="task_1",
            origin_turn_id="t1",
            origin_candidate_id="c1",
            tool_name="read_file",
            normalized_args={},
            spec_key="spec_1",
            env_fingerprint="fp",
            policy_snapshot=policy,
            state=state,
            started_at=started_at,
        )

    def test_completed_task_returns_cancel_now(self) -> None:
        governor = SalvageGovernor()
        record = self._make_record(state=ShadowTaskState.COMPLETED)
        assert governor.evaluate(record) == SalvageDecision.CANCEL_NOW

    def test_high_progress_returns_let_finish(self) -> None:
        governor = SalvageGovernor()
        # 90% elapsed => progress ~0.9
        record = self._make_record(elapsed_ms=900, timeout_ms=1000)
        assert governor.evaluate(record) == SalvageDecision.LET_FINISH_AND_CACHE

    def test_near_deadline_returns_let_finish(self) -> None:
        governor = SalvageGovernor(near_deadline_ratio=0.90)
        # 95% elapsed
        record = self._make_record(elapsed_ms=950, timeout_ms=1000)
        assert governor.evaluate(record) == SalvageDecision.LET_FINISH_AND_CACHE

    def test_low_progress_cheap_cooperative_returns_cancel_now(self) -> None:
        governor = SalvageGovernor()
        record = self._make_record(elapsed_ms=50, timeout_ms=1000, cost="cheap", cancellability="cooperative")
        assert governor.evaluate(record) == SalvageDecision.CANCEL_NOW

    def test_non_cancelable_returns_join(self) -> None:
        governor = SalvageGovernor()
        record = self._make_record(cancellability="non_cancelable")
        assert governor.evaluate(record) == SalvageDecision.JOIN_AUTHORITATIVE

    def test_expensive_low_progress_returns_cancel_now(self) -> None:
        governor = SalvageGovernor()
        record = self._make_record(cost="expensive", elapsed_ms=50, timeout_ms=1000)
        assert governor.evaluate(record) == SalvageDecision.CANCEL_NOW

    def test_running_medium_progress_default_join(self) -> None:
        governor = SalvageGovernor()
        # 50% elapsed, not high, not low (below 0.2 is low)
        record = self._make_record(elapsed_ms=500, timeout_ms=1000)
        assert governor.evaluate(record) == SalvageDecision.JOIN_AUTHORITATIVE

    def test_fast_tool_boost(self) -> None:
        governor = SalvageGovernor()
        # stat_file with 5% elapsed gets a +0.3 boost => 0.35 which is below high threshold
        record = self._make_record(elapsed_ms=50, timeout_ms=1000)
        record.tool_name = "stat_file"
        # With boost: 0.05 + 0.3 = 0.35, above 0.2 low threshold, so default JOIN
        assert governor.evaluate(record) == SalvageDecision.JOIN_AUTHORITATIVE

    def test_batch_evaluate(self) -> None:
        governor = SalvageGovernor()
        high_progress = self._make_record(elapsed_ms=900, timeout_ms=1000)
        high_progress.task_id = "task_high"
        low_progress = self._make_record(elapsed_ms=50, timeout_ms=1000)
        low_progress.task_id = "task_low"
        records = [high_progress, low_progress]
        decisions = governor.batch_evaluate(records)
        assert decisions["task_high"] == SalvageDecision.LET_FINISH_AND_CACHE
        assert decisions["task_low"] == SalvageDecision.CANCEL_NOW
