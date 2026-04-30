"""Tests for polaris.cells.chief_engineer.blueprint.internal.chief_engineer_preflight.

Covers PreflightContext, pure helper functions, result builders,
payload construction, auto-decision, and blueprint slicing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from polaris.cells.chief_engineer.blueprint.internal.chief_engineer_preflight import (
    PreflightContext,
    _build_failure_result,
    _collect_task_scope_modules,
    _emit_preflight_events,
    _execute_preflight,
    _module_key_from_path,
    _normalize_file_plan_entry,
    _normalize_path_list,
    _normalize_success_result,
    _safe_payload_digest,
    _slice_blueprint_for_task,
    _tail_non_empty_lines,
    _trim_str_list,
    build_task_focused_chief_engineer_payload,
    chief_engineer_auto_decision,
    inject_chief_engineer_constraints,
    run_pre_dispatch_chief_engineer_ctx,
)


class TestNormalizePathList:
    """Tests for _normalize_path_list."""

    def test_empty_returns_empty(self) -> None:
        assert _normalize_path_list([]) == []
        assert _normalize_path_list("") == []
        assert _normalize_path_list(None) == []

    def test_string_split(self) -> None:
        assert _normalize_path_list("a.py, b.py") == ["a.py", "b.py"]

    def test_list_of_strings(self) -> None:
        assert _normalize_path_list(["a.py", "b.py"]) == ["a.py", "b.py"]

    def test_strips_dot_slash_and_backslash(self) -> None:
        assert _normalize_path_list(["./a.py", "b\\c.py"]) == ["a.py", "b/c.py"]


class TestNormalizeFilePlanEntry:
    """Tests for _normalize_file_plan_entry."""

    def test_dict_entry(self) -> None:
        result = _normalize_file_plan_entry({"path": "foo.py", "action": "CREATE"})
        assert result == {"path": "foo.py", "action": "create", "content": None}

    def test_string_entry_modify(self) -> None:
        result = _normalize_file_plan_entry("foo.py")
        assert result == {"path": "foo.py", "action": "modify"}

    def test_string_entry_create(self) -> None:
        result = _normalize_file_plan_entry("+foo.py")
        assert result == {"path": "foo.py", "action": "create"}

    def test_string_entry_delete(self) -> None:
        result = _normalize_file_plan_entry("-foo.py")
        assert result == {"path": "foo.py", "action": "delete"}

    def test_empty_string_returns_none(self) -> None:
        assert _normalize_file_plan_entry("") is None

    def test_none_returns_none(self) -> None:
        assert _normalize_file_plan_entry(None) is None


class TestTrimStrList:
    """Tests for _trim_str_list."""

    def test_empty_returns_empty(self) -> None:
        assert _trim_str_list([]) == []
        assert _trim_str_list("") == []
        assert _trim_str_list(None) == []

    def test_string_split(self) -> None:
        assert _trim_str_list("a, b, c") == ["a", "b", "c"]

    def test_list_trimmed(self) -> None:
        assert _trim_str_list(["a", "b", "c"], limit=2) == ["a", "b"]


class TestModuleKeyFromPath:
    """Tests for _module_key_from_path."""

    def test_empty_returns_root(self) -> None:
        assert _module_key_from_path("") == "root"

    def test_single_part(self) -> None:
        assert _module_key_from_path("foo") == "foo"

    def test_src_prefix(self) -> None:
        assert _module_key_from_path("src/backend/app.py") == "src/backend"

    def test_app_prefix_returns_first_two_parts(self) -> None:
        # app is in the prefix list, so it returns first 2 parts
        assert _module_key_from_path("app/models.py") == "app/models.py"

    def test_backslash_normalized(self) -> None:
        assert _module_key_from_path("src\\backend\\app.py") == "src/backend"


class TestTailNonEmptyLines:
    """Tests for _tail_non_empty_lines."""

    def test_empty_returns_empty(self) -> None:
        assert _tail_non_empty_lines("") == []

    def test_returns_all_when_under_limit(self) -> None:
        assert _tail_non_empty_lines("a\nb\nc") == ["a", "b", "c"]

    def test_tails_when_over_limit(self) -> None:
        lines = "\n".join(str(i) for i in range(12))
        result = _tail_non_empty_lines(lines, limit=5)
        assert result == ["7", "8", "9", "10", "11"]

    def test_skips_empty_lines(self) -> None:
        assert _tail_non_empty_lines("a\n\n\nb") == ["a", "b"]


class TestCollectTaskScopeModules:
    """Tests for _collect_task_scope_modules.

    Note: This function returns raw normalized paths, not module keys.
    It calls _module_key_from_path for deduplication but returns paths.
    """

    def test_empty_returns_empty(self) -> None:
        assert _collect_task_scope_modules({}, {}) == []

    def test_collects_from_task_and_update(self) -> None:
        task = {"target_files": ["src/app.py"], "scope_paths": ["src/models.py"]}
        update = {"scope_for_apply": ["src/utils.py"], "missing_targets": ["src/tests.py"]}
        result = _collect_task_scope_modules(task, update)
        assert "src/app.py" in result
        assert "src/models.py" in result
        assert "src/utils.py" in result
        assert "src/tests.py" in result

    def test_deduplicates_by_module_key(self) -> None:
        # Two files in same module should be deduplicated
        task = {"target_files": ["src/app/main.py"]}
        update = {"scope_for_apply": ["src/app/helper.py"]}
        result = _collect_task_scope_modules(task, update)
        # Both map to src/app module key (3+ part paths truncate to first 2 dirs)
        assert len(result) == 1

    def test_limits_to_12(self) -> None:
        task = {"target_files": [f"src/f{i}.py" for i in range(20)]}
        result = _collect_task_scope_modules(task, {})
        assert len(result) == 12


class TestSafePayloadDigest:
    """Tests for _safe_payload_digest."""

    def test_dict_returns_hex(self) -> None:
        result = _safe_payload_digest({"a": 1})
        assert isinstance(result, str)
        assert len(result) == 16

    def test_consistent_for_same_input(self) -> None:
        assert _safe_payload_digest({"a": 1}) == _safe_payload_digest({"a": 1})

    def test_none_returns_digest(self) -> None:
        # json.dumps(None) works, so it should return a digest
        result = _safe_payload_digest(None)
        assert isinstance(result, str)


class TestSliceBlueprintForTask:
    """Tests for _slice_blueprint_for_task."""

    def test_no_blueprint_returns_empty(self) -> None:
        result = _slice_blueprint_for_task(task={}, task_update={}, blueprint_data=None)
        assert result == {}

    def test_non_dict_blueprint_returns_empty(self) -> None:
        result = _slice_blueprint_for_task(task={}, task_update={}, blueprint_data="bad")
        assert result == {}

    def test_slices_module_order(self) -> None:
        blueprint = {
            "module_order": ["src/app.py", "src/models", "src/tests"],
            "module_architecture": {
                "src/app.py": {"layer": "ui"},
                "src/models": {"layer": "domain"},
            },
        }
        task = {"target_files": ["src/app.py"]}
        update = {}
        result = _slice_blueprint_for_task(task=task, task_update=update, blueprint_data=blueprint)
        # src/app.py module key is src/app.py (2-part paths preserve filename)
        assert "src/app.py" in result["module_order"]
        assert "src/app.py" in result["module_architecture"]

    def test_api_contracts_filtered(self) -> None:
        blueprint = {
            "module_order": [],
            "api_contracts": [
                {"provider": "src/app.py", "consumer": "src/models", "name": "c1"},
                {"provider": "src/other", "consumer": "src/else", "name": "c2"},
            ],
        }
        task = {"target_files": ["src/app.py"]}
        result = _slice_blueprint_for_task(task=task, task_update={}, blueprint_data=blueprint)
        assert len(result["api_contracts"]) == 1
        assert result["api_contracts"][0]["name"] == "c1"

    def test_limits_api_contracts(self) -> None:
        blueprint = {
            "module_order": [],
            "api_contracts": [{"provider": "src/app.py", "consumer": "src/models", "name": f"c{i}"} for i in range(12)],
        }
        task = {"target_files": ["src/app.py"]}
        result = _slice_blueprint_for_task(task=task, task_update={}, blueprint_data=blueprint)
        assert len(result["api_contracts"]) == 8


class TestBuildFailureResult:
    """Tests for _build_failure_result."""

    def test_structure(self) -> None:
        result = _build_failure_result(
            run_blueprint_path="/bp",
            runtime_blueprint_path="/rt",
            reason="fail",
            summary="bad",
            tasks=[],
        )
        assert result["hard_failure"] is True
        assert result["reason"] == "fail"
        assert result["task_update_count"] == 0


class TestNormalizeSuccessResult:
    """Tests for _normalize_success_result."""

    def test_sets_defaults(self) -> None:
        result = _normalize_success_result(
            {},
            run_blueprint_path="/bp",
            runtime_blueprint_path="/rt",
        )
        assert result["schema_version"] == 1
        assert result["role"] == "ChiefEngineer"
        assert result["hard_failure"] is False
        assert result["blueprint_path"] == "/bp"

    def test_preserves_existing_values(self) -> None:
        result = _normalize_success_result(
            {"reason": "custom"},
            run_blueprint_path="/bp",
            runtime_blueprint_path="/rt",
        )
        assert result["reason"] == "custom"


class TestExecutePreflight:
    """Tests for _execute_preflight."""

    def test_successful_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_runner = MagicMock(return_value={"task_updates": [{"id": "t1"}]})
        ctx = PreflightContext(
            workspace_full="/ws",
            cache_root_full="/cache",
            run_dir="/run",
            run_id="r1",
            pm_iteration=1,
            tasks=[],
            run_events="/events",
            dialogue_full="/dialogue",
            analysis_runner=mock_runner,
        )
        result = _execute_preflight(ctx)
        assert result["hard_failure"] is False
        assert result["task_update_count"] == 1
        mock_runner.assert_called_once()

    def test_runner_exception_becomes_failure(self) -> None:
        mock_runner = MagicMock(side_effect=RuntimeError("boom"))
        ctx = PreflightContext(
            workspace_full="/ws",
            cache_root_full="/cache",
            run_dir="/run",
            run_id="r1",
            pm_iteration=1,
            tasks=[],
            run_events="/events",
            dialogue_full="/dialogue",
            analysis_runner=mock_runner,
        )
        result = _execute_preflight(ctx)
        assert result["hard_failure"] is True
        assert "boom" in result["summary"]

    def test_invalid_result_type_becomes_failure(self) -> None:
        mock_runner = MagicMock(return_value="not a dict")
        ctx = PreflightContext(
            workspace_full="/ws",
            cache_root_full="/cache",
            run_dir="/run",
            run_id="r1",
            pm_iteration=1,
            tasks=[],
            run_events="/events",
            dialogue_full="/dialogue",
            analysis_runner=mock_runner,
        )
        result = _execute_preflight(ctx)
        assert result["hard_failure"] is True
        assert "invalid result" in result["summary"]


class TestEmitPreflightEvents:
    """Tests for _emit_preflight_events."""

    def test_emits_success(self) -> None:
        emitter = MagicMock()
        ctx = PreflightContext(
            workspace_full="/ws",
            cache_root_full="/cache",
            run_dir="/run",
            run_id="r1",
            pm_iteration=1,
            tasks=[{"id": "t1"}],
            run_events="/events",
            dialogue_full="/dialogue",
            event_emitter=emitter,
        )
        result = {
            "hard_failure": False,
            "summary": "ok",
            "blueprint_path": "/bp",
            "runtime_blueprint_path": "/rt",
        }
        _emit_preflight_events(ctx, result)
        emitter.emit_event.assert_called_once()
        emitter.emit_dialogue.assert_called_once()
        call_kwargs = emitter.emit_event.call_args.kwargs
        assert call_kwargs["ok"] is True

    def test_emits_failure(self) -> None:
        emitter = MagicMock()
        ctx = PreflightContext(
            workspace_full="/ws",
            cache_root_full="/cache",
            run_dir="/run",
            run_id="r1",
            pm_iteration=1,
            tasks=[],
            run_events="/events",
            dialogue_full="/dialogue",
            event_emitter=emitter,
        )
        result = {
            "hard_failure": True,
            "summary": "bad",
            "blueprint_path": "/bp",
            "runtime_blueprint_path": "/rt",
        }
        _emit_preflight_events(ctx, result)
        call_kwargs = emitter.emit_event.call_args.kwargs
        assert call_kwargs["ok"] is False
        assert call_kwargs["error"] == "bad"


class TestPreflightContext:
    """Tests for PreflightContext."""

    def test_effective_emitter_injected(self) -> None:
        emitter = MagicMock()
        ctx = PreflightContext(
            workspace_full="/ws",
            cache_root_full="/cache",
            run_dir="/run",
            run_id="r1",
            pm_iteration=1,
            tasks=[],
            run_events="/events",
            dialogue_full="/dialogue",
            event_emitter=emitter,
        )
        assert ctx.effective_emitter() is emitter

    def test_effective_runner_injected(self) -> None:
        runner = MagicMock()
        ctx = PreflightContext(
            workspace_full="/ws",
            cache_root_full="/cache",
            run_dir="/run",
            run_id="r1",
            pm_iteration=1,
            tasks=[],
            run_events="/events",
            dialogue_full="/dialogue",
            analysis_runner=runner,
        )
        assert ctx.effective_runner() is runner

    def test_effective_runner_missing_raises(self) -> None:
        ctx = PreflightContext(
            workspace_full="/ws",
            cache_root_full="/cache",
            run_dir="/run",
            run_id="r1",
            pm_iteration=1,
            tasks=[],
            run_events="/events",
            dialogue_full="/dialogue",
        )
        with pytest.raises(RuntimeError, match="analysis_runner is required"):
            ctx.effective_runner()


class TestRunPreDispatchChiefEngineerCtx:
    """Tests for run_pre_dispatch_chief_engineer_ctx."""

    def test_full_flow(self) -> None:
        emitter = MagicMock()
        runner = MagicMock(return_value={"task_updates": []})
        ctx = PreflightContext(
            workspace_full="/ws",
            cache_root_full="/cache",
            run_dir="/run",
            run_id="r1",
            pm_iteration=1,
            tasks=[],
            run_events="/events",
            dialogue_full="/dialogue",
            analysis_runner=runner,
            event_emitter=emitter,
        )
        result = run_pre_dispatch_chief_engineer_ctx(ctx)
        assert result["hard_failure"] is False
        emitter.emit_event.assert_called_once()


class TestBuildTaskFocusedChiefEngineerPayload:
    """Tests for build_task_focused_chief_engineer_payload."""

    def test_basic_payload(self) -> None:
        task = {"id": "t1", "title": "Fix bug", "goal": "make it work"}
        update = {"task_id": "t1", "scope_for_apply": ["a.py"], "missing_targets": ["b.py"]}
        result = build_task_focused_chief_engineer_payload(task=task, task_update=update, blueprint_data=None)
        assert result["task_id"] == "t1"
        assert result["scope_for_apply"] == ["a.py"]
        assert result["missing_targets"] == ["b.py"]
        assert result["task_title"] == "Fix bug"
        assert result["task_goal"] == "make it work"

    def test_construction_plan_compact(self) -> None:
        task = {"id": "t1"}
        update = {
            "task_id": "t1",
            "construction_plan": {
                "file_plans": [
                    {"path": "a.py", "action": "create"},
                    "+b.py",
                ],
                "method_catalog": ["m1", "m2"],
                "verification_steps": ["v1"],
            },
        }
        result = build_task_focused_chief_engineer_payload(task=task, task_update=update, blueprint_data=None)
        assert "construction_plan" in result
        cp = result["construction_plan"]
        assert len(cp["file_plans"]) == 2
        assert cp["method_catalog"] == ["m1", "m2"]
        assert cp["verification_steps"] == ["v1"]

    def test_empty_construction_plan_omitted(self) -> None:
        task = {"id": "t1"}
        update = {"task_id": "t1", "construction_plan": {}}
        result = build_task_focused_chief_engineer_payload(task=task, task_update=update, blueprint_data=None)
        assert "construction_plan" not in result


class TestInjectChiefEngineerConstraints:
    """Tests for inject_chief_engineer_constraints."""

    def test_injects_task_ids(self) -> None:
        payload = {}
        tasks = [{"id": "t1"}, {"id": "t2"}]
        result = inject_chief_engineer_constraints(payload, tasks=tasks, workspace_full="/ws")
        assert result["constraints"]["task_ids"] == ["t1", "t2"]

    def test_injects_scope_paths(self) -> None:
        payload = {}
        tasks = [{"scope_paths": ["src/a.py", "src/b.py"]}]
        result = inject_chief_engineer_constraints(payload, tasks=tasks, workspace_full="/ws")
        assert "src/a.py" in result["constraints"]["all_scope_paths"]

    def test_injects_affected_modules(self) -> None:
        payload = {}
        tasks = [{"scope_paths": ["src/app/models.py"]}]
        result = inject_chief_engineer_constraints(payload, tasks=tasks, workspace_full="/ws")
        assert "src/app" in result["constraints"]["affected_modules"]


class TestChiefEngineerAutoDecision:
    """Tests for chief_engineer_auto_decision."""

    def test_no_tasks_blocks(self) -> None:
        result = chief_engineer_auto_decision([])
        assert result["proceed"] is False
        assert result["reason"] == "no_tasks"

    def test_blocked_tasks_block(self) -> None:
        result = chief_engineer_auto_decision([{"status": "blocked"}])
        assert result["proceed"] is False
        assert result["needs_review"] is True

    def test_failed_tasks_block(self) -> None:
        result = chief_engineer_auto_decision([{"status": "failed"}])
        assert result["proceed"] is False

    def test_needs_review_blocks(self) -> None:
        result = chief_engineer_auto_decision([{"status": "ok", "needs_review": True}])
        assert result["proceed"] is False
        assert result["reason"] == "1 tasks need review"

    def test_many_tasks_no_review_approves(self) -> None:
        tasks = [{"status": "ok"} for _ in range(12)]
        result = chief_engineer_auto_decision(tasks)
        assert result["proceed"] is True
        assert result["reason"] == "all tasks ready"

    def test_few_tasks_auto_approves(self) -> None:
        tasks = [{"status": "ok"} for _ in range(3)]
        result = chief_engineer_auto_decision(tasks)
        assert result["proceed"] is True
        assert result["reason"] == "auto_approved"
