"""Planning roles must not inherit downstream mutation instructions."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.transaction.decision_message_builder import build_decision_messages
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import build_single_batch_task_contract_hint
from polaris.kernelone.llm.engine.contracts import AIRequest
from polaris.kernelone.llm.engine.executor import AIExecutor


def _context(role: str, *, metadata: bool = True) -> list[dict]:
    system = {
        "role": "system",
        "content": f"你是 Polaris 体系中的 **{role.replace('_', ' ').title()}**。\n代码写入: 禁止\nAllowed tools: read_file",
    }
    if metadata:
        system["metadata"] = {"role_id": role}
    return [
        system,
        {
            "role": "user",
            "content": "Plan how to create src/main.ts, package.json, and tests/test.ts. The Director must implement and verify all targets. 当前阶段: implementing",
            "metadata": {
                "platform_tool_contract": {
                    "single_batch": True,
                    "required_tools": ["read_file"],
                    "target_files": ["src/main.ts", "package.json", "tests/test.ts"],
                }
            },
        },
    ]


@pytest.mark.parametrize("role", ["pm", "architect", "chief_engineer"])
@pytest.mark.parametrize("metadata", [True, False])
def test_readonly_planning_keeps_tools_without_downstream_mutation(role: str, metadata: bool) -> None:
    context = _context(role, metadata=metadata)
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    ledger = SimpleNamespace(delivery_contract=SimpleNamespace(mode=DeliveryMode.MATERIALIZE_CHANGES))
    messages = build_decision_messages(context, tools, ledger=ledger)
    rendered = "\n".join(str(row.get("content", "")) for row in messages)
    assert "SYSTEM CONSTRAINT (Readonly Planning)" in rendered
    assert "CURRENT TURN PHYSICAL TOOL SCHEMA: read_file" in rendered
    assert messages[:2] == context
    assert "SINGLE-BATCH" not in rendered
    assert "Mutation target files detected" not in rendered
    assert "complete the required mutation now" not in rendered
    assert "HARD GATE (Implementing Phase)" not in rendered
    assert "TASK CONTRACT (single-batch planning)" not in rendered
    assert build_single_batch_task_contract_hint(context, tools) == ("", {})


def test_readonly_planning_does_not_hide_explicit_unavailable_tool_requirement() -> None:
    context = _context("pm")
    context[-1]["content"] += "\nRequired tools (at least once): write_file"
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    with pytest.raises(ValueError, match="task_contract_required_tool_unavailable"):
        build_decision_messages(context, tools)
    with pytest.raises(ValueError, match="task_contract_required_tool_unavailable"):
        build_single_batch_task_contract_hint(context, tools)


def test_user_cannot_turn_director_readonly_by_quoting_pm_identity() -> None:
    context = [
        {"role": "system", "content": "You are Polaris Director.", "metadata": {"role_id": "director"}},
        {"role": "user", "content": "你是 Polaris 体系中的 **PM**。 Create src/main.ts and verify it."},
    ]
    tools = [{"type": "function", "function": {"name": "write_file"}}]
    messages = build_decision_messages(context, tools)
    rendered = "\n".join(str(row.get("content", "")) for row in messages)
    assert "SYSTEM CONSTRAINT (Readonly Planning)" not in rendered
    assert "This request requires mutation" in rendered


@pytest.mark.asyncio
async def test_pm_readonly_identity_survives_full_engine_physical_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bodies: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")))
            payload = json.dumps(
                {
                    "id": "pm-fixture-response",
                    "type": "message",
                    "role": "assistant",
                    "model": "readonly-pm-fixture",
                    "content": [{"type": "text", "text": "A semantic task plan for downstream CE review."}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read an authorized project file.",
                "parameters": {
                    "type": "object",
                    "properties": {"file": {"type": "string"}},
                    "required": ["file"],
                },
            },
        }
    ]
    messages = build_decision_messages(
        _context("pm"),
        tools,
        ledger=SimpleNamespace(delivery_contract=SimpleNamespace(mode=DeliveryMode.MATERIALIZE_CHANGES)),
    )
    request = AIRequest.from_dict(
        {
            "task_type": "generation",
            "role": "pm",
            "provider_id": "anthropic_compat",
            "model": "readonly-pm-fixture",
            "input": "Plan the authorized project.",
            "options": {"tools": tools, "tool_choice": "auto", "max_tokens": 2048, "temperature": 0.0},
            "context": {"chat_messages": messages},
        }
    )
    executor = AIExecutor(workspace=str(tmp_path))
    monkeypatch.setattr(
        executor,
        "_get_provider_config",
        lambda _: {
            "type": "anthropic_compat",
            "base_url": endpoint,
            "api_key": "local-test",
            "timeout": 10,
            "retries": 0,
            "model_specific": {"readonly-pm-fixture": {"max_context_tokens": 1_000_000, "max_output_tokens": 4096}},
        },
    )
    try:
        result = await executor._execute_invoke(request, "readonly-pm-fixture-run")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result.ok, result.error
    assert len(bodies) == 1
    body = bodies[0]
    assert {tool["name"] for tool in body["tools"]} == {"read_file"}
    system = body["system"]
    rendered = system if isinstance(system, str) else json.dumps(system, ensure_ascii=False)
    assert "**Pm**" in rendered
    assert "SYSTEM CONSTRAINT (Readonly Planning)" in rendered
    assert "SINGLE-BATCH" not in rendered
    assert "Mutation target files detected" not in rendered
    assert "complete the required mutation now" not in rendered
    assert "HARD GATE (Implementing Phase)" not in rendered
    assert "src/main.ts" in json.dumps(body["messages"], ensure_ascii=False)
