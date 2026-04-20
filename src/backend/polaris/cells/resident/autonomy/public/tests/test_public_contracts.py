"""Unit tests for `resident/autonomy` public contracts."""

from __future__ import annotations

import pytest
from polaris.cells.resident.autonomy.public.contracts import (
    QueryResidentStatusV1,
    RecordResidentEvidenceCommandV1,
    ResidentAutonomyError,
    ResidentAutonomyResultV1,
    ResidentCycleCompletedEventV1,
    RunResidentCycleCommandV1,
)


class TestRunResidentCycleCommandV1HappyPath:
    def test_construction(self) -> None:
        cmd = RunResidentCycleCommandV1(
            workspace="/repo",
            cycle_id="cycle-1",
            goal="Review open tasks",
        )
        assert cmd.workspace == "/repo"
        assert cmd.cycle_id == "cycle-1"
        assert cmd.goal == "Review open tasks"
        assert cmd.context == {}

    def test_with_context(self) -> None:
        cmd = RunResidentCycleCommandV1(
            workspace="/repo",
            cycle_id="cycle-1",
            goal="Review open tasks",
            context={"priority": "high"},
        )
        assert cmd.context == {"priority": "high"}

    def test_context_is_copied(self) -> None:
        original = {"priority": "high"}
        cmd = RunResidentCycleCommandV1(workspace="/repo", cycle_id="c1", goal="g", context=original)
        original.clear()
        assert cmd.context == {"priority": "high"}


class TestRunResidentCycleCommandV1EdgeCases:
    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            RunResidentCycleCommandV1(workspace="", cycle_id="c", goal="g")

    def test_empty_cycle_id_raises(self) -> None:
        with pytest.raises(ValueError, match="cycle_id"):
            RunResidentCycleCommandV1(workspace="/repo", cycle_id="", goal="g")

    def test_empty_goal_raises(self) -> None:
        with pytest.raises(ValueError, match="goal"):
            RunResidentCycleCommandV1(workspace="/repo", cycle_id="c", goal="")


class TestRecordResidentEvidenceCommandV1HappyPath:
    def test_construction(self) -> None:
        cmd = RecordResidentEvidenceCommandV1(
            workspace="/repo",
            cycle_id="cycle-1",
            evidence_kind="task_review",
            payload={"task_id": "task-1", "status": "reviewed"},
        )
        assert cmd.workspace == "/repo"
        assert cmd.cycle_id == "cycle-1"
        assert cmd.evidence_kind == "task_review"
        assert cmd.payload == {"task_id": "task-1", "status": "reviewed"}


class TestRecordResidentEvidenceCommandV1EdgeCases:
    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            RecordResidentEvidenceCommandV1(workspace="", cycle_id="c", evidence_kind="x", payload={})

    def test_empty_payload_raises(self) -> None:
        with pytest.raises(ValueError, match="payload"):
            RecordResidentEvidenceCommandV1(workspace="/repo", cycle_id="c", evidence_kind="x", payload={})


class TestQueryResidentStatusV1HappyPath:
    def test_workspace_only(self) -> None:
        q = QueryResidentStatusV1(workspace="/repo")
        assert q.workspace == "/repo"
        assert q.cycle_id is None

    def test_with_cycle_id(self) -> None:
        q = QueryResidentStatusV1(workspace="/repo", cycle_id="cycle-1")
        assert q.cycle_id == "cycle-1"


class TestQueryResidentStatusV1EdgeCases:
    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            QueryResidentStatusV1(workspace="")

    def test_whitespace_cycle_id_raises(self) -> None:
        with pytest.raises(ValueError, match="cycle_id"):
            QueryResidentStatusV1(workspace="/repo", cycle_id="  ")


class TestResidentCycleCompletedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = ResidentCycleCompletedEventV1(
            event_id="evt-1",
            workspace="/repo",
            cycle_id="cycle-1",
            status="completed",
            completed_at="2026-03-24T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.status == "completed"


class TestResidentCycleCompletedEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            ResidentCycleCompletedEventV1(
                event_id="",
                workspace="/repo",
                cycle_id="c",
                status="ok",
                completed_at="2026-03-24T10:00:00Z",
            )

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            ResidentCycleCompletedEventV1(
                event_id="e1",
                workspace="/repo",
                cycle_id="c",
                status="",
                completed_at="2026-03-24T10:00:00Z",
            )


class TestResidentAutonomyResultV1HappyPath:
    def test_success(self) -> None:
        res = ResidentAutonomyResultV1(
            ok=True,
            workspace="/repo",
            cycle_id="cycle-1",
            status="completed",
            actions=("review_tasks", "update_board"),
            evidence_refs=("ev-1",),
            metrics={"tasks_reviewed": 5},
        )
        assert res.ok is True
        assert res.actions == ("review_tasks", "update_board")
        assert res.evidence_refs == ("ev-1",)
        assert res.metrics == {"tasks_reviewed": 5}

    def test_failure(self) -> None:
        res = ResidentAutonomyResultV1(
            ok=False,
            workspace="/repo",
            cycle_id="cycle-1",
            status="failed",
        )
        assert res.ok is False

    def test_whitespace_actions_filtered(self) -> None:
        res = ResidentAutonomyResultV1(
            ok=True,
            workspace="/repo",
            cycle_id="c",
            status="ok",
            actions=("action1", "  ", "action2"),
        )
        assert res.actions == ("action1", "action2")


class TestResidentAutonomyResultV1EdgeCases:
    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            ResidentAutonomyResultV1(ok=True, workspace="", cycle_id="c", status="ok")

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            ResidentAutonomyResultV1(ok=True, workspace="/repo", cycle_id="c", status="")


class TestResidentAutonomyError:
    def test_default_values(self) -> None:
        err = ResidentAutonomyError("cycle execution failed")
        assert str(err) == "cycle execution failed"
        assert err.code == "resident_autonomy_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = ResidentAutonomyError(
            "timeout",
            code="cycle_timeout",
            details={"cycle_id": "cycle-1"},
        )
        assert err.code == "cycle_timeout"
        assert err.details == {"cycle_id": "cycle-1"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            ResidentAutonomyError("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            ResidentAutonomyError("error", code="  ")
