"""Unit tests for orchestration.pm_dispatch public contracts.

Tests frozen dataclasses, validation, serialisation,
and the PmDispatchError custom error type.
"""

from __future__ import annotations

import pytest
from polaris.cells.orchestration.pm_dispatch.public.contracts import (
    DispatchPmTasksCommandV1,
    GetPmDispatchStatusQueryV1,
    PmDispatchError,
    PmDispatchResultV1,
    PmIterationAdvancedEventV1,
    PmTaskDispatchedEventV1,
    ResumePmIterationCommandV1,
)

# ---------------------------------------------------------------------------
# DispatchPmTasksCommandV1
# ---------------------------------------------------------------------------


class TestDispatchPmTasksCommandV1HappyPath:
    def test_minimal(self) -> None:
        cmd = DispatchPmTasksCommandV1(
            run_id="run-1",
            workspace="/ws",
            dispatcher="pm",
        )
        assert cmd.run_id == "run-1"
        assert cmd.workspace == "/ws"
        assert cmd.dispatcher == "pm"
        assert cmd.task_ids == ()
        assert cmd.options == {}

    def test_full(self) -> None:
        cmd = DispatchPmTasksCommandV1(
            run_id="run-2",
            workspace="/repo",
            dispatcher="director",
            task_ids=("t-1", "t-2"),
            options={"max_workers": 4},
        )
        assert cmd.task_ids == ("t-1", "t-2")
        assert cmd.options == {"max_workers": 4}

    def test_whitespace_normalised(self) -> None:
        cmd = DispatchPmTasksCommandV1(
            run_id="  run-3  ",
            workspace="  /ws  ",
            dispatcher="  pm  ",
        )
        assert cmd.run_id == "run-3"
        assert cmd.workspace == "/ws"
        assert cmd.dispatcher == "pm"

    def test_task_ids_whitespace_filtered(self) -> None:
        cmd = DispatchPmTasksCommandV1(
            run_id="r",
            workspace="/ws",
            dispatcher="d",
            task_ids=["  t-1  ", "  ", "t-2", ""],  # type: ignore[arg-type]
        )
        # Empty/whitespace-only strings are filtered out; str() preserves spaces
        # inside non-empty values so "  t-1  " stays as-is
        assert "  " not in cmd.task_ids  # pure-whitespace entry removed
        assert "" not in cmd.task_ids
        assert "t-2" in cmd.task_ids
        assert len(cmd.task_ids) == 2

    def test_options_copy(self) -> None:
        original = {"foo": "bar"}
        cmd = DispatchPmTasksCommandV1(
            run_id="r",
            workspace="/ws",
            dispatcher="d",
            options=original,
        )
        original.clear()
        assert cmd.options == {"foo": "bar"}


class TestDispatchPmTasksCommandV1EdgeCases:
    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            DispatchPmTasksCommandV1(run_id="", workspace="/ws", dispatcher="pm")

    def test_whitespace_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            DispatchPmTasksCommandV1(run_id="   ", workspace="/ws", dispatcher="pm")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            DispatchPmTasksCommandV1(run_id="r", workspace="", dispatcher="pm")

    def test_empty_dispatcher_raises(self) -> None:
        with pytest.raises(ValueError, match="dispatcher"):
            DispatchPmTasksCommandV1(run_id="r", workspace="/ws", dispatcher="")


# ---------------------------------------------------------------------------
# ResumePmIterationCommandV1
# ---------------------------------------------------------------------------


class TestResumePmIterationCommandV1HappyPath:
    def test_construction(self) -> None:
        cmd = ResumePmIterationCommandV1(
            run_id="run-1",
            workspace="/ws",
            iteration_id="iter-1",
            reason="user requested retry",
        )
        assert cmd.run_id == "run-1"
        assert cmd.workspace == "/ws"
        assert cmd.iteration_id == "iter-1"
        assert cmd.reason == "user requested retry"

    def test_whitespace_normalised(self) -> None:
        cmd = ResumePmIterationCommandV1(
            run_id="  run-2  ",
            workspace="  /ws  ",
            iteration_id="  iter-2  ",
            reason="  manual  ",
        )
        assert cmd.run_id == "run-2"
        assert cmd.workspace == "/ws"
        assert cmd.iteration_id == "iter-2"
        assert cmd.reason == "manual"


