"""Synthetic native HTTP/SSE fixtures: structured no-image cost reaches output clamp."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pytest
from polaris.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from polaris.infrastructure.llm.providers.openai_provider import OpenAIProvider
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.llm.engine.contracts import AIRequest, ModelSpec, TaskType
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.model_catalog import ModelCatalog
from polaris.kernelone.llm.engine.visual_working_set import visual_payload_cost
from polaris.kernelone.storage.io_paths import resolve_storage_roots


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", ["anthropic_compat", "openai_compat"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model_ceiling", [262144, 524288])
async def test_no_image_native_arguments_share_admission_and_generation_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_type: str, stream: bool, model_ceiling: int
) -> None:
    """Remote model is replaced, native codecs/budget/capture and local transport are real."""
    monkeypatch.setenv("KERNELONE_LIGHTWEIGHT_STREAM_SESSIONS", "0")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    bodies: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if provider_type == "anthropic_compat":
                payload = {
                    "content": [{"type": "text", "text": "fixture"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
                chunks = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": "fixture",
                            "type": "message",
                            "role": "assistant",
                            "model": "fixture-model",
                            "content": [],
                            "usage": {"input_tokens": 1, "output_tokens": 0},
                        },
                    },
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "fixture"}},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
                    {"type": "message_stop"},
                ]
            else:
                payload = {
                    "choices": [{"message": {"content": "fixture"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
                chunks = [
                    {"choices": [{"delta": {"content": "fixture"}, "finish_reason": None}]},
                    {
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    },
                ]
            encoded = (
                "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                + ("data: [DONE]\n\n" if provider_type == "openai_compat" else "")
                if stream
                else json.dumps(payload)
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    provider = AnthropicProvider() if provider_type == "anthropic_compat" else OpenAIProvider()

    class Manager:
        def get_provider_instance(self, kind: str) -> Any:
            assert kind == provider_type
            return provider

    class Catalog:
        def resolve(self, *args: Any) -> ModelSpec:
            return ModelSpec(
                provider_id="fixture-only",
                provider_type=provider_type,
                model="fixture-model",
                max_context_tokens=1000000,
                max_output_tokens=model_ceiling,
            )

    monkeypatch.setattr("polaris.kernelone.llm.engine.executor.get_provider_manager", Manager)
    monkeypatch.setattr("polaris.kernelone.llm.providers.get_provider_manager", Manager)
    executor = AIExecutor(workspace=str(tmp_path), model_catalog=cast(ModelCatalog, Catalog()))
    config = {
        "type": provider_type,
        "base_url": f"http://127.0.0.1:{server.server_port}",
        "api_key": "synthetic-fixture-only",
        "timeout": 5,
        "retries": 0,
        "max_tokens": 524288,
    }
    monkeypatch.setattr(executor, "_get_provider_config", lambda _: dict(config))
    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.stream.executor.StreamExecutor._get_provider_config",
        lambda _self, identity: dict(config),
    )
    arguments = {"file": "synthetic.txt", "content": "x" * 2800000}
    messages = [
        {"role": "system", "content": "You are QA in a synthetic transport fixture."},
        {"role": "user", "content": "Read the fixture."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "original-call",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps(arguments)},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "original-call", "content": "Synthetic result; no tool was executed."},
        {"role": "user", "content": "Judge the result."},
    ]
    expected = visual_payload_cost(messages)
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="qa",
        provider_id="fixture-only",
        model="fixture-model",
        input="Judge the result.",
        options={"messages": messages, "max_tokens": 524288, "max_retries": 0},
        context={"workspace": str(tmp_path), "engine_version": "engineering_v2", "call_id": "fixture-budget"},
    )
    try:
        if stream:
            events = [event async for event in executor.invoke_stream(request)]
            assert not any(event.error for event in events), events
        else:
            response = await executor.invoke(request)
            assert response.ok, response.error
        assert len(bodies) == 1
        body = bodies[0]
        generation = request.context["generation_budget"]
        assert (
            expected["admission_proxy_tokens"]
            + body["max_tokens"]
            + generation["payload_overhead_tokens"]
            + generation["safety_reserve_tokens"]
            <= 1000000
        )
        assert generation["estimated_prompt_tokens"] == expected["admission_proxy_tokens"]
        assert generation["input_payload_cost"] == expected
        assert expected["image_count"] == 0
        assert body["max_tokens"] <= model_ceiling
        assert generation["model_output_limit_tokens"] == model_ceiling
        if provider_type == "anthropic_compat":
            actual_arguments = next(
                block["input"]
                for message in body["messages"]
                for block in message["content"]
                if isinstance(block, dict) and block.get("type") == "tool_use"
            )
        else:
            actual_arguments = json.loads(
                next(
                    message["tool_calls"][0]["function"]["arguments"]
                    for message in body["messages"]
                    if message.get("tool_calls")
                )
            )
        assert actual_arguments == arguments
        ref = request.context["context_snapshot_ref"]
        runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
        fs = KernelFileSystem(str(runtime), LocalFileSystemAdapter(), read_only_paths=True)
        saved = json.loads(fs.workspace_read_text(f"contexts/{ref[:2]}/{ref}", encoding="utf-8"))["provider_request"]
        assert saved["final_physical_request"]["body"] == body
        assert saved["physical_attempt"]["status"] == "completed"
        assert saved["final_request_context_audit"]["generation_budget_matches_wire"] is True
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
