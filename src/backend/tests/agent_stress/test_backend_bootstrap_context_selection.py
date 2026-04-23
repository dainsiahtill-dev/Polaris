from __future__ import annotations

from pathlib import Path

import pytest
from tests.agent_stress.backend_bootstrap import (
    BOOTSTRAP_CONTEXT_SOURCE,
    ManagedBackendSession,
    ensure_backend_session,
)
from tests.agent_stress.backend_context import BackendContext
from tests.agent_stress.preflight import BackendPreflightStatus


@pytest.mark.asyncio
async def test_desktop_context_defaults_to_auto_bootstrap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    desktop_context = BackendContext(
        backend_url="http://127.0.0.1:49977",
        token="desktop-token",
        source="desktop-backend-info",
        desktop_info_path=str(tmp_path / "desktop-backend.json"),
    )
    captured: dict[str, object] = {}

    async def _fake_auto_bootstrap_backend(**_kwargs):  # noqa: ANN003
        captured.update(_kwargs)
        return ManagedBackendSession(
            context=BackendContext(
                backend_url="http://127.0.0.1:51234",
                token="bootstrap-token",
                source=BOOTSTRAP_CONTEXT_SOURCE,
            ),
            auto_bootstrapped=True,
            desktop_info_path=str(tmp_path / "desktop-backend.json"),
        )

    monkeypatch.setattr("tests.agent_stress.backend_bootstrap.resolve_backend_context", lambda **_kwargs: desktop_context)
    monkeypatch.setattr("tests.agent_stress.backend_bootstrap._auto_bootstrap_backend", _fake_auto_bootstrap_backend)

    session = await ensure_backend_session(auto_bootstrap=True)

    assert session.auto_bootstrapped is True
    assert session.context.source == BOOTSTRAP_CONTEXT_SOURCE
    assert isinstance(captured.get("startup_workspace"), Path)
    assert Path(captured["startup_workspace"]).name.startswith("tests-agent-stress-backend-")


@pytest.mark.asyncio
async def test_explicit_startup_workspace_is_forwarded_to_auto_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    desktop_context = BackendContext(
        backend_url="http://127.0.0.1:49977",
        token="desktop-token",
        source="desktop-backend-info",
        desktop_info_path=str(tmp_path / "desktop-backend.json"),
    )
    expected_workspace = tmp_path / "fresh-stress-workspace"
    captured: dict[str, object] = {}

    async def _fake_auto_bootstrap_backend(**_kwargs):  # noqa: ANN003
        captured.update(_kwargs)
        return ManagedBackendSession(
            context=BackendContext(
                backend_url="http://127.0.0.1:51235",
                token="bootstrap-token",
                source=BOOTSTRAP_CONTEXT_SOURCE,
            ),
            auto_bootstrapped=True,
            desktop_info_path=str(tmp_path / "desktop-backend.json"),
        )

    monkeypatch.setattr("tests.agent_stress.backend_bootstrap.resolve_backend_context", lambda **_kwargs: desktop_context)
    monkeypatch.setattr("tests.agent_stress.backend_bootstrap._auto_bootstrap_backend", _fake_auto_bootstrap_backend)

    session = await ensure_backend_session(auto_bootstrap=True, startup_workspace=expected_workspace)

    assert session.auto_bootstrapped is True
    assert Path(captured["startup_workspace"]) == expected_workspace.resolve()


@pytest.mark.asyncio
async def test_allow_desktop_context_env_preserves_healthy_desktop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    desktop_context = BackendContext(
        backend_url="http://127.0.0.1:49977",
        token="desktop-token",
        source="desktop-backend-info",
        desktop_info_path=str(tmp_path / "desktop-backend.json"),
    )

    async def _fake_probe_preflight_status(_context):  # noqa: ANN001
        return BackendPreflightStatus.HEALTHY

    monkeypatch.setenv("KERNELONE_STRESS_ALLOW_DESKTOP_CONTEXT", "1")
    monkeypatch.setattr("tests.agent_stress.backend_bootstrap.resolve_backend_context", lambda **_kwargs: desktop_context)
    monkeypatch.setattr("tests.agent_stress.backend_bootstrap._probe_preflight_status", _fake_probe_preflight_status)

    session = await ensure_backend_session(auto_bootstrap=True)

    assert session.auto_bootstrapped is False
    assert session.context.source == "desktop-backend-info"
