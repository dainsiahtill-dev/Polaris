"""Pre-split deterministic values and real KFS bytes remain unchanged."""

from __future__ import annotations

import hashlib
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.utils import time_utils


def test_budget_service_keeps_all_old_operation_values_and_exact_state_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = importlib.import_module("polaris.cells.finops.budget_guard.internal.budget_service")
    golden = json.loads((Path(__file__).parent / "fixtures/budget_service_golden_v1.json").read_text(encoding="utf-8"))

    class TickDateTime(datetime):
        tick = 0

        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            result = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc) + timedelta(microseconds=cls.tick)
            cls.tick += 1
            return result if tz else result.replace(tzinfo=None)

    monkeypatch.setattr(owner, "datetime", TickDateTime)
    monkeypatch.setattr(time_utils, "datetime", TickDateTime)
    service = owner.BudgetService(str(tmp_path))
    results: list[dict[str, Any]] = []

    def observe(name: str, result: dict[str, Any]) -> dict[str, Any]:
        results.append({"name": name, "result": result})
        return result

    observe("set_global", service.set_global_budget(2000, " tokens "))
    allocated = observe("allocate", service.allocate_budget("task-a", "context_tokens", 1000, "tokens"))
    budget_id = allocated["budget"]["budget_id"]
    observe("check_initial", service.check_budget("task-a", 500))
    observe("record", service.record_usage("task-a", "director", "context_tokens", 400))
    observe("check_exceeded", service.check_budget("task-a", 700))
    observe("status", service.get_budget_status("task-a"))
    observe("stats_task", service.get_usage_stats(task_id="task-a"))
    observe("stats_agent", service.get_usage_stats(agent_id="director"))
    observe("set_limit", service.set_budget_limit(budget_id, 500))
    observe("check_exact_remaining", service.check_budget("task-a", 100))
    observe("check_after_limit", service.check_budget("task-a", 101))
    observe("negative_usage", service.record_usage("task-a", "director", "context_tokens", -9))
    observe("allocate_empty_task", service.allocate_budget(" ", "general", 12, "tokens"))
    observe("set_unknown", service.set_budget_limit("absent", -1))
    observe("unallocated", service.check_budget("task-empty", 999))
    observe("negative_limit", service.allocate_budget("task-b", " general ", -7, " Credits "))
    observe("negative_estimate", service.check_budget("task-b", -1))
    observe("stats_empty", service.get_usage_stats())

    assert results == golden["operations"]
    state = tmp_path / ".polaris/runtime/state/budget/global.json"
    assert state.read_text(encoding="utf-8") == golden["serialized_state"]
    assert hashlib.sha256(state.read_bytes()).hexdigest() == golden["serialized_sha256"]
    before = state.read_bytes()
    reopened = owner.BudgetService(str(tmp_path))
    assert reopened.get_budget_status("task-a")["budgets"][0]["used"] == 400
    assert reopened.get_usage_stats(task_id="task-a")["totals"] == {"context_tokens": 400}
    assert state.read_bytes() == before


@pytest.mark.parametrize(
    "method,arguments",
    [
        ("allocate_budget", ("task", "general", "invalid")),
        ("check_budget", ("task", "invalid")),
        ("record_usage", ("task", "director", "general", "invalid")),
    ],
)
def test_invalid_numeric_budget_input_remains_visible_error_without_state_write(
    tmp_path: Path, method: str, arguments: tuple[Any, ...]
) -> None:
    owner = importlib.import_module("polaris.cells.finops.budget_guard.internal.budget_service")
    service = owner.BudgetService(str(tmp_path))
    state = tmp_path / ".polaris/runtime/state/budget/global.json"
    with pytest.raises(ValueError):
        getattr(service, method)(*arguments)
    assert not state.exists()
