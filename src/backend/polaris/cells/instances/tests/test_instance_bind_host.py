"""Instance bind and published address.

An instance is loopback-only by default, so its pages and API cannot be
reached from another host. A deployment whose launcher is consumed from
another host, for example the Windows host of a WSL2 workspace, needs the
instance to listen on a reachable address and to advertise the address a
consumer can actually dial.

Two addresses are therefore distinct: the bind address the process listens
on, and the published address the registry advertises. A wildcard bind is not
a dialable address, so it publishes loopback unless a public address is
configured.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.instances.internal import service as instance_service
from polaris.cells.instances.internal.service import InstanceRegistry, InstanceSupervisor


def _make_polaris_root(tmp_path: Path) -> Path:
    root = tmp_path / "polaris-root"
    (root / "src" / "backend" / "polaris").mkdir(parents=True)
    (root / "package.json").write_text('{"scripts":{"dev:renderer":"vite"}}\n', encoding="utf-8")
    return root


def _start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Start an instance without launching a real backend or renderer.

    The identity probes wait for a live backend process and a renderer
    command line, so they are disabled here: this test only asserts the
    spawn commands and the advertised addresses.
    """

    monkeypatch.setattr(instance_service, "_read_linux_process_text", lambda _pid: "")
    calls: list[dict[str, Any]] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append({"command": command, **kwargs})
        return FakeProcess(62000 + len(calls))

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: bool(pid))
    monkeypatch.setattr(instance_service, "is_port_free", lambda _port: True)
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))
    monkeypatch.setattr(InstanceSupervisor, "_wait_for_backend_identity", lambda _self, _record: None)
    monkeypatch.setattr(InstanceSupervisor, "_wait_for_frontend_identity", lambda _self, _record: None)
    monkeypatch.setattr(InstanceSupervisor, "_with_health", lambda _self, record, **_kwargs: record)

    supervisor = InstanceSupervisor(InstanceRegistry(tmp_path / "instances", publish_events=False))
    record = supervisor.start_instance(
        {
            "instance_id": "bench-l1-01",
            "kind": "bench_project",
            "polaris_root": str(_make_polaris_root(tmp_path)),
            "workspace": str(tmp_path / "bench" / "L1-01"),
            "backend_port": 59911,
            "frontend_port": 59912,
            "backend_reload": False,
            "start_frontend": True,
            "bench": {"level": 1, "project_id": "L1-01"},
        }
    )
    return dict(record), calls


def _backend_call(calls: list[dict[str, Any]]) -> dict[str, Any]:
    return next(call for call in calls if "polaris.delivery.cli.backend" in call["command"][:4])


def _frontend_call(calls: list[dict[str, Any]]) -> dict[str, Any]:
    return next(call for call in calls if call["command"][:3] == ["npm", "run", "dev:renderer"])


def test_instance_listens_on_loopback_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KERNELONE_INSTANCE_BIND_HOST", raising=False)
    monkeypatch.delenv("KERNELONE_INSTANCE_PUBLIC_HOST", raising=False)

    record, calls = _start(tmp_path, monkeypatch)

    backend = _backend_call(calls)
    assert backend["command"][backend["command"].index("--host") + 1] == "127.0.0.1"
    frontend = _frontend_call(calls)
    assert frontend["command"][frontend["command"].index("--host") + 1] == "127.0.0.1"
    assert record["backend_url"] == "http://127.0.0.1:59911"
    assert record["frontend_url"] == "http://127.0.0.1:59912"
    # A loopback instance may address its backend directly.
    assert frontend["env"]["VITE_BACKEND_URL"] == "http://127.0.0.1:59911"


def test_configured_bind_address_is_used_for_both_processes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_INSTANCE_BIND_HOST", "0.0.0.0")
    monkeypatch.delenv("KERNELONE_INSTANCE_PUBLIC_HOST", raising=False)

    record, calls = _start(tmp_path, monkeypatch)

    backend = _backend_call(calls)
    assert backend["command"][backend["command"].index("--host") + 1] == "0.0.0.0"
    frontend = _frontend_call(calls)
    assert frontend["command"][frontend["command"].index("--host") + 1] == "0.0.0.0"
    # A wildcard bind is not a dialable address, so it publishes loopback.
    assert record["backend_url"] == "http://127.0.0.1:59911"
    assert record["frontend_url"] == "http://127.0.0.1:59912"
    # The dev proxy must reach this instance's backend, not the main one.
    assert frontend["env"]["KERNELONE_BACKEND_PORT"] == "59911"
    assert frontend["env"]["VITE_BACKEND_PORT"] == "59911"


def test_published_address_is_advertised_and_keeps_the_instance_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_INSTANCE_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("KERNELONE_INSTANCE_PUBLIC_HOST", "192.168.10.2")

    record, calls = _start(tmp_path, monkeypatch)

    assert record["backend_url"] == "http://192.168.10.2:59911"
    assert record["frontend_url"] == "http://192.168.10.2:59912"
    frontend = _frontend_call(calls)
    # A published instance keeps the same-origin dev proxy: the connect-src
    # policy permits only loopback origins and the page origin.
    assert "VITE_BACKEND_URL" not in frontend["env"]
    assert "VITE_POLARIS_BACKEND_URL" not in frontend["env"]
    # The explicit instance binding stays complete.
    assert frontend["env"]["VITE_POLARIS_BACKEND_TOKEN"] == record["token"]
    assert frontend["env"]["VITE_POLARIS_INSTANCE_ID"] == "bench-l1-01"
    assert frontend["env"]["VITE_POLARIS_WORKSPACE"] == str((tmp_path / "bench" / "L1-01").resolve())
    backend = _backend_call(calls)
    assert "192.168.10.2:59912" in backend["env"]["KERNELONE_CORS_ORIGINS"]
