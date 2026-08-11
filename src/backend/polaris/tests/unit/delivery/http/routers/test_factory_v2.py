"""Tests for Polaris v2 factory router.

Covers GET /v2/factory/runs, POST /v2/factory/runs,
GET /v2/factory/runs/{run_id}, GET /v2/factory/runs/{run_id}/events,
GET /v2/factory/runs/{run_id}/audit-bundle,
removed legacy GET /v2/factory/runs/{run_id}/stream,
POST /v2/factory/runs/{run_id}/control,
and GET /v2/factory/runs/{run_id}/artifacts.
External services are mocked to avoid storage and orchestration dependencies.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.public.types import RunPhase
from polaris.cells.runtime.state_owner.public.service import AppState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> Settings:
    """Create a minimal Settings instance for testing."""
    from polaris.bootstrap.config import ServerConfig, Settings
    from polaris.config.nats_config import NATSConfig

    settings = MagicMock(spec=Settings)
    settings.workspace = "."
    settings.workspace_path = "."
    settings.ramdisk_root = ""
    settings.nats = NATSConfig(enabled=False, required=False, url="")
    settings.server = ServerConfig(cors_origins=["*"])
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.logging = MagicMock()
    settings.logging.enable_debug_tracing = False
    return settings


@pytest.fixture
def mock_app_state(mock_settings: Settings) -> AppState:
    """Create a minimal AppState for testing."""
    return AppState(settings=mock_settings)


@pytest.fixture
async def client(mock_settings: Settings, mock_app_state: AppState) -> AsyncIterator[AsyncClient]:
    """Create an async test client with mocked lifespan."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch.dict(
            "os.environ",
            {
                "KERNELONE_METRICS_ENABLED": "false",
                "KERNELONE_RATE_LIMIT_ENABLED": "false",
            },
        ),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_factory_run(
    run_id: str = "factory_test123",
    status: str = "running",
    stages: list[str] | None = None,
    stages_completed: list[str] | None = None,
    metadata: dict | None = None,
) -> MagicMock:
    """Build a MagicMock that looks like a FactoryRun."""
    from polaris.cells.factory.pipeline.public.service import FactoryRunStatus

    run = MagicMock()
    run.id = run_id
    run.status = FactoryRunStatus(status)
    run.config = MagicMock()
    run.config.stages = stages or ["pm_planning", "quality_gate"]
    run.config.name = "Test Run"
    run.config.description = "test"
    run.stages_completed = stages_completed or []
    run.stages_failed = []
    run.recovery_point = None
    run.created_at = "2024-01-01T00:00:00+00:00"
    run.started_at = run.created_at
    run.updated_at = run.created_at
    run.completed_at = None
    run.metadata = metadata or {
        "current_stage": "pm_planning",
        "last_successful_stage": None,
        "last_failed_stage": None,
        "failure": None,
    }
    return run


