from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.bootstrap.config import Settings
from polaris.config.nats_config import NATSConfig
from polaris.delivery.http.app_factory import lifespan


def _make_app(settings: Settings) -> Any:
    return SimpleNamespace(state=SimpleNamespace(settings=settings))


def _patch_lifespan_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_container() -> object:
        return object()

    async def fake_close_default_client() -> None:
        return None

    async def fake_shutdown_local_nats_runtime() -> None:
        return None

    async def fake_start_factory_settlement_runtime(
        workspace: str,
        *,
        enable_wake_bridge: bool,
        wake_bridge_required: bool,
    ) -> object:
        del workspace, enable_wake_bridge, wake_bridge_required
        return object()

    async def fake_stop_factory_settlement_runtime(_workspace: str) -> bool:
        return True

    monkeypatch.setattr("polaris.infrastructure.di.container.reset_container", lambda: None)
    monkeypatch.setattr("polaris.infrastructure.di.container.get_container", fake_get_container)
    monkeypatch.setattr("polaris.cells.resident.autonomy.public.service.reset_resident_services", lambda: None)
    monkeypatch.setattr("polaris.bootstrap.assembly.assemble_core_services", lambda container, settings: None)
    monkeypatch.setattr("polaris.kernelone.process.terminate_external_loop_pm_processes", lambda workspace: [])
    monkeypatch.setattr("polaris.infrastructure.messaging.close_default_client", fake_close_default_client)
    monkeypatch.setattr(
        "polaris.infrastructure.log_pipeline.jetstream_publisher.shutdown_log_jetstream_publisher",
        lambda: None,
    )
    monkeypatch.setattr(
        "polaris.infrastructure.messaging.nats.server_runtime.shutdown_local_nats_runtime",
        fake_shutdown_local_nats_runtime,
    )
    monkeypatch.setattr(
        "polaris.cells.factory.pipeline.public.start_factory_settlement_runtime",
        fake_start_factory_settlement_runtime,
    )
    monkeypatch.setattr(
        "polaris.cells.factory.pipeline.public.stop_factory_settlement_runtime",
        fake_stop_factory_settlement_runtime,
    )
    monkeypatch.setattr(
        "polaris.cells.events.fact_stream.public.bootstrap_fact_stream_workspace",
        lambda _command: None,
    )


@pytest.mark.asyncio
async def test_lifespan_skips_managed_nats_when_nats_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_lifespan_dependencies(monkeypatch)
    calls: list[str] = []

    async def fake_ensure_local_nats_runtime(
        url: str, *, startup_timeout_seconds: float, allow_autostart: bool
    ) -> None:
        calls.append(url)

    monkeypatch.setattr(
        "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
        fake_ensure_local_nats_runtime,
    )
    settings = Settings(
        workspace=str(tmp_path),
        nats=NATSConfig(enabled=False, required=False, url="nats://127.0.0.1:4222"),
    )

    async with lifespan(_make_app(settings)):
        pass

    assert calls == []


@pytest.mark.asyncio
async def test_lifespan_enables_log_pipeline_jetstream_publish_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_lifespan_dependencies(monkeypatch)
    monkeypatch.delenv("KERNELONE_JETSTREAM_PUBLISH", raising=False)

    async def fake_ensure_local_nats_runtime(
        _url: str, *, startup_timeout_seconds: float, allow_autostart: bool
    ) -> None:
        return None

    monkeypatch.setattr(
        "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
        fake_ensure_local_nats_runtime,
    )
    settings = Settings(
        workspace=str(tmp_path),
        nats=NATSConfig(enabled=True, required=True, url="nats://127.0.0.1:4222"),
    )

    async with lifespan(_make_app(settings)):
        assert os.environ["KERNELONE_JETSTREAM_PUBLISH"] == "1"


@pytest.mark.asyncio
async def test_lifespan_continues_when_nats_optional_bootstrap_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_lifespan_dependencies(monkeypatch)

    async def fake_ensure_local_nats_runtime(
        _url: str, *, startup_timeout_seconds: float, allow_autostart: bool
    ) -> None:
        raise OSError("nats socket is unavailable")

    monkeypatch.setattr(
        "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
        fake_ensure_local_nats_runtime,
    )
    settings = Settings(
        workspace=str(tmp_path),
        nats=NATSConfig(enabled=True, required=False, url="nats://127.0.0.1:4222"),
    )

    async with lifespan(_make_app(settings)):
        pass


@pytest.mark.asyncio
async def test_lifespan_fails_closed_when_required_nats_bootstrap_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_lifespan_dependencies(monkeypatch)

    async def fake_ensure_local_nats_runtime(
        _url: str, *, startup_timeout_seconds: float, allow_autostart: bool
    ) -> None:
        raise RuntimeError("nats-server executable not found for managed local runtime")

    monkeypatch.setattr(
        "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
        fake_ensure_local_nats_runtime,
    )
    settings = Settings(
        workspace=str(tmp_path),
        nats=NATSConfig(enabled=True, required=True, url="nats://127.0.0.1:4222"),
    )

    with pytest.raises(RuntimeError, match="nats-server executable not found"):
        async with lifespan(_make_app(settings)):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("credentials", [{"user": "configured-user"}, {"password": "configured-password"}])
async def test_config_only_credentials_deny_plaintext_autostart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, credentials: dict[str, str]
) -> None:
    import socket

    from polaris.infrastructure.messaging.nats import server_runtime

    _patch_lifespan_dependencies(monkeypatch)
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "private-home"))
    monkeypatch.delenv("KERNELONE_NATS_USER", raising=False)
    monkeypatch.delenv("KERNELONE_NATS_PASSWORD", raising=False)
    native = server_runtime.ensure_local_nats_runtime

    async def ensure_with_security_intent(
        url: str, *, startup_timeout_seconds: float, allow_autostart: bool = True
    ) -> None:
        assert allow_autostart is False, "config credentials were not conveyed as autostart denial"
        await native(url, startup_timeout_seconds=startup_timeout_seconds, allow_autostart=allow_autostart)

    monkeypatch.setattr(server_runtime, "ensure_local_nats_runtime", ensure_with_security_intent)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    settings = Settings(workspace=str(tmp_path), nats=NATSConfig(url=f"nats://127.0.0.1:{port}", **credentials))
    with pytest.raises(RuntimeError, match="credentialed endpoint cannot autostart"):
        async with lifespan(_make_app(settings)):
            pass
    assert not (tmp_path / "private-home").exists()
