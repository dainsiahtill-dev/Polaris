"""Two-stage job claiming semantics for TaskMarket."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    ClaimStage1Result,
    ClaimStage2Result,
    TaskWorkItemState,
)


@dataclass
class _WorkItemRecord:
    """Internal in-memory record for a work item's two-stage lifecycle."""

    item_id: str
    state: TaskWorkItemState = TaskWorkItemState.PENDING
    stage1_claimant: str = ""
    stage2_claimant: str = ""
    stage1_result: Any = None
    stage2_result: Any = None
    source_chain: list[str] = field(default_factory=list)
    consolidated_from: list[str] = field(default_factory=list)


class StageClaimManager:
    """Manages two-stage job claiming with in-memory storage.

    Stage 1: Initial claim (e.g., memory ingestion / partial work)
    Stage 2: Consolidation claim (e.g., merge / finalize) — only available
    after stage 1 completes.
    """

    def __init__(self) -> None:
        self._items: dict[str, _WorkItemRecord] = {}

    def _get_or_create(self, item_id: str) -> _WorkItemRecord:
        if item_id not in self._items:
            self._items[item_id] = _WorkItemRecord(item_id=item_id)
        return self._items[item_id]

    def try_claim_stage1(self, item_id: str, worker_id: str) -> ClaimStage1Result:
        """Attempt to claim stage 1 for *worker_id*.

        Returns:
            ClaimStage1Result: ``success=True`` if newly claimed.
            If already claimed by another worker, returns ``success=False``
            with ``already_claimed_by`` set and ``merged=True`` to signal
            that the work should be treated as merged / deduplicated.
        """
        record = self._get_or_create(item_id)

        if record.state == TaskWorkItemState.PENDING:
            record.state = TaskWorkItemState.STAGE1_CLAIMED
            record.stage1_claimant = worker_id
            return ClaimStage1Result(success=True, claimant_id=worker_id)

        if record.state in (
            TaskWorkItemState.STAGE1_CLAIMED,
            TaskWorkItemState.STAGE1_COMPLETE,
            TaskWorkItemState.STAGE2_CLAIMED,
            TaskWorkItemState.STAGE2_COMPLETE,
        ):
            return ClaimStage1Result(
                success=False,
                claimant_id=worker_id,
                already_claimed_by=record.stage1_claimant,
                merged=True,
            )

        # Defensive fallback — should not reach here.
        return ClaimStage1Result(
            success=False,
            claimant_id=worker_id,
            already_claimed_by=record.stage1_claimant,
            merged=True,
        )

    def try_claim_stage2(self, item_id: str, worker_id: str) -> ClaimStage2Result:
        """Attempt to claim stage 2 for *worker_id*.

        Stage 2 may only be claimed after stage 1 has completed.

        Returns:
            ClaimStage2Result: ``success=True`` if stage 1 is complete and
            stage 2 is newly claimed. ``stage1_result_available`` indicates
            whether a stage 1 result exists for consolidation.
        """
        record = self._get_or_create(item_id)

        if record.state == TaskWorkItemState.STAGE1_COMPLETE:
            record.state = TaskWorkItemState.STAGE2_CLAIMED
            record.stage2_claimant = worker_id
            return ClaimStage2Result(
                success=True,
                claimant_id=worker_id,
                stage1_result_available=record.stage1_result is not None,
                consolidated_result=record.stage1_result,
            )

        if record.state == TaskWorkItemState.STAGE2_CLAIMED:
            return ClaimStage2Result(
                success=False,
                claimant_id=worker_id,
                stage1_result_available=record.stage1_result is not None,
                consolidated_result=record.stage1_result,
            )

        if record.state == TaskWorkItemState.STAGE2_COMPLETE:
            return ClaimStage2Result(
                success=False,
                claimant_id=worker_id,
                stage1_result_available=record.stage1_result is not None,
                consolidated_result=record.stage2_result,
            )

        # Any other state (PENDING, STAGE1_CLAIMED) → not eligible.
        return ClaimStage2Result(
            success=False,
            claimant_id=worker_id,
            stage1_result_available=False,
        )

    def complete_stage1(self, item_id: str, worker_id: str, result: Any) -> bool:
        """Mark stage 1 as complete with *result*.

        Only the original stage 1 claimant may complete it.

        Returns:
            bool: ``True`` if completion was accepted.
        """
        record = self._items.get(item_id)
        if record is None:
            return False
        if record.state != TaskWorkItemState.STAGE1_CLAIMED:
            return False
        if record.stage1_claimant != worker_id:
            return False

        record.state = TaskWorkItemState.STAGE1_COMPLETE
        record.stage1_result = result
        return True

    def complete_stage2(self, item_id: str, worker_id: str, result: Any) -> bool:
        """Mark stage 2 as complete with *result*.

        Only the original stage 2 claimant may complete it.

        Returns:
            bool: ``True`` if completion was accepted.
        """
        record = self._items.get(item_id)
        if record is None:
            return False
        if record.state != TaskWorkItemState.STAGE2_CLAIMED:
            return False
        if record.stage2_claimant != worker_id:
            return False

        record.state = TaskWorkItemState.STAGE2_COMPLETE
        record.stage2_result = result
        return True

    def get_state(self, item_id: str) -> TaskWorkItemState:
        """Return the current lifecycle state for *item_id*.

        Defaults to ``TaskWorkItemState.PENDING`` if the item has never
        been seen.
        """
        record = self._items.get(item_id)
        if record is None:
            return TaskWorkItemState.PENDING
        return record.state

    def get_record(self, item_id: str) -> _WorkItemRecord | None:
        """Return the internal record for inspection (testing / debugging)."""
        return self._items.get(item_id)
