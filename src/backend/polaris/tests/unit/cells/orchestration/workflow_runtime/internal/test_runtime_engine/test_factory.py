"""Tests for workflow_runtime internal runtime_engine factory module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime.factory import (
    RuntimeFactory,
    get_runtime,
)


class TestRuntimeFactory:
    @pytest.fixture(autouse=True)
    def reset_factory(self) -> None:
        RuntimeFactory._instance = None
        RuntimeFactory._runtime_type = None

    @pytest.mark.asyncio
    async def test_create_runtime_workflow(self) -> None:
        with patch.object(
            RuntimeFactory,
            "_create_workflow",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ) as mock_create:
            runtime = await RuntimeFactory.create_runtime("workflow")
            assert runtime is not None
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_runtime_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown runtime type"):
            await RuntimeFactory.create_runtime("unknown")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_get_runtime(self) -> None:
        with patch.object(
            RuntimeFactory,
            "create_runtime",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ) as mock_create:
            runtime = await get_runtime()
            assert runtime is not None
            mock_create.assert_called_once_with("workflow", None)

    @pytest.mark.asyncio
    async def test_shutdown_runtime(self) -> None:
        mock_runtime = MagicMock()
        mock_runtime.stop = AsyncMock()
        RuntimeFactory._instance = mock_runtime
        RuntimeFactory._runtime_type = "workflow"
        await RuntimeFactory.shutdown_runtime()
        mock_runtime.stop.assert_awaited_once()
        assert RuntimeFactory._instance is None

    def test_get_runtime_type(self) -> None:
        assert RuntimeFactory.get_runtime_type() is None
        RuntimeFactory._runtime_type = "workflow"
        assert RuntimeFactory.get_runtime_type() == "workflow"
