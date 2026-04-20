"""Unit tests for orchestration.pm_planning public contracts.

Tests frozen dataclasses, validation, serialisation,
and the PmPlanningError custom error type.
"""

from __future__ import annotations

import pytest
from polaris.cells.orchestration.pm_planning.public.contracts import (
    GeneratePmTaskContractCommandV1,
    GetPmPlanningStatusQueryV1,
    PmPlanningError,
    PmTaskContractGeneratedEventV1,
    PmTaskContractResultV1,
)

# ---------------------------------------------------------------------------
# GeneratePmTaskContractCommandV1
# ---------------------------------------------------------------------------


class TestGeneratePmTaskContractCommandV1HappyPath:
    def test_minimal(self) -> None:
        cmd = GeneratePmTaskContractCommandV1(
            run_id="run-1",
            workspace="/ws",
            directive="build a login feature",
        )
        assert cmd.run_id == "run-1"
        assert cmd.workspace == "/ws"
        assert cmd.directive == "build a login feature"
        assert cmd.task_count_hint == 0
        assert cmd.context == {}

    def test_full(self) -> None:
        cmd = GeneratePmTaskContractCommandV1(
            run_id="run-2",
            workspace="/repo",
            directive="implement auth",
            task_count_hint=5,
            context={"key": "value"},
        )
        assert cmd.task_count_hint == 5
        assert cmd.context == {"key": "value"}

    def test_whitespace_normalised(self) -> None:
        cmd = GeneratePmTaskContractCommandV1(
            run_id="  run-3  ",
            workspace="  /ws  ",
            directive="  do it  ",
        )
        assert cmd.run_id == "run-3"
        assert cmd.workspace == "/ws"
        assert cmd.directive == "do it"

    def test_context_copy(self) -> None:
        original = {"foo": "bar"}
        cmd = GeneratePmTaskContractCommandV1(
            run_id="r",
            workspace="/ws",
            directive="d",
            context=original,
        )
        original.clear()
        assert cmd.context == {"foo": "bar"}


class TestGeneratePmTaskContractCommandV1EdgeCases:
    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            GeneratePmTaskContractCommandV1(run_id="", workspace="/ws", directive="d")

    def test_whitespace_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            GeneratePmTaskContractCommandV1(run_id="   ", workspace="/ws", directive="d")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            GeneratePmTaskContractCommandV1(run_id="r", workspace="", directive="d")

    def test_empty_directive_raises(self) -> None:
        with pytest.raises(ValueError, match="directive"):
            GeneratePmTaskContractCommandV1(run_id="r", workspace="/ws", directive="")

    def test_negative_task_count_hint_raises(self) -> None:
        with pytest.raises(ValueError, match="task_count_hint"):
            GeneratePmTaskContractCommandV1(
                run_id="r",
                workspace="/ws",
                directive="d",
                task_count_hint=-1,
            )


# ---------------------------------------------------------------------------
# GetPmPlanningStatusQueryV1
# ---------------------------------------------------------------------------


class TestGetPmPlanningStatusQueryV1HappyPath:
    def test_construction(self) -> None:
        q = GetPmPlanningStatusQueryV1(run_id="run-1", workspace="/ws")
        assert q.run_id == "run-1"
        assert q.workspace == "/ws"


class TestGetPmPlanningStatusQueryV1EdgeCases:
    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            GetPmPlanningStatusQueryV1(run_id="", workspace="/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            GetPmPlanningStatusQueryV1(run_id="r", workspace="")


# ---------------------------------------------------------------------------
# PmTaskContractGeneratedEventV1
# ---------------------------------------------------------------------------


class TestPmTaskContractGeneratedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = PmTaskContractGeneratedEventV1(
            event_id="evt-1",
            run_id="run-1",
            workspace="/ws",
            contract_path="/ws/.polaris/contracts/tasks.json",
            generated_at="2026-03-23T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.run_id == "run-1"
        assert evt.contract_path == "/ws/.polaris/contracts/tasks.json"


class TestPmTaskContractGeneratedEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            PmTaskContractGeneratedEventV1(
                event_id="",
                run_id="r",
                workspace="/ws",
                contract_path="/p",
                generated_at="t",
            )

    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            PmTaskContractGeneratedEventV1(
                event_id="e",
                run_id="",
                workspace="/ws",
                contract_path="/p",
                generated_at="t",
            )

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            PmTaskContractGeneratedEventV1(
                event_id="e",
                run_id="r",
                workspace="",
                contract_path="/p",
                generated_at="t",
            )

    def test_empty_contract_path_raises(self) -> None:
        with pytest.raises(ValueError, match="contract_path"):
            PmTaskContractGeneratedEventV1(
                event_id="e",
                run_id="r",
                workspace="/ws",
                contract_path="",
                generated_at="t",
            )

    def test_empty_generated_at_raises(self) -> None:
        with pytest.raises(ValueError, match="generated_at"):
            PmTaskContractGeneratedEventV1(
                event_id="e",
                run_id="r",
                workspace="/ws",
                contract_path="/p",
                generated_at="",
            )


# ---------------------------------------------------------------------------
# PmTaskContractResultV1
# ---------------------------------------------------------------------------


class TestPmTaskContractResultV1HappyPath:
    def test_success(self) -> None:
        res = PmTaskContractResultV1(
            ok=True,
            run_id="run-1",
            workspace="/ws",
            status="success",
            contract_ids=("task-1", "task-2"),
            summary="2 tasks generated",
        )
        assert res.ok is True
        assert res.contract_ids == ("task-1", "task-2")
        assert res.summary == "2 tasks generated"

    def test_failure(self) -> None:
        res = PmTaskContractResultV1(
            ok=False,
            run_id="run-2",
            workspace="/ws",
            status="failed",
        )
        assert res.ok is False
        assert res.contract_ids == ()

    def test_contract_ids_whitespace_filtered(self) -> None:
        res = PmTaskContractResultV1(
            ok=True,
            run_id="r",
            workspace="/ws",
            status="ok",
            contract_ids=("a", "", "  ", "b"),
        )
        assert res.contract_ids == ("a", "b")


class TestPmTaskContractResultV1EdgeCases:
    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            PmTaskContractResultV1(ok=True, run_id="", workspace="/ws", status="ok")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            PmTaskContractResultV1(ok=True, run_id="r", workspace="", status="ok")

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            PmTaskContractResultV1(ok=True, run_id="r", workspace="/ws", status="")


# ---------------------------------------------------------------------------
# PmPlanningError
# ---------------------------------------------------------------------------


class TestPmPlanningError:
    def test_default_values(self) -> None:
        err = PmPlanningError("Something went wrong")
        assert str(err) == "Something went wrong"
        assert err.code == "pm_planning_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = PmPlanningError(
            "Contract invalid",
            code="contract_invalid",
            details={"run_id": "run-1"},
        )
        assert str(err) == "Contract invalid"
        assert err.code == "contract_invalid"
        assert err.details == {"run_id": "run-1"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            PmPlanningError("")

    def test_whitespace_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            PmPlanningError("   ")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            PmPlanningError("error", code="")

    def test_whitespace_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            PmPlanningError("error", code="   ")

    def test_inherits_runtime_error(self) -> None:
        err = PmPlanningError("boom")
        assert isinstance(err, RuntimeError)
