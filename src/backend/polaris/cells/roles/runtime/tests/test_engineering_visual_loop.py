"""Synthetic browser attachment, real native HTTP/codecs and durable role history."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_loop import run_engineering_loop
from polaris.cells.roles.runtime.internal.engineering_roles import role_scope
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    BrowserObservationConfig,
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
    tool_observation_payload,
)
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_loop import _native
from polaris.cells.roles.runtime.tests.test_engineering_visual_messages import IMAGE, RAW


def scope(workspace: Path) -> PermissionScope:
    return PermissionScope(
        (workspace,),
        (workspace,),
        frozenset({"browser_observe", "read_file", "write_file", "execute_command", "report_review"}),
        True,
        BrowserObservationConfig("/usr/bin/true", "/usr/bin/python3", ()),
    )


def test_browser_authority_is_available_to_director_and_readonly_qa_only(isolated_workspace: Path) -> None:
    original = scope(isolated_workspace)
    for role in ("director", "qa"):
        actual = role_scope(role, original)
        assert actual.browser == original.browser
        assert "browser_observe" in actual.enabled_tools
        if role == "qa":
            assert not actual.write_roots and not actual.network_allowed
            assert "execute_command" not in actual.enabled_tools
    for role in ("pm", "chief_engineer"):
        actual = role_scope(role, original)
        assert actual.browser is None and "browser_observe" not in actual.enabled_tools


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_actual_native_request_contains_exact_images_after_complete_tool_batch(
    protocol: str,
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (isolated_workspace / "index.html").write_text("<h1>fixture</h1>", encoding="utf-8")
    store = EngineeringSessionStore(isolated_workspace)
    command = RoleRunCommand("visual-http", "director", "Inspect the page.", isolated_workspace)
    limits = RunLimits(time.time() + 20, 100000, 64000)
    original = scope(isolated_workspace)
    store.create_run(command, scope=original, limits=limits, engine_version="engineering_v2", request_key=None)
    observed = []

    async def observe(arguments: dict[str, Any], turn_seq: int, call_id: str) -> ToolObservation:
        observed.append((arguments, turn_seq, call_id))
        return ToolObservation(call_id, "browser_observe", True, "Synthetic capture only.", 0, False, images=(IMAGE,))

    scripted_provider.enqueue_json(
        _native(
            ("capture", "browser_observe", {"path": "index.html"}),
            ("source", "read_file", {"file": "index.html"}),
            protocol=protocol,
        )
    )
    scripted_provider.enqueue_json(_native(text="Observed.", protocol=protocol))
    result = await run_engineering_loop(
        command,
        scope=original,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client(protocol, isolated_workspace, monkeypatch),
        observe=observe,
    )
    assert result.status == "completed", result.text
    assert len(observed) == 1 and observed[0][0] == {"entry": "index.html"}
    body = scripted_provider.requests[1]["body"]
    images = []
    for message in body["messages"]:
        for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if block.get("type") == "image":
                images.append(base64.b64decode(block["source"]["data"]))
            if block.get("type") == "image_url":
                images.append(base64.b64decode(block["image_url"]["url"].split(",", 1)[1]))
    assert images == [RAW]
    encoded = IMAGE.data_base64
    assert json.dumps(body).count(encoded) == 1
    durable = store.load_message_history(command.session_id, role="director", after_seq=0)
    assert [item["role"] for item in durable][-3:] == ["tool", "tool", "assistant"]
    stored = next(
        item["payload"]
        for item in store.load_events(command.session_id)
        if item["kind"] == "tool_observed" and item["payload"].get("tool") == "browser_observe"
    )
    assert stored["images"][0]["data_base64"] == encoded
    assert not store.pending_tool_calls(command.session_id)


@pytest.mark.parametrize("kind", ["tool_observed", "run_updated", "verification_completed"])
def test_public_projection_contains_image_metadata_without_any_pixel_bytes(kind: str) -> None:
    from polaris.delivery.ws.engineering_events import compact_engineering_payload

    actual = tool_observation_payload(
        ToolObservation("capture", "browser_observe", True, "capture", 0, False, images=(IMAGE,))
    )
    payload = actual if kind == "tool_observed" else {"checks": [actual]}
    projected = compact_engineering_payload(kind, payload)
    shown = projected if kind == "tool_observed" else projected["checks"][0]
    assert shown["images"][0]["sha256"] == IMAGE.sha256
    assert "data_base64" not in json.dumps(projected) and IMAGE.data_base64 not in json.dumps(projected)
    assert "data_base64" in actual["images"][0]
