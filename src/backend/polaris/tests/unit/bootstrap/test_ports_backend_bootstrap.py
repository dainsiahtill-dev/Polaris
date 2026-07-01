"""Tests for polaris.bootstrap.ports.backend_bootstrap."""

from __future__ import annotations

from typing import Any, get_type_hints

from polaris.bootstrap.contracts.backend_launch import BackendLaunchRequest, BackendLaunchResult
from polaris.bootstrap.ports.backend_bootstrap import BackendBootstrapPort, BootstrapPort


class TestBackendBootstrapPort:
    def test_is_runtime_checkable(self) -> None:
        assert hasattr(BackendBootstrapPort, "__subclasshook__")

    def test_bootstrap_port_alias(self) -> None:
        assert BootstrapPort is BackendBootstrapPort

    def test_launch_contract_annotations_do_not_degrade_to_any(self) -> None:
        hints = get_type_hints(BackendBootstrapPort.bootstrap)

        assert hints["request"] is BackendLaunchRequest
        assert hints["return"] is BackendLaunchResult
        assert hints["request"] is not Any
        assert hints["return"] is not Any
