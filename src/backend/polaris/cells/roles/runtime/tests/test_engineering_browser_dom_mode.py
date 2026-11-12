"""Actual contained DOM inspection and saved host mode; no native-model acceptance."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.bootstrap.config import EngineeringBrowserObservationConfig, ServerConfig, Settings
from polaris.bootstrap.engineering_runtime import resolve_engineering_profile
from polaris.cells.roles.runtime.internal.engineering_browser import capture_browser_observation
from polaris.cells.roles.runtime.internal.engineering_roles import register_browser_observation_tool, role_instruction
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    BrowserObservationConfig,
    PermissionScope,
    RoleRunCommand,
    RunLimits,
)
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.browser_support import (
    browser_executable,
    browser_packages,
    browser_python,
)
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_browser_complete_flow import _dispatch
from polaris.cells.roles.runtime.tests.test_engineering_roles import _native
from polaris.kernelone.process.process_tree import ProcessTreeRunControl, run_process_tree_safe
from polaris.kernelone.process.sandbox import build_workspace_sandbox_argv
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

BROWSER = browser_executable()
PYTHON = browser_python()
PACKAGES = browser_packages()


def _scope(workspace: Path, mode: str = "dom") -> PermissionScope:
    return PermissionScope(
        (
            workspace,
            PYTHON.parent.parent,
            PACKAGES / "playwright",
            PACKAGES / "pyee",
            PACKAGES / "greenlet",
            BROWSER.parent,
        ),
        (),
        frozenset({"browser_observe"}),
        False,
        BrowserObservationConfig(str(BROWSER), str(PYTHON), (str(PACKAGES),), observation_mode=mode),
    )


def _runner(workspace: Path, scope: PermissionScope):
    async def execute(argv, timeout):
        sandboxed = build_workspace_sandbox_argv(
            argv, workspace=workspace, cwd=workspace, read_roots=scope.read_roots, write_roots=(), network_allowed=False
        )
        return await asyncio.to_thread(
            run_process_tree_safe,
            sandboxed,
            cwd=workspace,
            timeout=timeout,
            encoding="utf-8",
            cancel_control=ProcessTreeRunControl(),
        )

    return execute


def _page(workspace: Path) -> dict[str, bytes]:
    (workspace / ".polaris").mkdir(exist_ok=True)
    files = {
        "index.html": '<!doctype html><meta charset="utf-8"><title>Actual DOM</title><input id="name"><button id="go">Apply</button><output id="result">initial</output><script src="/app.js"></script><script src="/broken.js"></script>',
        "app.js": 'document.querySelector("#go").onclick=()=>{document.querySelector("#result").textContent=document.querySelector("#name").value;console.log("ACTUAL_HANDLER")};fetch("https://outside.invalid/").catch(()=>console.log("NETWORK_BLOCKED"));',
        "broken.js": "exports.unsupported = true;",
    }
    for name, content in files.items():
        (workspace / name).write_text(content, encoding="utf-8")
    return {name: (workspace / name).read_bytes() for name in files}


def _arguments() -> dict:
    return {
        "web_root": ".",
        "frame_times_ms": [0, 50],
        "actions": [
            {"type": "fill", "selector": "#name", "value": " 真實 DOM "},
            {"type": "click", "selector": "#go"},
        ],
    }


@pytest.mark.parametrize("config_type", [BrowserObservationConfig, EngineeringBrowserObservationConfig])
def test_host_rejects_dom_with_required_visual_review_and_invalid_mode(config_type):
    values = {"executable": "/opt/browser", "python_executable": "/opt/python", "python_paths": ()}
    assert config_type(**values).observation_mode == "images"
    with pytest.raises(ValueError, match="visual"):
        config_type(**values, observation_mode="dom", require_visual_review=True)
    with pytest.raises(ValueError):
        config_type(**values, observation_mode="automatic")


def test_host_profile_and_cold_scope_preserve_dom_and_reject_mode_change(isolated_workspace: Path):
    browser = {
        "executable": "/opt/browser",
        "python_executable": "/opt/python",
        "python_paths": [],
        "observation_mode": "dom",
    }
    settings = Settings(
        workspace=isolated_workspace,
        server=ServerConfig.model_validate(
            {
                "engineering_permission_profiles": {
                    "dom": {"read_roots": [], "write_roots": [], "enabled_tools": [], "browser": browser}
                }
            }
        ),
    )
    scope = resolve_engineering_profile(settings, isolated_workspace, "dom")
    assert scope.browser.observation_mode == "dom"
    assert not scope.enabled_tools and not scope.read_roots and not scope.write_roots
    store = EngineeringSessionStore(isolated_workspace)
    command = RoleRunCommand("dom-saved", "director", "Observe", isolated_workspace)
    limits = RunLimits(time.time() + 60, 100000, 64000)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key="dom-key")
    row = EngineeringSessionStore(isolated_workspace).load_run(command.session_id, workspace=isolated_workspace)
    assert EngineeringRunService._scope(row) == scope
    service = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: scope)
    assert service._revalidated_scope(row) == scope
    changed_policy = replace(scope, browser=replace(scope.browser, observation_mode="images"))
    cold_service = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: changed_policy)
    with pytest.raises(PermissionError, match="permission policy"):
        cold_service._revalidated_scope(row)
    with pytest.raises(ValueError, match="conflict"):
        store.create_run(
            command,
            scope=replace(scope, browser=replace(scope.browser, observation_mode="images")),
            limits=limits,
            engine_version="engineering_v2",
            request_key="dom-key",
        )
    del row["scope"]["browser"]["observation_mode"]
    assert EngineeringRunService._scope(row).browser.observation_mode == "images"


async def test_dom_capture_observes_actual_controls_js_errors_and_no_images(isolated_workspace: Path):
    before = _page(isolated_workspace)
    scope = _scope(isolated_workspace)
    result = await capture_browser_observation(
        "dom", _arguments(), workspace=isolated_workspace, scope=scope, run_process=_runner(isolated_workspace, scope)
    )
    assert result.ok, result.content
    assert result.images == ()
    payload = json.loads(result.content)
    assert payload["observation_mode"] == "dom" and payload["visual_evidence"] is False
    assert payload["dom_observation"]["timing"] == "final_after_actions_and_wait"
    assert payload["dom_observation"]["requested_offset_ms"] == 50
    # Report measured elapsed time, not an invented exact timer guarantee.
    # Chromium timers may round the requested50ms wait to49ms.
    assert isinstance(payload["dom_observation"]["observed_offset_ms"], int)
    assert payload["dom_observation"]["observed_offset_ms"] >= 0
    assert payload["dom"]["controls"][0]["value"] == " 真實 DOM "
    assert "真實 DOM" in payload["dom"]["body_text"]
    assert "ACTUAL_HANDLER" in payload["console"]
    assert any("exports is not defined" in error for error in payload["page_errors"])
    assert any("outside.invalid" in url for url in payload["blocked_requests"])
    assert {entry["path"] for entry in payload["static_resources"]["reads"]} >= {"index.html", "app.js", "broken.js"}
    assert {name: (isolated_workspace / name).read_bytes() for name in before} == before
    assert "data_base64" not in result.content and "image_url" not in result.content


async def test_omitted_mode_keeps_actual_jpeg_capture(isolated_workspace: Path):
    _page(isolated_workspace)
    scope = _scope(isolated_workspace, "images")
    scope = replace(scope, browser=BrowserObservationConfig(str(BROWSER), str(PYTHON), (str(PACKAGES),)))
    result = await capture_browser_observation(
        "images",
        {"web_root": ".", "frame_times_ms": [0]},
        workspace=isolated_workspace,
        scope=scope,
        run_process=_runner(isolated_workspace, scope),
    )
    assert result.ok, result.content
    assert len(result.images) == 1 and result.images[0].media_type == "image/jpeg"
    assert json.loads(result.content)["visual_evidence"] is True


@pytest.mark.parametrize("role", ["director", "qa"])
def test_dom_prompt_and_shared_schema_state_host_boundary(role):
    register_browser_observation_tool()
    before = ToolSpecRegistry.get_llm_schema("browser_observe")
    text = role_instruction(role, visual=True, observation_mode="dom")
    assert "DOM-only" in text and "no images" in text and "visual correctness" in text
    assert "receive actual images" not in text
    assert "Inspect those images" not in text
    assert "DOM" in before["function"]["description"]
    assert "observation_mode" not in before["function"]["parameters"]["properties"]
    assert ToolSpecRegistry.get_llm_schema("browser_observe") == before


async def test_dom_revisit_refused_before_saved_image_lookup(isolated_workspace: Path):
    scope = _scope(isolated_workspace)
    service = EngineeringRunService(isolated_workspace)
    command = RoleRunCommand("dom-revisit", "director", "Observe", isolated_workspace)
    limits = RunLimits(time.time() + 30, 100000, 64000)
    service.store.create_run(
        command, scope=scope, limits=limits, engine_version=service.engine_version, request_key=None
    )
    result = await _dispatch(service, command, scope, limits, {"revisit_call_ids": ["old-image"]})
    assert not result.ok and "DOM-only" in result.content
    assert not result.images


async def test_dom_actual_provider_request_contains_text_evidence_and_no_image_blocks(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
):
    _page(isolated_workspace)
    scripted_provider.enqueue_json(_native(("dom-native", "browser_observe", _arguments())))
    scripted_provider.enqueue_json(
        _native(text="Actual DOM observed; browser JavaScript failed; visual correctness unverified.")
    )
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    result = await service.run_role(
        RoleRunCommand("dom-native", "director", "Inspect actual browser behavior", isolated_workspace),
        scope=_scope(isolated_workspace),
        limits=RunLimits(time.time() + 60, 1000000, 64000),
        cancel=asyncio.Event(),
    )
    assert result.status == "completed"
    events = service.store.load_events("dom-native")
    captured = [event["payload"] for event in events if event["kind"] == "tool_observed"]
    assert len(captured) == 1 and captured[0]["ok"], captured
    assert not any(event["kind"] == "visual_observation_completed" for event in events)
    assert len(scripted_provider.requests) == 2
    final = scripted_provider.requests[-1]["body"]
    wire = json.dumps(final, ensure_ascii=False)
    assert "exports is not defined" in wire and "ACTUAL_HANDLER" in wire
    assert "DOM-only" in final["messages"][0]["content"]
    assert "image_url" not in wire and "data:image" not in wire and "data_base64" not in wire


def test_default_image_scope_preserves_historical_json_and_idempotency(isolated_workspace: Path):
    browser = BrowserObservationConfig("/opt/browser", "/opt/python", ())
    scope = PermissionScope((), (), frozenset(), False, browser)
    command = RoleRunCommand("images-saved", "director", "Observe", isolated_workspace)
    limits = RunLimits(time.time() + 60, 100000, 64000)
    store = EngineeringSessionStore(isolated_workspace)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key="image-key")
    row = store.load_run(command.session_id, workspace=isolated_workspace)
    assert row["scope"]["browser"] == {
        "executable": "/opt/browser",
        "python_executable": "/opt/python",
        "python_paths": [],
        "require_visual_review": False,
    }
    cold = EngineeringSessionStore(isolated_workspace)
    assert (
        cold.create_run(
            replace(command, session_id="retry-id"),
            scope=scope,
            limits=limits,
            engine_version="engineering_v2",
            request_key="image-key",
        )
        == command.session_id
    )
    assert EngineeringRunService._scope(row).browser.observation_mode == "images"


async def test_dom_model_cannot_override_host_mode(isolated_workspace: Path):
    _page(isolated_workspace)
    scope = _scope(isolated_workspace)
    result = await capture_browser_observation(
        "mode-override",
        {"observation_mode": "images"},
        workspace=isolated_workspace,
        scope=scope,
        run_process=_runner(isolated_workspace, scope),
    )
    assert not result.ok and "unsupported" in result.content
    assert not result.images
