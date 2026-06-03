"""Tests for Polaris v2 Chief Engineer router."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.chief_engineer.blueprint.public.contracts import TaskBlueprintResultV1
from polaris.delivery.http.routers._shared import StructuredHTTPException


@pytest.fixture
def mock_settings() -> Settings:
    """Create a minimal Settings instance for testing."""
    from polaris.bootstrap.config import ServerConfig, Settings
    from polaris.config.nats_config import NATSConfig

    settings = MagicMock(spec=Settings)
    settings.workspace = Path(".")
    settings.ramdisk_root = ""
    settings.nats = NATSConfig(enabled=False, required=False, url="")
    settings.server = ServerConfig(cors_origins=["*"])
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.logging = MagicMock()
    settings.logging.enable_debug_tracing = False
    return settings


@pytest.fixture
async def client(mock_settings: Settings) -> AsyncIterator[AsyncClient]:
    """Create an async test client with mocked lifespan."""
    from polaris.cells.runtime.state_owner.public.service import AppState
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)
    app.state.app_state = AppState(settings=mock_settings)

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch("polaris.bootstrap.assembly.assemble_core_services"),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch("polaris.delivery.http.app_factory.sync_process_settings_environment"),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch.dict("os.environ", {"KERNELONE_METRICS_ENABLED": "false"}),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


@pytest.fixture(autouse=True)
def chief_engineer_llm_ready() -> Iterator[MagicMock]:
    """Keep CE router tests focused unless a test overrides LLM readiness."""

    payload = {
        "state": "READY",
        "required_ready_roles": ["chief_engineer"],
        "blocked_roles": [],
        "unsupported_roles": [],
        "roles": {
            "chief_engineer": {
                "ready": True,
                "runtime_supported": True,
                "provider_id": "qwen",
                "model": "Qwen3-Max",
            }
        },
    }
    with (
        patch("polaris.delivery.http.v2.chief_engineer.build_llm_status", return_value=payload),
        patch("polaris.delivery.http.v2.chief_engineer.ensure_required_roles_ready") as ready_gate,
    ):
        yield ready_gate


def _ready_blueprint(blueprint_id: str, task_id: str) -> dict[str, Any]:
    return {
        "blueprint_id": blueprint_id,
        "task_id": task_id,
        "target_files": [f"src/{task_id.lower()}.ts"],
        "acceptance_criteria": [f"{task_id} acceptance is implemented"],
        "execution_checklist": [f"Implement {task_id}", f"Verify {task_id}"],
        "contract_completeness": {
            "handoff_ready": True,
            "missing_fields": [],
            "requires": ["target_files", "acceptance_criteria", "execution_checklist"],
        },
        "handoff_ready": True,
    }


@pytest.mark.asyncio
async def test_get_chief_engineer_diagnostics_reports_blueprint_store_health(client: AsyncClient) -> None:
    """Chief Engineer diagnostics should summarize blueprint store readiness without writes."""
    persistence = MagicMock()
    persistence.list_all.return_value = ["bp-invalid", "bp-ready"]

    def load_payload(blueprint_id: str) -> dict[str, object] | None:
        if blueprint_id == "bp-ready":
            return {
                "blueprint_id": "bp-ready",
                "summary": "Ready Director handoff",
                "updated_at": "2026-05-23T08:00:00Z",
            }
        return None

    persistence.load.side_effect = load_payload

    with patch(
        "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
        return_value=persistence,
    ) as persistence_cls:
        response = await client.get("/v2/chief-engineer/diagnostics")

    assert response.status_code == 200
    persistence_cls.assert_called_once_with(".", ensure_directory=False)
    data = response.json()
    assert data["role"] == "chief_engineer"
    assert data["ok"] is False
    assert data["can_generate"] is True
    assert data["generate_blockers"] == []
    assert data["llm"]["ok"] is True
    assert data["llm"]["provider_id"] == "qwen"
    assert data["llm"]["model"] == "Qwen3-Max"
    assert data["workspace"]["ok"] is True
    assert data["blueprints"]["status"] == "degraded"
    assert data["blueprints"]["plan_status"] == "missing"
    assert data["blueprints"]["total"] == 2
    assert data["blueprints"]["loadable"] == 1
    assert data["blueprints"]["invalid_payloads"] == 1
    assert data["blueprints"]["director_handoff_ready"] is False
    assert data["blueprints"]["latest_updated_at"] == "2026-05-23T08:00:00Z"
    assert data["issues"] == ["blueprint_task_plan_unavailable", "blueprint_payload_invalid"]
    assert data["can_handoff"] is False
    assert data["handoff_blockers"] == ["blueprint_task_plan_unavailable", "blueprint_payload_invalid"]


@pytest.mark.asyncio
async def test_get_chief_engineer_diagnostics_accepts_workspace_query_override(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Chief Engineer diagnostics should inspect blueprint and LLM state for the requested workspace."""
    active_workspace = tmp_path / "active"
    requested_workspace = tmp_path / "requested"
    active_workspace.mkdir()
    requested_workspace.mkdir()
    mock_settings.workspace = str(active_workspace)
    mock_settings.workspace_path = ""

    persistence = MagicMock()
    persistence.list_all.return_value = []

    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
            return_value=persistence,
        ) as persistence_cls,
        patch(
            "polaris.delivery.http.v2.chief_engineer.build_llm_status",
            return_value={
                "state": "READY",
                "required_ready_roles": ["chief_engineer"],
                "blocked_roles": [],
                "unsupported_roles": [],
                "roles": {
                    "chief_engineer": {
                        "ready": True,
                        "runtime_supported": True,
                        "provider_id": "qwen",
                        "model": "Qwen3-Max",
                    }
                },
            },
        ) as mock_llm_status,
    ):
        response = await client.get(
            "/v2/chief-engineer/diagnostics",
            params={"workspace": str(requested_workspace)},
        )

    assert response.status_code == 200
    data = response.json()
    assert Path(data["workspace"]["workspace"]).resolve() == requested_workspace.resolve()
    persistence_cls.assert_called_once_with(str(requested_workspace), ensure_directory=False)
    called_settings = mock_llm_status.call_args.args[0]
    assert Path(str(called_settings.workspace)).resolve() == requested_workspace.resolve()
    assert Path(str(mock_settings.workspace)).resolve() == active_workspace.resolve()


