from __future__ import annotations

from polaris.cells.runtime.task_market.public.claiming import StageClaimManager
from polaris.cells.runtime.task_market.public.contracts import (
    ClaimStage1Result,
    ClaimStage2Result,
    TaskWorkItemState,
)


class TestStageClaimManager:
    def test_get_state_returns_pending_for_unknown_item(self) -> None:
        mgr = StageClaimManager()
        assert mgr.get_state("unknown-item") == TaskWorkItemState.PENDING

    def test_claim_stage1_success_on_first_attempt(self) -> None:
        mgr = StageClaimManager()
        result = mgr.try_claim_stage1("item-1", "worker-a")
        assert result == ClaimStage1Result(success=True, claimant_id="worker-a")
        assert mgr.get_state("item-1") == TaskWorkItemState.STAGE1_CLAIMED

    def test_claim_stage1_fails_when_already_claimed(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-1", "worker-a")
        result = mgr.try_claim_stage1("item-1", "worker-b")
        assert result.success is False
        assert result.already_claimed_by == "worker-a"
        assert result.merged is True

    def test_claim_stage1_fails_after_stage1_complete(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-1", "worker-a")
        mgr.complete_stage1("item-1", "worker-a", result={"data": 1})
        result = mgr.try_claim_stage1("item-1", "worker-b")
        assert result.success is False
        assert result.already_claimed_by == "worker-a"
        assert result.merged is True

    def test_claim_stage1_fails_after_stage2_claimed(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-1", "worker-a")
        mgr.complete_stage1("item-1", "worker-a", result={"data": 1})
        mgr.try_claim_stage2("item-1", "worker-b")
        result = mgr.try_claim_stage1("item-1", "worker-c")
        assert result.success is False
        assert result.merged is True

    def test_complete_stage1_only_by_claimant(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-1", "worker-a")
        assert mgr.complete_stage1("item-1", "worker-a", result={"ok": True}) is True
        assert mgr.get_state("item-1") == TaskWorkItemState.STAGE1_COMPLETE

    def test_complete_stage1_rejected_for_wrong_worker(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-1", "worker-a")
        assert mgr.complete_stage1("item-1", "worker-b", result={"ok": True}) is False
        assert mgr.get_state("item-1") == TaskWorkItemState.STAGE1_CLAIMED

    def test_complete_stage1_rejected_when_not_claimed(self) -> None:
        mgr = StageClaimManager()
        assert mgr.complete_stage1("item-1", "worker-a", result={"ok": True}) is False

    def test_claim_stage2_success_after_stage1_complete(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-1", "worker-a")
        mgr.complete_stage1("item-1", "worker-a", result={"data": 42})
        result = mgr.try_claim_stage2("item-1", "worker-b")
        assert result == ClaimStage2Result(
            success=True,
            claimant_id="worker-b",
            stage1_result_available=True,
            consolidated_result={"data": 42},
        )
        assert mgr.get_state("item-1") == TaskWorkItemState.STAGE2_CLAIMED

    def test_claim_stage2_fails_when_stage1_not_complete(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-1", "worker-a")
        result = mgr.try_claim_stage2("item-1", "worker-b")
        assert result.success is False
        assert result.stage1_result_available is False

    def test_claim_stage2_fails_when_pending(self) -> None:
        mgr = StageClaimManager()
        result = mgr.try_claim_stage2("item-1", "worker-b")
        assert result.success is False
        assert result.stage1_result_available is False

    def test_claim_stage2_fails_when_already_stage2_claimed(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-1", "worker-a")
        mgr.complete_stage1("item-1", "worker-a", result={"data": 42})
        mgr.try_claim_stage2("item-1", "worker-b")
        result = mgr.try_claim_stage2("item-1", "worker-c")
        assert result.success is False
        assert result.stage1_result_available is True
        assert result.consolidated_result == {"data": 42}

    def test_claim_stage2_fails_when_stage2_complete(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-1", "worker-a")
        mgr.complete_stage1("item-1", "worker-a", result={"data": 42})
        mgr.try_claim_stage2("item-1", "worker-b")
        mgr.complete_stage2("item-1", "worker-b", result={"final": 99})
        result = mgr.try_claim_stage2("item-1", "worker-c")
        assert result.success is False
        assert result.consolidated_result == {"final": 99}

    def test_complete_stage2_only_by_claimant(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-1", "worker-a")
        mgr.complete_stage1("item-1", "worker-a", result={"data": 1})
        mgr.try_claim_stage2("item-1", "worker-b")
        assert mgr.complete_stage2("item-1", "worker-b", result={"final": True}) is True
        assert mgr.get_state("item-1") == TaskWorkItemState.STAGE2_COMPLETE

    def test_complete_stage2_rejected_for_wrong_worker(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-1", "worker-a")
        mgr.complete_stage1("item-1", "worker-a", result={"data": 1})
        mgr.try_claim_stage2("item-1", "worker-b")
        assert mgr.complete_stage2("item-1", "worker-c", result={"final": True}) is False
        assert mgr.get_state("item-1") == TaskWorkItemState.STAGE2_CLAIMED

    def test_complete_stage2_rejected_when_not_claimed(self) -> None:
        mgr = StageClaimManager()
        assert mgr.complete_stage2("item-1", "worker-a", result={"final": True}) is False

    def test_multiple_items_are_isolated(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-a", "worker-1")
        mgr.try_claim_stage1("item-b", "worker-2")
        assert mgr.get_state("item-a") == TaskWorkItemState.STAGE1_CLAIMED
        assert mgr.get_state("item-b") == TaskWorkItemState.STAGE1_CLAIMED
        assert mgr.get_record("item-a").stage1_claimant == "worker-1"
        assert mgr.get_record("item-b").stage1_claimant == "worker-2"

    def test_full_lifecycle_happy_path(self) -> None:
        mgr = StageClaimManager()
        # Stage 1
        r1 = mgr.try_claim_stage1("item-1", "worker-a")
        assert r1.success is True
        assert mgr.complete_stage1("item-1", "worker-a", result={"partial": 1}) is True
        # Stage 2
        r2 = mgr.try_claim_stage2("item-1", "worker-b")
        assert r2.success is True
        assert r2.stage1_result_available is True
        assert r2.consolidated_result == {"partial": 1}
        assert mgr.complete_stage2("item-1", "worker-b", result={"final": 2}) is True
        assert mgr.get_state("item-1") == TaskWorkItemState.STAGE2_COMPLETE

    def test_source_chain_and_consolidated_from_defaults(self) -> None:
        mgr = StageClaimManager()
        mgr.try_claim_stage1("item-1", "worker-a")
        record = mgr.get_record("item-1")
        assert record is not None
        assert record.source_chain == []
        assert record.consolidated_from == []
