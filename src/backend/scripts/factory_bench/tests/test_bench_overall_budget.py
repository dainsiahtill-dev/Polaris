"""--overall-timeout 必须真的参与控制流。

此前它只被校验（!=6000 就报错）和记录进报告，从不限制跑批：120 个项目每个最多
5400s，实际要跑 7 天以上，而这个参数假装兜住了。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _cli_module():
    path = Path(__file__).resolve().parents[1] / "_bench_lib" / "cli.py"
    spec = importlib.util.spec_from_file_location("bench_cli_overall_budget", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_overall_budget_exhausted_record_is_machine_readable() -> None:
    module = _cli_module()
    record = module.overall_budget_exhausted_record(budget_seconds=6000, completed=7, selected=120)
    assert record["reason"] == "overall_budget_exhausted"
    assert record["exit_code"] == 2
    assert record["harness_status"] == "UNKNOWN"
    assert record["completed_records"] == 7 and record["selected_count"] == 120


def test_deadline_check_stops_before_the_next_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """预算耗尽必须在开下一个项目**之前**停，而不是让它跑满 5400s。"""

    module = _cli_module()
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    deadline = 100.0 + 6000.0
    assert module.overall_budget_exhausted_record(budget_seconds=6000, completed=0, selected=1)
    # 未到期：还可以跑
    now[0] = 100.0 + 5999.0
    assert now[0] < deadline
    # 到期：停
    now[0] = 100.0 + 6001.0
    assert now[0] >= deadline
