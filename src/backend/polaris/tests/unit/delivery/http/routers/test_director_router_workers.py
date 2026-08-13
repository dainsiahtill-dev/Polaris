"""Director workers + LLM-events/cache/token-budget router tests.

Split from the historical test_v2_director_router.py Workers and
LLM Events / Cache / Token Budget sections."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from polaris.bootstrap.config import Settings


@pytest.mark.asyncio
async def test_director_list_workers(client: AsyncClient) -> None:
    """Director list workers should return worker list."""
    mock_worker = MagicMock()
    mock_worker.to_dict.return_value = {"id": "worker-1", "status": "idle"}

    mock_director = MagicMock()
    mock_director.list_workers = AsyncMock(return_value=[mock_worker])
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)

        response = await client.get("/v2/director/workers")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "worker-1"


@pytest.mark.asyncio
async def test_director_list_workers_falls_back_to_projection(client: AsyncClient) -> None:
    """Director worker list should expose projected worker evidence after local service empties."""
    mock_director = MagicMock()
    mock_director.list_workers = AsyncMock(return_value=[])
    mock_director.config.workspace = "."

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
    ):

        async def _resolve_workers(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_workers)
        mock_projection = MagicMock()
        mock_projection.director_merged = {
            "workers": {
                "worker_rows": [
                    {
                        "id": "projected-worker-1",
                        "name": "Projected Worker",
                        "status": "busy",
                        "current_task_id": "projected-task-1",
                        "healthy": True,
                    }
                ]
            }
        }
        mock_projection.director_local = {}
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/workers")

    assert response.status_code == 200
    data = response.json()
    assert data == [
        {
            "id": "projected-worker-1",
            "name": "Projected Worker",
            "status": "busy",
            "current_task_id": "projected-task-1",
            "healthy": True,
        }
    ]
    mock_director.list_workers.assert_awaited_once()
    mock_build.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_list_workers_projection_accepts_workspace_override(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Projected worker fallback should honor the requested workspace query."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Stale"
    mock_director = MagicMock()
    mock_director.list_workers = AsyncMock(return_value=[])
    mock_director.config.workspace = "C:/Temp/Stale"

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
    ):

        async def _resolve_workers(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_workers)
        mock_projection = MagicMock()
        mock_projection.director_merged = {}
        mock_projection.director_local = {}
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/workers?workspace=C%3A%2FTemp%2FVerified")

    assert response.status_code == 200
    mock_build.assert_awaited_once()
    build_call = mock_build.await_args
    assert build_call is not None
    assert build_call.args[0] == "C:/Temp/Verified"


@pytest.mark.asyncio
async def test_director_get_worker_found(client: AsyncClient) -> None:
    """Director get worker should return worker when found."""
    mock_worker = MagicMock()
    mock_worker.to_dict.return_value = {"id": "worker-1", "status": "busy", "task_id": "task-1"}

    mock_director = MagicMock()
    mock_director.get_worker = AsyncMock(return_value=mock_worker)
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)

        response = await client.get("/v2/director/workers/worker-1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "worker-1"


@pytest.mark.asyncio
async def test_director_get_worker_falls_back_to_projection(client: AsyncClient) -> None:
    """Director worker detail should resolve projected workers after a local miss."""
    mock_director = MagicMock()
    mock_director.get_worker = AsyncMock(return_value=None)
    mock_director.config.workspace = "."

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
    ):

        async def _resolve_worker_detail(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_worker_detail)
        mock_projection = MagicMock()
        mock_projection.director_merged = {
            "workers": {
                "worker_rows": [
                    {
                        "worker_id": "projected-worker-1",
                        "name": "Projected Worker",
                        "status": "busy",
                        "currentTaskId": "projected-task-1",
                        "healthy": True,
                    }
                ]
            }
        }
        mock_projection.director_local = {}
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/workers/projected-worker-1")

    assert response.status_code == 200
    data = response.json()
    assert data["worker_id"] == "projected-worker-1"
    assert data["name"] == "Projected Worker"
    assert data["status"] == "busy"
    assert data["currentTaskId"] == "projected-task-1"
    mock_director.get_worker.assert_awaited_once_with("projected-worker-1")
    mock_build.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_get_worker_not_found(client: AsyncClient) -> None:
    """Director get worker should 404 when worker doesn't exist."""
    mock_director = MagicMock()
    mock_director.get_worker = AsyncMock(return_value=None)
    mock_director.config.workspace = "."

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
    ):

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)
        mock_projection = MagicMock()
        mock_projection.director_merged = {}
        mock_projection.director_local = {}
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/workers/nonexistent")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_director_task_llm_events(client: AsyncClient) -> None:
    """Get task LLM events should return events for a specific task."""
    mock_event = MagicMock()
    mock_event.event_type = "llm_call_start"
    mock_event.metadata = {"workspace": "."}
    mock_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-1",
        "task_id": "task-1",
    }

    with patch(
        "polaris.delivery.http.v2.director.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [mock_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/director/tasks/task-1/llm-events?run_id=run-1")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "task-1"
        assert data["stats"]["total"] == 1
        assert data["stats"]["call_start"] == 1


