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


# Raw usage observed at event 1705 of run 6d51da0262c241038fd624920378875d.
# Replayed over finite loopback HTTP only; this is not another model-run receipt.
OBSERVED_ANTHROPIC_CACHE_USAGE = {
    "input_tokens": 816,
    "output_tokens": 725,
    "cache_read_input_tokens": 143597,
    "cache_creation_input_tokens": 0,
}


async def _record_native_usage_run(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    scripted_provider: ScriptedProvider,
    raw_usage: dict[str, int],
) -> tuple[str, Any, dict[Path, bytes]]:
    """Actual producer/capture callback; only host run/turn admission is seeded."""
    from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    scripted_provider.client("anthropic", workspace, monkeypatch)
    scripted_provider.enqueue_json({"content": [{"type": "text", "text": "synthetic usage reply"}], "usage": raw_usage})
    service = EngineeringRunService(workspace)
    session_id = "synthetic-cache-usage"
    service.store.create_run(
        RoleRunCommand(session_id, "director", "Observe synthetic usage only.", workspace),
        scope=PermissionScope((workspace,), (), frozenset(), False),
        limits=RunLimits(None, 10000, 2000),
        engine_version=service.engine_version,
        request_key=None,
    )
    turn_seq = service.store.append_event(session_id, kind="turn_started", role="director", payload={})
    runtime = Path(resolve_storage_roots(str(workspace)).runtime_root)
    sealed: dict[Path, bytes] = {}

    def record(request: AIRequest, observation: Mapping[str, Any]) -> None:
        service.record_native_request_observation(request, observation)
        ref = observation["context_snapshot_ref"]
        path = runtime / "contexts" / ref[:2] / ref
        sealed[path] = path.read_bytes()

    executor = AIExecutor(
        workspace=str(workspace),
        native_request_observer=record,
        context_reference_owner=service.context_reference_owner_resolver(session_id),
    )
    _configure(executor, scripted_provider.base_url, "anthropic", monkeypatch)
    request = _request()
    request.context.update(workspace=str(workspace), session_id=session_id, turn_seq=turn_seq)
    service.store.append_event(
        session_id,
        kind="request_prepared",
        role="director",
        payload={"turn_seq": turn_seq, "request": request.to_dict()},
    )
    response = await executor.invoke(request)
    assert response.ok, response.error
    assert not response.metadata.get("native_request_observer_errors")
    assert len(scripted_provider.requests) == 1
    assert len(sealed) == 2, "prepared and terminal snapshots must keep distinct immutable content"
    assert all(path.read_bytes() == original for path, original in sealed.items())
    return session_id, response, sealed


@pytest.mark.parametrize(
    "raw_usage,prompt_tokens,completion_tokens,total_tokens,cached_tokens",
    [
        pytest.param(OBSERVED_ANTHROPIC_CACHE_USAGE, 144413, 725, 145138, 143597, id="observed-cache-read"),
        pytest.param({"input_tokens": 816, "output_tokens": 725}, 816, 725, 1541, None, id="cache-unreported"),
        pytest.param(
            {"input_tokens": 816, "output_tokens": 725, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            816,
            725,
            1541,
            0,
            id="cache-explicit-zero",
        ),
        # Synthetic flat compatibility aliases through the Anthropic-compatible
        # producer; native OpenAI prompt_tokens_details usage is outside this proof.
        pytest.param(
            {"prompt_tokens": 100, "completion_tokens": 4, "total_tokens": 104, "cached_tokens": 80},
            100,
            4,
            104,
            80,
            id="synthetic-inclusive-aliases",
        ),
    ],
)
async def test_actual_cache_usage_survives_cold_engineering_and_context_readers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scripted_provider: ScriptedProvider,
    raw_usage: dict[str, int],
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cached_tokens: int | None,
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    session_id, response, sealed = await _record_native_usage_run(tmp_path, monkeypatch, scripted_provider, raw_usage)
    assert response.usage.prompt_tokens == prompt_tokens
    assert response.usage.completion_tokens == completion_tokens
    assert response.usage.total_tokens == total_tokens
    if cached_tokens is not None:
        assert response.usage.cached_tokens == cached_tokens

    # A new service reads SQLite, without any access to the producer's callback.
    cold = EngineeringRunService(tmp_path).snapshot(session_id, workspace=tmp_path)
    events = [event["payload"] for event in cold["events"] if event["kind"] == "llm_request_observed"]
    assert [event["phase"] for event in events] == ["prepared", "terminal"]
    terminal = events[-1]
    assert terminal["usage_status"] == "reported"
    assert terminal["raw_provider_usage"] == raw_usage
    assert terminal["usage"]["prompt_tokens"] == prompt_tokens
    assert terminal["usage"]["completion_tokens"] == completion_tokens
    assert terminal["usage"]["total_tokens"] == total_tokens
    if cached_tokens is None:
        assert "cached_tokens" not in terminal["usage"], "missing cache evidence must not become reported zero"
    else:
        assert terminal["usage"]["cached_tokens"] == cached_tokens
    assert all(event["physical_send_count"] is None for event in events)
    assert cold["verification"] == "not_run"
    assert cold["project_outcome"] != "accepted"

    ref = terminal["context_snapshot_ref"]
    with _reader(tmp_path) as client:
        context = client.get(f"/v2/context/{ref}", params={"workspace": str(tmp_path)})
        final = client.get(f"/v2/context/{ref}/final-request", params={"workspace": str(tmp_path)})
        assert context.status_code == final.status_code == 200
        result = final.json()
        captured = result["provider_request"]["usage_observation"]
        assert captured["usage"] == terminal["usage"]
        assert captured["raw_provider_usage"] == raw_usage
        assert result["final_physical_request_body"] == scripted_provider.requests[0]["body"]
        assert result["physical_send_count"] is None
        assert result["physical_send_count_status"] == "unknown"
    assert all(path.read_bytes() == original for path, original in sealed.items())
