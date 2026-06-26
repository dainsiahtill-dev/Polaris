from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.instances.internal import service as instance_service
from polaris.cells.instances.internal.service import (
    InstanceRecord,
    InstanceRegistry,
    InstanceSupervisor,
    publish_instances_update,
)


@pytest.fixture(autouse=True)
def _disable_backend_identity_probe(monkeypatch: Any) -> None:
    monkeypatch.setattr(InstanceSupervisor, "_wait_for_backend_identity", lambda _self, _record: None)


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


def test_is_port_free_detects_bound_non_listening_socket() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind((instance_service.DEFAULT_HOST, 0))
        port = int(held.getsockname()[1])

        assert instance_service.is_port_free(port) is False


def test_current_backend_instance_cannot_stop_restart_or_delete_itself(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = _make_polaris_root(tmp_path)
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    supervisor = InstanceSupervisor(registry)
    record = registry.save(
        InstanceRecord(
            instance_id="main",
            name="Main Polaris Dev",
            kind="development",
            polaris_root=str(root),
            workspace=str(root),
            runtime_root=str((tmp_path / "runtime").resolve()),
            backend_port=instance_service.DEFAULT_BACKEND_PORT,
            frontend_port=instance_service.DEFAULT_FRONTEND_PORT,
            backend_url=f"http://127.0.0.1:{instance_service.DEFAULT_BACKEND_PORT}",
            frontend_url=f"http://127.0.0.1:{instance_service.DEFAULT_FRONTEND_PORT}",
            token="test-token",
            backend_pid=os.getpid(),
            frontend_pid=None,
            start_frontend=False,
            status="running",
        )
    )
    terminated: list[int] = []
    monkeypatch.setattr(
        InstanceSupervisor, "_terminate_pid", staticmethod(lambda pid: terminated.append(int(pid or 0)))
    )

    for operation in (
        lambda: supervisor.stop_instance(record.instance_id),
        lambda: supervisor.restart_instance(record.instance_id),
        lambda: supervisor.delete_instance(record.instance_id),
    ):
        try:
            operation()
        except RuntimeError as exc:
            assert "current backend instance" in str(exc)
        else:
            raise AssertionError("self-management operation should fail closed")

    assert terminated == []
    assert registry.get(record.instance_id) is not None


def test_list_instances_uses_fast_process_projection_without_http_probe(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = _make_polaris_root(tmp_path)
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    registry.save(
        InstanceRecord(
            instance_id="main",
            name="Main",
            kind="development",
            polaris_root=str(root),
            workspace=str(root),
            runtime_root=str((tmp_path / "runtime").resolve()),
            backend_port=instance_service.DEFAULT_BACKEND_PORT,
            frontend_port=instance_service.DEFAULT_FRONTEND_PORT,
            backend_url=f"http://127.0.0.1:{instance_service.DEFAULT_BACKEND_PORT}",
            frontend_url=f"http://127.0.0.1:{instance_service.DEFAULT_FRONTEND_PORT}",
            token="test-token",
            backend_pid=12345,
            frontend_pid=None,
            start_frontend=False,
            status="running",
        )
    )
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: pid == 12345)

    def fail_http_probe(_url: str, _token: str) -> bool:
        raise AssertionError("HTTP probe not allowed")

    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(fail_http_probe))

    records = InstanceSupervisor(registry).list_instances()

    assert records[0]["instance_id"] == "main"
    assert records[0]["status"] == "running"
    assert records[0]["metadata"]["backend_health"] == "process"
    assert records[0]["metadata"]["frontend_health"] == "disabled"


