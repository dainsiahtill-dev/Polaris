"""Application lifespan: startup order, shutdown order and failure cleanup.

The factory settlement runtime is retired, so the lifespan owns the managed
NATS runtime, the file-event publisher, the context receipt lookup and the
workspace fact stream. These scenarios pin that order: the managed runtime is
ready before the application admits requests, every cleanup callback runs even
when one of them fails, and a failed startup releases what it acquired.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamError,
)
from polaris.delivery.http.app_factory import lifespan


class ExpectedLifespanError(RuntimeError):
    """Sentinel used to verify exception-path shutdown."""


def _app(workspace: Path | None, *, nats_enabled: bool = True, nats_required: bool = True) -> FastAPI:
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        workspace=str(workspace.resolve()) if workspace is not None else "",
        nats=SimpleNamespace(
            enabled=nats_enabled,
            required=nats_required,
            url="nats://127.0.0.1:4222",
        ),
    )
    return app


def _install_lifespan_fakes(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
) -> dict[str, Any]:
    import polaris.bootstrap.assembly as assembly
    import polaris.cells.events.fact_stream.public as fact_stream_public
    import polaris.cells.instances.public.service as instance_service
    import polaris.cells.resident.autonomy.public.service as resident_service
    import polaris.delivery.http.resident_autotick as resident_autotick
    import polaris.infrastructure.db.repositories.accel_session_receipt_store as receipt_store
    import polaris.infrastructure.di.container as container_module
    import polaris.infrastructure.log_pipeline.jetstream_publisher as log_publisher
    import polaris.infrastructure.messaging as messaging
    import polaris.infrastructure.messaging.nats.jetstream_admin as jetstream_admin
    import polaris.infrastructure.messaging.nats.server_runtime as nats_runtime
    import polaris.kernelone.process as process

    startup_options: dict[str, Any] = {}

    async def get_container() -> object:
        events.append("container.get")
        return object()

    def reset_container() -> None:
        events.append("container.reset")

    def assemble_core_services(container: object, *, settings: object) -> None:
        del container, settings
        events.append("assembly")

    async def ensure_local_nats_runtime(url: str, *, startup_timeout_seconds: float, allow_autostart: bool) -> None:
        assert url == "nats://127.0.0.1:4222"
        assert startup_timeout_seconds == 60.0
        assert allow_autostart is True
        events.append("nats.server.start")

    async def shutdown_local_nats_runtime() -> None:
        events.append("nats.server.stop")

    async def close_default_client() -> None:
        events.append("nats.client.stop")

    async def get_default_client() -> object:
        events.append("nats.client.connect")
        return SimpleNamespace(is_connected=True)

    async def ensure_stream_exists(_admin: object) -> bool:
        events.append("nats.stream.ensure")
        return True

    def bootstrap_fact_stream_workspace(
        command: BootstrapFactStreamWorkspaceCommandV1,
    ) -> None:
        assert command.maintenance_reason == "http_app_lifespan_startup"
        startup_options["fact_stream_workspace"] = command.workspace
        startup_options["fact_stream_streams"] = command.streams
        events.append("fact_stream.bootstrap")

    monkeypatch.setattr(container_module, "get_container", get_container)
    monkeypatch.setattr(container_module, "reset_container", reset_container)
    monkeypatch.setattr(assembly, "assemble_core_services", assemble_core_services)
    monkeypatch.setattr(nats_runtime, "ensure_local_nats_runtime", ensure_local_nats_runtime)
    monkeypatch.setattr(nats_runtime, "shutdown_local_nats_runtime", shutdown_local_nats_runtime)
    monkeypatch.setattr(messaging, "close_default_client", close_default_client)
    monkeypatch.setattr(messaging, "get_default_client", get_default_client)
    monkeypatch.setattr(jetstream_admin.JetStreamAdmin, "ensure_stream_exists", ensure_stream_exists)
    monkeypatch.setattr(
        fact_stream_public,
        "bootstrap_fact_stream_workspace",
        bootstrap_fact_stream_workspace,
    )
    monkeypatch.setattr(
        instance_service,
        "maybe_start_instance_watchdog",
        lambda: None,
    )
    monkeypatch.setattr(
        resident_autotick,
        "maybe_start_resident_autotick",
        lambda workspace: None,
    )
    monkeypatch.setattr(
        resident_service,
        "reset_resident_services",
        lambda: events.append("resident.reset"),
    )
    monkeypatch.setattr(
        receipt_store,
        "install_context_retrieve_receipt_lookup",
        lambda: events.append("receipt.install"),
    )
    monkeypatch.setattr(
        receipt_store,
        "clear_context_retrieve_receipt_lookup",
        lambda: events.append("receipt.clear"),
    )
    monkeypatch.setattr(
        log_publisher,
        "install_file_event_broadcaster_publisher",
        lambda: events.append("publisher.install"),
    )
    monkeypatch.setattr(
        log_publisher,
        "clear_file_event_broadcaster_publisher",
        lambda: events.append("publisher.clear"),
    )
    monkeypatch.setattr(
        log_publisher,
        "shutdown_log_jetstream_publisher",
        lambda: events.append("publisher.stop"),
    )
    monkeypatch.setattr(process, "terminate_external_loop_pm_processes", lambda workspace: [])
    monkeypatch.setenv("KERNELONE_TOKEN", "test-token")
    return startup_options


@pytest.mark.asyncio
async def test_lifespan_starts_nats_before_the_app_runs_and_stops_the_client_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    _install_lifespan_fakes(monkeypatch, events)
    app = _app(tmp_path)

    async with lifespan(app):
        events.append("app.running")

    assert events.index("nats.server.start") < events.index("fact_stream.bootstrap")
    assert events.index("fact_stream.bootstrap") < events.index("app.running")
    assert events.index("app.running") < events.index("receipt.clear")
    assert events.index("receipt.clear") < events.index("publisher.clear")
    assert events.index("publisher.clear") < events.index("publisher.stop")
    assert events.index("publisher.stop") < events.index("nats.client.stop")
    assert events.index("nats.client.stop") < events.index("nats.server.stop")


@pytest.mark.asyncio
async def test_lifespan_exception_still_stops_the_client_before_the_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    _install_lifespan_fakes(monkeypatch, events)
    app = _app(tmp_path)

    with pytest.raises(ExpectedLifespanError):
        async with lifespan(app):
            raise ExpectedLifespanError("request loop failed")

    assert events.index("receipt.clear") < events.index("nats.client.stop")
    assert events.index("nats.client.stop") < events.index("nats.server.stop")


@pytest.mark.asyncio
async def test_lifespan_cleanup_oserrors_log_and_run_every_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import polaris.infrastructure.db.repositories.accel_session_receipt_store as receipt_store
    import polaris.infrastructure.messaging as messaging

    events: list[str] = []
    _install_lifespan_fakes(monkeypatch, events)

    def fail_clear_context_retrieve_receipt_lookup() -> None:
        events.append("receipt.clear.oserror")
        raise OSError("receipt lookup is unavailable")

    async def fail_close_default_client() -> None:
        events.append("nats.client.stop.oserror")
        raise OSError("NATS client is unavailable")

    monkeypatch.setattr(
        receipt_store,
        "clear_context_retrieve_receipt_lookup",
        fail_clear_context_retrieve_receipt_lookup,
    )
    monkeypatch.setattr(messaging, "close_default_client", fail_close_default_client)

    with caplog.at_level(logging.ERROR, logger="polaris.delivery.http.app_factory"):
        async with lifespan(_app(tmp_path)):
            events.append("app.running")

    assert events.index("receipt.clear.oserror") < events.index("publisher.clear")
    assert events.index("publisher.clear") < events.index("publisher.stop")
    assert events.index("publisher.stop") < events.index("nats.client.stop.oserror")
    assert events.index("nats.client.stop.oserror") < events.index("nats.server.stop")
    assert "Lifespan cleanup failed for context receipt lookup" in caplog.text
    assert "Lifespan cleanup failed for default NATS client" in caplog.text


@pytest.mark.asyncio
async def test_lifespan_cleanup_oserrors_preserve_application_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import polaris.infrastructure.db.repositories.accel_session_receipt_store as receipt_store
    import polaris.infrastructure.messaging as messaging

    events: list[str] = []
    _install_lifespan_fakes(monkeypatch, events)

    def fail_clear_context_retrieve_receipt_lookup() -> None:
        events.append("receipt.clear.oserror")
        raise OSError("receipt lookup is unavailable")

    async def fail_close_default_client() -> None:
        events.append("nats.client.stop.oserror")
        raise OSError("NATS client is unavailable")

    monkeypatch.setattr(
        receipt_store,
        "clear_context_retrieve_receipt_lookup",
        fail_clear_context_retrieve_receipt_lookup,
    )
    monkeypatch.setattr(messaging, "close_default_client", fail_close_default_client)

    with pytest.raises(ExpectedLifespanError, match="request loop failed"):
        async with lifespan(_app(tmp_path)):
            raise ExpectedLifespanError("request loop failed")

    assert events.index("receipt.clear.oserror") < events.index("publisher.clear")
    assert events.index("publisher.clear") < events.index("publisher.stop")
    assert events.index("publisher.stop") < events.index("nats.client.stop.oserror")
    assert events.index("nats.client.stop.oserror") < events.index("nats.server.stop")


@pytest.mark.asyncio
async def test_lifespan_disabled_nats_keeps_startup_without_a_managed_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    _install_lifespan_fakes(monkeypatch, events)
    app = _app(tmp_path, nats_enabled=False, nats_required=True)

    async with lifespan(app):
        events.append("app.running")

    assert "nats.server.start" not in events
    assert events.index("fact_stream.bootstrap") < events.index("app.running")
    assert events.index("app.running") < events.index("nats.client.stop")
    assert events.index("nats.client.stop") < events.index("nats.server.stop")


@pytest.mark.asyncio
async def test_lifespan_empty_workspace_does_not_bootstrap_the_fact_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _install_lifespan_fakes(monkeypatch, events)
    app = _app(None)

    async with lifespan(app):
        events.append("app.running")

    assert "fact_stream.bootstrap" not in events
    assert events.index("app.running") < events.index("nats.client.stop")


@pytest.mark.asyncio
async def test_lifespan_repeats_explicit_provision_for_each_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    _install_lifespan_fakes(monkeypatch, events)
    app = _app(tmp_path)

    async with lifespan(app):
        pass
    async with lifespan(app):
        pass

    assert events.count("fact_stream.bootstrap") == 2
    assert events.count("nats.server.start") == 2


@pytest.mark.asyncio
async def test_lifespan_fails_closed_before_admission_when_authority_is_unsafe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import polaris.cells.events.fact_stream.public as fact_stream_public

    events: list[str] = []
    _install_lifespan_fakes(monkeypatch, events)

    def fail_bootstrap(_command: object) -> None:
        events.append("fact_stream.bootstrap.failed")
        raise FactStreamError(
            "authority binding does not match the workspace",
            code="lock_anchor_binding_mismatch",
        )

    monkeypatch.setattr(
        fact_stream_public,
        "bootstrap_fact_stream_workspace",
        fail_bootstrap,
    )

    with pytest.raises(FactStreamError) as exc_info:
        async with lifespan(_app(tmp_path)):
            pass

    assert exc_info.value.code == "lock_anchor_binding_mismatch"
    assert "app.running" not in events


@pytest.mark.asyncio
async def test_lifespan_assembly_failure_cleans_installed_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import polaris.bootstrap.assembly as assembly

    events: list[str] = []
    _install_lifespan_fakes(monkeypatch, events)

    def fail_assembly(_container: object, *, settings: object) -> None:
        del settings
        events.append("assembly.failed")
        raise RuntimeError("assembly failure")

    monkeypatch.setattr(assembly, "assemble_core_services", fail_assembly)

    with pytest.raises(RuntimeError, match="assembly failure"):
        async with lifespan(_app(tmp_path)):
            pass

    assert events.index("assembly.failed") < events.index("receipt.clear")
    assert events.index("receipt.clear") < events.index("publisher.clear")
    assert events.index("publisher.clear") < events.index("nats.client.stop")
    assert events.index("nats.client.stop") < events.index("nats.server.stop")
