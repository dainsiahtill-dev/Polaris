"""Tests for polaris.cells.director.planning.public contracts.

Covers dataclass validation, error classes, and public surface imports
from the planning cell boundary.
"""

from __future__ import annotations

import pytest
from polaris.cells.director.planning.public.contracts import (
    DirectorPlanningError,
    DirectorPlanningResultV1,
    GetDirectorStatusQueryV1,
    PlanDirectorTaskCommandV1,
)


class TestPlanDirectorTaskCommandV1:
    """Tests for PlanDirectorTaskCommandV1."""

    def test_valid_command(self) -> None:
        cmd = PlanDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="plan it")
        assert cmd.task_id == "t1"
        assert cmd.workspace == "/ws"
        assert cmd.instruction == "plan it"
        assert cmd.run_id is None
        assert cmd.attempt == 1
        assert cmd.metadata == {}

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            PlanDirectorTaskCommandV1(task_id="", workspace="/ws", instruction="plan it")

    def test_whitespace_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            PlanDirectorTaskCommandV1(task_id="   ", workspace="/ws", instruction="plan it")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            PlanDirectorTaskCommandV1(task_id="t1", workspace="", instruction="plan it")

    def test_empty_instruction_raises(self) -> None:
        with pytest.raises(ValueError, match="instruction must be a non-empty string"):
            PlanDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="")

    def test_attempt_less_than_one_raises(self) -> None:
        with pytest.raises(ValueError, match="attempt must be >= 1"):
            PlanDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="plan it", attempt=0)

    def test_attempt_one_is_valid(self) -> None:
        cmd = PlanDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="plan it", attempt=1)
        assert cmd.attempt == 1

    def test_metadata_copied(self) -> None:
        original = {"key": "value"}
        cmd = PlanDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="plan it", metadata=original)
        assert cmd.metadata == {"key": "value"}
        original["key"] = "changed"
        assert cmd.metadata == {"key": "value"}

    def test_run_id_optional(self) -> None:
        cmd = PlanDirectorTaskCommandV1(task_id="t1", workspace="/ws", instruction="plan it", run_id="r1")
        assert cmd.run_id == "r1"

    def test_none_metadata_becomes_empty_dict(self) -> None:
        cmd = PlanDirectorTaskCommandV1(
            task_id="t1",
            workspace="/ws",
            instruction="plan it",
            metadata=None,  # type: ignore[arg-type]
        )
        assert cmd.metadata == {}


class TestGetDirectorStatusQueryV1:
    """Tests for GetDirectorStatusQueryV1."""

    def test_valid_query(self) -> None:
        q = GetDirectorStatusQueryV1(task_id="t1", workspace="/ws")
        assert q.task_id == "t1"
        assert q.workspace == "/ws"
        assert q.run_id is None

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GetDirectorStatusQueryV1(task_id="", workspace="/ws")

    def test_whitespace_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GetDirectorStatusQueryV1(task_id="   ", workspace="/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            GetDirectorStatusQueryV1(task_id="t1", workspace="")

    def test_with_run_id(self) -> None:
        q = GetDirectorStatusQueryV1(task_id="t1", workspace="/ws", run_id="r1")
        assert q.run_id == "r1"


class TestDirectorPlanningResultV1:
    """Tests for DirectorPlanningResultV1."""

    def test_valid_success_result(self) -> None:
        r = DirectorPlanningResultV1(ok=True, task_id="t1", workspace="/ws", status="planned")
        assert r.ok is True
        assert r.plan_summary == ""
        assert r.error_code is None
        assert r.error_message is None

    def test_failed_result_without_error_raises(self) -> None:
        with pytest.raises(ValueError, match="failed result must include error_code or error_message"):
            DirectorPlanningResultV1(ok=False, task_id="t1", workspace="/ws", status="failed")

    def test_failed_result_with_error_code_ok(self) -> None:
        r = DirectorPlanningResultV1(
            ok=False,
            task_id="t1",
            workspace="/ws",
            status="failed",
            error_code="E1",
        )
        assert r.ok is False
        assert r.error_code == "E1"

    def test_failed_result_with_error_message_ok(self) -> None:
        r = DirectorPlanningResultV1(
            ok=False,
            task_id="t1",
            workspace="/ws",
            status="failed",
            error_message="something broke",
        )
        assert r.ok is False
        assert r.error_message == "something broke"

    def test_plan_summary_set(self) -> None:
        r = DirectorPlanningResultV1(
            ok=True,
            task_id="t1",
            workspace="/ws",
            status="planned",
            plan_summary="summary text",
        )
        assert r.plan_summary == "summary text"

    def test_run_id_optional(self) -> None:
        r = DirectorPlanningResultV1(ok=True, task_id="t1", workspace="/ws", status="planned", run_id="r1")
        assert r.run_id == "r1"

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            DirectorPlanningResultV1(ok=True, task_id="", workspace="/ws", status="planned")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            DirectorPlanningResultV1(ok=True, task_id="t1", workspace="", status="planned")

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status must be a non-empty string"):
            DirectorPlanningResultV1(ok=True, task_id="t1", workspace="/ws", status="")


class TestDirectorPlanningError:
    """Tests for DirectorPlanningError."""

    def test_defaults(self) -> None:
        err = DirectorPlanningError("boom")
        assert str(err) == "boom"
        assert err.code == "director_planning_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = DirectorPlanningError("boom", code="E1", details={"k": "v"})
        assert err.code == "E1"
        assert err.details == {"k": "v"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            DirectorPlanningError("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            DirectorPlanningError("boom", code="")

    def test_none_details_becomes_empty_dict(self) -> None:
        err = DirectorPlanningError("boom", details=None)
        assert err.details == {}

    def test_details_copied(self) -> None:
        original = {"key": "value"}
        err = DirectorPlanningError("boom", details=original)
        assert err.details == {"key": "value"}
        original["key"] = "changed"
        assert err.details == {"key": "value"}


class TestPublicSurfaceImports:
    """Smoke tests that public surface exports are importable."""

    def test_import_all_from_public_init(self) -> None:
        from polaris.cells.director.planning.public import (
            DirectorPlanningError,
            DirectorPlanningResultV1,
            GetDirectorStatusQueryV1,
            PlanDirectorTaskCommandV1,
        )

        assert DirectorPlanningError is not None
        assert DirectorPlanningResultV1 is not None
        assert GetDirectorStatusQueryV1 is not None
        assert PlanDirectorTaskCommandV1 is not None
