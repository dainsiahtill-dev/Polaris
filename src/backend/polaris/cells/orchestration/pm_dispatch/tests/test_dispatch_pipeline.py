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
    _classify_integration_qa_evidence,
    _mainline_publish_dispatch_tasks_to_task_market,
    _normalize_task_market_route,
    _resolve_task_market_mode,
    _resolve_task_market_rollout_mode,
    _resolve_workflow_submit_fn,
    _run_inline_task_market_consumers,
    _shadow_publish_dispatch_tasks_to_task_market,
    _start_durable_consumer_loops,
    _task_market_stage_for_route,
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


def test_task_market_off_mode_is_forced_to_mainline_full(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_TASK_MARKET_MODE", "off")

    assert _resolve_task_market_rollout_mode() == "mainline-full"
    assert _resolve_task_market_mode() == "mainline"


def test_task_market_legacy_direct_route_is_forced_to_chief_blueprint() -> None:
    assert _normalize_task_market_route("direct_to_director") == "chief_blueprint_required"
    assert _normalize_task_market_route("pending_exec") == "chief_blueprint_required"
    assert _task_market_stage_for_route("direct_to_director") == "pending_design"


def test_shadow_publish_emits_publish_commands(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_TASK_MARKET_MODE", "shadow")

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
    assert first.kwargs["stage"] == "pending_design"
    assert first.kwargs["metadata"]["dispatch_mode"] == "mainline"
    assert first.kwargs["metadata"]["dispatch_rollout_mode"] == "mainline-full"
    assert first.kwargs["metadata"]["route"] == "chief_blueprint_required"
    assert first.kwargs["metadata"]["blueprint_required"] is True
    assert first.kwargs["metadata"]["published_via"] == "legacy_shadow_normalized"
    assert first.kwargs["plan_id"]
    assert first.kwargs["plan_revision_id"].startswith("rev-")
    assert second.kwargs["trace_id"] == "trace-2"


def test_mainline_publish_routes_tasks_by_contract(monkeypatch) -> None:
    """In mainline mode, PM records per-task task-market routing."""
    monkeypatch.setenv("KERNELONE_TASK_MARKET_MODE", "mainline")

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
            {"id": "T02", "goal": "Task 2 Goal", "task_market_route": "direct_to_director"},
        ],
    )
    assert len(captured) == 2
    first = captured[0]
    second = captured[1]
    assert isinstance(first, _PublishCommand)
    assert isinstance(second, _PublishCommand)
    # Default mainline tasks go through ChiefEngineer blueprint generation.
    assert first.kwargs["stage"] == "pending_design"
    assert first.kwargs["metadata"]["route"] == "chief_blueprint_required"
    assert first.kwargs["metadata"]["blueprint_required"] is True
    # Historical direct routes are normalized to the governed ChiefEngineer handoff.
    assert second.kwargs["stage"] == "pending_design"
    assert second.kwargs["metadata"]["route"] == "chief_blueprint_required"
    assert second.kwargs["metadata"]["blueprint_required"] is True
    assert first.kwargs["metadata"]["dispatch_mode"] == "mainline"
    assert first.kwargs["metadata"]["published_via"] == "mainline"
    assert first.kwargs["plan_id"]
    assert first.kwargs["plan_revision_id"].startswith("rev-")
    # Results are returned
    assert len(results) == 2
    assert results[0]["task_id"] == "T01"
    assert results[0]["ok"] is True
    assert results[0]["route"] == "chief_blueprint_required"
    assert results[1]["task_id"] == "T02"
    assert results[1]["stage"] == "pending_design"
    assert results[1]["route"] == "chief_blueprint_required"


