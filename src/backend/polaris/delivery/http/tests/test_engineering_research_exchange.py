"""Exercise authenticated research transfers through isolated HTTP instance bindings."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from polaris.bootstrap.config import EngineeringPermissionProfile, ServerConfig, Settings
from polaris.bootstrap.engineering_runtime import get_engineering_service
from polaris.cells.roles.runtime.public.engineering_research import DESCRIPTOR_LIMIT
from polaris.cells.runtime.state_owner.public.service import Auth
from polaris.delivery import research_peer
from polaris.delivery.http.routers import engineering_research_exchange as exchange


def test_loopback_origin_is_canonical_and_port_bound() -> None:
    assert research_peer._loopback_origin("http://localhost:9011/", 9011) == "http://127.0.0.1:9011"
    assert research_peer._loopback_origin("http://[::1]:9011", 9011) == "http://[::1]:9011"
    for invalid in (
        "http://127.0.0.1:9012",
        "http://user@127.0.0.1:9011",
        "http://127.0.0.1:9011/?url",
        "http://example.invalid:9011",
    ):
        with pytest.raises(PermissionError):
            research_peer._loopback_origin(invalid, 9011)


def wire(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass
class Producer:
    workspace: Path
    registry: Path
    descriptor: dict[str, Any]
    content: bytes
    record: dict[str, Any] = field(default_factory=dict)
    requests: list[tuple[str, str, str]] = field(default_factory=list)
    mode: str = "normal"

    @property
    def digest(self) -> str:
        return hashlib.sha256(wire(self.descriptor)).hexdigest()

    @property
    def args(self) -> dict[str, str]:
        return {"source_instance": "lab-source", "source_run": "r-src", "publication_sha256": self.digest}

    def save_record(self) -> None:
        self.registry.write_text(json.dumps({"schema_version": 1, "instances": [self.record]}), encoding="utf-8")


@pytest.fixture
def producer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Producer]:
    workspace = tmp_path / "source"
    workspace.mkdir()
    registry = tmp_path / "instances"
    registry.mkdir()
    monkeypatch.setenv("KERNELONE_INSTANCE_HOME", str(registry))
    content = bytes(range(256))
    peer = Producer(
        workspace,
        registry / "registry.json",
        {
            "schema": "engineering.research-publication.v1",
            "project": "models",
            "lab": "structure",
            "scope": "local structure evidence",
            "experiment": "shape",
            "producer_workspace": str(workspace),
            "run_id": "r-src",
            "plan_sha256": "e" * 64,
            "verification_seq": 19,
            "workspace_revision": 2,
            "producer_acceptance_basis": "verified",
            "files": [
                {"path": "sample/data.bin", "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
            ],
        },
        content,
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            auth = self.headers.get("Authorization", "")
            assertion = parse_qs(parsed.query).get("workspace", [""])[0]
            peer.requests.append((parsed.path, auth, assertion))
            prefix = f"/v2/engineering/runs/r-src/research-publications/{peer.digest}"
            if auth != "Bearer source-operator-secret":
                self.send_error(403)
                return
            if assertion != str(workspace):
                self.send_error(403)
                return
            if parsed.path == prefix:
                if peer.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/redirected-away")
                    self.end_headers()
                    return
                body = wire(peer.descriptor)
                if peer.mode == "duplicate":
                    body = b'{"lab":"duplicate",' + body[1:]
            elif parsed.path == prefix + "/files/0":
                body = peer.content
                if peer.mode == "wrong-bytes":
                    body = b"x" * len(body)
                if peer.mode == "too-long":
                    body += b"extra"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            if peer.mode == "declared-overflow":
                self.send_header("Content-Length", str(DESCRIPTOR_LIMIT + 1))
            else:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    runner = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    port = server.server_port
    peer.record = {
        "instance_id": "lab-source",
        "name": "lab source",
        "kind": "project",
        "polaris_root": str(tmp_path),
        "workspace": str(workspace),
        "runtime_root": str(workspace / ".polaris/runtime"),
        "backend_port": port,
        "frontend_port": 8090,
        "backend_url": f"http://127.0.0.1:{port}",
        "frontend_url": "http://127.0.0.1:8090",
        "token": "registry-credential-not-authorized-for-transfer",
        "public_backend_url": "https://remote.invalid:443",
        "status": "running",
        "backend_pid": os.getpid(),
        "metadata": {
            "backend_binding": "isolated_backend_instance",
            "backend_process_identity": {"instance_id": "lab-source", "workspace": str(workspace), "pid": os.getpid()},
        },
    }
    peer.save_record()
    runner.start()
    try:
        yield peer
    finally:
        server.shutdown()
        server.server_close()
        runner.join(timeout=5)


def app_for(workspace: Path) -> FastAPI:
    workspace.mkdir(exist_ok=True)
    server = ServerConfig(
        engineering_permission_profiles={
            "workspace": EngineeringPermissionProfile(),
            "readonly": EngineeringPermissionProfile(write_roots=(), enabled_tools=frozenset({"read_file"})),
        }
    )
    settings = Settings(workspace=workspace, server=server)
    app = FastAPI()
    app.state.settings = settings
    app.state.app_state = SimpleNamespace(settings=settings)
    app.state.auth = Auth("consumer-secret")
    get_engineering_service(app.state, settings=settings)
    app.include_router(exchange.router, prefix="/v2/engineering")
    return app


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://consumer.test",
        headers={
            "Authorization": "Bearer consumer-secret",
            "X-Polaris-Research-Source-Token": "source-operator-secret",
        },
    )


IMPORT_PATH = "/v2/engineering/research/imports"


async def test_registered_transfer_reads_real_socket_and_commits_verified_bytes(
    tmp_path: Path, producer: Producer, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("HTTP_PROXY", "ALL_PROXY", "HTTPS_PROXY"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    assert research_peer.registered_research_instance("lab-source", home=producer.registry.parent) == {
        "instance_id": "lab-source",
        "workspace": str(producer.workspace),
        "backend_url": producer.record["backend_url"],
    }
    consumer = tmp_path / "consumer"
    async with client_for(app_for(consumer)) as client:
        success = await client.post(IMPORT_PATH, json=producer.args)
        assert success.status_code == 200, success.text
        result = success.json()
        manifest = consumer / result["report"]["manifest"]
        assert result["report"]["sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
        bound = json.loads(manifest.read_text(encoding="utf-8"))
        assert bound["producer"]["producer_workspace"] == str(producer.workspace)
        artifact = bound["artifacts"][0]
        assert (consumer / artifact["path"]).read_bytes() == producer.content
        assert artifact["sha256"] == hashlib.sha256(producer.content).hexdigest()
        repeat = await client.post(IMPORT_PATH, json=producer.args)
        assert repeat.status_code == 200 and repeat.json() == result
    assert len(producer.requests) == 3
    assert sum(path.endswith("/files/0") for path, _, _ in producer.requests) == 1
    assert all(
        auth == "Bearer source-operator-secret" and workspace == str(producer.workspace)
        for _, auth, workspace in producer.requests
    )


async def test_both_credentials_are_explicit_and_body_cannot_authorize_a_source(
    tmp_path: Path, producer: Producer
) -> None:
    consumer = tmp_path / "consumer"
    async with client_for(app_for(consumer)) as client:
        del client.headers["X-Polaris-Research-Source-Token"]
        missing = await client.post(IMPORT_PATH, json=producer.args)
        assert missing.status_code == 403 and not producer.requests
        extra = await client.post(
            IMPORT_PATH, json={**producer.args, "source_url": producer.record["backend_url"], "accepted": True}
        )
        assert extra.status_code == 422 and not producer.requests
        client.headers["X-Polaris-Research-Source-Token"] = "wrong-source"
        assert (await client.post(IMPORT_PATH, json=producer.args)).status_code == 403
        assert producer.requests[0][1] == "Bearer wrong-source"
        client.headers["Authorization"] = "Bearer invalid-consumer"
        assert (await client.post(IMPORT_PATH, json=producer.args)).status_code in (401, 403)
        assert len(producer.requests) == 1
    assert not (consumer / "research-inputs").exists()


async def test_consumer_write_grants_and_workspace_assertions(tmp_path: Path, producer: Producer) -> None:
    consumer = tmp_path / "consumer"
    async with client_for(app_for(consumer)) as client:
        crossed = await client.post(IMPORT_PATH, json={**producer.args, "workspace": str(producer.workspace)})
        assert crossed.status_code == 403 and not producer.requests
        unknown = await client.post(IMPORT_PATH, json={**producer.args, "permission_profile_id": "unregistered"})
        assert unknown.status_code == 403 and not producer.requests
        denied = await client.post(IMPORT_PATH, json={**producer.args, "permission_profile_id": "readonly"})
        assert denied.status_code == 403
    assert not (consumer / "research-inputs").exists()
    assert not any(path.endswith("/files/0") for path, _, _ in producer.requests)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("redirect", 502),
        ("duplicate", 422),
        ("wrong-bytes", 422),
        ("too-long", 422),
        ("declared-overflow", 422),
        ("wrong-workspace", 403),
        ("wrong-run", 403),
    ],
)
async def test_invalid_remote_publication_never_commits(
    tmp_path: Path, producer: Producer, mode: str, expected: int
) -> None:
    producer.mode = mode
    if mode == "wrong-workspace":
        producer.descriptor["producer_workspace"] = str(tmp_path / "someone-else")
    if mode == "wrong-run":
        producer.descriptor["run_id"] = "another-run"
    consumer = tmp_path / "consumer"
    async with client_for(app_for(consumer)) as client:
        response = await client.post(IMPORT_PATH, json=producer.args)
    assert response.status_code == expected, response.text
    assert "source-operator-secret" not in response.text
    assert not (consumer / "research-inputs" / producer.digest / "manifest.json").exists()
    assert not any(path == "/redirected-away" for path, _, _ in producer.requests)


@pytest.mark.parametrize(
    "mismatch", ["observed", "wrong-runtime", "not-owner", "stale", "overshared-bench", "wrong-identity", "remote"]
)
def test_registry_rejects_records_without_independent_live_bindings(producer: Producer, mismatch: str) -> None:
    r = producer.record
    if mismatch == "observed":
        r["metadata"]["backend_binding"] = "shared_backend_workspace_switch"
    elif mismatch == "wrong-runtime":
        r["runtime_root"] = str(producer.registry.parent)
    elif mismatch == "not-owner":
        r["backend_pid"] = None
        del r["metadata"]["backend_process_identity"]
    elif mismatch == "stale":
        r["status"] = "stopped"
    elif mismatch == "overshared-bench":
        r["kind"] = "bench_project"
        del r["metadata"]["backend_binding"]
    elif mismatch == "wrong-identity":
        r["metadata"]["backend_process_identity"]["workspace"] = str(producer.registry.parent)
    elif mismatch == "remote":
        r["backend_url"] = f"http://203.0.113.3:{r['backend_port']}"
    producer.save_record()
    with pytest.raises(PermissionError):
        research_peer.registered_research_instance("lab-source", home=producer.registry.parent)
    assert not producer.requests


def test_transport_wall_deadline_is_checked_before_get() -> None:
    transport = httpx.MockTransport(lambda _: pytest.fail("expired transfer made a request"))
    with httpx.Client(transport=transport) as client, pytest.raises(HTTPException, match="504"):
        exchange._read_bytes(client, "/missing", "/workspace", 0, time.monotonic() - 1)


async def test_publisher_routes_bind_workspace_session_scope_and_return_binary_unchanged(
    tmp_path: Path, producer: Producer, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = app_for(tmp_path / "publisher")
    service = app.state.engineering_run_service
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    envelope = {"publication_sha256": producer.digest, "issued_seq": 25, "descriptor": producer.descriptor}

    def spy(name: str, result: object) -> None:
        def capture(*args: object, **kwargs: object) -> object:
            calls.append((name, args, kwargs))
            return result

        monkeypatch.setattr(service, name, capture)

    spy("publish_research", envelope)
    spy("research_publication", producer.descriptor)
    spy("research_publication_file", producer.content)
    async with client_for(app) as client:
        path = "/v2/engineering/runs/r-src/research-publications"
        selected = {"experiment": "shape", "files": ["sample/data.bin"], "verification_seq": 19}
        posted = await client.post(path, json=selected)
        assert posted.status_code == 200 and posted.json() == envelope
        get = await client.get(f"{path}/{producer.digest}")
        assert get.status_code == 200 and get.json() == producer.descriptor
        blob = await client.get(f"{path}/{producer.digest}/files/0")
        assert (
            blob.status_code == 200
            and blob.content == producer.content
            and blob.headers["content-type"] == "application/octet-stream"
        )
        assert (
            await client.get(f"{path}/{producer.digest}", params={"workspace": str(producer.workspace)})
        ).status_code == 403
        assert (await client.get(f"{path}/not-a-digest")).status_code == 422
    assert calls == [
        ("publish_research", ("r-src",), {"workspace": service.workspace, **selected}),
        ("research_publication", ("r-src", producer.digest), {"workspace": service.workspace}),
        ("research_publication_file", ("r-src", producer.digest, 0), {"workspace": service.workspace}),
    ]


def test_registered_listener_must_own_the_exact_loopback_address(producer: Producer) -> None:
    port = producer.record["backend_port"]
    assert research_peer._owns_listener(os.getpid(), port, "127.0.0.1")
    assert not research_peer._owns_listener(os.getpid(), port, "127.0.0.2")
