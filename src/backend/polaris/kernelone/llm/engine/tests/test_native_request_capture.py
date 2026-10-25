"""Ordinary Engine calls must expose actual native transport evidence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polaris.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from polaris.kernelone.llm.engine.contracts import AIRequest, ModelSpec, TaskType, TokenBudgetDecision
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.model_catalog import ModelCatalog
from polaris.kernelone.llm.engine.native_request_capture import NativeRequestCapturePort
from polaris.kernelone.llm.engine.prompt_budget import TokenBudgetManager
from polaris.kernelone.llm.engine.stream.executor import StreamExecutor
from polaris.kernelone.storage.io_paths import resolve_storage_roots


class _Catalog:
    def resolve(self, provider_id: str, model: str, provider_cfg: dict[str, Any]) -> ModelSpec:
        return ModelSpec(
            provider_id=provider_id,
            provider_type="anthropic_compat",
            model=model,
            max_context_tokens=4096,
            max_output_tokens=512,
            supports_tools=True,
        )


class _Budget:
    def enforce(self, *args: Any, **kwargs: Any) -> TokenBudgetDecision:
        return TokenBudgetDecision(
            allowed=True,
            max_context_tokens=4096,
            allowed_prompt_tokens=3000,
            requested_prompt_tokens=64,
            reserved_output_tokens=128,
            safety_margin_tokens=64,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_usage", "expected_raw_usage", "expected_usage_status", "expected_cached_evidence"),
    [
        pytest.param(
            {"input_tokens": 11, "output_tokens": 2},
            {"input_tokens": 11, "output_tokens": 2},
            "reported",
            False,
            id="noncached",
        ),
        pytest.param(
            # Actual Director response event 1705, run 6d51da0262c241038fd624920378875d.
            {
                "input_tokens": 816,
                "output_tokens": 725,
                "cache_read_input_tokens": 143597,
                "cache_creation_input_tokens": 0,
                "service_tier": "standard",
            },
            {
                "input_tokens": 816,
                "output_tokens": 725,
                "cache_read_input_tokens": 143597,
                "cache_creation_input_tokens": 0,
            },
            "reported",
            True,
            id="observed-cache-read-and-explicit-zero-creation",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 5},
            {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 5},
            "reported",
            True,
            id="synthetic-positive-cache-creation",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": True},
            {"input_tokens": 10, "output_tokens": 2},
            "estimated",
            False,
            id="malformed-cache-read-bool",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": -1},
            {"input_tokens": 10, "output_tokens": 2},
            "estimated",
            False,
            id="malformed-cache-read-negative",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 2, "cache_creation_input_tokens": True},
            {"input_tokens": 10, "output_tokens": 2},
            "estimated",
            False,
            id="malformed-cache-creation-bool",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 2, "cache_creation_input_tokens": -1},
            {"input_tokens": 10, "output_tokens": 2},
            "estimated",
            False,
            id="malformed-cache-creation-negative",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 2, "cached_tokens": True},
            {"input_tokens": 10, "output_tokens": 2},
            "estimated",
            False,
            id="malformed-cached-alias-bool",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 2, "cached_tokens": -1},
            {"input_tokens": 10, "output_tokens": 2},
            "estimated",
            False,
            id="malformed-cached-alias-negative",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 2, "cached_prompt_tokens": True},
            {"input_tokens": 10, "output_tokens": 2},
            "estimated",
            False,
            id="malformed-cached-prompt-alias-bool",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 2, "cached_prompt_tokens": -1},
            {"input_tokens": 10, "output_tokens": 2},
            "estimated",
            False,
            id="malformed-cached-prompt-alias-negative",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": "7"},
            {"input_tokens": 10, "output_tokens": 2},
            "estimated",
            False,
            id="malformed-cache-read-numeric-string",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 2, "cache_creation_input_tokens": 1.5},
            {"input_tokens": 10, "output_tokens": 2},
            "estimated",
            False,
            id="malformed-cache-creation-float",
        ),
        pytest.param(
            {
                "input_tokens": 10,
                "output_tokens": 2,
                "cache_read_input_tokens": None,
                "cache_creation_input_tokens": None,
            },
            {"input_tokens": 10, "output_tokens": 2},
            "reported",
            False,
            id="optional-null-cache-is-unreported",
        ),
        pytest.param(
            {"input_tokens": 10, "output_tokens": 2, "cache_creation_input_tokens": 5},
            {"input_tokens": 10, "output_tokens": 2, "cache_creation_input_tokens": 5},
            "reported",
            False,
            id="creation-only-does-not-report-cache-read-zero",
        ),
    ],
)
async def test_ordinary_http_native_body_is_persisted_without_factory_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_usage: dict[str, Any],
    expected_raw_usage: dict[str, int],
    expected_usage_status: str,
    expected_cached_evidence: bool,
) -> None:
    observed: list[dict[str, Any]] = []
    native_observations: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            response = json.dumps({"content": [{"type": "text", "text": "accepted"}], "usage": raw_usage}).encode(
                "utf-8"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    provider = AnthropicProvider()

    class Manager:
        def get_provider_instance(self, provider_type: str) -> AnthropicProvider:
            assert provider_type == "anthropic_compat"
            return provider

    monkeypatch.setattr("polaris.kernelone.llm.engine.executor.get_provider_manager", Manager)
    executor = AIExecutor(
        workspace=str(tmp_path),
        model_catalog=cast(ModelCatalog, _Catalog()),
        token_budget=cast(TokenBudgetManager, _Budget()),
        native_request_observer=lambda _request, observation: native_observations.append(dict(observation)),
    )
    monkeypatch.setattr(
        executor,
        "_get_provider_config",
        lambda _: {
            "type": "anthropic_compat",
            "base_url": f"http://127.0.0.1:{server.server_port}",
            "api_key": "private-fixture-key",
            "timeout": 5,
            "retries": 0,
        },
    )
    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="director",
        provider_id="local-native",
        model="local-model",
        input="Inspect the current candidate.",
        options={"max_tokens": 128},
        context={
            "call_id": "ordinary-native-call",
            "run_id": "ordinary-native-run",
            "chat_messages": [
                {"role": "system", "content": "You are Director. NATIVE_CAPTURE_MARKER."},
                {"role": "user", "content": "Inspect the current candidate."},
            ],
        },
    )
    try:
        result = await executor.invoke(request)
        assert result.ok, result.error
        assert result.raw is not None
        assert result.raw["usage"] == raw_usage
        assert result.usage.estimated is (expected_usage_status == "estimated")
        if expected_usage_status == "estimated":
            assert (result.usage.prompt_tokens, result.usage.completion_tokens, result.usage.total_tokens) == (7, 2, 9)
        assert len(observed) == 1
        ref = request.context["context_snapshot_ref"]
        runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
        snapshot = json.loads((runtime / "contexts" / ref[:2] / ref).read_text(encoding="utf-8"))
        provider_request = snapshot["provider_request"]
        wire = provider_request.get("final_physical_request")
        assert isinstance(wire, dict), "ordinary call retained semantic projection instead of actual native body"
        assert wire["body"] == observed[0]
        canonical = json.dumps(wire, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        assert provider_request["final_physical_wire_hash"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert "NATIVE_CAPTURE_MARKER" in wire["body"]["system"]
        assert "private-fixture-key" not in canonical
        assert provider_request["evidence_level"] == "physical_capture"
        assert provider_request["execution_qualified"] is False
        assert provider_request["physical_attempt"]["status"] == "completed"
        assert "factory_final_request" not in provider_request
        assert provider_request["usage_observation"]["raw_provider_usage"] == expected_raw_usage
        assert native_observations[-1]["phase"] == "terminal"
        assert native_observations[-1]["raw_provider_usage"] == expected_raw_usage
        for evidence in (provider_request["usage_observation"], native_observations[-1]):
            assert evidence["usage_status"] == expected_usage_status
            assert ("cached_tokens" in evidence["usage"]) is expected_cached_evidence
        assert raw_usage == result.raw["usage"]
        assert native_observations[-1]["physical_send_count"] is None
        assert native_observations[-1]["physical_send_count_status"] == "unknown"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _capture_port(
    tmp_path: Path, native_request_observer: Callable[[AIRequest, Mapping[str, Any]], None] | None = None
) -> tuple[NativeRequestCapturePort, AIRequest]:
    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="director",
        provider_id="local-native",
        model="local-model",
        input="Inspect.",
        context={"call_id": "capture-attempts", "run_id": "capture-run"},
    )
    port = NativeRequestCapturePort(
        request=request,
        model_spec=_Catalog().resolve("local-native", "local-model", {}),
        trace_id="capture-run",
        messages=[{"role": "user", "content": "Inspect."}],
        provider_request={"role": "director", "final_request_context_audit": {}},
        persist=AIExecutor._store_context_messages_sync,
        workspace=str(tmp_path),
        native_request_observer=native_request_observer,
    )
    return port, request


def _wire() -> dict[str, Any]:
    return {
        "endpoint": "http://127.0.0.1/native",
        "headers": {"authorization": "private-token"},
        "body": {"model": "local-model", "messages": [{"role": "user", "content": "Inspect."}]},
        "transport": {"kind": "local-fixture"},
    }


def test_retry_attempt_identity_is_distinct_even_when_native_body_is_identical(tmp_path: Path) -> None:
    port, request = _capture_port(tmp_path)
    refs: list[str] = []
    ids: list[str] = []
    for _ in range(2):
        port.dispatch_sync(wire_request=_wire(), send=lambda _: "ok")
        refs.append(request.context["context_snapshot_ref"])
        ids.append(request.context["physical_request_capture"]["provider_request_id"])
    assert ids[0] != ids[1]
    assert refs[0] != refs[1]
    runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
    snapshots = [json.loads((runtime / "contexts" / ref[:2] / ref).read_text(encoding="utf-8")) for ref in refs]
    assert (
        snapshots[0]["provider_request"]["final_physical_wire_hash"]
        == snapshots[1]["provider_request"]["final_physical_wire_hash"]
    )
    assert snapshots[1]["provider_request"]["physical_attempt"]["attempt_number"] == 2


@pytest.mark.asyncio
async def test_stream_cancel_is_recorded_after_response_cleanup(tmp_path: Path) -> None:
    port, request = _capture_port(tmp_path)
    order: list[str] = []

    @asynccontextmanager
    async def open_stream(_: Any) -> Any:
        try:
            yield object()
        finally:
            order.append("response_closed")

    async def consume(_: Any) -> Any:
        try:
            yield "chunk"
            yield "unused"
        finally:
            order.append("consumer_closed")

    stream = port.dispatch_stream_async(wire_request=_wire(), open_stream=open_stream, consume=consume)
    assert await anext(stream) == "chunk"
    assert request.context["physical_request_capture"]["status"] == "prepared"
    await cast(AsyncGenerator[Any, None], stream).aclose()
    assert order == ["consumer_closed", "response_closed"]
    assert request.context["physical_request_capture"]["status"] == "cancelled"


def test_capture_persistence_failure_blocks_send(tmp_path: Path) -> None:
    port, _ = _capture_port(tmp_path)
    calls: list[str] = []

    def fail(**_: Any) -> str:
        raise OSError("private storage unavailable")

    port._persist = fail
    with pytest.raises(RuntimeError, match="native_request_capture_persist_failed"):
        port.dispatch_sync(wire_request=_wire(), send=lambda _: calls.append("sent"))
    assert calls == []


def test_native_audit_is_verified_from_workspace_snapshot(tmp_path: Path) -> None:
    from polaris.kernelone.llm.engine import native_request_capture

    port, request = _capture_port(tmp_path)
    port.dispatch_sync(wire_request=_wire(), send=lambda _: "ok")
    read = getattr(native_request_capture, "verified_native_capture_audit", None)
    assert callable(read), "native audit has no persisted snapshot validation boundary"
    assert read(request=request, workspace=str(tmp_path))["audit_scope"] == "provider_native_wire"
    assert read(request=request, workspace=str(tmp_path / "foreign")) is None
    ref = request.context["context_snapshot_ref"]
    runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
    (runtime / "contexts" / ref[:2] / ref).write_text("{}", encoding="utf-8")
    assert read(request=request, workspace=str(tmp_path)) is None


def test_capture_retains_role_evidence_coverage_while_recomputing_native_metrics(tmp_path: Path) -> None:
    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="director",
        input="Inspect.",
        context={
            "call_id": "coverage-call",
            "provider_request_semantic_audit": {
                "role": "director",
                "request_metadata_summary": {"task_id": "owned-task"},
                "final_request_context_audit": {
                    "final_request_token_estimate": 99999,
                    "coverage": {"pm_contract": True, "chief_engineer_blueprint": True, "target_files": True},
                    "final_request_evidence_coverage": {"missing_required_refs": []},
                },
            },
        },
    )
    port = NativeRequestCapturePort(
        request=request,
        model_spec=_Catalog().resolve("local-native", "local-model", {}),
        trace_id="coverage-run",
        messages=[{"role": "user", "content": "Inspect."}],
        provider_request={"role": "director", "final_request_context_audit": {}},
        persist=AIExecutor._store_context_messages_sync,
        workspace=str(tmp_path),
    )
    port.dispatch_sync(wire_request=_wire(), send=lambda _: "ok")
    audit = request.context["final_request_context_audit"]
    assert audit["coverage"]["chief_engineer_blueprint"] is True
    assert audit["final_request_token_estimate"] < 99999
    assert audit["audit_scope"] == "provider_native_wire"


def test_observation_port_does_not_enable_factory_sdk_constraints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from polaris.infrastructure.llm.providers.codex_sdk_provider import CodexSDKProvider
    from polaris.kernelone.llm.engine.contracts import bind_physical_provider_dispatch_port

    port, _ = _capture_port(tmp_path)
    provider = CodexSDKProvider()
    seen: list[dict[str, Any]] = []

    class Client:
        def invoke(self, **kwargs: Any) -> Any:
            seen.append(kwargs)
            return SimpleNamespace(content="accepted", usage=None, thinking=None, metadata={})

    monkeypatch.setattr(provider, "_get_sdk_client", lambda _: Client())
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.codex_sdk_provider._installed_openai_sdk_version",
        lambda: "ordinary-sdk-version",
    )
    with bind_physical_provider_dispatch_port(port):
        result = provider.invoke("inspect", "local-model", {"sdk_params": {}, "max_retries": 2})
    assert result.ok, result.error
    assert len(seen) == 1
    ref = port._request.context["context_snapshot_ref"]
    runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
    snapshot = json.loads((runtime / "contexts" / ref[:2] / ref).read_text(encoding="utf-8"))
    assert snapshot["provider_request"]["evidence_level"] == "provider_call_observation"
    assert "final_physical_request" not in snapshot["provider_request"]
    assert "final_physical_wire_hash" not in snapshot["provider_request"]["final_request_context_audit"]


def test_native_projection_change_does_not_keep_semantic_coverage_as_pass(tmp_path: Path) -> None:
    port, request = _capture_port(tmp_path)
    port._provider_request["final_request_context_audit"] = {
        "coverage": {"chief_engineer_blueprint": True},
        "final_request_evidence_coverage": {"pass": True},
    }
    port._messages = [{"role": "user", "content": "CURRENT CE CONTRACT"}]
    wire = _wire()
    wire["body"]["messages"] = [{"role": "user", "content": "contract removed"}]
    port.dispatch_sync(wire_request=wire, send=lambda _: "ok")
    audit = request.context["final_request_context_audit"]
    assert audit["coverage"]["chief_engineer_blueprint"] is False
    assert audit["final_request_evidence_coverage"]["pass"] is False


@pytest.mark.asyncio
async def test_cancel_during_capture_waits_for_preparation_and_never_sends(tmp_path: Path) -> None:
    port, request = _capture_port(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    real_persist = port._persist
    sent: list[str] = []

    def blocking(**kwargs: Any) -> str:
        if kwargs["provider_request"]["physical_attempt"]["status"] == "prepared":
            entered.set()
            assert release.wait(10)
        return real_persist(**kwargs)

    async def send(_: Any) -> str:
        sent.append("sent")
        return "ok"

    port._persist = blocking
    pending = asyncio.create_task(port.dispatch_async(wire_request=_wire(), send=send))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        pending.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert sent == []
        assert request.context["physical_request_capture"]["status"] == "cancelled"
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("host_owned", [False, True])
@pytest.mark.parametrize("outer_executor", [False, True])
async def test_ordinary_stream_preserves_native_capture_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host_owned: bool, outer_executor: bool
) -> None:
    from polaris.kernelone.events.final_request_evidence import ContextSnapshotReferenceOwnerV1
    from polaris.kernelone.llm.engine.context_store_retention import (
        ContextSnapshotAuditPinRepository,
        ContextStoreRetention,
        ContextStoreRetentionConfig,
    )

    observed: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            records = [
                {"type": "message_start", "message": {"usage": {"input_tokens": 11}}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "accepted"}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
                {"type": "message_stop"},
            ]
            payload = "".join("data: " + json.dumps(row) + "\n\n" for row in records).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    provider = AnthropicProvider()

    class Manager:
        def get_provider_instance(self, provider_type: str) -> AnthropicProvider:
            return provider

    monkeypatch.setattr("polaris.kernelone.llm.providers.get_provider_manager", Manager)
    monkeypatch.setenv("KERNELONE_LIGHTWEIGHT_STREAM_SESSIONS", "0")
    # Other provider tests can cache the lightweight transport before this real
    # loopback test; changing its environment flag alone cannot replace that cache.
    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers._http_pooling._aiohttp_module", None)
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._http_pooling._REAL_CLIENT_SESSION_TYPE", None
    )
    executor = (AIExecutor if outer_executor else StreamExecutor)(
        workspace=str(tmp_path),
        model_catalog=cast(ModelCatalog, _Catalog()),
        token_budget=cast(TokenBudgetManager, _Budget()),
        native_request_observer=lambda _request, observation: observations.append(dict(observation)),
        context_reference_owner=(
            lambda _request, provider: ContextSnapshotReferenceOwnerV1(
                "synthetic-stream-host", "host-run", provider, str(tmp_path)
            )
        )
        if host_owned
        else None,
    )
    monkeypatch.setattr(
        StreamExecutor,
        "_get_provider_config",
        lambda self, _: {
            "type": "anthropic_compat",
            "base_url": f"http://127.0.0.1:{server.server_port}",
            "api_key": "private-stream-key",
            "timeout": 5,
            "retries": 0,
        },
    )
    monkeypatch.setattr(StreamExecutor, "_resolve_provider_model", lambda self, _: ("local-native", "local-model"))
    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="director",
        provider_id="local-native",
        model="local-model",
        input="Inspect the candidate.",
        options={"max_tokens": 128},
        context={"call_id": "stream-native-call", "chat_messages": [{"role": "user", "content": "Inspect."}]},
    )
    try:
        events = [event async for event in executor.invoke_stream(request)]
        assert not [event for event in events if event.error], events
        assert len(observed) == 1
        ref = request.context.get("context_snapshot_ref")
        assert ref, "ordinary stream has no actual native context reference"
        runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
        snapshot = json.loads((runtime / "contexts" / ref[:2] / ref).read_text(encoding="utf-8"))
        assert snapshot["provider_request"]["final_physical_request"]["body"] == observed[0]
        assert snapshot["provider_request"]["execution_qualified"] is False
        assert snapshot["provider_request"]["physical_attempt"]["status"] == "completed"
        assert [item["phase"] for item in observations] == ["prepared", "terminal"]
        assert observations[-1]["context_snapshot_ref"] == ref
        assert observations[-1]["usage_status"] == "reported"
        assert observations[-1]["raw_provider_usage"] == {"output_tokens": 2}
        assert "synthetic-stream-host" not in json.dumps(observed[0]), "host owner leaked into provider body"
        repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path), read_only=True)
        assert bool(repository.query_reference_pins(ref)) is host_owned
        report = ContextStoreRetention(str(tmp_path), ContextStoreRetentionConfig(ttl_seconds=0)).sweep()
        assert report.inventory_complete
        assert (runtime / "contexts" / ref[:2] / ref).exists() is host_owned
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.asyncio
async def test_real_invoke_cancel_during_preparation_never_sends(tmp_path, monkeypatch):
    observed = []
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            observed.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            payload = json.dumps({"content": [{"type": "text", "text": "accepted"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider = AnthropicProvider()

    class Manager:
        def get_provider_instance(self, kind):
            return provider

    monkeypatch.setattr("polaris.kernelone.llm.engine.executor.get_provider_manager", Manager)
    real = AIExecutor._store_context_messages_sync

    def persist(*args, **kw):
        status = kw.get("provider_request", {}).get("physical_attempt", {}).get("status")
        if status == "prepared":
            entered.set()
            assert release.wait(5)
        result = real(*args, **kw)
        if status in ("completed", "failed", "cancelled"):
            finished.set()
        return result

    monkeypatch.setattr(AIExecutor, "_store_context_messages_sync", staticmethod(persist))
    executor = AIExecutor(
        workspace=str(tmp_path),
        model_catalog=cast(ModelCatalog, _Catalog()),
        token_budget=cast(TokenBudgetManager, _Budget()),
    )
    monkeypatch.setattr(
        executor,
        "_get_provider_config",
        lambda _: {
            "type": "anthropic_compat",
            "base_url": f"http://127.0.0.1:{server.server_port}",
            "api_key": "private-fixture",
            "timeout": 5,
            "retries": 0,
        },
    )
    request = AIRequest(
        task_type=TaskType.GENERATION,
        role="director",
        provider_id="local-native",
        model="local-model",
        input="Inspect.",
        options={"max_tokens": 128},
        context={
            "call_id": "cancel-invoke",
            "run_id": "cancel-run",
            "chat_messages": [{"role": "user", "content": "Inspect."}],
        },
    )
    task = asyncio.create_task(executor.invoke(request))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        assert observed == [], "Engine cancelled during native preflight but worker later opened real HTTP"
        assert request.context.get("physical_request_capture", {}).get("status") == "cancelled"
        assert request.context["physical_request_capture"]["stop_reason"] == "caller_cancelled"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("changed", ["tools", "tool_choice", "response_format"])
def test_matching_messages_cannot_retain_changed_native_surface_coverage(tmp_path: Path, changed: str) -> None:
    port, request = _capture_port(tmp_path)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ]
    port._provider_request.update(tools=tools, tool_choice="required", response_format={"type": "json_object"})
    port._provider_request["final_request_context_audit"] = {
        "coverage": {"required_tool_schema": True},
        "final_request_evidence_coverage": {
            "pass": True,
            "required_tools": ["read_file"],
            "available_tools": ["read_file"],
            "missing_required_tools": [],
        },
    }
    wire = _wire()
    wire["body"].update(tools=tools, tool_choice="required", response_format={"type": "json_object"})
    wire["body"][changed] = [] if changed == "tools" else None
    port.dispatch_sync(wire_request=wire, send=lambda _: "ok")
    audit = request.context["final_request_context_audit"]
    assert not audit["final_request_evidence_coverage"]["pass"]
    if changed == "tools":
        assert audit["tool_schema_count"] == 0
        assert audit["final_request_evidence_coverage"]["available_tools"] == []
        assert audit["final_request_evidence_coverage"]["missing_required_tools"] == ["read_file"]


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_matching_native_tool_surface_retains_verified_coverage(tmp_path: Path, protocol: str) -> None:
    from dataclasses import replace

    from polaris.kernelone.llm.engine.provider_native_request import convert_tools_to_anthropic

    port, request = _capture_port(tmp_path)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ]
    port._prepared_surface = {"tools": tools, "tool_choice": "required", "response_format": None}
    port._provider_request["final_request_context_audit"] = {
        "coverage": {"required_tool_schema": True},
        "final_request_evidence_coverage": {"pass": True, "required_tools": ["read_file"]},
    }
    wire = _wire()
    if protocol == "anthropic":
        wire["body"].update(system=None, tools=convert_tools_to_anthropic(tools), tool_choice={"type": "any"})
    else:
        port._model_spec = replace(port._model_spec, provider_type="openai")
        wire["body"].update(tools=tools, tool_choice="required")
    port.dispatch_sync(wire_request=wire, send=lambda _: "ok")
    audit = request.context["final_request_context_audit"]
    assert audit["native_surface_projection_matched"]
    assert audit["final_request_evidence_coverage"]["pass"]
    assert audit["final_request_evidence_coverage"]["available_tools"] == ["read_file"]
    assert audit["final_request_evidence_coverage"]["missing_required_tools"] == []


def test_native_observer_receives_saved_ref_before_terminal_usage(tmp_path: Path) -> None:
    from polaris.kernelone.llm.engine.contracts import Usage

    observed: list[dict[str, Any]] = []

    def observe(request: AIRequest, observation: Mapping[str, Any]) -> None:
        item = dict(observation)
        ref = item["context_snapshot_ref"]
        runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
        stored = (runtime / "contexts" / ref[:2] / ref).read_text(encoding="utf-8")
        assert hashlib.sha256(stored.encode("utf-8")).hexdigest()[:24] == ref
        assert request.context["context_snapshot_ref"] == ref
        observed.append(item)

    port, request = _capture_port(tmp_path, observe)
    result = SimpleNamespace(usage=Usage(prompt_tokens=11, completion_tokens=3, total_tokens=14))
    assert port.dispatch_sync(wire_request=_wire(), send=lambda _: result) is result
    assert [item["phase"] for item in observed] == ["prepared", "terminal"]
    assert [item["status"] for item in observed] == ["prepared", "completed"]
    assert all(item["dispatch_attempt_count"] == 1 for item in observed)
    assert all(item["physical_send_count"] is None for item in observed)
    assert all(item["physical_send_count_status"] == "unknown" for item in observed)
    assert observed[-1]["usage_status"] == "reported"
    assert observed[-1]["usage"]["total_tokens"] == 14
    assert observed[-1]["context_snapshot_ref"] == request.context["context_snapshot_ref"]
    assert observed[0]["context_snapshot_ref"] != observed[-1]["context_snapshot_ref"]


def test_native_observer_failure_does_not_repeat_or_replace_success(tmp_path: Path) -> None:
    sent: list[str] = []

    def observe(_request: AIRequest, _observation: Mapping[str, Any]) -> None:
        raise RuntimeError("observation store unavailable")

    port, request = _capture_port(tmp_path, observe)

    def send(_request: Any) -> str:
        sent.append("sent")
        return "real result"

    assert port.dispatch_sync(wire_request=_wire(), send=send) == "real result"
    assert sent == ["sent"]
    assert request.context["native_request_observer_errors"][-1]["code"] == "native_request_observer_failed"
    assert request.context["physical_request_capture"]["status"] == "completed"


def test_terminal_snapshot_failure_is_unavailable_without_repeating_send(tmp_path: Path) -> None:
    observed: list[dict[str, Any]] = []
    port, request = _capture_port(tmp_path, lambda _request, observation: observed.append(dict(observation)))
    persist = port._persist
    calls = 0

    def fail_terminal(**kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("terminal snapshot unavailable")
        return persist(**kwargs)

    port._persist = fail_terminal
    sent: list[str] = []

    def send(_request: Any) -> str:
        sent.append("sent")
        return "real result"

    assert port.dispatch_sync(wire_request=_wire(), send=send) == "real result"
    assert sent == ["sent"]
    assert observed[-1]["phase"] == "terminal"
    assert observed[-1]["snapshot_status"] == "unavailable"
    assert observed[-1]["context_snapshot_ref"] is None
    assert "context_snapshot_ref" not in request.context


@pytest.mark.parametrize(
    ("cache_fields", "reported_cache_fields", "normalized_cache_reported"),
    [
        pytest.param({}, {}, False, id="absent-cache-is-not-reported-zero"),
        pytest.param({"cache_read_input_tokens": 0}, {"cache_read_input_tokens": 0}, True, id="explicit-read-zero"),
        pytest.param(
            {"cache_creation_input_tokens": 0},
            {"cache_creation_input_tokens": 0},
            False,
            id="creation-zero-does-not-report-read-zero",
        ),
        pytest.param(
            {"cache_read_input_tokens": True, "cache_creation_input_tokens": -1},
            {},
            False,
            id="invalid-cache-counters-not-reported",
        ),
        pytest.param({"cached_prompt_tokens": 0}, {}, True, id="explicit-cached-prompt-alias-zero"),
        pytest.param({"prompt_tokens_details": {"cached_tokens": 0}}, {}, True, id="explicit-openai-cache-detail-zero"),
        pytest.param(
            {"prompt_tokens_details": {"cached_tokens": True}}, {}, False, id="invalid-openai-cache-detail-not-reported"
        ),
        pytest.param(
            {"cache_read_input_tokens": 3},
            {"cache_read_input_tokens": 3},
            False,
            id="raw-positive-does-not-confirm-normalized-zero",
        ),
    ],
)
def test_native_cache_usage_evidence_preserves_reported_zero_and_unknown_fields(
    tmp_path: Path,
    cache_fields: dict[str, Any],
    reported_cache_fields: dict[str, int],
    normalized_cache_reported: bool,
) -> None:
    from polaris.kernelone.llm.engine.contracts import Usage

    observed: list[dict[str, Any]] = []
    port, request = _capture_port(tmp_path, lambda _request, observation: observed.append(dict(observation)))
    raw_usage = {"input_tokens": 11, "output_tokens": 2, **cache_fields}
    result = SimpleNamespace(
        usage=Usage(prompt_tokens=11, completion_tokens=2, total_tokens=13),
        raw={"usage": dict(raw_usage)},
    )
    port.dispatch_sync(wire_request=_wire(), send=lambda _: result)

    runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
    ref = request.context["context_snapshot_ref"]
    snapshot = json.loads((runtime / "contexts" / ref[:2] / ref).read_text(encoding="utf-8"))
    for evidence in (observed[-1], snapshot["provider_request"]["usage_observation"]):
        assert evidence["usage_status"] == "reported"
        assert evidence["raw_provider_usage"] == {"input_tokens": 11, "output_tokens": 2, **reported_cache_fields}
        assert ("cached_tokens" in evidence["usage"]) is normalized_cache_reported
        if normalized_cache_reported:
            assert evidence["usage"]["cached_tokens"] == 0
    assert result.raw["usage"] == raw_usage


def test_unreported_usage_stays_unknown_in_native_observation(tmp_path: Path) -> None:
    from polaris.kernelone.llm.engine.contracts import Usage

    observed: list[dict[str, Any]] = []
    port, _request = _capture_port(tmp_path, lambda _request, observation: observed.append(dict(observation)))
    port.dispatch_sync(wire_request=_wire(), send=lambda _: SimpleNamespace(usage=Usage()))
    assert observed[-1]["usage_status"] == "unknown"
    assert observed[-1]["usage"] is None


def test_new_engine_context_coverage_does_not_require_uncalled_contracts(tmp_path: Path) -> None:
    port, request = _capture_port(tmp_path)
    request.context["engine_version"] = "engineering_v2"
    port.dispatch_sync(wire_request=_wire(), send=lambda _: "ok")
    coverage = request.context["final_request_context_audit"]["coverage"]
    assert coverage["pm_contract"] == "not_applicable"
    assert coverage["chief_engineer_blueprint"] == "not_applicable"
    assert coverage["original_goal"] == "present"
    assert "missing_pm_contract" not in str(request.context["final_request_context_audit"].get("context_quality", {}))


def test_observer_cannot_replace_materialized_ref_or_role(tmp_path: Path) -> None:
    def mutate(request: AIRequest, _observation: Mapping[str, Any]) -> None:
        request.context["context_snapshot_ref"] = "not-a-saved-reference"
        request.role = "pm"

    port, request = _capture_port(tmp_path, mutate)
    port.dispatch_sync(wire_request=_wire(), send=lambda _: "ok")
    assert request.role == "director"
    evidence = port.final_context_evidence()
    assert evidence is not None
    assert request.context["context_snapshot_ref"] == evidence[0]


def test_opaque_sdk_observation_keeps_physical_send_count_unknown(tmp_path: Path) -> None:
    observations: list[dict[str, Any]] = []
    port, request = _capture_port(tmp_path, lambda _request, observation: observations.append(dict(observation)))
    wire = _wire()
    wire["transport"]["kind"] = "openai-python-sdk"
    port.dispatch_sync(wire_request=wire, send=lambda _: "ok")
    assert observations[-1]["evidence_level"] == "provider_call_observation"
    assert observations[-1]["physical_send_count"] is None
    assert observations[-1]["usage_status"] == "unknown"
    assert request.context["final_request_context_audit"]["audit_scope"] == "provider_call_observation"
