"""Tests for polaris.bootstrap.uvicorn_server."""

from __future__ import annotations

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