def test_refresh_instance_states_publishes_natural_process_death(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    published: list[dict[str, Any]] = []

    class FakePublisher:
        def publish(self, *, subject: str, payload: dict[str, Any]) -> bool:
            published.append({"subject": subject, "payload": payload})
            return True

    monkeypatch.setattr(
        "polaris.infrastructure.log_pipeline.jetstream_publisher.get_log_jetstream_publisher",
        lambda: FakePublisher(),
    )
    monkeypatch.setattr(instance_service, "is_process_alive", lambda _pid: False)
    root = _make_polaris_root(tmp_path)
    registry = InstanceRegistry(tmp_path / "instances", publish_events=True)
    registry.save(
        InstanceRecord(
            instance_id="dead-bench",
            name="Dead Bench",
            kind="bench_project",
            polaris_root=str(root),
            workspace=str((tmp_path / "bench" / "L1-07").resolve()),
            runtime_root=str((tmp_path / "bench" / "L1-07" / "runtime").resolve()),
            backend_port=59911,
            frontend_port=59912,
            backend_url="http://127.0.0.1:59911",
            frontend_url="http://127.0.0.1:59912",
            token="secret-token",
            backend_pid=61001,
            frontend_pid=61002,
            start_frontend=True,
            status="running",
            metadata={"backend_health": "ok", "frontend_health": "ok"},
        )
    )
    published.clear()

    changed = InstanceSupervisor(registry).refresh_instance_states()

    stored = registry.get("dead-bench")
    assert stored is not None
    assert changed[0]["instance_id"] == "dead-bench"
    assert stored.status == "stopped"
    assert stored.metadata["backend_health"] == "stopped"
    assert stored.metadata["frontend_health"] == "stopped"
    assert published[0]["subject"] == "hp.runtime.instances.status.instances"
    payload = published[0]["payload"]
    assert payload["payload"]["action"] == "health_changed"
    assert payload["payload"]["instance"]["instance_id"] == "dead-bench"
    assert "token" not in payload["payload"]["instance"]


def test_refresh_instance_states_keeps_recent_partial_startup_as_starting(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: pid == 61001)
    root = _make_polaris_root(tmp_path)
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    registry.save(
        InstanceRecord(
            instance_id="fresh-partial",
            name="Fresh Partial",
            kind="bench_project",
            polaris_root=str(root),
            workspace=str((tmp_path / "bench" / "L1-09").resolve()),
            runtime_root=str((tmp_path / "bench" / "L1-09" / "runtime").resolve()),
            backend_port=59931,
            frontend_port=59932,
            backend_url="http://127.0.0.1:59931",
            frontend_url="http://127.0.0.1:59932",
            token="secret-token",
            backend_pid=61001,
            frontend_pid=61002,
            start_frontend=True,
            status="starting",
            last_started_at=instance_service.utc_timestamp(),
            metadata={"backend_health": "starting", "frontend_health": "starting"},
        )
    )

    changed = InstanceSupervisor(registry).refresh_instance_states()

    stored = registry.get("fresh-partial")
    assert stored is not None
    assert stored.status == "starting"
    assert stored.metadata["backend_health"] == "process"
    assert stored.metadata["frontend_health"] == "stopped"
    assert changed[0]["status"] == "starting"


def test_refresh_instance_states_fails_stale_partial_startup(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    published: list[dict[str, Any]] = []

    class FakePublisher:
        def publish(self, *, subject: str, payload: dict[str, Any]) -> bool:
            published.append({"subject": subject, "payload": payload})
            return True

    monkeypatch.setattr(
        "polaris.infrastructure.log_pipeline.jetstream_publisher.get_log_jetstream_publisher",
        lambda: FakePublisher(),
    )
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: pid == 61001)
    root = _make_polaris_root(tmp_path)
    registry = InstanceRegistry(tmp_path / "instances", publish_events=True)
    registry.save(
        InstanceRecord(
            instance_id="stale-partial",
            name="Stale Partial",
            kind="bench_project",
            polaris_root=str(root),
            workspace=str((tmp_path / "bench" / "L1-10").resolve()),
            runtime_root=str((tmp_path / "bench" / "L1-10" / "runtime").resolve()),
            backend_port=59941,
            frontend_port=59942,
            backend_url="http://127.0.0.1:59941",
            frontend_url="http://127.0.0.1:59942",
            token="secret-token",
            backend_pid=61001,
            frontend_pid=61002,
            start_frontend=True,
            status="starting",
            last_started_at="2000-01-01T00:00:00Z",
            metadata={"backend_health": "starting", "frontend_health": "starting"},
        )
    )
    published.clear()

    changed = InstanceSupervisor(registry).refresh_instance_states()

    stored = registry.get("stale-partial")
    assert stored is not None
    assert changed[0]["instance_id"] == "stale-partial"
    assert stored.status == "failed"
    assert stored.metadata["backend_health"] == "process"
    assert stored.metadata["frontend_health"] == "failed"
    assert stored.metadata["status_reason"] == "frontend process did not survive startup"
    assert published[0]["subject"] == "hp.runtime.instances.status.instances"
    assert published[0]["payload"]["payload"]["instance"]["status"] == "failed"


def test_refresh_instance_states_does_not_publish_without_state_change(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    published: list[dict[str, Any]] = []

    class FakePublisher:
        def publish(self, *, subject: str, payload: dict[str, Any]) -> bool:
            published.append({"subject": subject, "payload": payload})
            return True

    monkeypatch.setattr(
        "polaris.infrastructure.log_pipeline.jetstream_publisher.get_log_jetstream_publisher",
        lambda: FakePublisher(),
    )
    monkeypatch.setattr(instance_service, "is_process_alive", lambda _pid: False)
    root = _make_polaris_root(tmp_path)
    registry = InstanceRegistry(tmp_path / "instances", publish_events=True)
    registry.save(
        InstanceRecord(
            instance_id="stopped-bench",
            name="Stopped Bench",
            kind="bench_project",
            polaris_root=str(root),
            workspace=str((tmp_path / "bench" / "L1-08").resolve()),
            runtime_root=str((tmp_path / "bench" / "L1-08" / "runtime").resolve()),
            backend_port=59921,
            frontend_port=59922,
            backend_url="http://127.0.0.1:59921",
            frontend_url="http://127.0.0.1:59922",
            token="secret-token",
            backend_pid=None,
            frontend_pid=None,
            start_frontend=True,
            status="stopped",
            metadata={"backend_health": "stopped", "frontend_health": "stopped"},
        )
    )
    published.clear()

    changed = InstanceSupervisor(registry).refresh_instance_states()

    assert changed == []
    assert published == []


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
    assert frontend_call["env"]["VITE_POLARIS_BACKEND_URL"] == "http://127.0.0.1:59911"
    assert frontend_call["env"]["VITE_POLARIS_BACKEND_TOKEN"] == record["token"]
    assert frontend_call["env"]["VITE_POLARIS_INSTANCE_ID"] == "bench-l1-01"
    assert frontend_call["env"]["VITE_POLARIS_WORKSPACE"] == str((tmp_path / "bench" / "L1-01").resolve())
    assert frontend_call["env"]["VITE_WORKSPACE"] == str((tmp_path / "bench" / "L1-01").resolve())
    assert backend_call["env"]["POLARIS_INSTANCE_ID"] == "bench-l1-01"
    assert backend_call["env"]["POLARIS_INSTANCE_KIND"] == "bench_project"
    assert backend_call["env"]["POLARIS_INSTANCE_WATCHDOG_ENABLED"] == "0"


def test_start_instance_recycled_pid_does_not_reuse_dead_backend(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """A stale record whose backend PID was recycled must NOT be reused.

    Regression: ``pid_max`` is small and bench runs churn short-lived backends,
    so a long-dead instance's recorded PID is routinely recycled to an unrelated
    live process. The old reuse guard trusted a bare ``is_process_alive`` check
    and handed back the stale record pointing at a dead port — the backend was
    never re-spawned and the bench chain connection-refused on that port. The
    supervisor must instead verify the PID really is *this instance's* backend
    (via cmdline) and otherwise spawn a fresh process.
    """
    root = _make_polaris_root(tmp_path)
    workspace = tmp_path / "bench" / "L1-02"
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)

    # Seed a stale record exactly as a prior run would have left it: a recorded
    # backend PID that is now "alive" only because it was recycled to something
    # else, and a backend_url whose port no longer serves HTTP. Use a low PID so
    # the identity mock below (fresh spawns are >= 70000) cleanly distinguishes
    # the recycled imposter from a genuinely-ours backend process.
    recycled_pid = 8327
    registry.save(
        InstanceRecord(
            instance_id="bench-l1-02",
            name="L1-02",
            kind="bench_project",
            polaris_root=str(root),
            workspace=str(workspace.resolve()),
            runtime_root=str((workspace / "runtime").resolve()),
            backend_port=50161,
            frontend_port=5258,
            backend_pid=recycled_pid,
            backend_url="http://127.0.0.1:50161",
            frontend_url="http://127.0.0.1:5258",
            token="bench-token",
            start_frontend=True,
            status="stopped",
            metadata={"backend_binding": "isolated_backend_instance"},
        )
    )

    calls: list[dict[str, Any]] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append({"command": command, **kwargs})
        return FakeProcess(70000 + len(calls))

    monkeypatch.setattr(instance_service.subprocess, "Popen", fake_popen)
    # The recycled PID *is* alive (recycled to an unrelated process)...
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: bool(pid))
    # ...but it is NOT our backend: the /proc cmdline identity check fails for it.
    # Newly spawned backend PIDs (>= 70000) are accepted as ours so _with_health
    # reports the fresh instance as running.
    monkeypatch.setattr(
        InstanceSupervisor,
        "_pid_looks_like_instance_process",
        staticmethod(lambda _record, pid, *, process_kind: bool(pid) and int(pid) >= 70000),
    )
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))

    supervisor = InstanceSupervisor(registry)
    record = supervisor.start_instance(
        {
            "instance_id": "bench-l1-02",
            "kind": "bench_project",
            "polaris_root": str(root),
            "workspace": str(workspace),
            # The factory_bench runner passes runtime_root explicitly (workspace/runtime),
            # which makes the incoming record match the stale one — exactly the
            # condition under which the old guard early-returned the dead record.
            "runtime_root": str((workspace / "runtime").resolve()),
            "backend_port": 50161,
            "frontend_port": 5258,
            "start_frontend": True,
            "metadata": {"backend_binding": "isolated_backend_instance"},
        }
    )

    # A fresh backend (and frontend) MUST have been spawned — the stale dead
    # record must not be returned as-is.
    assert len(calls) == 2, "expected a fresh backend+frontend spawn, not stale reuse"
    assert record["status"] == "running"
    assert int(record["backend_pid"]) >= 70000
    assert int(record["backend_pid"]) != recycled_pid


