"""Heartbeat contention must not kill the Director lease loop."""

from __future__ import annotations

from polaris.cells.roles.adapters.internal.director.execute_method import (
    _task_runtime_heartbeat_is_retryable,
)


def test_task_runtime_heartbeat_contention_is_retryable_not_terminal() -> None:
    assert _task_runtime_heartbeat_is_retryable("authority_operation_in_progress") is True
    assert _task_runtime_heartbeat_is_retryable("authority_lock_timeout") is True
    assert _task_runtime_heartbeat_is_retryable("file_lock_timeout") is True
    assert _task_runtime_heartbeat_is_retryable("authority_closed") is False
    assert _task_runtime_heartbeat_is_retryable("workspace_mismatch") is False
    assert _task_runtime_heartbeat_is_retryable("") is False
