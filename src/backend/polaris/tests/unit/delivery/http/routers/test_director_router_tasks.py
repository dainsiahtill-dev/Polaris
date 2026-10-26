"""Director task-management router tests (create/list/get/cancel).

Split from the historical test_v2_director_router.py Task Management section."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.tests.unit.delivery.http.routers.conftest import _DirectorRouterTestSettings


@pytest.mark.asyncio
async def test_director_create_task(client: AsyncClient) -> None:
    """Retired task creation must refuse the legacy write without submitting it.

    OLD GOAL (retired): POST /v2/director/tasks submitted a task to the legacy
    ``DirectorService`` and answered ``200`` with the created task row.
    CURRENT MEANING: new work is created through ``EngineeringRunService``
    (``POST /v2/engineering/runs``); the legacy task write route is retired and
    refuses with ``410 legacy_engine_retired`` before any task is submitted.
    """
    from polaris.domain.entities import TaskPriority, TaskResult, TaskStatus

    mock_task = MagicMock()
    mock_task.id = "task-123"
    mock_task.subject = "Test task"
    mock_task.description = "Description"
    mock_task.status = TaskStatus.PENDING
    mock_task.priority = TaskPriority.MEDIUM
    mock_task.claimed_by = None
    mock_task.result = TaskResult(success=True)
    mock_task.metadata = {}

    mock_director = MagicMock()
    mock_director.submit_task = AsyncMock(return_value=mock_task)
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve_create(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_create)

        response = await client.post(
            "/v2/director/tasks",
            json={
                "subject": "Test task",
                "description": "Description",
                "priority": "MEDIUM",
            },
        )

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error_code"] == "legacy_engine_retired"
    assert "/v2/engineering/runs" in detail["supported_endpoints"]
    mock_container.return_value.resolve_async.assert_not_awaited()
    mock_director.submit_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_create_task_uses_workspace_query_for_service_and_metadata(client: AsyncClient) -> None:
    """A workspace-scoped task create on the retired route is refused, not rebound.

    OLD GOAL (retired): the ``workspace`` query selected a rebound Director
    service and the submitted task metadata carried that workspace.
    CURRENT MEANING: the legacy task write route is retired.  The workspace
    request variant is refused with ``410 legacy_engine_retired``; no service is
    resolved or rebound and no task metadata is produced.
    """
    from polaris.domain.entities import TaskPriority, TaskResult, TaskStatus

    target_workspace = "C:/Temp/Product"

    mock_task = MagicMock()
    mock_task.id = "task-workspace"
    mock_task.subject = "Workspace task"
    mock_task.description = "Description"
    mock_task.status = TaskStatus.PENDING
    mock_task.priority = TaskPriority.HIGH
    mock_task.claimed_by = None
    mock_task.result = TaskResult(success=True)
    mock_task.metadata = {}

    stale_director = MagicMock()
    stale_director.config.workspace = "C:/Other"
    stale_director.submit_task = AsyncMock()

    workspace_director = MagicMock()
    workspace_director.config.workspace = str(Path(target_workspace).resolve())
    workspace_director.submit_task = AsyncMock(return_value=mock_task)

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.dependencies.rebind_director_service",
            new_callable=AsyncMock,
            return_value=workspace_director,
        ) as mock_rebind,
    ):

        async def _resolve(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return stale_director
            return MagicMock()

        mock_container.return_value.has_registration.return_value = True
        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve)

        response = await client.post(
            "/v2/director/tasks",
            params={"workspace": target_workspace},
            json={
                "subject": "Workspace task",
                "description": "Description",
                "priority": "HIGH",
                "metadata": {"source": "desktop"},
            },
        )

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_rebind.assert_not_awaited()
    mock_container.return_value.resolve_async.assert_not_awaited()
    stale_director.submit_task.assert_not_awaited()
    workspace_director.submit_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_create_task_with_command(client: AsyncClient) -> None:
    """A command-carrying task create on the retired route is refused and runs nothing.

    OLD GOAL (retired): a task carrying a ``command`` was submitted to the legacy
    Director service, which could execute it.
    CURRENT MEANING: command execution is owned by the engineering run's
    permission-scoped tools; the retired route refuses the request variant with
    ``410 legacy_engine_retired`` and never submits or executes the command.
    """
    from polaris.domain.entities import TaskPriority, TaskResult, TaskStatus

    mock_task = MagicMock()
    mock_task.id = "task-456"
    mock_task.subject = "Run tests"
    mock_task.description = ""
    mock_task.status = TaskStatus.PENDING
    mock_task.priority = TaskPriority.HIGH
    mock_task.claimed_by = None
    mock_task.result = TaskResult(success=True)
    mock_task.metadata = {"command": "pytest"}

    mock_director = MagicMock()
    mock_director.submit_task = AsyncMock(return_value=mock_task)
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

        response = await client.post(
            "/v2/director/tasks",
            json={
                "subject": "Run tests",
                "command": "pytest -x",
                "priority": "HIGH",
                "blocked_by": ["task-123"],
                "timeout_seconds": 300,
                "metadata": {"command": "pytest"},
            },
        )

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_container.return_value.resolve_async.assert_not_awaited()
    mock_director.submit_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_create_task_accepts_lowercase_priority(client: AsyncClient) -> None:
    """The lowercase-priority variant is refused on the retired route before parsing.

    OLD GOAL (retired): a lowercase priority was normalised to the
    ``TaskPriority`` enum and submitted.
    CURRENT MEANING: the retired route refuses first, so no priority is
    normalised and no task is submitted.
    """
    from polaris.domain.entities import TaskPriority, TaskResult, TaskStatus

    mock_task = MagicMock()
    mock_task.id = "task-789"
    mock_task.subject = "Run focused tests"
    mock_task.description = ""
    mock_task.status = TaskStatus.PENDING
    mock_task.priority = TaskPriority.HIGH
    mock_task.claimed_by = None
    mock_task.result = TaskResult(success=True)
    mock_task.metadata = {}

    mock_director = MagicMock()
    mock_director.submit_task = AsyncMock(return_value=mock_task)
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

        response = await client.post(
            "/v2/director/tasks",
            json={
                "subject": "Run focused tests",
                "priority": "high",
            },
        )

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_container.return_value.resolve_async.assert_not_awaited()
    mock_director.submit_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_create_task_rejects_invalid_priority(client: AsyncClient) -> None:
    """An invalid priority is refused by retirement, not by legacy validation.

    OLD GOAL (retired): an unknown priority produced a structured ``400
    INVALID_TASK_PRIORITY`` with the allowed values.
    CURRENT MEANING: the retired route refuses every request before any legacy
    validation runs, so the refusal is ``410 legacy_engine_retired`` and no
    legacy validation payload is produced.
    """
    mock_director = MagicMock()
    mock_director.submit_task = AsyncMock()
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

        response = await client.post(
            "/v2/director/tasks",
            json={
                "subject": "Bad task",
                "priority": "urgent",
            },
        )

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error_code"] == "legacy_engine_retired"
    assert "INVALID_TASK_PRIORITY" not in str(detail)
    mock_container.return_value.resolve_async.assert_not_awaited()
    mock_director.submit_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_list_tasks(client: AsyncClient) -> None:
    """Director list tasks should return an empty list when projection and local queue are empty."""
    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.config.workspace = "."
    mock_task_market = MagicMock()
    mock_task_market.query_status.return_value = MagicMock(items=())

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
        patch("polaris.delivery.http.v2.director.get_task_market_service", return_value=mock_task_market),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks")
        assert response.status_code == 200
        data = response.json()
        assert data == []
        mock_director.list_tasks.assert_awaited_once_with(status=None)


@pytest.mark.asyncio
async def test_director_list_tasks_auto_falls_back_to_local_queue(client: AsyncClient) -> None:
    """source=auto should expose local Director tasks when workflow projection is empty."""
    from polaris.domain.entities import TaskPriority, TaskStatus

    mock_task = MagicMock()
    mock_task.id = "local-task-1"
    mock_task.subject = "Local Director task"
    mock_task.description = "Queued outside workflow projection"
    mock_task.status = TaskStatus.PENDING
    mock_task.priority = TaskPriority.HIGH
    mock_task.claimed_by = None
    mock_task.result = None
    mock_task.metadata = {"pm_task_id": "PM-local", "blueprint_id": "BP-local"}

    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[mock_task])
    mock_director.config.workspace = "."
    mock_task_market = MagicMock()
    mock_task_market.query_status.return_value = MagicMock(items=())

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
        patch("polaris.delivery.http.v2.director.get_task_market_service", return_value=mock_task_market),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks?source=auto")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "local-task-1"
    assert data[0]["subject"] == "Local Director task"
    assert data[0]["status"] == "PENDING"
    assert data[0]["pm_task_id"] == "PM-local"
    assert data[0]["blueprint_id"] == "BP-local"
    mock_director.list_tasks.assert_awaited_once_with(status=None)


@pytest.mark.asyncio
async def test_director_list_tasks_uses_task_market_execution_rows(client: AsyncClient) -> None:
    """Task-market pending_exec rows should be visible before runtime projection exists."""
    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.config.workspace = "."
    mock_task_market = MagicMock()
    mock_task_market.query_status.return_value = MagicMock(
        items=(
            {
                "task_id": "PM-100",
                "stage": "pending_exec",
                "status": "pending_exec",
                "priority": "high",
                "claimed_by": "",
                "depends_on": [],
                "payload": {
                    "title": "Implement combat loop",
                    "goal": "Create deterministic combat loop",
                    "target_files": ["src/combat.ts"],
                    "scope_paths": ["src/combat.ts"],
                    "blueprint_id": "bp-PM-100",
                    "blueprint_path": "runtime/blueprints/bp-PM-100.json",
                    "runtime_blueprint_path": "runtime/blueprints/bp-PM-100.json",
                    "route": "chief_blueprint_required",
                    "blueprint_required": True,
                },
                "metadata": {"route": "chief_blueprint_required"},
            },
        )
    )

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
        patch("polaris.delivery.http.v2.director.get_task_market_service", return_value=mock_task_market),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks?source=auto")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "PM-100"
    assert data[0]["subject"] == "Implement combat loop"
    assert data[0]["status"] == "PENDING"
    assert data[0]["blueprint_id"] == "bp-PM-100"
    assert data[0]["metadata"]["route"] == "chief_blueprint_required"
    mock_director.list_tasks.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_list_tasks_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """Director task projection should use workspace_path before stale workspace."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.config.workspace = "C:/Temp/Product"

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks")

    assert response.status_code == 200
    mock_build.assert_awaited_once()
    build_call = mock_build.await_args
    assert build_call is not None
    assert build_call.args[0] == "C:/Temp/Product"