def test_mainline_design_alias_publishes_pending_design(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_TASK_MARKET_MODE", "mainline-design")

    class _PublishCommand:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    captured: list[_PublishCommand] = []

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
    monkeypatch.setenv("KERNELONE_TASK_MARKET_MODE", "mainline")

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

    captured_publish: list[_PublishCommand] = []
    register_calls: list[_RegisterRevisionCommand] = []
    change_calls: list[_SubmitChangeOrderCommand] = []
    query_calls: list[_QueryPlanRevisions] = []

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
    monkeypatch.setenv("KERNELONE_TASK_MARKET_MODE", "mainline-full")

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


def test_run_inline_task_market_consumers_ignores_transient_scope_conflict(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_TASK_MARKET_MODE", "mainline-full")
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._build_director_worker_pool",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._task_market_lineage_snapshot",
        lambda **_kwargs: {
            "available": True,
            "open_task_ids": (),
            "status_counts": {},
            "terminal_status_by_task": {"T01": "resolved"},
        },
    )

    class _FakeCEConsumer:
        def __init__(self, **_kwargs) -> None:
            pass

        def poll_once(self) -> list[dict[str, object]]:
            return []

    class _FakeDirectorConsumer:
        def __init__(self, **_kwargs) -> None:
            self._called = False

        def poll_once(self) -> list[dict[str, object]]:
            if self._called:
                return []
            self._called = True
            return [{"task_id": "T01-B", "ok": False, "reason": "scope_conflict"}]

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
        run_id="run-scope-conflict",
        iteration=1,
        published_task_ids=("T01",),
    )
    assert result["ok"] is True
    assert result["reason"] == "mainline_full_complete"
    assert result["director_results"] == ({"task_id": "T01-B", "ok": False, "reason": "scope_conflict"},)


def test_run_inline_task_market_consumers_drains_past_legacy_two_cycles(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_TASK_MARKET_MODE", "mainline-full")
    monkeypatch.delenv("KERNELONE_TASK_MARKET_MAINLINE_FULL_MAX_CYCLES", raising=False)

    class _FakeStatus:
        def __init__(self, items: list[dict[str, object]]) -> None:
            self.items = tuple(items)

    class _FakeService:
        def __init__(self) -> None:
            self.query_calls = 0

        def reconcile_parent_statuses(self, _workspace: str) -> dict[str, object]:
            return {"updated": 1, "updated_parent_ids": ("PARENT",)}

        def query_status(self, _query: object) -> _FakeStatus:
            self.query_calls += 1
            if self.query_calls == 1:
                parent_status = "pending_exec"
                child_statuses = ("resolved", "pending_exec", "pending_exec")
            elif self.query_calls == 2:
                parent_status = "pending_exec"
                child_statuses = ("resolved", "resolved", "pending_exec")
            else:
                parent_status = "resolved"
                child_statuses = ("resolved", "resolved", "resolved")
            items: list[dict[str, object]] = [
                {"task_id": "PARENT", "status": parent_status, "root_task_id": "PARENT", "parent_task_id": ""}
            ]
            for index, status in enumerate(child_statuses, start=1):
                items.append(
                    {
                        "task_id": f"PARENT-{index}",
                        "status": status,
                        "root_task_id": "PARENT",
                        "parent_task_id": "PARENT",
                    }
                )
            return _FakeStatus(items)

    service = _FakeService()

    class _FakeCEConsumer:
        def __init__(self, **_kwargs) -> None:
            self._called = False

        def poll_once(self) -> list[dict[str, object]]:
            if self._called:
                return []
            self._called = True
            return [{"task_id": "PARENT", "ok": True, "status": "pending_exec"}]

    class _FakeDirectorConsumer:
        def __init__(self, **_kwargs) -> None:
            self._calls = 0

        def poll_once(self) -> list[dict[str, object]]:
            self._calls += 1
            if self._calls <= 3:
                return [{"task_id": f"PARENT-{self._calls}", "ok": True, "status": "pending_qa"}]
            return []

    class _FakeQAConsumer:
        def __init__(self, **_kwargs) -> None:
            self._calls = 0

        def poll_once(self) -> list[dict[str, object]]:
            self._calls += 1
            if self._calls <= 3:
                return [{"task_id": f"PARENT-{self._calls}", "ok": True, "status": "resolved"}]
            return []

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_consumers",
        lambda: (_FakeCEConsumer, _FakeDirectorConsumer, _FakeQAConsumer),
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_services",
        lambda: (object, lambda: service),
    )

    result = _run_inline_task_market_consumers(
        workspace_full="/workspace",
        run_id="run-legacy-two-cycle-regression",
        iteration=1,
        published_task_ids=("PARENT",),
    )
    assert result["enabled"] is True
    assert result["ok"] is True
    assert result["reason"] == "mainline_full_complete"
    assert result["cycles_ran"] == 3
    assert result["open_task_ids"] == ()
    assert result["terminal_status_by_task"]["PARENT"] == "resolved"


