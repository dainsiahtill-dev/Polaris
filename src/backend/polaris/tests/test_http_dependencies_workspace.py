from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from polaris.delivery.http.dependencies import _append_debug, get_director_service, get_workspace


@pytest.mark.asyncio
async def test_get_workspace_prefers_app_state_settings(tmp_path) -> None:
    request = SimpleNamespace(
        query_params={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                app_state=SimpleNamespace(
                    settings=SimpleNamespace(workspace=str(tmp_path / "from_app_state")),
                ),
                settings=SimpleNamespace(workspace=str(tmp_path / "from_app")),
            )
        ),
    )

    resolved = await get_workspace(request)
    assert resolved == (tmp_path / "from_app_state").resolve()


@pytest.mark.asyncio
async def test_get_workspace_prefers_workspace_path(tmp_path) -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                app_state=SimpleNamespace(
                    settings=SimpleNamespace(
                        workspace=str(tmp_path / "stale_repo"),
                        workspace_path=str(tmp_path / "active_project"),
                    ),
                ),
                settings=SimpleNamespace(workspace=str(tmp_path / "from_app")),
            )
        )
    )

    resolved = await get_workspace(request)
    assert resolved == (tmp_path / "active_project").resolve()


@pytest.mark.asyncio
async def test_get_workspace_falls_back_to_app_settings(tmp_path) -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                app_state=SimpleNamespace(settings=SimpleNamespace(workspace="")),
                settings=SimpleNamespace(workspace=str(tmp_path / "fallback")),
            )
        )
    )

    resolved = await get_workspace(request)
    assert resolved == (tmp_path / "fallback").resolve()


@pytest.mark.asyncio
async def test_get_workspace_falls_back_to_app_workspace_path(tmp_path) -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                app_state=SimpleNamespace(settings=SimpleNamespace(workspace="", workspace_path="")),
                settings=SimpleNamespace(
                    workspace=str(tmp_path / "stale_repo"),
                    workspace_path=str(tmp_path / "active_fallback"),
                ),
            )
        )
    )

    resolved = await get_workspace(request)
    assert resolved == (tmp_path / "active_fallback").resolve()


@pytest.mark.asyncio
async def test_get_workspace_raises_when_not_configured() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                app_state=SimpleNamespace(settings=SimpleNamespace(workspace="")),
                settings=SimpleNamespace(workspace=""),
            )
        )
    )

    with pytest.raises(HTTPException) as exc:
        await get_workspace(request)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_get_director_service_prefers_active_workspace_path(tmp_path) -> None:
    active_workspace = str((tmp_path / "active_project").resolve())
    stale_workspace = str((tmp_path / "stale_repo").resolve())
    request = SimpleNamespace(
        query_params={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                app_state=SimpleNamespace(
                    settings=SimpleNamespace(
                        workspace=stale_workspace,
                        workspace_path=active_workspace,
                    ),
                ),
                settings=SimpleNamespace(workspace=""),
            )
        ),
    )
    mock_service = MagicMock()
    mock_service.config.workspace = active_workspace

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_get_container,
        patch(
            "polaris.delivery.http.dependencies.rebind_director_service",
            new_callable=AsyncMock,
        ) as mock_rebind,
    ):
        mock_get_container.return_value.resolve_async = AsyncMock(return_value=mock_service)

        result = await get_director_service(request)

    assert result is mock_service
    mock_rebind.assert_not_awaited()


def test_dependency_debug_append_is_disabled_without_explicit_log_path() -> None:
    """Director dependency resolution should not write debug files unless explicitly enabled."""
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("polaris.delivery.http.dependencies.Path.open") as mock_open,
    ):
        _append_debug("dependency.get_director_service", {"rebind": False})

    mock_open.assert_not_called()


def test_dependency_debug_append_ignores_debug_log_failure() -> None:
    """Optional dependency debug evidence must not break desktop API calls."""
    with (
        patch.dict("os.environ", {"KERNELONE_BACKEND_DEBUG_LOG": "C:/Temp/dependency-debug.jsonl"}),
        patch("polaris.delivery.http.dependencies.Path.open", side_effect=OSError("debug log locked")),
        patch("polaris.delivery.http.dependencies.logger.debug") as mock_debug,
    ):
        _append_debug("dependency.get_director_service", {"rebind": False})

    mock_debug.assert_called_once()
