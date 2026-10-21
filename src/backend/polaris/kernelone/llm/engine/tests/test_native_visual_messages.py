"""Images must remain images across native codecs and durable wire capture."""

from __future__ import annotations

import base64
import json
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pytest
from polaris.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from polaris.infrastructure.llm.providers.openai_provider import OpenAIProvider
from polaris.kernelone.llm.engine.contracts import AIRequest, ModelSpec, TaskType
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.model_catalog import ModelCatalog
from polaris.kernelone.llm.engine.prompt_budget import compress_chat_messages_to_budget
from polaris.kernelone.llm.engine.provider_native_request import (
    FactoryProviderNativeRequestProjectionError,
    build_anthropic_native_messages,
    build_openai_native_messages,
)
from polaris.kernelone.storage.io_paths import resolve_storage_roots

_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6VRAAAAAASUVORK5CYII="
_PNG_SHA256 = "76082f2d5036598b4b110a39655d79d45263bf8e9283464accac587b9f6d838e"


def _image() -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _PNG}}


@pytest.mark.parametrize("stream", [False, True])
def test_unmigrated_responses_visual_route_rejects_instead_of_sending_chat_image_blocks(stream: bool) -> None:
    from polaris.infrastructure.llm.providers.openai_provider import _build_openai_payload

    messages = [{"role": "user", "content": [{"type": "text", "text": "Inspect"}, _image()]}]
    config = {"api_path": "/v1/responses", "base_url": "https://synthetic.invalid/v1", "chat_messages": messages}
    with pytest.raises(
        FactoryProviderNativeRequestProjectionError, match="visual_protocol_unsupported:openai_responses"
    ):
        _build_openai_payload(
            prompt="Inspect", model="synthetic", config=config, api_path="/v1/responses", stream=stream
        )


def _history() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "You are QA."},
        {"role": "user", "content": "Inspect the current candidate."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": identity,
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"file":"index.html"}'},
                }
                for identity in ("read-one", "read-two")
            ],
        },
        {"role": "tool", "tool_call_id": "read-one", "content": "first result"},
        {"role": "tool", "tool_call_id": "read-two", "content": "second result"},
        {"role": "user", "content": [{"type": "text", "text": "Current capture"}, _image()]},
        {"role": "user", "content": "Judge this image."},
    ]


def test_openai_converts_image_without_stringifying_mixed_user_messages() -> None:
    messages = [
        {"role": "user", "content": "Before"},
        {"role": "user", "content": [{"type": "text", "text": "Frame"}, _image()]},
        {"role": "user", "content": "After"},
    ]
    before = deepcopy(messages)
    actual = build_openai_native_messages(messages)
    assert actual == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Before"},
                {"type": "text", "text": "Frame"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _PNG}},
                {"type": "text", "text": "After"},
            ],
        }
    ]
    assert messages == before
    actual[0]["content"][2]["image_url"]["url"] = "changed"
    assert messages == before


def test_openai_preserves_provider_native_image_url_blocks() -> None:
    messages = [
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + _PNG, "detail": "low"}}],
        }
    ]
    before = deepcopy(messages)
    actual = build_openai_native_messages(messages)
    assert actual == before
    actual[0]["content"][0]["image_url"]["detail"] = "high"
    assert messages == before


