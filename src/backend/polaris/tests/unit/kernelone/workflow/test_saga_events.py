"""Tests for polaris.kernelone.workflow.saga_events."""

from __future__ import annotations

from polaris.kernelone.workflow.saga_events import (
    _EVENT_COMPENSATION_COMPLETED,
    _EVENT_COMPENSATION_FAILED,
    _EVENT_COMPENSATION_STARTED,
    _EVENT_COMPENSATION_TASK_COMPLETED,
    _EVENT_COMPENSATION_TASK_FAILED,
    _EVENT_COMPENSATION_TASK_STARTED,
    _EVENT_HUMAN_APPROVED,
    _EVENT_HUMAN_REJECTED,
    _EVENT_TASK_SUSPENDED_HUMAN_REVIEW,
    _EVENT_WORKFLOW_CHECKPOINT,
    _EVENT_WORKFLOW_PAUSED,
    _EVENT_WORKFLOW_RESUMED,
    _EVENT_WORKFLOW_SIGNAL_RECEIVED,
)


class TestSagaEventConstants:
    def test_compensation_events(self) -> None:
        assert _EVENT_COMPENSATION_STARTED == "compensation_started"
        assert _EVENT_COMPENSATION_TASK_STARTED == "compensation_task_started"
        assert _EVENT_COMPENSATION_TASK_COMPLETED == "compensation_task_completed"
        assert _EVENT_COMPENSATION_TASK_FAILED == "compensation_task_failed"
        assert _EVENT_COMPENSATION_COMPLETED == "compensation_completed"
        assert _EVENT_COMPENSATION_FAILED == "compensation_failed"

    def test_human_in_the_loop_events(self) -> None:
        assert _EVENT_TASK_SUSPENDED_HUMAN_REVIEW == "task_suspended_human_review"
        assert _EVENT_HUMAN_APPROVED == "human_approved"
        assert _EVENT_HUMAN_REJECTED == "human_rejected"

    def test_workflow_lifecycle_events(self) -> None:
        assert _EVENT_WORKFLOW_CHECKPOINT == "workflow_checkpoint"
        assert _EVENT_WORKFLOW_PAUSED == "workflow_paused"
        assert _EVENT_WORKFLOW_RESUMED == "workflow_resumed"
        assert _EVENT_WORKFLOW_SIGNAL_RECEIVED == "signal_received"
