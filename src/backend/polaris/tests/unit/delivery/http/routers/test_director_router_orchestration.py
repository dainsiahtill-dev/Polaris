"""Director orchestration + integration-QA router tests (run/get orchestration).

Split from the historical test_v2_director_router.py Orchestration and
Integration QA sections."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from polaris.bootstrap.config import Settings
from polaris.kernelone.storage.io_paths import build_cache_root
from polaris.tests.unit.delivery.http.routers._helpers import (
    _director_run_diagnostics,
)


@pytest.mark.asyncio
async def test_director_run_orchestration(client: AsyncClient) -> None:
    """Director run orchestration should create a run."""
    mock_result = MagicMock()
    mock_result.run_id = "run-789"
    mock_result.status = "running"
    mock_result.message = "Director started in parallel mode"
    mock_result.metadata = {"tasks_queued": 2, "task_ids": ["PM-1", "PM-2"]}

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(workspace=".")

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": ".",
                "max_workers": 3,
                "execution_mode": "parallel",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-789"
        assert data["status"] == "running"
        assert data["workspace"] == "."
        assert data["tasks_queued"] == 2
        mock_preflight.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_run_orchestration_defaults_to_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director run should resolve omitted workspace through active desktop settings."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_result = MagicMock()
    mock_result.run_id = "run-active"
    mock_result.status = "running"
    mock_result.message = "Director started in parallel mode"
    mock_result.metadata = {"tasks_queued": 0}

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(workspace="C:/Temp/Product")

        response = await client.post(
            "/v2/director/run",
            json={
                "max_workers": 3,
                "execution_mode": "parallel",
            },
        )

    assert response.status_code == 200
    execute_args = mock_service.execute_director_run.await_args
    assert execute_args is not None
    _, kwargs = execute_args
    assert kwargs["workspace"] == "C:/Temp/Product"
    assert response.json()["workspace"] == "C:/Temp/Product"
    mock_preflight.assert_awaited_once()
    preflight_args = mock_preflight.await_args
    assert preflight_args is not None
    assert preflight_args.args[1] == "C:/Temp/Product"


@pytest.mark.asyncio
async def test_director_run_orchestration_preserves_explicit_workspace(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director run should not override explicit non-dot API workspace values."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_result = MagicMock()
    mock_result.run_id = "run-explicit"
    mock_result.status = "running"
    mock_result.message = "Director started in parallel mode"
    mock_result.metadata = {"tasks_queued": 0}

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(workspace="D:/Explicit/Product")

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": "D:/Explicit/Product",
                "max_workers": 3,
                "execution_mode": "parallel",
            },
        )

    assert response.status_code == 200
    execute_args = mock_service.execute_director_run.await_args
    assert execute_args is not None
    _, kwargs = execute_args
    assert kwargs["workspace"] == "D:/Explicit/Product"
    assert response.json()["workspace"] == "D:/Explicit/Product"
    mock_preflight.assert_awaited_once()
    preflight_args = mock_preflight.await_args
    assert preflight_args is not None
    assert preflight_args.args[1] == "D:/Explicit/Product"


@pytest.mark.asyncio
async def test_director_run_orchestration_serial_mode(client: AsyncClient) -> None:
    """Director run orchestration should support serial mode."""
    mock_result = MagicMock()
    mock_result.run_id = "run-abc"
    mock_result.status = "running"
    mock_result.message = None
    mock_result.metadata = None

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(workspace=".")

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": ".",
                "execution_mode": "serial",
                "task_filter": "priority:high",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-abc"
        assert "serial" in data["message"]
        mock_preflight.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_run_orchestration_accepts_task_id(client: AsyncClient) -> None:
    """Director run orchestration should forward selected task id into options."""
    mock_result = MagicMock()
    mock_result.run_id = "run-task"
    mock_result.status = "running"
    mock_result.message = "Director started for selected task"
    mock_result.metadata = None

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(workspace=".")

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": ".",
                "execution_mode": "parallel",
                "task_id": "PM-42",
            },
        )

        assert response.status_code == 200
        _, kwargs = mock_service.execute_director_run.await_args
        assert kwargs["tasks"] == ["PM-42"]
        assert kwargs["options"]["task_id"] == "PM-42"
        assert kwargs["options"]["task_filter"] == "PM-42"
        assert response.json()["tasks_queued"] == 1
        mock_preflight.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_run_orchestration_uses_diagnostics_ready_tasks_when_no_task_selected(
    client: AsyncClient,
) -> None:
    """Director run should execute diagnostics-ready workflow tasks by default."""
    mock_result = MagicMock()
    mock_result.run_id = "run-ready"
    mock_result.status = "running"
    mock_result.message = "Director started for ready tasks"
    mock_result.metadata = {"tasks_queued": 1, "task_ids": ["PM-42"]}

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(workspace=".")

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": ".",
                "execution_mode": "parallel",
            },
        )

        assert response.status_code == 200
        _, kwargs = mock_service.execute_director_run.await_args
        assert kwargs["tasks"] == ["PM-42"]
        assert kwargs["options"]["task_filter"] == "PM-42"
        assert kwargs["options"]["metadata"]["task_selection_source"] == "diagnostics_ready"
        assert kwargs["options"]["metadata"]["selected_task_ids"] == ["PM-42"]
        assert response.json()["tasks_queued"] == 1
        mock_preflight.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_run_orchestration_merges_direct_and_blueprint_ready_tasks(
    client: AsyncClient,
) -> None:
    """Director run should not hide direct PM tasks when CE blueprint tasks are also ready."""
    mock_result = MagicMock()
    mock_result.run_id = "run-mixed-ready"
    mock_result.status = "running"
    mock_result.message = "Director started for mixed ready tasks"
    mock_result.metadata = {"tasks_queued": 2, "task_ids": ["chief-1", "direct-1"]}

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
    ):
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service
        mock_preflight.return_value = _director_run_diagnostics(
            workspace=".",
            ready_task_ids=["direct-1", "chief-1"],
            blueprint_ready_task_ids=["chief-1"],
        )

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": ".",
                "execution_mode": "parallel",
            },
        )

        assert response.status_code == 200
        _, kwargs = mock_service.execute_director_run.await_args
        assert kwargs["tasks"] == ["chief-1", "direct-1"]
        assert kwargs["options"]["task_filter"] is None
        assert kwargs["options"]["metadata"]["task_selection_source"] == "diagnostics_mixed_ready"
        assert kwargs["options"]["metadata"]["selected_task_ids"] == ["chief-1", "direct-1"]
        assert response.json()["tasks_queued"] == 2
        mock_preflight.assert_awaited_once()


@pytest.mark.asyncio
async def test_director_run_orchestration_blocks_when_diagnostics_cannot_execute(
    client: AsyncClient,
) -> None:
    """Director run should fail closed when readiness diagnostics report blockers."""
    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.director._build_director_diagnostics_for_request",
            new_callable=AsyncMock,
        ) as mock_preflight,
    ):
        mock_preflight.return_value = _director_run_diagnostics(
            can_execute=False,
            execution_blockers=["director_no_ready_tasks"],
        )

        response = await client.post(
            "/v2/director/run",
            json={
                "workspace": ".",
                "execution_mode": "parallel",
            },
        )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "DIRECTOR_EXECUTION_BLOCKED"
    assert data["error"]["details"]["execution_blockers"] == ["director_no_ready_tasks"]
    assert data["error"]["details"]["diagnostics"]["can_execute"] is False
    mock_service_cls.assert_not_called()


@pytest.mark.asyncio
async def test_director_get_orchestration_found(client: AsyncClient) -> None:
    """Director get orchestration should return run details."""
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-789"
    mock_snapshot.status.value = "running"
    mock_snapshot.workspace = "."
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/director/runs/run-789")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-789"
        assert data["status"] == "running"


@pytest.mark.asyncio
async def test_director_get_orchestration_honors_requested_workspace(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director run evidence should not leak runs from a different desktop workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Requested"
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-789"
    mock_snapshot.status.value = "running"
    mock_snapshot.workspace = "C:/Temp/Requested"
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/director/runs/run-789?workspace=C%3A%2FTemp%2FRequested")

    assert response.status_code == 200
    assert response.json()["workspace"] == "C:/Temp/Requested"


@pytest.mark.asyncio
async def test_director_get_orchestration_hides_workspace_mismatch(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director run detail should return 404 when a run belongs to another workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Requested"
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-789"
    mock_snapshot.status.value = "running"
    mock_snapshot.workspace = "D:/Other/Product"
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/director/runs/run-789?workspace=C%3A%2FTemp%2FRequested")

    assert response.status_code == 404
    assert "Run not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_director_get_orchestration_accepts_plain_string_status(client: AsyncClient) -> None:
    """Director run detail should tolerate runtime snapshots with string statuses."""
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-string-status"
    mock_snapshot.status = "completed"
    mock_snapshot.workspace = "."
    mock_snapshot.tasks = None

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/director/runs/run-string-status")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-string-status"
        assert data["status"] == "completed"
        assert data["tasks_queued"] == 0


@pytest.mark.asyncio
async def test_director_cancel_orchestration_run(client: AsyncClient) -> None:
    """Director cancel orchestration should call the runtime cancel path."""
    mock_current_snapshot = MagicMock()
    mock_current_snapshot.run_id = "run-789"
    mock_current_snapshot.status.value = "running"
    mock_current_snapshot.workspace = "."
    mock_current_snapshot.tasks = {"task-1": MagicMock()}

    mock_cancelled_snapshot = MagicMock()
    mock_cancelled_snapshot.run_id = "run-789"
    mock_cancelled_snapshot.status.value = "cancelled"
    mock_cancelled_snapshot.workspace = "."
    mock_cancelled_snapshot.tasks = {"task-1": MagicMock()}

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_current_snapshot)
        mock_service.cancel_run = AsyncMock(return_value=mock_cancelled_snapshot)
        mock_orch.return_value = mock_service

        response = await client.post("/v2/director/runs/run-789/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-789"
        assert data["status"] == "cancelled"
        assert data["tasks_queued"] == 1
        mock_service.query_run.assert_awaited_once_with("run-789")
        mock_service.cancel_run.assert_awaited_once_with("run-789")


@pytest.mark.asyncio
async def test_director_cancel_orchestration_hides_workspace_mismatch(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Director run cancel should not cancel a run from another desktop workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Requested"
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-789"
    mock_snapshot.status.value = "running"
    mock_snapshot.workspace = "D:/Other/Product"
    mock_snapshot.tasks = {"task-1": MagicMock()}

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_service.cancel_run = AsyncMock()
        mock_orch.return_value = mock_service

        response = await client.post("/v2/director/runs/run-789/cancel?workspace=C%3A%2FTemp%2FRequested")

    assert response.status_code == 404
    mock_service.cancel_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_cancel_orchestration_terminal_run_is_idempotent(client: AsyncClient) -> None:
    """Director cancel orchestration should return terminal snapshots unchanged."""
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-789"
    mock_snapshot.status.value = "completed"
    mock_snapshot.workspace = "."
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_service.cancel_run = AsyncMock()
        mock_orch.return_value = mock_service

        response = await client.post("/v2/director/runs/run-789/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-789"
        assert data["status"] == "completed"
        mock_service.query_run.assert_awaited_once_with("run-789")
        mock_service.cancel_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_cancel_orchestration_string_status_is_idempotent(client: AsyncClient) -> None:
    """Director cancel should not require enum-like status values for terminal runs."""
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-string-status"
    mock_snapshot.status = "completed"
    mock_snapshot.workspace = "."
    mock_snapshot.tasks = ["task-1", "task-2"]

    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_service.cancel_run = AsyncMock()
        mock_orch.return_value = mock_service

        response = await client.post("/v2/director/runs/run-string-status/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-string-status"
        assert data["status"] == "completed"
        assert data["tasks_queued"] == 2
        mock_service.query_run.assert_awaited_once_with("run-string-status")
        mock_service.cancel_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_cancel_orchestration_not_found(client: AsyncClient) -> None:
    """Director cancel orchestration should 404 for unknown run_id."""
    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=None)
        mock_orch.return_value = mock_service

        response = await client.post("/v2/director/runs/nonexistent/cancel")
        assert response.status_code == 404
        assert "Run not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_director_get_orchestration_not_found(client: AsyncClient) -> None:
    """Director get orchestration should 404 for unknown run_id."""
    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=None)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/director/runs/nonexistent")
        assert response.status_code == 404
        assert "Run not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_director_get_orchestration_server_error(client: AsyncClient) -> None:
    """Director get orchestration should 500 on unexpected errors."""
    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(side_effect=RuntimeError("db failure"))
        mock_orch.return_value = mock_service

        response = await client.get("/v2/director/runs/run-789")
        assert response.status_code == 500
        assert "internal error" in response.json()["detail"]


@pytest.mark.asyncio
async def test_director_integration_qa_persists_to_active_runtime_root(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """API-triggered integration QA should persist artifacts in the active runtime root."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_base = tmp_path / "runtime-base"
    mock_settings.workspace = str(workspace)
    mock_settings.workspace_path = str(workspace)
    mock_settings.ramdisk_root = str(runtime_base)
    cache_root = build_cache_root(str(runtime_base), str(workspace))

    director_result = {
        "status": "success",
        "successes": 1,
        "failures": 0,
        "blocked": 0,
        "tasks": [{"id": "PM-1", "status": "done"}],
    }
    task_rows = [
        {
            "task_id": "PM-1",
            "assigned_to": "director",
            "status": "done",
            "target_files": ["src/index.ts"],
            "metadata": {"pm_task_id": "PM-1"},
        }
    ]

    with (
        patch(
            "polaris.cells.orchestration.workflow_runtime.public.service.persist_director_result_from_runtime",
            return_value=director_result,
        ),
        patch(
            "polaris.cells.orchestration.workflow_runtime.public.service.build_integration_qa_tasks_from_director_result",
            return_value=task_rows,
        ),
        patch(
            "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._resolve_verify_runner",
            return_value=lambda _workspace: (True, "Integration verification passed", []),
        ),
        patch(
            "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._attach_pm_dispatch_qa_cognitive_receipt",
        ),
    ):
        response = await client.post(
            "/v2/director/integration-qa",
            json={
                "workspace": str(workspace),
                "run_id": "api-qa-1",
                "iteration": 2,
            },
        )

    assert response.status_code == 200
    data = response.json()
    result = data["result"]
    result_path = Path(result["result_path"])
    runtime_result_path = Path(result["runtime_result_path"])
    assert result_path == Path(cache_root) / "runs" / "api-qa-1" / "qa" / "integration_qa.result.json"
    assert runtime_result_path == Path(cache_root) / "results" / "integration_qa.result.json"
    assert result_path.is_file()
    assert runtime_result_path.is_file()
    persisted = json.loads(runtime_result_path.read_text(encoding="utf-8"))
    assert persisted["reason"] == "integration_qa_passed"
    assert persisted["passed"] is True
