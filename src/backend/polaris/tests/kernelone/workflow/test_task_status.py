"""Tests for polaris.kernelone.workflow.task_status.

Pure function tests for WorkflowTaskStatus, ActivityStatus, and
module-level frozenset constants.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.workflow.task_status import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    ActivityStatus,
    WorkflowTaskStatus,
)

# =============================================================================
# WorkflowTaskStatus — enum membership and value correctness
# =============================================================================


@pytest.mark.parametrize(
    "member,value",
    [
        (WorkflowTaskStatus.PENDING, "pending"),
        (WorkflowTaskStatus.RUNNING, "running"),
        (WorkflowTaskStatus.RETRYING, "retrying"),
        (WorkflowTaskStatus.COMPLETED, "completed"),
        (WorkflowTaskStatus.FAILED, "failed"),
        (WorkflowTaskStatus.CANCELLED, "cancelled"),
        (WorkflowTaskStatus.BLOCKED, "blocked"),
        (WorkflowTaskStatus.SKIPPED, "skipped"),
        (WorkflowTaskStatus.WAITING_HUMAN, "waiting_human"),
    ],
)
def test_workflow_task_status_values(member: WorkflowTaskStatus, value: str) -> None:
    assert member.value == value


def test_workflow_task_status_str_enum_behavior() -> None:
    assert str(WorkflowTaskStatus.RUNNING) == "running"
    assert WorkflowTaskStatus("failed") is WorkflowTaskStatus.FAILED


def test_workflow_task_status_total_members() -> None:
    assert len(WorkflowTaskStatus) == 9


# =============================================================================
# WorkflowTaskStatus.is_terminal
# =============================================================================


@pytest.mark.parametrize(
    "status,expected",
    [
        (WorkflowTaskStatus.PENDING, False),
        (WorkflowTaskStatus.RUNNING, False),
        (WorkflowTaskStatus.RETRYING, False),
        (WorkflowTaskStatus.COMPLETED, True),
        (WorkflowTaskStatus.FAILED, True),
        (WorkflowTaskStatus.CANCELLED, True),
        (WorkflowTaskStatus.BLOCKED, False),
        (WorkflowTaskStatus.SKIPPED, True),
        (WorkflowTaskStatus.WAITING_HUMAN, False),
    ],
)
def test_workflow_task_status_is_terminal(status: WorkflowTaskStatus, expected: bool) -> None:
    assert status.is_terminal is expected


def test_workflow_task_status_terminal_set_consistency() -> None:
    terminal_members = {s for s in WorkflowTaskStatus if s.is_terminal}
    assert terminal_members == {
        WorkflowTaskStatus.COMPLETED,
        WorkflowTaskStatus.FAILED,
        WorkflowTaskStatus.CANCELLED,
        WorkflowTaskStatus.SKIPPED,
    }


# =============================================================================
# WorkflowTaskStatus.is_active
# =============================================================================


@pytest.mark.parametrize(
    "status,expected",
    [
        (WorkflowTaskStatus.PENDING, True),
        (WorkflowTaskStatus.RUNNING, True),
        (WorkflowTaskStatus.RETRYING, True),
        (WorkflowTaskStatus.COMPLETED, False),
        (WorkflowTaskStatus.FAILED, False),
        (WorkflowTaskStatus.CANCELLED, False),
        (WorkflowTaskStatus.BLOCKED, False),
        (WorkflowTaskStatus.SKIPPED, False),
        (WorkflowTaskStatus.WAITING_HUMAN, True),
    ],
)
def test_workflow_task_status_is_active(status: WorkflowTaskStatus, expected: bool) -> None:
    assert status.is_active is expected


def test_workflow_task_status_active_set_consistency() -> None:
    active_members = {s for s in WorkflowTaskStatus if s.is_active}
    assert active_members == {
        WorkflowTaskStatus.PENDING,
        WorkflowTaskStatus.RUNNING,
        WorkflowTaskStatus.RETRYING,
        WorkflowTaskStatus.WAITING_HUMAN,
    }


def test_workflow_task_status_active_and_terminal_are_disjoint() -> None:
    for status in WorkflowTaskStatus:
        assert not (status.is_terminal and status.is_active)


# =============================================================================
# ActivityStatus — enum membership and value correctness
# =============================================================================


@pytest.mark.parametrize(
    "member,value",
    [
        (ActivityStatus.PENDING, "pending"),
        (ActivityStatus.RUNNING, "running"),
        (ActivityStatus.COMPLETED, "completed"),
        (ActivityStatus.FAILED, "failed"),
        (ActivityStatus.CANCELLED, "cancelled"),
    ],
)
def test_activity_status_values(member: ActivityStatus, value: str) -> None:
    assert member.value == value


def test_activity_status_str_enum_behavior() -> None:
    assert str(ActivityStatus.COMPLETED) == "completed"
    assert ActivityStatus("cancelled") is ActivityStatus.CANCELLED


def test_activity_status_total_members() -> None:
    assert len(ActivityStatus) == 5


# =============================================================================
# ActivityStatus.is_terminal
# =============================================================================


@pytest.mark.parametrize(
    "status,expected",
    [
        (ActivityStatus.PENDING, False),
        (ActivityStatus.RUNNING, False),
        (ActivityStatus.COMPLETED, True),
        (ActivityStatus.FAILED, True),
        (ActivityStatus.CANCELLED, True),
    ],
)
def test_activity_status_is_terminal(status: ActivityStatus, expected: bool) -> None:
    assert status.is_terminal is expected


def test_activity_status_terminal_set_consistency() -> None:
    terminal_members = {s for s in ActivityStatus if s.is_terminal}
    assert terminal_members == {
        ActivityStatus.COMPLETED,
        ActivityStatus.FAILED,
        ActivityStatus.CANCELLED,
    }


# =============================================================================
# ActivityStatus.is_active
# =============================================================================


@pytest.mark.parametrize(
    "status,expected",
    [
        (ActivityStatus.PENDING, True),
        (ActivityStatus.RUNNING, True),
        (ActivityStatus.COMPLETED, False),
        (ActivityStatus.FAILED, False),
        (ActivityStatus.CANCELLED, False),
    ],
)
def test_activity_status_is_active(status: ActivityStatus, expected: bool) -> None:
    assert status.is_active is expected


def test_activity_status_is_active_complements_is_terminal() -> None:
    for status in ActivityStatus:
        assert status.is_active == (not status.is_terminal)


# =============================================================================
# Module-level frozenset constants
# =============================================================================


def test_terminal_statuses_is_frozenset() -> None:
    assert isinstance(TERMINAL_STATUSES, frozenset)


def test_terminal_statuses_contents() -> None:
    assert {"completed", "failed", "cancelled", "skipped"} == TERMINAL_STATUSES


def test_active_statuses_is_frozenset() -> None:
    assert isinstance(ACTIVE_STATUSES, frozenset)


def test_active_statuses_contents() -> None:
    assert {"pending", "running", "retrying", "waiting_human"} == ACTIVE_STATUSES


def test_terminal_and_active_statuses_are_disjoint() -> None:
    assert TERMINAL_STATUSES.isdisjoint(ACTIVE_STATUSES)


def test_terminal_statuses_match_enum_property() -> None:
    for status in WorkflowTaskStatus:
        if status.is_terminal:
            assert status.value in TERMINAL_STATUSES
        else:
            assert status.value not in TERMINAL_STATUSES


def test_active_statuses_match_enum_property() -> None:
    for status in WorkflowTaskStatus:
        if status.is_active:
            assert status.value in ACTIVE_STATUSES
        else:
            assert status.value not in ACTIVE_STATUSES


# =============================================================================
# Edge cases / boundary conditions
# =============================================================================


def test_workflow_task_status_no_duplicate_values() -> None:
    values = [s.value for s in WorkflowTaskStatus]
    assert len(values) == len(set(values))


def test_activity_status_no_duplicate_values() -> None:
    values = [s.value for s in ActivityStatus]
    assert len(values) == len(set(values))


def test_workflow_task_status_equality_by_identity_and_value() -> None:
    assert WorkflowTaskStatus.RUNNING == WorkflowTaskStatus.RUNNING
    assert WorkflowTaskStatus.RUNNING != WorkflowTaskStatus.FAILED
    assert WorkflowTaskStatus.RUNNING == "running"


def test_activity_status_hashable_in_set() -> None:
    assert len({ActivityStatus.PENDING, ActivityStatus.PENDING, ActivityStatus.RUNNING}) == 2
