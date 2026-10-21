"""Serialization length cannot claim visual token usage or window occupancy."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from polaris.kernelone.llm.engine.native_request_metrics import provider_native_request_metrics

_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6VRAAAAAASUVORK5CYII="
_HASH = "76082f2d5036598b4b110a39655d79d45263bf8e9283464accac587b9f6d838e"


def _metrics(body: dict[str, Any], protocol: str = "anthropic_messages") -> dict[str, Any]:
    return provider_native_request_metrics(body=body, native_protocol=protocol, context_window_tokens=1000000)


@pytest.mark.parametrize(
    "protocol,image",
    [
        (
            "anthropic_messages",
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _PNG}},
        ),
        ("openai_chat_completions", {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _PNG}}),
        ("openai_responses", {"type": "input_image", "image_url": "data:image/png;base64," + _PNG}),
    ],
)
def test_image_bytes_are_auditable_while_visual_token_totals_stay_unknown(protocol: str, image: dict[str, Any]) -> None:
    messages = [{"role": "user", "content": [{"type": "text", "text": "Current frame"}, image]}]
    body = {"model": "test", "input" if protocol == "openai_responses" else "messages": messages}
    before = deepcopy(body)
    actual = _metrics(body, protocol)
    assert actual["image_count"] == 1
    assert actual["image_bytes"] == 68
    assert actual["image_sha256"] == [_HASH]
    assert actual["visual_token_estimate"] is None
    assert actual["visual_token_status"] == "unknown"
    for key in (
        "message_token_estimate",
        "final_request_token_estimate",
        "context_window_utilization",
        "available_token_headroom",
        "context_underutilized",
    ):
        assert actual[key] is None
    assert actual["serialized_message_token_estimate"] > 0
    assert actual["serialized_final_request_token_estimate"] >= actual["serialized_message_token_estimate"]
    assert body == before


def test_nested_tool_result_images_are_counted_but_tool_arguments_are_not_images() -> None:
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _PNG}}
    actual = _metrics(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call", "name": "capture", "input": {"example": image}}],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call", "content": [image, image]}],
                },
            ]
        }
    )
    assert actual["image_count"] == 2
    assert actual["image_bytes"] == 136
    assert actual["image_sha256"] == [_HASH, _HASH]


@pytest.mark.parametrize(
    "image",
    [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "%%%"}},
        {"type": "image", "source": {"type": "url", "url": "https://unfetched.invalid/image.png"}},
        {"type": "image_url", "image_url": {"url": "https://unfetched.invalid/image.png"}},
        {"type": "image", "source": {"type": "base64", "data": ""}},
        {"type": "image", "source": None},
        {"type": "image", "source": {"type": "base64", "media_type": [], "data": _PNG}},
    ],
)
def test_unknown_image_bytes_are_not_zero_or_invented_hashes(image: dict[str, Any]) -> None:
    actual = _metrics({"messages": [{"role": "user", "content": [image]}]})
    assert actual["image_count"] == 1
    assert actual["image_bytes"] is None
    assert actual["image_sha256"] == []
    assert actual["visual_token_status"] == "unknown"
    assert actual["final_request_token_estimate"] is None


def test_text_only_request_preserves_previous_estimate_semantics() -> None:
    body = {"messages": [{"role": "user", "content": "Ordinary text"}], "model": "test", "max_tokens": 30}
    actual = _metrics(body)
    assert isinstance(actual["message_token_estimate"], int)
    assert isinstance(actual["final_request_token_estimate"], int)
    assert isinstance(actual["context_window_utilization"], float)
    assert actual.get("image_count", 0) == 0
    assert actual.get("image_sha256", []) == []


def test_unrecognized_content_type_does_not_break_request_observation() -> None:
    actual = _metrics({"messages": [{"role": "user", "content": [{"type": []}]}]})
    assert actual.get("image_count", 0) == 0