def test_instance_watchdog_default_scope(monkeypatch: Any) -> None:
    monkeypatch.delenv("POLARIS_INSTANCE_ID", raising=False)
    monkeypatch.delenv("POLARIS_INSTANCE_KIND", raising=False)
    assert instance_service._instance_watchdog_default_enabled() is False

    monkeypatch.setenv("POLARIS_INSTANCE_ID", "main")
    monkeypatch.setenv("POLARIS_INSTANCE_KIND", "development")
    assert instance_service._instance_watchdog_default_enabled() is True

    monkeypatch.setenv("POLARIS_INSTANCE_ID", "factory-bench-l1-01")
    monkeypatch.setenv("POLARIS_INSTANCE_KIND", "bench_project")
    assert instance_service._instance_watchdog_default_enabled() is False


def test_start_instance_rejects_requested_busy_backend_port(tmp_path: Path, monkeypatch: Any) -> None:
    root = _make_polaris_root(tmp_path)
    supervisor = InstanceSupervisor(InstanceRegistry(tmp_path / "instances", publish_events=False))
    monkeypatch.setattr(
        instance_service,
        "is_port_free",
        lambda port, host=instance_service.DEFAULT_HOST: port != 59911,
    )

    try:
        supervisor.start_instance(
            {
                "instance_id": "busy-backend",
                "kind": "bench_project",
                "polaris_root": str(root),
                "workspace": str(tmp_path / "bench" / "L1-01"),
                "backend_port": 59911,
                "frontend_port": 59912,
                "start_frontend": True,
            }
        )
    except RuntimeError as exc:
        assert "backend port is already in use: 59911" in str(exc)
    else:
        raise AssertionError("requested busy backend port must fail closed")


