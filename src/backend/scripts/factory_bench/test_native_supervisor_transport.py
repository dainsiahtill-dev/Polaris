"""Owned HTTP fixtures for Bench's authenticated native Supervisor boundary."""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.instances.internal.service import InstanceSupervisor
from scripts.factory_bench import factory_http_client
from scripts.factory_bench._bench_lib import cli, session


@pytest.fixture
def supervisor_http() -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {
        "calls": [],
        "instances": [],
        "status": 200,
        "mutate": lambda value: value,
        "stop_status": 200,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def respond(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            state["calls"].append((self.command, self.path, self.headers.get("Authorization"), body))
            status = state["status"]
            if self.path == "/v2/instances/start":
                instance = {
                    **copy.deepcopy(body),
                    "backend_url": "http://127.0.0.1:60101",
                    "frontend_url": "http://127.0.0.1:60102",
                    "backend_port": 60101,
                    "frontend_port": 60102,
                    "backend_pid": 12345,
                    "token": "project-secret",
                    "status": "running",
                }
                response = {"instance": state["mutate"](instance)}
            elif self.path.endswith("/stop"):
                response = {"instance": {"instance_id": self.path.split("/")[-2], "status": "stopped"}}
                status = state["stop_status"]
            else:
                response = {"instances": state["instances"]}
            if status != 200:
                response = {"detail": "supervisor-secret rejected", "diagnostic": "preserve-this"}
            self.send_response(status)
            if status in {301, 302, 303, 307, 308}:
                self.send_header("Location", state["redirect"])
            self.end_headers()
            self.wfile.write(state.get("raw_response", json.dumps(response).encode("utf-8")))

        do_POST = respond  # noqa: N815 -- stdlib handler dispatch names
        do_GET = respond  # noqa: N815 -- stdlib handler dispatch names

    server = HTTPServer(("127.0.0.1", 0), Handler)
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def launch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    monkeypatch.setenv("POLARIS_LAUNCHER_SUPERVISOR_TOKEN", "supervisor-secret")
    monkeypatch.setattr(InstanceSupervisor, "__init__", lambda *_a, **_k: pytest.fail("local Supervisor spawn"))
    monkeypatch.setattr(session, "compute_source_fingerprint", lambda _root: "source-fingerprint")
    workspace = session._allocate_fresh_project_workspace(tmp_path, project_id="L1-01", run_id="source-run")
    catalog = session._write_workspace_catalog_meta_exclusive(
        tmp_path,
        workspace,
        {
            "run_id": "source-run",
            "project_id": "L1-01",
            "requested_project_id": "L1-01",
            "canonical_catalog_project_id": "L1-01",
        },
    )
    receipt = session._new_isolated_bench_launch_receipt(
        bench_session_id="",
        run_id="source-run",
        project_id="L1-01",
        requested_project_id="L1-01",
        canonical_project_id="L1-01",
        bench_workspace=tmp_path,
        project_workspace=str(workspace),
        workspace_catalog_meta=catalog,
    )
    health: list[tuple[str, str]] = []
    monkeypatch.setattr(session, "_wait_backend_health", lambda url, token: health.append((url, token)) or True)
    return {"receipt": receipt, "workspace": workspace, "bench_workspace": tmp_path, "health": health}


def start(launch: dict[str, Any], url: str) -> dict[str, Any]:
    return session._start_isolated_bench_project_instance(
        bench_session_id="",
        project_id="L1-01",
        project_title="Fixture",
        level=1,
        bench_workspace=launch["bench_workspace"],
        project_workspace=str(launch["workspace"]),
        backend_token="main-secret",
        launch_receipt=launch["receipt"],
        launcher_supervisor_url=url,
    )


def test_remote_start_keeps_fresh_receipt_and_separates_tokens(
    supervisor_http: dict[str, Any], launch: dict[str, Any]
) -> None:
    result = start(launch, supervisor_http["url"])
    assert result["ok"] is True
    method, path, authorization, payload = supervisor_http["calls"][0]
    assert (method, path, authorization) == ("POST", "/v2/instances/start", "Bearer supervisor-secret")
    assert payload["require_fresh_instance"] is True
    assert payload["backend_reload"] is False
    assert payload["backend_port"] is None and payload["frontend_port"] is None
    assert payload["metadata"]["instance_launch_receipt"] == launch["receipt"]
    assert payload["bench"]["run_id"] == "source-run"
    assert payload["runtime_root"] == str(launch["workspace"] / ".polaris" / "runtime")
    assert "token" not in payload
    assert result["token"] == "project-secret"
    assert launch["health"] == [("http://127.0.0.1:60101", "project-secret")]


@pytest.mark.parametrize("status", [401, 503, 302, 307, 308])
def test_remote_errors_preserve_diagnostics_without_retry_fallback_or_secret(
    supervisor_http: dict[str, Any],
    launch: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    status: int,
) -> None:
    supervisor_http.update(status=status, redirect=supervisor_http["url"] + "/stolen-token")
    result = start(launch, supervisor_http["url"])
    assert result["ok"] is False
    assert result["platform_error"]["status"] == status
    assert "preserve-this" in json.dumps(result)
    assert "supervisor-secret" not in json.dumps(result)
    assert "supervisor-secret" not in capsys.readouterr().err
    assert len(supervisor_http["calls"]) == 1
    assert launch["health"] == []


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://127.0.0.1/?token=secret",
        "http://user:secret@127.0.0.1",
        "file:///tmp/supervisor",
        "http://127.0.0.1/v2",
    ],
)
def test_remote_rejects_non_loopback_or_credential_urls(launch: dict[str, Any], url: str) -> None:
    result = start(launch, url)
    assert result["ok"] is False
    assert launch["health"] == []
    assert "secret" not in json.dumps(result)