def _write_director_resume_evidence(workspace: Path) -> None:
    from polaris.kernelone.storage import resolve_runtime_path

    _bootstrap_test_fact_stream(workspace)
    plan_path = Path(resolve_runtime_path(str(workspace), "runtime/tasks/plan.json"))
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps({"tasks": [{"id": "TASK-1", "goal": "Implement feature"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    task_path = Path(resolve_runtime_path(str(workspace), "runtime/tasks/task_1.json"))
    task_path.write_text(
        json.dumps({"id": 1, "status": "pending", "subject": "Implement feature"}, ensure_ascii=False),
        encoding="utf-8",
    )
    blueprint_path = workspace / ".polaris" / "blueprints" / "latest.review.json"
    blueprint_path.parent.mkdir(parents=True, exist_ok=True)
    blueprint_path.write_text(
        json.dumps(
            {
                "factory_run_id": "factory_original123",
                "generated_blueprints": 1,
                "blueprints": [{"task_id": "TASK-1", "blueprint_id": "ce_TASK-1_original"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    snapshot_path = workspace / ".polaris" / "factory_snapshots" / "pre_director" / "manifest.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps({"snapshot_kind": "pre_director_workspace", "files": []}, ensure_ascii=False),
        encoding="utf-8",
    )


def _bootstrap_test_fact_stream(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_router_unit_test",
        )
    )


def test_director_resume_evidence_rehydrates_source_taskboard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from polaris.cells.events.fact_stream.public.contracts import QueryFactEventsV1
    from polaris.cells.events.fact_stream.public.service import query_fact_events
    from polaris.delivery.http.routers import factory

    workspace = tmp_path / "resume-unit"
    workspace.mkdir()
    _bootstrap_test_fact_stream(workspace)
    runtime_projects = tmp_path / "runtime-projects"
    legacy_runtime = runtime_projects / "resume-unit-222222222222" / "runtime"
    monkeypatch.setattr(
        factory,
        "resolve_storage_roots",
        lambda _workspace: SimpleNamespace(
            runtime_projects_root=str(runtime_projects),
            workspace_key="resume-unit-111111111111",
        ),
    )

    task_dir = legacy_runtime / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "plan.json").write_text(
        json.dumps({"tasks": [{"id": "TASK-1", "target_files": ["src/index.ts"]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "task_1.json").write_text(
        json.dumps(
            {
                "id": 1,
                "status": "in_progress",
                "metadata": {
                    "runtime_execution": {"status": "active"},
                    "workflow_run_id": "director-old",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (task_dir / "task_1.session.json").write_text(
        json.dumps({"status": "active"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (legacy_runtime / "blueprints").mkdir(parents=True)
    (legacy_runtime / "blueprints" / "ce_TASK-1.json").write_text("{}", encoding="utf-8")

    blueprint_path = workspace / ".polaris" / "blueprints" / "latest.review.json"
    blueprint_path.parent.mkdir(parents=True)
    blueprint_path.write_text(
        json.dumps({"generated_blueprints": 1, "blueprints": [{"task_id": "TASK-1"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    snapshot_path = workspace / ".polaris" / "factory_snapshots" / "pre_director" / "manifest.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps({"snapshot_kind": "pre_director_workspace", "files": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    factory._ensure_director_resume_evidence_ready(str(workspace))

    target_dir = Path(factory.resolve_runtime_path(str(workspace), "runtime/tasks"))
    assert factory._taskboard_record_count(str(workspace)) == 1
    assert not (target_dir / "task_1.session.json").exists()
    task_1 = json.loads((target_dir / "task_1.json").read_text(encoding="utf-8"))
    assert task_1["status"] == "pending"
    assert "runtime_execution" not in task_1["metadata"]
    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    assert any(event.get("event_type") == "reexecution_imported" for event in events)


def test_director_resume_evidence_resets_current_dirty_taskboard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from polaris.cells.events.fact_stream.public.contracts import QueryFactEventsV1
    from polaris.cells.events.fact_stream.public.service import query_fact_events
    from polaris.delivery.http.routers import factory

    workspace = tmp_path / "resume-current-unit"
    workspace.mkdir()
    _bootstrap_test_fact_stream(workspace)
    runtime_projects = tmp_path / "runtime-projects"
    monkeypatch.setattr(
        factory,
        "resolve_storage_roots",
        lambda _workspace: SimpleNamespace(
            runtime_projects_root=str(runtime_projects),
            workspace_key="resume-current-unit-111111111111",
        ),
    )

    task_dir = Path(factory.resolve_runtime_path(str(workspace), "runtime/tasks"))
    task_dir.mkdir(parents=True)
    (task_dir / "plan.json").write_text(
        json.dumps({"tasks": [{"id": "TASK-1", "target_files": ["src/index.ts"]}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (task_dir / "task_1.json").write_text(
        json.dumps(
            {
                "id": 1,
                "status": "failed",
                "metadata": {
                    "runtime_execution": {"status": "failed"},
                    "workflow_run_id": "director-old",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (task_dir / "task_1.session.json").write_text(
        json.dumps({"status": "failed"}, ensure_ascii=False),
        encoding="utf-8",
    )

    blueprint_path = workspace / ".polaris" / "blueprints" / "latest.review.json"
    blueprint_path.parent.mkdir(parents=True)
    blueprint_path.write_text(
        json.dumps({"generated_blueprints": 1, "blueprints": [{"task_id": "TASK-1"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    snapshot_path = workspace / ".polaris" / "factory_snapshots" / "pre_director" / "manifest.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps({"snapshot_kind": "pre_director_workspace", "files": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    factory._ensure_director_resume_evidence_ready(str(workspace))

    assert not (task_dir / "task_1.session.json").exists()
    task_1 = json.loads((task_dir / "task_1.json").read_text(encoding="utf-8"))
    assert task_1["status"] == "pending"
    assert "runtime_execution" not in task_1["metadata"]
    evidence = json.loads((task_dir / "director_resume_reset.json").read_text(encoding="utf-8"))
    assert evidence["reset_statuses"] == "all_task_records"
    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    assert any(event.get("event_type") == "reexecution_reset" for event in events)


def test_build_gates_uses_recorded_quality_gate_score() -> None:
    from polaris.delivery.http.routers.factory import _build_gates

    run = _make_factory_run(
        status="completed",
        stages_completed=["quality_gate"],
        metadata={
            "current_stage": "quality_gate",
            "last_successful_stage": "quality_gate",
            "stage_results": {
                "quality_gate": {
                    "status": "success",
                    "output": "Quality gate completed; qa_passed=True; qa_score=46; qa_critical=0",
                    "artifacts": ["runtime/qa/report.json", "workspace/roles/qa/run/report.json"],
                }
            },
        },
    )

    gates = _build_gates(run, RunPhase.COMPLETED)

    assert len(gates) == 1
    assert gates[0].passed is True
    assert gates[0].score == 46.0
    assert "qa_score=46" in gates[0].message
    assert gates[0].artifacts == ["runtime/qa/report.json", "workspace/roles/qa/run/report.json"]


def test_execution_stages_for_recovery_after_checkpoint() -> None:
    """Recovered checkpoint retries should resume after the saved checkpoint."""
    from polaris.delivery.http.routers.factory import _execution_stages_for_run

    run = _make_factory_run(
        status="recovering",
        stages=["pm_planning", "chief_engineer_review", "director_dispatch", "quality_gate"],
        metadata={
            "current_stage": "director_dispatch",
            "last_successful_stage": "pm_planning",
            "retry_execution_stage": "director_dispatch",
            "retry_start_policy": "after_checkpoint",
        },
    )
    run.recovery_point = "pm_planning"

    assert _execution_stages_for_run(run, run.config.stages) == [
        "chief_engineer_review",
        "director_dispatch",
        "quality_gate",
    ]


def test_execution_stages_for_explicit_retry_phase_reruns_target_stage() -> None:
    """Explicit retry phase requests should rerun the selected stage."""
    from polaris.delivery.http.routers.factory import _execution_stages_for_run

    run = _make_factory_run(
        status="recovering",
        stages=["pm_planning", "chief_engineer_review", "director_dispatch", "quality_gate"],
        metadata={
            "current_stage": "pm_planning",
            "retry_execution_stage": "pm_planning",
            "retry_start_policy": "rerun_stage",
        },
    )
    run.recovery_point = "pm_planning"

    assert _execution_stages_for_run(run, run.config.stages) == [
        "pm_planning",
        "chief_engineer_review",
        "director_dispatch",
        "quality_gate",
    ]


def test_retry_start_request_marks_same_run_director_retry_as_resume() -> None:
    """Director retry restores its checkpoint without rerunning PM or CE."""
    from polaris.delivery.http.routers.factory import _build_retry_start_request

    run = _make_factory_run(
        status="recovering",
        stages=["pm_planning", "chief_engineer_review", "director_dispatch", "quality_gate"],
        metadata={
            "retry_execution_stage": "director_dispatch",
            "factory_start_request": {
                "workspace": "/tmp/project",
                "start_from": "pm",
                "directive": "Build project",
                "director_workflow_execution_mode": "serial",
                "director_dispatch_driver": "task-market",
                "persist_workspace": True,
            },
        },
    )

    request = _build_retry_start_request(run, "/tmp/project")

    assert request.start_from == "director_resume"
    assert request.director_workflow_execution_mode == "serial"
    assert request.director_dispatch_driver == "task-market"
    assert request.persist_workspace is False


# ---------------------------------------------------------------------------
# GET /v2/factory/runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_factory_runs_success(client: AsyncClient) -> None:
    """GET /v2/factory/runs should return a paginated list of runs."""
    run = _make_factory_run(run_id="factory_abc", status="running")

    with (
        patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
        ) as mock_svc_cls,
    ):
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.list_runs = AsyncMock(
            return_value=[
                {
                    "id": "factory_abc",
                    "name": "Test Run",
                    "status": "running",
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "updated_at": "2024-01-01T00:00:00+00:00",
                    "current_stage": "pm_planning",
                    "last_successful_stage": None,
                    "stages_completed": 0,
                    "stages_failed": 0,
                }
            ]
        )
        mock_svc.get_run = AsyncMock(return_value=run)

        response = await client.get("/v2/factory/runs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["runs"]) == 1
        assert data["runs"][0]["run_id"] == "factory_abc"
        mock_svc.list_runs.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_factory_runs_empty(client: AsyncClient) -> None:
    """GET /v2/factory/runs should return empty list when no runs exist."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.list_runs = AsyncMock(return_value=[])

        response = await client.get("/v2/factory/runs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["runs"] == []


# ---------------------------------------------------------------------------
# POST /v2/factory/runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_factory_run_success(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /v2/factory/runs should create and start a factory run."""
    monkeypatch.delenv("KERNELONE_FACTORY_LIVE_LLM_PREFLIGHT", raising=False)
    run = _make_factory_run(run_id="factory_new123", status="running")

    with (
        patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
        ) as mock_svc_cls,
        patch(
            "polaris.delivery.http.routers.factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.factory.save_persisted_settings",
        ),
        patch(
            "polaris.delivery.http.routers.factory._schedule_factory_run_task",
        ) as schedule_run_mock,
        patch("polaris.delivery.http.routers.factory.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.create_run = AsyncMock(return_value=run)
        mock_svc.start_run = AsyncMock(return_value=run)

        response = await client.post(
            "/v2/factory/runs",
            json={
                "workspace": ".",
                "start_from": "pm",
                "directive": "Build a thing",
                "run_director": True,
                "director_iterations": 1,
                "loop": False,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_new123"
        assert data["status"] == "running"
        mock_svc.create_run.assert_awaited_once()
        mock_svc.start_run.assert_awaited_once()
        mock_roles_ready.assert_called_once()
        assert mock_roles_ready.call_args.kwargs["default_roles"] == [
            "pm",
            "director",
            "qa",
        ]
        assert mock_roles_ready.call_args.kwargs["force_roles"] == [
            "pm",
            "director",
            "qa",
        ]
        assert mock_roles_ready.call_args.kwargs["live_check"] is False
        schedule_run_mock.assert_called_once()


@pytest.mark.asyncio
async def test_start_factory_run_from_architect_requires_architect_readiness(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full factory runs must fail closed when Architect LLM readiness is blocked."""
    monkeypatch.delenv("KERNELONE_FACTORY_LIVE_LLM_PREFLIGHT", raising=False)
    run = _make_factory_run(run_id="factory_architect123", status="running")

    with (
        patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
        ) as mock_svc_cls,
        patch(
            "polaris.delivery.http.routers.factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.factory.save_persisted_settings",
        ),
        patch(
            "polaris.delivery.http.routers.factory._schedule_factory_run_task",
        ) as schedule_run_mock,
        patch("polaris.delivery.http.routers.factory.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.create_run = AsyncMock(return_value=run)
        mock_svc.start_run = AsyncMock(return_value=run)

        response = await client.post(
            "/v2/factory/runs",
            json={
                "workspace": ".",
                "start_from": "architect",
                "directive": "Build the full FashionGenStudio workflow",
                "run_director": True,
                "director_iterations": 0,
                "loop": False,
            },
        )

    assert response.status_code == 200
    mock_roles_ready.assert_called_once()
    assert mock_roles_ready.call_args.kwargs["default_roles"] == [
        "architect",
        "pm",
        "director",
        "qa",
    ]
    assert mock_roles_ready.call_args.kwargs["force_roles"] == [
        "architect",
        "pm",
        "director",
        "qa",
    ]
    assert mock_roles_ready.call_args.kwargs["live_check"] is False
    schedule_run_mock.assert_called_once()


@pytest.mark.asyncio
async def test_start_factory_run_from_director_resume_uses_director_only_stage_graph(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Director-only resume must not silently rerun PM or Chief Engineer."""
    monkeypatch.delenv("KERNELONE_FACTORY_LIVE_LLM_PREFLIGHT", raising=False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    _write_director_resume_evidence(workspace)
    run = _make_factory_run(
        run_id="factory_director123",
        status="running",
        stages=["director_dispatch", "quality_gate"],
    )

    with (
        patch("polaris.delivery.http.routers.factory.FactoryRunService") as mock_svc_cls,
        patch("polaris.delivery.http.routers.factory.sync_process_settings_environment"),
        patch("polaris.delivery.http.routers.factory.save_persisted_settings"),
        patch("polaris.delivery.http.routers.factory._schedule_factory_run_task") as schedule_run_mock,
        patch("polaris.delivery.http.routers.factory.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.create_run = AsyncMock(return_value=run)
        mock_svc.start_run = AsyncMock(return_value=run)

        response = await client.post(
            "/v2/factory/runs",
            json={
                "workspace": str(workspace),
                "start_from": "director_resume",
                "directive": "Resume Director from trusted PM/CE evidence",
                "run_director": True,
                "director_iterations": 1,
                "loop": False,
                "persist_workspace": False,
            },
        )

    assert response.status_code == 200
    config = mock_svc.create_run.call_args.args[0]
    assert config.name == "Factory Run - director_resume"
    assert config.stages == ["director_dispatch", "quality_gate"]
    assert mock_roles_ready.call_args.kwargs["default_roles"] == ["director", "qa"]
    assert mock_roles_ready.call_args.kwargs["force_roles"] == ["director", "qa"]
    schedule_run_mock.assert_called_once()
    from polaris.kernelone.storage import resolve_runtime_path

    bound_review_path = Path(
        resolve_runtime_path(str(workspace), "runtime/state/blueprints/factory_director123.review.json")
    )
    bound_review = json.loads(bound_review_path.read_text(encoding="utf-8"))
    assert bound_review["factory_run_id"] == "factory_director123"
    assert bound_review["director_resume_binding"]["source_factory_run_id"] == "factory_original123"
    assert bound_review["blueprints"][0]["blueprint_id"] == "ce_TASK-1_original"


def test_director_resume_evidence_accepts_plan_without_task_file_mirror(
    tmp_path: Path,
) -> None:
    """TaskRuntime rows are rematerialized by Director dispatch for the new run."""
    from polaris.delivery.http.routers import factory
    from polaris.kernelone.storage import resolve_runtime_path

    workspace = tmp_path / "project"
    workspace.mkdir()
    _write_director_resume_evidence(workspace)
    task_mirror = Path(resolve_runtime_path(str(workspace), "runtime/tasks/task_1.json"))
    task_mirror.unlink()

    factory._ensure_director_resume_evidence_ready(str(workspace))

    assert not task_mirror.exists()


@pytest.mark.asyncio
async def test_start_factory_run_from_director_resume_fails_closed_without_resume_evidence(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    """Director-only resume requires PM, CE and snapshot evidence."""
    workspace = tmp_path / "project"
    workspace.mkdir()

    with (
        patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
        ) as mock_svc_cls,
        patch(
            "polaris.delivery.http.routers.factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.factory.save_persisted_settings",
        ),
        patch("polaris.delivery.http.routers.factory.ensure_required_roles_ready") as mock_roles_ready,
    ):
        response = await client.post(
            "/v2/factory/runs",
            json={
                "workspace": str(workspace),
                "start_from": "director_resume",
                "directive": "Resume Director",
                "run_director": True,
                "director_iterations": 1,
                "loop": False,
                "persist_workspace": False,
            },
        )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "DIRECTOR_RESUME_EVIDENCE_MISSING"
    assert "runtime/tasks/plan.json" in data["error"]["details"]["missing_evidence"]
    mock_svc_cls.return_value.create_run.assert_not_called()
    mock_roles_ready.assert_not_called()


@pytest.mark.asyncio
async def test_start_factory_run_enables_live_llm_preflight_when_env_requests_it(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory start should only run live LLM probes when explicitly requested."""
    monkeypatch.setenv("KERNELONE_FACTORY_LIVE_LLM_PREFLIGHT", "1")
    run = _make_factory_run(run_id="factory_live123", status="running")

    with (
        patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
        ) as mock_svc_cls,
        patch(
            "polaris.delivery.http.routers.factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.factory.save_persisted_settings",
        ),
        patch(
            "polaris.delivery.http.routers.factory._schedule_factory_run_task",
        ) as schedule_run_mock,
        patch("polaris.delivery.http.routers.factory.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.create_run = AsyncMock(return_value=run)
        mock_svc.start_run = AsyncMock(return_value=run)

        response = await client.post(
            "/v2/factory/runs",
            json={
                "workspace": ".",
                "start_from": "pm",
                "directive": "Build a thing",
                "run_director": True,
                "director_iterations": 1,
                "loop": False,
            },
        )

    assert response.status_code == 200
    assert mock_roles_ready.call_args.kwargs["live_check"] is True
    schedule_run_mock.assert_called_once()


@pytest.mark.asyncio
async def test_start_factory_run_blocks_when_stage_roles_not_ready(client: AsyncClient) -> None:
    """Factory start should fail closed before run creation when stage runtime roles are blocked."""
    from polaris.delivery.http.routers._shared import StructuredHTTPException

    with (
        patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
        ) as mock_svc_cls,
        patch(
            "polaris.delivery.http.routers.factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.factory.save_persisted_settings",
        ),
        patch(
            "polaris.delivery.http.routers.factory._schedule_factory_run_task",
        ) as schedule_run_mock,
        patch("polaris.delivery.http.routers.factory.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_roles_ready.side_effect = StructuredHTTPException(
            status_code=409,
            code="RUNTIME_ROLES_NOT_READY",
            message="One or more required runtime roles are not ready",
            details={
                "required_roles": ["pm", "director", "qa"],
                "missing_roles": ["pm"],
            },
        )

        response = await client.post(
            "/v2/factory/runs",
            json={
                "workspace": ".",
                "start_from": "pm",
                "directive": "Build a thing",
                "run_director": True,
                "director_iterations": 1,
                "loop": False,
            },
        )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "RUNTIME_ROLES_NOT_READY"
    assert data["error"]["details"]["missing_roles"] == ["pm"]
    mock_svc_cls.return_value.create_run.assert_not_called()
    schedule_run_mock.assert_not_called()


@pytest.mark.asyncio
async def test_start_factory_run_missing_workspace(client: AsyncClient) -> None:
    """POST /v2/factory/runs when workspace is not configured should return 400."""
    from polaris.delivery.http.routers._shared import StructuredHTTPException

    with patch(
        "polaris.delivery.http.routers.factory._resolve_workspace",
        side_effect=StructuredHTTPException(
            status_code=400,
            code="WORKSPACE_NOT_CONFIGURED",
            message="workspace not configured",
        ),
    ):
        response = await client.post(
            "/v2/factory/runs",
            json={
                "workspace": ".",
                "start_from": "auto",
                "directive": "test",
            },
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "WORKSPACE_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# GET /v2/factory/runs/{run_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_factory_run_status_success(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id} should return run status."""
    run = _make_factory_run(run_id="factory_abc", status="running")

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)

        response = await client.get("/v2/factory/runs/factory_abc")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert data["status"] == "running"
        assert "roles" in data


@pytest.mark.asyncio
async def test_get_factory_run_status_exposes_audit_metadata(client: AsyncClient) -> None:
    """Factory status should expose safe lineage metadata for desktop evidence."""
    run = _make_factory_run(
        run_id="factory_exported",
        status="running",
        metadata={
            "current_stage": "pm_planning",
            "last_successful_stage": None,
            "export_session_id": "sess_pm",
            "export_bundle_path": ".polaris/exports/sess_pm_export.json",
            "directive": "Build the PM Director desktop handoff.",
            "summary_md": "# Hidden duplicate summary",
            "failure": {
                "detail": "previous failure detail",
                "traceback": "internal traceback should not appear in status metadata",
            },
        },
    )

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)

        response = await client.get("/v2/factory/runs/factory_exported")
        assert response.status_code == 200
        data = response.json()
        assert data["metadata"]["export_session_id"] == "sess_pm"
        assert data["metadata"]["export_bundle_path"] == ".polaris/exports/sess_pm_export.json"
        assert "Build the PM Director desktop handoff." in data["metadata"]["directive"]
        assert "summary_md" not in data["metadata"]
        assert "traceback" not in data["metadata"]["failure"]


@pytest.mark.asyncio
async def test_get_factory_run_status_not_found(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id} should 404 for missing run."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=None)

        response = await client.get("/v2/factory/runs/missing")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_factory_run_status_reports_snapshot_contention_as_503(client: AsyncClient) -> None:
    """An existing run with a busy snapshot must never be projected as 404."""

    from polaris.cells.factory.pipeline.internal.factory_store import FileLockTimeoutError

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(
            side_effect=FileLockTimeoutError(Path("/runtime/factory/factory_busy/run.json"), 5.0)
        )

        response = await client.get("/v2/factory/runs/factory_busy")

    assert response.status_code == 503
    data = response.json()
    assert data["error"]["code"] == "FACTORY_RUN_SNAPSHOT_BUSY"


# ---------------------------------------------------------------------------
# GET /v2/factory/runs/{run_id}/events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_factory_run_events_success(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id}/events should return events."""
    run = _make_factory_run(run_id="factory_abc")
    events = [
        {"type": "started", "message": "Run started"},
        {"type": "stage_started", "stage": "pm_planning"},
    ]

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.get_run_events = AsyncMock(return_value=events)

        response = await client.get("/v2/factory/runs/factory_abc/events")
        assert response.status_code == 200
        data = response.json()
        assert len(data["events"]) == 2
        assert data["events"][0]["type"] == "started"


@pytest.mark.asyncio
async def test_get_factory_run_events_not_found(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id}/events should 404 for missing run."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=None)

        response = await client.get("/v2/factory/runs/missing/events")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /v2/factory/runs/{run_id}/audit-bundle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_factory_run_audit_bundle_success(client: AsyncClient, tmp_path: Path) -> None:
    """GET /v2/factory/runs/{run_id}/audit-bundle should return audit bundle."""
    run = _make_factory_run(run_id="factory_abc", status="completed", stages_completed=["quality_gate"])
    run.metadata["summary_md"] = "# Summary"
    run.metadata["summary_json"] = {"ok": True}

    run_dir = tmp_path / "factory_abc"
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "report.json").write_text('{"passed": true}')

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.get_run_events = AsyncMock(return_value=[])
        mock_svc.store.get_run_dir.return_value = run_dir

        response = await client.get("/v2/factory/runs/factory_abc/audit-bundle")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert data["status"] == "completed"
        assert "evidence_counts" in data


@pytest.mark.asyncio
async def test_get_factory_run_audit_bundle_not_found(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id}/audit-bundle should 404 for missing run."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=None)

        response = await client.get("/v2/factory/runs/missing/audit-bundle")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"


# ---------------------------------------------------------------------------
# Removed legacy GET /v2/factory/runs/{run_id}/stream route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_factory_run_events_route_is_not_registered(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id}/stream must not exist."""
    response = await client.get("/v2/factory/runs/factory_abc/stream")

    assert response.status_code == 404
    assert "text/event-stream" not in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_stream_factory_run_events_missing_run_still_route_absent(client: AsyncClient) -> None:
    """Removed stream route should not leak run existence checks."""
    response = await client.get("/v2/factory/runs/missing/stream")

    assert response.status_code == 404
    assert "text/event-stream" not in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# POST /v2/factory/runs/{run_id}/control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_factory_run_cancel(client: AsyncClient) -> None:
    """POST /v2/factory/runs/{run_id}/control with cancel should cancel run."""
    run = _make_factory_run(run_id="factory_abc", status="running")
    cancelled = _make_factory_run(run_id="factory_abc", status="cancelled")

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.cancel_run = AsyncMock(return_value=cancelled)

        response = await client.post(
            "/v2/factory/runs/factory_abc/control",
            json={"action": "cancel", "reason": "user request"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert data["status"] == "cancelled"
        mock_svc.cancel_run.assert_awaited_once_with("factory_abc", "user request")


@pytest.mark.asyncio
async def test_control_factory_run_not_found(client: AsyncClient) -> None:
    """POST /v2/factory/runs/{run_id}/control should 404 for missing run."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=None)

        response = await client.post(
            "/v2/factory/runs/missing/control",
            json={"action": "cancel"},
        )
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_control_factory_run_pause(client: AsyncClient) -> None:
    """POST /v2/factory/runs/{run_id}/control with pause should pause run."""
    run = _make_factory_run(run_id="factory_abc", status="running")
    paused = _make_factory_run(run_id="factory_abc", status="paused")

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.execute_pause = AsyncMock(return_value=paused)

        response = await client.post(
            "/v2/factory/runs/factory_abc/control",
            json={"action": "pause"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert data["status"] == "paused"
        mock_svc.execute_pause.assert_awaited_once_with("factory_abc")


@pytest.mark.asyncio
async def test_control_factory_run_resume(client: AsyncClient) -> None:
    """POST /v2/factory/runs/{run_id}/control with resume should resume run."""
    run = _make_factory_run(run_id="factory_abc", status="paused")
    resumed = _make_factory_run(run_id="factory_abc", status="running")

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.execute_resume = AsyncMock(return_value=resumed)

        response = await client.post(
            "/v2/factory/runs/factory_abc/control",
            json={"action": "resume"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert data["status"] == "running"
        mock_svc.execute_resume.assert_awaited_once_with("factory_abc")


@pytest.mark.asyncio
async def test_control_factory_run_retry_from_checkpoint(client: AsyncClient) -> None:
    """POST /v2/factory/runs/{run_id}/control can recover from the latest checkpoint."""
    run = _make_factory_run(run_id="factory_abc", status="failed")
    recovered = _make_factory_run(run_id="factory_abc", status="recovering")

    with (
        patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
        ) as mock_svc_cls,
        patch(
            "polaris.delivery.http.routers.factory._schedule_factory_run_task",
        ) as schedule_run_mock,
    ):
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.retry_run_from_stage = AsyncMock(return_value=recovered)

        response = await client.post(
            "/v2/factory/runs/factory_abc/control",
            json={"action": "retry_from_checkpoint", "reason": "operator retry"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert data["status"] == "recovering"
        mock_svc.retry_run_from_stage.assert_awaited_once_with("factory_abc", None, "operator retry")
        schedule_run_mock.assert_called_once()


@pytest.mark.asyncio
async def test_control_factory_run_retry_phase(client: AsyncClient) -> None:
    """POST /v2/factory/runs/{run_id}/control maps target phase to a configured stage."""
    run = _make_factory_run(
        run_id="factory_abc",
        status="failed",
        stages=["docs_generation", "pm_planning", "director_dispatch"],
    )
    recovered = _make_factory_run(run_id="factory_abc", status="recovering")

    with (
        patch(
            "polaris.delivery.http.routers.factory.FactoryRunService",
        ) as mock_svc_cls,
        patch(
            "polaris.delivery.http.routers.factory._schedule_factory_run_task",
        ) as schedule_run_mock,
    ):
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.retry_run_from_stage = AsyncMock(return_value=recovered)

        response = await client.post(
            "/v2/factory/runs/factory_abc/control",
            json={"action": "retry_phase", "target_phase": "implementation", "reason": "rerun delivery"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "recovering"
        mock_svc.retry_run_from_stage.assert_awaited_once_with(
            "factory_abc",
            "director_dispatch",
            "rerun delivery",
        )
        schedule_run_mock.assert_called_once()


@pytest.mark.asyncio
async def test_control_factory_run_retry_phase_rejects_unconfigured_phase(client: AsyncClient) -> None:
    """POST /v2/factory/runs/{run_id}/control rejects phases outside the run stage graph."""
    run = _make_factory_run(run_id="factory_abc", status="failed", stages=["pm_planning"])

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)

        response = await client.post(
            "/v2/factory/runs/factory_abc/control",
            json={"action": "retry_phase", "target_phase": "implementation"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "INVALID_REQUEST"
        assert "implementation" in data["error"]["message"]


# ---------------------------------------------------------------------------
# GET /v2/factory/runs/{run_id}/artifacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_factory_run_artifacts_success(client: AsyncClient, tmp_path: Path) -> None:
    """GET /v2/factory/runs/{run_id}/artifacts should list artifact files."""
    run = _make_factory_run(run_id="factory_abc", status="completed")
    run.metadata["summary_md"] = "# Summary"
    run.metadata["summary_json"] = {"ok": True}

    run_dir = tmp_path / "factory_abc"
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "report.json").write_text('{"passed": true}')

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.get_run_events = AsyncMock(return_value=[])
        mock_svc.store.get_run_dir.return_value = run_dir

        response = await client.get("/v2/factory/runs/factory_abc/artifacts")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert len(data["artifacts"]) == 1
        assert data["artifacts"][0]["name"] == "report.json"


@pytest.mark.asyncio
async def test_get_factory_run_artifacts_not_found(client: AsyncClient) -> None:
    """GET /v2/factory/runs/{run_id}/artifacts should 404 for missing run."""
    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=None)

        response = await client.get("/v2/factory/runs/missing/artifacts")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_factory_run_artifacts_empty(client: AsyncClient, tmp_path: Path) -> None:
    """GET /v2/factory/runs/{run_id}/artifacts should return empty list when no artifacts."""
    run = _make_factory_run(run_id="factory_abc", status="running")

    run_dir = tmp_path / "factory_abc"
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)

    with patch(
        "polaris.delivery.http.routers.factory.FactoryRunService",
    ) as mock_svc_cls:
        mock_svc = MagicMock()
        mock_svc_cls.return_value = mock_svc
        mock_svc.get_run = AsyncMock(return_value=run)
        mock_svc.get_run_events = AsyncMock(return_value=[])
        mock_svc.store.get_run_dir.return_value = run_dir

        response = await client.get("/v2/factory/runs/factory_abc/artifacts")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "factory_abc"
        assert data["artifacts"] == []
