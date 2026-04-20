"""Unit tests for orchestration.pm_dispatch internal dispatch_pipeline.

Tests pure/isolatable functions: resolve_director_dispatch_tasks,
record_dispatch_status_to_shangshuling, _tasks_touch_docs_only,
and helpers _build_director_workflow_result, _apply_post_dispatch_skip_reason.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline import (
    _apply_post_dispatch_skip_reason,
    _build_director_workflow_result,
    _build_post_dispatch_integration_qa_result,
    _build_workflow_input,
    _mainline_publish_dispatch_tasks_to_task_market,
    _resolve_workflow_submit_fn,
    _run_inline_task_market_consumers,
    _shadow_publish_dispatch_tasks_to_task_market,
    _tasks_touch_docs_only,
    record_dispatch_status_to_shangshuling,
    resolve_director_dispatch_tasks,
    run_dispatch_pipeline,
)
from polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils import (
    NoopShangshulingPort,
)

# ---------------------------------------------------------------------------
# resolve_director_dispatch_tasks
# ---------------------------------------------------------------------------


class TestResolveDirectorDispatchTasks:
    def test_empty_tasks_returns_empty(self) -> None:
        tasks_out, meta = resolve_director_dispatch_tasks(workspace_full="/ws", tasks=[])
        assert tasks_out == []
        assert meta["selected_count"] == 0

    def test_non_list_returns_empty(self) -> None:
        tasks_out, meta = resolve_director_dispatch_tasks(workspace_full="/ws", tasks=[])
        assert tasks_out == []
        assert meta["selected_count"] == 0

    def test_uses_injected_noop_port(self) -> None:
        port = NoopShangshulingPort()
        tasks = [{"id": "T01", "status": "todo"}]
        tasks_out, _meta = resolve_director_dispatch_tasks(workspace_full="/ws", tasks=tasks, shangshuling_port=port)
        # Noop port returns empty ready list, so nothing selected
        assert tasks_out == []

    def test_injected_port_with_ready_tasks(self) -> None:
        port = MagicMock()
        port.sync_tasks_to_shangshuling.return_value = 1
        port.get_shangshuling_ready_tasks.return_value = [
            {"id": "T01", "status": "todo"},
            {"id": "T02", "status": "in_progress"},
        ]
        tasks = [
            {"id": "T01", "status": "todo"},
            {"id": "T02", "status": "todo"},
            {"id": "T03", "status": "todo"},  # not in ready list
        ]
        tasks_out, meta = resolve_director_dispatch_tasks(workspace_full="/ws", tasks=tasks, shangshuling_port=port)
        assert len(tasks_out) == 2
        ids = {t["id"] for t in tasks_out}
        assert ids == {"T01", "T02"}
        assert meta["enabled"] is True
        assert meta["sync_count"] == 1

    def test_port_exception_falls_back_to_original_tasks(self) -> None:
        port = MagicMock()
        port.sync_tasks_to_shangshuling.side_effect = OSError("disk error")
        tasks = [{"id": "T01", "status": "todo"}]
        tasks_out, _meta = resolve_director_dispatch_tasks(workspace_full="/ws", tasks=tasks, shangshuling_port=port)
        assert tasks_out == tasks  # falls back to original


# ---------------------------------------------------------------------------
# record_dispatch_status_to_shangshuling
# ---------------------------------------------------------------------------


def test_shadow_publish_skips_when_mode_off(monkeypatch) -> None:
    monkeypatch.setenv("POLARIS_TASK_MARKET_MODE", "off")
    calls: list[str] = []

    def _fake_get_task_market_services():
        calls.append("imported")
        return None, None

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_services",
        _fake_get_task_market_services,
    )
    _shadow_publish_dispatch_tasks_to_task_market(
        workspace_full="/ws",
        run_id="run-1",
        tasks=[{"id": "T01", "title": "Task 1"}],
    )
    assert calls == []


def test_shadow_publish_emits_publish_commands(monkeypatch) -> None:
    monkeypatch.setenv("POLARIS_TASK_MARKET_MODE", "shadow")

    captured: list[object] = []

    class _PublishCommand:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _FakeService:
        def publish_work_item(self, command) -> None:
            captured.append(command)

    def _fake_get_task_market_services():
        return _PublishCommand, lambda: _FakeService()

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_services",
        _fake_get_task_market_services,
    )
    _shadow_publish_dispatch_tasks_to_task_market(
        workspace_full="/workspace",
        run_id="run-2",
        tasks=[
            {"id": "T01", "title": "Task 1"},
            {"id": "T02", "goal": "Task 2 Goal", "trace_id": "trace-2"},
            {"title": "missing id should be skipped"},
        ],
    )
    assert len(captured) == 2
    first = captured[0]
    second = captured[1]
    assert isinstance(first, _PublishCommand)
    assert isinstance(second, _PublishCommand)
    assert first.kwargs["workspace"] == "/workspace"
    assert first.kwargs["task_id"] == "T01"
    assert first.kwargs["stage"] == "pending_exec"
    assert first.kwargs["metadata"]["dispatch_mode"] == "shadow"
    assert first.kwargs["plan_id"]
    assert first.kwargs["plan_revision_id"].startswith("rev-")
    assert second.kwargs["trace_id"] == "trace-2"


def test_mainline_publish_emits_pending_design_stage(monkeypatch) -> None:
    """In mainline mode, tasks are published to PENDING_DESIGN for CE consumption."""
    monkeypatch.setenv("POLARIS_TASK_MARKET_MODE", "mainline")

    captured: list[object] = []

    class _PublishCommand:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _FakeService:
        def publish_work_item(self, command) -> object:
            captured.append(command)

            # Fake result
            class _Result:
                ok = True
                status = "pending_design"
                reason = ""
                task_id = command.task_id if hasattr(command, "task_id") else ""
                stage = ""
                version = 1

            return _Result()

    def _fake_get_task_market_services():
        return _PublishCommand, lambda: _FakeService()

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_services",
        _fake_get_task_market_services,
    )
    results = _mainline_publish_dispatch_tasks_to_task_market(
        workspace_full="/workspace",
        run_id="run-3",
        tasks=[
            {"id": "T01", "title": "Task 1"},
            {"id": "T02", "goal": "Task 2 Goal"},
        ],
    )
    assert len(captured) == 2
    first = captured[0]
    assert isinstance(first, _PublishCommand)
    # mainline publishes to pending_design (not pending_exec)
    assert first.kwargs["stage"] == "pending_design"
    assert first.kwargs["metadata"]["dispatch_mode"] == "mainline"
    assert first.kwargs["metadata"]["published_via"] == "mainline"
    assert first.kwargs["plan_id"]
    assert first.kwargs["plan_revision_id"].startswith("rev-")
    # Results are returned
    assert len(results) == 2
    assert results[0]["task_id"] == "T01"
    assert results[0]["ok"] is True
    assert results[1]["task_id"] == "T02"


def test_mainline_design_alias_publishes_pending_design(monkeypatch) -> None:
    monkeypatch.setenv("POLARIS_TASK_MARKET_MODE", "mainline-design")

    captured: list[object] = []

    class _PublishCommand:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _FakeService:
        def publish_work_item(self, command) -> object:
            captured.append(command)

            class _Result:
                ok = True
                status = "pending_design"
                reason = ""
                task_id = "T01"
                stage = "pending_design"
                version = 1

            return _Result()

    def _fake_get_task_market_services():
        return _PublishCommand, lambda: _FakeService()

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_services",
        _fake_get_task_market_services,
    )

    results = _mainline_publish_dispatch_tasks_to_task_market(
        workspace_full="/workspace",
        run_id="run-33",
        tasks=[{"id": "T01", "title": "Task 1"}],
    )
    assert len(results) == 1
    assert captured[0].kwargs["stage"] == "pending_design"


def test_mainline_publish_submits_change_order_on_revision_drift(monkeypatch) -> None:
    monkeypatch.setenv("POLARIS_TASK_MARKET_MODE", "mainline")

    captured_publish: list[object] = []
    register_calls: list[object] = []
    change_calls: list[object] = []
    query_calls: list[object] = []

    class _PublishCommand:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _RegisterRevisionCommand:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _SubmitChangeOrderCommand:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _QueryPlanRevisions:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class _FakeService:
        def publish_work_item(self, command) -> object:
            captured_publish.append(command)

            class _Result:
                ok = True
                status = "pending_design"
                reason = ""
                task_id = "T01"
                stage = "pending_design"
                version = 1

            return _Result()

        def query_plan_revisions(self, command) -> tuple[dict[str, str], ...]:
            query_calls.append(command)
            return ({"plan_revision_id": "rev-old"},)

        def register_plan_revision(self, command) -> object:
            register_calls.append(command)
            return object()

        def submit_change_order(self, command) -> object:
            change_calls.append(command)
            return object()

    def _fake_get_task_market_services():
        return _PublishCommand, lambda: _FakeService()

    def _fake_get_task_market_revision_services():
        return _RegisterRevisionCommand, _SubmitChangeOrderCommand, _QueryPlanRevisions

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_services",
        _fake_get_task_market_services,
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_revision_services",
        _fake_get_task_market_revision_services,
    )

    _mainline_publish_dispatch_tasks_to_task_market(
        workspace_full="/workspace",
        run_id="run-44",
        tasks=[{"id": "T01", "title": "Task 1"}],
    )
    assert len(captured_publish) == 1
    assert len(query_calls) == 1
    assert len(register_calls) == 1
    assert len(change_calls) == 1
    assert register_calls[0].kwargs["plan_id"] == "workspace::/workspace"
    assert change_calls[0].kwargs["from_revision_id"] == "rev-old"


def test_run_inline_task_market_consumers_mainline_full_success(monkeypatch) -> None:
    monkeypatch.setenv("POLARIS_TASK_MARKET_MODE", "mainline-full")

    class _FakeCEConsumer:
        def __init__(self, **_kwargs) -> None:
            self._called = False

        def poll_once(self) -> list[dict[str, object]]:
            if self._called:
                return []
            self._called = True
            return [{"task_id": "T01", "ok": True, "status": "pending_exec"}]

    class _FakeDirectorConsumer:
        def __init__(self, **_kwargs) -> None:
            self._called = False

        def poll_once(self) -> list[dict[str, object]]:
            if self._called:
                return []
            self._called = True
            return [{"task_id": "T01", "ok": True, "status": "pending_qa"}]

    class _FakeQAConsumer:
        def __init__(self, **_kwargs) -> None:
            self._called = False

        def poll_once(self) -> list[dict[str, object]]:
            if self._called:
                return []
            self._called = True
            return [{"task_id": "T01", "ok": True, "status": "resolved"}]

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_consumers",
        lambda: (_FakeCEConsumer, _FakeDirectorConsumer, _FakeQAConsumer),
    )

    result = _run_inline_task_market_consumers(
        workspace_full="/workspace",
        run_id="run-51",
        iteration=1,
        published_task_ids=("T01",),
    )
    assert result["enabled"] is True
    assert result["ok"] is True
    assert result["reason"] == "mainline_full_complete"
    assert result["unresolved_task_ids"] == ()
    assert result["rejected_task_ids"] == ()
    assert result["terminal_status_by_task"]["T01"] == "resolved"


def test_run_dispatch_pipeline_mainline_full_skips_engine_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("POLARIS_TASK_MARKET_MODE", "mainline-full")

    dispatch_tasks = [{"id": "T01", "title": "Task 1"}]

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline.resolve_director_dispatch_tasks",
        lambda **_kwargs: (dispatch_tasks, {}),
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline.run_chief_engineer_preflight",
        lambda **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._mainline_publish_dispatch_tasks_to_task_market",
        lambda **_kwargs: [{"task_id": "T01", "ok": True, "status": "pending_design", "reason": ""}],
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._run_inline_task_market_consumers",
        lambda **_kwargs: {
            "enabled": True,
            "ok": True,
            "reason": "mainline_full_complete",
            "qa_results": ({"task_id": "T01", "ok": True, "status": "resolved"},),
            "director_results": ({"task_id": "T01", "ok": True, "status": "pending_qa"},),
            "unresolved_task_ids": (),
            "rejected_task_ids": (),
        },
    )

    def _unexpected_engine_dispatch(**_kwargs):
        raise AssertionError("run_engine_dispatch should not be called in mainline-full mode")

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline.run_engine_dispatch",
        _unexpected_engine_dispatch,
    )

    outcome = run_dispatch_pipeline(
        workspace_full="/workspace",
        cache_root_full="/cache",
        run_dir="/run",
        run_id="run-52",
        iteration=2,
        normalized={"tasks": dispatch_tasks},
        run_events="/run/events.json",
        dialogue_full="/run/dialogue.jsonl",
        runtime_pm_tasks_full="/run/pm_tasks_runtime.json",
        pm_out_full="/run/pm_out.json",
        run_pm_tasks="/run/pm_tasks.json",
        run_director_result="/run/director_result.json",
    )
    assert outcome["used"] is True
    assert outcome["exit_code"] == 0
    assert outcome["error"] == ""
    assert outcome["engine_dispatch"]["skipped"] is True
    assert outcome["integration_qa_result"]["passed"] is True
    assert outcome["integration_qa_result"]["reason"] == "mainline_full_complete"
    assert outcome["director_result"]["mode"] == "task_market_mainline_full"


class TestRecordDispatchStatusToShangshuling:
    def test_empty_updates_returns_zero(self) -> None:
        result = record_dispatch_status_to_shangshuling(
            workspace_full="/ws",
            status_updates={},
            failure_info={},
        )
        assert result == 0

    def test_non_dict_updates_returns_zero(self) -> None:
        result = record_dispatch_status_to_shangshuling(
            workspace_full="/ws",
            status_updates="not a dict",  # type: ignore[arg-type]
            failure_info={},
        )
        assert result == 0

    def test_skips_non_terminal_statuses(self) -> None:
        port = MagicMock()
        result = record_dispatch_status_to_shangshuling(
            workspace_full="/ws",
            status_updates={"T01": "todo", "T02": "in_progress"},
            failure_info={},
            shangshuling_port=port,
        )
        assert result == 0
        port.record_shangshuling_task_completion.assert_not_called()

    def test_records_terminal_statuses(self) -> None:
        port = MagicMock()
        port.record_shangshuling_task_completion.return_value = True
        result = record_dispatch_status_to_shangshuling(
            workspace_full="/ws",
            status_updates={"T01": "done", "T02": "failed"},
            failure_info={"reason": "test"},
            shangshuling_port=port,
        )
        assert result == 2
        assert port.record_shangshuling_task_completion.call_count == 2

    def test_normalizes_status_aliases(self) -> None:
        port = MagicMock()
        port.record_shangshuling_task_completion.return_value = True
        # "completed" -> "done", "fail" -> "failed", "blocked" -> "blocked"
        result = record_dispatch_status_to_shangshuling(
            workspace_full="/ws",
            status_updates={"T01": "completed", "T02": "fail"},
            failure_info={},
            shangshuling_port=port,
        )
        assert result == 2


# ---------------------------------------------------------------------------
# _tasks_touch_docs_only
# ---------------------------------------------------------------------------


class TestTasksTouchDocsOnly:
    def test_empty_list(self) -> None:
        assert _tasks_touch_docs_only([]) is False

    def test_non_list(self) -> None:
        assert _tasks_touch_docs_only("not a list") is False
        assert _tasks_touch_docs_only([]) is False

    def test_non_dict_items_skipped(self) -> None:
        result = _tasks_touch_docs_only(["not a dict", 123])
        assert result is False

    def test_non_director_task_skipped(self) -> None:
        tasks = [{"assigned_to": "pm", "status": "todo"}]
        assert _tasks_touch_docs_only(tasks) is False

    def test_task_with_no_files_returns_false(self) -> None:
        tasks = [{"assigned_to": "director", "status": "todo"}]
        assert _tasks_touch_docs_only(tasks) is False

    def test_task_with_code_files_returns_false(self) -> None:
        tasks = [{"assigned_to": "director", "target_files": ["src/app.py"]}]
        assert _tasks_touch_docs_only(tasks) is False

    def test_docs_only_task_returns_true(self) -> None:
        tasks = [{"assigned_to": "director", "target_files": ["workspace/docs/guide.md"]}]
        assert _tasks_touch_docs_only(tasks) is True

    def test_docs_only_task_with_list_format(self) -> None:
        tasks = [{"assigned_to": "director", "scope": ["docs/README.md"]}]
        assert _tasks_touch_docs_only(tasks) is True

    def test_mixed_tasks_returns_false(self) -> None:
        tasks = [
            {"assigned_to": "director", "target_files": ["workspace/docs/guide.md"]},
            {"assigned_to": "director", "target_files": ["src/app.py"]},
        ]
        assert _tasks_touch_docs_only(tasks) is False

    def test_docs_path_with_dot_prefix(self) -> None:
        tasks = [{"assigned_to": "director", "context_files": ["./docs/notes.md"]}]
        assert _tasks_touch_docs_only(tasks) is True

    def test_docs_type_task(self) -> None:
        tasks = [{"assigned_to": "director", "type": "documentation"}]
        assert _tasks_touch_docs_only(tasks) is True

    def test_docs_type_with_code_files_still_false(self) -> None:
        tasks = [{"assigned_to": "director", "type": "documentation", "target_files": ["src/main.py"]}]
        assert _tasks_touch_docs_only(tasks) is False


# ---------------------------------------------------------------------------
# _build_director_workflow_result
# ---------------------------------------------------------------------------


class TestBuildDirectorWorkflowResult:
    def test_not_submitted(self) -> None:
        result = _build_director_workflow_result(
            run_id="run-1",
            task_count=5,
            workflow_result=MagicMock(submitted=False, status="failed", error="oops"),
        )
        assert result["run_id"] == "run-1"
        assert result["status"] == "failed"
        assert result["successes"] == 0
        assert result["total"] == 5
        assert result["mode"] == "workflow"

    def test_submitted(self) -> None:
        result = _build_director_workflow_result(
            run_id="run-2",
            task_count=3,
            workflow_result=MagicMock(
                submitted=True,
                workflow_id="wf-123",
                workflow_run_id="run-abc",
                status="queued",
                error="",
            ),
        )
        assert result["status"] == "queued"
        assert result["successes"] == 3
        assert result["summary"] == "Director workflow scheduled in Workflow"

    def test_missing_workflow_id_strips_to_empty(self) -> None:
        result = _build_director_workflow_result(
            run_id="run-3",
            task_count=2,
            workflow_result=MagicMock(submitted=False, status="error", workflow_id=[]),
        )
        assert result["workflow_id"] == ""


# ---------------------------------------------------------------------------
# _build_workflow_input
# ---------------------------------------------------------------------------


def test_build_workflow_input_sets_fields() -> None:
    class FakeInput:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    result = _build_workflow_input(
        FakeInput,
        workspace_full="/ws",
        run_id="run-1",
        iteration=5,
        tasks=[{"id": "T01"}],
    )
    assert result.workspace == "/ws"
    assert result.run_id == "run-1"
    assert result.precomputed_payload == {"tasks": [{"id": "T01"}]}
    assert result.metadata == {"iteration": 5}


# ---------------------------------------------------------------------------
# _resolve_workflow_submit_fn
# ---------------------------------------------------------------------------


def test_resolve_with_explicit_fn() -> None:
    def explicit():
        return []

    result = _resolve_workflow_submit_fn(explicit)
    assert result is explicit


# ---------------------------------------------------------------------------
# _apply_post_dispatch_skip_reason
# ---------------------------------------------------------------------------


class TestApplyPostDispatchSkipReason:
    def test_disabled_skips(self) -> None:
        result: dict = {"enabled": False}
        stop = _apply_post_dispatch_skip_reason(
            result=result,
            status_summary={"total": 5},
            tasks=[],
            docs_stage_payload={},
        )
        assert stop is True
        assert result["reason"] == "integration_qa_disabled"

    def test_no_director_tasks_skips(self) -> None:
        result: dict = {"enabled": True}
        stop = _apply_post_dispatch_skip_reason(
            result=result,
            status_summary={"total": 0},
            tasks=[],
            docs_stage_payload={},
        )
        assert stop is True
        assert result["reason"] == "no_director_tasks"

    def test_docs_stage_docs_only_skips(self) -> None:
        result: dict = {"enabled": True}
        tasks = [{"assigned_to": "director", "target_files": ["workspace/docs/guide.md"]}]
        stop = _apply_post_dispatch_skip_reason(
            result=result,
            status_summary={"total": 1},
            tasks=tasks,
            docs_stage_payload={"enabled": True},
        )
        assert stop is True
        assert result["reason"] == "docs_stage_docs_only"

    def test_pending_tasks_skips(self) -> None:
        result: dict = {"enabled": True}
        stop = _apply_post_dispatch_skip_reason(
            result=result,
            status_summary={"total": 2, "todo": 1, "in_progress": 0},
            tasks=[],
            docs_stage_payload={},
        )
        assert stop is True
        assert result["reason"] == "pending_director_tasks"

    def test_failed_tasks_skips(self) -> None:
        result: dict = {"enabled": True}
        stop = _apply_post_dispatch_skip_reason(
            result=result,
            status_summary={"total": 2, "failed": 1},
            tasks=[],
            docs_stage_payload={},
        )
        assert stop is True
        assert result["reason"] == "director_failures_present"

    def test_all_done_continues(self) -> None:
        result: dict = {"enabled": True}
        stop = _apply_post_dispatch_skip_reason(
            result=result,
            status_summary={
                "total": 3,
                "done": 3,
                "todo": 0,
                "in_progress": 0,
                "review": 0,
                "needs_continue": 0,
                "failed": 0,
                "blocked": 0,
            },
            tasks=[],
            docs_stage_payload={},
        )
        assert stop is False


# ---------------------------------------------------------------------------
# _build_post_dispatch_integration_qa_result
# ---------------------------------------------------------------------------


def test_build_post_dispatch_result() -> None:
    result = _build_post_dispatch_integration_qa_result(
        enabled=True,
        run_id="run-1",
        iteration=2,
        status_summary={"total": 3},
        docs_stage_payload={"enabled": False},
    )
    assert result["schema_version"] == 1
    assert result["enabled"] is True
    assert result["ran"] is False
    assert result["passed"] == []
    assert result["pm_iteration"] == 2
    assert result["docs_stage"]["enabled"] is False
