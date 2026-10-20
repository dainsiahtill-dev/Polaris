"""Synthetic model HTTP, real native codecs and real confined process effects.

This proves the technical composition, not a physical-model or Bench delivery.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    private_sandbox as private_sandbox,
    scripted_provider as scripted_provider,
)
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs.runtime import KernelFileSystem
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
from polaris.kernelone.llm.toolkit.parsers.core import parse_tool_calls
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments
from polaris.kernelone.process.process_tree import ProcessTreeRunControl, run_process_tree_safe


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_native_calls_execute_distinct_confined_effects_and_return_real_results(
    protocol: str,
    scripted_provider: ScriptedProvider,
    isolated_workspace: Path,
    private_sandbox: Callable[[list[str]], list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = shlex.join(
        [
            "/usr/bin/python3",
            "-c",
            "from pathlib import Path; "
            "p = Path('native-effects.txt'); "
            "p.open('a', encoding='utf-8').write('真实效果\\n'); "
            "print('actual command completed')",
        ]
    )
    ids = ["effect-one", "effect-two"]
    args = {"cmd": command}
    if protocol == "anthropic":
        packet = {
            "id": "synthetic-effects",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "tool_use", "id": call_id, "name": "run_command", "input": args} for call_id in ids],
            "stop_reason": "tool_use",
        }
        final = {
            "id": "synthetic-final",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Two observed results received."}],
            "stop_reason": "end_turn",
        }
    else:
        packet = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": "run_command", "arguments": json.dumps(args)},
                            }
                            for call_id in ids
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        final = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Two observed results received."},
                    "finish_reason": "stop",
                }
            ]
        }
    tools = [
        {
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Execute a command inside the confined workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        }
    ]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are Director. Use the tools and their actual results."},
        {"role": "user", "content": "Append the same observation twice using separate calls."},
    ]

    def request() -> AIRequest:
        return AIRequest(
            task_type=TaskType.DIALOGUE,
            role="engineering_primitives_test",
            provider_id="synthetic",
            model="synthetic-model",
            input="The native messages are authoritative for this component test.",
            options={"messages": messages, "tools": tools, "tool_choice": "auto", "max_tokens": 512},
        )

    client = scripted_provider.client(protocol, isolated_workspace, monkeypatch)
    scripted_provider.enqueue_json(packet)
    scripted_provider.enqueue_json(final)
    response = await client.invoke(request())
    assert response.ok, response.error
    raw = response.raw
    assert isinstance(raw, dict)
    calls = parse_tool_calls(
        tool_calls=(raw["content"] if protocol == "anthropic" else raw["choices"][0]["message"]["tool_calls"]),
        allowed_tool_names=["execute_command"],
        allow_text_fallback=False,
    )
    assert [call.id for call in calls] == ids
    assert all(call.name == "execute_command" and call.parse_error is None for call in calls)
    assistant_calls = []
    observations = []
    filesystem = KernelFileSystem(str(isolated_workspace), LocalFileSystemAdapter())
    log_path = "runtime/engineering/primitive-observations.jsonl"
    for call in calls:
        normalized = normalize_tool_arguments(call.name, call.arguments)
        assert normalized == {"command": command}
        process = run_process_tree_safe(
            private_sandbox(shlex.split(normalized["command"])),
            cwd=isolated_workspace,
            timeout=5,
            cancel_control=ProcessTreeRunControl(),
        )
        assert process.returncode == 0, process.stderr
        assert process.stdout.strip() == "actual command completed"
        assistant_calls.append(
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(normalized)},
            }
        )
        observations.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(
                    {"exit_code": process.returncode, "stdout": process.stdout, "stderr": process.stderr}
                ),
            }
        )
        filesystem.append_jsonl(
            log_path,
            {
                "call_id": call.id,
                "exit_code": process.returncode,
                "stdout": process.stdout,
                "stderr": process.stderr,
            },
            durability="fsync",
        )
    assert (isolated_workspace / "native-effects.txt").read_text(encoding="utf-8") == "真实效果\n" * 2
    cold_filesystem = KernelFileSystem(str(isolated_workspace), LocalFileSystemAdapter())
    stored = [json.loads(row) for row in cold_filesystem.read_text(log_path, encoding="utf-8").splitlines()]
    assert [row["call_id"] for row in stored] == ids
    assert all(row["exit_code"] == 0 and row["stdout"] == "actual command completed\n" for row in stored)
    messages.extend([{"role": "assistant", "content": "", "tool_calls": assistant_calls}, *observations])
    final_response = await client.invoke(request())
    assert final_response.ok, final_response.error
    assert final_response.output == "Two observed results received."
    final_body = scripted_provider.requests[-1]["body"]
    if protocol == "openai":
        assert final_body["messages"][-2:] == observations
    else:
        result_blocks = [
            block
            for message in final_body["messages"]
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        assert [block["tool_use_id"] for block in result_blocks] == ids
        assert [block["content"] for block in result_blocks] == [row["content"] for row in observations]