@pytest.mark.asyncio
async def test_director_list_tasks_accepts_workspace_override(
    client: AsyncClient,
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """Director task projection should honor the requested workspace query."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
    mock_settings.workspace_path = "C:/Temp/Stale"
    mock_director = MagicMock()
    mock_director.list_tasks = AsyncMock(return_value=[])
    mock_director.config.workspace = "C:/Temp/Stale"

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks?source=workflow&workspace=C%3A%2FTemp%2FVerified")

    assert response.status_code == 200
    mock_build.assert_awaited_once()
    build_call = mock_build.await_args
    assert build_call is not None
    assert build_call.args[0] == "C:/Temp/Verified"


def test_director_debug_append_ignores_debug_log_failure() -> None:
    """Optional Director debug evidence should not leak filesystem failures."""
    with (
        patch.dict("os.environ", {"KERNELONE_BACKEND_DEBUG_LOG": "C:/Temp/director-debug.jsonl"}),
        patch(
            "polaris.delivery.http.v2.director.Path.open",
            side_effect=OSError("debug log locked"),
        ),
        patch("polaris.delivery.http.v2.director.logger.debug") as mock_debug,
    ):
        from polaris.delivery.http.v2.director import _append_debug

        _append_debug("test.event", {"ok": True})
        mock_debug.assert_called_once()


def test_director_debug_append_is_disabled_without_explicit_log_path() -> None:
    """High-frequency Director read routes must not write debug logs by default."""
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("polaris.delivery.http.v2.director.Path.open") as mock_open,
    ):
        from polaris.delivery.http.v2.director import _append_debug

        _append_debug("test.event", {"ok": True})

    mock_open.assert_not_called()


@pytest.mark.asyncio
async def test_director_list_tasks_with_status_filter(client: AsyncClient) -> None:
    """Director list tasks should filter by status via projection."""
    mock_director = MagicMock()
    mock_director.config.workspace = "."

    with (
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[
                {
                    "id": "t1",
                    "subject": "Task 1",
                    "status": "PENDING",
                    "priority": "HIGH",
                    "claimed_by": None,
                    "blueprint_id": "bp-1",
                    "runtime_blueprint_path": "runtime/contracts/bp-1.json",
                    "metadata": {"pm_task_id": "PM-1"},
                },
            ],
        ),
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_director)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks?status=PENDING")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "PENDING"
        assert data[0]["blueprint_id"] == "bp-1"
        assert data[0]["runtime_blueprint_path"] == "runtime/contracts/bp-1.json"
        assert data[0]["metadata"]["pm_task_id"] == "PM-1"
        assert data[0]["metadata"]["projection_source"] == "runtime_projection"


def test_runtime_backed_task_rows_expose_projection_source_from_runtime_lineage(
    tmp_path: Path,
) -> None:
    """Runtime-backed Director task rows should preserve source provenance for E2E audits."""
    from polaris.delivery.http.v2.director import _runtime_backed_task_rows

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    task_runtime = TaskRuntimeService(str(workspace))
    task = task_runtime.create_task_row(
        subject="Runtime backed task",
        description="Runtime lineage should be visible through the HTTP task projection.",
        metadata={
            "pm_task_id": "PM-runtime-source",
            "materialized_by": "runtime.task_runtime",
        },
    )

    rows = _runtime_backed_task_rows(
        [
            {
                "id": str(task["id"]),
                "subject": "Runtime backed task",
                "status": "RUNNING",
                "metadata": {"pm_task_id": "PM-runtime-source"},
            }
        ],
        workspace=str(workspace),
    )

    assert rows[0]["metadata"]["pm_task_id"] == "PM-runtime-source"
    assert rows[0]["metadata"]["projection_source"] == "runtime.task_runtime"


@pytest.mark.asyncio
async def test_director_get_task_found(client: AsyncClient) -> None:
    """Director get task should return task when found."""
    from polaris.domain.entities import TaskPriority, TaskStatus

    mock_task = MagicMock()
    mock_task.id = "task-123"
    mock_task.subject = "Found task"
    mock_task.description = "Desc"
    mock_task.status = TaskStatus.IN_PROGRESS
    mock_task.priority = TaskPriority.LOW
    mock_task.claimed_by = "worker-1"
    mock_task.result = None
    mock_task.metadata = {}

    mock_director = MagicMock()
    mock_director.get_task = AsyncMock(return_value=mock_task)
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

        response = await client.get("/v2/director/tasks/task-123")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "task-123"
        assert data["claimed_by"] == "worker-1"


@pytest.mark.asyncio
async def test_director_get_task_falls_back_to_projection(client: AsyncClient) -> None:
    """Director task detail should resolve workflow/projection rows after a local miss."""
    mock_director = MagicMock()
    mock_director.get_task = AsyncMock(return_value=None)
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
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[
                {
                    "id": "projection-row-1",
                    "subject": "Workflow projected task",
                    "description": "Visible from workflow projection",
                    "status": "RUNNING",
                    "priority": "HIGH",
                    "claimed_by": "worker-projection",
                    "goal": "Keep detail panel in sync with listed tasks",
                    "acceptance": ["projection detail returned"],
                    "metadata": {
                        "pm_task_id": "PM-42",
                        "blueprint_id": "BP-42",
                    },
                },
            ],
        ),
    ):

        async def _resolve_projection_fallback(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_projection_fallback)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks/PM-42")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "projection-row-1"
    assert data["subject"] == "Workflow projected task"
    assert data["status"] == "RUNNING"
    assert data["worker"] == "worker-projection"
    assert data["pm_task_id"] == "PM-42"
    assert data["blueprint_id"] == "BP-42"
    assert data["acceptance"] == ["projection detail returned"]


@pytest.mark.asyncio
async def test_director_get_task_projection_fallback_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """Director task detail projection fallback should use the active workspace."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_director = MagicMock()
    mock_director.get_task = AsyncMock(return_value=None)
    mock_director.config.workspace = "C:/Temp/Product"

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.director.RuntimeProjectionService.build_async",
            new_callable=AsyncMock,
        ) as mock_build,
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
    ):

        async def _resolve_projection_fallback(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_projection_fallback)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks/PM-404")

    assert response.status_code == 404
    mock_build.assert_awaited_once()
    build_call = mock_build.await_args
    assert build_call is not None
    assert build_call.args[0] == "C:/Temp/Product"


