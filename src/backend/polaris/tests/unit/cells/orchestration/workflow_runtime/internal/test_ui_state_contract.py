"""Tests for workflow_runtime internal ui_state_contract module."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from polaris.cells.orchestration.workflow_runtime.internal.runtime_contracts import (
    FileChangeStats,
    OrchestrationSnapshot,
    RunStatus,
    TaskPhase,
    TaskSnapshot,
)
from polaris.cells.orchestration.workflow_runtime.internal.ui_state_contract import (
    UIFileChangeMetrics,
    UIOrchestrationState,
    UIPhase,
    UIStateConverter,
    UITaskItem,
    UITaskStatus,
)


class TestUIFileChangeMetrics:
    def test_to_display_string(self) -> None:
        metrics = UIFileChangeMetrics(created=1, modified=2, deleted=3, lines_added=10, lines_removed=5, lines_changed=2)
        assert metrics.to_display_string() == "C1/M2/D3 +10/-5/~2"

    def test_to_dict(self) -> None:
        metrics = UIFileChangeMetrics(created=1, modified=2)
        d = metrics.to_dict()
        assert d["created"] == 1
        assert d["modified"] == 2


class TestUITaskItem:
    def test_to_dict(self) -> None:
        item = UITaskItem(
            task_id="t1",
            role_id="pm",
            status=UITaskStatus.RUNNING,
            phase=UIPhase.EXECUTING,
        )
        d = item.to_dict()
        assert d["task_id"] == "t1"
        assert d["status"] == "running"
        assert d["phase"] == "executing"


class TestUIOrchestrationState:
    def test_to_dict(self) -> None:
        state = UIOrchestrationState(run_id="r1", workspace="/tmp")
        d = state.to_dict()
        assert d["run_id"] == "r1"
        assert d["workspace"] == "/tmp"
        assert d["overall_status"] == "pending"


class TestUIStateConverter:
    def test_from_orchestration_snapshot(self) -> None:
        snapshot = OrchestrationSnapshot(
            run_id="r1",
            workspace="/tmp",
            status=RunStatus.RUNNING,
            current_phase=TaskPhase.EXECUTING,
            tasks={
                "t1": TaskSnapshot(
                    task_id="t1",
                    status=RunStatus.RUNNING,
                    phase=TaskPhase.EXECUTING,
                    role_id="pm",
                    file_changes=FileChangeStats(created=1, modified=2),
                )
            },
        )
        ui_state = UIStateConverter.from_orchestration_snapshot(snapshot)
        assert ui_state.run_id == "r1"
        assert ui_state.overall_status == UITaskStatus.RUNNING
        assert "t1" in ui_state.tasks
        assert ui_state.total_file_changes.created == 1

    def test_calculate_latency(self) -> None:
        state = UIOrchestrationState(run_id="r1")
        latency = UIStateConverter.calculate_latency(state)
        assert latency >= 0.0
