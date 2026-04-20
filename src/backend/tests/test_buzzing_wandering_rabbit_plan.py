"""Regression tests for buzzing-wandering-rabbit implementation milestones."""

from __future__ import annotations

import pytest


def test_factory_can_be_constructed(mock_workspace: str) -> None:
    """Factory service construction should not fail."""
    from pathlib import Path

    from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryRunService

    factory = FactoryRunService(workspace=Path(mock_workspace))
    assert factory.workspace == Path(mock_workspace)


def test_qa_agent_can_be_constructed(mock_workspace: str) -> None:
    """QA agent should match current RoleAgent constructor contract."""
    from polaris.cells.qa.audit_verdict.internal.qa_agent import QAAgent

    agent = QAAgent(workspace=mock_workspace)
    assert agent.agent_name == "QA"


@pytest.mark.asyncio
async def test_container_provides_director_and_background_services() -> None:
    """DI container should expose Director and BackgroundTask services."""
    from polaris.cells.director.execution.public.service import DirectorService
    from polaris.domain.services.background_task import BackgroundTaskService
    from polaris.infrastructure.di.container import get_container, reset_container

    reset_container()
    container = await get_container()

    director = await container.resolve_async(DirectorService)
    background = await container.resolve_async(BackgroundTaskService)

    assert isinstance(director, DirectorService)
    assert isinstance(background, BackgroundTaskService)

