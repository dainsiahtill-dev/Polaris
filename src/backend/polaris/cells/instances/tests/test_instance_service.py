from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.cells.instances.internal import service as instance_service
from polaris.cells.instances.internal.service import (
    InstanceRecord,
    InstanceRegistry,
    InstanceSupervisor,
    publish_instances_update,
)


def _make_polaris_root(tmp_path: Path) -> Path:
    root = tmp_path / "polaris-root"
    (root / "src" / "backend" / "polaris").mkdir(parents=True)
    (root / "package.json").write_text('{"scripts":{"dev:renderer":"vite"}}\n', encoding="utf-8")
    return root


def test_instance_registry_round_trip(tmp_path: Path) -> None:
    root = _make_polaris_root(tmp_path)
    workspace = tmp_path / "project-a"
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    record = registry.save(
        InstanceRecord(
            instance_id="project-a",
            name="Project A",
            kind="project",
            polaris_root=str(root),
            workspace=str(workspace.resolve()),
            runtime_root=str((tmp_path / "instances" / "project-a" / "runtime").resolve()),
            backend_port=59901,
            frontend_port=59902,
            backend_url="http://127.0.0.1:59901",
            frontend_url="http://127.0.0.1:59902",
            token="test-token",
            start_frontend=False,
        )
    )

    assert record.instance_id == "project-a"
    assert registry.get("project-a") is not None

    data = json.loads((tmp_path / "instances" / "registry.json").read_text(encoding="utf-8"))
    assert data["instances"][0]["instance_id"] == "project-a"


def test_start_instance_builds_backend_and_frontend_processes(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = _make_polaris_root(tmp_path)
    calls: list[dict[str, Any]] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append({"command": command, **kwargs})
        return FakeProcess(61000 + len(calls))

    monkeypatch.setattr(instance_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: bool(pid))
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))

    supervisor = InstanceSupervisor(InstanceRegistry(tmp_path / "instances", publish_events=False))
    record = supervisor.start_instance(
        {
            "instance_id": "bench-l1-01",
            "kind": "bench_project",
            "polaris_root": str(root),
            "workspace": str(tmp_path / "bench" / "L1-01"),
            "backend_port": 59911,
            "frontend_port": 59912,
            "backend_reload": True,
            "start_frontend": True,
            "bench": {"level": 1, "project_id": "L1-01"},
        }
    )

    assert record["status"] == "running"
    assert record["kind"] == "bench_project"
    assert len(calls) == 2
    backend_call = calls[0]
    frontend_call = calls[1]
    assert backend_call["command"][:3] == [instance_service.sys.executable, "-m", "polaris.delivery.cli.backend"]
    assert "--reload" in backend_call["command"]
    assert backend_call["env"]["KERNELONE_CORS_ORIGINS"].startswith("http://127.0.0.1:59912")
    assert frontend_call["command"][:3] == ["npm", "run", "dev:renderer"]
    assert frontend_call["env"]["VITE_BACKEND_URL"] == "http://127.0.0.1:59911"
    assert frontend_call["env"]["VITE_POLARIS_INSTANCE_ID"] == "bench-l1-01"


def test_instance_update_event_redacts_token(tmp_path: Path, monkeypatch: Any) -> None:
    published: list[dict[str, Any]] = []

    class FakePublisher:
        def publish(self, *, subject: str, payload: dict[str, Any]) -> bool:
            published.append({"subject": subject, "payload": payload})
            return True

    monkeypatch.setattr(
        "polaris.infrastructure.log_pipeline.jetstream_publisher.get_log_jetstream_publisher",
        lambda: FakePublisher(),
    )
    record = InstanceRecord(
        instance_id="project-a",
        name="Project A",
        kind="project",
        polaris_root=str(_make_polaris_root(tmp_path)),
        workspace=str((tmp_path / "project-a").resolve()),
        runtime_root=str((tmp_path / "runtime").resolve()),
        backend_port=59901,
        frontend_port=59902,
        backend_url="http://127.0.0.1:59901",
        frontend_url="http://127.0.0.1:59902",
        token="secret-token",
    )

    assert publish_instances_update(action="saved", record=record, records=[record]) is True
    payload = published[0]["payload"]
    assert published[0]["subject"] == "hp.runtime.instances.status.instances"
    assert payload["channel"] == "status.instances"
    assert payload["payload"]["instance"]["instance_id"] == "project-a"
    assert "token" not in payload["payload"]["instance"]
    assert "token" not in payload["payload"]["instances"][0]


def test_external_backend_without_pid_is_observed(tmp_path: Path, monkeypatch: Any) -> None:
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    supervisor = InstanceSupervisor(registry)
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))
    registry.save(
        InstanceRecord(
            instance_id="bench-observed",
            name="Bench Observed",
            kind="bench_project",
            polaris_root=str(_make_polaris_root(tmp_path)),
            workspace=str((tmp_path / "workspace").resolve()),
            runtime_root=str((tmp_path / "runtime").resolve()),
            backend_port=59901,
            frontend_port=0,
            backend_url="http://127.0.0.1:59901",
            frontend_url="",
            token="token",
            backend_pid=None,
            frontend_pid=None,
            start_frontend=False,
            status="observed",
        )
    )

    health = supervisor.health("bench-observed")

    assert health["status"] == "observed"
    assert health["metadata"]["backend_health"] == "ok"


def test_registered_external_frontend_url_counts_as_alive(tmp_path: Path, monkeypatch: Any) -> None:
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    supervisor = InstanceSupervisor(registry)

    def fake_http_ok(url: str, _token: str) -> bool:
        return url in {"http://127.0.0.1:59901/health", "http://127.0.0.1:59902"}

    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: pid == 61001)
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(fake_http_ok))
    registry.save(
        InstanceRecord(
            instance_id="main",
            name="Main",
            kind="development",
            polaris_root=str(_make_polaris_root(tmp_path)),
            workspace=str((tmp_path / "workspace").resolve()),
            runtime_root=str((tmp_path / "runtime").resolve()),
            backend_port=59901,
            frontend_port=59902,
            backend_url="http://127.0.0.1:59901",
            frontend_url="http://127.0.0.1:59902",
            token="token",
            backend_pid=61001,
            frontend_pid=None,
            start_frontend=False,
            status="running",
        )
    )

    health = supervisor.health("main")

    assert health["status"] == "running"
    assert health["frontend_alive"] is True
    assert health["metadata"]["frontend_health"] == "ok"