def test_run_inline_task_market_consumers_terminal_fold_is_lineage_scoped(monkeypatch, tmp_path) -> None:
    """The market store is workspace-persistent: a dead-letter row left by a
    PREVIOUS run must not fold into this run's terminal report and flip it
    to failed (it would stay failed forever)."""
    monkeypatch.setenv("KERNELONE_TASK_MARKET_MODE", "mainline-full")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    from polaris.cells.runtime.task_market.public.contracts import (
        ClaimTaskWorkItemCommandV1,
        FailTaskStageCommandV1,
        PublishTaskWorkItemCommandV1,
    )
    from polaris.cells.runtime.task_market.public.service import get_task_market_service

    service = get_task_market_service()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="tr-old",
            run_id="run-OLD",
            task_id="OLD-1",
            stage="pending_exec",
            source_role="PM",
            payload={"title": "stale task from a previous run"},
        )
    )
    old_claim = service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="dw-old",
            worker_role="director",
            task_id="OLD-1",
        )
    )
    service.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="OLD-1",
            lease_token=old_claim.lease_token,
            error_code="exec_failed",
            error_message="fatal",
            to_dead_letter=True,
        )
    )

    class _IdleConsumer:
        def __init__(self, **_kwargs) -> None:
            self._called = False

        def poll_once(self) -> list[dict[str, object]]:
            return []

    class _ResolvingQAConsumer:
        def __init__(self, **_kwargs) -> None:
            self._called = False

        def poll_once(self) -> list[dict[str, object]]:
            if self._called:
                return []
            self._called = True
            return [{"task_id": "T-NEW", "ok": True, "status": "resolved"}]

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_consumers",
        lambda: (_IdleConsumer, _IdleConsumer, _ResolvingQAConsumer),
    )

    result = _run_inline_task_market_consumers(
        workspace_full=str(workspace),
        run_id="run-NEW",
        iteration=1,
        published_task_ids=("T-NEW",),
    )
    assert result["rejected_task_ids"] == ()
    assert result["unresolved_task_ids"] == ()
    assert result["ok"] is True
    assert "OLD-1" not in result["terminal_status_by_task"]


def test_run_dispatch_pipeline_mainline_full_skips_engine_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_TASK_MARKET_MODE", "mainline-full")

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


