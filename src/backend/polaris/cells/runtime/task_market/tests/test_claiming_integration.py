"""Integration tests for TaskMarket Claiming with Contracts.

These tests verify that StageClaimManager correctly implements two-stage
job claiming semantics as defined in the task market contracts.
"""

from __future__ import annotations

import pytest
from polaris.cells.runtime.task_market.public.claiming import StageClaimManager
from polaris.cells.runtime.task_market.public.contracts import (
    ClaimStage1Result,
    ClaimStage2Result,
    TaskWorkItemState,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def manager() -> StageClaimManager:
    """Return a fresh StageClaimManager instance."""
    return StageClaimManager()


# =============================================================================
# Integration: Full Two-Stage Lifecycle
# =============================================================================


class TestFullTwoStageLifecycle:
    """Test complete two-stage lifecycle: publish -> stage1 claim ->
    stage1 complete -> stage2 claim -> stage2 complete."""

    def test_happy_path_full_lifecycle(self, manager: StageClaimManager) -> None:
        """Full happy-path two-stage lifecycle completes successfully."""
        item_id = "work-item-001"

        # Initial state
        assert manager.get_state(item_id) == TaskWorkItemState.PENDING

        # Stage 1: Claim
        r1 = manager.try_claim_stage1(item_id, "worker-alpha")
        assert r1.success is True
        assert r1.claimant_id == "worker-alpha"
        assert manager.get_state(item_id) == TaskWorkItemState.STAGE1_CLAIMED

        # Stage 1: Complete
        stage1_result = {"partial_data": [1, 2, 3]}
        completed = manager.complete_stage1(item_id, "worker-alpha", stage1_result)
        assert completed is True
        assert manager.get_state(item_id) == TaskWorkItemState.STAGE1_COMPLETE

        # Stage 2: Claim
        r2 = manager.try_claim_stage2(item_id, "worker-beta")
        assert r2.success is True
        assert r2.claimant_id == "worker-beta"
        assert r2.stage1_result_available is True
        assert r2.consolidated_result == stage1_result
        assert manager.get_state(item_id) == TaskWorkItemState.STAGE2_CLAIMED

        # Stage 2: Complete
        stage2_result = {"final_data": {"merged": True, "items": 3}}
        completed = manager.complete_stage2(item_id, "worker-beta", stage2_result)
        assert completed is True
        assert manager.get_state(item_id) == TaskWorkItemState.STAGE2_COMPLETE

        # Verify final record state
        record = manager.get_record(item_id)
        assert record is not None
        assert record.stage1_result == stage1_result
        assert record.stage2_result == stage2_result
        assert record.stage1_claimant == "worker-alpha"
        assert record.stage2_claimant == "worker-beta"

    def test_stage2_blocked_until_stage1_complete(self, manager: StageClaimManager) -> None:
        """Stage 2 claim is blocked until stage 1 is complete."""
        item_id = "work-item-002"

        # Stage 1: Claim
        manager.try_claim_stage1(item_id, "worker-a")

        # Try stage 2 while stage 1 is only claimed (not complete)
        r2 = manager.try_claim_stage2(item_id, "worker-b")
        assert r2.success is False
        assert r2.stage1_result_available is False
        assert r2.consolidated_result is None
        assert manager.get_state(item_id) != TaskWorkItemState.STAGE2_CLAIMED

        # Complete stage 1
        manager.complete_stage1(item_id, "worker-a", {"data": 1})

        # Now stage 2 should succeed
        r2 = manager.try_claim_stage2(item_id, "worker-b")
        assert r2.success is True

    def test_state_transitions_match_lifecycle(self, manager: StageClaimManager) -> None:
        """State transitions follow the expected lifecycle."""
        item_id = "work-item-003"

        # PENDING -> STAGE1_CLAIMED
        assert manager.get_state(item_id) == TaskWorkItemState.PENDING
        manager.try_claim_stage1(item_id, "worker-a")
        assert manager.get_state(item_id) == TaskWorkItemState.STAGE1_CLAIMED

        # STAGE1_CLAIMED -> STAGE1_COMPLETE
        manager.complete_stage1(item_id, "worker-a", {})
        assert manager.get_state(item_id) == TaskWorkItemState.STAGE1_COMPLETE

        # STAGE1_COMPLETE -> STAGE2_CLAIMED
        manager.try_claim_stage2(item_id, "worker-b")
        assert manager.get_state(item_id) == TaskWorkItemState.STAGE2_CLAIMED

        # STAGE2_CLAIMED -> STAGE2_COMPLETE
        manager.complete_stage2(item_id, "worker-b", {})
        assert manager.get_state(item_id) == TaskWorkItemState.STAGE2_COMPLETE


# =============================================================================
# Integration: Deduplication Behavior
# =============================================================================


class TestDeduplicationBehavior:
    """Test dedup behavior: second stage1 claim returns merged=True."""

    def test_second_stage1_claim_returns_merged(self, manager: StageClaimManager) -> None:
        """Second stage1 claim returns merged=True for deduplication."""
        item_id = "work-item-010"

        # First claim succeeds
        r1 = manager.try_claim_stage1(item_id, "worker-a")
        assert r1.success is True
        assert r1.merged is False

        # Second claim from different worker
        r2 = manager.try_claim_stage1(item_id, "worker-b")
        assert r2.success is False
        assert r2.already_claimed_by == "worker-a"
        assert r2.merged is True
        assert r2.claimant_id == "worker-b"

    def test_second_stage1_claim_after_complete_returns_merged(self, manager: StageClaimManager) -> None:
        """Second stage1 claim after completion returns merged=True."""
        item_id = "work-item-011"

        # First claim and complete
        manager.try_claim_stage1(item_id, "worker-a")
        manager.complete_stage1(item_id, "worker-a", {"data": 1})

        # Second claim from different worker
        r2 = manager.try_claim_stage1(item_id, "worker-b")
        assert r2.success is False
        assert r2.already_claimed_by == "worker-a"
        assert r2.merged is True

    def test_stage2_dedup_not_possible(self, manager: StageClaimManager) -> None:
        """Stage 2 does not have deduplication - only one claim at a time."""
        item_id = "work-item-012"

        # Setup: complete stage 1
        manager.try_claim_stage1(item_id, "worker-a")
        manager.complete_stage1(item_id, "worker-a", {"data": 1})

        # First stage 2 claim
        r1 = manager.try_claim_stage2(item_id, "worker-b")
        assert r1.success is True

        # Second stage 2 claim from different worker fails
        r2 = manager.try_claim_stage2(item_id, "worker-c")
        assert r2.success is False
        # Note: merged is not set for stage2 dedup
        assert r2.claimant_id == "worker-c"


# =============================================================================
# Integration: Authorization Enforcement
# =============================================================================


class TestAuthorizationEnforcement:
    """Test authorization: only original claimant can complete each stage."""

    def test_stage1_only_by_original_claimant(self, manager: StageClaimManager) -> None:
        """Stage 1 completion requires original claimant."""
        item_id = "work-item-020"

        manager.try_claim_stage1(item_id, "worker-alpha")

        # Wrong worker cannot complete
        rejected = manager.complete_stage1(item_id, "worker-beta", {"data": 1})
        assert rejected is False
        assert manager.get_state(item_id) == TaskWorkItemState.STAGE1_CLAIMED

        # Correct worker can complete
        accepted = manager.complete_stage1(item_id, "worker-alpha", {"data": 1})
        assert accepted is True
        assert manager.get_state(item_id) == TaskWorkItemState.STAGE1_COMPLETE

    def test_stage2_only_by_original_claimant(self, manager: StageClaimManager) -> None:
        """Stage 2 completion requires original claimant."""
        item_id = "work-item-021"

        # Complete stage 1
        manager.try_claim_stage1(item_id, "worker-a")
        manager.complete_stage1(item_id, "worker-a", {"partial": 1})

        # Claim stage 2
        manager.try_claim_stage2(item_id, "worker-b")

        # Wrong worker cannot complete stage 2
        rejected = manager.complete_stage2(item_id, "worker-c", {"final": 2})
        assert rejected is False
        assert manager.get_state(item_id) == TaskWorkItemState.STAGE2_CLAIMED

        # Correct worker can complete
        accepted = manager.complete_stage2(item_id, "worker-b", {"final": 2})
        assert accepted is True
        assert manager.get_state(item_id) == TaskWorkItemState.STAGE2_COMPLETE

    def test_complete_stage1_without_claim_fails(self, manager: StageClaimManager) -> None:
        """Cannot complete stage 1 without a prior claim."""
        item_id = "work-item-022"

        result = manager.complete_stage1(item_id, "worker-a", {"data": 1})
        assert result is False
        assert manager.get_state(item_id) == TaskWorkItemState.PENDING

    def test_complete_stage2_without_claim_fails(self, manager: StageClaimManager) -> None:
        """Cannot complete stage 2 without a prior claim."""
        item_id = "work-item-023"

        result = manager.complete_stage2(item_id, "worker-a", {"data": 1})
        assert result is False


# =============================================================================
# Integration: Consolidated Result Accumulation
# =============================================================================


class TestConsolidatedResultAccumulation:
    """Test consolidated_from accumulates merge sources."""

    def test_stage1_result_passed_to_stage2(self, manager: StageClaimManager) -> None:
        """Stage 1 result is available to stage 2 claimant."""
        item_id = "work-item-030"
        stage1_data = {"step_1": "done", "step_2": "done"}

        manager.try_claim_stage1(item_id, "worker-a")
        manager.complete_stage1(item_id, "worker-a", stage1_data)

        r2 = manager.try_claim_stage2(item_id, "worker-b")
        assert r2.success is True
        assert r2.stage1_result_available is True
        assert r2.consolidated_result == stage1_data

    def test_stage2_result_overwrites_consolidated(self, manager: StageClaimManager) -> None:
        """Stage 2 result becomes the final consolidated result."""
        item_id = "work-item-031"
        stage1_data = {"partial": True}
        stage2_data = {"final": True, "merged": True}

        manager.try_claim_stage1(item_id, "worker-a")
        manager.complete_stage1(item_id, "worker-a", stage1_data)
        manager.try_claim_stage2(item_id, "worker-b")
        manager.complete_stage2(item_id, "worker-b", stage2_data)

        # Final result is stage2
        record = manager.get_record(item_id)
        assert record is not None
        assert record.stage1_result == stage1_data
        assert record.stage2_result == stage2_data

    def test_consolidated_result_none_until_stage1_complete(self, manager: StageClaimManager) -> None:
        """Stage 2 cannot access result until stage 1 is complete."""
        item_id = "work-item-032"

        manager.try_claim_stage1(item_id, "worker-a")
        # Don't complete stage 1

        r2 = manager.try_claim_stage2(item_id, "worker-b")
        assert r2.success is False
        assert r2.stage1_result_available is False
        assert r2.consolidated_result is None


# =============================================================================
# Integration: Result Structures
# =============================================================================


class TestResultStructures:
    """Verify ClaimStage1Result and ClaimStage2Result structures."""

    def test_claim_stage1_result_fields(self, manager: StageClaimManager) -> None:
        """ClaimStage1Result has all required fields."""
        item_id = "work-item-040"

        # Success case
        result = manager.try_claim_stage1(item_id, "worker-a")
        assert isinstance(result, ClaimStage1Result)
        assert result.success is True
        assert result.claimant_id == "worker-a"
        assert result.already_claimed_by == ""
        assert result.merged is False

        # Failure case
        result = manager.try_claim_stage1(item_id, "worker-b")
        assert result.success is False
        assert result.claimant_id == "worker-b"
        assert result.already_claimed_by == "worker-a"
        assert result.merged is True

    def test_claim_stage2_result_fields(self, manager: StageClaimManager) -> None:
        """ClaimStage2Result has all required fields."""
        item_id = "work-item-041"

        # Complete stage 1 first
        manager.try_claim_stage1(item_id, "worker-a")
        manager.complete_stage1(item_id, "worker-a", {"data": 42})

        # Success case
        result = manager.try_claim_stage2(item_id, "worker-b")
        assert isinstance(result, ClaimStage2Result)
        assert result.success is True
        assert result.claimant_id == "worker-b"
        assert result.stage1_result_available is True
        assert result.consolidated_result == {"data": 42}

    def test_task_work_item_state_values(self) -> None:
        """TaskWorkItemState enum has expected values."""
        assert TaskWorkItemState.PENDING.value == "pending"
        assert TaskWorkItemState.STAGE1_CLAIMED.value == "stage1_claimed"
        assert TaskWorkItemState.STAGE1_COMPLETE.value == "stage1_complete"
        assert TaskWorkItemState.STAGE2_CLAIMED.value == "stage2_claimed"
        assert TaskWorkItemState.STAGE2_COMPLETE.value == "stage2_complete"


# =============================================================================
# Integration: Multiple Items Isolation
# =============================================================================


class TestMultipleItemsIsolation:
    """Test that multiple work items are isolated from each other."""

    def test_independent_items(self, manager: StageClaimManager) -> None:
        """Multiple work items operate independently."""
        # Claim stage 1 for item A
        r1 = manager.try_claim_stage1("item-a", "worker-1")
        assert r1.success is True

        # Claim stage 1 for item B
        r2 = manager.try_claim_stage1("item-b", "worker-2")
        assert r2.success is True

        # Complete stage 1 for item A only
        manager.complete_stage1("item-a", "worker-1", {"a": 1})

        # Verify isolation
        assert manager.get_state("item-a") == TaskWorkItemState.STAGE1_COMPLETE
        assert manager.get_state("item-b") == TaskWorkItemState.STAGE1_CLAIMED

    def test_item_a_claims_do_not_affect_item_b(self, manager: StageClaimManager) -> None:
        """Claims on item A don't affect item B."""
        # First worker claims item A
        r1 = manager.try_claim_stage1("item-a", "worker-a")
        assert r1.success is True

        # Second worker claims item A
        r2 = manager.try_claim_stage1("item-a", "worker-b")
        assert r2.success is False

        # Third worker can still claim item B
        r3 = manager.try_claim_stage1("item-b", "worker-c")
        assert r3.success is True

    def test_parallel_stages_for_different_items(self, manager: StageClaimManager) -> None:
        """Different items can be in different stages simultaneously."""
        # Item 1: Stage 1 claimed
        manager.try_claim_stage1("item-1", "worker-a")

        # Item 2: Stage 1 complete, Stage 2 claimed
        manager.try_claim_stage1("item-2", "worker-b")
        manager.complete_stage1("item-2", "worker-b", {"data": 1})
        manager.try_claim_stage2("item-2", "worker-c")

        # Item 3: Stage 1 complete, Stage 2 complete
        manager.try_claim_stage1("item-3", "worker-d")
        manager.complete_stage1("item-3", "worker-d", {"data": 1})
        manager.try_claim_stage2("item-3", "worker-e")
        manager.complete_stage2("item-3", "worker-e", {"final": 1})

        assert manager.get_state("item-1") == TaskWorkItemState.STAGE1_CLAIMED
        assert manager.get_state("item-2") == TaskWorkItemState.STAGE2_CLAIMED
        assert manager.get_state("item-3") == TaskWorkItemState.STAGE2_COMPLETE


# =============================================================================
# Integration: Edge Cases
# =============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_unknown_item_returns_pending(self, manager: StageClaimManager) -> None:
        """Unknown item ID returns PENDING state."""
        assert manager.get_state("never-seen-item") == TaskWorkItemState.PENDING

    def test_get_record_returns_none_for_unknown(self, manager: StageClaimManager) -> None:
        """get_record returns None for unknown item."""
        record = manager.get_record("never-seen-item")
        assert record is None

    def test_complete_stage2_without_stage1_claim_fails(self, manager: StageClaimManager) -> None:
        """Cannot claim stage 2 without stage 1 claim."""
        result = manager.try_claim_stage2("item-1", "worker-a")
        assert result.success is False
        assert result.stage1_result_available is False

    def test_stage1_claim_after_stage2_claimed_fails(self, manager: StageClaimManager) -> None:
        """Stage 1 cannot be claimed after stage 2 is claimed."""
        item_id = "work-item-060"

        manager.try_claim_stage1(item_id, "worker-a")
        manager.complete_stage1(item_id, "worker-a", {})
        manager.try_claim_stage2(item_id, "worker-b")

        result = manager.try_claim_stage1(item_id, "worker-c")
        assert result.success is False
        assert result.merged is True

    def test_stage2_claim_after_stage2_complete_fails(self, manager: StageClaimManager) -> None:
        """Stage 2 cannot be claimed after stage 2 is complete."""
        item_id = "work-item-061"

        manager.try_claim_stage1(item_id, "worker-a")
        manager.complete_stage1(item_id, "worker-a", {})
        manager.try_claim_stage2(item_id, "worker-b")
        manager.complete_stage2(item_id, "worker-b", {})

        result = manager.try_claim_stage2(item_id, "worker-c")
        assert result.success is False
        # consolidated_result should be stage2 result
        assert result.consolidated_result == {}

    def test_empty_result_handling(self, manager: StageClaimManager) -> None:
        """Empty results are handled correctly."""
        item_id = "work-item-070"

        manager.try_claim_stage1(item_id, "worker-a")
        manager.complete_stage1(item_id, "worker-a", {})

        r2 = manager.try_claim_stage2(item_id, "worker-b")
        assert r2.success is True
        assert r2.consolidated_result == {}

    def test_complex_result_handling(self, manager: StageClaimManager) -> None:
        """Complex nested results are preserved."""
        item_id = "work-item-080"
        complex_result = {
            "level1": {
                "level2": {
                    "list": [1, 2, 3],
                    "nested": {"deep": True},
                }
            },
            "null": None,
            "bool": False,
        }

        manager.try_claim_stage1(item_id, "worker-a")
        manager.complete_stage1(item_id, "worker-a", complex_result)

        r2 = manager.try_claim_stage2(item_id, "worker-b")
        assert r2.consolidated_result == complex_result
