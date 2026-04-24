"""Tests for SalvageGovernor — cancel-or-salvage decision engine."""

from __future__ import annotations

import time

import pytest
from polaris.cells.roles.kernel.internal.speculation.models import (
    SalvageDecision,
    ShadowTaskRecord,
    ShadowTaskState,
    ToolSpecPolicy,
)
from polaris.cells.roles.kernel.internal.speculation.salvage import SalvageGovernor


def _policy(
    tool_name: str = "read_file",
    *,
    side_effect: str = "readonly",
    cost: str = "cheap",
    cancellability: str = "cooperative",
    reusability: str = "adoptable",
    speculate_mode: str = "speculative_allowed",
    timeout_ms: int = 1000,
) -> ToolSpecPolicy:
    return ToolSpecPolicy(
        tool_name=tool_name,
        side_effect=side_effect,
        cost=cost,
        cancellability=cancellability,
        reusability=reusability,
        speculate_mode=speculate_mode,
        timeout_ms=timeout_ms,
    )


def _record(
    tool_name: str = "read_file",
    state: ShadowTaskState = ShadowTaskState.RUNNING,
    started_offset_ms: float = 0.0,
    timeout_ms: int = 1000,
    cancellability: str = "cooperative",
) -> ShadowTaskRecord:
    """Create a ShadowTaskRecord with controlled started_at for progress testing."""
    started_at = time.monotonic() - (started_offset_ms / 1000.0) if started_offset_ms > 0 else None
    return ShadowTaskRecord(
        task_id="task_test",
        origin_turn_id="turn_test",
        origin_candidate_id="cand_test",
        tool_name=tool_name,
        normalized_args={},
        spec_key="spec_test",
        env_fingerprint="fp_test",
        policy_snapshot=_policy(tool_name=tool_name, timeout_ms=timeout_ms, cancellability=cancellability),
        state=state,
        started_at=started_at,
    )


class TestSalvageGovernorDefaults:
    """Tests for default thresholds."""

    def test_default_thresholds(self) -> None:
        governor = SalvageGovernor()
        assert governor._progress_high == 0.80
        assert governor._progress_low == 0.20
        assert governor._near_deadline_ratio == 0.90


class TestEvaluateNonRunningTasks:
    """Tests for evaluate() on non-running task states."""

    def test_completed_returns_cancel_now(self) -> None:
        governor = SalvageGovernor()
        record = _record(state=ShadowTaskState.COMPLETED)
        assert governor.evaluate(record) == SalvageDecision.CANCEL_NOW

    def test_failed_returns_cancel_now(self) -> None:
        governor = SalvageGovernor()
        record = _record(state=ShadowTaskState.FAILED)
        assert governor.evaluate(record) == SalvageDecision.CANCEL_NOW

    def test_cancelled_returns_cancel_now(self) -> None:
        governor = SalvageGovernor()
        record = _record(state=ShadowTaskState.CANCELLED)
        assert governor.evaluate(record) == SalvageDecision.CANCEL_NOW

    def test_abandoned_returns_cancel_now(self) -> None:
        governor = SalvageGovernor()
        record = _record(state=ShadowTaskState.ABANDONED)
        assert governor.evaluate(record) == SalvageDecision.CANCEL_NOW

    def test_adopted_returns_cancel_now(self) -> None:
        governor = SalvageGovernor()
        record = _record(state=ShadowTaskState.ADOPTED)
        assert governor.evaluate(record) == SalvageDecision.CANCEL_NOW


class TestEvaluateHighProgress:
    """Tests for high-progress scenarios."""

    def test_high_progress_returns_let_finish_and_cache(self) -> None:
        governor = SalvageGovernor(progress_high_threshold=0.80)
        # 85% progress → LET_FINISH_AND_CACHE
        record = _record(started_offset_ms=850, timeout_ms=1000)
        assert governor.evaluate(record) == SalvageDecision.LET_FINISH_AND_CACHE

    def test_exactly_at_high_threshold_returns_let_finish_and_cache(self) -> None:
        governor = SalvageGovernor(progress_high_threshold=0.80)
        # 80% progress (exactly at threshold) → high ≥ threshold → LET_FINISH_AND_CACHE
        record = _record(started_offset_ms=800, timeout_ms=1000)
        assert governor.evaluate(record) == SalvageDecision.LET_FINISH_AND_CACHE


class TestEvaluateNearDeadline:
    """Tests for near-deadline scenarios."""

    def test_near_deadline_returns_let_finish_and_cache(self) -> None:
        governor = SalvageGovernor(near_deadline_ratio=0.90)
        # 91% elapsed but not yet at high progress → near deadline
        record = _record(started_offset_ms=910, timeout_ms=1000)
        assert governor.evaluate(record) == SalvageDecision.LET_FINISH_AND_CACHE

    def test_at_deadline_ratio_returns_let_finish_and_cache(self) -> None:
        governor = SalvageGovernor(near_deadline_ratio=0.90)
        # Exactly 90% elapsed
        record = _record(started_offset_ms=900, timeout_ms=1000)
        assert governor.evaluate(record) == SalvageDecision.LET_FINISH_AND_CACHE


