"""Rejected model output is feedback, with no partial native tool effects.

The model is a scripted loopback HTTP fixture. File writes, command isolation,
verification and QA use the actual public service. This is not remote-model
or Bench delivery evidence.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _scope
from polaris.cells.roles.runtime.tests.test_engineering_loop import _setup


def _packet(
    protocol: str, *calls: tuple[str, str, dict[str, Any]], text: str = "Ready.", terminal: str | None = None
) -> dict[str, Any]:
    if protocol == "anthropic":
        return {
            "id": "synthetic-message",
            "type": "message",
            "role": "assistant",
            "model": "synthetic-model",
            "content": [
                {"type": "text", "text": text},
                *[{"type": "tool_use", "id": identity, "name": name, "input": args} for identity, name, args in calls],
            ],
            "stop_reason": terminal or ("tool_use" if calls else "end_turn"),
            "usage": {"input_tokens": 5, "output_tokens": 5},
        }
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": [
                        {"id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                        for identity, name, args in calls
                    ],
                },
                "finish_reason": terminal or ("tool_calls" if calls else "stop"),
            }
        ]
    }


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_output_limit_rejects_whole_turn_then_same_director_delivers(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch, protocol: str
) -> None:
    """A cutoff must neither apply its valid-looking prefix nor end legal repair."""
    actual = "python3 -c \"from pathlib import Path; assert Path('artifact.txt').read_text(encoding='utf-8')=='2'\""
    limited = "max_tokens" if protocol == "anthropic" else "length"
    for packet in (
        _packet(protocol, ("keep", "write_file", {"file": "previous.txt", "content": "keep"})),
        _packet(
            protocol,
            ("rejected-overwrite", "write_file", {"file": "previous.txt", "content": "CORRUPT"}),
            ("rejected-new", "write_file", {"file": "unexecuted.txt", "content": "NEVER"}),
            terminal=limited,
        ),
        _packet(
            protocol,
            ("actual-write", "write_file", {"file": "artifact.txt", "content": "2"}),
            ("actual-verify", "verify_project", {"commands": [actual]}),
        ),
        _packet(protocol),
        _packet(
            protocol,
            ("qa-current", "read_file", {"file": "artifact.txt"}),
            ("qa-preserved", "read_file", {"file": "previous.txt"}),
        ),
        _packet(
            protocol,
            (
                "qa-review",
                "report_review",
                {"approved": True, "feedback": "Read artifact2 and preserved previous file."},
            ),
        ),
    ):
        scripted_provider.enqueue_json(packet)
    scripted_provider.client(protocol, isolated_workspace, monkeypatch)  # Synthetic model binding only.
    service = EngineeringRunService(
        isolated_workspace, require_qa=True, permission_resolver=lambda _row: _scope(isolated_workspace)
    )
    command = RoleRunCommand(
        "native-feedback", "director", "Deliver artifact2 and preserve previous.txt.", isolated_workspace
    )
    await service.start(
        command, scope=_scope(isolated_workspace), limits=RunLimits(time.time() + 45, 1_000_000, 64_000)
    )
    await service.wait(command.session_id, workspace=isolated_workspace)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["project_outcome"] == "accepted", row
    assert row["verification"] == "passed" and row["qa_verdict"] == "approved"
    assert (isolated_workspace / "previous.txt").read_text(encoding="utf-8") == "keep"
    assert not (isolated_workspace / "unexecuted.txt").exists()
    assert (isolated_workspace / "artifact.txt").read_text(encoding="utf-8") == "2"
    events = service.store.load_events(command.session_id)
    assert [event["role"] for event in events if event["kind"] == "role_started"] == ["director", "qa"]
    assert not any(event["kind"] == "role_error" for event in events)
    rejected = [event for event in events if "native_turn_rejection" in event["payload"]]
    assert len(rejected) == 1
    diagnostic = rejected[0]["payload"]["native_turn_rejection"]
    assert diagnostic["tools_dispatched"] is False
    assert [candidate["call_id"] for candidate in diagnostic["candidates"]] == ["rejected-overwrite", "rejected-new"]
    assert not any(
        event["kind"] == "tool_started" and event["payload"]["call_id"].startswith("rejected-") for event in events
    )
    history = service.store.load_message_history(command.session_id, role="director")
    assert not any(call["id"].startswith("rejected-") for message in history for call in message.get("tool_calls", []))
    assert limited in json.dumps(scripted_provider.requests[2]["body"], ensure_ascii=False)
    assert row["required_checks"] == [] and row["verification_commands"] == [actual]

    from polaris.delivery.ws.engineering_events import compact_engineering_payload

    preview = compact_engineering_payload(rejected[0]["kind"], rejected[0]["payload"])
    assert preview["native_turn_rejection"] == diagnostic and "message" not in preview


@pytest.mark.parametrize("corruption", ["arguments", "text_cutoff"])
async def test_model_corrects_rejected_proposal_without_applying_valid_prefix(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    """Rejecting one malformed sibling must not run the valid write beside it."""
    packet = _packet(
        "openai",
        ("first", "write_file", {"file": "forbidden-prefix", "content": "bad"}),
        ("second", "read_file", {"file": "nonexistent"}),
    )
    calls = packet["choices"][0]["message"]["tool_calls"]
    if corruption == "arguments":
        calls[1]["function"]["arguments"] = '{"file":'
    else:
        packet = _packet("openai", text="unfinished answer", terminal="length")
    for response in (
        packet,
        _packet("openai", ("fixed", "write_file", {"file": "corrected", "content": "ok"})),
        _packet("openai"),
    ):
        scripted_provider.enqueue_json(response)
    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    result = await loop(
        command,
        store=store,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "completed", result
    assert not (isolated_workspace / "forbidden-prefix").exists()
    assert (isolated_workspace / "corrected").read_text(encoding="utf-8") == "ok"
    events = store.load_events(command.session_id)
    rejected = [
        event["payload"]["native_turn_rejection"] for event in events if "native_turn_rejection" in event["payload"]
    ]
    assert len(rejected) == 1
    assert len(rejected[0]["candidates"]) == (0 if corruption == "text_cutoff" else 2)
    assert [event["payload"]["call_id"] for event in events if event["kind"] == "tool_started"] == ["fixed"]
    assert command.instruction in json.dumps(scripted_provider.requests[1]["body"])


async def test_rejection_feedback_does_not_restore_explicit_empty_write_authority(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Correction receives the original empty grants; a later read can progress."""
    (isolated_workspace / "readable").write_text("keep", encoding="utf-8")
    for response in (
        _packet("openai", ("cutoff", "write_file", {"file": "readable", "content": "bad"}), terminal="length"),
        _packet("openai", ("denied", "write_file", {"file": "readable", "content": "bad"})),
        _packet("openai", ("legal", "read_file", {"file": "readable"})),
        _packet("openai"),
    ):
        scripted_provider.enqueue_json(response)
    loop, store, _tools, command, scope, limits = _setup(
        isolated_workspace, write=False, enabled=frozenset({"read_file"})
    )
    result = await loop(
        command,
        store=store,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "completed"
    assert (isolated_workspace / "readable").read_text(encoding="utf-8") == "keep"
    observed = [event["payload"] for event in store.load_events(command.session_id) if event["kind"] == "tool_observed"]
    assert [(item["call_id"], item["ok"]) for item in observed] == [("denied", False), ("legal", True)]
    assert [tool["function"]["name"] for tool in scripted_provider.requests[1]["body"]["tools"]] == ["read_file"]


async def test_repeated_rejections_consume_original_text_output_budget(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rejected output cannot evade the existing UTF-8 text budget by continuing."""
    for _ in range(3):
        scripted_provider.enqueue_json(_packet("openai", text="界" * 10, terminal="length"))
    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    result = await loop(
        command,
        store=store,
        scope=scope,
        limits=replace(limits, max_output_bytes=50),
        cancel=asyncio.Event(),
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "failed" and "output budget" in result.text
    assert len(scripted_provider.requests) == 2
    assert not any(event["kind"] == "tool_started" for event in store.load_events(command.session_id))


@pytest.mark.parametrize("terminal", ["tool_calls", "length"])
@pytest.mark.parametrize("broken", ["duplicate_id", "missing_function"])
async def test_protocol_damage_does_not_enter_model_output_repair(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    terminal: str,
    broken: str,
) -> None:
    """A truncation marker cannot conceal native protocol damage or grant a retry."""
    packet = _packet(
        "openai",
        ("same", "write_file", {"file": "never", "content": "bad"}),
        ("other", "read_file", {"file": "unknown"}),
        terminal=terminal,
    )
    calls = packet["choices"][0]["message"]["tool_calls"]
    if broken == "duplicate_id":
        calls[1]["id"] = "same"
    else:
        calls[1].pop("function")
    for response in (
        packet,
        _packet("openai", ("later", "write_file", {"file": "later", "content": "ran"})),
        _packet("openai"),
    ):
        scripted_provider.enqueue_json(response)
    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    result = await loop(
        command,
        store=store,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "failed", result
    assert len(scripted_provider.requests) == 1
    assert not (isolated_workspace / "never").exists() and not (isolated_workspace / "later").exists()
    assert not any(event["kind"] == "tool_started" for event in store.load_events(command.session_id))
