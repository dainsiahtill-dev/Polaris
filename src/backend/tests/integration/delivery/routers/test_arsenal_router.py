"""Contract tests for polaris.delivery.http.routers.arsenal module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import arsenal as arsenal_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(arsenal_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class TestArsenalRouter:
    """Contract tests for the arsenal router."""

    def test_vision_status_happy_path(self) -> None:
        """GET /arsenal/vision/status returns 200 with vision service status."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.get_status.return_value = {
            "pil_available": True,
            "advanced_available": True,
            "model_loaded": True,
        }
        with patch(
            "polaris.delivery.http.routers.arsenal.get_vision_service",
            return_value=mock_service,
        ):
            response = client.get("/arsenal/vision/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["pil_available"] is True
        assert payload["model_loaded"] is True

    def test_vision_status_service_unavailable(self) -> None:
        """GET /arsenal/vision/status returns fallback status when service unavailable."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.arsenal.get_vision_service",
            side_effect=RuntimeError("Service not initialized"),
        ):
            response = client.get("/arsenal/vision/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["pil_available"] is False
        assert payload["advanced_available"] is False
        assert payload["model_loaded"] is False

    def test_vision_analyze_happy_path(self) -> None:
        """POST /arsenal/vision/analyze returns 200 with analysis result."""
        client = _build_client()
        mock_service = MagicMock()
        mock_service.is_loaded = True
        mock_service.analyze_image.return_value = {
            "objects": [{"label": "button", "confidence": 0.95}],
        }
        with patch(
            "polaris.delivery.http.routers.arsenal.get_vision_service",
            return_value=mock_service,
        ):
            response = client.post(
                "/arsenal/vision/analyze",
                json={"image": "base64encodedimage", "task": "<OD>"},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "objects" in payload

    def test_scheduler_status_returns_disabled(self) -> None:
        """GET /arsenal/scheduler/status returns turbo_disabled status."""
        client = _build_client()
        response = client.get("/arsenal/scheduler/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["available"] is False
        assert payload["active"] is False
        assert payload["reason"] == "turbo_disabled"

    def test_scheduler_start_returns_disabled_message(self) -> None:
        """POST /arsenal/scheduler/start returns disabled message."""
        client = _build_client()
        response = client.post("/arsenal/scheduler/start")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["available"] is False
        assert "turbo feature is disabled" in payload.get("message", "")

    def test_scheduler_stop_returns_disabled_message(self) -> None:
        """POST /arsenal/scheduler/stop returns disabled message."""
        client = _build_client()
        response = client.post("/arsenal/scheduler/stop")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["available"] is False
        assert "turbo feature is disabled" in payload.get("message", "")

    def test_code_map_invalid_workspace(self) -> None:
        """GET /arsenal/code_map returns empty points for invalid workspace."""
        client = _build_client()
        # Override settings to use invalid workspace
        app = client.app
        app.state.app_state.settings.workspace = "/nonexistent/path"
        with patch(
            "os.path.isdir",
            return_value=False,
        ):
            response = client.get("/arsenal/code_map")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["points"] == []
        assert payload["mode"] == "error"

    def test_code_map_valid_workspace_empty(self) -> None:
        """GET /arsenal/code_map returns empty points for empty workspace."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.arsenal.os.path.isdir",
                return_value=True,
            ),
            patch(
                "polaris.delivery.http.routers.arsenal.os.walk",
                return_value=[],
            ),
        ):
            response = client.get("/arsenal/code_map")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["points"] == []
        assert payload["mode"] == "cpu"

    def test_code_index_happy_path(self) -> None:
        """POST /arsenal/code/index returns 200 with indexing result."""
        client = _build_client()
        with patch(
            "polaris.infrastructure.db.repositories.lancedb_code_search.index_workspace",
            return_value={"indexed": 10, "errors": 0},
        ):
            response = client.post("/arsenal/code/index")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True

    def test_code_search_happy_path(self) -> None:
        """POST /arsenal/code/search returns 200 with search results."""
        client = _build_client()
        mock_results = [
            {"path": "src/main.py", "score": 0.95, "content": "def hello"},
        ]
        with patch(
            "polaris.infrastructure.db.repositories.lancedb_code_search.search_code",
            return_value=mock_results,
        ):
            response = client.post(
                "/arsenal/code/search",
                json={"query": "hello function", "limit": 10},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is True
        assert len(payload["results"]) == 1

    def test_code_search_error_handling(self) -> None:
        """POST /arsenal/code/search handles errors gracefully."""
        client = _build_client()
        with patch(
            "polaris.infrastructure.db.repositories.lancedb_code_search.search_code",
            side_effect=RuntimeError("Search failed"),
        ):
            response = client.post(
                "/arsenal/code/search",
                json={"query": "test"},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["ok"] is False
        assert "error" in payload

    def test_mcp_status_server_not_found(self) -> None:
        """GET /arsenal/mcp/status returns not available when server file missing."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.arsenal.os.path.isfile",
            return_value=False,
        ):
            response = client.get("/arsenal/mcp/status")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["available"] is False
        assert payload["healthy"] is False
        assert "Server file not found" in payload["error"]

    def test_director_capabilities_happy_path(self) -> None:
        """GET /arsenal/director/capabilities returns 200 with capability matrix."""
        client = _build_client()
        mock_caps = [
            {"name": "task_planning", "enabled": True},
            {"name": "code_review", "enabled": True},
        ]
        with patch(
            "polaris.domain.entities.capability.get_role_capabilities",
            return_value=mock_caps,
        ):
            response = client.get("/arsenal/director/capabilities")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["role"] == "director"
        assert len(payload["capabilities"]) == 2

    def test_director_capabilities_error_handling(self) -> None:
        """GET /arsenal/director/capabilities handles errors gracefully."""
        client = _build_client()
        with patch(
            "polaris.domain.entities.capability.get_role_capabilities",
            side_effect=RuntimeError("Failed to get capabilities"),
        ):
            response = client.get("/arsenal/director/capabilities")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "capabilities" in payload
        assert payload["capabilities"] == []


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
        # Line count is text.count("\\n") + 1, so "def hello()\\n    pass" has 2 lines
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
        # Files should be sorted
        assert result[0]["id"] == "a.txt"
        assert result[1]["id"] == "b.txt"
        assert result[2]["id"] == "c.txt"


class TestReadFileChunked:
    """Unit tests for read_file_chunked helper function."""

    def test_read_file_chunked_returns_chunks(self) -> None:
        """read_file_chunked yields content in chunks."""
        import tempfile

        from polaris.delivery.http.routers.arsenal import read_file_chunked

        # Create a temp file with known content
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as f:
            f.write("Hello, World!")
            temp_path = f.name

        try:
            chunks = list(read_file_chunked(temp_path, chunk_size=5))
            combined = "".join(chunks)
            assert combined == "Hello, World!"
        finally:
            import os

            os.unlink(temp_path)

    def test_read_file_chunked_respects_max_size(self) -> None:
        """read_file_chunked truncates at MAX_FILE_SIZE_BYTES."""
        import tempfile

        from polaris.delivery.http.routers.arsenal import MAX_FILE_SIZE_BYTES, read_file_chunked

        # Create a temp file with content exceeding max size
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as f:
            f.write("x" * (MAX_FILE_SIZE_BYTES + 1000))
            temp_path = f.name

        try:
            chunks = list(read_file_chunked(temp_path))
            combined = "".join(chunks)
            # Should be truncated to MAX_FILE_SIZE_BYTES
            assert len(combined.encode("utf-8")) <= MAX_FILE_SIZE_BYTES
        finally:
            import os

            os.unlink(temp_path)
