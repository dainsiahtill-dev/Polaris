from __future__ import annotations

import pytest

from .preflight import BackendPreflightProbe, BackendPreflightStatus


@pytest.mark.asyncio
async def test_preflight_is_healthy_when_runtime_v2_and_jetstream_are_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with BackendPreflightProbe(
        backend_url="http://127.0.0.1:49977",
        token="demo-token",
        timeout=1.0,
    ) as probe:

        async def _fake_request(path: str, *, include_auth: bool) -> dict[str, object]:
            del include_auth
            if path == "/health":
                return {
                    "ok": True,
                    "reachable": True,
                    "status_code": 200,
                    "error": None,
                    "payload": None,
                }
            return {
                "ok": True,
                "reachable": True,
                "status_code": 200,
                "error": None,
                "payload": {"workspace": "C:/Temp/demo-workspace"},
            }

        async def _fake_probe_runtime_v2(_settings_result: dict[str, object]) -> dict[str, object]:
            return {
                "ok": True,
                "runtime_v2": True,
                "jetstream": True,
                "transport": "ws.runtime_v2",
                "error": "",
                "ws_url": "ws://127.0.0.1:49977/v2/ws/runtime",
            }

        monkeypatch.setattr(probe, "_request", _fake_request)
        monkeypatch.setattr(probe, "_probe_runtime_v2", _fake_probe_runtime_v2)

        report = await probe.run()

    assert report.status == BackendPreflightStatus.HEALTHY
    assert report.ws_runtime_v2_accessible is True
    assert report.jetstream_accessible is True
    assert report.projection_transport == "ws.runtime_v2"


@pytest.mark.asyncio
async def test_preflight_fails_when_runtime_v2_or_jetstream_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with BackendPreflightProbe(
        backend_url="http://127.0.0.1:49977",
        token="demo-token",
        timeout=1.0,
    ) as probe:

        async def _fake_request(path: str, *, include_auth: bool) -> dict[str, object]:
            del include_auth
            if path == "/health":
                return {
                    "ok": True,
                    "reachable": True,
                    "status_code": 200,
                    "error": None,
                    "payload": None,
                }
            return {
                "ok": True,
                "reachable": True,
                "status_code": 200,
                "error": None,
                "payload": {"workspace": "C:/Temp/demo-workspace"},
            }

        async def _fake_probe_runtime_v2(_settings_result: dict[str, object]) -> dict[str, object]:
            return {
                "ok": False,
                "runtime_v2": True,
                "jetstream": False,
                "transport": "none",
                "error": "runtime_v2_subscribed_without_jetstream",
                "ws_url": "ws://127.0.0.1:49977/v2/ws/runtime",
            }

        monkeypatch.setattr(probe, "_request", _fake_request)
        monkeypatch.setattr(probe, "_probe_runtime_v2", _fake_probe_runtime_v2)

        report = await probe.run()

    assert report.status == BackendPreflightStatus.RUNTIME_V2_UNAVAILABLE
    assert report.ws_runtime_v2_accessible is True
    assert report.jetstream_accessible is False
    assert report.error == "runtime_v2_subscribed_without_jetstream"
