"""Unit tests for `delivery/api_gateway` public contracts."""

from __future__ import annotations

from polaris.cells.delivery.api_gateway.public.contracts import (
    ApiCommandV1,
    ApiGatewayError,
    ApiQueryV1,
    ApiResponseEventV1,
    ApiResponseV1,
)


class TestApiCommandV1HappyPath:
    def test_minimal(self) -> None:
        cmd = ApiCommandV1(method="GET", route="/health")
        assert cmd.method == "GET"
        assert cmd.route == "/health"
        assert cmd.payload is None

    def test_with_payload(self) -> None:
        cmd = ApiCommandV1(method="POST", route="/api/tasks", payload={"title": "new task"})
        assert cmd.payload == {"title": "new task"}


class TestApiQueryV1HappyPath:
    def test_minimal(self) -> None:
        q = ApiQueryV1(route="/health")
        assert q.route == "/health"
        assert q.params is None

    def test_with_params(self) -> None:
        q = ApiQueryV1(route="/api/tasks", params={"status": "open"})
        assert q.params == {"status": "open"}


class TestApiResponseV1HappyPath:
    def test_construction(self) -> None:
        res = ApiResponseV1(status_code=200, payload={"ok": True})
        assert res.status_code == 200
        assert res.payload == {"ok": True}


class TestApiResponseEventV1HappyPath:
    def test_construction(self) -> None:
        evt = ApiResponseEventV1(route="/health", status_code=200)
        assert evt.route == "/health"
        assert evt.status_code == 200


class TestApiGatewayError:
    def test_raise_and_catch(self) -> None:
        err = ApiGatewayError("invalid route")
        assert str(err) == "invalid route"
        assert isinstance(err, Exception)
