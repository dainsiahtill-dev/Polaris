"""Actual browser/tool/schema closure; synthetic forms and model replies are not delivery."""

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
from typing import Any, cast
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from polaris.cells.roles.runtime.internal.engineering_browser import _settings
from polaris.cells.roles.runtime.internal.engineering_roles import register_browser_observation_tool
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    BrowserObservationConfig,
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
)
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _response
from polaris.kernelone.fs import get_default_adapter, guarded_create_directory
from polaris.kernelone.llm.engine.client import ResilientLLMClient
from polaris.kernelone.process.process_tree import ProcessTreeDrainError
from polaris.kernelone.tool_execution.contracts import frozen_node_to_value
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

BROWSER = (
    Path.home()
    / ".cache/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-linux64/chrome-headless-shell"
)
PACKAGES = Path.home() / ".pyenv/versions/3.12.3/lib/python3.12/site-packages"
_HTML = """<!doctype html><meta charset="utf-8"><title>Synthetic form</title>
<style>body{font:18px sans-serif}label,input,select,button{padding:8px;margin:8px}output{display:block;white-space:pre-wrap}</style>
<label>姓名<input id="name"></label><select id="plan"><option value="">请选择</option><option value=" 细节方案 ">细节方案</option></select><button id="prepare">准备</button>
<section id="second" hidden><input id="note"><input id="clear" value="seeded"><select id="tone"><option value="warm" selected>暖色</option><option value="">合法空选项</option></select><button id="confirm">确认</button><button id="finish" disabled>完成</button></section><output id="state">initial</output>
<textarea id="long">LONG_VALUE</textarea><input id="private-password" type="password" value="NOT_PUBLIC"><input id="private-hidden" type="hidden" value="NOT_VISIBLE">
EXTRA_CONTROLS
<script>let stage=0;const q=s=>document.querySelector(s),v=s=>q(s).value;q('#prepare').onclick=()=>{if(v('#name')==='  星河中文  '&&v('#plan')===' 细节方案 '){stage=1;q('#second').hidden=false}};q('#confirm').onclick=()=>{if(stage===1&&v('#note')===' 第二步确认 '&&v('#clear')===''&&v('#tone')===''){stage=2;q('#finish').disabled=false}};q('#finish').onclick=()=>{q('#state').textContent=stage===2?'COMPLETE_CURRENT_DOM':'INCOMPLETE'};</script>"""
_ACTIONS: list[dict[str, Any]] = [
    {"type": "fill", "selector": "#name", "value": "  星河中文  "},
    {"type": "select_option", "selector": "#plan", "value": " 细节方案 "},
    {"type": "click", "selector": "#prepare"},
    {"type": "fill", "selector": "#note", "value": " 第二步确认 "},
    {"type": "fill", "selector": "#clear", "value": ""},
    {"type": "select_option", "selector": "#tone", "value": ""},
    {"type": "click", "selector": "#confirm"},
    {"type": "click", "selector": "#finish"},
]


@pytest.fixture
def form_workspace(tmp_path: Path) -> Path:
    guarded_create_directory(str(tmp_path), "public", parents=True, exist_ok=True)
    html = _HTML.replace("LONG_VALUE", "界" * 700).replace(
        "EXTRA_CONTROLS", "".join(f'<input id="extra-{n}" value="{n}">' for n in range(40))
    )
    fs = get_default_adapter()
    fs.write_text(str(tmp_path / "public/index.html"), html, encoding="utf-8")
    fs.write_text(str(tmp_path / "forbidden.html"), "<h1>FORBIDDEN</h1>", encoding="utf-8")
    return tmp_path


def _scope(workspace: Path) -> PermissionScope:
    return PermissionScope(
        (workspace / "public", PACKAGES / "playwright", PACKAGES / "pyee", PACKAGES / "greenlet", BROWSER.parent),
        (),
        frozenset({"browser_observe"}),
        False,
        BrowserObservationConfig(str(BROWSER), "/usr/bin/python3", (str(PACKAGES),), False),
    )


def _schema() -> dict[str, Any]:
    register_browser_observation_tool()
    schema = ToolSpecRegistry.get_llm_schema("browser_observe")
    assert schema is not None
    return cast(dict[str, Any], schema["function"]["parameters"])


def _seed(workspace: Path, identity: str) -> tuple[EngineeringRunService, RoleRunCommand, PermissionScope, RunLimits]:
    scope = _scope(workspace)
    service = EngineeringRunService(workspace, permission_resolver=lambda _row: scope)
    command = RoleRunCommand(
        identity, "director", "Explicit synthetic browser fixture; no semantic project claim.", workspace
    )
    limits = RunLimits(time.time() + 90, 2_000_000, 64000)
    service.store.create_run(
        command, scope=scope, limits=limits, engine_version=service.engine_version, request_key=None
    )
    return service, command, scope, limits


