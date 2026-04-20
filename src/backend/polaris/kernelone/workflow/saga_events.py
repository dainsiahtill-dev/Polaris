"""Chronos Hourglass Saga Event Constants.

This module defines event type constants for the Saga compensation pattern
and human-in-the-loop workflow state machine.

References:
- SagaWorkflowEngine: kernelone/workflow/saga_engine.py
- Chronos Hourglass Architecture: docs/blueprints/CHRONOS_HOURGLASS_ARCHITECTURE_PLAN_20260404.md
"""

from __future__ import annotations

# Saga Compensation Events
_EVENT_COMPENSATION_STARTED = "compensation_started"
_EVENT_COMPENSATION_TASK_STARTED = "compensation_task_started"
_EVENT_COMPENSATION_TASK_COMPLETED = "compensation_task_completed"
_EVENT_COMPENSATION_TASK_FAILED = "compensation_task_failed"
_EVENT_COMPENSATION_COMPLETED = "compensation_completed"
_EVENT_COMPENSATION_FAILED = "compensation_failed"

# Human-in-the-Loop Events
_EVENT_TASK_SUSPENDED_HUMAN_REVIEW = "task_suspended_human_review"
_EVENT_HUMAN_APPROVED = "human_approved"
_EVENT_HUMAN_REJECTED = "human_rejected"

# Workflow Lifecycle Events
_EVENT_WORKFLOW_CHECKPOINT = "workflow_checkpoint"
_EVENT_WORKFLOW_PAUSED = "workflow_paused"
_EVENT_WORKFLOW_RESUMED = "workflow_resumed"
_EVENT_WORKFLOW_SIGNAL_RECEIVED = "signal_received"

__all__ = [
    "_EVENT_COMPENSATION_COMPLETED",
    "_EVENT_COMPENSATION_FAILED",
    "_EVENT_COMPENSATION_STARTED",
    "_EVENT_COMPENSATION_TASK_COMPLETED",
    "_EVENT_COMPENSATION_TASK_FAILED",
    "_EVENT_COMPENSATION_TASK_STARTED",
    "_EVENT_HUMAN_APPROVED",
    "_EVENT_HUMAN_REJECTED",
    "_EVENT_TASK_SUSPENDED_HUMAN_REVIEW",
    "_EVENT_WORKFLOW_CHECKPOINT",
    "_EVENT_WORKFLOW_PAUSED",
    "_EVENT_WORKFLOW_RESUMED",
    "_EVENT_WORKFLOW_SIGNAL_RECEIVED",
]