class TestEvaluateNonCancelable:
    """Tests for non-cancellable tools."""

    def test_non_cancelable_returns_join_authoritative(self) -> None:
        governor = SalvageGovernor()
        record = _record(state=ShadowTaskState.RUNNING, cancellability="non_cancelable")
        # low progress + non-cancelable → JOIN_AUTHORITATIVE
        assert governor.evaluate(record) == SalvageDecision.JOIN_AUTHORITATIVE

    def test_non_cancelable_high_progress_returns_let_finish_and_cache(self) -> None:
        governor = SalvageGovernor()
        record = _record(started_offset_ms=850, timeout_ms=1000, cancellability="non_cancelable")
        # High progress takes priority over non-cancelable
        assert governor.evaluate(record) == SalvageDecision.LET_FINISH_AND_CACHE


class TestEvaluateCooperative:
    """Tests for cooperative cancellation scenarios."""

    def test_low_progress_cooperative_returns_cancel_now(self) -> None:
        governor = SalvageGovernor(progress_low_threshold=0.20)
        # 10% progress, cooperative, low → CANCEL_NOW
        record = _record(started_offset_ms=100, timeout_ms=1000, cancellability="cooperative")
        assert governor.evaluate(record) == SalvageDecision.CANCEL_NOW

    def test_at_low_threshold_cooperative_returns_cancel_now(self) -> None:
        governor = SalvageGovernor(progress_low_threshold=0.20)
        # Exactly at 20% threshold
        record = _record(started_offset_ms=200, timeout_ms=1000, cancellability="cooperative")
        assert governor.evaluate(record) == SalvageDecision.CANCEL_NOW

    def test_above_low_threshold_cooperative_returns_join(self) -> None:
        governor = SalvageGovernor(progress_low_threshold=0.20)
        # 30% progress, cooperative → JOIN_AUTHORITATIVE (default)
        record = _record(started_offset_ms=300, timeout_ms=1000, cancellability="cooperative")
        assert governor.evaluate(record) == SalvageDecision.JOIN_AUTHORITATIVE


class TestEvaluateDefaultDecision:
    """Tests for default fallback decision."""

    def test_default_returns_join_authoritative(self) -> None:
        governor = SalvageGovernor()
        # Medium progress (50%), cooperative, cancelable → JOIN_AUTHORITATIVE
        record = _record(started_offset_ms=500, timeout_ms=1000, cancellability="cooperative")
        assert governor.evaluate(record) == SalvageDecision.JOIN_AUTHORITATIVE

    def test_best_effort_falls_to_join(self) -> None:
        governor = SalvageGovernor()
        # 50% progress, best_effort → JOIN_AUTHORITATIVE
        record = _record(started_offset_ms=500, timeout_ms=1000, cancellability="best_effort")
        assert governor.evaluate(record) == SalvageDecision.JOIN_AUTHORITATIVE


class TestEstimateProgress:
    """Tests for _estimate_progress() time-based estimation."""

    def test_zero_timeout_returns_zero_progress(self) -> None:
        governor = SalvageGovernor()
        record = _record(started_offset_ms=100, timeout_ms=0)
        assert governor._estimate_progress(record) == 0.0

    def test_fast_tools_get_progress_boost(self) -> None:
        governor = SalvageGovernor()
        # stat_file with < 10% elapsed time gets +0.3 boost, capped at 0.95
        record = _record(tool_name="stat_file", started_offset_ms=50, timeout_ms=1000)
        progress = governor._estimate_progress(record)
        # raw ratio = 0.05, boost = 0.35, capped at 0.95
        assert progress == pytest.approx(0.35)

    def test_progress_clamped_to_one(self) -> None:
        governor = SalvageGovernor()
        # 120% elapsed time should be clamped to 1.0
        record = _record(started_offset_ms=1200, timeout_ms=1000)
        assert governor._estimate_progress(record) == 1.0

    def test_progress_clamped_to_zero(self) -> None:
        governor = SalvageGovernor()
        record = _record(started_offset_ms=-100, timeout_ms=1000)
        assert governor._estimate_progress(record) == 0.0


class TestElapsedMs:
    """Tests for _elapsed_ms() calculation."""

    def test_no_started_at_returns_zero(self) -> None:
        governor = SalvageGovernor()
        record = _record(started_offset_ms=0.0)
        record.started_at = None
        assert governor._elapsed_ms(record) == 0.0


class TestBatchEvaluate:
    """Tests for batch_evaluate() across multiple records."""

    def test_batch_evaluate_returns_dict(self) -> None:
        governor = SalvageGovernor()
        records = [
            _record(state=ShadowTaskState.COMPLETED),
            _record(state=ShadowTaskState.RUNNING, started_offset_ms=500),
            _record(state=ShadowTaskState.RUNNING, started_offset_ms=100, cancellability="cooperative"),
        ]
        decisions = governor.batch_evaluate(records)
        assert isinstance(decisions, dict)
        assert len(decisions) == 3
        assert all(isinstance(d, SalvageDecision) for d in decisions.values())

    def test_batch_evaluate_all_states(self) -> None:
        governor = SalvageGovernor()
        states = [
            ShadowTaskState.CREATED,
            ShadowTaskState.ELIGIBLE,
            ShadowTaskState.STARTING,
            ShadowTaskState.RUNNING,
            ShadowTaskState.COMPLETED,
            ShadowTaskState.FAILED,
            ShadowTaskState.CANCEL_REQUESTED,
            ShadowTaskState.CANCELLED,
            ShadowTaskState.ABANDONED,
            ShadowTaskState.ADOPTED,
            ShadowTaskState.EXPIRED,
        ]
        records = [_record(state=s) for s in states]
        decisions = governor.batch_evaluate(records)
        assert len(decisions) == len(states)