@pytest.mark.parametrize(
    "source",
    [
        {"type": "base64", "media_type": "image/png", "data": "%%%"},
        {"type": "base64", "media_type": "image/png", "data": ""},
        {"type": "base64", "media_type": "text/plain", "data": _PNG},
        {"type": "base64", "media_type": [], "data": _PNG},
        {"type": "unrepresentable", "data": _PNG},
    ],
)
def test_openai_rejects_unrepresentable_canonical_images(source: dict[str, Any]) -> None:
    with pytest.raises(FactoryProviderNativeRequestProjectionError, match="native_message_image_invalid"):
        build_openai_native_messages([{"role": "user", "content": [{"type": "image", "source": source}]}])


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
def test_image_messages_follow_all_correlated_tool_results(protocol: str) -> None:
    messages = _history()
    before = deepcopy(messages)
    budgeted = compress_chat_messages_to_budget(messages, 10000)
    assert budgeted is not None
    if protocol == "anthropic":
        actual, system = build_anthropic_native_messages(budgeted)
        assert system == "You are QA."
        assert [block["id"] for block in actual[1]["content"]] == ["read-one", "read-two"]
        assert [block["type"] for block in actual[2]["content"]] == [
            "tool_result",
            "tool_result",
            "text",
            "image",
            "text",
        ]
        assert [block["tool_use_id"] for block in actual[2]["content"][:2]] == ["read-one", "read-two"]
        assert actual[2]["content"][3] == _image()
    else:
        actual = build_openai_native_messages(budgeted)
        assert [call["id"] for call in actual[2]["tool_calls"]] == ["read-one", "read-two"]
        assert [(message["role"], message.get("tool_call_id")) for message in actual[3:5]] == [
            ("tool", "read-one"),
            ("tool", "read-two"),
        ]
        assert actual[5]["content"][1]["image_url"]["url"] == "data:image/png;base64," + _PNG
        assert actual[5]["content"][2] == {"type": "text", "text": "Judge this image."}
    assert messages == before


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", ["anthropic_compat", "openai_compat"])
async def test_real_native_http_body_and_snapshot_preserve_image_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_type: str
) -> None:
    """A local HTTP server replaces the remote model, not native conversion/capture."""
    bodies: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            payload = (
                {"content": [{"type": "text", "text": "observed"}], "usage": {"input_tokens": 17, "output_tokens": 2}}
                if provider_type == "anthropic_compat"
                else {
                    "choices": [{"message": {"content": "observed"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 17, "completion_tokens": 2, "total_tokens": 19},
                }
            )
            response = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

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
                provider_id="local-visual",
                provider_type=provider_type,
                model="local-model",
                max_context_tokens=100000,
                max_output_tokens=512,
                supports_vision=True,
            )

    monkeypatch.setattr("polaris.kernelone.llm.engine.executor.get_provider_manager", Manager)
    executor = AIExecutor(
        workspace=str(tmp_path),
        model_catalog=cast(ModelCatalog, Catalog()),
        native_request_observer=lambda request, observation: observations.append(dict(observation)),
    )
    monkeypatch.setattr(
        executor,
        "_get_provider_config",
        lambda _: {
            "type": provider_type,
            "base_url": f"http://127.0.0.1:{server.server_port}",
            "api_key": "fixture-only-key",
            "timeout": 5,
            "retries": 0,
        },
    )
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="qa",
        provider_id="local-visual",
        model="local-model",
        input="Inspect the current candidate.",
        options={"messages": _history(), "max_tokens": 64, "max_retries": 0},
        context={"workspace": str(tmp_path), "engine_version": "engineering_v2", "call_id": "visual-call"},
    )
    try:
        response = await executor.invoke(request)
        assert response.ok, response.error
        assert len(bodies) == 1
        ref = request.context["context_snapshot_ref"]
        runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
        snapshot = json.loads((runtime / "contexts" / ref[:2] / ref).read_text(encoding="utf-8"))
        saved = snapshot["provider_request"]
        assert saved["final_physical_request"]["body"] == bodies[0]
        content = bodies[0]["messages"][-1]["content"]
        block = next(
            item
            for item in content
            if item["type"] == ("image" if provider_type == "anthropic_compat" else "image_url")
        )
        encoded = (
            block["source"]["data"]
            if provider_type == "anthropic_compat"
            else block["image_url"]["url"].split(",", 1)[1]
        )
        assert base64.b64decode(encoded, validate=True) == base64.b64decode(_PNG)
        audit = saved["final_request_context_audit"]
        assert audit["coverage_verification"] == "native_message_projection_matched"
        assert audit["image_sha256"] == [_PNG_SHA256]
        assert audit["image_bytes"] == 68
        assert audit["final_request_token_estimate"] is None
        assert audit["context_window_utilization"] is None
        assert saved["physical_attempt"]["status"] == "completed"
        assert observations[-1]["audit"]["image_sha256"] == [_PNG_SHA256]
        assert observations[-1]["audit"]["visual_token_status"] == "unknown"
        assert observations[-1]["physical_send_count"] is None
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