def test_bench_project_reserved_main_ports_are_reallocated(tmp_path: Path, monkeypatch: Any) -> None:
    root = _make_polaris_root(tmp_path)
    calls: list[dict[str, Any]] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append({"command": command, **kwargs})
        return FakeProcess(64500 + len(calls))

    def fake_allocate_port(start: int) -> int:
        if start == instance_service.DEFAULT_BACKEND_PORT + 1:
            return 60111
        if start == instance_service.DEFAULT_FRONTEND_PORT + 1:
            return 60112
        raise AssertionError(f"unexpected allocation start: {start}")

    monkeypatch.setattr(instance_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(instance_service, "allocate_port", fake_allocate_port)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: bool(pid))
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))
    supervisor = InstanceSupervisor(InstanceRegistry(tmp_path / "instances", publish_events=False))

    record = supervisor.start_instance(
        {
            "instance_id": "bench-reserved-ports",
            "kind": "bench_project",
            "polaris_root": str(root),
            "workspace": str(tmp_path / "bench" / "L1-03"),
            "backend_port": instance_service.DEFAULT_BACKEND_PORT,
            "frontend_port": instance_service.DEFAULT_FRONTEND_PORT,
            "start_frontend": True,
            "metadata": {"backend_binding": "isolated_backend_instance"},
        }
    )

    assert record["backend_port"] == 60111
    assert record["frontend_port"] == 60112
    assert calls[0]["command"][calls[0]["command"].index("--port") + 1] == "60111"
    assert calls[1]["command"][calls[1]["command"].index("--port") + 1] == "60112"
    assert calls[1]["env"]["VITE_BACKEND_URL"] == "http://127.0.0.1:60111"


