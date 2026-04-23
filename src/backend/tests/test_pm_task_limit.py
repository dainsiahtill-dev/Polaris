from __future__ import annotations

import os
import sys

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for candidate in (BACKEND_ROOT,):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.delivery.cli.pm import tasks as pm_tasks  # noqa: E402


def _sample_raw_tasks(count: int) -> list[dict]:
    items: list[dict] = []
    for index in range(1, count + 1):
        items.append(
            {
                "id": f"T-{index}",
                "title": f"Task {index}",
                "goal": f"Goal {index} with actionable description",
                "target_files": ["workspace/docs/product/requirements.md"],
                "acceptance_criteria": ["criterion a", "criterion b"],
                "assigned_to": "Director",
            }
        )
    return items


def test_normalize_tasks_default_limit_is_not_three(monkeypatch) -> None:
    monkeypatch.delenv("KERNELONE_PM_MAX_TASKS", raising=False)
    normalized = pm_tasks.normalize_tasks(_sample_raw_tasks(5), iteration=1)
    assert len(normalized) == 5


def test_normalize_tasks_honors_env_limit(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_PM_MAX_TASKS", "2")
    normalized = pm_tasks.normalize_tasks(_sample_raw_tasks(5), iteration=1)
    assert len(normalized) == 2


def test_normalize_tasks_limit_zero_disables_truncation(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_PM_MAX_TASKS", "0")
    normalized = pm_tasks.normalize_tasks(_sample_raw_tasks(7), iteration=1)
    assert len(normalized) == 7


