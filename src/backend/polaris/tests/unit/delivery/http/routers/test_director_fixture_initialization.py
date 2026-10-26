"""Regression coverage for the shared Director router fixture initialisation.

Scope: this test only asserts the FIXTURE contract that the previous
``MagicMock(spec=Settings)`` debt broke.  It does not assert any business
behaviour and it does not write any target-project artefact.

Covered:
* ``mock_settings`` is a real ``Settings`` instance (not a mock) and carries no
  mock-derived configuration value.
* the settings snapshot is JSON-serialisable, which is what
  ``build_default_config`` -> ``write_json_atomic`` requires.
* ``create_app`` really constructs the engineering owner on the
  project-private workspace, and the engineering SQLite really lands inside
  that private workspace.
* the fixture never writes the developer's global LLM config or the Polaris
  main repository.
"""

from __future__ import annotations

import json
from pathlib import Path

from polaris.bootstrap.config import Settings
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.kernelone.llm.config_store import build_default_config


def _assert_no_mock_values(payload: object) -> None:
    """Reject any mock-derived value anywhere in the settings snapshot."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert "MagicMock" not in type(value).__name__, f"mock-derived value at {key!r}"
            assert "Mock" not in type(value).__name__, f"mock-derived value at {key!r}"
            _assert_no_mock_values(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _assert_no_mock_values(item)


def test_mock_settings_is_a_real_settings_instance(mock_settings: Settings) -> None:
    """The fixture must provide a genuine settings snapshot, not a mock."""
    assert isinstance(mock_settings, Settings)
    assert type(mock_settings).__module__ == "polaris.tests.unit.delivery.http.routers.conftest"


def test_mock_settings_snapshot_is_json_serialisable(mock_settings: Settings) -> None:
    """The real owner path serialises the settings; mocks break that path."""
    payload = build_default_config(mock_settings)
    _assert_no_mock_values(payload)
    encoded = json.dumps(payload)
    assert encoded
    assert "MagicMock" not in encoded


def test_mock_settings_binds_a_project_private_workspace(
    mock_settings: Settings,
    director_router_private_workspace: Path,
) -> None:
    """The engineering owner must bind to the private fixture workspace.

    The private workspace lives under the project's own ``.polaris`` area, so
    this asserts the resolved ownership rather than a raw path prefix: the
    workspace is the private fixture directory, and it is neither the
    repository root nor the backend source root.
    """
    workspace = Path(str(mock_settings.workspace))
    assert workspace.is_dir()
    resolved = workspace.resolve()

    assert resolved == director_router_private_workspace.resolve()

    repo_root = Path(__file__).resolve().parents[6]
    backend_root = Path(str(mock_settings.backend_root)).resolve()
    assert resolved != repo_root
    assert resolved != backend_root
    assert backend_root not in resolved.parents

    # The resolved runtime root belongs to this workspace only.
    from polaris.kernelone.storage.layout import resolve_storage_roots

    roots = resolve_storage_roots(str(resolved))
    runtime_root = Path(roots.runtime_project_root).resolve()
    assert runtime_root == resolved / ".polaris" / "runtime"
    assert runtime_root != backend_root / ".polaris" / "runtime"


def test_create_app_builds_the_real_engineering_owner_on_the_private_workspace(
    mock_settings: Settings,
    director_router_private_workspace: Path,
) -> None:
    """``create_app`` must construct the real owner on the private workspace."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)

    service = getattr(app.state, "engineering_run_service", None)
    assert isinstance(service, EngineeringRunService)
    owner = service

    owner_workspace = Path(str(owner.workspace)).resolve()
    assert owner_workspace == director_router_private_workspace.resolve()

    sessions_db = owner_workspace / ".polaris" / "runtime" / "engineering" / "sessions.sqlite3"
    assert sessions_db.is_file()
    assert sessions_db.parent.is_relative_to(director_router_private_workspace)


def test_fixture_does_not_touch_the_global_llm_config(
    mock_settings: Settings,
    director_router_private_kernel_home: Path,
) -> None:
    """The fixture must keep the platform's global config root private.

    The global config root is resolved by ``kernelone_home()``; the assertion
    is on the EFFECTIVE resolved path, not on whether a particular environment
    override happens to be set.
    """
    from polaris.delivery.http.app_factory import create_app
    from polaris.kernelone.llm.config_store import llm_config_path
    from polaris.kernelone.storage.layout import kernelone_home

    effective_home = Path(kernelone_home()).resolve()
    assert effective_home == director_router_private_kernel_home.resolve()

    config_path = Path(llm_config_path(str(mock_settings.workspace), "")).resolve()
    assert config_path.is_relative_to(effective_home)

    # The real owner writes its default config into that private root, and the
    # written config carries no mock-derived value.
    create_app(settings=mock_settings)
    assert config_path.is_file()
    assert "MagicMock" not in config_path.read_text(encoding="utf-8")
