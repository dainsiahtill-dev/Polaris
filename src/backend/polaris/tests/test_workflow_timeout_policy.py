"""Tests for the shared kernelone workflow timeout policy.

Pins the timeout-scaling behavior extracted from the duplicated
workflow_runtime / workflow_activity helpers, so the canonical kernelone copy is
guaranteed to reproduce the values the cell parity tests assert.
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS
from polaris.kernelone.workflow.timeout_policy import (
    _director_child_workflow_timeout_seconds,
    _director_positive_int,
    _task_payload_list,
    _task_phase_timeout_seconds,
    _task_run_timeout_seconds,
)


class _Task:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload


def test_director_child_timeout_scales_with_task_count() -> None:
    # 30 (ready) + 900 * 3 (per-task) + 120 margin = 2850
    assert (
        _director_child_workflow_timeout_seconds(
            {"task_timeout_seconds": 900, "ready_timeout_seconds": 30},
            task_count=3,
        )
        == 2850
    )


def test_director_positive_int_coerces_and_floors() -> None:
    assert _director_positive_int("5", 10) == 5
    assert _director_positive_int(0, 10) == 1
    # A bad string raises ValueError → falls back to the (floored) default.
    assert _director_positive_int("not-a-number", 7) == 7
    # None raises TypeError, which is NOT caught (only RuntimeError/ValueError
    # are) — faithful to the original byte-identical behavior.
    with pytest.raises(TypeError):
        _director_positive_int(None, 5)


def test_task_payload_list_filters_blanks_and_missing() -> None:
    task = _Task({"target_files": ["a.py", "", "  ", "b.py"]})
    assert _task_payload_list(task, "target_files") == ["a.py", "b.py"]
    assert _task_payload_list(task, "missing") == []
    assert _task_payload_list(object(), "x") == []


def test_task_phase_timeout_non_implement_returns_base() -> None:
    assert _task_phase_timeout_seconds(_Task({}), "review", 200) == 200


def test_task_phase_timeout_scales_for_implement() -> None:
    task = _Task({"target_files": ["a.py", "b.py"]})
    # rounds=2 → floor = 30 + 2*600 + 300 = 1530
    assert _task_phase_timeout_seconds(task, "implement", 100) == 1530


def test_task_run_timeout_caps_at_global_budget() -> None:
    task = _Task({"target_files": [f"f{i}.py" for i in range(50)]})
    # rounds capped at 12 → floor = 30 + 12*600 + 300 = 7530, then capped at MAX
    assert _task_run_timeout_seconds(task, 100) == min(MAX_WORKFLOW_TIMEOUT_SECONDS, 7530)
