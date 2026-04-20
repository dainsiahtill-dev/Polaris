from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from polaris.delivery.http.dependencies import get_workspace


@pytest.mark.asyncio
async def test_get_workspace_prefers_app_state_settings(tmp_path) -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                app_state=SimpleNamespace(
                    settings=SimpleNamespace(workspace=str(tmp_path / "from_app_state")),
                ),
                settings=SimpleNamespace(workspace=str(tmp_path / "from_app")),
            )
        )
    )

    resolved = await get_workspace(request)
    assert resolved == (tmp_path / "from_app_state").resolve()


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