def test_remote_requires_explicit_supervisor_token(
    supervisor_http: dict[str, Any],
    launch: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLARIS_LAUNCHER_SUPERVISOR_TOKEN")
    result = start(launch, supervisor_http["url"])
    assert result["ok"] is False
    assert supervisor_http["calls"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("instance_id", "main"),
        ("workspace", "/wrong/workspace"),
        ("runtime_root", "/wrong/runtime"),
        ("polaris_root", "/wrong/source"),
        ("token", "supervisor-secret"),
        ("token", ""),
        ("backend_url", "http://127.0.0.1:49977"),
        ("frontend_port", 5173),
    ],
)
def test_remote_rejects_wrong_response_binding(
    supervisor_http: dict[str, Any],
    launch: dict[str, Any],
    field: str,
    value: Any,
) -> None:
    supervisor_http["mutate"] = lambda instance: {**instance, field: value}
    result = start(launch, supervisor_http["url"])
    assert result["ok"] is False
    assert launch["health"] == []
    assert all(not call[1].endswith("/stop") for call in supervisor_http["calls"])


def test_remote_rejects_tampered_persisted_receipt(supervisor_http: dict[str, Any], launch: dict[str, Any]) -> None:
    def mutate(instance: dict[str, Any]) -> dict[str, Any]:
        instance["metadata"]["instance_launch_receipt"]["run_id"] = "foreign-run"
        return instance

    supervisor_http["mutate"] = mutate
    assert start(launch, supervisor_http["url"])["ok"] is False
    assert launch["health"] == []


def test_remote_resume_stops_only_exact_owned_predecessor(
    supervisor_http: dict[str, Any], launch: dict[str, Any]
) -> None:
    launch["receipt"]["run_id"] = "repair-attempt"
    prior = {
        "instance_id": "owned-prior",
        "kind": "bench_project",
        "status": "running",
        "workspace": str(launch["workspace"]),
        "metadata": {
            "internal_test_only": True,
            "instance_launch_receipt": {
                "run_id": "source-run",
                "project_id": "L1-01",
                "bench_workspace": str(launch["bench_workspace"]),
            },
        },
    }
    foreign = copy.deepcopy(prior)
    foreign["instance_id"] = "foreign-prior"
    foreign["metadata"]["instance_launch_receipt"]["run_id"] = "foreign-run"
    main = {**prior, "instance_id": "main", "kind": "development"}
    supervisor_http["instances"] = [main, foreign, prior]
    assert start(launch, supervisor_http["url"])["ok"] is True
    assert [(call[0], call[1]) for call in supervisor_http["calls"]] == [
        ("GET", "/v2/instances"),
        ("POST", "/v2/instances/owned-prior/stop"),
        ("POST", "/v2/instances/start"),
    ]
    assert supervisor_http["calls"][-1][3]["metadata"]["instance_launch_receipt"][
        "resume_predecessor_instance_ids"
    ] == ["owned-prior"]


