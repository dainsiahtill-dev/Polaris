"""Tests for CancellationCoordinator — cancellation orchestration."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.speculation.cancel import CancellationCoordinator
from polaris.cells.roles.kernel.internal.speculation.registry import ShadowTaskRegistry
from polaris.cells.roles.kernel.internal.speculation.task_group import TurnScopedTaskGroup


@pytest.fixture
def mock_registry() -> AsyncMock:
    return AsyncMock(spec=ShadowTaskRegistry)


@pytest.fixture
def mock_task_group() -> AsyncMock:
    return AsyncMock(spec=TurnScopedTaskGroup)


@pytest.fixture
def coordinator() -> CancellationCoordinator:
    return CancellationCoordinator()


class TestRefuseTurn:
    """Tests for refuse_turn() — refusal abort semantics."""

    @pytest.mark.asyncio
    async def test_refuse_turn_abandons_entire_turn(
        self, coordinator: CancellationCoordinator, mock_registry: AsyncMock
    ) -> None:
        """refuse_turn should abandon all tasks in the turn."""
        mock_registry.get_turn_records.return_value = []
        await coordinator.refuse_turn("turn_1", mock_registry)
        mock_registry.abandon_turn.assert_awaited_once_with("turn_1", reason="refusal_abort")

    @pytest.mark.asyncio
    async def test_refuse_turn_cancels_task_group_when_provided(
        self, coordinator: CancellationCoordinator, mock_registry: AsyncMock, mock_task_group: AsyncMock
    ) -> None:
        """With task_group, refuse_turn should cancel all tasks."""
        await coordinator.refuse_turn("turn_1", mock_registry, task_group=mock_task_group)
        mock_task_group.cancel_all.assert_awaited_once_with(salvage=False)


class TestCancelTurn:
    """Tests for cancel_turn() — graceful vs hard cancel."""

    @pytest.mark.asyncio
    async def test_cancel_turn_with_salvage_calls_cancel_with_salvage(
        self, coordinator: CancellationCoordinator, mock_registry: AsyncMock, mock_task_group: AsyncMock
    ) -> None:
        """salvage=True should use cancel_with_salvage."""
        mock_registry.get_turn_records.return_value = []
        await coordinator.cancel_turn("turn_1", mock_registry, mock_task_group, salvage=True)
        mock_task_group.cancel_with_salvage.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_turn_without_salvage_calls_cancel_all(
        self, coordinator: CancellationCoordinator, mock_registry: AsyncMock, mock_task_group: AsyncMock
    ) -> None:
        """salvage=False should hard cancel all tasks."""
        await coordinator.cancel_turn("turn_1", mock_registry, mock_task_group, salvage=False)
        mock_task_group.cancel_all.assert_awaited_once_with(salvage=False)

    @pytest.mark.asyncio
    async def test_cancel_turn_drains_turn_on_hard_cancel(
        self, coordinator: CancellationCoordinator, mock_registry: AsyncMock, mock_task_group: AsyncMock
    ) -> None:
        """Hard cancel should also drain the turn registry."""
        await coordinator.cancel_turn("turn_1", mock_registry, mock_task_group, salvage=False)
        mock_registry.drain_turn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_turn_abandons_completed_unadopted_on_hard_cancel(
        self, coordinator: CancellationCoordinator, mock_registry: AsyncMock, mock_task_group: AsyncMock
    ) -> None:
        """Hard cancel should abandon completed but unadopted tasks."""
        await coordinator.cancel_turn("turn_1", mock_registry, mock_task_group, salvage=False)
        mock_registry.abandon_turn.assert_awaited_once_with("turn_1", reason="turn_cancel")
