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
    import polaris.cells.factory.pipeline.public as factory_public
    import polaris.cells.instances.public.service as instance_service
    import polaris.cells.resident.autonomy.public.service as resident_service
    import polaris.delivery.http.resident_autotick as resident_autotick
    import polaris.infrastructure.db.repositories.accel_session_receipt_store as receipt_store
    import polaris.infrastructure.di.container as container_module
    import polaris.infrastructure.log_pipeline.jetstream_publisher as log_publisher
    import polaris.infrastructure.messaging as messaging
    import polaris.infrastructure.messaging.nats.server_runtime as nats_runtime
    import polaris.kernelone.process as process

    settlement_options: dict[str, Any] = {}

    async def get_container() -> object:
        events.append("container.get")
        return object()

    def reset_container() -> None:
        events.append("container.reset")

    def assemble_core_services(container: object, *, settings: object) -> None:
        del container, settings
        events.append("assembly")

    async def ensure_local_nats_runtime(url: str) -> None:
        assert url == "nats://127.0.0.1:4222"
        events.append("nats.server.start")

    async def shutdown_local_nats_runtime() -> None:
        events.append("nats.server.stop")

    async def close_default_client() -> None:
        events.append("nats.client.stop")

    async def start_factory_settlement_runtime(
        workspace: str,
        *,
        enable_wake_bridge: bool,
        wake_bridge_required: bool,
    ) -> object:
        settlement_options.update(
            {
                "workspace": workspace,
                "enable_wake_bridge": enable_wake_bridge,
                "wake_bridge_required": wake_bridge_required,
            }
        )
        events.append("settlement.start")
        return object()

    def bootstrap_fact_stream_workspace(
        command: BootstrapFactStreamWorkspaceCommandV1,
    ) -> None:
        assert command.maintenance_reason == "http_app_lifespan_startup"
        assert command.streams == (
            "execution.control_plane",
            "factory.settlement",
            "resident.cycle.events",
            "roles.kernel.turn_outcomes",
            "task_market.events",
            "task_runtime.execution",
            "taskboard.terminal.events",
        )
        events.append("fact_stream.bootstrap")

    async def stop_factory_settlement_runtime(workspace: str) -> bool:
        settlement_options["stopped_workspace"] = workspace
        events.append("settlement.stop")
        return True

    monkeypatch.setattr(container_module, "get_container", get_container)
    monkeypatch.setattr(container_module, "reset_container", reset_container)
    monkeypatch.setattr(assembly, "assemble_core_services", assemble_core_services)
    monkeypatch.setattr(nats_runtime, "ensure_local_nats_runtime", ensure_local_nats_runtime)
    monkeypatch.setattr(nats_runtime, "shutdown_local_nats_runtime", shutdown_local_nats_runtime)
    monkeypatch.setattr(messaging, "close_default_client", close_default_client)
    monkeypatch.setattr(
        factory_public,
        "start_factory_settlement_runtime",
        start_factory_settlement_runtime,
    )
    monkeypatch.setattr(
        factory_public,
        "stop_factory_settlement_runtime",
        stop_factory_settlement_runtime,
    )
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
    return settlement_options


@pytest.mark.asyncio
async def test_lifespan_starts_after_nats_and_stops_before_nats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    options = _install_lifespan_fakes(monkeypatch, events)
    app = _app(tmp_path)

    async with lifespan(app):
        events.append("app.running")

    workspace = str(tmp_path.resolve())
    assert options == {
        "workspace": workspace,
        "enable_wake_bridge": True,
        "wake_bridge_required": True,
        "stopped_workspace": workspace,
    }
    assert events.index("nats.server.start") < events.index("settlement.start")
    assert events.index("fact_stream.bootstrap") < events.index("settlement.start")
    assert events.index("settlement.start") < events.index("app.running")
    assert events.index("app.running") < events.index("settlement.stop")
    assert events.index("settlement.stop") < events.index("nats.client.stop")
    assert events.index("nats.client.stop") < events.index("nats.server.stop")


@pytest.mark.asyncio
async def test_lifespan_exception_still_stops_settlement_before_nats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    _install_lifespan_fakes(monkeypatch, events)
    app = _app(tmp_path)

    with pytest.raises(ExpectedLifespanError):
        async with lifespan(app):
            raise ExpectedLifespanError("request loop failed")

    assert events.index("settlement.stop") < events.index("nats.client.stop")
    assert events.index("nats.client.stop") < events.index("nats.server.stop")


@pytest.mark.asyncio
async def test_lifespan_settlement_shutdown_oserror_preserves_body_error_and_runs_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import polaris.cells.factory.pipeline.public as factory_public

    events: list[str] = []
    _install_lifespan_fakes(monkeypatch, events)

    async def fail_stop_factory_settlement_runtime(workspace: str) -> bool:
        assert workspace == str(tmp_path.resolve())
        events.append("settlement.stop.oserror")
        raise OSError("settlement socket is unavailable")

    monkeypatch.setattr(
        factory_public,
        "stop_factory_settlement_runtime",
        fail_stop_factory_settlement_runtime,
    )

    with pytest.raises(ExpectedLifespanError, match="request loop failed"):
        async with lifespan(_app(tmp_path)):
            raise ExpectedLifespanError("request loop failed")

    assert events.index("settlement.stop.oserror") < events.index("receipt.clear")
    assert events.index("receipt.clear") < events.index("publisher.clear")
    assert events.index("publisher.clear") < events.index("nats.client.stop")
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
async def test_lifespan_disabled_nats_keeps_startup_replay_without_bridge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    options = _install_lifespan_fakes(monkeypatch, events)
    app = _app(tmp_path, nats_enabled=False, nats_required=True)

    async with lifespan(app):
        events.append("app.running")

    assert "nats.server.start" not in events
    assert options["enable_wake_bridge"] is False
    assert options["wake_bridge_required"] is False
    assert events.index("settlement.start") < events.index("app.running")
    assert events.index("settlement.stop") < events.index("nats.server.stop")


@pytest.mark.asyncio
async def test_lifespan_empty_workspace_does_not_create_settlement_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    options = _install_lifespan_fakes(monkeypatch, events)
    app = _app(None)

    async with lifespan(app):
        events.append("app.running")

    assert options == {}
    assert "settlement.start" not in events
    assert "settlement.stop" not in events
    assert "fact_stream.bootstrap" not in events


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
    assert events.count("settlement.start") == 2


@pytest.mark.asyncio
async def test_lifespan_fails_closed_before_settlement_when_authority_is_unsafe(
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
    assert "settlement.start" not in events


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
