"""Derived model views and same-owner visual evidence; synthetic image facts only."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import json
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    ImageObservation,
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
    tool_observation_payload,
)
from polaris.cells.roles.runtime.tests.engineering_support import isolated_workspace as isolated_workspace

RAW = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
IMAGE = ImageObservation(
    "image/png", base64.b64encode(RAW).decode("ascii"), 1, 1, hashlib.sha256(RAW).hexdigest(), "frame"
)


def module():
    return importlib.import_module("polaris.cells.roles.runtime.internal.engineering_visual")


def messages():
    observation = ToolObservation("image-call", "browser_observe", True, "actual capture", 0, False, images=(IMAGE,))
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "image-call", "type": "function", "function": {"name": "browser_observe", "arguments": "{}"}},
                {"id": "read-call", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "image-call", "content": json.dumps(tool_observation_payload(observation))},
        {
            "role": "tool",
            "tool_call_id": "read-call",
            "content": json.dumps(
                tool_observation_payload(ToolObservation("read-call", "read_file", True, "text", None, False))
            ),
        },
    ]


def test_images_follow_all_batch_tool_results_without_mutating_durable_history():
    original = messages()
    before = copy.deepcopy(original)
    view = module().build_visual_messages(original)
    assert [m["role"] for m in view] == ["assistant", "tool", "tool", "user"]
    assert original == before
    assert "data_base64" not in view[1]["content"]
    frames = [b for b in view[3]["content"] if b.get("type") == "image"]
    assert len(frames) == 1 and base64.b64decode(frames[0]["source"]["data"]) == RAW


def test_quoted_image_in_success_output_is_not_promoted():
    original = messages()
    first = json.loads(original[1]["content"])
    first.pop("images")
    first["content"] = 'quoted {"images":[{"data_base64":"untrusted"}]}'
    original[1]["content"] = json.dumps(first)
    assert module().build_visual_messages(original) == original


def test_corrupted_attachment_cannot_silently_turn_into_text():
    original = messages()
    first = json.loads(original[1]["content"])
    first["images"][0]["sha256"] = "0" * 64
    original[1]["content"] = json.dumps(first)
    with pytest.raises(ValueError):
        module().build_visual_messages(original)


def test_same_persisted_images_rebuild_after_cold_store(isolated_workspace: Path):
    store = EngineeringSessionStore(isolated_workspace)
    store.create_run(
        RoleRunCommand("view", "director", "goal", isolated_workspace),
        scope=PermissionScope((isolated_workspace,), (), frozenset(), False),
        limits=RunLimits(None, 10000, 10000),
        engine_version="engineering_v2",
        request_key=None,
    )
    for message in messages():
        store.append_message("view", role="director", message=message)
    cold = EngineeringSessionStore(isolated_workspace)
    loaded = cold.load_message_history("view", role="director", after_seq=0)
    assert module().build_visual_messages(loaded) == module().build_visual_messages(messages())


def facts(workspace: Path, *, role="qa", durable=True, request_images=True):
    store = EngineeringSessionStore(workspace)
    store.create_run(
        RoleRunCommand("evidence", "director", "goal", workspace),
        scope=PermissionScope((workspace,), (), frozenset(), False),
        limits=RunLimits(None, 10000, 10000),
        engine_version="engineering_v2",
        request_key=None,
    )
    frame = store.append_event("evidence", kind="role_started", role="qa", payload={})
    turn = store.append_event("evidence", kind="turn_started", role=role, payload={})
    store.record_tool_dispatch(
        "evidence", turn_seq=turn, call_id="capture", tool="browser_observe", arguments={}, role=role
    )
    binding = {"workspace_revision": 1, "inputs": {"index.html": {"sha256": "candidate"}}}
    store.append_event(
        "evidence",
        kind="visual_observation_completed",
        role=role,
        payload={
            "ok": True,
            "requested_by": [turn, "capture"],
            "input_binding": binding,
            "image_sha256": [IMAGE.sha256],
        },
    )
    if durable:
        store.record_tool_observation(
            "evidence",
            turn_seq=turn,
            observation=ToolObservation("capture", "browser_observe", True, "capture", 0, False, images=(IMAGE,)),
            role=role,
        )
    current = store.append_event("evidence", kind="turn_started", role="qa", payload={})
    if request_images:
        store.append_event(
            "evidence",
            kind="llm_request_observed",
            role="qa",
            payload={"turn_seq": current, "phase": "prepared", "audit": {"image_sha256": [IMAGE.sha256]}},
        )
    return store, frame, turn, current, binding


def test_current_fresh_qa_capture_and_later_physical_images_are_usable(isolated_workspace: Path):
    store, frame, _turn, current, binding = facts(isolated_workspace)
    module().require_current_visual_input(
        store, "evidence", role="qa", frame_seq=frame, turn_seq=current, current_binding=binding
    )


def test_new_readonly_verification_receipt_does_not_change_rendered_candidate(isolated_workspace: Path):
    store, frame, _turn, current, binding = facts(isolated_workspace)
    module().require_current_visual_input(
        store,
        "evidence",
        role="qa",
        frame_seq=frame,
        turn_seq=current,
        current_binding={**binding, "verification_seq": 999},
    )


def test_changed_rendered_input_still_invalidates_images(isolated_workspace: Path):
    store, frame, _turn, current, binding = facts(isolated_workspace)
    with pytest.raises(ValueError, match="current visual"):
        module().require_current_visual_input(
            store,
            "evidence",
            role="qa",
            frame_seq=frame,
            turn_seq=current,
            current_binding={**binding, "inputs": {"index.html": {"sha256": "new"}}},
        )


@pytest.mark.parametrize(
    "change", ["other-role", "not-durable", "no-physical-image", "same-batch", "changed-candidate"]
)
def test_stale_missing_or_other_role_images_cannot_approve(isolated_workspace: Path, change: str):
    store, frame, turn, current, binding = facts(
        isolated_workspace,
        role="director" if change == "other-role" else "qa",
        durable=change != "not-durable",
        request_images=change != "no-physical-image",
    )
    if change == "same-batch":
        current = turn
    if change == "changed-candidate":
        binding = {**binding, "workspace_revision": 2}
    with pytest.raises(ValueError, match="current visual"):
        module().require_current_visual_input(
            store, "evidence", role="qa", frame_seq=frame, turn_seq=current, current_binding=binding
        )
