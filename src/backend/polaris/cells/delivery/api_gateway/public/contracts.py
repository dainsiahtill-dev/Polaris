from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class ApiCommandV1:
    method: str
    route: str
    payload: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ApiQueryV1:
    route: str
    params: Mapping[str, str] | None = None


@dataclass(frozen=True)
class ApiResponseV1:
    status_code: int
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class ApiResponseEventV1:
    route: str
    status_code: int


class ApiGatewayError(Exception):
    """Raised when delivery cannot normalize an inbound request."""


__all__ = [
    "ApiCommandV1",
    "ApiGatewayError",
    "ApiQueryV1",
    "ApiResponseEventV1",
    "ApiResponseV1",
]
