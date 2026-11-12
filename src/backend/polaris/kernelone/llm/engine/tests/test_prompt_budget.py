"""Prompt budget edge-case coverage."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import pytest
import requests
from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import (
    _isolated_external_delivery as _isolated_external_delivery,
)
from polaris.infrastructure.llm.providers.provider_helpers import _http_pooling
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.prompt_budget import (
    _estimate_message_tokens,
    compress_chat_messages_to_budget,
    compress_native_tool_history,
)


def test_compress_chat_messages_noops_when_messages_fit_exact_budget() -> None:
    messages = [
        {"role": "system", "content": "system anchor"},
        {"role": "user", "content": "first turn"},
        {"role": "assistant", "content": "ack"},
        {"role": "user", "content": "current request"},
    ]
    exact_budget = sum(_estimate_message_tokens(str(item["content"]), "general") for item in messages)

    result = compress_chat_messages_to_budget(messages, exact_budget)

    assert result == messages


@pytest.mark.parametrize(
    "role,cap,window,want,diagnostic",
    [
        ("pm", 65536, 1000000, 60000, None),
        ("chief_engineer", 65536, 1000000, 60000, None),
        ("pm", 32768, 1000000, 32768, None),
        ("chief_engineer", 32768, 1000000, 32768, None),
        ("pm", 65536, 60032, "window", None),
        ("chief_engineer", 65536, 60032, "window", None),
        ("pm", 65536, 60200, "window", None),
        ("chief_engineer", 65536, 60200, "window", None),
        ("director", 32768, 1000000, 32768, None),
        ("qa", 32768, 1000000, 32768, None),
    ],
)
async def test_planning_budget_at_actual_http_boundary(tmp_path, monkeypatch, role, cap, window, want, diagnostic):
    wires: list[dict[str, Any]] = []
    executor = AIExecutor(workspace=str(tmp_path))
    configuration = {
        "type": "openai_compat",
        "base_url": "https://planning-budget.example.test/v1",
        "retries": 0,
        "model_specific": {"budget-model": {"max_context_tokens": window, "max_output_tokens": cap}},
    }
    monkeypatch.setattr(executor, "_get_provider_config", lambda _: configuration)

    def post(url, *, headers, json: dict[str, Any], timeout):
        assert url == "https://planning-budget.example.test/v1/chat/completions"
        wires.append(json)
        response = requests.Response()
        response.status_code = 200
        response.encoding = "utf-8"
        response._content = b'{"choices":[{"message":{"role":"assistant","content":"complete"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":1,"total_tokens":11}}'
        return response

    monkeypatch.setattr(_http_pooling, "bounded_http_post", post)
    requirement = "Preserve original requirement exactly: distinctive domain constraint."
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role=role,
        input=requirement,
        provider_id="budget-provider",
        model="budget-model",
        options={"max_tokens": 60000, "timeout": 750},
        context={
            "chat_messages": [
                {
                    "role": "system",
                    "content": f"You are {role}" + (" retained system context" * 1000 if window == 60200 else ""),
                },
                {"role": "user", "content": requirement},
            ]
        },
    )
    response = await executor._execute_invoke(request, "budget-test")
    (tmp_path / "planning-budget-http-proof.json").write_text(
        json.dumps({"request": asdict(request), "response": asdict(response), "wires": wires}, default=str, indent=2),
        encoding="utf-8",
    )
    assert request.context["chat_messages"][-1]["content"] == requirement
    if diagnostic:
        assert not response.ok and wires == []
        assert diagnostic in str(response.error)
    else:
        assert response.ok, response.error
        assert len(wires) == 1
        if want == "window":
            budget = request.context["generation_budget"]
            assert wires[0]["max_tokens"] == budget["effective_output_tokens"] < 60000
            assert "context_window_headroom" in budget["adjustment_reasons"]
            assert wires[0]["messages"] == request.context["chat_messages"]
        else:
            assert wires[0]["max_tokens"] == want
        assert requirement in "\n".join(message["content"] for message in wires[0]["messages"])


def _native_turn(index: int, payload: str = "x" * 400) -> list[dict[str, Any]]:
    return [
        {
            "role": "assistant",
            "content": f"turn {index}",
            "tool_calls": [
                {
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {"name": "execute_command", "arguments": json.dumps({"command": payload})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": f"call-{index}", "content": f"result {index} {payload}"},
    ]


def test_compress_native_tool_history_drops_whole_old_turns() -> None:
    """A native working set is bounded by dropping whole turns.

    The structured path refuses a native tool history, because trimming text
    could erase a tool call id or one half of a pair. Without a bound the
    native working set grows until the provider window refuses the request.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": "deliver the artifact"}]
    for index in range(12):
        messages.extend(_native_turn(index))

    compressed = compress_native_tool_history(messages, 600)

    assert compressed is not None
    assert len(compressed) < len(messages)
    # The leading goal and the final turn survive.
    assert compressed[0]["role"] == "user" and compressed[0]["content"] == "deliver the artifact"
    assert compressed[-1]["role"] == "tool" and compressed[-1]["tool_call_id"] == "call-11"
    # Every kept tool result still answers a kept assistant tool call.
    answered = {
        call["id"]
        for message in compressed
        for call in (message.get("tool_calls") or [])
    }
    for message in compressed:
        if message.get("role") == "tool":
            assert message["tool_call_id"] in answered
    # The elided span is marked, not silently dropped.
    assert any("【上下文已压缩】" in str(message.get("content") or "") for message in compressed)


def test_compress_native_tool_history_keeps_a_history_that_fits() -> None:
    messages: list[dict[str, Any]] = [{"role": "user", "content": "deliver the artifact"}]
    messages.extend(_native_turn(0))

    compressed = compress_native_tool_history(messages, 100_000)

    assert compressed == messages
