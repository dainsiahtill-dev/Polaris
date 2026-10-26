"""Lifecycle tests for the shared synthetic native HTTP provider endpoint.

The endpoint resource is a real loopback HTTP server plus a real serving
thread. These tests exercise the actual resource lifecycle: normal release,
release after a setup failure, and release when the serving thread never
starts. Only the injected failure is a controlled substitute; every socket,
thread and server involved is really created and really closed.

No paid LLM call happens here: the endpoint answers with its own bounded local
payloads.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.tests import native_endpoint_fixture as fixture
from polaris.cells.factory.pipeline.tests.native_endpoint_fixture import (
    NativeEndpoint,
    native_endpoint_context,
)


class _ControlledSetupError(RuntimeError):
    """Declared failure injected at one controlled setup step."""


class _ControlledThreadStartError(RuntimeError):
    """Declared failure injected at the serving thread start."""


def _live_threads() -> set[threading.Thread]:
    return set(threading.enumerate())


def _new_live_threads(before: set[threading.Thread]) -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread not in before and thread.is_alive()]


def _provider_port(tmp_path: Path) -> int:
    """Read the bound port from the provider configuration the resource wrote."""
    config = json.loads((tmp_path / "llm-config.json").read_text(encoding="utf-8"))
    base_url = config["providers"]["runtime-fixture"]["base_url"]
    return int(base_url.rsplit(":", 1)[1].split("/", 1)[0])


def test_native_endpoint_context_closes_listener_and_thread_on_normal_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normal exit really closes the listener and stops the serving thread."""
    threads_before = _live_threads()
    with native_endpoint_context(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        outcome="accept",
        endpoint_class=NativeEndpoint,
    ) as endpoint:
        port = _provider_port(tmp_path)
        address = ("127.0.0.1", port)

        body = json.dumps(
            {"model": "gpt-4.1", "messages": [{"role": "system", "content": "polaris.role_identity.v1:pm"}]}
        ).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("POST", "/v1/chat/completions", body=body, headers={"Content-Length": str(len(body))})
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()

        assert response.status == 200
        assert payload["choices"][0]["finish_reason"] == "stop"
        assert len(endpoint.requests) == 1
        assert endpoint.errors == []

        serving = _new_live_threads(threads_before)
        assert len(serving) == 1, "the real serving thread must be running while the context is open"

    assert not _new_live_threads(threads_before), "the serving thread must stop when the context exits"
    with pytest.raises(OSError):
        socket.create_connection(address, timeout=1)


def test_native_endpoint_context_releases_resources_when_setup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after the listener exists must still release listener and thread.

    This is the Root counter-example path: the controlled fault replaces only
    the environment setup call, while the real server, socket and thread are
    created by the resource itself.
    """
    threads_before = _live_threads()
    created_servers: list[Any] = []
    original_server = fixture.ThreadingHTTPServer

    def recording_server(*args: Any, **kwargs: Any) -> Any:
        server = original_server(*args, **kwargs)
        created_servers.append(server)
        return server

    def failing_setenv(name: str, value: str, prepend: str | None = None) -> None:
        raise _ControlledSetupError("controlled setup failure after the listener was allocated")

    original_setenv = monkeypatch.setenv
    monkeypatch.setattr(fixture, "ThreadingHTTPServer", recording_server)
    monkeypatch.setenv = failing_setenv  # type: ignore[method-assign]
    try:
        with (
            pytest.raises(_ControlledSetupError),
            native_endpoint_context(
                tmp_path=tmp_path,
                monkeypatch=monkeypatch,
                outcome="accept",
                endpoint_class=NativeEndpoint,
            ),
        ):
            raise AssertionError("the controlled setup fault must fire before the body runs")
    finally:
        monkeypatch.setenv = original_setenv  # type: ignore[method-assign]

    assert len(created_servers) == 1
    server = created_servers[0]
    assert server.socket.fileno() < 0, "the listener socket must be closed after the setup failure"
    assert not _new_live_threads(threads_before)
    with pytest.raises(OSError):
        socket.create_connection(server.server_address, timeout=1)


def test_native_endpoint_context_releases_listener_when_serving_thread_does_not_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A thread.start() failure must close the listener without a blocking shutdown.

    The controlled substitute replaces only the thread start call. The socket,
    the server and the serve target stay real, and shutdown() must not be
    called for a serve loop that never ran.
    """
    threads_before = _live_threads()
    created_servers: list[Any] = []
    shutdown_calls: list[bool] = []
    original_server = fixture.ThreadingHTTPServer

    def recording_server(*args: Any, **kwargs: Any) -> Any:
        server = original_server(*args, **kwargs)
        created_servers.append(server)
        original_shutdown = server.shutdown

        def tracked_shutdown() -> None:
            shutdown_calls.append(True)
            original_shutdown()

        server.shutdown = tracked_shutdown  # type: ignore[method-assign]
        return server

    class _FailingThread(threading.Thread):
        def start(self) -> None:
            raise _ControlledThreadStartError("controlled thread start failure")

    monkeypatch.setattr(fixture, "ThreadingHTTPServer", recording_server)
    monkeypatch.setattr(fixture.threading, "Thread", _FailingThread)
    with (
        pytest.raises(_ControlledThreadStartError),
        native_endpoint_context(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            outcome="accept",
            endpoint_class=NativeEndpoint,
        ),
    ):
        raise AssertionError("the controlled thread start failure must fire before the body runs")

    assert len(created_servers) == 1
    server = created_servers[0]
    assert shutdown_calls == [], "shutdown() must not run for a serve loop that never started"
    assert server.socket.fileno() < 0, "the listener socket must be closed after the thread start failure"
    assert not _new_live_threads(threads_before)
    with pytest.raises(OSError):
        socket.create_connection(server.server_address, timeout=1)
