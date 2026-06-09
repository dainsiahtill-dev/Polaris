"""Tests for the shared kernelone task-payload accessors.

Pins ``_task_dependencies`` (unified from the cell copies that diverged only in a
local variable name) and ``_task_payload_list``.
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.workflow.task_payload import _task_dependencies, _task_payload_list


class _Task:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload


def test_task_dependencies_merges_all_keys_and_strips() -> None:
    task = _Task({"depends_on": ["T01"], "dependencies": ["T02", ""], "blocked_by": ["  T03  "]})
    assert _task_dependencies(task) == {"T01", "T02", "T03"}


def test_task_dependencies_parity_case() -> None:
    # mirrors the cross-cell parity assertion: depends_on -> {"T01"}
    assert _task_dependencies(_Task({"depends_on": ["T01"]})) == {"T01"}


def test_task_dependencies_empty_and_defensive() -> None:
    assert _task_dependencies(_Task({})) == set()
    assert _task_dependencies(_Task({"depends_on": "not-a-list"})) == set()
    assert _task_dependencies(object()) == set()  # no payload attribute


def test_task_payload_list_filters_blanks_and_missing() -> None:
    task = _Task({"target_files": ["a.py", "", "  ", "b.py"]})
    assert _task_payload_list(task, "target_files") == ["a.py", "b.py"]
    assert _task_payload_list(task, "missing") == []
    assert _task_payload_list(object(), "x") == []