def test_remote_timeout_is_single_attempt_without_spawn(
    supervisor_http: dict[str, Any],
    launch: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutError("supervisor-secret timed out")

    monkeypatch.setattr(factory_http_client.urllib.request.OpenerDirector, "open", timeout)
    result = start(launch, supervisor_http["url"])
    assert result["ok"] is False
    assert result["platform_error"]["exception"] == "TimeoutError"
    assert "supervisor-secret" not in json.dumps(result)


def test_cli_documents_explicit_native_supervisor_option(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["run_factory_bench.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert "--launcher-supervisor-url" in capsys.readouterr().out


def test_cross_origin_redirect_never_forwards_supervisor_token(
    supervisor_http: dict[str, Any], launch: dict[str, Any]
) -> None:
    received: list[Any] = []

    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_GET(self) -> None:
            received.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        do_POST = do_GET  # noqa: N815 -- stdlib dispatch

    sink = HTTPServer(("127.0.0.1", 0), Sink)
    thread = threading.Thread(target=sink.serve_forever, daemon=True)
    thread.start()
    try:
        supervisor_http.update(status=307, redirect=f"http://127.0.0.1:{sink.server_port}/capture")
        assert start(launch, supervisor_http["url"])["ok"] is False
        assert received == []
    finally:
        sink.shutdown()
        sink.server_close()
        thread.join(timeout=2)


def test_remote_degraded_registry_blocks_resume_before_stop_or_start(
    supervisor_http: dict[str, Any], launch: dict[str, Any]
) -> None:
    launch["receipt"]["run_id"] = "repair-attempt"
    supervisor_http["instances"] = {"degraded": True, "items": [], "error": {"code": "registry_corrupt"}}
    result = start(launch, supervisor_http["url"])
    assert result["ok"] is False
    assert "registry_corrupt" in json.dumps(result)
    assert [(call[0], call[1]) for call in supervisor_http["calls"]] == [("GET", "/v2/instances")]


def test_remote_stop_failure_blocks_resume_start(supervisor_http: dict[str, Any], launch: dict[str, Any]) -> None:
    launch["receipt"]["run_id"] = "repair-attempt"
    supervisor_http["instances"] = [
        {
            "instance_id": "owned-prior",
            "kind": "bench_project",
            "status": "running",
            "workspace": str(launch["workspace"]),
            "metadata": {
                "internal_test_only": True,
                "instance_launch_receipt": {
                    "run_id": "source-run",
                    "project_id": "L1-01",
                    "bench_workspace": str(launch["bench_workspace"]),
                },
            },
        }
    ]
    supervisor_http["stop_status"] = 503
    result = start(launch, supervisor_http["url"])
    assert result["ok"] is False
    assert result["platform_error"]["status"] == 503
    assert all(call[1] != "/v2/instances/start" for call in supervisor_http["calls"])


@pytest.mark.parametrize(
    "raw",
    [
        b'{"token": "supervisor-secret", broken',
        b"[]",
        b'{"instance": null}',
        b'{"_http_error": {"reason": "supervisor-secret", "detail": "private-evidence"}}',
    ],
)
def test_remote_invalid_response_keeps_private_failure_evidence(
    supervisor_http: dict[str, Any], launch: dict[str, Any], raw: bytes
) -> None:
    supervisor_http["raw_response"] = raw
    result = start(launch, supervisor_http["url"])
    assert result["ok"] is False
    assert result["platform_error"]
    assert "supervisor-secret" not in json.dumps(result)
    assert launch["health"] == []


def test_remote_health_failure_keeps_instance_for_owner_diagnosis(
    supervisor_http: dict[str, Any],
    launch: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session, "_wait_backend_health", lambda *_a: False)
    result = start(launch, supervisor_http["url"])
    assert result["ok"] is False
    assert result["instance_id"] == launch["receipt"]["instance_id"]
    assert result["launch_receipt"] == launch["receipt"]
    assert len(supervisor_http["calls"]) == 1


@pytest.mark.parametrize("prior_id", ["MAIN", "main/", "owned-prior/../main"])
def test_remote_noncanonical_predecessor_id_cannot_target_control_instance(
    supervisor_http: dict[str, Any],
    launch: dict[str, Any],
    prior_id: str,
) -> None:
    launch["receipt"]["run_id"] = "repair-attempt"
    supervisor_http["instances"] = [
        {
            "instance_id": prior_id,
            "kind": "bench_project",
            "status": "running",
            "workspace": str(launch["workspace"]),
            "metadata": {
                "internal_test_only": True,
                "instance_launch_receipt": {
                    "run_id": "source-run",
                    "project_id": "L1-01",
                    "bench_workspace": str(launch["bench_workspace"]),
                },
            },
        }
    ]
    result = start(launch, supervisor_http["url"])
    assert result["ok"] is False
    assert len(supervisor_http["calls"]) == 1


@pytest.mark.parametrize("instance_id", ["main", "MAIN", "main/", ""])
def test_remote_start_rejects_control_or_noncanonical_requested_identity_before_http(
    supervisor_http: dict[str, Any],
    launch: dict[str, Any],
    instance_id: str,
) -> None:
    launch["receipt"]["instance_id"] = instance_id
    assert start(launch, supervisor_http["url"])["ok"] is False
    assert supervisor_http["calls"] == []
