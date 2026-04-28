"""运行 tests.agent_stress 前的 backend 预检。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx


class BackendPreflightStatus(Enum):
    """backend 预检状态。"""

    HEALTHY = "healthy"
    BACKEND_CONTEXT_MISSING = "backend_context_missing"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    AUTH_INVALID = "auth_invalid"
    SETTINGS_UNAVAILABLE = "settings_unavailable"
    RUNTIME_V2_UNAVAILABLE = "runtime_v2_unavailable"


@dataclass
class BackendPreflightReport:
    """backend 预检结果。"""

    timestamp: str
    backend_url: str
    status: BackendPreflightStatus
    backend_reachable: bool
    auth_valid: bool
    settings_accessible: bool
    ws_runtime_v2_accessible: bool = False
    jetstream_accessible: bool = False
    projection_transport: str = "none"
    health_status_code: int | None = None
    settings_status_code: int | None = None
    latency_ms: int = 0
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "backend_url": self.backend_url,
            "status": self.status.value,
            "backend_reachable": self.backend_reachable,
            "auth_valid": self.auth_valid,
            "settings_accessible": self.settings_accessible,
            "ws_runtime_v2_accessible": self.ws_runtime_v2_accessible,
            "jetstream_accessible": self.jetstream_accessible,
            "projection_transport": self.projection_transport,
            "health_status_code": self.health_status_code,
            "settings_status_code": self.settings_status_code,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "details": self.details,
        }


class BackendPreflightProbe:
    """区分 backend 不可达、鉴权错误和 settings 不可用。"""

    def __init__(
        self,
        backend_url: str,
        token: str = "",
        timeout: float = 5.0,
    ) -> None:
        self.backend_url = str(backend_url or "").strip().rstrip("/")
        self.token = str(token or "").strip()
        self.timeout = max(float(timeout or 0.0), 0.5)

        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        http_timeout = httpx.Timeout(self.timeout, connect=min(self.timeout, 2.0))
        self.client = httpx.AsyncClient(timeout=http_timeout, headers=headers)

    async def __aenter__(self) -> BackendPreflightProbe:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.client.aclose()

    async def run(self) -> BackendPreflightReport:
        started = time.perf_counter()
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not self.backend_url:
            return BackendPreflightReport(
                timestamp=timestamp,
                backend_url="",
                status=BackendPreflightStatus.BACKEND_CONTEXT_MISSING,
                backend_reachable=False,
                auth_valid=False,
                settings_accessible=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error="Unable to resolve Polaris backend URL",
                details={},
            )
        health_result = await self._request("/health", include_auth=False)
        latency_ms = int((time.perf_counter() - started) * 1000)

        if not health_result["reachable"]:
            return BackendPreflightReport(
                timestamp=timestamp,
                backend_url=self.backend_url,
                status=BackendPreflightStatus.BACKEND_UNAVAILABLE,
                backend_reachable=False,
                auth_valid=False,
                settings_accessible=False,
                health_status_code=health_result["status_code"],
                latency_ms=latency_ms,
                error=str(health_result["error"]),
                details={
                    "health_error": health_result["error"],
                },
            )

        settings_result = await self._request("/settings", include_auth=True)
        if settings_result["status_code"] in {401, 403}:
            return BackendPreflightReport(
                timestamp=timestamp,
                backend_url=self.backend_url,
                status=BackendPreflightStatus.AUTH_INVALID,
                backend_reachable=True,
                auth_valid=False,
                settings_accessible=False,
                health_status_code=health_result["status_code"],
                settings_status_code=settings_result["status_code"],
                latency_ms=latency_ms,
                error="Unauthorized settings access",
                details={
                    "health_error": health_result["error"],
                    "settings_error": settings_result["error"],
                },
            )

        if not settings_result["ok"]:
            return BackendPreflightReport(
                timestamp=timestamp,
                backend_url=self.backend_url,
                status=BackendPreflightStatus.SETTINGS_UNAVAILABLE,
                backend_reachable=True,
                auth_valid=bool(settings_result["status_code"] not in {401, 403}),
                settings_accessible=False,
                health_status_code=health_result["status_code"],
                settings_status_code=settings_result["status_code"],
                latency_ms=latency_ms,
                error=str(settings_result["error"]),
                details={
                    "health_error": health_result["error"],
                    "settings_error": settings_result["error"],
                },
            )

        projection_result = await self._probe_runtime_v2(settings_result)
        if not projection_result["ok"]:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return BackendPreflightReport(
                timestamp=timestamp,
                backend_url=self.backend_url,
                status=BackendPreflightStatus.RUNTIME_V2_UNAVAILABLE,
                backend_reachable=True,
                auth_valid=True,
                settings_accessible=True,
                ws_runtime_v2_accessible=bool(projection_result["runtime_v2"]),
                jetstream_accessible=bool(projection_result["jetstream"]),
                projection_transport=str(projection_result["transport"] or "none"),
                health_status_code=health_result["status_code"],
                settings_status_code=settings_result["status_code"],
                latency_ms=latency_ms,
                error=str(projection_result["error"]),
                details={
                    "health_error": health_result["error"],
                    "settings_error": settings_result["error"],
                    "projection_error": projection_result["error"],
                    "projection_transport": projection_result["transport"],
                    "projection_ws_url": projection_result["ws_url"],
                },
            )

        return BackendPreflightReport(
            timestamp=timestamp,
            backend_url=self.backend_url,
            status=BackendPreflightStatus.HEALTHY,
            backend_reachable=True,
            auth_valid=True,
            settings_accessible=True,
            ws_runtime_v2_accessible=bool(projection_result["runtime_v2"]),
            jetstream_accessible=bool(projection_result["jetstream"]),
            projection_transport=str(projection_result["transport"] or "none"),
            health_status_code=health_result["status_code"],
            settings_status_code=settings_result["status_code"],
            latency_ms=int((time.perf_counter() - started) * 1000),
            details={
                "projection_transport": projection_result["transport"],
                "projection_ws_url": projection_result["ws_url"],
            },
        )

    async def _request(self, path: str, *, include_auth: bool) -> dict[str, Any]:
        url = f"{self.backend_url}{path}"
        try:
            headers = None
            if not include_auth:
                headers = {}
            response = await self.client.get(url, headers=headers, timeout=self.timeout)
            status_code = int(response.status_code)
            reachable = status_code < 500
            ok = status_code == 200
            payload: dict[str, Any] | None = None
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                # Response body is not valid JSON, payload stays None
                payload = None
            return {
                "ok": ok,
                "reachable": reachable,
                "status_code": status_code,
                "error": None if ok else f"HTTP {status_code}",
                "payload": payload,
            }
        except httpx.HTTPError as exc:
            # httpx.HTTPError covers connection errors, timeouts, HTTP protocol errors.
            # We intentionally do NOT catch CancelledError so it propagates.
            return {
                "ok": False,
                "reachable": False,
                "status_code": None,
                "error": f"{type(exc).__name__}: {exc}",
                "payload": None,
            }

    async def _probe_runtime_v2(self, settings_result: dict[str, Any]) -> dict[str, Any]:
        from .observer.projection import RuntimeProjection

        settings_payload = settings_result.get("payload")
        settings_payload = settings_payload if isinstance(settings_payload, dict) else {}
        workspace = str(settings_payload.get("workspace") or "").strip()
        projection = RuntimeProjection(
            backend_url=self.backend_url,
            token=self.token,
            workspace=workspace,
            transport="ws",
            focus="all",
        )
        probe_timeout = min(max(self.timeout + 2.0, 4.0), 15.0)
        result = await projection.probe_connection(timeout=probe_timeout)
        return {
            "ok": bool(result.get("ok")),
            "runtime_v2": bool(result.get("runtime_v2")),
            "jetstream": bool(result.get("jetstream")),
            "transport": str(result.get("transport") or "none"),
            "error": str(result.get("connection_error") or ""),
            "ws_url": str(result.get("ws_url") or ""),
        }
