"""Tests for workflow_runtime internal runtime_backend_adapter module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.runtime_backend_adapter import (
    RuntimeBackendAdapter,
    WorkflowResult,
    _run_sync,
    describe_workflow_sync,
    get_adapter,
    query_workflow_sync,
    reset_adapter,
    set_adapter_factory,
    submit_pm_workflow_sync,
)


class TestWorkflowResult:
    def test_creation(self) -> None:
        result = WorkflowResult(workflow_id="w1", run_id="r1", status="running")
        assert result.workflow_id == "w1"


class TestRuntimeBackendAdapter:
    def test_resolve_runtime_db_path_explicit(self) -> None:
        with patch.dict("os.environ", {"KERNELONE_RUNTIME_DB": ":memory:"}):
            path = RuntimeBackendAdapter._resolve_runtime_db_path()
            assert path == ":memory:"

    def test_resolve_runtime_db_path_temp(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            path = RuntimeBackendAdapter._resolve_runtime_db_path()
            assert "polaris-runtime" in path

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        adapter = RuntimeBackendAdapter()
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.runtime_backend_adapter._get_runtime_backend",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_runtime = MagicMock()
            mock_runtime.stop = AsyncMock()
            mock_get.return_value = mock_runtime
            with patch.object(adapter, "_resolve_runtime_db_path", return_value=":memory:"):
                await adapter.start()
                assert adapter._running is True
                await adapter.stop()
                assert adapter._running is False

    @pytest.mark.asyncio
    async def test_require_runtime_raises(self) -> None:
        adapter = RuntimeBackendAdapter()
        with pytest.raises(RuntimeError, match="not started"):
            adapter._require_runtime()

    @pytest.mark.asyncio
    async def test_submit_workflow_validation(self) -> None:
        adapter = RuntimeBackendAdapter()
        # start adapter so _require_runtime doesn't raise
        mock_runtime = MagicMock()
        mock_runtime.stop = AsyncMock()
        with (
            patch(
                "polaris.cells.orchestration.workflow_runtime.internal.runtime_backend_adapter._get_runtime_backend",
                new_callable=AsyncMock,
                return_value=mock_runtime,
            ),
            patch.object(adapter, "_resolve_runtime_db_path", return_value=":memory:"),
        ):
            await adapter.start()
            with pytest.raises(ValueError, match="required"):
                await adapter.submit_workflow("", "wid")
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_submit_pm_workflow_validation(self) -> None:
        adapter = RuntimeBackendAdapter()
        with pytest.raises(ValueError, match="workspace is required"):
            await adapter.submit_pm_workflow("  ")


class TestSyncHelpers:
    def test_run_sync_no_loop(self) -> None:
        async def coro() -> str:
            return "ok"

        result = _run_sync(coro())
        assert result == "ok"

    def test_run_sync_inside_loop_raises(self) -> None:
        async def coro() -> str:
            return "ok"

        async def runner() -> None:
            _run_sync(coro())

        with pytest.raises(RuntimeError, match="active event loop"):
            import asyncio

            asyncio.run(runner())


class TestSubmitPmWorkflowSync:
    def test_disabled(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.runtime_backend_adapter.WorkflowConfig.from_env",
            return_value=MagicMock(enabled=False),
        ):
            result = submit_pm_workflow_sync("/tmp")
            assert result["ok"] is False
            assert result["status"] == "disabled"


class TestDescribeWorkflowSync:
    def test_disabled(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.runtime_backend_adapter.WorkflowConfig.from_env",
            return_value=MagicMock(enabled=False),
        ):
            result = describe_workflow_sync("w1")
            assert result["ok"] is False
            assert result["error"] == "workflow_runtime_disabled"


class TestQueryWorkflowSync:
    def test_disabled(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.runtime_backend_adapter.WorkflowConfig.from_env",
            return_value=MagicMock(enabled=False),
        ):
            result = query_workflow_sync("w1", "q1")
            assert result["ok"] is False
            assert result["error"] == "workflow_runtime_disabled"


class TestAdapterContextVars:
    @pytest.mark.asyncio
    async def test_get_adapter_creates_new(self) -> None:
        reset_adapter()
        adapter = await get_adapter()
        assert isinstance(adapter, RuntimeBackendAdapter)

    def test_set_adapter_factory(self) -> None:
        factory = MagicMock(return_value=MagicMock(spec=RuntimeBackendAdapter))
        set_adapter_factory(factory)
        assert factory is not None