@pytest.mark.asyncio
async def test_director_get_task_projection_fallback_accepts_workspace_override(
    client: AsyncClient,
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """Director task detail fallback should honor the requested workspace query."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
    mock_settings.workspace_path = "C:/Temp/Stale"
    mock_director = MagicMock()
    mock_director.get_task = AsyncMock(return_value=None)
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
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
    ):

        async def _resolve_projection_fallback(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_projection_fallback)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks/PM-404?workspace=C%3A%2FTemp%2FVerified")

    assert response.status_code == 404
    mock_build.assert_awaited_once()
    build_call = mock_build.await_args
    assert build_call is not None
    assert build_call.args[0] == "C:/Temp/Verified"


@pytest.mark.asyncio
async def test_director_get_task_not_found(client: AsyncClient) -> None:
    """Director get task should 404 when task doesn't exist."""
    mock_director = MagicMock()
    mock_director.get_task = AsyncMock(return_value=None)
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
        patch(
            "polaris.delivery.http.v2.director.select_task_rows_from_projection",
            return_value=[],
        ),
    ):

        async def _resolve_get_task(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_get_task)
        mock_projection = MagicMock()
        mock_projection.workflow_archive = None
        mock_build.return_value = mock_projection

        response = await client.get("/v2/director/tasks/nonexistent")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_director_cancel_task_success(client: AsyncClient) -> None:
    """Retired task cancellation must refuse without cancelling a task.

    OLD GOAL (retired): POST /v2/director/tasks/{task_id}/cancel cancelled the
    task through the legacy Director service and answered ``200 {"ok": true}``.
    CURRENT MEANING: run cancellation belongs to ``EngineeringRunService``; the
    legacy task cancel route is retired and refuses with
    ``410 legacy_engine_retired`` before any task is cancelled.
    """
    mock_director = MagicMock()
    mock_director.cancel_task = AsyncMock(return_value=True)
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve_cancel(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_cancel)

        response = await client.post("/v2/director/tasks/task-123/cancel")

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error_code"] == "legacy_engine_retired"
    assert "/v2/engineering/runs" in detail["supported_endpoints"]
    mock_container.return_value.resolve_async.assert_not_awaited()
    mock_director.cancel_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_cancel_task_returns_requested_workspace_evidence(
    client: AsyncClient,
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """A workspace-scoped task cancel on the retired route is refused, not echoed.

    OLD GOAL (retired): the requested workspace was echoed back as evidence of the
    workspace the cancel had used.
    CURRENT MEANING: the retired route refuses the workspace-scoped variant with
    ``410 legacy_engine_retired``; no workspace-scoped cancel happens and no
    workspace evidence is produced.
    """
    mock_settings.workspace = Path("C:/Repo/Polaris")
    mock_settings.workspace_path = "C:/Temp/Stale"
    mock_director = MagicMock()
    mock_director.cancel_task = AsyncMock(return_value=True)
    mock_director.config.workspace = "C:/Temp/Stale"

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve_cancel(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_cancel)

        response = await client.post("/v2/director/tasks/task-123/cancel?workspace=C%3A%2FTemp%2FVerified")

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error_code"] == "legacy_engine_retired"
    assert "workspace" not in detail
    mock_container.return_value.resolve_async.assert_not_awaited()
    mock_director.cancel_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_cancel_task_returns_service_success_payload(client: AsyncClient) -> None:
    """A successful legacy cancel payload is never produced on the retired route.

    OLD GOAL (retired): a successful legacy cancel payload (``ok``, ``status``,
    ``worker``) was forwarded to the caller.
    CURRENT MEANING: the legacy cancel is never attempted, so no service payload
    exists to forward; the route refuses with ``410 legacy_engine_retired``.
    """
    mock_director = MagicMock()
    mock_director.cancel_task = AsyncMock(
        return_value={
            "ok": True,
            "task_id": "task-123",
            "status": "CANCELLED",
            "worker": "worker-1",
        }
    )
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve_cancel_payload(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_cancel_payload)

        response = await client.post("/v2/director/tasks/task-123/cancel")

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error_code"] == "legacy_engine_retired"
    assert "worker" not in str(detail)
    mock_container.return_value.resolve_async.assert_not_awaited()
    mock_director.cancel_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_cancel_task_fails(client: AsyncClient) -> None:
    """A failing legacy cancel is never attempted on the retired route.

    OLD GOAL (retired): a failed cancellation produced ``400``.
    CURRENT MEANING: the legacy cancel is never attempted, so there is no failure
    to map; the route refuses with ``410 legacy_engine_retired``.
    """
    mock_director = MagicMock()
    mock_director.cancel_task = AsyncMock(return_value=False)
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

        response = await client.post("/v2/director/tasks/task-123/cancel")

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_container.return_value.resolve_async.assert_not_awaited()
    mock_director.cancel_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_cancel_task_respects_service_failure_payload(client: AsyncClient) -> None:
    """A legacy failure payload is never produced on the retired route.

    OLD GOAL (retired): a non-empty ``ok=false`` legacy payload was mapped to
    ``400`` with the service error message.
    CURRENT MEANING: the legacy cancel is never attempted, so no failure payload
    exists; the route refuses with ``410 legacy_engine_retired``.
    """
    mock_director = MagicMock()
    mock_director.cancel_task = AsyncMock(
        return_value={
            "ok": False,
            "error": "Task not found or not cancellable",
            "task_id": "task-123",
        }
    )
    mock_director.config.workspace = "."

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:

        async def _resolve_cancel_failure_payload(iface: type) -> object:
            if iface.__name__ == "DirectorService":
                return mock_director
            return MagicMock()

        mock_container.return_value.resolve_async = AsyncMock(side_effect=_resolve_cancel_failure_payload)

        response = await client.post("/v2/director/tasks/task-123/cancel")

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error_code"] == "legacy_engine_retired"
    assert "Task not found or not cancellable" not in str(detail)
    mock_container.return_value.resolve_async.assert_not_awaited()
    mock_director.cancel_task.assert_not_awaited()
