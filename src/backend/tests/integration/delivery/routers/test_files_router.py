"""Contract tests for polaris.delivery.http.routers.files module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import files as files_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(files_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestFilesRouter:
    """Contract tests for the files router."""

    def test_read_file_happy_path(self) -> None:
        """GET /files/read returns 200 with file metadata and content."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.files.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.files.resolve_safe_path",
                return_value="/tmp/workspace/test.py",
            ),
            patch(
                "polaris.delivery.http.routers.files.read_file_tail",
                return_value="hello world",
            ),
            patch(
                "polaris.delivery.http.routers.files.format_mtime",
                return_value="2026-04-24T00:00:00",
            ),
        ):
            response = client.get("/files/read", params={"path": "test.py"})

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["rel_path"] == "test.py"
        assert payload["content"] == "hello world"
        assert "path" in payload
        assert "mtime" in payload

    def test_read_file_missing_path_param(self) -> None:
        """GET /files/read without required path param returns 422."""
        client = _build_client()
        response = client.get("/files/read")
        assert response.status_code == 422

    def test_read_file_with_tail_lines(self) -> None:
        """GET /files/read respects tail_lines query parameter."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.files.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.files.resolve_safe_path",
                return_value="/tmp/workspace/test.py",
            ),
            patch(
                "polaris.delivery.http.routers.files.read_file_tail",
                return_value="line1\nline2",
            ) as mock_read,
            patch(
                "polaris.delivery.http.routers.files.format_mtime",
                return_value="2026-04-24T00:00:00",
            ),
        ):
            response = client.get(
                "/files/read",
                params={"path": "test.py", "tail_lines": 10, "max_chars": 5000},
            )

        assert response.status_code == 200
        mock_read.assert_called_once_with(
            "/tmp/workspace/test.py",
            max_lines=10,
            max_chars=5000,
            allow_fallback=True,
        )

    def test_read_file_dialogue_jsonl_no_fallback(self) -> None:
        """GET /files/read for dialogue.jsonl disables fallback."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.files.build_cache_root",
                return_value="/tmp/cache",
            ),
            patch(
                "polaris.delivery.http.routers.files.resolve_safe_path",
                return_value="/tmp/workspace/dialogue.jsonl",
            ),
            patch(
                "polaris.delivery.http.routers.files.read_file_tail",
                return_value="[]",
            ) as mock_read,
            patch(
                "polaris.delivery.http.routers.files.format_mtime",
                return_value="2026-04-24T00:00:00",
            ),
        ):
            response = client.get(
                "/files/read",
                params={"path": "dialogue.jsonl"},
            )

        assert response.status_code == 200
        mock_read.assert_called_once_with(
            "/tmp/workspace/dialogue.jsonl",
            max_lines=400,
            max_chars=20000,
            allow_fallback=False,
        )
