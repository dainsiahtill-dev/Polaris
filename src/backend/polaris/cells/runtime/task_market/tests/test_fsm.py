"""Tests for ``internal/fsm.py``."""

from __future__ import annotations

import pytest
from polaris.cells.runtime.task_market.internal.fsm import (
    PRIORITY_WEIGHT,
    QUEUE_STAGES,
    TERMINAL_STATUSES,
    TaskStageFSM,
    get_fsm,
)


class TestTaskStageFSM:
    """Unit tests for the FSM."""

    @pytest.fixture
    def fsm(self) -> TaskStageFSM:
        return TaskStageFSM()

    # ---- Status classification -----------------------------------------------

    def test_is_terminal_resolved(self, fsm: TaskStageFSM) -> None:
        assert fsm.is_terminal("resolved") is True

    def test_is_terminal_dead_letter(self, fsm: TaskStageFSM) -> None:
        assert fsm.is_terminal("dead_letter") is True

    def test_is_terminal_pending_design(self, fsm: TaskStageFSM) -> None:
        assert fsm.is_terminal("pending_design") is False

    def test_is_queue_stage(self, fsm: TaskStageFSM) -> None:
        assert fsm.is_queue_stage("pending_design") is True
        assert fsm.is_queue_stage("pending_exec") is True
        assert fsm.is_queue_stage("in_design") is False

    def test_is_in_progress(self, fsm: TaskStageFSM) -> None:
        assert fsm.is_in_progress("in_design") is True
        assert fsm.is_in_progress("in_execution") is True
        assert fsm.is_in_progress("pending_design") is False

    # ---- In-progress status mapping -----------------------------------------

    def test_get_in_progress_status_design(self, fsm: TaskStageFSM) -> None:
        assert fsm.get_in_progress_status("pending_design") == "in_design"

    def test_get_in_progress_status_exec(self, fsm: TaskStageFSM) -> None:
        assert fsm.get_in_progress_status("pending_exec") == "in_execution"

    def test_get_in_progress_status_qa(self, fsm: TaskStageFSM) -> None:
        assert fsm.get_in_progress_status("pending_qa") == "in_qa"

    def test_get_queue_stage_for_status(self, fsm: TaskStageFSM) -> None:
        assert fsm.get_queue_stage_for_status("in_design") == "pending_design"
        assert fsm.get_queue_stage_for_status("in_execution") == "pending_exec"

    # ---- Legal transitions --------------------------------------------------

    def test_publish_to_pending_design(self, fsm: TaskStageFSM) -> None:
        assert fsm.can_transition("", "pending_design", "publish") is True

    def test_claim_pending_design(self, fsm: TaskStageFSM) -> None:
        assert fsm.can_transition("pending_design", "in_design", "claim") is True

    def test_ack_in_design_to_pending_exec(self, fsm: TaskStageFSM) -> None:
        assert (
            fsm.can_transition(
                "in_design",
                "pending_exec",
                "ack",
                next_stage="pending_exec",
            )
            is True
        )

    def test_ack_in_design_to_pending_design_requeue(self, fsm: TaskStageFSM) -> None:
        # DESIGN_FAILED is expressed via requeue_stage=pending_design
        assert (
            fsm.can_transition(
                "in_design",
                "pending_design",
                "ack",
                next_stage="pending_design",
            )
            is True
        )

    def test_fail_in_design_to_dead_letter(self, fsm: TaskStageFSM) -> None:
        assert (
            fsm.can_transition(
                "in_design",
                "dead_letter",
                "fail",
            )
            is True
        )

    def test_claim_pending_exec(self, fsm: TaskStageFSM) -> None:
        assert fsm.can_transition("pending_exec", "in_execution", "claim") is True

    def test_ack_in_execution_to_pending_qa(self, fsm: TaskStageFSM) -> None:
        assert (
            fsm.can_transition(
                "in_execution",
                "pending_qa",
                "ack",
                next_stage="pending_qa",
            )
            is True
        )

    def test_fail_in_execution_to_dead_letter(self, fsm: TaskStageFSM) -> None:
        assert fsm.can_transition("in_execution", "dead_letter", "fail") is True

    def test_ack_pending_qa_to_resolved(self, fsm: TaskStageFSM) -> None:
        assert (
            fsm.can_transition(
                "pending_qa",
                "resolved",
                "ack",
                terminal_status="resolved",
            )
            is True
        )

    def test_ack_pending_qa_to_rejected(self, fsm: TaskStageFSM) -> None:
        assert (
            fsm.can_transition(
                "pending_qa",
                "rejected",
                "ack",
                terminal_status="rejected",
            )
            is True
        )

    # ---- Illegal transitions -----------------------------------------------

    def test_illegal_pending_qa_claim_in_design(self, fsm: TaskStageFSM) -> None:
        assert fsm.can_transition("pending_qa", "in_design", "claim") is False

    def test_illegal_ack_terminal_without_terminal_status(self, fsm: TaskStageFSM) -> None:
        # Cannot ack to a terminal status without explicitly passing terminal_status
        assert fsm.can_transition("pending_qa", "resolved", "ack", next_stage="resolved") is False

    def test_illegal_unknown_event(self, fsm: TaskStageFSM) -> None:
        assert fsm.can_transition("pending_design", "in_design", "unknown_event") is False

    # ---- Priority weights ---------------------------------------------------

    def test_priority_ordering(self) -> None:
        assert PRIORITY_WEIGHT["critical"] > PRIORITY_WEIGHT["high"]
        assert PRIORITY_WEIGHT["high"] > PRIORITY_WEIGHT["medium"]
        assert PRIORITY_WEIGHT["medium"] > PRIORITY_WEIGHT["low"]

    # ---- Constants ---------------------------------------------------------

    def test_all_stages_non_empty(self) -> None:
        from polaris.cells.runtime.task_market.internal.fsm import ALL_STAGES

        assert len(ALL_STAGES) > 0
        assert "pending_design" in ALL_STAGES
        assert "in_design" in ALL_STAGES
        assert "in_execution" in ALL_STAGES
        assert "pending_exec" in ALL_STAGES
        assert "pending_qa" in ALL_STAGES
        assert "dead_letter" in ALL_STAGES

    def test_queue_stages_disjoint_from_terminal(self) -> None:
        assert QUEUE_STAGES.isdisjoint(TERMINAL_STATUSES)

    # ---- Singleton ---------------------------------------------------------

    def test_get_fsm_returns_same_instance(self) -> None:
        fsm1 = get_fsm()
        fsm2 = get_fsm()
        assert fsm1 is fsm2