def test_start_instance_auto_allocates_free_ports_without_claiming_busy_ports(
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
        return FakeProcess(64000 + len(calls))

    def fake_allocate_port(start: int) -> int:
        if start == instance_service.DEFAULT_BACKEND_PORT + 1:
            return 60021
        if start == instance_service.DEFAULT_FRONTEND_PORT + 1:
            return 60022
        raise AssertionError(f"unexpected allocation start: {start}")

    monkeypatch.setattr(instance_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(instance_service, "allocate_port", fake_allocate_port)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: bool(pid))
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))
    supervisor = InstanceSupervisor(InstanceRegistry(tmp_path / "instances", publish_events=False))

    record = supervisor.start_instance(
        {
            "instance_id": "auto-ports",
            "kind": "bench_project",
            "polaris_root": str(root),
            "workspace": str(tmp_path / "bench" / "L1-02"),
            "backend_port": None,
            "frontend_port": None,
            "start_frontend": True,
        }
    )

    assert record["backend_port"] == 60021
    assert record["frontend_port"] == 60022
    assert calls[0]["command"][calls[0]["command"].index("--port") + 1] == "60021"
    assert calls[1]["command"][calls[1]["command"].index("--port") + 1] == "60022"


def test_start_instance_auto_ports_skip_registered_running_instances(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = _make_polaris_root(tmp_path)
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    registry.save(
        InstanceRecord(
            instance_id="existing-bench",
            name="Existing",
            kind="bench_project",
            polaris_root=str(root),
            workspace=str((tmp_path / "bench" / "L1-09").resolve()),
            runtime_root=str((tmp_path / "bench" / "L1-09" / "runtime").resolve()),
            backend_port=instance_service.DEFAULT_BACKEND_PORT + 1,
            frontend_port=instance_service.DEFAULT_FRONTEND_PORT + 1,
            backend_url=f"http://127.0.0.1:{instance_service.DEFAULT_BACKEND_PORT + 1}",
            frontend_url=f"http://127.0.0.1:{instance_service.DEFAULT_FRONTEND_PORT + 1}",
            token="existing-token",
            backend_pid=71001,
            frontend_pid=71002,
            status="running",
        )
    )
    calls: list[dict[str, Any]] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append({"command": command, **kwargs})
        return FakeProcess(71010 + len(calls))

    monkeypatch.setattr(instance_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(instance_service, "is_port_free", lambda _port: True)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: bool(pid))
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))

    record = InstanceSupervisor(registry).start_instance(
        {
            "instance_id": "new-bench",
            "kind": "bench_project",
            "polaris_root": str(root),
            "workspace": str(tmp_path / "bench" / "L1-10"),
            "start_frontend": True,
        }
    )

    assert record["backend_port"] == instance_service.DEFAULT_BACKEND_PORT + 2
    assert record["frontend_port"] == instance_service.DEFAULT_FRONTEND_PORT + 2
    assert calls[0]["command"][calls[0]["command"].index("--port") + 1] == str(
        instance_service.DEFAULT_BACKEND_PORT + 2
    )


def test_start_instance_retries_auto_backend_port_identity_mismatch(
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
        return FakeProcess(73000 + len(calls))

    def fake_allocate_port(start: int, *, excluded_ports: set[int] | None = None) -> int:
        excluded = excluded_ports or set()
        if start == instance_service.DEFAULT_BACKEND_PORT + 1:
            return 60023 if 60021 in excluded else 60021
        if start == instance_service.DEFAULT_FRONTEND_PORT + 1:
            return 60024 if 60022 in excluded else 60022
        raise AssertionError(f"unexpected allocation start: {start}")

    def fake_wait_for_backend_identity(_self: InstanceSupervisor, record: InstanceRecord) -> None:
        if record.backend_port == 60021:
            raise RuntimeError(
                f"backend identity mismatch: port 60021 serves workspace /tmp/other, expected {record.workspace}"
            )

    monkeypatch.setattr(instance_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(instance_service, "allocate_port", fake_allocate_port)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: bool(pid))
    monkeypatch.setattr(InstanceSupervisor, "_wait_for_backend_identity", fake_wait_for_backend_identity)
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))

    record = InstanceSupervisor(InstanceRegistry(tmp_path / "instances", publish_events=False)).start_instance(
        {
            "instance_id": "retry-auto-port",
            "kind": "bench_project",
            "polaris_root": str(root),
            "workspace": str(tmp_path / "bench" / "L1-08"),
            "backend_port": None,
            "frontend_port": None,
            "start_frontend": False,
        }
    )

    backend_ports = [
        call["command"][call["command"].index("--port") + 1]
        for call in calls
        if call["command"][:3] == [instance_service.sys.executable, "-m", "polaris.delivery.cli.backend"]
    ]
    assert backend_ports == ["60021", "60023"]
    assert record["backend_port"] == 60023
    assert record["status"] == "running"


