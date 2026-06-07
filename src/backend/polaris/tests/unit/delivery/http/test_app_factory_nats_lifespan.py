from __future__ import annotations

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


@pytest.mark.asyncio
async def test_lifespan_skips_managed_nats_when_nats_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_lifespan_dependencies(monkeypatch)
    calls: list[str] = []

    async def fake_ensure_local_nats_runtime(url: str) -> None:
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
async def test_lifespan_continues_when_nats_optional_bootstrap_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_lifespan_dependencies(monkeypatch)

    async def fake_ensure_local_nats_runtime(_url: str) -> None:
        raise RuntimeError("nats-server executable not found for managed local runtime")

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

    async def fake_ensure_local_nats_runtime(_url: str) -> None:
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