@pytest.mark.asyncio
async def test_director_task_llm_events_filters_requested_workspace(client: AsyncClient, tmp_path: Path) -> None:
    """Director task LLM events should filter shared history by requested workspace."""
    requested_workspace = tmp_path / "requested"
    other_workspace = tmp_path / "other"
    requested_workspace.mkdir()
    other_workspace.mkdir()

    matching_event = MagicMock()
    matching_event.event_type = "llm_call_start"
    matching_event.metadata = {"workspace": str(requested_workspace)}
    matching_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-requested",
        "task_id": "task-1",
    }

    other_event = MagicMock()
    other_event.event_type = "llm_call_start"
    other_event.metadata = {"workspace": str(other_workspace)}
    other_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-other",
        "task_id": "task-1",
    }

    with patch(
        "polaris.delivery.http.v2.director.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [matching_event, other_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get(
            "/v2/director/tasks/task-1/llm-events",
            params={"workspace": str(requested_workspace), "limit": "5"},
        )

    assert response.status_code == 200
    data = response.json()
    assert Path(data["workspace"]).resolve() == requested_workspace.resolve()
    assert data["stats"]["total"] == 1
    assert data["events"][0]["run_id"] == "run-requested"


@pytest.mark.asyncio
async def test_director_task_llm_events_filters_active_workspace_by_default(client: AsyncClient) -> None:
    """Director task LLM events should filter to active workspace without query workspace."""
    matching_event = MagicMock()
    matching_event.event_type = "llm_call_start"
    matching_event.metadata = {"workspace": "."}
    matching_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-active",
        "task_id": "task-1",
    }
    other_event = MagicMock()
    other_event.event_type = "llm_call_start"
    other_event.metadata = {"workspace": "/tmp/other-workspace"}
    other_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-other",
        "task_id": "task-1",
    }

    with patch(
        "polaris.delivery.http.v2.director.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [matching_event, other_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/director/tasks/task-1/llm-events?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["workspace"] == "."
    assert data["stats"]["total"] == 1
    assert data["events"][0]["run_id"] == "run-active"


@pytest.mark.asyncio
async def test_director_global_llm_events(client: AsyncClient) -> None:
    """Get global LLM events should return all events."""
    mock_event = MagicMock()
    mock_event.event_type = "llm_error"
    mock_event.metadata = {"workspace": "."}
    mock_event.to_dict.return_value = {
        "event_type": "llm_error",
        "run_id": "run-1",
        "role": "director",
    }

    with patch(
        "polaris.delivery.http.v2.director.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [mock_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/director/llm-events?role=director")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["events"][0]["event_type"] == "llm_error"


@pytest.mark.asyncio
async def test_director_global_llm_events_filters_requested_workspace(client: AsyncClient, tmp_path: Path) -> None:
    """Director global LLM events should not mix evidence from another workspace."""
    requested_workspace = tmp_path / "requested"
    other_workspace = tmp_path / "other"
    requested_workspace.mkdir()
    other_workspace.mkdir()

    matching_event = MagicMock()
    matching_event.event_type = "llm_error"
    matching_event.metadata = {"workspace": str(requested_workspace)}
    matching_event.to_dict.return_value = {
        "event_type": "llm_error",
        "run_id": "run-requested",
        "role": "director",
    }

    other_event = MagicMock()
    other_event.event_type = "llm_error"
    other_event.metadata = {"workspace": str(other_workspace)}
    other_event.to_dict.return_value = {
        "event_type": "llm_error",
        "run_id": "run-other",
        "role": "director",
    }

    with patch(
        "polaris.delivery.http.v2.director.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [matching_event, other_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get(
            "/v2/director/llm-events",
            params={"role": "director", "workspace": str(requested_workspace)},
        )

    assert response.status_code == 200
    data = response.json()
    assert Path(data["workspace"]).resolve() == requested_workspace.resolve()
    assert data["count"] == 1
    assert data["events"][0]["run_id"] == "run-requested"


@pytest.mark.asyncio
async def test_director_cache_stats(client: AsyncClient) -> None:
    """Get Director cache stats should return cache statistics."""
    with patch(
        "polaris.cells.roles.kernel.public.service.get_global_llm_cache",
    ) as mock_get_cache:
        mock_cache = MagicMock()
        mock_cache.get_stats.return_value = {"hits": 50, "misses": 10, "size": 60}
        mock_get_cache.return_value = mock_cache

        response = await client.get("/v2/director/cache-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["hits"] == 50
        assert data["misses"] == 10


@pytest.mark.asyncio
async def test_director_cache_clear(client: AsyncClient) -> None:
    """Clear Director cache should return success."""
    with patch(
        "polaris.cells.roles.kernel.public.service.get_global_llm_cache",
    ) as mock_get_cache:
        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache

        response = await client.post("/v2/director/cache-clear")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        mock_cache.clear.assert_called_once()


@pytest.mark.asyncio
async def test_director_token_budget_stats(client: AsyncClient) -> None:
    """Get Director token budget stats should return budget information."""
    with patch(
        "polaris.delivery.http.v2.director.get_global_token_budget",
    ) as mock_get_budget:
        mock_budget = MagicMock()
        mock_budget.get_stats.return_value = {
            "total_budget": 50000,
            "used_tokens": 2500,
            "remaining": 47500,
        }
        mock_get_budget.return_value = mock_budget

        response = await client.get("/v2/director/token-budget-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_budget"] == 50000
        assert data["used_tokens"] == 2500
