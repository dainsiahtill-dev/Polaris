"""Real registry/native/service/browser/SQLite paths; only model replies are scripted."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from contextvars import Context
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from polaris.cells.roles.runtime.internal.engineering_roles import register_browser_observation_tool
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.internal.engineering_visual import build_visual_messages
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _response
from polaris.cells.roles.runtime.tests.test_engineering_browser_complete_flow import _dispatch, _scope
from polaris.cells.roles.runtime.tests.test_engineering_browser_static_origin import (
    READY,
    built_workspace as built_workspace,
)
from polaris.kernelone.fs import get_default_adapter, guarded_create_directory
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments
from polaris.kernelone.tool_execution.contracts import frozen_node_to_value
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("entry_argument", ["entry", "path"])
async def test_static_origin_registry_json_native_service_and_cold_original_pixels(
    built_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    entry_argument: str,
) -> None:
    register_browser_observation_tool()
    original = ToolSpecRegistry.get_llm_schema("browser_observe")
    assert original is not None
    parameters = original["function"]["parameters"]
    Draft202012Validator(parameters).validate({"entry": "public/dist/nested/index.html", "web_root": "public/dist"})
    assert list(
        Draft202012Validator(parameters).iter_errors({"revisit_call_ids": ["saved"], "web_root": "public/dist"})
    )
    assert list(Draft202012Validator(parameters).iter_errors({"web_root": ""}))
    snapshot = ToolSpecRegistry.capture_effective_spec("browser_observe")
    effective = json.loads(json.dumps(frozen_node_to_value(snapshot.canonical_effective_spec), ensure_ascii=False))
    restored_context = Context()

    def restore() -> None:
        ToolSpecRegistry.register("browser_observe", effective)
        restored = ToolSpecRegistry.get_llm_schema("browser_observe")
        assert restored is not None and restored["function"]["parameters"] == parameters
        normalized = normalize_tool_arguments(
            "browser_observe", {"path": "public/dist/nested/index.html", "web_root": "public/dist"}
        )
        assert normalized["entry"] == "public/dist/nested/index.html" and normalized["web_root"] == "public/dist"

    restored_context.run(restore)
    # path is a genuine natural-call alias; web_root must survive normalization.
    arguments = {entry_argument: "public/dist/nested/index.html", "web_root": "public/dist", "frame_times_ms": [100]}
    if protocol == "openai":
        scripted_provider.enqueue_json(_response(("static-capture", "browser_observe", arguments)))
        scripted_provider.enqueue_json(_response(text="Inspected the explicit synthetic static-origin capture."))
    else:
        scripted_provider.enqueue_json(
            {
                "content": [
                    {"type": "tool_use", "id": "static-capture", "name": "browser_observe", "input": arguments}
                ],
                "stop_reason": "tool_use",
            }
        )
        scripted_provider.enqueue_json(
            {
                "content": [{"type": "text", "text": "Inspected the explicit synthetic static-origin capture."}],
                "stop_reason": "end_turn",
            }
        )
    scope = _scope(built_workspace)
    service = EngineeringRunService(
        built_workspace,
        client=scripted_provider.client(protocol, built_workspace, monkeypatch),
        permission_resolver=lambda _row: scope,
    )
    command = RoleRunCommand(
        "static-native-" + protocol,
        "director",
        "Use the authorized synthetic static browser fixture; no delivery claim.",
        built_workspace,
    )
    limits = RunLimits(time.time() + 120, 2_000_000, 64000)
    result = await service.run_role(command, scope=scope, limits=limits, cancel=asyncio.Event())
    assert result.status == "completed", result.text
    events = service.store.load_events(command.session_id)
    observation = next(
        e["payload"] for e in events if e["kind"] == "tool_observed" and e["payload"]["tool"] == "browser_observe"
    )
    assert observation["ok"], observation["content"]
    payload = json.loads(observation["content"])
    assert READY in payload["dom"]["body_text"]
    assert payload["entry"] == "public/dist/nested/index.html" and payload["web_root"] == "public/dist"
    assert payload["origin_mode"] == "static"
    dispatch = next(e for e in events if e["kind"] == "tool_started")
    assert dispatch["payload"]["arguments"]["web_root"] == "public/dist"
    assert len(scripted_provider.requests) == 2
    wire = scripted_provider.requests[0]["body"]
    native_schema = (
        next(t["function"]["parameters"] for t in wire["tools"] if t["function"]["name"] == "browser_observe")
        if protocol == "openai"
        else next(t["input_schema"] for t in wire["tools"] if t["name"] == "browser_observe")
    )
    assert native_schema == parameters
    image = observation["images"][0]
    pixels = base64.b64decode(image["data_base64"], validate=True)
    assert hashlib.sha256(pixels).hexdigest() == image["sha256"]
    assert image["data_base64"] in json.dumps(scripted_provider.requests[1]["body"])
    visual_events = service.store.load_events(command.session_id, kinds=("visual_observation_completed",))
    assert len(visual_events) == 1
    original_binding = visual_events[0]["payload"]["input_binding"]
    cold = EngineeringRunService(built_workspace, permission_resolver=lambda _row: scope)
    restored_run = cold.store.load_run(command.session_id, workspace=built_workspace)
    assert restored_run["limits"]["deadline"] == limits.deadline
    saved_dispatch = next(
        e
        for e in cold.store.load_events(command.session_id, kinds=("tool_started",))
        if e["payload"]["call_id"] == "static-capture"
    )
    assert saved_dispatch["payload"]["arguments"]["web_root"] == "public/dist"
    assert saved_dispatch["payload"]["arguments"][entry_argument] == "public/dist/nested/index.html"

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("cold historical reinspection attempted native/browser replay")

    monkeypatch.setattr(EngineeringTools, "_process", forbidden)
    revisited = await _dispatch(cold, command, scope, limits, {"revisit_call_ids": ["static-capture"]})
    assert revisited.ok, revisited.content
    assert not revisited.images  # The journal stores references, never duplicate pixel bytes.
    projected = build_visual_messages([], events=cold.store.load_events(command.session_id), role="director")
    restored_images = [
        block["source"]["data"]
        for message in projected
        for block in message.get("content", [])
        if isinstance(block, dict) and block.get("type") == "image"
    ]
    assert len(restored_images) == 1
    assert base64.b64decode(restored_images[0], validate=True) == pixels
    assert cold.store.load_events(command.session_id, kinds=("visual_observation_completed",)) == visual_events
    assert (
        cold.store.load_events(command.session_id, kinds=("visual_observation_completed",))[0]["payload"][
            "input_binding"
        ]
        == original_binding
    )
    assert not cold.store.pending_tool_calls(command.session_id)
    mixed = await _dispatch(
        cold, command, scope, limits, {"revisit_call_ids": ["static-capture"], "web_root": "public/dist"}
    )
    assert not mixed.ok and not mixed.images
    narrowed = replace(scope, read_roots=(built_workspace / "public/dist/nested", *scope.read_roots[1:]))
    restricted = EngineeringRunService(built_workspace, permission_resolver=lambda _row: narrowed)
    denied_root = await _dispatch(restricted, command, scope, limits, {"revisit_call_ids": ["static-capture"]})
    assert not denied_root.ok and "saved scope exceeds current permission policy" in denied_root.content
    revoked = EngineeringRunService(
        built_workspace, permission_resolver=lambda _row: replace(scope, enabled_tools=frozenset())
    )
    denied_empty = await _dispatch(revoked, command, scope, limits, {"revisit_call_ids": ["static-capture"]})
    assert not denied_empty.ok and not denied_empty.images

    artifact = Path(
        os.environ.get("POLARIS_TEST_AUDIT_ROOT", str(Path(__file__).resolve().parents[7] / ".polaris/test-workspaces"))
    ) / ("static-native-" + protocol + "-" + uuid4().hex)
    guarded_create_directory(
        str(artifact.parents[2]), artifact.relative_to(artifact.parents[2]).as_posix(), parents=True, exist_ok=True
    )
    fs = get_default_adapter()
    fs.write_bytes(str(artifact / "original.jpg"), pixels)
    fs.write_text(
        str(artifact / "native-requests.json"),
        json.dumps(scripted_provider.requests, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    fs.write_text(
        str(artifact / "observation.json"), json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
