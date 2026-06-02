"""Tests for polaris.bootstrap.uvicorn_server."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest
from polaris.bootstrap.uvicorn_server import UvicornServerHandle


class TestUvicornServerHandle:
    def test_init_defaults(self) -> None:
        handle = UvicornServerHandle(app="app")
        assert handle.host == "127.0.0.1"
        assert handle.port == 8000
        assert handle.log_level == "info"
        assert handle._server is None
        assert handle._task is None

    def test_init_custom(self) -> None:
        handle = UvicornServerHandle(app="app", host="0.0.0.0", port=8080, log_level="debug")
        assert handle.host == "0.0.0.0"
        assert handle.port == 8080
        assert handle.log_level == "debug"

    def test_is_running_before_start(self) -> None:
        handle = UvicornServerHandle(app="app")
        assert handle.is_running is False

    def test_pid_returns_int(self) -> None:
        handle = UvicornServerHandle(app="app")
        pid = handle.pid
        assert isinstance(pid, int)
        assert pid > 0

    @pytest.mark.asyncio
    async def test_start_waits_until_server_is_listening(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return only after uvicorn reports a listening server."""

        class FakeServer:
            def __init__(self, _config: object) -> None:
                self.started = False
                self.should_exit = False

            async def serve(self) -> None:
                await asyncio.sleep(0.05)
                self.started = True
                while not self.should_exit:
                    await asyncio.sleep(0.01)

        monkeypatch.setitem(
            sys.modules,
            "uvicorn",
            SimpleNamespace(Config=lambda **kwargs: kwargs, Server=FakeServer),
        )

        handle = UvicornServerHandle(app="app")

        await handle.start(startup_timeout=1.0)

        assert handle.is_running is True
        assert bool(getattr(handle._server, "started", False)) is True
        await handle.shutdown()

    @pytest.mark.asyncio
    async def test_start_raises_when_server_task_exits_before_listening(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should surface bind failures before bootstrap emits backend_started."""

        class FakeServer:
            started = False

            def __init__(self, _config: object) -> None:
                self.should_exit = False

            async def serve(self) -> None:
                raise SystemExit(1)

        monkeypatch.setitem(
            sys.modules,
            "uvicorn",
            SimpleNamespace(Config=lambda **kwargs: kwargs, Server=FakeServer),
        )

        handle = UvicornServerHandle(app="app", port=58123)

        with pytest.raises(OSError, match="uvicorn failed to start"):
            await handle.start(startup_timeout=1.0)
