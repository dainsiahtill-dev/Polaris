"""Contract tests for polaris.delivery.http.routers.memos module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import memos as memos_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(memos_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestMemosRouter:
    """Contract tests for the memos router."""

    def test_get_memos_happy_path(self) -> None:
        """GET /memos/list returns 200 with memo list."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.memos.list_memos",
            return_value={"memos": [{"id": "1", "text": "hello"}]},
        ) as mock_list:
            response = client.get("/memos/list")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["memos"][0]["id"] == "1"
        mock_list.assert_called_once_with(".", "", 200)

    def test_get_memos_with_limit(self) -> None:
        """GET /memos/list respects limit parameter."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.memos.list_memos",
            return_value={"memos": []},
        ) as mock_list:
            response = client.get("/memos/list", params={"limit": 50})

        assert response.status_code == 200
        mock_list.assert_called_once_with(".", "", 50)

    def test_get_memos_invalid_limit(self) -> None:
        """GET /memos/list with invalid limit returns 422."""
        client = _build_client()
        response = client.get("/memos/list", params={"limit": "not_a_number"})
        assert response.status_code == 422