def test_start_instance_rejects_explicit_registered_backend_port(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = _make_polaris_root(tmp_path)
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    registry.save(
        InstanceRecord(
            instance_id="existing-explicit",
            name="Existing",
            kind="project",
            polaris_root=str(root),
            workspace=str((tmp_path / "project-a").resolve()),
            runtime_root=str((tmp_path / "project-a" / "runtime").resolve()),
            backend_port=60231,
            frontend_port=0,
            backend_url="http://127.0.0.1:60231",
            frontend_url="",
            token="existing-token",
            backend_pid=72001,
            frontend_pid=None,
            start_frontend=False,
            status="running",
        )
    )
    monkeypatch.setattr(instance_service, "is_port_free", lambda _port: True)

    try:
        InstanceSupervisor(registry).start_instance(
            {
                "instance_id": "new-explicit",
                "kind": "project",
                "polaris_root": str(root),
                "workspace": str(tmp_path / "project-b"),
                "backend_port": 60231,
                "start_frontend": False,
            }
        )
    except RuntimeError as exc:
        assert "backend port is already in use: 60231" in str(exc)
    else:
        raise AssertionError("explicit registered backend port must fail closed")


def test_start_instance_restarts_alive_record_when_workspace_changes(tmp_path: Path, monkeypatch: Any) -> None:
    root = _make_polaris_root(tmp_path)
    calls: list[dict[str, Any]] = []
    terminated: list[int] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append({"command": command, **kwargs})
        return FakeProcess(65000 + len(calls))

    def fake_allocate_port(start: int) -> int:
        if start == instance_service.DEFAULT_BACKEND_PORT + 1:
            return 60101 + len(calls)
        if start == instance_service.DEFAULT_FRONTEND_PORT + 1:
            return 60201 + len(calls)
        raise AssertionError(f"unexpected allocation start: {start}")

    monkeypatch.setattr(instance_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(instance_service, "allocate_port", fake_allocate_port)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: bool(pid))
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))
    monkeypatch.setattr(
        InstanceSupervisor, "_terminate_pid", staticmethod(lambda pid: terminated.append(int(pid or 0)))
    )
    monkeypatch.setattr(
        InstanceSupervisor,
        "_pid_looks_like_instance_process",
        staticmethod(lambda _record, pid, process_kind: bool(pid and process_kind in {"backend", "frontend"})),
    )

    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    supervisor = InstanceSupervisor(registry)
    first = supervisor.start_instance(
        {
            "instance_id": "bench-l1-05",
            "kind": "bench_project",
            "polaris_root": str(root),
            "workspace": str(tmp_path / "bench-r01" / "L1-05"),
            "backend_port": None,
            "frontend_port": None,
            "start_frontend": True,
            "metadata": {"backend_binding": "isolated_backend_instance"},
        }
    )

    second = supervisor.start_instance(
        {
            "instance_id": "bench-l1-05",
            "kind": "bench_project",
            "polaris_root": str(root),
            "workspace": str(tmp_path / "bench-r02" / "L1-05"),
            "backend_port": None,
            "frontend_port": None,
            "start_frontend": True,
            "metadata": {"backend_binding": "isolated_backend_instance"},
        }
    )

    assert first["workspace"] != second["workspace"]
    assert second["workspace"] == str((tmp_path / "bench-r02" / "L1-05").resolve())
    assert terminated == [first["frontend_pid"], first["backend_pid"]]
    assert len(calls) == 4


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
            frontend_port=59902,
            backend_url="http://127.0.0.1:59901",
            frontend_url="http://127.0.0.1:59902",
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


def test_backend_process_without_http_health_is_starting(tmp_path: Path, monkeypatch: Any) -> None:
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    supervisor = InstanceSupervisor(registry)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: pid == 61001)
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: False))
    registry.save(
        InstanceRecord(
            instance_id="starting-project",
            name="Starting Project",
            kind="project",
            polaris_root=str(_make_polaris_root(tmp_path)),
            workspace=str((tmp_path / "workspace").resolve()),
            runtime_root=str((tmp_path / "runtime").resolve()),
            backend_port=59901,
            frontend_port=0,
            backend_url="http://127.0.0.1:59901",
            frontend_url="",
            token="token",
            backend_pid=61001,
            frontend_pid=None,
            start_frontend=False,
            status="running",
        )
    )

    health = supervisor.health("starting-project")

    assert health["status"] == "starting"
    assert health["metadata"]["backend_health"] == "starting"


