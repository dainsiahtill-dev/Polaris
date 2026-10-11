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
        ("pm", 32768, 1000000, None, "planning_output_budget_model_limit_exceeded"),
        ("chief_engineer", 32768, 1000000, None, "planning_output_budget_model_limit_exceeded"),
        ("pm", 65536, 60032, None, "planning_output_budget_context_window_exceeded"),
        ("chief_engineer", 65536, 60032, None, "planning_output_budget_context_window_exceeded"),
        ("pm", 65536, 60200, None, "planning_output_budget_context_window_exceeded"),
        ("chief_engineer", 65536, 60200, None, "planning_output_budget_context_window_exceeded"),
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
        assert len(wires) == 1 and wires[0]["max_tokens"] == want
        assert requirement in "\n".join(message["content"] for message in wires[0]["messages"])
