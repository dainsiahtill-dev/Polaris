"""Configured output ceilings, effective context budget and actual native transport."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from polaris.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from polaris.infrastructure.llm.providers.openai_provider import OpenAIProvider
from polaris.kernelone.llm.config_store import load_llm_config, save_llm_config
from polaris.kernelone.llm.engine._executor_base import build_invoke_config
from polaris.kernelone.llm.engine.contracts import AIRequest, ModelSpec, StreamEventType, TaskType
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.prompt_budget import TokenBudgetManager
from polaris.kernelone.storage.io_paths import resolve_storage_roots


def test_output_metadata_is_the_default_ceiling_not_3000():
    assert build_invoke_config({"max_output_tokens": 1_000_000}, {})["max_tokens"] == 1_000_000
    assert build_invoke_config({"max_output_tokens": 1_000_000, "max_tokens": 2048}, {})["max_tokens"] == 2048
    assert build_invoke_config({"max_output_tokens": 1_000_000}, {"max_tokens": 512})["max_tokens"] == 512
    assert build_invoke_config({}, {})["max_tokens"] == 3000


def test_ceiling_does_not_reserve_the_entire_context_window():
    spec = ModelSpec("probe", "openai_compat", "probe", 1_000_000, 1_000_000)
    decision = TokenBudgetManager().enforce("The complete current user requirement.", spec, 1_000_000)
    assert decision.allowed and not decision.compression_applied
    assert decision.reserved_output_tokens == 256
    assert decision.allowed_prompt_tokens > 900_000


@pytest.mark.parametrize("configured_kind", ["configured", "null"])
@pytest.mark.parametrize("model_cap", [1_000_000, 524288])
@pytest.mark.parametrize("request_limit", [None, 512])
@pytest.mark.parametrize("role", ["pm", "chief_engineer", "director", "qa"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("protocol", ["anthropic_compat", "openai_compat"])
async def test_saved_ceiling_cold_load_reaches_actual_native_body_and_audit(
    tmp_path, monkeypatch, role, stream, protocol, model_cap, request_limit, configured_kind
):
    """Only the remote model is replaced. Config, catalog, budgets and HTTP remain real."""
    from polaris.infrastructure.llm.providers.provider_helpers import _http_pooling

    await _http_pooling.close_stream_sessions()
    monkeypatch.setattr(_http_pooling, "_aiohttp_module", None)
    monkeypatch.setattr(_http_pooling, "_REAL_CLIENT_SESSION_TYPE", None)
    monkeypatch.setenv("KERNELONE_LIGHTWEIGHT_STREAM_SESSIONS", "0")
    bodies: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            bodies.append(body)
            if body.get("stream"):
                if protocol == "anthropic_compat":
                    events = [
                        {
                            "type": "message_start",
                            "message": {
                                "id": "probe",
                                "type": "message",
                                "role": "assistant",
                                "model": "probe",
                                "usage": {"input_tokens": 20, "output_tokens": 0},
                            },
                        },
                        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "observed"},
                        },
                        {"type": "content_block_stop", "index": 0},
                        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
                        {"type": "message_stop"},
                    ]
                    text = "".join(
                        "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events
                    )
                else:
                    events = [
                        {"choices": [{"delta": {"content": "observed"}, "finish_reason": None}]},
                        {
                            "choices": [{"delta": {}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 20, "completion_tokens": 1, "total_tokens": 21},
                        },
                    ]
                    text = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
                response = text.encode("utf-8")
                media = "text/event-stream"
            else:
                value = (
                    {
                        "content": [{"type": "text", "text": "observed"}],
                        "stop_reason": "end_turn",
                        "usage": {"input_tokens": 20, "output_tokens": 1},
                    }
                    if protocol == "anthropic_compat"
                    else {
                        "choices": [{"message": {"role": "assistant", "content": "observed"}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 1, "total_tokens": 21},
                    }
                )
                response = json.dumps(value).encode("utf-8")
                media = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    config_path = tmp_path / "host-llm.json"
    monkeypatch.setenv("KERNELONE_LLM_CONFIG", str(config_path))
    payload = {
        "schema_version": 2,
        "providers": {
            "ceiling-probe": {
                "type": protocol,
                "base_url": f"http://127.0.0.1:{server.server_port}",
                "api_key": "fixture-only",
                "model": "ceiling-model",
                "max_tokens": None if configured_kind == "null" else 1_000_000,
                "max_output_tokens": model_cap,
                "max_context_tokens": 1_000_000,
                "timeout": 10,
                "retries": 0,
            }
        },
        "roles": {},
        "policies": {"required_ready_roles": []},
    }
    observations = []
    receipts = []
    try:
        save_llm_config(str(tmp_path), str(tmp_path / "cache"), payload)
        cold = load_llm_config(str(tmp_path), str(tmp_path / "cache"))
        configured = model_cap if configured_kind == "null" else 1_000_000
        assert cold["providers"]["ceiling-probe"]["max_tokens"] == (None if configured_kind == "null" else 1_000_000)
        provider = AnthropicProvider() if protocol == "anthropic_compat" else OpenAIProvider()

        class Manager:
            def get_provider_instance(self, _kind):
                return provider

        monkeypatch.setattr("polaris.kernelone.llm.engine.executor.get_provider_manager", Manager)
        monkeypatch.setattr("polaris.kernelone.llm.providers.get_provider_manager", Manager)
        executor = AIExecutor(
            workspace=str(tmp_path),
            native_request_observer=lambda _r, observation: observations.append(observation),
            final_request_receipt_sink=receipts.append,
        )
        messages = [
            {"role": "system", "content": f"You are {role}."},
            {"role": "user", "content": "Retain the complete current requirement and inspect actual results."},
        ]
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role=role,
            provider_id="ceiling-probe",
            model="ceiling-model",
            input=messages[1]["content"],
            options={
                "messages": messages,
                "max_retries": 0,
                **({"max_tokens": request_limit} if request_limit is not None or configured_kind == "null" else {}),
            },
            context={"workspace": str(tmp_path), "engine_version": "engineering_v2"},
        )
        if stream:
            events = [event async for event in executor.invoke_stream(request)]
            assert not [event for event in events if event.type == StreamEventType.ERROR], events
        else:
            response = await executor.invoke(request)
            assert response.ok, response.error
        assert len(bodies) == 1
        actual = bodies[0]["max_tokens"]
        if request_limit is not None:
            assert actual == request_limit
        elif model_cap < 1_000_000:
            assert actual == model_cap
        else:
            assert 900_000 < actual < 1_000_000
        assert (
            (bodies[0].get("system") == messages[0]["content"])
            if protocol == "anthropic_compat"
            else bodies[0]["messages"][0] == messages[0]
        )
        ref = request.context["context_snapshot_ref"]
        runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
        snapshot = json.loads((runtime / "contexts" / ref[:2] / ref).read_text(encoding="utf-8"))
        saved = snapshot["provider_request"]
        assert saved["final_physical_request"]["body"] == bodies[0]
        assert saved["final_request_context_audit"]["output_token_limit"] == actual
        assert saved["final_request_context_audit"]["generation_budget_matches_wire"] is True
        budget = saved["final_request_context_audit"]["generation_budget"]
        requested = request_limit or configured
        assert budget["configured_output_ceiling_tokens"] == configured
        assert budget["requested_output_tokens"] == requested
        assert budget["output_ceiling_tokens"] == min(requested, model_cap)
        assert budget["effective_output_tokens"] == actual
        expected_reasons = []
        if requested > model_cap:
            expected_reasons.append("model_output_limit")
        if actual < min(requested, model_cap):
            expected_reasons.append("context_window_headroom")
        assert budget["adjustment_reasons"] == expected_reasons
        assert (
            budget["effective_output_tokens"]
            + budget["estimated_prompt_tokens"]
            + budget["payload_overhead_tokens"]
            + budget["safety_reserve_tokens"]
            <= 1_000_000
        )
        assert observations[-1]["audit"]["generation_budget"] == budget
        assert receipts[-1]["payload"]["token_budget"]["generation_budget"] == budget
    finally:
        from polaris.infrastructure.llm.providers.provider_helpers._http_pooling import close_stream_sessions

        await close_stream_sessions()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize("wire_limit", [128, 1024, None])
def test_wire_override_cannot_bypass_the_admitted_effective_output(wire_limit):
    from copy import deepcopy

    from polaris.kernelone.llm.engine.native_request_capture import NativeRequestCapturePort

    request = AIRequest(task_type=TaskType.DIALOGUE, role="director", input="goal")
    records = []
    sent = []

    def persist(**values):
        records.append(deepcopy(values["provider_request"]))
        return "a" * 24

    port = NativeRequestCapturePort(
        request=request,
        model_spec=ModelSpec("probe", "openai_compat", "probe", 4096, 512),
        trace_id="budget-fixture",
        messages=[{"role": "user", "content": "goal"}],
        provider_request={"final_request_context_audit": {"generation_budget": {"effective_output_tokens": 512}}},
        persist=persist,
        workspace=None,
    )
    with pytest.raises(RuntimeError, match="output_budget_wire_mismatch"):
        port.dispatch_sync(
            wire_request={
                "transport": {"kind": "http"},
                "body": {"model": "probe", "messages": [{"role": "user", "content": "goal"}], "max_tokens": wire_limit},
            },
            send=lambda wire: sent.append(wire),
        )
    assert sent == []
    assert records[-1]["physical_attempt"]["status"] == "failed"
    assert records[-1]["final_request_context_audit"]["output_token_limit"] == wire_limit
    assert records[-1]["final_request_context_audit"]["generation_budget_matches_wire"] is False


def test_missing_model_metadata_stays_unavailable_and_smaller_caps_are_retained(tmp_path):
    from polaris.kernelone.llm.engine._executor_base import resolve_requested_output_tokens
    from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

    catalog = ModelCatalog(str(tmp_path))
    with pytest.raises(ValueError, match="not configured"):
        catalog.resolve("probe", "unknown-model", {"type": "openai_compat"})
    spec = catalog.resolve(
        "probe", "unknown-model", {"type": "openai_compat", "max_context_tokens": 1_000_000, "max_output_tokens": 4096}
    )
    assert resolve_requested_output_tokens({}, {"max_tokens": 1_000_000}, spec) == 4096
    assert resolve_requested_output_tokens({"max_tokens": 256}, {"max_tokens": 1_000_000}, spec) == 256


def test_configuration_agent_accepts_platform_ceiling_and_rejects_outside_range():
    from polaris.cells.llm.control_plane.public.service import validate_llm_provider_cfg as _validate_provider_cfg
    from polaris.kernelone.llm.config_store import ProviderConfig

    _validate_provider_cfg({"max_tokens": 1_000_000})
    ProviderConfig(type="anthropic_compat", max_tokens=1_000_000, max_output_tokens=524288)
    for value in [0, 1_000_001]:
        with pytest.raises(ValueError):
            _validate_provider_cfg({"max_tokens": value})
        with pytest.raises(ValueError):
            ProviderConfig(type="anthropic_compat", max_tokens=value)


def test_null_request_is_absent_but_invalid_numbers_are_not():
    assert (
        build_invoke_config({"max_tokens": 2048, "max_output_tokens": 524288}, {"max_tokens": None})["max_tokens"]
        == 2048
    )
    assert build_invoke_config({"max_tokens": None, "max_output_tokens": 524288}, {})["max_tokens"] == 524288
    for invalid in (0, -1):
        with pytest.raises(ValueError, match="max_tokens"):
            build_invoke_config({"max_output_tokens": 524288}, {"max_tokens": invalid})