def test_current_backend_process_does_not_http_probe_itself(tmp_path: Path, monkeypatch: Any) -> None:
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    supervisor = InstanceSupervisor(registry)
    current_pid = 61001
    http_calls: list[str] = []

    def fake_http_ok(url: str, _token: str) -> bool:
        http_calls.append(url)
        return False

    monkeypatch.setattr(instance_service.os, "getpid", lambda: current_pid)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: pid == current_pid)
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
            frontend_port=0,
            backend_url="http://127.0.0.1:59901",
            frontend_url="",
            token="token",
            backend_pid=current_pid,
            frontend_pid=None,
            start_frontend=False,
            status="running",
        )
    )

    health = supervisor.health("main")

    assert health["status"] == "running"
    assert health["metadata"]["backend_health"] == "ok"
    assert http_calls == []


def test_current_backend_reloader_parent_does_not_http_probe_itself(tmp_path: Path, monkeypatch: Any) -> None:
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    supervisor = InstanceSupervisor(registry)
    current_pid = 61001
    parent_pid = 61000
    http_calls: list[str] = []

    def fake_http_ok(url: str, _token: str) -> bool:
        http_calls.append(url)
        return False

    monkeypatch.setattr(instance_service.os, "getpid", lambda: current_pid)
    monkeypatch.setattr(instance_service.os, "getppid", lambda: parent_pid)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: pid == parent_pid)
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
            frontend_port=0,
            backend_url="http://127.0.0.1:59901",
            frontend_url="",
            token="token",
            backend_pid=parent_pid,
            frontend_pid=None,
            start_frontend=False,
            status="running",
        )
    )

    health = supervisor.health("main")

    assert health["status"] == "running"
    assert health["metadata"]["backend_health"] == "ok"
    assert http_calls == []


def test_restart_shared_backend_bench_project_promotes_to_isolated_ports(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = _make_polaris_root(tmp_path)
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    supervisor = InstanceSupervisor(registry)
    calls: list[dict[str, Any]] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append({"command": command, **kwargs})
        return FakeProcess(62000 + len(calls))

    def fake_allocate_port(start: int) -> int:
        if start == instance_service.DEFAULT_BACKEND_PORT + 1:
            return 60011
        if start == instance_service.DEFAULT_FRONTEND_PORT + 1:
            return 60012
        raise AssertionError(f"unexpected allocation start: {start}")

    monkeypatch.setattr(instance_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(instance_service, "allocate_port", fake_allocate_port)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: bool(pid))
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))
    registry.save(
        InstanceRecord(
            instance_id="factory-bench-l1-01",
            name="L1-01",
            kind="bench_project",
            polaris_root=str(root),
            workspace=str((tmp_path / "bench" / "L1-01").resolve()),
            runtime_root=str((tmp_path / "bench" / "L1-01" / "runtime").resolve()),
            backend_port=49977,
            frontend_port=0,
            backend_url="http://127.0.0.1:49977",
            frontend_url="",
            token="shared-token",
            backend_pid=None,
            frontend_pid=None,
            start_frontend=False,
            status="observed",
            metadata={"backend_binding": "shared_backend_workspace_switch"},
        )
    )

    record = supervisor.restart_instance("factory-bench-l1-01")

    assert record["status"] == "running"
    assert record["backend_port"] == 60011
    assert record["frontend_port"] == 60012
    assert record["backend_url"] == "http://127.0.0.1:60011"
    assert record["frontend_url"] == "http://127.0.0.1:60012"
    assert record["metadata"]["backend_binding"] == "isolated_backend_instance"
    assert record["metadata"]["promoted_from_backend_binding"] == "shared_backend_workspace_switch"
    assert len(calls) == 2
    assert calls[0]["command"][calls[0]["command"].index("--port") + 1] == "60011"
    assert "--reload" not in calls[0]["command"]
    assert calls[1]["command"][calls[1]["command"].index("--port") + 1] == "60012"
    assert calls[1]["env"]["VITE_POLARIS_WORKSPACE"] == str((tmp_path / "bench" / "L1-01").resolve())


def test_restart_isolated_bench_project_disables_backend_reload(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = _make_polaris_root(tmp_path)
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    supervisor = InstanceSupervisor(registry)
    calls: list[dict[str, Any]] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append({"command": command, **kwargs})
        return FakeProcess(63000 + len(calls))

    monkeypatch.setattr(instance_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: bool(pid))
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))
    registry.save(
        InstanceRecord(
            instance_id="factory-bench-l1-04",
            name="L1-04",
            kind="bench_project",
            polaris_root=str(root),
            workspace=str((tmp_path / "bench" / "L1-04").resolve()),
            runtime_root=str((tmp_path / "bench" / "L1-04" / "runtime").resolve()),
            backend_port=59921,
            frontend_port=59922,
            backend_url="http://127.0.0.1:59921",
            frontend_url="http://127.0.0.1:59922",
            token="isolated-token",
            backend_pid=62001,
            frontend_pid=62002,
            backend_reload=True,
            start_frontend=True,
            status="running",
            metadata={"backend_binding": "isolated_backend_instance"},
        )
    )

    record = supervisor.restart_instance("factory-bench-l1-04")

    assert record["status"] == "running"
    assert record["backend_reload"] is False
    assert record["metadata"]["backend_binding"] == "isolated_backend_instance"
    assert len(calls) == 2
    assert "--reload" not in calls[0]["command"]


