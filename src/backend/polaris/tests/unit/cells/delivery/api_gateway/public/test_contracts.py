"""Tests for polaris.cells.delivery.api_gateway.public.contracts."""

from __future__ import annotations

from polaris.cells.delivery.api_gateway.public.contracts import (
    ApiCommandV1,
    ApiGatewayError,
    ApiQueryV1,
    ApiResponseEventV1,
    ApiResponseV1,
)


class TestApiCommandV1:
    def test_fields(self) -> None:
        cmd = ApiCommandV1(method="POST", route="/v1/test")
        assert cmd.method == "POST"
        assert cmd.route == "/v1/test"
        assert cmd.payload is None

    def test_with_payload(self) -> None:
        cmd = ApiCommandV1(method="POST", route="/v1/test", payload={"key": "value"})
        assert cmd.payload == {"key": "value"}


class TestApiQueryV1:
    def test_fields(self) -> None:
        q = ApiQueryV1(route="/v1/test")
        assert q.route == "/v1/test"
        assert q.params is None

    def test_with_params(self) -> None:
        q = ApiQueryV1(route="/v1/test", params={"key": "value"})
        assert q.params == {"key": "value"}


class TestApiResponseV1:
    def test_fields(self) -> None:
        r = ApiResponseV1(status_code=200, payload={"result": "ok"})
        assert r.status_code == 200
        assert r.payload == {"result": "ok"}


class TestApiResponseEventV1:
    def test_fields(self) -> None:
        ev = ApiResponseEventV1(route="/v1/test", status_code=200)
        assert ev.route == "/v1/test"
        assert ev.status_code == 200


class TestApiGatewayError:
    def test_is_exception(self) -> None:
        assert issubclass(ApiGatewayError, Exception)
