"""Tests for polaris.infrastructure.log_pipeline.run_context."""

from __future__ import annotations

import os

from polaris.infrastructure.log_pipeline.run_context import (
    ActiveRunContext,
    RunContextManager,
    clear_active_run_context,
    get_active_run_context,
    get_global_run_context,
    resolve_current_run_id,
    resolve_current_workspace,
    set_active_run_context,
    set_global_run_context,
)


class TestActiveRunContext:
    def test_basic_init(self) -> None:
        ctx = ActiveRunContext(
            run_id="run-1",
            workspace=".",
            runtime_root="",
        )
        assert ctx.run_id == "run-1"

    def test_workspace_becomes_absolute(self) -> None:
        ctx = ActiveRunContext(
            run_id="run-1",
            workspace=".",
            runtime_root="",
        )
        assert os.path.isabs(ctx.workspace)

    def test_runtime_root_resolved(self) -> None:
        ctx = ActiveRunContext(
            run_id="run-1",
            workspace=".",
            runtime_root="",
        )
        assert os.path.isabs(ctx.runtime_root)

    def test_run_dir_constructed(self) -> None:
        ctx = ActiveRunContext(
            run_id="run-42",
            workspace=".",
            runtime_root="C:/runtime",
        )
        assert ctx.run_dir.replace("\\", "/") == "C:/runtime/runs/run-42"

    def test_logs_dir_constructed(self) -> None:
        ctx = ActiveRunContext(
            run_id="run-42",
            workspace=".",
            runtime_root="C:/runtime",
        )
        assert ctx.logs_dir.replace("\\", "/") == "C:/runtime/runs/run-42/logs"

    def test_with_metadata(self) -> None:
        ctx = ActiveRunContext(
            run_id="run-1",
            workspace=".",
            runtime_root="",
            pm_iteration=3,
            director_iteration=2,
            task_id="task-abc",
        )
        assert ctx.pm_iteration == 3
        assert ctx.director_iteration == 2
        assert ctx.task_id == "task-abc"


class TestThreadLocalContextFunctions:
    def test_clear_and_get_none(self) -> None:
        clear_active_run_context()
        assert get_active_run_context() is None

    def test_set_and_get_context(self) -> None:
        clear_active_run_context()
        ctx = ActiveRunContext(
            run_id="run-test",
            workspace=".",
            runtime_root="",
        )
        set_active_run_context(ctx)
        retrieved = get_active_run_context()
        assert retrieved is not None
        assert retrieved.run_id == "run-test"

    def test_clear_restores_none(self) -> None:
        clear_active_run_context()
        ctx = ActiveRunContext(run_id="run-x", workspace=".", runtime_root="")
        set_active_run_context(ctx)
        clear_active_run_context()
        assert get_active_run_context() is None


class TestGlobalContextFunctions:
    def test_set_and_get_global_context(self) -> None:
        ctx = ActiveRunContext(
            run_id="global-run",
            workspace=".",
            runtime_root="",
        )
        set_global_run_context(ctx)
        retrieved = get_global_run_context()
        assert retrieved is not None
        assert retrieved.run_id == "global-run"

    def test_global_is_none_initially(self) -> None:
        # Clear first to ensure clean state
        set_global_run_context(None)
        assert get_global_run_context() is None


class TestRunContextManager:
    def test_enter_sets_context(self) -> None:
        clear_active_run_context()
        with RunContextManager(workspace=".", run_id="managed-run") as ctx:
            assert ctx.run_id == "managed-run"
            assert get_active_run_context() is not None

    def test_exit_restores_old_context(self) -> None:
        clear_active_run_context()
        old_ctx = ActiveRunContext(run_id="old-run", workspace=".", runtime_root="")
        set_active_run_context(old_ctx)

        with RunContextManager(workspace=".", run_id="new-run"):
            assert get_active_run_context().run_id == "new-run"

        # After exiting, should restore old context
        assert get_active_run_context().run_id == "old-run"

    def test_with_metadata_passed_through(self) -> None:
        clear_active_run_context()
        with RunContextManager(
            workspace=".",
            run_id="meta-run",
            pm_iteration=5,
            director_iteration=3,
            task_id="task-xyz",
        ) as ctx:
            assert ctx.pm_iteration == 5
            assert ctx.director_iteration == 3
            assert ctx.task_id == "task-xyz"

    def test_uses_global_context_when_disabled(self) -> None:
        set_global_run_context(None)
        set_active_run_context(None)
        ctx = ActiveRunContext(run_id="global", workspace=".", runtime_root="")
        set_global_run_context(ctx)

        with RunContextManager(
            workspace=".",
            run_id="local",
            use_thread_local=False,
        ):
            # Should use global context, not thread-local
            global_ctx = get_global_run_context()
            assert global_ctx.run_id == "local"


class TestResolveFunctions:
    def test_resolve_current_run_id_from_thread_local(self) -> None:
        clear_active_run_context()
        ctx = ActiveRunContext(run_id="thread-local-run", workspace=".", runtime_root="")
        set_active_run_context(ctx)

        result = resolve_current_run_id()
        assert result == "thread-local-run"

    def test_resolve_current_workspace_from_thread_local(self) -> None:
        clear_active_run_context()
        ctx = ActiveRunContext(run_id="run-1", workspace=".", runtime_root="")
        set_active_run_context(ctx)

        result = resolve_current_workspace()
        # Should return the workspace from context
        assert result is not None

    def test_resolve_current_workspace_fallback_to_cwd(self) -> None:
        clear_active_run_context()
        set_active_run_context(None)

        result = resolve_current_workspace()
        assert result == os.getcwd()

    def test_resolve_run_id_falls_back_to_global(self) -> None:
        clear_active_run_context()
        ctx = ActiveRunContext(run_id="global-fallback", workspace=".", runtime_root="")
        set_active_run_context(None)
        set_global_run_context(ctx)

        result = resolve_current_run_id()
        assert result == "global-fallback"