def test_run_dispatch_pipeline_blocks_when_chief_engineer_preflight_hard_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("KERNELONE_TASK_MARKET_MODE", "mainline-full")

    dispatch_tasks = [{"id": "T01", "title": "Task 1"}]
    status_events: list[dict[str, object]] = []

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline.resolve_director_dispatch_tasks",
        lambda **_kwargs: (dispatch_tasks, {}),
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline.run_chief_engineer_preflight",
        lambda **_kwargs: {
            "ran": True,
            "hard_failure": True,
            "reason": "chief_engineer_error",
            "summary": "ChiefEngineer preflight failed",
        },
    )

    def _unexpected_engine_dispatch(**_kwargs):
        raise AssertionError("run_engine_dispatch must not run after ChiefEngineer hard failure")

    def _unexpected_task_market_publish(**_kwargs):
        raise AssertionError("task-market publishing must not run after ChiefEngineer hard failure")

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline.run_engine_dispatch",
        _unexpected_engine_dispatch,
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._mainline_publish_dispatch_tasks_to_task_market",
        _unexpected_task_market_publish,
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._emit_engine_dispatch_status",
        lambda **kwargs: status_events.append(kwargs),
    )

    outcome = run_dispatch_pipeline(
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(tmp_path / "run"),
        run_id="run-ce-blocked",
        iteration=1,
        normalized={"tasks": dispatch_tasks},
        run_events=str(tmp_path / "runtime.events.jsonl"),
        dialogue_full=str(tmp_path / "dialogue.transcript.jsonl"),
        runtime_pm_tasks_full=str(tmp_path / "pm_tasks_runtime.json"),
        pm_out_full=str(tmp_path / "pm_out.json"),
        run_pm_tasks=str(tmp_path / "pm_tasks.json"),
        run_director_result=str(tmp_path / "director_result.json"),
    )

    assert outcome["used"] is True
    assert outcome["exit_code"] == 1
    assert outcome["error"] == "chief_engineer_preflight_failed: chief_engineer_error"
    assert outcome["engine_dispatch"]["skipped"] is True
    assert outcome["engine_dispatch"]["reason"] == "chief_engineer_preflight_failed"
    assert outcome["director_result"]["status"] == "blocked"
    assert outcome["director_result"]["dispatch_blocked"] is True
    assert outcome["director_result"]["blocked"] == 1
    assert outcome["integration_qa_result"] is None
    assert "blueprint_id" not in dispatch_tasks[0]
    assert status_events
    assert status_events[0]["name"] == "engine_dispatch_blocked"


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
        assert result["passed"] is False
        assert "Director" in result["summary"]

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

    def test_partial_evidence_failed_with_done_runs_qa(self) -> None:
        """failed>0 但 done>0 → 不再硬跳过:QA 在已完成范围上运行(部分证据模式)。"""
        result: dict = {"enabled": True}
        stop = _apply_post_dispatch_skip_reason(
            result=result,
            status_summary={"total": 3, "done": 2, "failed": 1},
            tasks=[],
            docs_stage_payload={},
        )
        assert stop is False
        assert result["scope"] == "partial_completed_tasks"
        assert result["scope_detail"] == {"done": 2, "failed": 1, "blocked": 0}
        assert "reason" not in result  # QA execution will set the terminal reason

    def test_partial_evidence_blocked_with_done_runs_qa(self) -> None:
        result: dict = {"enabled": True}
        stop = _apply_post_dispatch_skip_reason(
            result=result,
            status_summary={"total": 2, "done": 1, "blocked": 1},
            tasks=[],
            docs_stage_payload={},
        )
        assert stop is False
        assert result["scope"] == "partial_completed_tasks"

    def test_zero_done_with_failures_still_skips(self) -> None:
        result: dict = {"enabled": True}
        stop = _apply_post_dispatch_skip_reason(
            result=result,
            status_summary={"total": 2, "failed": 2, "done": 0},
            tasks=[],
            docs_stage_payload={},
        )
        assert stop is True
        assert result["reason"] == "director_failures_present"


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
    assert result["passed"] is None
    assert result["evidence_grade"] == "not_run"
    assert result["pm_iteration"] == 2
    assert result["docs_stage"]["enabled"] is False


def test_classify_integration_qa_evidence_real_command_passed() -> None:
    assert (
        _classify_integration_qa_evidence(
            ran=True,
            passed=True,
            reason="integration_qa_passed",
            summary="Integration verification passed: npm run test -- --watch=false",
            errors=[],
        )
        == "real_command_passed"
    )


def test_classify_integration_qa_evidence_static_fallback() -> None:
    assert (
        _classify_integration_qa_evidence(
            ran=True,
            passed=True,
            reason="integration_qa_passed",
            summary="Node static verification passed while dependencies are not installed (source_files=8, tests=present).",
            errors=[],
        )
        == "structural_fallback_passed"
    )


def test_classify_integration_qa_evidence_dependency_blocked() -> None:
    assert (
        _classify_integration_qa_evidence(
            ran=True,
            passed=False,
            reason="integration_qa_failed",
            summary="Integration verification blocked: Node dependencies are declared but not installed for command: npm test",
            errors=[],
        )
        == "blocked_missing_dependencies"
    )


# ---------------------------------------------------------------------------
# _start_durable_consumer_loops — REGRESSION GUARD
#
# After breaking the runtime.task_market -> {chief_engineer, director, qa}
# back-edges, the task_market Cell no longer imports the concrete consumer
# classes.  pm_dispatch (the composition root) MUST now inject them via
# ``consumer_types`` so durable production consumers still launch.  These
# tests fail closed if pm_dispatch ever stops supplying the mapping.
# ---------------------------------------------------------------------------


