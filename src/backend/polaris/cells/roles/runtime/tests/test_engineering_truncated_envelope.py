"""A known truncated nullable envelope can continue without partial effects.

The first packet mirrors the native fields captured in real response seq220:
Anthropic-compatible max_tokens with content:null. Recorded usage is evidence,
not a runtime limit. Loopback responses replace only the remote model.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_loop import NativeTurnRejectedError, _native_calls
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_loop import _setup
from polaris.kernelone.llm.engine.contracts import AIResponse


def _captured_truncation(*, missing_content: bool = False) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "base_resp": {"status_code": 0, "status_msg": ""},
        "content": None,
        "id": "sanitized-captured-response-220",
        "model": "MiniMax-M3",
        "role": "assistant",
        "stop_reason": "max_tokens",
        "type": "message",
        "usage": {"input_tokens": 288, "cache_read_input_tokens": 9214, "output_tokens": 3000},
    }
    if missing_content:
        raw.pop("content")
    return raw


@pytest.mark.parametrize("missing_content", [False, True])
def test_captured_nullable_truncation_is_correctable_before_any_dispatch(missing_content: bool) -> None:
    with pytest.raises(NativeTurnRejectedError) as rejected:
        _native_calls(AIResponse.success(output="", raw=_captured_truncation(missing_content=missing_content)))
    assert rejected.value.diagnostic["stop_reason"] == "max_tokens"
    assert rejected.value.diagnostic["tools_dispatched"] is False
    assert rejected.value.diagnostic["candidates"] == []


@pytest.mark.parametrize("terminal", [None, "end_turn", "tool_use"])
@pytest.mark.parametrize("missing_content", [False, True])
def test_nontruncated_nullable_envelope_still_fails_without_model_output_repair(
    terminal: str | None, missing_content: bool
) -> None:
    raw = _captured_truncation(missing_content=missing_content)
    raw["stop_reason"] = terminal
    with pytest.raises(ValueError) as invalid:
        _native_calls(AIResponse.success(output="", raw=raw))
    assert not isinstance(invalid.value, NativeTurnRejectedError)


@pytest.mark.parametrize("content", ["broken", [None], [{"type": "tool_use", "id": "partial"}]])
def test_truncation_does_not_hide_nonnullable_native_protocol_damage(content: Any) -> None:
    raw = _captured_truncation()
    raw["content"] = content
    with pytest.raises(ValueError) as invalid:
        _native_calls(AIResponse.success(output="", raw=raw))
    assert not isinstance(invalid.value, NativeTurnRejectedError)


@pytest.mark.parametrize("missing_content", [False, True])
async def test_real_native_loop_preserves_nullable_raw_and_same_role_can_make_legal_progress(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    missing_content: bool,
) -> None:
    packet = _captured_truncation(missing_content=missing_content)
    scripted_provider.enqueue_json(packet)
    scripted_provider.enqueue_json(
        {
            "content": [
                {
                    "type": "tool_use",
                    "id": "legal-write",
                    "name": "write_file",
                    "input": {"file": "fragment.txt", "content": "small complete action"},
                }
            ],
            "stop_reason": "tool_use",
        }
    )
    scripted_provider.enqueue_json({"content": [{"type": "text", "text": "Done."}], "stop_reason": "end_turn"})
    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("anthropic", isolated_workspace, monkeypatch),
    )
    assert result.status == "completed", result
    assert (isolated_workspace / "fragment.txt").read_text(encoding="utf-8") == "small complete action"
    events = store.load_events(command.session_id)
    responses = [event for event in events if event["kind"] == "provider_response"]
    saved_raw = responses[0]["payload"]["raw"]
    assert saved_raw["stop_reason"] == "max_tokens"
    assert saved_raw["usage"]["output_tokens"] == packet["usage"]["output_tokens"]
    assert ("content" not in saved_raw) if missing_content else saved_raw["content"] is None
    rejected = [event for event in events if "native_turn_rejection" in event["payload"]]
    assert len(rejected) == 1
    assert rejected[0]["payload"]["native_turn_rejection"]["provider_response_seq"] == responses[0]["seq"]
    assert rejected[0]["payload"]["native_turn_rejection"]["tools_dispatched"] is False
    starts = [event for event in events if event["kind"] == "tool_started"]
    assert [(event["role"], event["payload"]["call_id"]) for event in starts] == [("director", "legal-write")]
    assert starts[0]["seq"] > rejected[0]["seq"]
    assert not store.pending_tool_calls(command.session_id)
    assert len(scripted_provider.requests) == 3
    second = scripted_provider.requests[1]["body"]
    assert second["system"].startswith("You are Director")
    assert "rejected before dispatch" in str(second["messages"])
    assert command.instruction in str(second["messages"])