async def _dispatch(
    service: EngineeringRunService,
    command: RoleRunCommand,
    scope: PermissionScope,
    limits: RunLimits,
    arguments: dict[str, Any],
    cancel: asyncio.Event | None = None,
) -> ToolObservation:
    register_browser_observation_tool()
    cancelled = cancel if cancel is not None else asyncio.Event()
    turn = service.store.append_event(command.session_id, kind="turn_started", role="director", payload={})
    identity = "capture-" + str(turn)
    service.store.record_tool_dispatch(
        command.session_id,
        turn_seq=turn,
        call_id=identity,
        tool="browser_observe",
        arguments=arguments,
        role="director",
    )

    async def observe(args: dict[str, Any]) -> ToolObservation:
        return await service._observe_dispatch(args, turn, identity)

    tools = EngineeringTools(command.workspace, scope, limits, cancelled, observe=observe)
    owner = service._owner.set((command, scope, limits, cancelled, cast(ResilientLLMClient, None)))
    role = service._active_role.set("director")
    try:
        observation = await tools.execute(identity, "browser_observe", arguments)
    finally:
        service._active_role.reset(role)
        service._owner.reset(owner)
    service.store.record_tool_observation(command.session_id, turn_seq=turn, observation=observation, role="director")
    return observation


@pytest.mark.parametrize(
    "arguments,valid",
    [
        ({}, True),
        ({"entry": "public/index.html"}, True),
        ({"revisit_call_ids": ["original"]}, True),
        ({"viewport": {"width": 640, "height": 360}}, True),
        ({"viewport": {"width": 640, "height": 1201}}, False),
        ({"actions": _ACTIONS}, True),
        ({"actions": [{"type": "fill", "selector": "#name", "value": ""}]}, True),
        ({"actions": [{"type": "select_option", "selector": "#tone", "value": ""}]}, True),
        ({"actions": [{"type": "fill", "selector": "#name"}]}, False),
        ({"actions": [{"type": "click", "selector": "#name", "value": "ignored"}]}, False),
        ({"actions": [{"type": "keypress", "selector": "#name", "key": "Enter", "value": "ignored"}]}, False),
        ({"actions": [{"type": "select_option", "selector": "#tone", "value": "warm", "key": "Enter"}]}, False),
        ({"revisit_call_ids": ["original"], "entry": "public/index.html"}, False),
        ({"revisit_call_ids": ["original"], "viewport": {"width": 640, "height": 360}}, False),
        ({"revisit_call_ids": ["original"], "actions": []}, False),
        ({"revisit_call_ids": []}, False),
        ({"revisit_call_ids": ["original", "original"]}, False),
        ({"unregistered": "field"}, False),
    ],
)
def test_actual_tool_schema_matches_capture_and_revisit_forms(arguments: dict[str, Any], valid: bool) -> None:
    errors = list(Draft202012Validator(_schema()).iter_errors(arguments))
    assert bool(errors) is not valid, [error.message for error in errors]