def test_restart_waits_for_old_ports_before_starting_replacement(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = _make_polaris_root(tmp_path)
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    supervisor = InstanceSupervisor(registry)
    events: list[str] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        events.append(f"popen:{command[command.index('--port') + 1]}")
        return FakeProcess(63500 + len(events))

    def fake_wait(record: InstanceRecord) -> None:
        assert not any(item.startswith("popen:") for item in events)
        events.append(f"wait:{record.backend_port}:{record.frontend_port}")

    monkeypatch.setattr(instance_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(InstanceSupervisor, "_terminate_pid", staticmethod(lambda pid: events.append(f"term:{pid}")))
    monkeypatch.setattr(InstanceSupervisor, "_wait_for_record_ports_free", staticmethod(fake_wait))
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: bool(pid))
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))
    registry.save(
        InstanceRecord(
            instance_id="factory-bench-l1-06",
            name="L1-06",
            kind="bench_project",
            polaris_root=str(root),
            workspace=str((tmp_path / "bench" / "L1-06").resolve()),
            runtime_root=str((tmp_path / "bench" / "L1-06" / "runtime").resolve()),
            backend_port=59931,
            frontend_port=59932,
            backend_url="http://127.0.0.1:59931",
            frontend_url="http://127.0.0.1:59932",
            token="isolated-token",
            backend_pid=62101,
            frontend_pid=62102,
            backend_reload=False,
            start_frontend=True,
            status="running",
            metadata={"backend_binding": "isolated_backend_instance"},
        )
    )

    record = supervisor.restart_instance("factory-bench-l1-06")

    assert record["status"] == "running"
    assert events == ["term:62102", "term:62101", "wait:59931:59932", "popen:59931", "popen:59932"]


def test_restart_isolated_bench_project_does_not_reuse_reserved_main_ports(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = _make_polaris_root(tmp_path)
    registry = InstanceRegistry(tmp_path / "instances", publish_events=False)
    supervisor = InstanceSupervisor(registry)
    calls: list[dict[str, Any]] = []

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append({"command": command, **kwargs})
        return FakeProcess(62500 + len(calls))

    def fake_allocate_port(start: int) -> int:
        if start == instance_service.DEFAULT_BACKEND_PORT + 1:
            return 60121
        if start == instance_service.DEFAULT_FRONTEND_PORT + 1:
            return 60122
        raise AssertionError(f"unexpected allocation start: {start}")

    monkeypatch.setattr(instance_service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(instance_service, "allocate_port", fake_allocate_port)
    monkeypatch.setattr(instance_service, "is_process_alive", lambda pid: bool(pid))
    monkeypatch.setattr(InstanceSupervisor, "_http_ok", staticmethod(lambda _url, _token: True))
    registry.save(
        InstanceRecord(
            instance_id="factory-bench-l1-02",
            name="L1-02",
            kind="bench_project",
            polaris_root=str(root),
            workspace=str((tmp_path / "bench" / "L1-02").resolve()),
            runtime_root=str((tmp_path / "bench" / "L1-02" / "runtime").resolve()),
            backend_port=instance_service.DEFAULT_BACKEND_PORT,
            frontend_port=instance_service.DEFAULT_FRONTEND_PORT,
            backend_url=f"http://127.0.0.1:{instance_service.DEFAULT_BACKEND_PORT}",
            frontend_url=f"http://127.0.0.1:{instance_service.DEFAULT_FRONTEND_PORT}",
            token="isolated-token",
            backend_pid=None,
            frontend_pid=None,
            start_frontend=True,
            status="stopped",
            metadata={"backend_binding": "isolated_backend_instance"},
        )
    )

    record = supervisor.restart_instance("factory-bench-l1-02")

    assert record["status"] == "running"
    assert record["backend_port"] == 60121
    assert record["frontend_port"] == 60122
    assert record["backend_url"] == "http://127.0.0.1:60121"
    assert record["frontend_url"] == "http://127.0.0.1:60122"
    assert calls[0]["command"][calls[0]["command"].index("--port") + 1] == "60121"
    assert calls[1]["command"][calls[1]["command"].index("--port") + 1] == "60122"
