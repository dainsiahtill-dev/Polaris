"""Tests for polaris.bootstrap.ports.backend_bootstrap."""

from __future__ import annotations

from typing import Protocol

from polaris.bootstrap.ports.backend_bootstrap import BackendBootstrapPort, BootstrapPort


class TestBackendBootstrapPort:
    def test_is_runtime_checkable(self) -> None:
        assert hasattr(BackendBootstrapPort, "__subclasshook__")

    def test_bootstrap_port_alias(self) -> None:
        assert BootstrapPort is BackendBootstrapPort
