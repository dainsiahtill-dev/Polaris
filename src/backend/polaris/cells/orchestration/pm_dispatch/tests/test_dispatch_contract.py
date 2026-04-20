"""Minimum test suite for `orchestration.pm_dispatch` public contracts and service.

Tests cover:
- DispatchPmTasksCommandV1: construction, empty-task-ids, options default
- ResumePmIterationCommandV1: required fields
- GetPmDispatchStatusQueryV1: required fields
- PmTaskDispatchedEventV1 / PmIterationAdvancedEventV1: event guards
- PmDispatchResultV1: ok flag, non-negative counters, counter range validation
- PmDispatchError: structured exception
- OrchestrationCommandService: CommandResult structure
- resolve_director_dispatch_tasks: task filtering (empty input, whitespace task IDs)
- ErrorCategory / ErrorClassifier: shared type imports from polaris.cells.orchestration.shared_types
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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
from polaris.cells.orchestration.pm_dispatch.public.service import (
    CommandResult,
    ErrorCategory,
    ErrorClassifier,
    resolve_director_dispatch_tasks,
)
from polaris.cells.orchestration.shared_types import ErrorCategory as SharedErrorCategory

# ---------------------------------------------------------------------------
# Happy path: command construction
# ---------------------------------------------------------------------------


class TestDispatchPmTasksCommandV1HappyPath:
    """DispatchPmTasksCommandV1 carries all fields correctly."""

    def test_basic_construction(self) -> None:
        cmd = DispatchPmTasksCommandV1(run_id="run-1", workspace="/ws", dispatcher="pm")
        assert cmd.run_id == "run-1"
        assert cmd.workspace == "/ws"
        assert cmd.dispatcher == "pm"

    def test_task_ids_default_empty_tuple(self) -> None:
        cmd = DispatchPmTasksCommandV1(run_id="run-2", workspace="/ws", dispatcher="pm")
        assert cmd.task_ids == ()

    def test_task_ids_coerced_to_tuple(self) -> None:
        cmd = DispatchPmTasksCommandV1(
            run_id="run-3",
            workspace="/ws",
            dispatcher="pm",
            task_ids=["t-1", "t-2"],  # type: ignore[arg-type]
        )
        assert isinstance(cmd.task_ids, tuple)
        assert len(cmd.task_ids) == 2

    def test_options_default_empty_dict(self) -> None:
        cmd = DispatchPmTasksCommandV1(run_id="run-4", workspace="/ws", dispatcher="pm")
        assert cmd.options == {}


class TestResumePmIterationCommandV1HappyPath:
    """ResumePmIterationCommandV1 requires all fields."""

    def test_basic_construction(self) -> None:
        cmd = ResumePmIterationCommandV1(run_id="run-5", workspace="/ws", iteration_id="iter-1", reason="manual retry")
        assert cmd.iteration_id == "iter-1"
        assert cmd.reason == "manual retry"


class TestGetPmDispatchStatusQueryV1HappyPath:
    """Query requires run_id and workspace."""

    def test_basic_construction(self) -> None:
        q = GetPmDispatchStatusQueryV1(run_id="run-6", workspace="/ws")
        assert q.run_id == "run-6"
        assert q.workspace == "/ws"


# ---------------------------------------------------------------------------
# Edge cases: empty-string guard
# ---------------------------------------------------------------------------


class TestDispatchPmTasksCommandV1EdgeCases:
    """Required string fields reject empty / whitespace inputs."""

    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            DispatchPmTasksCommandV1(run_id="", workspace="/ws", dispatcher="pm")

    def test_whitespace_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            DispatchPmTasksCommandV1(run_id="run-7", workspace="   ", dispatcher="pm")

    def test_whitespace_task_ids_filtered_out(self) -> None:
        cmd = DispatchPmTasksCommandV1(
            run_id="run-8",
            workspace="/ws",
            dispatcher="pm",
            task_ids=["t-1", "  ", "t-2", ""],  # type: ignore[arg-type]
        )
        # Whitespace-only and empty strings are filtered
        assert "  " not in cmd.task_ids
        assert "" not in cmd.task_ids


class TestResumePmIterationCommandV1EdgeCases:
    """All fields required, non-empty."""

    def test_empty_iteration_id_raises(self) -> None:
        with pytest.raises(ValueError, match="iteration_id"):
            ResumePmIterationCommandV1(run_id="run-9", workspace="/ws", iteration_id="", reason="x")

    def test_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            ResumePmIterationCommandV1(run_id="run-10", workspace="/ws", iteration_id="iter-2", reason="")


# ---------------------------------------------------------------------------
# Event contracts
# ---------------------------------------------------------------------------


class TestPmTaskDispatchedEventV1:
    """PmTaskDispatchedEventV1 enforces required fields."""

    def test_valid_construction(self) -> None:
        ev = PmTaskDispatchedEventV1(
            event_id="e-1",
            run_id="run-11",
            task_id="t-1",
            dispatched_to="director",
            dispatched_at="2026-01-01T00:00:00Z",
        )
        assert ev.dispatched_to == "director"

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError):
            PmTaskDispatchedEventV1(
                event_id="",
                run_id="run-12",
                task_id="t-2",
                dispatched_to="director",
                dispatched_at="2026-01-01T00:00:00Z",
            )


class TestPmIterationAdvancedEventV1:
    """PmIterationAdvancedEventV1 enforces required fields."""

    def test_valid_construction(self) -> None:
        ev = PmIterationAdvancedEventV1(
            event_id="e-2",
            run_id="run-13",
            iteration_id="iter-3",
            status="completed",
            advanced_at="2026-01-01T00:00:00Z",
        )
        assert ev.status == "completed"

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError):
            PmIterationAdvancedEventV1(
                event_id="e-3", run_id="run-14", iteration_id="iter-4", status="", advanced_at="2026-01-01T00:00:00Z"
            )


# ---------------------------------------------------------------------------
# PmDispatchResultV1
# ---------------------------------------------------------------------------


class TestPmDispatchResultV1:
    """Result tracks dispatch counters and enforces non-negative values."""

    def test_success_result_construction(self) -> None:
        result = PmDispatchResultV1(
            ok=True, run_id="run-15", status="dispatched", dispatched_count=5, skipped_count=1, failed_count=0
        )
        assert result.ok is True
        assert result.dispatched_count == 5

    def test_counters_must_be_non_negative(self) -> None:
        with pytest.raises(ValueError, match="dispatch counters"):
            PmDispatchResultV1(ok=False, run_id="run-16", status="failed", dispatched_count=-1)

    def test_failed_count_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="dispatch counters"):
            PmDispatchResultV1(ok=False, run_id="run-17", status="failed", failed_count=-1)


# ---------------------------------------------------------------------------
# PmDispatchError
# ---------------------------------------------------------------------------


class TestPmDispatchError:
    """Structured exception."""

    def test_default_code(self) -> None:
        err = PmDispatchError("dispatch pipeline failed")
        assert err.code == "pm_dispatch_error"

    def test_custom_code_and_details(self) -> None:
        err = PmDispatchError("workspace not found", code="WORKSPACE_NOT_FOUND", details={"workspace": "/nonexistent"})
        assert err.code == "WORKSPACE_NOT_FOUND"
        assert err.details == {"workspace": "/nonexistent"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            PmDispatchError("")


# ---------------------------------------------------------------------------
# resolve_director_dispatch_tasks: keyword-only, requires orchestration infra
# ---------------------------------------------------------------------------


class TestResolveDirectorDispatchTasks:
    """resolve_director_dispatch_tasks is keyword-only and requires workspace/tasks."""

    def test_signature_is_keyword_only(self) -> None:
        import inspect

        sig = inspect.signature(resolve_director_dispatch_tasks)
        params = list(sig.parameters.values())
        assert all(p.kind == inspect.Parameter.KEYWORD_ONLY for p in params)

    def test_empty_tasks_returns_empty_dispatch(self) -> None:
        tasks_out, summary = resolve_director_dispatch_tasks(workspace_full="/ws", tasks=[])
        assert tasks_out == []
        assert isinstance(summary, dict)

    def test_whitespace_task_ids_are_preserved(self) -> None:
        # Caller guards empty/whitespace task identity; with a real id field,
        # tasks pass through the shangshuling filter unchanged.  Use a mock port
        # that declares the tasks as ready so the test is deterministic and
        # isolated from any pre-existing registry file on disk.
        port = MagicMock()
        port.sync_tasks_to_shangshuling.return_value = 2
        port.get_shangshuling_ready_tasks.return_value = [
            {"id": "t-1"},
            {"id": "t-2"},
        ]
        tasks_out, _summary = resolve_director_dispatch_tasks(
            workspace_full="/ws",
            tasks=[{"id": "t-1"}, {"id": "t-2"}],
            shangshuling_port=port,
        )
        assert len(tasks_out) == 2


# ---------------------------------------------------------------------------
# OrchestrationCommandService / CommandResult
# ---------------------------------------------------------------------------


class TestOrchestrationCommandServiceResult:
    """CommandResult is a TypedDict-backed result carrier."""

    def test_command_result_is_dict(self) -> None:
        result: CommandResult = CommandResult(  # type: ignore[assignment]
            run_id="test-run",
            status="completed",
            message="dispatched",
        )
        assert result.status == "completed"
        assert result.message == "dispatched"


# ---------------------------------------------------------------------------
# ErrorCategory / ErrorClassifier from shared_types
# ---------------------------------------------------------------------------


class TestSharedErrorCategory:
    """shared_types ErrorCategory is accessible through the pm_dispatch export."""

    # Verify both aliases point to the same enum
    def test_local_alias_matches_shared(self) -> None:
        assert ErrorCategory is SharedErrorCategory

    def test_error_classifier_exists(self) -> None:
        # ErrorClassifier is a protocol / callable in shared_types
        assert ErrorClassifier is not None


# ---------------------------------------------------------------------------
# state_bridge.py — _normalize_status, StateSyncEvent, TaskBoardStateBridge,
# StateConsistencyChecker
# ---------------------------------------------------------------------------


class TestNormalizeStatus:
    """_normalize_status maps varied status tokens to canonical strings."""

    def test_in_progress_becomes_running(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            _normalize_status,
        )

        assert _normalize_status("in_progress") == "running"
        assert _normalize_status("IN_PROGRESS") == "running"
        # "In Progress" (with spaces) lowercases to "in progress" — not in the
        # {"in_progress","running"} set, so it falls through to the else branch
        assert _normalize_status("In Progress") == "in progress"

    def test_running_becomes_running(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            _normalize_status,
        )

        assert _normalize_status("running") == "running"
        assert _normalize_status("RUNNING") == "running"

    def test_done_becomes_completed(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            _normalize_status,
        )

        assert _normalize_status("done") == "completed"
        assert _normalize_status("Done") == "completed"

    def test_unknown_becomes_lowercased(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            _normalize_status,
        )

        assert _normalize_status("blocked") == "blocked"
        assert _normalize_status("pending") == "pending"

    def test_none_yields_pending(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            _normalize_status,
        )

        assert _normalize_status(None) == "pending"

    def test_empty_string_yields_pending(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            _normalize_status,
        )

        assert _normalize_status("") == "pending"

    def test_whitespace_stripped(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            _normalize_status,
        )

        assert _normalize_status("  running  ") == "running"


class TestStateSyncEvent:
    """StateSyncEvent dataclass carries event metadata."""

    def test_construction(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            StateSyncEvent,
        )

        evt = StateSyncEvent(
            event_type="task_created",
            task_id="t-1",
            status="pending",
            workflow_id="wf-1",
        )
        assert evt.event_type == "task_created"
        assert evt.task_id == "t-1"
        assert evt.status == "pending"
        assert evt.workflow_id == "wf-1"

    def test_defaults(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            StateSyncEvent,
        )

        evt = StateSyncEvent(
            event_type="task_updated",
            task_id="t-2",
            status="running",
            workflow_id="wf-1",
        )
        assert evt.task_type == "taskboard.task"
        assert evt.handler_name == "task_board"
        assert evt.metadata == {}
        assert evt.created_at != ""

    def test_custom_fields(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            StateSyncEvent,
        )

        evt = StateSyncEvent(
            event_type="task_completed",
            task_id="t-3",
            status="completed",
            workflow_id="wf-2",
            task_type="custom.task",
            handler_name="custom_handler",
            metadata={"result_summary": "ok"},
            created_at="2026-01-01T00:00:00Z",
        )
        assert evt.task_type == "custom.task"
        assert evt.handler_name == "custom_handler"
        assert evt.metadata == {"result_summary": "ok"}
        assert evt.created_at == "2026-01-01T00:00:00Z"


class TestTaskBoardStateBridgeNotifications:
    """notify_task_created/updated/completed enqueue StateSyncEvent records."""

    def test_notify_task_created_enqueues_event(self) -> None:
        from unittest.mock import MagicMock

        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            TaskBoardStateBridge,
        )

        mock_board = MagicMock()
        bridge = TaskBoardStateBridge(task_board=mock_board)

        bridge.notify_task_created(
            task_id=42,
            subject="implement login",
            status="in_progress",
            blocked_by=[1, 2],
        )

        with bridge._pending_lock:
            assert len(bridge._pending_events) == 1

        evt = bridge._pending_events[0]
        assert evt.event_type == "task_created"
        assert evt.task_id == "42"
        assert evt.status == "running"  # normalised
        assert evt.metadata["subject"] == "implement login"
        assert evt.metadata["blocked_by"] == [1, 2]

    def test_notify_task_updated_enqueues_event(self) -> None:
        from unittest.mock import MagicMock

        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            TaskBoardStateBridge,
        )

        mock_board = MagicMock()
        bridge = TaskBoardStateBridge(task_board=mock_board)

        bridge.notify_task_updated(task_id="t-1", status="done")

        with bridge._pending_lock:
            assert len(bridge._pending_events) == 1

        evt = bridge._pending_events[0]
        assert evt.event_type == "task_updated"
        assert evt.task_id == "t-1"
        assert evt.status == "completed"  # normalised from "done"

    def test_notify_task_completed_sets_status_completed(self) -> None:
        from unittest.mock import MagicMock

        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            TaskBoardStateBridge,
        )

        mock_board = MagicMock()
        bridge = TaskBoardStateBridge(task_board=mock_board)

        bridge.notify_task_completed(task_id="t-5", result_summary="all ok")

        with bridge._pending_lock:
            assert len(bridge._pending_events) == 1

        evt = bridge._pending_events[0]
        assert evt.event_type == "task_completed"
        assert evt.status == "completed"
        assert evt.metadata["result_summary"] == "all ok"

    def test_workflow_id_defaults_to_configured_value(self) -> None:
        from unittest.mock import MagicMock

        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            TaskBoardStateBridge,
        )

        mock_board = MagicMock()
        bridge = TaskBoardStateBridge(
            task_board=mock_board,
            default_workflow_id="my-workflow",
        )

        bridge.notify_task_created(task_id="t-1", status="pending")

        with bridge._pending_lock:
            evt = bridge._pending_events[0]
            assert evt.workflow_id == "my-workflow"


class TestStateConsistencyChecker:
    """StateConsistencyChecker identifies mismatches between task boards."""

    def test_consistent_returns_true(self) -> None:
        import asyncio
        from dataclasses import dataclass

        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            StateConsistencyChecker,
        )

        @dataclass
        class FakeTask:
            id: str
            status: str

        @dataclass
        class FakeState:
            task_id: str
            status: str

        mock_board = MagicMock()
        mock_board.list_all.return_value = [
            FakeTask(id="t-1", status="running"),
            FakeTask(id="t-2", status="completed"),
        ]

        mock_store = MagicMock()
        mock_store.list_task_states = AsyncMock(
            return_value=[
                FakeState(task_id="t-1", status="running"),
                FakeState(task_id="t-2", status="completed"),
            ]
        )

        checker = StateConsistencyChecker(task_board=mock_board, workflow_store=mock_store)

        async def run():
            return await checker.check_consistency("wf-1")

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert result["consistent"] is True
            assert result["summary"]["status_mismatch"] == 0
            assert result["summary"]["checked"] == 2
        finally:
            loop.close()

    def test_missing_in_workflow_flagged(self) -> None:
        import asyncio
        from dataclasses import dataclass

        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            StateConsistencyChecker,
        )

        @dataclass
        class FakeTask:
            id: str
            status: str

        @dataclass
        class FakeState:
            task_id: str
            status: str

        mock_board = MagicMock()
        mock_board.list_all.return_value = [
            FakeTask(id="t-1", status="running"),
            FakeTask(id="t-2", status="running"),  # t-2 missing in workflow
        ]

        mock_store = MagicMock()
        mock_store.list_task_states = AsyncMock(
            return_value=[
                FakeState(task_id="t-1", status="running"),
            ]
        )

        checker = StateConsistencyChecker(task_board=mock_board, workflow_store=mock_store)

        async def run():
            return await checker.check_consistency("wf-1")

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert result["consistent"] is False
            assert result["summary"]["missing_in_workflow"] == 1
            mismatch_types = [d["type"] for d in result["inconsistencies"]]
            assert "missing_in_workflow" in mismatch_types
        finally:
            loop.close()

    def test_status_mismatch_counted(self) -> None:
        import asyncio
        from dataclasses import dataclass

        from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
            StateConsistencyChecker,
        )

        @dataclass
        class FakeTask:
            id: str
            status: str

        @dataclass
        class FakeState:
            task_id: str
            status: str

        mock_board = MagicMock()
        mock_board.list_all.return_value = [
            FakeTask(id="t-1", status="running"),
        ]

        mock_store = MagicMock()
        mock_store.list_task_states = AsyncMock(
            return_value=[
                FakeState(task_id="t-1", status="completed"),  # status mismatch
            ]
        )

        checker = StateConsistencyChecker(task_board=mock_board, workflow_store=mock_store)

        async def run():
            return await checker.check_consistency("wf-1")

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert result["consistent"] is False
            assert result["summary"]["status_mismatch"] == 1
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# orchestration_command_service.py — static helpers, dataclasses, convenience
# ---------------------------------------------------------------------------


class TestCoerceMetadataOverrides:
    """_coerce_metadata_overrides filters and normalises a metadata dict."""

    def test_passes_valid_keys(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            _coerce_metadata_overrides,
        )

        result = _coerce_metadata_overrides({"max_workers": 4, "timeout": 30})
        assert result == {"max_workers": 4, "timeout": 30}

    def test_strips_whitespace_from_keys(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            _coerce_metadata_overrides,
        )

        result = _coerce_metadata_overrides({"  key1  ": "v", "key2": "v2"})
        assert "key1" in result
        assert "  key1  " not in result

    def test_skips_empty_keys(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            _coerce_metadata_overrides,
        )

        result = _coerce_metadata_overrides({"valid": 1, "": 2, "   ": 3})
        assert "valid" in result
        assert "" not in result
        assert "   " not in result

    def test_non_dict_returns_empty(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            _coerce_metadata_overrides,
        )

        assert _coerce_metadata_overrides(None) == {}
        assert _coerce_metadata_overrides("string") == {}
        assert _coerce_metadata_overrides(123) == {}


class TestOrchestrationCommandServiceStatics:
    """OrchestrationCommandService static helpers are pure and testable."""

    def test_trim_error_text_short_string_unchanged(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        text = "short error"
        assert OrchestrationCommandService._trim_error_text(text) == "short error"

    def test_trim_error_text_long_string_truncated(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        long_text = "a" * 300
        result = OrchestrationCommandService._trim_error_text(long_text, limit=240)
        assert len(result) < len(long_text)
        assert result.endswith("…")

    def test_trim_error_text_none_returns_empty(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        assert OrchestrationCommandService._trim_error_text(None) == ""

    def test_trim_error_text_empty_returns_empty(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        assert OrchestrationCommandService._trim_error_text("") == ""

    def test_build_failed_task_summaries_empty_tasks(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        mock_snapshot = MagicMock()
        mock_snapshot.tasks = {}
        result = OrchestrationCommandService._build_failed_task_summaries(mock_snapshot)
        assert result == []

    def test_build_task_status_counts_empty_tasks(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        mock_snapshot = MagicMock()
        mock_snapshot.tasks = {}
        result = OrchestrationCommandService._build_task_status_counts(mock_snapshot)
        assert result == {}

    def test_generate_run_id_prefix(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        # Service needs settings; use object()
        svc = OrchestrationCommandService(settings=object())
        run_id = svc._generate_run_id("pm")
        assert run_id.startswith("pm-")
        assert len(run_id) == len("pm-") + 12


class TestOrchestrationCommandServiceRunOptions:
    """PMRunOptions, DirectorRunOptions, FactoryRunOptions dataclasses."""

    def test_pm_run_options_defaults(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            PMRunOptions,
        )

        opts = PMRunOptions()
        assert opts.run_type == "full"
        assert opts.directive == ""
        assert opts.run_director is False
        assert opts.director_iterations == 2

    def test_director_run_options_defaults(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            DirectorRunOptions,
        )

        opts = DirectorRunOptions()
        assert opts.task_filter is None
        assert opts.max_workers >= 4  # based on cpu_count formula
        assert opts.execution_mode == "parallel"

    def test_factory_run_options_defaults(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            FactoryRunOptions,
        )

        opts = FactoryRunOptions()
        assert opts.config == {}
        assert opts.auto_start is True


class TestOrchestrationCommandServiceActiveRunTracking:
    """list_active_runs and clear_completed_runs operate on _active_runs."""

    def test_list_active_runs_empty(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        svc = OrchestrationCommandService(settings=object())
        assert svc.list_active_runs() == []

    def test_list_active_runs_filters_by_workspace(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        svc = OrchestrationCommandService(settings=object())
        svc._active_runs["r1"] = {"workspace": "/ws1", "role": "pm", "started_at": "t1", "status": "running"}
        svc._active_runs["r2"] = {"workspace": "/ws2", "role": "director", "started_at": "t2", "status": "running"}

        result = svc.list_active_runs(workspace="/ws1")
        assert len(result) == 1
        assert result[0]["run_id"] == "r1"

    def test_clear_completed_runs_removes_completed(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        svc = OrchestrationCommandService(settings=object())
        svc._active_runs["r1"] = {"workspace": "/ws", "role": "pm", "started_at": "t", "status": "completed"}
        svc._active_runs["r2"] = {"workspace": "/ws", "role": "pm", "started_at": "t", "status": "running"}

        count = svc.clear_completed_runs()
        assert count == 1
        assert "r1" not in svc._active_runs
        assert "r2" in svc._active_runs


class TestOrchestrationCommandServiceGetRunStatus:
    """get_run_status returns CommandResult for tracked runs."""

    def test_get_run_status_unknown_returns_none(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        svc = OrchestrationCommandService(settings=object())
        assert svc.get_run_status("does-not-exist") is None

    def test_get_run_status_returns_result(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
            OrchestrationCommandService,
        )

        svc = OrchestrationCommandService(settings=object())
        svc._active_runs["run-abc"] = {
            "workspace": "/ws",
            "role": "pm",
            "started_at": "2026-03-23T10:00:00Z",
            "status": "running",
        }

        result = svc.get_run_status("run-abc")
        assert result is not None
        assert result.run_id == "run-abc"
        assert result.status == "running"