class TestResumePmIterationCommandV1EdgeCases:
    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            ResumePmIterationCommandV1(
                run_id="",
                workspace="/ws",
                iteration_id="i",
                reason="r",
            )

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            ResumePmIterationCommandV1(
                run_id="r",
                workspace="",
                iteration_id="i",
                reason="r",
            )

    def test_empty_iteration_id_raises(self) -> None:
        with pytest.raises(ValueError, match="iteration_id"):
            ResumePmIterationCommandV1(
                run_id="r",
                workspace="/ws",
                iteration_id="",
                reason="r",
            )

    def test_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            ResumePmIterationCommandV1(
                run_id="r",
                workspace="/ws",
                iteration_id="i",
                reason="",
            )


# ---------------------------------------------------------------------------
# GetPmDispatchStatusQueryV1
# ---------------------------------------------------------------------------


class TestGetPmDispatchStatusQueryV1HappyPath:
    def test_construction(self) -> None:
        q = GetPmDispatchStatusQueryV1(run_id="run-1", workspace="/ws")
        assert q.run_id == "run-1"
        assert q.workspace == "/ws"

    def test_whitespace_normalised(self) -> None:
        q = GetPmDispatchStatusQueryV1(run_id="  run-2  ", workspace="  /ws  ")
        assert q.run_id == "run-2"
        assert q.workspace == "/ws"


