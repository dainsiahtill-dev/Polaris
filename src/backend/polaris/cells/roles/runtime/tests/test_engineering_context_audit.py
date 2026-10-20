"""Synthetic native HTTP proof; no remote model or engineering delivery claim."""

from __future__ import annotations

import hashlib
import json
import socket
import threading
from collections.abc import Iterator, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    scripted_provider as scripted_provider,
)
from polaris.delivery.http.v2._shared import require_auth
from polaris.delivery.http.v2.context import router
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.storage.io_paths import resolve_storage_roots


def _configure(executor: AIExecutor, base_url: str, protocol: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        executor,
        "_get_provider_config",
        lambda _: {
            "type": "anthropic_compat" if protocol == "anthropic" else "openai_compat",
            "base_url": base_url,
            "api_path": "/v1/messages" if protocol == "anthropic" else "/v1/chat/completions",
            "api_key": "synthetic-only-never-real-secret",
            "max_context_tokens": 65536,
            "max_output_tokens": 16384,
            "max_tokens": 8192,
            "timeout": 5,
            "retries": 1,
        },
    )


def _request(role: str = "director") -> AIRequest:
    return AIRequest(
        task_type=TaskType.DIALOGUE,
        role=role,
        provider_id="synthetic",
        model="synthetic-model",
        input="Inspect current source.",
        options={
            "messages": [
                {"role": "system", "content": f"You are {role}. Preserve native history."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "previous-read",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"file":"one.txt"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "previous-read", "content": "actual prior file observation"},
                {"role": "user", "content": "Inspect current source."},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "parameters": {
                            "type": "object",
                            "properties": {"file": {"type": "string"}},
                            "required": ["file"],
                        },
                    },
                }
            ],
            "tool_choice": "required",
        },
        context={"engine_version": "engineering_v2", "call_id": "server-call", "turn_seq": 1},
    )


