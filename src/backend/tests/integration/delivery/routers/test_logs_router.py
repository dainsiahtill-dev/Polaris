"""Contract tests for polaris.delivery.http.routers.logs module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import logs as logs_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(logs_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


class _FakeLogEvent:
    """Fake log event for testing."""

    def __init__(self, message: str = "test") -> None:
        self.message = message

    def model_dump(self) -> dict[str, Any]:
        return {"message": self.message}


class _FakeQueryResult:
    """Fake query result for testing."""

    def __init__(self) -> None:
        self.events = [_FakeLogEvent("hello"), _FakeLogEvent("world")]
        self.next_cursor = "cursor-2"
        self.total_count = 100
        self.has_more = True


class TestLogsRouter:
    """Contract tests for the logs router."""

    def test_query_logs_happy_path(self) -> None:
        """GET /logs/query returns 200 with filtered log events."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.logs.LogQueryService",
        ) as mock_service_cls:
            mock_service = MagicMock()
            mock_service.query.return_value = _FakeQueryResult()
            mock_service_cls.return_value = mock_service

            response = client.get(
                "/logs/query",
                params={
                    "run_id": "run-1",
                    "channel": "system",
                    "severity": "info",
                    "limit": 10,
                },
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert len(payload["events"]) == 2
        assert payload["next_cursor"] == "cursor-2"
        assert payload["total_count"] == 100
        assert payload["has_more"] is True

    def test_query_logs_invalid_limit(self) -> None:
        """GET /logs/query with out-of-range limit returns 422."""
        client = _build_client()
        response = client.get("/logs/query", params={"limit": 5000})
        assert response.status_code == 422

    def test_query_logs_invalid_channel(self) -> None:
        """GET /logs/query with invalid channel is treated as None (no filter)."""
        client = _build_client()
        with patch(
            "polaris.delivery.http.routers.logs.LogQueryService",
        ) as mock_service_cls:
            mock_service = MagicMock()
            mock_service.query.return_value = _FakeQueryResult()
            mock_service_cls.return_value = mock_service

            response = client.get(
                "/logs/query",
                params={"channel": "invalid_channel"},
            )

        assert response.status_code == 200
        # Invalid channel is silently ignored and passed as None
        call_args = mock_service.query.call_args[0][0]
        assert call_args.channel is None

    def test_log_user_action_happy_path(self) -> None:
        """POST /logs/user-action returns 200 when action is logged."""
        client = _build_client()
        with (
            patch(
                "polaris.delivery.http.routers.logs.resolve_runtime_path",
                return_value="/tmp/logs/user_actions.jsonl",
            ),
            patch(
                "polaris.kernelone.fs.jsonl.ops.append_jsonl_atomic",
            ) as mock_append,
            patch(
                "polaris.kernelone.events.utc_iso_now",
                return_value="2026-04-24T00:00:00Z",
            ),
        ):
            response = client.post(
                "/logs/user-action",
                json={"action": "click", "user": "tester", "metadata": {"page": "home"}},
            )

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert payload["status"] == "logged"
        assert payload["action"] == "click"
        mock_append.assert_called_once()

    def test_log_user_action_missing_action(self) -> None:
        """POST /logs/user-action without action returns 422."""
        client = _build_client()
        response = client.post("/logs/user-action", json={"user": "tester"})
        assert response.status_code == 422

    def test_get_channels_happy_path(self) -> None:
        """GET /logs/channels returns 200 with available channels."""
        client = _build_client()
        response = client.get("/logs/channels")

        assert response.status_code == 200
        payload: dict[str, Any] = response.json()
        assert "channels" in payload
        assert len(payload["channels"]) == 3
        channel_names = {c["name"] for c in payload["channels"]}
        assert channel_names == {"system", "process", "llm"}
