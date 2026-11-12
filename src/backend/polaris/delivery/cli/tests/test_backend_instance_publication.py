"""Published address of a backend CLI registered instance.

The registry advertises the address a consumer can dial. A wildcard bind
address is not dialable, so registering with ``--host 0.0.0.0`` must not
advertise ``0.0.0.0``: it publishes loopback, or the configured public
address when a deployment names one.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest
from polaris.delivery.cli import backend as backend_cli


def _args(tmp_path: Path, *, host: str) -> argparse.Namespace:
    workspace = tmp_path / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    return argparse.Namespace(
        register_instance=True,
        workspace=str(workspace),
        runtime_root="",
        instance_id="main",
        instance_name="main",
        kind="main",
        host=host,
        port=51000,
        frontend_port=5173,
        token="test-token",
        reload=False,
    )


def _register(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, host: str) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    class FakeRegistry:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def save(self, record: Any) -> Any:
            captured["record"] = record
            return record

    monkeypatch.setattr("polaris.cells.instances.internal.service.InstanceRegistry", FakeRegistry)
    monkeypatch.setattr(backend_cli, "_find_polaris_root", lambda _cwd: tmp_path / "polaris-root")
    backend_cli._register_instance(_args(tmp_path, host=host))
    record = captured["record"]
    return {
        "backend_url": record.backend_url,
        "frontend_url": record.frontend_url,
    }


def test_register_instance_publishes_loopback_for_a_wildcard_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("KERNELONE_INSTANCE_PUBLIC_HOST", raising=False)

    published = _register(tmp_path, monkeypatch, host="0.0.0.0")

    assert published["backend_url"] == "http://127.0.0.1:51000"
    assert published["frontend_url"] == "http://127.0.0.1:5173"


def test_register_instance_publishes_the_configured_public_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_INSTANCE_PUBLIC_HOST", "192.168.10.2")

    published = _register(tmp_path, monkeypatch, host="0.0.0.0")

    assert published["backend_url"] == "http://192.168.10.2:51000"
    assert published["frontend_url"] == "http://192.168.10.2:5173"


def test_register_instance_without_a_registration_is_a_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_INSTANCE_PUBLIC_HOST", "192.168.10.2")
    monkeypatch.setattr(
        "polaris.cells.instances.internal.service.InstanceRegistry",
        lambda *_a, **_k: pytest.fail("registration must not touch the registry"),
    )

    args = _args(tmp_path, host="0.0.0.0")
    args.register_instance = False
    backend_cli._register_instance(args)