@pytest.mark.parametrize(
    "kind,value",
    [
        ("fill", ""),
        ("fill", "  星河\t中文  "),
        ("select_option", ""),
        ("select_option", " 细节方案 "),
        ("fill", "  " + ("界" * 1800) + "  "),
    ],
    ids=["empty-fill", "spaced-cjk-fill", "empty-option", "spaced-option", "long-exact-fill"],
)
def test_production_settings_preserve_exact_action_values(form_workspace: Path, kind: str, value: str) -> None:
    actual = _settings(
        {"entry": "public/index.html", "actions": [{"type": kind, "selector": "#name", "value": value}]},
        workspace=form_workspace,
        scope=_scope(form_workspace),
    )
    assert actual["actions"][0]["value"] == value


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_real_producer_native_request_and_eight_step_dom_flow(
    form_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch, protocol: str
) -> None:
    arguments = {
        "entry": "public/index.html",
        "viewport": {"width": 1100, "height": 800},
        "frame_times_ms": [0],
        "actions": _ACTIONS,
    }
    original = get_default_adapter().read_bytes(str(form_workspace / "public/index.html"))
    original_schema = _schema()
    snapshot = ToolSpecRegistry.capture_effective_spec("browser_observe")
    effective = json.loads(json.dumps(frozen_node_to_value(snapshot.canonical_effective_spec), ensure_ascii=False))

    def restore_actual_spec() -> dict[str, Any]:
        ToolSpecRegistry.register("browser_observe", effective)
        restored = ToolSpecRegistry.get_llm_schema("browser_observe")
        assert restored is not None
        return cast(dict[str, Any], restored["function"]["parameters"])

    restored_schema = Context().run(restore_actual_spec)
    assert restored_schema == original_schema
    if protocol == "openai":
        scripted_provider.enqueue_json(_response(("complete-flow", "browser_observe", arguments)))
        scripted_provider.enqueue_json(_response(text="Observed synthetic current controls and exact original pixels."))
    else:
        scripted_provider.enqueue_json(
            {
                "content": [{"type": "tool_use", "id": "complete-flow", "name": "browser_observe", "input": arguments}],
                "stop_reason": "tool_use",
            }
        )
        scripted_provider.enqueue_json(
            {
                "content": [{"type": "text", "text": "Observed synthetic current controls and exact original pixels."}],
                "stop_reason": "end_turn",
            }
        )
    scope = _scope(form_workspace)
    service = EngineeringRunService(
        form_workspace,
        client=scripted_provider.client(protocol, form_workspace, monkeypatch),
        permission_resolver=lambda _row: scope,
    )
    command = RoleRunCommand(
        "native-complete-flow",
        "director",
        "Use the supplied authorized browser tool on the synthetic form.",
        form_workspace,
    )
    result = await service.run_role(
        command, scope=scope, limits=RunLimits(time.time() + 90, 2_000_000, 64000), cancel=asyncio.Event()
    )
    assert result.status == "completed", result.text
    events = service.store.load_events(command.session_id)
    observation = next(
        e["payload"] for e in events if e["kind"] == "tool_observed" and e["payload"]["tool"] == "browser_observe"
    )
    assert observation["ok"], observation["content"]
    payload = json.loads(observation["content"])
    assert "COMPLETE_CURRENT_DOM" in payload["dom"]["body_text"]
    controls = {v["id"]: v for v in payload["dom"]["controls"]}
    assert controls["name"]["value"] == "  星河中文  "
    assert controls["note"]["value"] == " 第二步确认 "
    assert controls["plan"]["value"] == " 细节方案 "
    assert controls["clear"]["value"] == controls["tone"]["value"] == ""
    assert controls["long"]["value"] == "界" * 512
    assert controls["long"]["value_truncated"] is True
    assert controls["long"]["value_length_utf16"] == 700
    assert controls["private-password"]["value"] is None
    assert controls["private-password"]["value_observed"] is False
    assert "private-hidden" not in controls
    assert len(payload["dom"]["controls"]) == 32
    assert payload["dom"]["controls_truncated"] is True
    assert payload["dom"]["controls_total"] > 32
    assert len(scripted_provider.requests) == 2
    wire = scripted_provider.requests[0]["body"]
    schema = (
        next(t["function"]["parameters"] for t in wire["tools"] if t["function"]["name"] == "browser_observe")
        if protocol == "openai"
        else next(t["input_schema"] for t in wire["tools"] if t["name"] == "browser_observe")
    )
    assert schema == restored_schema == original_schema == _schema()
    Draft202012Validator(schema).validate(arguments)
    Draft202012Validator(schema).validate({"revisit_call_ids": ["original"]})
    image = observation["images"][0]
    pixels = base64.b64decode(image["data_base64"], validate=True)
    assert hashlib.sha256(pixels).hexdigest() == image["sha256"]
    assert image["data_base64"] in json.dumps(scripted_provider.requests[1]["body"])
    assert get_default_adapter().read_bytes(str(form_workspace / "public/index.html")) == original
    artifact = Path(
        os.environ.get("POLARIS_TEST_AUDIT_ROOT", str(Path(__file__).resolve().parents[7] / ".polaris/test-workspaces"))
    ) / ("complete-flow-" + uuid4().hex)
    guarded_create_directory(str(artifact.parent), artifact.name, parents=True, exist_ok=True)
    fs = get_default_adapter()
    fs.write_bytes(str(artifact / "current-dom.jpg"), pixels)
    fs.write_text(
        str(artifact / "tool-observation.json"), json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    fs.write_text(
        str(artifact / "native-requests.json"),
        json.dumps(scripted_provider.requests, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def test_complete_flow_scope_network_and_cold_policy_remain_host_owned(
    form_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, command, scope, limits = _seed(form_workspace, "scope-cold")
    guarded_create_directory(str(form_workspace), ".polaris", parents=True, exist_ok=True)
    fs = get_default_adapter()
    fs.write_text(str(form_workspace / ".polaris/hidden.html"), "<h1>CONTROL</h1>", encoding="utf-8")
    for entry in ("forbidden.html", ".polaris/hidden.html", "../outside.html"):
        value = await _dispatch(service, command, scope, limits, {"entry": entry, "actions": _ACTIONS})
        assert not value.ok and not value.images
    fs.write_text(
        str(form_workspace / "public/network.html"),
        '<button id="go">Go</button><output id="state"></output><script>document.querySelector("#go").onclick=()=>fetch("http://127.0.0.1:9/denied").catch(()=>document.querySelector("#state").textContent="NETWORK_BLOCKED")</script>',
        encoding="utf-8",
    )
    value = await _dispatch(
        service,
        command,
        scope,
        limits,
        {
            "entry": "public/network.html",
            "actions": [{"type": "click", "selector": "#go", "wait_ms": 100}],
            "frame_times_ms": [0],
        },
    )
    assert value.ok, value.content
    payload = json.loads(value.content)
    assert payload["network_allowed"] is False
    assert payload["blocked_requests"] == ["http://127.0.0.1:9/denied"]
    assert "NETWORK_BLOCKED" in payload["dom"]["body_text"]
    cold = EngineeringRunService(
        form_workspace, permission_resolver=lambda _row: replace(scope, enabled_tools=frozenset())
    )
    called = False

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal called
        called = True
        pytest.fail("revoked cold scope reached browser/process capture")

    monkeypatch.setattr(
        "polaris.cells.roles.runtime.internal.engineering_browser.capture_browser_observation", forbidden
    )
    denied = await _dispatch(cold, command, scope, limits, {"entry": "public/index.html", "actions": _ACTIONS})
    assert not denied.ok and not called
    assert not cold.store.pending_tool_calls(command.session_id)


async def test_complete_flow_unknown_drain_stays_pending(form_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, command, scope, limits = _seed(form_workspace, "unknown-flow")

    async def unknown(*_args: Any, **_kwargs: Any) -> Any:
        raise ProcessTreeDrainError("synthetic original process owner could not prove drain")

    monkeypatch.setattr(EngineeringTools, "_process", unknown)
    with pytest.raises(ProcessTreeDrainError, match="could not prove drain"):
        await _dispatch(service, command, scope, limits, {"entry": "public/index.html", "actions": _ACTIONS})
    assert len(service.store.pending_tool_calls(command.session_id)) == 1
    assert not service.store.load_events(command.session_id, kinds=("visual_observation_completed",))


@pytest.mark.parametrize(
    "action",
    [
        {"type": "fill", "selector": "#name"},
        {"type": "fill", "selector": "#name", "value": None},
        {"type": "fill", "selector": "#name", "value": "a\0b"},
        {"type": "fill", "selector": "#name", "value": "safe", "key": "Enter"},
        {"type": "click", "selector": "#prepare", "value": "ignored"},
        {"type": "select_option", "selector": "#plan", "value": "detail", "key": "Enter"},
    ],
)
async def test_new_action_shapes_fail_before_process(
    form_workspace: Path, action: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, command, scope, limits = _seed(form_workspace, "invalid-shape")

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("invalid normalized action reached physical process")

    monkeypatch.setattr(EngineeringTools, "_process", forbidden)
    observation = await _dispatch(service, command, scope, limits, {"entry": "public/index.html", "actions": [action]})
    assert not observation.ok and not observation.images
    assert not service.store.pending_tool_calls(command.session_id)


async def test_legal_long_flow_cancel_uses_original_process_owner(
    form_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, command, scope, limits = _seed(form_workspace, "cancel-flow")
    entered = asyncio.Event()
    original = EngineeringTools._process

    async def observed(self: EngineeringTools, argv: list[str], *, timeout: float, writes: tuple[Path, ...]) -> Any:
        entered.set()
        return await original(self, argv, timeout=timeout, writes=writes)

    monkeypatch.setattr(EngineeringTools, "_process", observed)
    actions = [dict(action) for action in _ACTIONS]
    actions[0]["wait_ms"] = 9000
    cancel = asyncio.Event()
    active = asyncio.create_task(
        _dispatch(service, command, scope, limits, {"entry": "public/index.html", "actions": actions}, cancel)
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    await asyncio.sleep(0.3)
    cancel.set()
    observation = await active
    assert observation.interrupted and not observation.ok and not observation.images
    assert "cancelled_and_drained" in observation.content
    assert not service.store.pending_tool_calls(command.session_id)
