from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from polaris.tests.agent_stress.backend_bootstrap import ManagedBackendSession
from polaris.tests.agent_stress.backend_context import BackendContext


@pytest.mark.asyncio
async def test_managed_backend_session_drain_background_tasks_resets_task_handles() -> None:
    session = ManagedBackendSession(
        context=BackendContext(
            backend_url="http://127.0.0.1:59999",
            token="token",
            source="unit-test",
            desktop_info_path="C:/tmp/desktop-backend.json",
        ),
        auto_bootstrapped=True,
        desktop_info_path="C:/tmp/desktop-backend.json",
    )

    async def _stream_failure() -> None:
        raise RuntimeError("stream reader failed")

    async def _wait_forever() -> None:
        await asyncio.sleep(60)

    session._stdout_task = asyncio.create_task(_stream_failure())
    session._stderr_task = asyncio.create_task(_wait_forever())
    session._watch_task = asyncio.create_task(_wait_forever())
    await asyncio.sleep(0)

    errors = await session._drain_background_tasks(timeout=0.1)

    assert session._stdout_task is None
    assert session._stderr_task is None
    assert session._watch_task is None
    assert any("stream reader failed" in err for err in errors)


@pytest.mark.asyncio
async def test_terminate_backend_without_process_still_drains_tasks() -> None:
    session = ManagedBackendSession(
        context=BackendContext(
            backend_url="http://127.0.0.1:59999",
            token="token",
            source="unit-test",
            desktop_info_path="C:/tmp/desktop-backend.json",
        ),
        auto_bootstrapped=True,
        desktop_info_path="C:/tmp/desktop-backend.json",
    )

    async def _wait_forever() -> None:
        await asyncio.sleep(60)

    session._stdout_task = asyncio.create_task(_wait_forever())
    session._stderr_task = asyncio.create_task(_wait_forever())
    session._watch_task = asyncio.create_task(_wait_forever())
    await asyncio.sleep(0)

    await session._terminate_backend()

    assert session._stdout_task is None
    assert session._stderr_task is None
    assert session._watch_task is None