def _reader(workspace: Path) -> TestClient:
    # Technical read endpoint proof, not full product gateway acceptance.
    app = FastAPI()
    app.include_router(router)
    app.state.app_state = SimpleNamespace(settings=SimpleNamespace(workspace=str(workspace), ramdisk_root=""))
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("role", ["pm", "chief_engineer", "director", "qa"])
async def test_role_native_wire_ref_history_and_two_context_readers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scripted_provider: ScriptedProvider, protocol: str, role: str
) -> None:
    # The fixture changes configuration only; real provider/bootstrap ports,
    # native conversion, HTTP transport, capture and context files remain real.
    scripted_provider.client(protocol, tmp_path, monkeypatch)
    packet = (
        {"content": [{"type": "text", "text": "real native reply"}], "usage": {"input_tokens": 11, "output_tokens": 2}}
        if protocol == "anthropic"
        else {
            "choices": [{"message": {"role": "assistant", "content": "real native reply"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
        }
    )
    scripted_provider.enqueue_json(packet)
    observations: list[dict[str, Any]] = []
    executor = AIExecutor(
        workspace=str(tmp_path),
        native_request_observer=lambda _request, observation: observations.append(dict(observation)),
    )
    _configure(executor, scripted_provider.base_url, protocol, monkeypatch)
    request = _request(role)
    response = await executor.invoke(request)
    assert response.ok, response.error
    assert len(scripted_provider.requests) == 1
    wire = scripted_provider.requests[0]["body"]
    ref = request.context["context_snapshot_ref"]
    assert response.metadata["context_snapshot_ref"] == ref
    assert [item["phase"] for item in observations] == ["prepared", "terminal"]
    assert observations[-1]["usage_status"] == "reported"
    assert observations[-1]["physical_send_count"] is None
    runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
    raw = (runtime / "contexts" / ref[:2] / ref).read_text(encoding="utf-8")
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24] == ref
    assert "synthetic-only-never-real-secret" not in raw
    assert json.loads(raw)["provider_request"]["final_physical_request"]["body"] == wire
    assert not (runtime / "engineering" / "sessions.sqlite3").exists(), (
        "pure native discussion created an engineering row owner"
    )
    with _reader(tmp_path) as client:
        context = client.get(f"/v2/context/{ref}", params={"workspace": str(tmp_path)})
        final = client.get(f"/v2/context/{ref}/final-request", params={"workspace": str(tmp_path)})
        assert context.status_code == final.status_code == 200
        result = final.json()
        assert result["role"] == role
        assert result["physical_capture_status"] == "available"
        assert result["final_physical_request_body"] == wire
        assert result["tools"] == wire["tools"]
        assert result["tool_choice"] == wire["tool_choice"]
        assert result["response_format"] is None
        assert result["final_request_context_audit"]["audit_scope"] == "provider_native_wire"
        expected_messages = (
            [{"role": "system", "content": wire["system"]}, *wire["messages"]]
            if protocol == "anthropic"
            else wire["messages"]
        )
        assert result["messages"] == context.json()["messages"] == expected_messages
        assert "previous-read" in json.dumps(result["messages"])
        missing = client.get(
            f"/v2/context/{ref}/final-request", params={"workspace": str(tmp_path / "other-workspace")}
        )
        assert missing.status_code == 404


@pytest.fixture
def retry_provider(request: pytest.FixtureRequest) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    mode = request.param
    received: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            received.append(
                {"path": self.path, "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))}
            )
            if len(received) == 1 and mode == "disconnect":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if len(received) == 1 and mode == "redirect":
                self.send_response(307)
                self.send_header("Location", "/redirected")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = json.dumps(
                {
                    "choices": [{"message": {"content": "real native reply"}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


@pytest.mark.parametrize("retry_provider,dispatches", [("disconnect", 2), ("redirect", 1)], indirect=["retry_provider"])
async def test_dispatch_count_never_claims_hidden_redirect_http_sends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retry_provider: tuple[str, list[dict[str, Any]]], dispatches: int
) -> None:
    base_url, received = retry_provider
    observations: list[dict[str, Any]] = []
    executor = AIExecutor(
        workspace=str(tmp_path),
        native_request_observer=lambda _request, observation: observations.append(dict(observation)),
    )
    _configure(executor, base_url, "openai", monkeypatch)
    response = await executor.invoke(_request())
    assert response.ok, response.error
    assert len(received) == 2
    terminal = [item for item in observations if item["phase"] == "terminal"]
    assert len(terminal) == dispatches
    assert len({item["provider_request_id"] for item in terminal}) == dispatches
    assert terminal[-1]["dispatch_attempt_count"] == dispatches
    assert all(
        item["physical_send_count"] is None and item["physical_send_count_status"] == "unknown" for item in observations
    )
    assert terminal[-1]["usage_status"] == "reported"
    assert terminal[-1]["usage"]["total_tokens"] == 13
    if dispatches == 2:
        assert terminal[0]["usage_status"] == "unknown"
        assert terminal[0]["usage"] is None


async def test_observer_error_does_not_retry_actual_native_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scripted_provider: ScriptedProvider
) -> None:
    scripted_provider.client("openai", tmp_path, monkeypatch)
    scripted_provider.enqueue_json({"choices": [{"message": {"content": "provider succeeded"}}]})

    def fail_observer(_request: AIRequest, _observation: Mapping[str, Any]) -> None:
        raise OSError("synthetic observation database unavailable")

    executor = AIExecutor(workspace=str(tmp_path), native_request_observer=fail_observer)
    _configure(executor, scripted_provider.base_url, "openai", monkeypatch)
    response = await executor.invoke(_request())
    assert response.ok, response.error
    assert len(scripted_provider.requests) == 1
    assert response.metadata["native_request_observer_errors"][-1]["code"] == "native_request_observer_failed"
    assert response.metadata["native_request_observation"]["usage_status"] == "estimated"
