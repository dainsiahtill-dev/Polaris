"""Director orchestration + integration-QA router tests (run/get orchestration).

Split from the historical test_v2_director_router.py Orchestration and
Integration QA sections."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from polaris.kernelone.storage.io_paths import build_cache_root
from polaris.tests.unit.delivery.http.routers._helpers import (
    _director_run_diagnostics,
)
from polaris.tests.unit.delivery.http.routers.conftest import _DirectorRouterTestSettings


def _engineering_run_rows(workspace: Path) -> dict[str, int]:
    """Count the engineering owner's own run/event/tool rows for one workspace.

    The engineering owner is the only current write authority for new work, so
    a retired legacy write must leave these counters unchanged.
    """
    database = workspace / ".polaris" / "runtime" / "engineering" / "sessions.sqlite3"
    if not database.is_file():
        return {"runs": 0, "events": 0, "tool_calls": 0}
    with sqlite3.connect(database) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("runs", "events", "tool_calls")
        }


@pytest.mark.asyncio
async def test_director_run_orchestration(
    client: AsyncClient,
    director_router_private_workspace: Path,
) -> None:
    """Retired Director orchestration run must refuse without dispatching a run.

    OLD GOAL (retired): POST /v2/director/run preflighted readiness, queued the
    ready PM/blueprint tasks and answered ``200`` with a ``run_id``.
    CURRENT MEANING: orchestration execution belongs to
    ``EngineeringRunService`` behind ``/v2/engineering/runs``; the legacy route is
    retired and refuses with ``410 legacy_engine_retired`` before any readiness
    preflight, dispatch service, or run record exists.
    """
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

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error_code"] == "legacy_engine_retired"
    assert "/v2/engineering/runs" in detail["supported_endpoints"]
    # No readiness preflight, no dispatch service, no legacy run record.
    mock_preflight.assert_not_awaited()
    mock_service_cls.assert_not_called()
    mock_service.execute_director_run.assert_not_awaited()
    assert _engineering_run_rows(director_router_private_workspace) == {
        "runs": 0,
        "events": 0,
        "tool_calls": 0,
    }


@pytest.mark.asyncio
async def test_director_run_orchestration_defaults_to_active_workspace_path(
    client: AsyncClient,
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """A retired orchestration run never resolves the active desktop workspace.

    OLD GOAL (retired): an omitted workspace was resolved through the active
    desktop settings and forwarded to the dispatch service.
    CURRENT MEANING: the legacy route is retired, so no workspace resolution,
    readiness preflight, or dispatch happens; the request variant is refused
    uniformly with ``410 legacy_engine_retired``.
    """
    mock_settings.workspace = Path("C:/Repo/Polaris")
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

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_preflight.assert_not_awaited()
    mock_service_cls.assert_not_called()
    mock_service.execute_director_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_run_orchestration_preserves_explicit_workspace(
    client: AsyncClient,
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """An explicit workspace on the retired route is refused, never honoured.

    OLD GOAL (retired): an explicit non-dot workspace was preserved and forwarded
    to the dispatch service as the execution workspace.
    CURRENT MEANING: the route is retired; the explicit-workspace request variant
    is refused with ``410 legacy_engine_retired`` and no workspace-scoped effect
    is produced.
    """
    mock_settings.workspace = Path("C:/Repo/Polaris")
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

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_preflight.assert_not_awaited()
    mock_service_cls.assert_not_called()
    mock_service.execute_director_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_run_orchestration_serial_mode(client: AsyncClient) -> None:
    """The serial-mode request variant on the retired route is refused too.

    OLD GOAL (retired): ``execution_mode="serial"`` with a task filter produced a
    serial orchestration run.
    CURRENT MEANING: execution mode selection belongs to the current engineering
    owner; the retired route refuses every request variant with the same
    ``410 legacy_engine_retired`` contract and starts nothing.
    """
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

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_preflight.assert_not_awaited()
    mock_service_cls.assert_not_called()
    mock_service.execute_director_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_run_orchestration_accepts_task_id(client: AsyncClient) -> None:
    """A selected-task request variant on the retired route is refused, not dispatched.

    OLD GOAL (retired): a ``task_id`` was forwarded into the dispatch options and
    narrowed the run to that task.
    CURRENT MEANING: the retired route refuses the variant uniformly; task
    selection is a current-entry concern of the engineering owner.
    """
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

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_preflight.assert_not_awaited()
    mock_service_cls.assert_not_called()
    mock_service.execute_director_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_run_orchestration_uses_diagnostics_ready_tasks_when_no_task_selected(
    client: AsyncClient,
) -> None:
    """Ready-task selection never happens on the retired orchestration route.

    OLD GOAL (retired): without an explicit task the route read the readiness
    diagnostics and executed the ready workflow tasks.
    CURRENT MEANING: the route is retired, so the readiness diagnostics are not
    even built and no ready-task selection or dispatch occurs.
    """
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

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_preflight.assert_not_awaited()
    mock_service_cls.assert_not_called()
    mock_service.execute_director_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_run_orchestration_merges_direct_and_blueprint_ready_tasks(
    client: AsyncClient,
) -> None:
    """Mixed direct/blueprint ready-task selection never happens on the retired route.

    OLD GOAL (retired): ready direct PM tasks and CE blueprint tasks were merged
    into one dispatched task set.
    CURRENT MEANING: the retired route refuses the request; no diagnostics are
    built and no task set is computed or dispatched.
    """
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

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_preflight.assert_not_awaited()
    mock_service_cls.assert_not_called()
    mock_service.execute_director_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_run_orchestration_blocks_when_diagnostics_cannot_execute(
    client: AsyncClient,
) -> None:
    """The retired route refuses before readiness blockers could be reported.

    OLD GOAL (retired): unexecutable readiness diagnostics produced ``409
    DIRECTOR_EXECUTION_BLOCKED`` with the blocker list.
    CURRENT MEANING: the route is retired, so diagnostics are never built; the
    refusal is the uniform ``410 legacy_engine_retired`` contract.  The legacy
    dispatch service is still not constructed.
    """
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

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error_code"] == "legacy_engine_retired"
    assert "DIRECTOR_EXECUTION_BLOCKED" not in detail.get("message", "")
    mock_preflight.assert_not_awaited()
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
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """Director run evidence should not leak runs from a different desktop workspace."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
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
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """Director run detail should return 404 when a run belongs to another workspace."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
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
async def test_director_cancel_orchestration_run(
    client: AsyncClient,
    director_router_private_workspace: Path,
) -> None:
    """The retired cancel route refuses without querying or cancelling a run.

    OLD GOAL (retired): POST /v2/director/runs/{run_id}/cancel queried the run and
    cancelled it through the legacy orchestration runtime.
    CURRENT MEANING: run cancellation belongs to ``EngineeringRunService``
    (``POST /v2/engineering/runs/{session_id}/cancel``); the legacy route is
    retired and refuses with ``410 legacy_engine_retired`` before any run is read
    or mutated.
    """
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

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error_code"] == "legacy_engine_retired"
    assert "/v2/engineering/runs" in detail["supported_endpoints"]
    mock_orch.assert_not_awaited()
    mock_service.query_run.assert_not_awaited()
    mock_service.cancel_run.assert_not_awaited()
    assert _engineering_run_rows(director_router_private_workspace) == {
        "runs": 0,
        "events": 0,
        "tool_calls": 0,
    }


@pytest.mark.asyncio
async def test_director_cancel_orchestration_hides_workspace_mismatch(
    client: AsyncClient,
    mock_settings: _DirectorRouterTestSettings,
) -> None:
    """A foreign-workspace cancel request on the retired route is refused uniformly.

    OLD GOAL (retired): a run belonging to another desktop workspace produced
    ``404`` so the run stayed hidden and uncancelled.
    CURRENT MEANING: the route is retired; the workspace-scoped request variant
    is refused with ``410 legacy_engine_retired`` and no run is read or cancelled.
    """
    mock_settings.workspace = Path("C:/Repo/Polaris")
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

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_orch.assert_not_awaited()
    mock_service.query_run.assert_not_awaited()
    mock_service.cancel_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_cancel_orchestration_terminal_run_is_idempotent(client: AsyncClient) -> None:
    """Terminal-run cancel idempotence is not a retired-route concern.

    OLD GOAL (retired): cancelling a terminal run returned the unchanged terminal
    snapshot and never called the cancel path.
    CURRENT MEANING: the route is retired; the cancel request is refused with
    ``410 legacy_engine_retired`` and no run is queried or cancelled.
    """
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

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_orch.assert_not_awaited()
    mock_service.query_run.assert_not_awaited()
    mock_service.cancel_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_cancel_orchestration_string_status_is_idempotent(client: AsyncClient) -> None:
    """String-status terminal cancel is refused on the retired route.

    OLD GOAL (retired): a snapshot with a plain string status was returned
    unchanged and never cancelled.
    CURRENT MEANING: the route is retired; the request is refused with
    ``410 legacy_engine_retired`` and no snapshot is read.
    """
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

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_orch.assert_not_awaited()
    mock_service.query_run.assert_not_awaited()
    mock_service.cancel_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_cancel_orchestration_not_found(client: AsyncClient) -> None:
    """An unknown run id on the retired cancel route is refused, not looked up.

    OLD GOAL (retired): an unknown run id produced ``404 Run not found``.
    CURRENT MEANING: the route is retired; the refusal is uniform
    ``410 legacy_engine_retired`` and no run lookup happens.
    """
    with patch(
        "polaris.delivery.http.v2.director.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=None)
        mock_orch.return_value = mock_service

        response = await client.post("/v2/director/runs/nonexistent/cancel")

    assert response.status_code == 410
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    mock_orch.assert_not_awaited()
    mock_service.query_run.assert_not_awaited()


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
    mock_settings: _DirectorRouterTestSettings,
    tmp_path: Path,
) -> None:
    """The retired API-triggered integration QA writes nothing to the runtime root.

    OLD GOAL (retired): POST /v2/director/integration-qa persisted an
    ``integration_qa.result.json`` into the active runtime root and echoed both
    result paths.
    CURRENT MEANING: verification is owned by the engineering run's own
    verification contract; the legacy QA trigger is retired, refuses with
    ``410 legacy_engine_retired`` and persists no QA artifact at all.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_base = tmp_path / "runtime-base"
    mock_settings.workspace = workspace
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
        ) as mock_persist_director_result,
        patch(
            "polaris.cells.orchestration.workflow_runtime.public.service.build_integration_qa_tasks_from_director_result",
            return_value=task_rows,
        ) as mock_build_qa_tasks,
        patch(
            "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._resolve_verify_runner",
            return_value=lambda _workspace: (True, "Integration verification passed", []),
        ) as mock_verify_runner,
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

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["error_code"] == "legacy_engine_retired"
    assert "/v2/engineering/runs" in detail["supported_endpoints"]
    # No legacy QA pipeline runs and no QA artifact is persisted.
    mock_persist_director_result.assert_not_called()
    mock_build_qa_tasks.assert_not_called()
    mock_verify_runner.assert_not_called()
    assert not (Path(cache_root) / "runs" / "api-qa-1" / "qa").exists()
    assert not (Path(cache_root) / "results" / "integration_qa.result.json").exists()
    assert not (Path(cache_root) / "runs").exists()