@pytest.mark.asyncio
async def test_get_chief_engineer_diagnostics_blocks_generation_when_llm_not_ready(
    client: AsyncClient,
) -> None:
    """Chief Engineer diagnostics should expose role-specific LLM generation blockers."""

    llm_payload = {
        "state": "BLOCKED",
        "required_ready_roles": ["pm", "director"],
        "blocked_roles": [],
        "unsupported_roles": [],
        "roles": {
            "chief_engineer": {
                "ready": False,
                "runtime_supported": True,
                "provider_id": "qwen",
                "model": "Qwen3-Max",
            }
        },
    }
    persistence = MagicMock()
    persistence.list_all.return_value = ["bp-ready"]
    persistence.load.return_value = {"blueprint_id": "bp-ready", "task_id": "PM-1"}

    with (
        patch("polaris.delivery.http.v2.chief_engineer.build_llm_status", return_value=llm_payload),
        patch("polaris.delivery.http.v2.chief_engineer.BlueprintPersistence", return_value=persistence),
    ):
        response = await client.get("/v2/chief-engineer/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["can_generate"] is False
    assert data["generate_blockers"] == ["llm_not_ready"]
    assert data["llm"]["ok"] is False
    assert data["llm"]["state"] == "blocked"
    assert data["llm"]["blocked_roles"] == ["chief_engineer"]
    assert "llm_not_ready" in data["issues"]


@pytest.mark.asyncio
async def test_get_chief_engineer_diagnostics_reports_store_error(client: AsyncClient) -> None:
    """Chief Engineer diagnostics should degrade when the blueprint store cannot be inspected."""
    with patch(
        "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
        side_effect=RuntimeError("store offline"),
    ):
        response = await client.get("/v2/chief-engineer/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["blueprints"]["status"] == "error"
    assert data["blueprints"]["error"] == "RuntimeError: store offline"
    assert data["issues"] == ["blueprint_store_unreadable"]
    assert data["can_handoff"] is False
    assert data["handoff_blockers"] == ["blueprint_store_unreadable"]


@pytest.mark.asyncio
async def test_get_chief_engineer_diagnostics_reports_no_handoff_when_blueprints_empty(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Empty blueprint evidence should not be reported as a ready CE handoff state."""
    mock_settings.workspace = tmp_path
    mock_settings.workspace_path = str(tmp_path)
    persistence = MagicMock()
    persistence.list_all.return_value = []

    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
            return_value=persistence,
        ),
        patch(
            "polaris.delivery.http.v2.chief_engineer.resolve_logical_path",
            return_value=str(tmp_path / "runtime" / "tasks" / "plan.json"),
        ),
    ):
        response = await client.get("/v2/chief-engineer/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["can_handoff"] is False
    assert data["blueprints"]["status"] == "empty"
    assert data["blueprints"]["plan_status"] == "missing"
    assert data["blueprints"]["director_handoff_ready"] is False
    assert data["issues"] == ["blueprint_task_plan_unavailable"]
    assert data["handoff_blockers"] == ["blueprint_task_plan_unavailable"]


@pytest.mark.asyncio
async def test_get_chief_engineer_diagnostics_blocks_stale_blueprint_without_pm_plan(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """A loadable blueprint is not handoff-ready without an auditable PM task plan."""
    mock_settings.workspace = tmp_path
    mock_settings.workspace_path = str(tmp_path)
    persistence = MagicMock()
    persistence.list_all.return_value = ["bp-stale"]
    persistence.load.return_value = {
        "blueprint_id": "bp-stale",
        "task_id": "PM-stale",
        "summary": "Stale blueprint without PM plan",
    }

    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
            return_value=persistence,
        ),
        patch(
            "polaris.delivery.http.v2.chief_engineer.resolve_logical_path",
            return_value=str(tmp_path / "runtime" / "tasks" / "plan.json"),
        ),
    ):
        response = await client.get("/v2/chief-engineer/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["blueprints"]["status"] == "degraded"
    assert data["blueprints"]["plan_status"] == "missing"
    assert data["blueprints"]["loadable"] == 1
    assert data["blueprints"]["invalid_payloads"] == 1
    assert data["blueprints"]["director_handoff_ready"] is False
    assert data["can_handoff"] is False
    assert data["issues"] == ["blueprint_task_plan_unavailable", "blueprint_payload_invalid"]
    assert data["handoff_blockers"] == ["blueprint_task_plan_unavailable", "blueprint_payload_invalid"]


@pytest.mark.asyncio
async def test_get_chief_engineer_diagnostics_uses_pm_contract_when_task_plan_missing(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Current PM workflow contracts should be valid CE handoff plan evidence."""
    mock_settings.workspace = tmp_path
    mock_settings.workspace_path = str(tmp_path)
    task_plan_path = tmp_path / "runtime" / "tasks" / "plan.json"
    contract_path = tmp_path / "runtime" / "contracts" / "pm_tasks.contract.json"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text(
        '{"tasks": [{"id": "PM-1"}, {"id": "PM-2"}]}',
        encoding="utf-8",
    )
    persistence = MagicMock()
    persistence.list_all.return_value = ["bp-1", "bp-2"]
    persistence.load.side_effect = lambda blueprint_id: {
        "bp-1": _ready_blueprint("bp-1", "PM-1"),
        "bp-2": _ready_blueprint("bp-2", "PM-2"),
    }[blueprint_id]

    def resolve_candidate(_workspace: str, logical_path: str, ramdisk_root: str | None = None) -> str:
        _ = ramdisk_root
        if logical_path == "runtime/contracts/pm_tasks.contract.json":
            return str(contract_path)
        return str(task_plan_path)

    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
            return_value=persistence,
        ),
        patch(
            "polaris.delivery.http.v2.chief_engineer.resolve_logical_path",
            side_effect=resolve_candidate,
        ),
    ):
        response = await client.get("/v2/chief-engineer/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["blueprints"]["plan_status"] == "ready"
    assert Path(data["blueprints"]["plan_path"]).resolve() == contract_path.resolve()
    assert data["blueprints"]["planned_tasks"] == 2
    assert data["blueprints"]["covered_tasks"] == 2
    assert data["blueprints"]["director_handoff_ready"] is True
    assert data["issues"] == []


@pytest.mark.asyncio
async def test_get_chief_engineer_diagnostics_reports_complete_plan_coverage(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Chief Engineer handoff should be ready only when the PM task plan is fully covered."""
    mock_settings.workspace = tmp_path
    mock_settings.workspace_path = str(tmp_path)
    plan_path = tmp_path / "runtime" / "tasks" / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        '{"tasks": [{"id": "PM-1"}, {"id": "PM-2"}]}',
        encoding="utf-8",
    )
    persistence = MagicMock()
    persistence.list_all.return_value = ["bp-1", "bp-2"]
    persistence.load.side_effect = lambda blueprint_id: {
        "bp-1": _ready_blueprint("bp-1", "PM-1"),
        "bp-2": _ready_blueprint("bp-2", "PM-2"),
    }[blueprint_id]

    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
            return_value=persistence,
        ),
        patch(
            "polaris.delivery.http.v2.chief_engineer.resolve_logical_path",
            return_value=str(plan_path),
        ),
    ):
        response = await client.get("/v2/chief-engineer/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["blueprints"]["ok"] is True
    assert data["blueprints"]["plan_status"] == "ready"
    assert Path(data["blueprints"]["plan_path"]).resolve() == plan_path.resolve()
    assert data["blueprints"]["planned_tasks"] == 2
    assert data["blueprints"]["covered_tasks"] == 2
    assert data["blueprints"]["missing_task_ids"] == []
    assert data["blueprints"]["director_handoff_ready"] is True
    assert data["issues"] == []
    assert data["can_handoff"] is True
    assert data["handoff_blockers"] == []


@pytest.mark.asyncio
async def test_get_chief_engineer_diagnostics_allows_ready_coverage_with_stale_invalid_duplicates(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """A stale hollow blueprint must not block handoff when a current ready blueprint covers the task."""
    mock_settings.workspace = tmp_path
    mock_settings.workspace_path = str(tmp_path)
    plan_path = tmp_path / "runtime" / "tasks" / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text('{"tasks": [{"id": "PM-1"}]}', encoding="utf-8")
    persistence = MagicMock()
    persistence.list_all.return_value = ["ce-old-hollow", "ce-new-ready"]
    persistence.load.side_effect = lambda blueprint_id: {
        "ce-old-hollow": {"blueprint_id": "ce-old-hollow", "task_id": "PM-1"},
        "ce-new-ready": _ready_blueprint("ce-new-ready", "PM-1"),
    }[blueprint_id]

    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
            return_value=persistence,
        ),
        patch(
            "polaris.delivery.http.v2.chief_engineer.resolve_logical_path",
            return_value=str(plan_path),
        ),
    ):
        response = await client.get("/v2/chief-engineer/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["blueprints"]["covered_tasks"] == 1
    assert data["blueprints"]["invalid_payloads"] == 1
    assert data["blueprints"]["director_handoff_ready"] is True
    assert data["issues"] == []
    assert data["can_handoff"] is True


@pytest.mark.asyncio
async def test_get_chief_engineer_diagnostics_ignores_traceability_only_blueprints(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Traceability mirror artifacts must not satisfy Chief Engineer handoff coverage."""
    mock_settings.workspace = tmp_path
    mock_settings.workspace_path = str(tmp_path)
    plan_path = tmp_path / "runtime" / "tasks" / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text('{"tasks": [{"id": "PM-1"}]}', encoding="utf-8")
    persistence = MagicMock()
    persistence.list_all.return_value = ["bp-trace"]
    traceability_blueprint = _ready_blueprint("bp-trace", "PM-1")
    traceability_blueprint["source"] = "pm_dispatch.traceability_reference"
    traceability_blueprint["traceability_only"] = True
    persistence.load.return_value = traceability_blueprint

    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
            return_value=persistence,
        ),
        patch(
            "polaris.delivery.http.v2.chief_engineer.resolve_logical_path",
            return_value=str(plan_path),
        ),
    ):
        response = await client.get("/v2/chief-engineer/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["blueprints"]["planned_tasks"] == 1
    assert data["blueprints"]["covered_tasks"] == 0
    assert data["blueprints"]["invalid_payloads"] == 0
    assert data["blueprints"]["missing_task_ids"] == ["PM-1"]
    assert data["blueprints"]["director_handoff_ready"] is False
    assert data["issues"] == ["blueprint_coverage_incomplete"]


@pytest.mark.asyncio
async def test_get_chief_engineer_diagnostics_blocks_partial_plan_coverage(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Partial blueprint coverage must not be reported as Director handoff-ready."""
    mock_settings.workspace = tmp_path
    mock_settings.workspace_path = str(tmp_path)
    plan_path = tmp_path / "runtime" / "tasks" / "plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        '{"tasks": [{"id": "PM-covered"}, {"id": "PM-missing"}]}',
        encoding="utf-8",
    )
    persistence = MagicMock()
    persistence.list_all.return_value = ["bp-covered"]
    persistence.load.return_value = _ready_blueprint("bp-covered", "PM-covered")

    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
            return_value=persistence,
        ),
        patch(
            "polaris.delivery.http.v2.chief_engineer.resolve_logical_path",
            return_value=str(plan_path),
        ),
    ):
        response = await client.get("/v2/chief-engineer/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["blueprints"]["ok"] is False
    assert data["blueprints"]["plan_status"] == "ready"
    assert data["blueprints"]["planned_tasks"] == 2
    assert data["blueprints"]["covered_tasks"] == 1
    assert data["blueprints"]["missing_task_ids"] == ["PM-missing"]
    assert data["blueprints"]["director_handoff_ready"] is False
    assert data["issues"] == ["blueprint_coverage_incomplete"]
    assert data["can_handoff"] is False
    assert data["handoff_blockers"] == ["blueprint_coverage_incomplete"]


@pytest.mark.asyncio
async def test_get_chief_engineer_llm_events_filters_role_and_task(client: AsyncClient) -> None:
    """Chief Engineer LLM events should be available through the role route."""
    mock_event = MagicMock()
    mock_event.event_type = "llm_call_start"
    mock_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-1",
        "task_id": "PM-1",
        "role": "chief_engineer",
    }

    with patch(
        "polaris.delivery.http.v2.chief_engineer.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [mock_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/chief-engineer/llm-events?run_id=run-1&task_id=PM-1&limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["role"] == "chief_engineer"
    assert data["run_id"] == "run-1"
    assert data["task_id"] == "PM-1"
    assert data["count"] == 1
    assert data["stats"]["call_start"] == 1
    mock_emitter.get_events.assert_called_once_with(
        run_id="run-1",
        task_id="PM-1",
        role="chief_engineer",
        limit=5,
    )


@pytest.mark.asyncio
async def test_get_chief_engineer_llm_events_filters_requested_workspace(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    """Chief Engineer LLM events should be scoped to the requested workspace."""
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
        "role": "chief_engineer",
    }

    other_event = MagicMock()
    other_event.event_type = "llm_call_start"
    other_event.metadata = {"workspace": str(other_workspace)}
    other_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-other",
        "role": "chief_engineer",
    }

    with patch(
        "polaris.delivery.http.v2.chief_engineer.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [matching_event, other_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get(
            "/v2/chief-engineer/llm-events",
            params={"workspace": str(requested_workspace), "limit": "5"},
        )

    assert response.status_code == 200
    data = response.json()
    assert Path(data["workspace"]).resolve() == requested_workspace.resolve()
    assert data["count"] == 1
    assert data["events"][0]["run_id"] == "run-requested"
    assert data["stats"]["call_start"] == 1


@pytest.mark.asyncio
async def test_get_chief_engineer_cache_stats(client: AsyncClient) -> None:
    """Chief Engineer cache stats should reuse the shared roles-kernel cache."""
    with patch(
        "polaris.cells.roles.kernel.public.service.get_global_llm_cache",
    ) as mock_get_cache:
        mock_cache = MagicMock()
        mock_cache.get_stats.return_value = {"hits": 7, "misses": 3, "size": 10}
        mock_get_cache.return_value = mock_cache

        response = await client.get("/v2/chief-engineer/cache-stats")

    assert response.status_code == 200
    data = response.json()
    assert data["hits"] == 7
    assert data["misses"] == 3
    assert data["size"] == 10


@pytest.mark.asyncio
async def test_clear_chief_engineer_cache(client: AsyncClient) -> None:
    """Chief Engineer cache clear should clear the shared roles-kernel cache."""
    with patch(
        "polaris.cells.roles.kernel.public.service.get_global_llm_cache",
    ) as mock_get_cache:
        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache

        response = await client.post("/v2/chief-engineer/cache-clear")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["message"] == "Cache cleared"
    mock_cache.clear.assert_called_once()


@pytest.mark.asyncio
async def test_get_chief_engineer_token_budget_stats(client: AsyncClient) -> None:
    """Chief Engineer token-budget stats should reuse the shared roles-kernel budget."""
    with patch(
        "polaris.delivery.http.v2.chief_engineer.get_global_token_budget",
    ) as mock_get_budget:
        mock_budget = MagicMock()
        mock_budget.get_stats.return_value = {
            "total_budget": 100000,
            "used_tokens": 4096,
            "remaining": 95904,
        }
        mock_get_budget.return_value = mock_budget

        response = await client.get("/v2/chief-engineer/token-budget-stats")

    assert response.status_code == 200
    data = response.json()
    assert data["total_budget"] == 100000
    assert data["used_tokens"] == 4096
    assert data["remaining"] == 95904


@pytest.mark.asyncio
async def test_list_chief_engineer_blueprints(client: AsyncClient) -> None:
    """Chief Engineer blueprints list should expose persisted blueprint summaries."""
    persistence = MagicMock()
    persistence.list_all.return_value = ["bp-1"]
    persistence.load.return_value = {
        "blueprint_id": "bp-1",
        "title": "Director TaskBoard",
        "summary": "Build real task board",
        "target_files": ["src/frontend/src/app/components/director/DirectorTaskPanel.tsx"],
        "updated_at": "2026-05-07T07:16:25Z",
    }

    with patch(
        "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
        return_value=persistence,
    ) as persistence_cls:
        response = await client.get("/v2/chief-engineer/blueprints")

    assert response.status_code == 200
    persistence_cls.assert_called_once_with(".", ensure_directory=False)
    data = response.json()
    assert data["total"] == 1
    assert data["blueprints"][0]["blueprint_id"] == "bp-1"
    assert data["blueprints"][0]["title"] == "Director TaskBoard"
    assert data["blueprints"][0]["target_files"] == ["src/frontend/src/app/components/director/DirectorTaskPanel.tsx"]
    assert data["blueprints"][0]["source"] == "runtime/blueprints"


@pytest.mark.asyncio
async def test_list_chief_engineer_blueprints_is_read_only_for_empty_store(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Blueprint list should not create runtime blueprint directories for an idle workspace."""
    mock_settings.workspace = tmp_path
    mock_settings.workspace_path = str(tmp_path)
    blueprint_dir = tmp_path / "runtime" / "blueprints"

    response = await client.get("/v2/chief-engineer/blueprints")

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert not blueprint_dir.exists()


@pytest.mark.asyncio
async def test_list_chief_engineer_blueprints_uses_workspace_path_fallback(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Blueprint list route should use workspace_path when workspace is not populated."""
    mock_settings.workspace = ""
    mock_settings.workspace_path = "C:/Temp/Product"
    persistence = MagicMock()
    persistence.list_all.return_value = []

    with patch(
        "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
        return_value=persistence,
    ) as persistence_cls:
        response = await client.get("/v2/chief-engineer/blueprints")

    assert response.status_code == 200
    persistence_cls.assert_called_once_with("C:/Temp/Product", ensure_directory=False)
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_list_chief_engineer_blueprints_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Blueprint list route should prefer active workspace_path over stale workspace."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
    mock_settings.workspace_path = "C:/Temp/Product"
    persistence = MagicMock()
    persistence.list_all.return_value = []

    with patch(
        "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
        return_value=persistence,
    ) as persistence_cls:
        response = await client.get("/v2/chief-engineer/blueprints")

    assert response.status_code == 200
    persistence_cls.assert_called_once_with("C:/Temp/Product", ensure_directory=False)
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_generate_chief_engineer_blueprint_uses_public_command_contract(client: AsyncClient) -> None:
    """Blueprint generation route should build the CE command and expose persisted context."""
    persistence = MagicMock()
    persistence.load.return_value = {
        "blueprint_id": "ce_PM-42_20260523",
        "task_id": "PM-42",
        "summary": "Generated handoff",
    }
    result = TaskBlueprintResultV1(
        ok=True,
        task_id="PM-42",
        workspace=".",
        status="generated",
        blueprint_id="ce_PM-42_20260523",
        blueprint_path="runtime/blueprints/ce_PM-42_20260523.json",
        summary="Generated handoff",
        recommendations=("Verify acceptance",),
    )

    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.generate_task_blueprint",
            return_value=result,
        ) as generate,
        patch(
            "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
            return_value=persistence,
        ),
    ):
        response = await client.post(
            "/v2/chief-engineer/blueprints",
            json={
                "task_id": "PM-42",
                "objective": "Build Director task board",
                "run_id": "run-1",
                "constraints": {"guardrail": "no target writes"},
                "context": {"target_files": ["src/frontend/src/app/components/director/DirectorTaskPanel.tsx"]},
            },
        )

    assert response.status_code == 200
    command = generate.call_args.args[0]
    assert command.task_id == "PM-42"
    assert command.workspace == "."
    assert command.run_id == "run-1"
    data = response.json()
    assert data["ok"] is True
    assert data["blueprint_id"] == "ce_PM-42_20260523"
    assert data["blueprint"]["task_id"] == "PM-42"
    assert data["recommendations"] == ["Verify acceptance"]


@pytest.mark.asyncio
async def test_generate_chief_engineer_blueprint_blocks_when_llm_not_ready(client: AsyncClient) -> None:
    """Blueprint generation should fail closed when CE LLM readiness is blocked."""

    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.ensure_required_roles_ready",
            side_effect=StructuredHTTPException(
                status_code=409,
                code="RUNTIME_ROLES_NOT_READY",
                message="One or more required runtime roles are not ready",
                details={"required_roles": ["chief_engineer"], "missing_roles": ["chief_engineer"]},
            ),
        ),
        patch("polaris.delivery.http.v2.chief_engineer.generate_task_blueprint") as generate,
    ):
        response = await client.post(
            "/v2/chief-engineer/blueprints",
            json={
                "task_id": "PM-42",
                "objective": "Build Director task board",
            },
        )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "RUNTIME_ROLES_NOT_READY"
    assert data["error"]["details"]["missing_roles"] == ["chief_engineer"]
    generate.assert_not_called()


@pytest.mark.asyncio
async def test_generate_chief_engineer_blueprint_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Blueprint generation should use the active desktop workspace_path."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
    mock_settings.workspace_path = "C:/Temp/Product"
    result = TaskBlueprintResultV1(
        ok=True,
        task_id="PM-active",
        workspace="C:/Temp/Product",
        status="generated",
        summary="Generated in active workspace",
    )

    with patch(
        "polaris.delivery.http.v2.chief_engineer.generate_task_blueprint",
        return_value=result,
    ) as generate:
        response = await client.post(
            "/v2/chief-engineer/blueprints",
            json={
                "task_id": "PM-active",
                "objective": "Build active workspace blueprint",
            },
        )

    assert response.status_code == 200
    command = generate.call_args.args[0]
    assert command.workspace == "C:/Temp/Product"
    assert response.json()["workspace"] == "C:/Temp/Product"


@pytest.mark.asyncio
async def test_bulk_generate_chief_engineer_blueprints_uses_public_command_contract(
    client: AsyncClient,
) -> None:
    """Bulk generation should cover multiple task handoffs through the CE command contract."""
    persistence = MagicMock()

    def load_payload(blueprint_id: str) -> dict[str, object]:
        return {
            "blueprint_id": blueprint_id,
            "task_id": blueprint_id.removeprefix("ce_"),
            "summary": f"Generated {blueprint_id}",
        }

    persistence.load.side_effect = load_payload

    def generate_result(command: Any) -> TaskBlueprintResultV1:
        task_id = str(command.task_id)
        return TaskBlueprintResultV1(
            ok=True,
            task_id=task_id,
            workspace=str(command.workspace),
            status="generated",
            blueprint_id=f"ce_{task_id}",
            blueprint_path=f"runtime/blueprints/ce_{task_id}.json",
            summary=f"Generated {task_id}",
            recommendations=("Verify acceptance",),
        )

    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.generate_task_blueprint",
            side_effect=generate_result,
        ) as generate,
        patch(
            "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
            return_value=persistence,
        ),
    ):
        response = await client.post(
            "/v2/chief-engineer/blueprints/bulk",
            json={
                "tasks": [
                    {
                        "task_id": "PM-1",
                        "objective": "Build PM handoff",
                        "context": {"target_files": ["src/pm.tsx"]},
                    },
                    {
                        "task_id": "PM-2",
                        "objective": "Build Director handoff",
                        "run_id": "run-2",
                    },
                ]
            },
        )

    assert response.status_code == 200
    assert generate.call_count == 2
    commands = [call.args[0] for call in generate.call_args_list]
    assert [command.task_id for command in commands] == ["PM-1", "PM-2"]
    assert [command.workspace for command in commands] == [".", "."]
    assert commands[1].run_id == "run-2"
    data = response.json()
    assert data["ok"] is True
    assert data["workspace"] == "."
    assert data["total"] == 2
    assert data["generated"] == 2
    assert data["failed"] == 0
    assert [item["blueprint_id"] for item in data["results"]] == ["ce_PM-1", "ce_PM-2"]
    assert data["results"][0]["blueprint"]["summary"] == "Generated ce_PM-1"


@pytest.mark.asyncio
async def test_bulk_generate_chief_engineer_blueprints_links_pm_task_contracts(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Bulk CE generation must link PM tasks to persisted runtime blueprints."""
    from polaris.kernelone.fs.text_ops import write_json_atomic
    from polaris.kernelone.storage import resolve_logical_path

    mock_settings.workspace = str(tmp_path)
    mock_settings.workspace_path = str(tmp_path)
    contract_payload: dict[str, Any] = {
        "schema_version": 2,
        "run_id": "pm-00001",
        "tasks": [
            {"id": "PM-1", "metadata": {}},
            {"id": "PM-2"},
        ],
    }
    contract_path = Path(resolve_logical_path(str(tmp_path), "runtime/contracts/pm_tasks.contract.json"))
    run_contract_path = contract_path.parent.parent / "runs" / "pm-00001" / "contracts" / "pm_tasks.contract.json"
    write_json_atomic(str(contract_path), contract_payload)
    write_json_atomic(str(run_contract_path), contract_payload)

    persistence = MagicMock()
    persistence.load.side_effect = lambda blueprint_id: {
        "blueprint_id": blueprint_id,
        "task_id": blueprint_id.removeprefix("ce_"),
        "summary": f"Generated {blueprint_id}",
    }

    def generate_result(command: Any) -> TaskBlueprintResultV1:
        task_id = str(command.task_id)
        blueprint_id = f"ce_{task_id}"
        return TaskBlueprintResultV1(
            ok=True,
            task_id=task_id,
            workspace=str(command.workspace),
            status="generated",
            blueprint_id=blueprint_id,
            blueprint_path=f"runtime/blueprints/{blueprint_id}.json",
        )

    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.generate_task_blueprint",
            side_effect=generate_result,
        ),
        patch(
            "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
            return_value=persistence,
        ),
    ):
        response = await client.post(
            "/v2/chief-engineer/blueprints/bulk",
            json={
                "tasks": [
                    {"task_id": "PM-1", "objective": "Blueprint PM-1"},
                    {"task_id": "PM-2", "objective": "Blueprint PM-2"},
                ]
            },
        )

    assert response.status_code == 200
    for path in (contract_path, run_contract_path):
        updated = json.loads(path.read_text(encoding="utf-8"))
        rows = {item["id"]: item for item in updated["tasks"]}
        assert rows["PM-1"]["blueprint_id"] == "ce_PM-1"
        assert rows["PM-1"]["runtime_blueprint_path"] == "runtime/blueprints/ce_PM-1.json"
        assert rows["PM-1"]["metadata"]["blueprint_id"] == "ce_PM-1"
        assert rows["PM-2"]["blueprint_id"] == "ce_PM-2"
        assert rows["PM-2"]["metadata"]["runtime_blueprint_path"] == "runtime/blueprints/ce_PM-2.json"


@pytest.mark.asyncio
async def test_bulk_generate_chief_engineer_blueprints_rejects_empty_batch(client: AsyncClient) -> None:
    """Bulk generation should fail before touching the CE command contract for empty batches."""
    with patch("polaris.delivery.http.v2.chief_engineer.generate_task_blueprint") as generate:
        response = await client.post("/v2/chief-engineer/blueprints/bulk", json={"tasks": []})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EMPTY_BLUEPRINT_BATCH"
    generate.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_generate_chief_engineer_blueprints_blocks_when_llm_not_ready(
    client: AsyncClient,
) -> None:
    """Bulk generation should fail closed with the same CE LLM readiness gate."""
    with (
        patch(
            "polaris.delivery.http.v2.chief_engineer.ensure_required_roles_ready",
            side_effect=StructuredHTTPException(
                status_code=409,
                code="RUNTIME_ROLES_NOT_READY",
                message="One or more required runtime roles are not ready",
                details={"required_roles": ["chief_engineer"], "missing_roles": ["chief_engineer"]},
            ),
        ),
        patch("polaris.delivery.http.v2.chief_engineer.generate_task_blueprint") as generate,
    ):
        response = await client.post(
            "/v2/chief-engineer/blueprints/bulk",
            json={
                "tasks": [
                    {
                        "task_id": "PM-42",
                        "objective": "Build Director task board",
                    }
                ]
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUNTIME_ROLES_NOT_READY"
    generate.assert_not_called()


@pytest.mark.asyncio
async def test_get_chief_engineer_blueprint_status_uses_public_query_contract(client: AsyncClient) -> None:
    """Blueprint status route should build the CE status query contract."""
    result = TaskBlueprintResultV1(
        ok=False,
        task_id="PM-404",
        workspace=".",
        status="missing",
        summary="No Chief Engineer blueprint has been generated for this task.",
    )

    with patch(
        "polaris.delivery.http.v2.chief_engineer.get_blueprint_status",
        return_value=result,
    ) as get_status:
        response = await client.get("/v2/chief-engineer/blueprints/status?task_id=PM-404")

    assert response.status_code == 200
    query = get_status.call_args.args[0]
    assert query.task_id == "PM-404"
    assert query.workspace == "."
    data = response.json()
    assert data["ok"] is False
    assert data["status"] == "missing"


@pytest.mark.asyncio
async def test_get_chief_engineer_blueprint_status_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """Blueprint status query should use active workspace_path over stale workspace."""
    mock_settings.workspace = Path("C:/Repo/Polaris")
    mock_settings.workspace_path = "C:/Temp/Product"
    result = TaskBlueprintResultV1(
        ok=False,
        task_id="PM-active",
        workspace="C:/Temp/Product",
        status="missing",
        summary="No Chief Engineer blueprint has been generated for this task.",
    )

    with patch(
        "polaris.delivery.http.v2.chief_engineer.get_blueprint_status",
        return_value=result,
    ) as get_status:
        response = await client.get("/v2/chief-engineer/blueprints/status?task_id=PM-active")

    assert response.status_code == 200
    query = get_status.call_args.args[0]
    assert query.workspace == "C:/Temp/Product"
    assert response.json()["workspace"] == "C:/Temp/Product"


@pytest.mark.asyncio
async def test_get_chief_engineer_blueprint_rejects_invalid_id(client: AsyncClient) -> None:
    """Blueprint detail endpoint should reject unsafe ids before touching persistence."""
    with patch("polaris.delivery.http.v2.chief_engineer.BlueprintPersistence") as persistence_cls:
        response = await client.get("/v2/chief-engineer/blueprints/bad$id")

    assert response.status_code == 400
    persistence_cls.assert_not_called()


@pytest.mark.asyncio
async def test_get_chief_engineer_blueprint_missing_is_read_only(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Missing blueprint details should not initialize the blueprint store on reads."""
    mock_settings.workspace = tmp_path
    mock_settings.workspace_path = str(tmp_path)
    blueprint_dir = tmp_path / "runtime" / "blueprints"

    response = await client.get("/v2/chief-engineer/blueprints/missing-blueprint")

    assert response.status_code == 404
    assert not blueprint_dir.exists()


@pytest.mark.asyncio
async def test_delete_chief_engineer_blueprint_removes_persisted_record(client: AsyncClient) -> None:
    """Blueprint delete should remove one persisted blueprint through the owned store."""
    persistence = MagicMock()
    persistence.delete.return_value = True

    with patch(
        "polaris.delivery.http.v2.chief_engineer.BlueprintPersistence",
        return_value=persistence,
    ) as persistence_cls:
        response = await client.delete("/v2/chief-engineer/blueprints/bp-001")

    assert response.status_code == 200
    persistence_cls.assert_called_once_with(".", ensure_directory=False)
    persistence.delete.assert_called_once_with("bp-001")
    data = response.json()
    assert data == {
        "ok": True,
        "blueprint_id": "bp-001",
        "deleted": True,
        "source": "runtime/blueprints",
    }


@pytest.mark.asyncio
async def test_delete_chief_engineer_blueprint_rejects_invalid_id(client: AsyncClient) -> None:
    """Blueprint delete must reject unsafe ids before touching persistence."""
    with patch("polaris.delivery.http.v2.chief_engineer.BlueprintPersistence") as persistence_cls:
        response = await client.delete("/v2/chief-engineer/blueprints/bad$id")

    assert response.status_code == 400
    persistence_cls.assert_not_called()


@pytest.mark.asyncio
async def test_delete_chief_engineer_blueprint_missing_is_read_only(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Missing blueprint deletion should not initialize the blueprint store."""
    mock_settings.workspace = tmp_path
    mock_settings.workspace_path = str(tmp_path)
    blueprint_dir = tmp_path / "runtime" / "blueprints"

    response = await client.delete("/v2/chief-engineer/blueprints/missing-blueprint")

    assert response.status_code == 404
    assert not blueprint_dir.exists()
