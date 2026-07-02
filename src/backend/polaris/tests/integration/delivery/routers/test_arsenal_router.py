"""Contract tests for retired Arsenal route aliases and helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import arsenal as arsenal_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(arsenal_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("GET", "/arsenal/vision/status"),
        ("POST", "/arsenal/vision/analyze"),
        ("GET", "/arsenal/scheduler/status"),
        ("POST", "/arsenal/scheduler/start"),
        ("POST", "/arsenal/scheduler/stop"),
        ("GET", "/arsenal/code_map"),
        ("POST", "/arsenal/code/index"),
        ("POST", "/arsenal/code/search"),
        ("GET", "/arsenal/mcp/status"),
        ("GET", "/arsenal/director/capabilities"),
    ),
)
def test_retired_arsenal_alias_routes_are_not_registered(method: str, path: str) -> None:
    client = _build_client()
    response = client.request(method, path, json={})
    assert response.status_code == 404


class TestBuildBasicProjectMap:
    """Unit tests for _build_basic_project_map helper function."""

    def test_build_basic_project_map_empty(self) -> None:
        """_build_basic_project_map returns empty list for empty input."""
        from polaris.delivery.http.routers.arsenal import _build_basic_project_map

        result = _build_basic_project_map({})
        assert result == []

    def test_build_basic_project_map_single_file(self) -> None:
        """_build_basic_project_map returns correct points for single file."""
        from polaris.delivery.http.routers.arsenal import _build_basic_project_map

        file_contents = {"src/main.py": "def hello():\n    pass"}
        result = _build_basic_project_map(file_contents)

        assert len(result) == 1
        assert result[0]["id"] == "src/main.py"
        assert result[0]["path"] == "src/main.py"
        # Line count is text.count("\n") + 1, so "def hello()\n    pass" has 2 lines.
        assert result[0]["lines"] == 2
        assert result[0]["size_bytes"] > 0

    def test_build_basic_project_map_multiple_files(self) -> None:
        """_build_basic_project_map returns sorted points for multiple files."""
        from polaris.delivery.http.routers.arsenal import _build_basic_project_map

        file_contents = {
            "b.txt": "content",
            "a.txt": "content",
            "c.txt": "content",
        }
        result = _build_basic_project_map(file_contents)

        assert len(result) == 3
        assert result[0]["id"] == "a.txt"
        assert result[1]["id"] == "b.txt"
        assert result[2]["id"] == "c.txt"


class TestReadFileChunked:
    """Unit tests for read_file_chunked helper function."""

    def test_read_file_chunked_returns_chunks(self, tmp_path: Path) -> None:
        """read_file_chunked yields content in chunks."""
        from polaris.delivery.http.routers.arsenal import read_file_chunked

        source = tmp_path / "sample.txt"
        source.write_text("Hello, World!", encoding="utf-8")

        chunks = list(read_file_chunked(str(source), chunk_size=5))
        combined = "".join(chunks)
        assert combined == "Hello, World!"

    def test_read_file_chunked_respects_max_size(self, tmp_path: Path) -> None:
        """read_file_chunked truncates at MAX_FILE_SIZE_BYTES."""
        from polaris.delivery.http.routers.arsenal import MAX_FILE_SIZE_BYTES, read_file_chunked

        source = tmp_path / "large.txt"
        source.write_text("x" * (MAX_FILE_SIZE_BYTES + 1000), encoding="utf-8")

        chunks = list(read_file_chunked(str(source)))
        combined = "".join(chunks)
        assert len(combined.encode("utf-8")) <= MAX_FILE_SIZE_BYTES
