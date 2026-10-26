"""Shared fixtures for the v2 Director router test suite.

Split from the historical monolithic test_v2_director_router.py so each
router endpoint group (lifecycle, tasks, workers, orchestration) lives in its
own focused test file. Fixtures here are discovered automatically by pytest for
every test_director_router_* module in this directory.

Fixture contract
----------------
* ``mock_settings`` is a REAL :class:`Settings` instance, not a mock.  The
  production ``create_app`` -> ``get_engineering_service`` ->
  ``EngineeringRunService`` owner chain really constructs here, so the host
  configuration must be a genuine, JSON-serialisable settings snapshot.
  (A ``MagicMock(spec=Settings)`` leaks mock children into
  ``build_default_config`` and makes ``json.dumps`` fail.)
* The engineering owner binds to a project-private workspace directory that
  this fixture creates under the pytest basetemp.  It never binds to the
  Polaris main repository, to another instance runtime, or to a real provider
  credential.
* ``workspace_path`` is a SYNTHETIC compatibility field.  The router unit
  tests assert unresolved Windows-style literals (``"C:/Repo/Polaris"``) that
  the production ``Settings.workspace`` validator would resolve against the
  process cwd.  ``_DirectorRouterTestSettings`` keeps the assigned literal for
  those tests only; it does not change production ``Settings`` validation, and
  it is not evidence that production settings normalisation was verified.
* Only transport/auth doubles are replaced here.  The engineering service is
  always the real owner; no fake engineering service is installed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import ServerConfig, Settings
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.config.nats_config import NATSConfig


class _DirectorRouterTestSettings(Settings):
    """Settings adapter that keeps assigned workspace literals for router tests.

    LIMITATION (synthetic test input): the production ``Settings.workspace``
    validator resolves the assigned value against the process working
    directory, while the router unit tests require the unresolved literal
    (``"C:/Repo/Polaris"``).  This adapter therefore disables assignment
    validation for its own instances so the assigned literal survives, and it
    adds ``workspace_path`` as a synthetic compatibility field.

    Production ``Settings`` validation and production code are untouched; this
    class is not evidence that production settings normalisation was verified.
    """

    model_config = {"validate_assignment": False}

    workspace_path: str = ""


@pytest.fixture(scope="session")
def director_router_private_workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create the project-private workspace owned by the engineering owner.

    The directory lives under the pytest basetemp, so the engineering owner
    never binds to the Polaris source tree or to another instance runtime.
    """
    workspace = tmp_path_factory.mktemp("director-router-workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture(scope="session")
def director_router_private_kernel_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create the private KernelOne home that owns the global config root.

    ``kernelone_home()`` is the single global config root
    (``<home>/config/llm/llm_config.json``).  Pointing it at a private
    directory keeps the fixture on the platform's default layout instead of
    introducing a second config root, and keeps the developer's global
    config and the main instance untouched.
    """
    kernel_home = tmp_path_factory.mktemp("director-router-kernel-home")
    kernel_home.mkdir(parents=True, exist_ok=True)
    return kernel_home


@pytest.fixture
def mock_settings(
    monkeypatch: pytest.MonkeyPatch,
    director_router_private_workspace: Path,
    director_router_private_kernel_home: Path,
) -> _DirectorRouterTestSettings:
    """Create a real, JSON-serialisable Settings instance for testing."""
    workspace = director_router_private_workspace
    settings = _DirectorRouterTestSettings(
        workspace=workspace,
        nats=NATSConfig(enabled=False, required=False, url=""),
        server=ServerConfig(cors_origins=["*"]),
    )
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.ramdisk_root = ""
    # The active desktop workspace is the private fixture workspace itself.
    # The historical "." default resolved to the process working directory
    # (the backend source root), which made the router project into the main
    # repository's legacy runtime instead of this private workspace.
    settings.workspace_path = str(workspace)
    # Keep the platform's single global config root inside this task's private
    # KernelOne home, so the fixture never reads or writes the developer's
    # global config nor the main instance. No provider credential is
    # materialised here.
    monkeypatch.setenv("KERNELONE_HOME", str(director_router_private_kernel_home))
    return settings


@pytest.fixture
def mock_app_state(mock_settings: Settings) -> AppState:
    """Create a minimal AppState for testing."""
    return AppState(settings=mock_settings)


@pytest.fixture
def fact_stream_authority() -> Callable[[Path], str]:
    """Provision the real FactStream authority for one private workspace.

    The runtime appends ``task_runtime.execution`` facts through the public
    FactStream owner, which requires an enrolled workspace authority.  A
    workspace that never entered the HTTP lifespan has no authority, so the
    runtime fails closed with ``execution_event_append_failed``.  This fixture
    uses the public bootstrap owner exactly like the production lifespan does;
    it does not fake any append result.
    """
    from polaris.cells.events.fact_stream.public import (
        BootstrapFactStreamWorkspaceCommandV1,
        bootstrap_fact_stream_workspace,
        fact_stream_bootstrap_streams,
    )

    def _provision(workspace: Path) -> str:
        receipt = bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(workspace),
                streams=fact_stream_bootstrap_streams(),
                maintenance_reason="director_router_test_authority_setup",
            )
        )
        return receipt.workspace

    return _provision


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
        patch(
            "polaris.delivery.http.v2.director.build_llm_status",
            return_value={
                "roles": {
                    "director": {
                        "ready": True,
                        "runtime_supported": True,
                        "provider_id": "qwen",
                        "model": "qwen3-max",
                    }
                },
                "blocked_roles": [],
                "unsupported_roles": [],
                "required_ready_roles": ["director"],
                "state": "READY",
            },
        ),
        patch.dict("os.environ", {"KERNELONE_METRICS_ENABLED": "false"}),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            try:
                yield ac
            finally:
                # ASGITransport does not run the ASGI lifespan, so the
                # engineering owner created by ``create_app`` would not be
                # released by the production lifespan teardown.  Release the
                # owner this fixture actually created through its public
                # shutdown path; no other instance is touched.
                engineering_service = getattr(app.state, "engineering_run_service", None)
                if engineering_service is not None:
                    await engineering_service.shutdown()


__all__ = [
    "director_router_private_workspace",
    "mock_app_state",
    "mock_settings",
]
