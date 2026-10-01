"""Catch normalization-order drift before a valid request is refused at dispatch."""

from __future__ import annotations

from copy import deepcopy

import pytest
from polaris.infrastructure.llm.providers.openai_provider import _build_openai_payload
from polaris.kernelone.llm.engine.provider_native_request import (
    build_openai_native_messages,
    project_factory_provider_native_request,
)


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "Role identity"},
        {"role": "system", "content": "Contract"},
        {"role": "user", "content": "Current task"},
        {"role": "system", "content": "Tool batch constraint"},
        {"role": "system", "content": "Physical tool schema"},
        {"role": "system", "content": "Target ownership"},
        {"role": "user", "content": "Perform the authorized work"},
    ]


def test_each_mid_system_message_keeps_its_marker_before_merge() -> None:
    messages = _messages()
    before = deepcopy(messages)

    actual = build_openai_native_messages(messages)

    assert actual == [
        {"role": "system", "content": "Role identity\n\nContract"},
        {
            "role": "user",
            "content": (
                "Current task\n\n【系统提示】\nTool batch constraint"
                "\n\n【系统提示】\nPhysical tool schema"
                "\n\n【系统提示】\nTarget ownership"
                "\n\nPerform the authorized work"
            ),
        },
    ]
    assert messages == before


@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.parametrize("api_path", ["/v1/chat/completions", "/v1/responses"])
def test_native_authority_matches_real_body_with_mid_system_group(mode: str, api_path: str) -> None:
    semantic = {
        "model": "test-model",
        "messages": _messages(),
        "tools": [],
        "tool_choice": None,
        "response_format": None,
        "temperature": 0.15,
        "max_tokens": 1024,
        "stream": mode == "stream",
    }
    before = deepcopy(semantic)
    config = {
        "base_url": "https://provider.test/v1",
        "api_path": api_path,
        "chat_messages": semantic["messages"],
        "temperature": semantic["temperature"],
        "max_tokens": semantic["max_tokens"],
    }

    projection = project_factory_provider_native_request(
        provider_type="openai_compat",
        mode=mode,  # type: ignore[arg-type]
        final_payload=semantic,
        provider_config=config,
    )
    actual = _build_openai_payload(
        prompt="Flattened prompt must not replace structured messages",
        model="test-model",
        config=config,
        api_path=api_path,
        stream=mode == "stream",
    )

    assert projection is not None
    assert projection.expected_body() == actual
    field = "input" if api_path == "/v1/responses" else "messages"
    assert actual[field][1]["content"].count("【系统提示】") == 3
    assert semantic == before