class TestGetPmDispatchStatusQueryV1EdgeCases:
    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            GetPmDispatchStatusQueryV1(run_id="", workspace="/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            GetPmDispatchStatusQueryV1(run_id="r", workspace="")


# ---------------------------------------------------------------------------
# PmTaskDispatchedEventV1
# ---------------------------------------------------------------------------


class TestPmTaskDispatchedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = PmTaskDispatchedEventV1(
            event_id="evt-1",
            run_id="run-1",
            task_id="t-1",
            dispatched_to="director",
            dispatched_at="2026-03-23T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.run_id == "run-1"
        assert evt.task_id == "t-1"
        assert evt.dispatched_to == "director"

    def test_whitespace_normalised(self) -> None:
        evt = PmTaskDispatchedEventV1(
            event_id="  evt-2  ",
            run_id="  run-2  ",
            task_id="  t-2  ",
            dispatched_to="  qa  ",
            dispatched_at="  2026-01-01T00:00:00Z  ",
        )
        assert evt.event_id == "evt-2"
        assert evt.run_id == "run-2"
        assert evt.task_id == "t-2"
        assert evt.dispatched_to == "qa"


class TestPmTaskDispatchedEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            PmTaskDispatchedEventV1(
                event_id="",
                run_id="r",
                task_id="t",
                dispatched_to="d",
                dispatched_at="t",
            )

    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            PmTaskDispatchedEventV1(
                event_id="e",
                run_id="",
                task_id="t",
                dispatched_to="d",
                dispatched_at="t",
            )

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            PmTaskDispatchedEventV1(
                event_id="e",
                run_id="r",
                task_id="",
                dispatched_to="d",
                dispatched_at="t",
            )

    def test_empty_dispatched_to_raises(self) -> None:
        with pytest.raises(ValueError, match="dispatched_to"):
            PmTaskDispatchedEventV1(
                event_id="e",
                run_id="r",
                task_id="t",
                dispatched_to="",
                dispatched_at="t",
            )

    def test_empty_dispatched_at_raises(self) -> None:
        with pytest.raises(ValueError, match="dispatched_at"):
            PmTaskDispatchedEventV1(
                event_id="e",
                run_id="r",
                task_id="t",
                dispatched_to="d",
                dispatched_at="",
            )


# ---------------------------------------------------------------------------
# PmIterationAdvancedEventV1
# ---------------------------------------------------------------------------


class TestPmIterationAdvancedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = PmIterationAdvancedEventV1(
            event_id="evt-1",
            run_id="run-1",
            iteration_id="iter-1",
            status="completed",
            advanced_at="2026-03-23T10:00:00Z",
        )
        assert evt.iteration_id == "iter-1"
        assert evt.status == "completed"

    def test_whitespace_normalised(self) -> None:
        evt = PmIterationAdvancedEventV1(
            event_id="  evt-2  ",
            run_id="  run-2  ",
            iteration_id="  iter-2  ",
            status="  running  ",
            advanced_at="  2026-01-01T00:00:00Z  ",
        )
        assert evt.event_id == "evt-2"
        assert evt.iteration_id == "iter-2"
        assert evt.status == "running"


class TestPmIterationAdvancedEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            PmIterationAdvancedEventV1(
                event_id="",
                run_id="r",
                iteration_id="i",
                status="s",
                advanced_at="t",
            )

    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            PmIterationAdvancedEventV1(
                event_id="e",
                run_id="",
                iteration_id="i",
                status="s",
                advanced_at="t",
            )

    def test_empty_iteration_id_raises(self) -> None:
        with pytest.raises(ValueError, match="iteration_id"):
            PmIterationAdvancedEventV1(
                event_id="e",
                run_id="r",
                iteration_id="",
                status="s",
                advanced_at="t",
            )

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            PmIterationAdvancedEventV1(
                event_id="e",
                run_id="r",
                iteration_id="i",
                status="",
                advanced_at="t",
            )

    def test_empty_advanced_at_raises(self) -> None:
        with pytest.raises(ValueError, match="advanced_at"):
            PmIterationAdvancedEventV1(
                event_id="e",
                run_id="r",
                iteration_id="i",
                status="s",
                advanced_at="",
            )


# ---------------------------------------------------------------------------
# PmDispatchResultV1
# ---------------------------------------------------------------------------


class TestPmDispatchResultV1HappyPath:
    def test_success(self) -> None:
        res = PmDispatchResultV1(
            ok=True,
            run_id="run-1",
            status="dispatched",
            dispatched_count=5,
            skipped_count=1,
            failed_count=0,
            summary="6 tasks processed",
        )
        assert res.ok is True
        assert res.dispatched_count == 5
        assert res.skipped_count == 1
        assert res.failed_count == 0
        assert res.summary == "6 tasks processed"

    def test_failure(self) -> None:
        res = PmDispatchResultV1(
            ok=False,
            run_id="run-2",
            status="failed",
        )
        assert res.ok is False
        assert res.dispatched_count == 0
        assert res.skipped_count == 0
        assert res.failed_count == 0

    def test_whitespace_normalised(self) -> None:
        res = PmDispatchResultV1(
            ok=True,
            run_id="  run-3  ",
            status="  dispatched  ",
        )
        assert res.run_id == "run-3"
        assert res.status == "dispatched"

    def test_all_zero_counters(self) -> None:
        res = PmDispatchResultV1(ok=True, run_id="r", status="ok")
        assert res.dispatched_count == 0
        assert res.skipped_count == 0
        assert res.failed_count == 0


class TestPmDispatchResultV1EdgeCases:
    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            PmDispatchResultV1(ok=True, run_id="", status="ok")

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            PmDispatchResultV1(ok=True, run_id="r", status="")

    def test_negative_dispatched_count_raises(self) -> None:
        with pytest.raises(ValueError, match="dispatch counters"):
            PmDispatchResultV1(
                ok=False,
                run_id="r",
                status="fail",
                dispatched_count=-1,
            )

    def test_negative_skipped_count_raises(self) -> None:
        with pytest.raises(ValueError, match="dispatch counters"):
            PmDispatchResultV1(
                ok=False,
                run_id="r",
                status="fail",
                skipped_count=-1,
            )

    def test_negative_failed_count_raises(self) -> None:
        with pytest.raises(ValueError, match="dispatch counters"):
            PmDispatchResultV1(
                ok=False,
                run_id="r",
                status="fail",
                failed_count=-1,
            )


# ---------------------------------------------------------------------------
# PmDispatchError
# ---------------------------------------------------------------------------


class TestPmDispatchError:
    def test_default_values(self) -> None:
        err = PmDispatchError("Something went wrong")
        assert str(err) == "Something went wrong"
        assert err.code == "pm_dispatch_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = PmDispatchError(
            "Contract invalid",
            code="CONTRACT_INVALID",
            details={"run_id": "run-1", "task_id": "t-1"},
        )
        assert str(err) == "Contract invalid"
        assert err.code == "CONTRACT_INVALID"
        assert err.details == {"run_id": "run-1", "task_id": "t-1"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            PmDispatchError("")

    def test_whitespace_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            PmDispatchError("   ")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            PmDispatchError("error", code="")

    def test_whitespace_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            PmDispatchError("error", code="   ")

    def test_details_copy(self) -> None:
        original = {"key": "value"}
        err = PmDispatchError("x", details=original)
        original.clear()
        assert err.details == {"key": "value"}

    def test_inherits_runtime_error(self) -> None:
        err = PmDispatchError("boom")
        assert isinstance(err, RuntimeError)
