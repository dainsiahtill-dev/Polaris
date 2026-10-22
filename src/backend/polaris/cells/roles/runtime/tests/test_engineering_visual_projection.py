"""Synthetic journal fixtures: model-input selection, never native-model evidence."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from polaris.cells.roles.runtime.internal.engineering_loop import _native_calls, run_engineering_loop
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.internal.engineering_visual import build_visual_messages, require_current_visual_input
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    BrowserObservationConfig,
    ImageObservation,
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
    tool_observation_payload,
)
from polaris.cells.roles.runtime.tests.engineering_support import isolated_workspace as isolated_workspace
from polaris.cells.roles.runtime.tests.test_engineering_loop import _native
from polaris.cells.roles.runtime.tests.test_engineering_visual_messages import RAW
from polaris.kernelone.llm.engine.contracts import AIRequest, AIResponse
from polaris.kernelone.llm.engine.prompt_budget import compress_chat_messages_to_budget
from polaris.kernelone.llm.engine.provider_native_request import (
    build_anthropic_native_messages,
    build_openai_native_messages,
)


def _image(label: str) -> ImageObservation:
    raw = RAW + label.encode("utf-8")
    return ImageObservation(
        "image/png", base64.b64encode(raw).decode("ascii"), 1, 1, hashlib.sha256(raw).hexdigest(), label
    )


def _binding(revision: int) -> dict[str, Any]:
    return {
        "workspace_revision": revision,
        "verification_seq": 0,
        "required_checks": [],
        "scope": {"read_roots": ["/synthetic"], "write_roots": ["/synthetic"], "network_allowed": False},
        "profile": {
            "read_roots": ["/synthetic"],
            "output_roots": [],
            "execute_command": True,
            "network_allowed": False,
        },
        "inputs": {"/synthetic/index.html": {"sha256": f"candidate-{revision}"}},
    }


def _capture(store: EngineeringSessionStore, binding: dict[str, Any], identity: str, labels: tuple[str, ...]) -> None:
    turn = store.append_event("projection", kind="turn_started", role="director", payload={})
    arguments = {"entry": "index.html", "viewport": {"width": 1200, "height": 800}}
    store.append_message(
        "projection",
        role="director",
        message={
            "role": "assistant",
            "content": "",
            "tool_calls": _native(
                (identity, "browser_observe", arguments), (f"{identity}-error", "read_file", {"file": "missing.txt"})
            )["choices"][0]["message"]["tool_calls"],
        },
    )
    store.record_tool_dispatch(
        "projection", turn_seq=turn, call_id=identity, tool="browser_observe", arguments=arguments, role="director"
    )
    images = tuple(_image(label) for label in labels)
    store.append_event(
        "projection",
        kind="visual_observation_completed",
        role="director",
        payload={
            "ok": True,
            "requested_by": [turn, identity],
            "input_binding": binding,
            "image_sha256": [image.sha256 for image in images],
        },
    )
    observation = ToolObservation(
        identity, "browser_observe", True, f"capture facts {identity}", 0, False, images=images
    )
    store.record_tool_observation("projection", turn_seq=turn, observation=observation, role="director")
    store.append_message(
        "projection",
        role="director",
        message={
            "role": "tool",
            "tool_call_id": identity,
            "content": json.dumps(tool_observation_payload(observation), ensure_ascii=False),
        },
    )
    store.record_tool_dispatch(
        "projection",
        turn_seq=turn,
        call_id=f"{identity}-error",
        tool="read_file",
        arguments={"file": "missing.txt"},
        role="director",
    )
    failed = ToolObservation(f"{identity}-error", "read_file", False, "File not found: missing.txt", None, False)
    store.record_tool_observation("projection", turn_seq=turn, observation=failed, role="director")
    store.append_message(
        "projection",
        role="director",
        message={
            "role": "tool",
            "tool_call_id": failed.call_id,
            "content": json.dumps(tool_observation_payload(failed), ensure_ascii=False),
        },
    )


def _history(workspace: Path) -> tuple[EngineeringSessionStore, RoleRunCommand, PermissionScope, RunLimits]:
    store = EngineeringSessionStore(workspace)
    command = RoleRunCommand("projection", "director", "Keep the goal and current QA feedback.", workspace)
    scope = PermissionScope(
        (workspace,),
        (),
        frozenset({"browser_observe"}),
        False,
        BrowserObservationConfig("/usr/bin/true", "/usr/bin/python3", ()),
    )
    limits = RunLimits(None, 100000, 64000)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=None)
    store.append_event(
        "projection",
        kind="role_started",
        role="director",
        payload={"instruction": command.instruction, "purpose": "role"},
    )
    store.append_message("projection", role="director", message={"role": "system", "content": "You are Director."})
    reference = _image("original-user-reference")
    store.append_message(
        "projection",
        role="director",
        message={
            "role": "user",
            "content": [
                {"type": "text", "text": command.instruction},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": reference.media_type, "data": reference.data_base64},
                },
            ],
        },
    )
    _capture(store, _binding(1), "old", ("old-desktop", "old-mobile"))
    _capture(store, _binding(2), "current-a", ("current-desktop", "current-mobile"))
    _capture(store, _binding(2), "current-b", ("current-later-time",))
    store.append_message(
        "projection",
        role="director",
        message={"role": "user", "content": "Current QA rejected the spacing; preserve this feedback."},
    )
    return store, command, scope, limits


def _pixels(messages: list[dict[str, Any]]) -> list[str]:
    return [
        block["source"]["data"]
        for message in messages
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "image"
    ]


class _NoTransportClient:
    """Only replace external model execution; the role loop and journal are real."""

    def __init__(self) -> None:
        self.requests: list[AIRequest] = []

    async def invoke(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        return AIResponse(ok=True, output="Inspected synthetic input.", raw=_native(text="Inspected synthetic input."))


async def test_no_browser_history_never_requires_observing_current_candidate(isolated_workspace: Path) -> None:
    """Catches the eager manifest scan preventing even a no-browser first role turn."""
    os.mkfifo(isolated_workspace / "dev.sock")
    store = EngineeringSessionStore(isolated_workspace)
    command = RoleRunCommand("no-images", "director", "Keep goal and failure feedback.", isolated_workspace)
    scope = PermissionScope(
        (isolated_workspace,), (), frozenset(), False, BrowserObservationConfig("/usr/bin/true", "/usr/bin/python3", ())
    )
    limits = RunLimits(None, 100000, 64000)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=None)
    store.append_event(
        command.session_id,
        kind="role_started",
        role="director",
        payload={"instruction": command.instruction, "purpose": "role"},
    )
    for message in [
        {"role": "system", "content": "You are Director."},
        {"role": "user", "content": command.instruction},
        {"role": "user", "content": "Previous failure feedback must stay available."},
    ]:
        store.append_message(command.session_id, role="director", message=message)
    verifier = EngineeringVerification(
        isolated_workspace, store=store, scope=scope, limits=limits, cancel=asyncio.Event()
    )
    # Prove the real callback is currently unobservable; the role must not need it.
    with pytest.raises(ValueError, match="cannot bind special input"):
        verifier.review_binding(command.session_id)
    client = _NoTransportClient()
    result = await run_engineering_loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=cast(Any, client),
        visual_binding=lambda: verifier.review_binding(command.session_id),
    )
    assert result.status == "completed", result.text
    assert client.requests[0].options["messages"][-1]["content"] == "Previous failure feedback must stay available."
    assert result.verification == "not_run"


@pytest.mark.parametrize("failure", ["special-input", "missing-file"])
async def test_unobservable_candidate_retains_browser_pixels_and_auxiliary_reason(
    isolated_workspace: Path, failure: str
) -> None:
    """Catches observational scan failures aborting input or silently discarding pixels."""
    store, command, scope, limits = _history(isolated_workspace)
    before = store.load_message_history(command.session_id, role="director")
    verifier = EngineeringVerification(
        isolated_workspace, store=store, scope=scope, limits=limits, cancel=asyncio.Event()
    )
    if failure == "special-input":
        os.mkfifo(isolated_workspace / "dev.sock")
        expected = "CandidateObservationUnavailableError"
    else:
        expected = "FileNotFoundError"

    def binding() -> dict[str, Any]:
        if failure == "special-input":
            return verifier.review_binding(command.session_id)
        raise FileNotFoundError("source disappeared during read-only manifest observation")

    client = _NoTransportClient()
    result = await run_engineering_loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=cast(Any, client),
        visual_binding=binding,
    )
    assert result.status == "completed", result.text
    assert _pixels(client.requests[0].options["messages"]) == _pixels(build_visual_messages(before))
    prepared = next(e["payload"] for e in store.load_events(command.session_id) if e["kind"] == "request_prepared")
    assert prepared["visual_projection"]["binding_observation"]["status"] == "unknown"
    assert prepared["visual_projection"]["binding_observation"]["error_type"] == expected
    assert prepared["visual_projection"]["omitted_image_count"] == 0
    assert all(
        item["reason"] == "current_binding_unavailable" for item in prepared["visual_projection"]["observations"]
    )
    assert result.verification == "not_run"
    with pytest.raises(ValueError, match="current visual"):
        require_current_visual_input(
            store, command.session_id, role="director", frame_seq=0, turn_seq=10000, current_binding=_binding(2)
        )


@pytest.mark.parametrize(
    "error",
    [
        PermissionError("host denied"),
        ValueError("engineering store workspace mismatch"),
        RuntimeError("host policy unavailable"),
    ],
)
async def test_binding_authority_errors_still_stop_the_loop_before_provider(
    isolated_workspace: Path, error: Exception
) -> None:
    """Catches a broad retention fallback swallowing policy or identity failures."""
    store, command, scope, limits = _history(isolated_workspace)

    def binding() -> dict[str, Any]:
        raise error

    client = _NoTransportClient()
    result = await run_engineering_loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=cast(Any, client),
        visual_binding=binding,
    )
    assert result.status == "failed" and str(error) in result.text
    assert not client.requests


@pytest.mark.parametrize("unsafe_input", [False, True], ids=["bindable-before-pixels", "fifo-refused"])
async def test_verification_before_pixels_requires_fully_observable_inputs(
    isolated_workspace: Path, unsafe_input: bool
) -> None:
    """Real checks need bindable inputs; visual review belongs after those checks."""
    store, command, scope, limits = _history(isolated_workspace)
    if unsafe_input:
        os.mkfifo(isolated_workspace / "dev.sock")
    assert scope.browser is not None
    scope = replace(
        scope,
        enabled_tools=scope.enabled_tools | {"verify_project", "execute_command"},
        browser=replace(scope.browser, require_visual_review=True),
    )
    verifier = EngineeringVerification(
        isolated_workspace, store=store, scope=scope, limits=limits, cancel=asyncio.Event()
    )

    class VerificationAttempt(_NoTransportClient):
        async def invoke(self, request: AIRequest) -> AIResponse:
            self.requests.append(request)
            if len(self.requests) == 1:
                return AIResponse(
                    ok=True,
                    raw=_native(("verify-attempt", "verify_project", {"commands": ["printf REAL_BINDABLE_CHECK"]})),
                )
            return AIResponse(
                ok=True,
                output="Cannot verify the current candidate.",
                raw=_native(text="Cannot verify the current candidate."),
            )

    async def verify(commands: tuple[str, ...], turn: int, identity: str) -> ToolObservation:
        from polaris.cells.roles.runtime.internal.engineering_verification import verification_observation

        await verifier.run_verification(
            commands, session_id=command.session_id, workspace_revision=0, requested_by=(turn, identity)
        )
        payload = next(
            event["payload"]
            for event in reversed(store.load_events(command.session_id))
            if event["kind"] == "verification_completed"
        )
        return verification_observation(identity, payload, limits.max_tool_output_bytes)

    client = VerificationAttempt()
    result = await run_engineering_loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=cast(Any, client),
        visual_binding=lambda: verifier.review_binding(command.session_id),
        verify=verify,
    )
    events = store.load_events(command.session_id)
    observed = next(
        event["payload"]
        for event in events
        if event["kind"] == "tool_observed" and event["payload"].get("call_id") == "verify-attempt"
    )
    children = [
        event
        for event in events
        if event["kind"] == "tool_started" and event["payload"].get("tool") == "execute_command"
    ]
    if unsafe_input:
        assert observed["ok"] is False and "cannot bind special input" in observed["content"]
        assert not children
    else:
        assert observed["ok"] is True and "REAL_BINDABLE_CHECK" in observed["content"]
        assert len(children) == 1
    assert result.verification == ("unknown" if unsafe_input else "not_run")
    assert store.load_run(command.session_id, workspace=isolated_workspace)["project_outcome"] != "accepted"


async def test_real_loop_projects_old_pixels_and_retains_all_current_views(isolated_workspace: Path) -> None:
    """Catches a missing loop consumer: budget sees old pixels despite a current binding."""
    store, command, scope, limits = _history(isolated_workspace)
    before = store.load_message_history("projection", role="director")
    client = _NoTransportClient()
    result = await run_engineering_loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=cast(Any, client),  # Only the external transport is replaced.
        visual_binding=lambda: _binding(2),
    )
    assert result.status == "completed", result.text
    request = client.requests[0]
    assert _pixels(request.options["messages"]) == [
        _image(label).data_base64
        for label in ("original-user-reference", "current-desktop", "current-mobile", "current-later-time")
    ]
    assert request.options["messages"][-1] == before[-1]
    assert store.load_message_history("projection", role="director")[:-1] == before
    prepared = next(
        event["payload"] for event in store.load_events("projection") if event["kind"] == "request_prepared"
    )
    assert prepared["visual_projection"]["omitted_image_count"] == 2


def test_cold_sqlite_projection_preserves_originals_and_all_nonpixel_tool_facts(isolated_workspace: Path) -> None:
    """Catches destructive history trimming, cold selection drift and lost error feedback."""
    store, _command, _scope, _limits = _history(isolated_workspace)
    original = store.load_message_history("projection", role="director")
    events = store.load_events("projection")
    view = build_visual_messages(original, events=events, role="director", current_binding=_binding(2))
    cold = EngineeringSessionStore(isolated_workspace)
    audit: dict[str, Any] = {}
    recovered = build_visual_messages(
        cold.load_message_history("projection", role="director"),
        events=cold.load_events("projection"),
        role="director",
        current_binding=_binding(2),
        audit=audit,
    )
    assert recovered == view
    assert store.load_events("projection") == events
    assert store.load_message_history("projection", role="director") == original
    assert _pixels(view) == [
        _image(label).data_base64
        for label in ("original-user-reference", "current-desktop", "current-mobile", "current-later-time")
    ]
    assert audit["omitted_image_count"] == 2 and audit["retained_image_count"] == 3
    native_original = [m for m in original if m["role"] == "assistant"]
    assert [m for m in view if m["role"] == "assistant"] == native_original
    for before, after in zip(
        [m for m in original if m["role"] == "tool"], [m for m in view if m["role"] == "tool"], strict=True
    ):
        assert before["tool_call_id"] == after["tool_call_id"]
        old, new = json.loads(before["content"]), json.loads(after["content"])
        assert {k: v for k, v in old.items() if k != "images"} == {
            k: v for k, v in new.items() if k not in {"images", "visual_projection"}
        }
        if "images" in old:
            assert [{k: v for k, v in image.items() if k != "data_base64"} for image in old["images"]] == new["images"]
        else:
            assert before == after
    with pytest.raises(ValueError, match="current visual"):
        require_current_visual_input(
            cold, "projection", role="director", frame_seq=0, turn_seq=10000, current_binding=_binding(2)
        )


def test_copied_historical_messages_keep_the_unique_original_observation_binding(isolated_workspace: Path) -> None:
    """Catches real continuation frames copying history without duplicating tool execution."""
    store, _command, _scope, _limits = _history(isolated_workspace)
    messages = store.load_message_history("projection", role="director")
    events = store.load_events("projection")
    original = next(e for e in events if e["payload"].get("message", {}).get("tool_call_id") == "old")
    events.extend([{**copy.deepcopy(original), "seq": 9000}, {**copy.deepcopy(original), "seq": 9001}])
    view = build_visual_messages(messages, events=events, role="director", current_binding=_binding(2))
    assert _image("old-desktop").data_base64 not in _pixels(view)
    assert len(_pixels(view)) == 4


@pytest.mark.parametrize("identity", ["unavailable", "old-error"])
def test_unavailable_explicit_visual_pin_rejects_instead_of_silently_omitting(
    isolated_workspace: Path, identity: str
) -> None:
    """Catches a misspelled or nonvisual host pin silently permitting omission."""
    store, _command, _scope, _limits = _history(isolated_workspace)
    with pytest.raises(ValueError, match="pinned visual"):
        build_visual_messages(
            store.load_message_history("projection", role="director"),
            events=store.load_events("projection"),
            role="director",
            current_binding=_binding(2),
            pinned_call_ids=frozenset({identity}),
        )


@pytest.mark.parametrize(
    "case",
    [
        "no-authority",
        "current-incomplete",
        "old-incomplete",
        "no-message",
        "no-observation",
        "no-dispatch",
        "cross-role",
        "hash-mismatch",
        "result-mismatch",
        "ambiguous",
        "future-field",
        "pinned",
    ],
)
def test_unproven_or_explicitly_pinned_images_are_retained(isolated_workspace: Path, case: str) -> None:
    """Catches treating unknown provenance, a pin, or an incompatible binding as stale."""
    store, _command, _scope, _limits = _history(isolated_workspace)
    original = store.load_message_history("projection", role="director")
    events = copy.deepcopy(store.load_events("projection"))
    current = _binding(2)
    pins = frozenset({"old"}) if case == "pinned" else frozenset()
    old = next(e for e in events if e["kind"] == "visual_observation_completed")
    if case == "no-authority":
        events = []
    elif case == "current-incomplete":
        current.pop("profile")
    elif case == "old-incomplete":
        old["payload"]["input_binding"].pop("scope")
    elif case == "cross-role":
        old["role"] = "qa"
    elif case == "hash-mismatch":
        old["payload"]["image_sha256"] = [_image("other").sha256]
    elif case == "future-field":
        current["future_input"] = "unavailable-in-historical-binding"
    elif case == "ambiguous":
        events.append(copy.deepcopy(old))
    elif case == "result-mismatch":
        next(e for e in events if e["kind"] == "tool_observed")["payload"]["content"] = "different durable facts"
    elif case in {"no-message", "no-observation", "no-dispatch"}:
        kind = {"no-message": "message", "no-observation": "tool_observed", "no-dispatch": "tool_started"}[case]
        events = [
            e
            for e in events
            if not (
                e["kind"] == kind
                and (
                    e["payload"].get("call_id") == "old" or e["payload"].get("message", {}).get("tool_call_id") == "old"
                )
            )
        ]
    view = build_visual_messages(
        original, events=events, role="director", current_binding=current, pinned_call_ids=pins
    )
    assert _pixels(view) == [
        _image(label).data_base64
        for label in (
            "original-user-reference",
            "old-desktop",
            "old-mobile",
            "current-desktop",
            "current-mobile",
            "current-later-time",
        )
    ]
    assert original == store.load_message_history("projection", role="director")


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_revision", 9),
        ("required_checks", ["new-check"]),
        ("scope", {"different": True}),
        ("profile", {"different": True}),
        ("inputs", {"different": True}),
    ],
)
def test_every_rendered_binding_field_still_invalidates_old_pixels(
    isolated_workspace: Path, field: str, value: Any
) -> None:
    """Catches revision-only or messages-only freshness selection."""
    store, _command, _scope, _limits = _history(isolated_workspace)
    events = store.load_events("projection")
    old = next(e for e in events if e["kind"] == "visual_observation_completed")
    old["payload"]["input_binding"] = {**_binding(2), field: value}
    view = build_visual_messages(
        store.load_message_history("projection", role="director"),
        events=events,
        role="director",
        current_binding=_binding(2),
    )
    assert _image("old-desktop").data_base64 not in _pixels(view)
    assert len(_pixels(view)) == 4


def test_readonly_verification_sequence_alone_does_not_omit_pixels(isolated_workspace: Path) -> None:
    """Catches confusing a verification receipt with a changed rendered candidate."""
    store, _command, _scope, _limits = _history(isolated_workspace)
    events = store.load_events("projection")
    old = next(e for e in events if e["kind"] == "visual_observation_completed")
    old["payload"]["input_binding"] = {**_binding(2), "verification_seq": 9000}
    view = build_visual_messages(
        store.load_message_history("projection", role="director"),
        events=events,
        role="director",
        current_binding=_binding(2),
    )
    assert len(_pixels(view)) == 6


def test_omission_cannot_hide_corrupted_stale_bytes(isolated_workspace: Path) -> None:
    """Catches validating only retained bytes and silently hiding corrupt history."""
    store, _command, _scope, _limits = _history(isolated_workspace)
    original = store.load_message_history("projection", role="director")
    old = next(m for m in original if m.get("tool_call_id") == "old")
    payload = json.loads(old["content"])
    payload["images"][0]["data_base64"] = "invalid!"
    old["content"] = json.dumps(payload, ensure_ascii=False)
    with pytest.raises(ValueError, match="base64"):
        build_visual_messages(
            original, events=store.load_events("projection"), role="director", current_binding=_binding(2)
        )


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_projection_retains_complete_native_batches_and_existing_negative_guards(
    isolated_workspace: Path, protocol: str
) -> None:
    """Catches dropped IDs/results, early images inside a batch, and disabled protocol guards."""
    store, _command, _scope, _limits = _history(isolated_workspace)
    view = build_visual_messages(
        store.load_message_history("projection", role="director"),
        events=store.load_events("projection"),
        role="director",
        current_binding=_binding(2),
    )
    # The same unmodified K1 native-history budget still rejects mandatory content.
    with pytest.raises(ValueError, match="native_tool_history_budget_exceeded"):
        compress_chat_messages_to_budget(view, 1)
    assert compress_chat_messages_to_budget(view, 100000) == view
    if protocol == "openai":
        native = build_openai_native_messages(view)
        assert [m["tool_call_id"] for m in native if m["role"] == "tool"] == [
            "old",
            "old-error",
            "current-a",
            "current-a-error",
            "current-b",
            "current-b-error",
        ]
        uses = [call["id"] for m in native for call in m.get("tool_calls", [])]
    else:
        native, system = build_anthropic_native_messages(view)
        assert system == "You are Director."
        uses = [b["id"] for m in native for b in m["content"] if b["type"] == "tool_use"]
        for message in native:
            blocks = message["content"]
            results = [i for i, block in enumerate(blocks) if block["type"] == "tool_result"]
            images = [i for i, block in enumerate(blocks) if block["type"] == "image"]
            if results and images:
                assert max(results) < min(images)
    assert uses == ["old", "old-error", "current-a", "current-a-error", "current-b", "current-b-error"]
    if protocol == "openai":
        return  # This converter preserves pairs; it does not own history rejection.
    for case in ("orphan", "duplicate", "missing"):
        broken = copy.deepcopy(view)
        if case == "orphan":
            next(m for m in broken if m.get("tool_call_id") == "old")["tool_call_id"] = "unmatched"
        elif case == "missing":
            broken = [m for m in broken if m.get("tool_call_id") != "old"]
        else:
            index = next(i for i, m in enumerate(broken) if m.get("tool_calls"))
            broken.insert(index + 1, copy.deepcopy(broken[index]))
        with pytest.raises(ValueError, match="native_tool_history"):
            build_anthropic_native_messages(broken)


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("identity", ["same", ""])
def test_native_response_identity_guards_remain_before_dispatch(protocol: str, identity: str) -> None:
    """Catches weakening the actual response guard while changing request projection."""
    response = AIResponse(
        ok=True, raw=_native(("same", "browser_observe", {}), (identity, "read_file", {}), protocol=protocol)
    )
    with pytest.raises(ValueError, match="call id"):
        _native_calls(response)
