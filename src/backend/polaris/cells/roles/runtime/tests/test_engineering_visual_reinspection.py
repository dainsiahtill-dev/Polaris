"""Saved-pixel reinspection; synthetic values are not native Bench evidence."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _response
from polaris.cells.roles.runtime.tests.test_engineering_verification_visual_ordering import _scope as _browser_scope
from polaris.cells.roles.runtime.tests.test_engineering_visual_service import _scope, _seed
from polaris.kernelone.fs import get_default_adapter, guarded_create_directory
from polaris.kernelone.llm.engine.client import ResilientLLMClient
from polaris.kernelone.llm.engine.executor import AIExecutor


def _working_scope(workspace: Path) -> PermissionScope:
    return _browser_scope(workspace)


def _fixture_client(provider: ScriptedProvider, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider.client("openai", workspace, monkeypatch)
    original = AIExecutor._get_provider_config

    def configured(self: AIExecutor, provider_id: str) -> dict[str, Any]:
        return {
            **original(self, provider_id),
            "max_context_tokens": 32768,
            "max_output_tokens": 28672,
            "max_tokens": 28672,
        }

    monkeypatch.setattr(AIExecutor, "_get_provider_config", configured)


def _physical_images(body: dict[str, Any]) -> list[bytes]:
    return [
        base64.b64decode(block["image_url"]["url"].split(",", 1)[1], validate=True)
        for message in body["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "image_url"
    ]


async def test_actual_native_optional_omission_and_three_state_reinspection_survive_cold_restore(
    tmp_path: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only model replies/config are scripted; service/SQLite/browser/sandbox/body stay real."""
    workspace = tmp_path / "visual-fixture"
    guarded_create_directory(str(tmp_path), "visual-fixture", parents=True, exist_ok=True)
    fs = get_default_adapter()
    for state in "abcde":
        fs.write_text(
            str(workspace / f"{state}.html"),
            f"<!doctype html><h1>Distinct saved state {state.upper()}</h1>",
            encoding="utf-8",
        )
    _fixture_client(scripted_provider, workspace, monkeypatch)

    def capture(state: str) -> dict[str, Any]:
        return {"entry": f"{state}.html", "viewport": {"width": 640, "height": 360}, "frame_times_ms": [0]}

    scripted_provider.enqueue_json(
        _response(("verify", "verify_project", {"commands": ["python3 -c \"print('REAL_VISUAL_FIXTURE_CHECK')\""]}))
    )
    for state in "abcde":
        scripted_provider.enqueue_json(_response(("cap-" + state, "browser_observe", capture(state))))
    scripted_provider.enqueue_json(
        _response(("revisit-three", "browser_observe", {"revisit_call_ids": ["cap-a", "cap-c", "cap-e"]}))
    )
    # A real failed physical send carries selected bytes but cannot release pins.
    scripted_provider.enqueue_json({"error": "synthetic finite transport failure"}, status=500)
    service = EngineeringRunService(
        workspace, require_qa=True, permission_resolver=lambda _row: _working_scope(workspace)
    )
    command = RoleRunCommand(
        "saved-visual-cold",
        "director",
        "Inspect all five fixture states, compare saved nonadjacent states, and obtain independent QA.",
        workspace,
    )
    await service.start(command, scope=_working_scope(workspace), limits=RunLimits(time.time() + 90, 2_000_000, 64000))
    await service.wait(command.session_id, workspace=workspace)
    first = service.store.load_run(command.session_id, workspace=workspace)
    assert first["status"] == "failed", first["text"]
    events = service.store.load_events(command.session_id)
    original = {
        event["payload"]["call_id"]: event["payload"]
        for event in events
        if event["kind"] == "tool_observed" and event["payload"].get("call_id", "").startswith("cap-")
    }
    assert len(original) == 5 and all(value["ok"] for value in original.values())
    raw = {
        identity: base64.b64decode(value["images"][0]["data_base64"], validate=True)
        for identity, value in original.items()
    }
    assert len({hashlib.sha256(value).hexdigest() for value in raw.values()}) == 5
    assert raw["cap-a"] not in _physical_images(scripted_provider.requests[6]["body"])
    assert _physical_images(scripted_provider.requests[7]["body"]) == [
        raw[identity] for identity in ("cap-a", "cap-c", "cap-e")
    ]
    revisited = next(
        event["payload"]
        for event in events
        if event["kind"] == "tool_observed" and event["payload"].get("call_id") == "revisit-three"
    )
    assert revisited["ok"] and "images" not in revisited
    assert len([event for event in events if event["kind"] == "visual_observation_completed"]) == 5
    assert not service.store.pending_tool_calls(command.session_id)
    before_originals = json.dumps(original, ensure_ascii=False, sort_keys=True)
    for response in (
        _response(("fresh-after-cold", "browser_observe", capture("e"))),
        _response(text="Fresh own current pixels and saved comparisons inspected."),
        _response(("qa-source", "read_file", {"file": "a.html"}), ("qa-own", "browser_observe", capture("e"))),
        _response(
            (
                "qa-approve",
                "report_review",
                {"approved": True, "feedback": "Read current source and own actual current pixels."},
            )
        ),
    ):
        scripted_provider.enqueue_json(response)
    child = r"""
import asyncio,json,sys
from pathlib import Path
import pytest
from polaris.cells.roles.runtime.tests.engineering_support import ScriptedProvider
from polaris.cells.roles.runtime.tests.test_engineering_visual_reinspection import _fixture_client,_working_scope
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
workspace=Path(sys.argv[1])
_fixture_client(ScriptedProvider(base_url=sys.argv[2]),workspace,pytest.MonkeyPatch())
async def main():
    service=EngineeringRunService(workspace,require_qa=True,permission_resolver=lambda row:_working_scope(workspace))
    await service.resume('saved-visual-cold',workspace=workspace)
    await service.wait('saved-visual-cold',workspace=workspace)
    row=service.snapshot('saved-visual-cold',workspace=workspace)
    print(json.dumps({k:row[k] for k in ('status','verification','qa_verdict','project_outcome')}),flush=True)
asyncio.run(main())
"""
    cold = await asyncio.to_thread(
        subprocess.run,
        ["rtk", "proxy", sys.executable, "-c", child, str(workspace), scripted_provider.base_url],
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert cold.returncode == 0, cold.stderr
    assert '"accepted"' in cold.stdout, cold.stdout + cold.stderr
    assert _physical_images(scripted_provider.requests[8]["body"]) == [
        raw[identity] for identity in ("cap-a", "cap-c", "cap-e")
    ]
    after = service.store.load_events(command.session_id)
    originals_after = {
        event["payload"]["call_id"]: event["payload"]
        for event in after
        if event["kind"] == "tool_observed" and event["payload"].get("call_id") in original
    }
    assert json.dumps(originals_after, ensure_ascii=False, sort_keys=True) == before_originals
    assert len([event for event in after if event["kind"] == "verification_completed"]) == 1
    assert len([event for event in after if event["kind"] == "visual_observation_completed"]) == 7
    assert "visual_input_sets" not in json.dumps(scripted_provider.requests)
    assert "visual_working_set" not in json.dumps(scripted_provider.requests)
    owner = Path(__file__).resolve().parents[7]
    artifact = Path(os.environ.get("POLARIS_TEST_AUDIT_ROOT", str(owner / ".polaris/test-workspaces"))) / (
        "visual-reinspection-" + uuid4().hex
    )
    fs.write_text(
        str(artifact / "integrated-native-requests.json"),
        json.dumps(scripted_provider.requests, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    fs.write_text(
        str(artifact / "integrated-events.json"), json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    fs.write_text(str(artifact / "cold-child.log"), cold.stdout + cold.stderr, encoding="utf-8")


@pytest.mark.parametrize("phase,status", [("prepared", "prepared"), ("terminal", "failed"), ("terminal", "unknown")])
def test_auxiliary_failed_or_unknown_native_facts_do_not_prove_image_delivery(phase: str, status: str) -> None:
    from polaris.cells.roles.runtime.internal.engineering_visual import _delivered

    event: dict[str, Any] = {
        "kind": "llm_request_observed",
        "role": "qa",
        "payload": {
            "phase": phase,
            "status": status,
            "turn_seq": 20,
            "audit": {"audit_scope": "provider_native_wire", "image_sha256": ["hash"]},
        },
    }
    assert not _delivered([event], "qa", ["hash"], 10)
    assert not _delivered([{**event, "kind": "request_prepared"}], "qa", ["hash"], 10)
    completed = copy.deepcopy(event)
    completed["payload"].update(phase="terminal", status="completed")
    assert _delivered([completed], "qa", ["hash"], 10)
    assert not _delivered([completed], "director", ["hash"], 10)
    assert not _delivered([completed], "qa", ["hash"], 20)


def test_staleness_precedes_unseen_selection_but_explicit_pin_restores_exact_bytes(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.internal.engineering_visual import build_visual_messages
    from polaris.cells.roles.runtime.tests.test_engineering_visual_projection import _binding, _history, _image, _pixels

    store, command, _scope_value, _limits = _history(isolated_workspace)
    messages = store.load_message_history(command.session_id, role="director")
    events = store.load_events(command.session_id)
    audit: dict[str, Any] = {}
    view = build_visual_messages(messages, events=events, role="director", current_binding=_binding(2), audit=audit)
    assert _image("old-desktop").data_base64 not in _pixels(view)
    assert all(item["required"] for item in audit["image_sets"])  # current captures have no proven delivery
    unknown: dict[str, Any] = {}
    unbound = build_visual_messages(messages, events=events, role="director", audit=unknown)
    assert _image("old-desktop").data_base64 in _pixels(unbound)
    assert all(item["required"] for item in unknown["image_sets"])
    pinned = build_visual_messages(
        messages, events=events, role="director", current_binding=_binding(2), pinned_call_ids=frozenset({"old"})
    )
    assert _image("old-desktop").data_base64 in _pixels(pinned)
    assert _image("old-mobile").data_base64 in _pixels(pinned)
    assert store.load_message_history(command.session_id, role="director") == messages


def test_original_capture_rejects_cross_role_and_corrupt_bytes(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.internal.engineering_visual import original_visual_capture
    from polaris.cells.roles.runtime.tests.test_engineering_visual_projection import _history

    store, command, _scope_value, _limits = _history(isolated_workspace)
    events = store.load_events(command.session_id)
    with pytest.raises(ValueError, match="unavailable"):
        original_visual_capture(events, "qa", "old")
    corrupted = copy.deepcopy(events)
    observation = next(
        event for event in corrupted if event["kind"] == "tool_observed" and event["payload"].get("call_id") == "old"
    )
    observation["payload"]["images"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        original_visual_capture(corrupted, "director", "old")
    assert store.load_events(command.session_id) == events


@pytest.mark.parametrize(
    "arguments", [{"revisit_call_ids": ["absent"]}, {"revisit_call_ids": ["absent"], "entry": "index.html"}]
)
async def test_invalid_reinspection_cannot_spawn_or_create_candidate_facts(
    isolated_workspace: Path, arguments: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope(isolated_workspace)
    service = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: scope)
    command, limits = _seed(service, scope)
    before = service.store.load_events(command.session_id)

    async def forbidden(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("Invalid reinspection must not enter capture/browser/process owner")

    monkeypatch.setattr(
        "polaris.cells.roles.runtime.internal.engineering_browser.capture_browser_observation", forbidden
    )
    token = service._owner.set((command, scope, limits, asyncio.Event(), cast(ResilientLLMClient, None)))
    role = service._active_role.set("director")
    try:
        with pytest.raises(ValueError):
            await service._observe_dispatch(arguments, 12, "revisit")
    finally:
        service._active_role.reset(role)
        service._owner.reset(token)
    assert service.store.load_events(command.session_id) == before