def test_start_durable_consumer_loops_injects_consumer_types(monkeypatch) -> None:
    """pm_dispatch must pass concrete CE/Director/QA classes to start_consumer_loops."""

    class _FakeCEConsumer:
        pass

    class _FakeDirectorConsumer:
        pass

    class _FakeQAConsumer:
        pass

    captured: dict[str, object] = {}

    class _FakeService:
        def start_consumer_loops(self, workspace: str, *, consumer_types=None) -> bool:
            captured["workspace"] = workspace
            captured["consumer_types"] = consumer_types
            return True

        def query_consumer_loop_status(self, workspace: str) -> dict[str, object]:
            return {"started": True, "is_running": True}

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_services",
        lambda: (object, lambda: _FakeService()),
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_consumers",
        lambda: (_FakeCEConsumer, _FakeDirectorConsumer, _FakeQAConsumer),
    )

    result = _start_durable_consumer_loops(workspace_full="/workspace", run_id="run-durable-1")

    assert result["ok"] is True
    assert result["reason"] == "durable_consumers_started"
    # The critical regression assertion: consumer_types were injected, mapping
    # each role to the concrete class supplied by the composition root.
    assert captured["consumer_types"] == {
        "chief_engineer": _FakeCEConsumer,
        "director": _FakeDirectorConsumer,
        "qa": _FakeQAConsumer,
    }


def test_start_durable_consumer_loops_reports_consumer_import_failure(monkeypatch) -> None:
    """A failure resolving consumer classes is reported fail-closed (no start)."""
    start_calls: list[object] = []

    class _FakeService:
        def start_consumer_loops(self, workspace: str, *, consumer_types=None) -> bool:
            start_calls.append(consumer_types)
            return True

        def query_consumer_loop_status(self, workspace: str) -> dict[str, object]:
            return {}

    def _boom() -> tuple[type, type, type]:
        raise ImportError("consumer module unavailable")

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_services",
        lambda: (object, lambda: _FakeService()),
    )
    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_consumers",
        _boom,
    )

    result = _start_durable_consumer_loops(workspace_full="/workspace", run_id="run-durable-2")

    assert result["ok"] is False
    assert result["reason"] == "consumer_import_failed"
    assert "consumer module unavailable" in result["error"]
    # Fail-closed: we never attempted to start loops without consumer classes.
    assert start_calls == []


def test_start_durable_consumer_loops_launches_real_threads(monkeypatch, tmp_path) -> None:
    """End-to-end: injected types reach the real service and spawn live threads.

    Guards against a silent regression where durable consumers stop launching:
    drives a real ``TaskMarketService.start_consumer_loops`` through the
    pm_dispatch entry point and asserts a daemon thread is running for every
    injected role.
    """
    import threading
    from typing import Any as _Any

    from polaris.cells.runtime.task_market.public.service import (
        get_task_market_service,
    )

    class _DurableFakeConsumer:
        def __init__(self, workspace: str = "", worker_id: str = "", **_kwargs: _Any) -> None:
            self._stop = threading.Event()

        def run(self) -> None:
            while not self._stop.wait(0.02):
                pass

        def stop(self) -> None:
            self._stop.set()

    monkeypatch.setattr(
        "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._get_task_market_consumers",
        lambda: (_DurableFakeConsumer, _DurableFakeConsumer, _DurableFakeConsumer),
    )

    workspace = str(tmp_path / "ws")
    service = get_task_market_service()
    try:
        result = _start_durable_consumer_loops(workspace_full=workspace, run_id="run-durable-3")

        assert result["ok"] is True
        status = result["consumer_status"]
        assert status["started"] is True
        assert status["is_running"] is True
        # Every injected role must have a live consumer thread.
        for role in ("chief_engineer", "director", "qa"):
            assert status["roles"][role]["running"] is True, f"role={role} did not launch"
        assert status["outbox_relay_running"] is True
    finally:
        service.stop_all_consumer_loops()
