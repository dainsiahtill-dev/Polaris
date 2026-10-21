"""Engineering coverage reads actual native body; it never repairs that body."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.events.final_request_evidence import redact_provider_transport
from polaris.kernelone.llm.engine.contracts import AIRequest, ModelSpec, TaskType
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.native_request_capture import NativeRequestCapturePort
from polaris.kernelone.llm.engine.provider_native_request import (
    build_anthropic_native_messages,
    build_openai_native_messages,
)
from polaris.kernelone.storage.io_paths import resolve_storage_roots


def _messages(goal: str, result: object = None) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are Director."},
        {"role": "user", "content": goal},
    ]
    if result is not None:
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"file":"one.txt"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": json.dumps(result, ensure_ascii=False)},
            ]
        )
    return messages


def _observation(ok: bool, content: str = "observed") -> dict[str, Any]:
    return {"call_id": "call-1", "tool": "read_file", "ok": ok, "content": content}


def _body(protocol: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    if protocol == "anthropic":
        native, system = build_anthropic_native_messages(messages)
        return {"model": "fixture", "system": system, "messages": native, "max_tokens": 100}
    return {"model": "fixture", "messages": build_openai_native_messages(messages), "max_tokens": 100}


def _capture(
    tmp_path: Path,
    protocol: str,
    goal: str,
    messages: list[dict[str, Any]],
    *,
    body: dict[str, Any] | None = None,
    expected_feedback: bool = False,
    role: str = "director",
    engine_version: str = "engineering_v2",
) -> dict[str, Any]:
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role=role,
        input=goal,
        context={"engine_version": engine_version, "call_id": "coverage", "failure_feedback": expected_feedback},
    )
    port = NativeRequestCapturePort(
        request=request,
        model_spec=ModelSpec(
            provider_id="fixture",
            provider_type="anthropic_compat" if protocol == "anthropic" else "openai_compat",
            model="fixture",
            max_context_tokens=4096,
            max_output_tokens=100,
            supports_tools=True,
        ),
        trace_id="coverage-test",
        messages=messages,
        provider_request={"role": role, "final_request_context_audit": {"coverage": {"legacy": True}}},
        persist=AIExecutor._store_context_messages_sync,
        workspace=str(tmp_path),
    )
    wire = {
        "endpoint": "https://fixture.invalid/messages",
        "body": body if body is not None else _body(protocol, messages),
    }
    before = copy.deepcopy(wire)
    sent: list[dict[str, Any]] = []
    port.dispatch_sync(wire_request=wire, send=lambda actual: sent.append(redact_provider_transport(actual)))
    assert wire == before and sent == [before], "coverage changed the physical request"
    evidence = port.final_context_evidence()
    assert evidence is not None
    ref = evidence[0]
    runtime = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
    snapshot = json.loads((runtime / "contexts" / ref[:2] / ref).read_text(encoding="utf-8"))
    assert snapshot["provider_request"]["final_physical_request"]["body"] == before["body"]
    return request.context["final_request_context_audit"]["coverage"]


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("role", ["director", "pm", "chief_engineer", "qa"])
def test_goal_with_json_escapes_is_found_in_actual_user_text(tmp_path: Path, protocol: str, role: str) -> None:
    goal = 'Create 鹈鹕\nUse "offline" and C:\\project.'
    assert _capture(tmp_path, protocol, goal, _messages(goal), role=role)["original_goal"] == "present"


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("location", ["assistant", "tool", "system", "absent"])
def test_goal_outside_actual_user_text_is_unknown(tmp_path: Path, protocol: str, location: str) -> None:
    goal = "The original goal."
    messages = _messages("A different user request.")
    if location == "tool":
        messages = _messages("A different user request.", _observation(True, goal))
    elif location != "absent":
        messages.append({"role": location, "content": goal})
    # The native converter may deliberately turn a supplemental system message
    # into user text. Use the physical role under test directly instead.
    body = _body(protocol, messages)
    if location == "system":
        body = _body(protocol, _messages("A different user request."))
        if protocol == "anthropic":
            body["system"] = goal
        else:
            body["messages"][0]["content"] = goal
    assert _capture(tmp_path, protocol, goal, messages, body=body)["original_goal"] == "unknown"


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_actual_paired_negative_tool_feedback_is_present(tmp_path: Path, protocol: str) -> None:
    coverage = _capture(tmp_path, protocol, "Work.", _messages("Work.", _observation(False, "file missing")))
    assert coverage["failure_feedback"] == "present"


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("result", [None, _observation(True, 'Example: {"ok": false}'), _observation(True)])
def test_no_failed_observation_is_not_applicable(tmp_path: Path, protocol: str, result: object) -> None:
    assert _capture(tmp_path, protocol, "Work.", _messages("Work.", result))["failure_feedback"] == "not_applicable"


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize(
    "result",
    [
        "opaque failed output",
        {"ok": False},
        {"call_id": "other", "tool": "read_file", "ok": False, "content": "error"},
        {"call_id": "call-1", "tool": "write_file", "ok": False, "content": "error"},
        {"call_id": "call-1", "tool": "read_file", "ok": 0, "content": "error"},
    ],
)
def test_unproven_tool_observation_stays_unknown(tmp_path: Path, protocol: str, result: object) -> None:
    assert _capture(tmp_path, protocol, "Work.", _messages("Work.", result))["failure_feedback"] == "unknown"


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_missing_expected_feedback_stays_unknown(tmp_path: Path, protocol: str) -> None:
    assert (
        _capture(tmp_path, protocol, "Work.", _messages("Work."), expected_feedback=True)["failure_feedback"]
        == "unknown"
    )


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_source_messages_cannot_prove_failure_removed_from_actual_body(tmp_path: Path, protocol: str) -> None:
    messages = _messages("Work.", _observation(False))
    body = _body(protocol, _messages("Work."))
    assert (
        _capture(tmp_path, protocol, "Work.", messages, body=body, expected_feedback=True)["failure_feedback"]
        == "unknown"
    )


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_other_engine_keeps_legacy_coverage(tmp_path: Path, protocol: str) -> None:
    assert _capture(tmp_path, protocol, "Work.", _messages("Work."), engine_version="legacy") == {"legacy": True}


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("change", ["orphan", "duplicate_call", "duplicate_json", "contradictory_error"])
def test_ambiguous_physical_tool_feedback_stays_unknown(tmp_path: Path, protocol: str, change: str) -> None:
    messages = _messages("Work.", _observation(False))
    body = _body(protocol, messages)
    if protocol == "openai":
        assistant, result = body["messages"][-2:]
        if change == "orphan":
            result["tool_call_id"] = "orphan"
        elif change == "duplicate_call":
            assistant["tool_calls"].append(copy.deepcopy(assistant["tool_calls"][0]))
        elif change == "duplicate_json":
            result["content"] = '{"call_id":"call-1","tool":"read_file","content":"error","ok":true,"ok":false}'
        else:
            result["content"] = json.dumps(_observation(True))
            # OpenAI has no standardized is_error field in this native projection;
            # an opaque status is insufficient to prove a failure.
            result["content"] = json.dumps({"error": "unknown status", "detail": result["content"]})
    else:
        assistant, result = body["messages"][-2:]
        block = result["content"][0]
        if change == "orphan":
            block["tool_use_id"] = "orphan"
        elif change == "duplicate_call":
            assistant["content"].append(copy.deepcopy(assistant["content"][0]))
        elif change == "duplicate_json":
            block["content"] = '{"call_id":"call-1","tool":"read_file","content":"error","ok":true,"ok":false}'
        else:
            block["content"] = json.dumps(_observation(True))
            block["is_error"] = True
    assert _capture(tmp_path, protocol, "Work.", messages, body=body)["failure_feedback"] == "unknown"


def test_unsupported_native_protocol_is_unknown(tmp_path: Path) -> None:
    body = {"model": "fixture", "input": [{"role": "user", "content": "Work."}]}
    coverage = _capture(tmp_path, "openai", "Work.", _messages("Work."), body=body)
    assert coverage["original_goal"] == coverage["failure_feedback"] == "unknown"


def test_malformed_native_tool_list_is_unknown(tmp_path: Path) -> None:
    messages = _messages("Work.")
    body = _body("openai", messages)
    body["messages"].append({"role": "assistant", "content": "", "tool_calls": 7})
    assert _capture(tmp_path, "openai", "Work.", messages, body=body)["failure_feedback"] == "unknown"


@pytest.mark.parametrize("is_error", ["true", "false", None, 1, 0, {}, [], True, False])
@pytest.mark.parametrize("ok", [True, False])
def test_anthropic_optional_error_flag_requires_boolean_type(tmp_path: Path, is_error: object, ok: bool) -> None:
    messages = _messages("Work.", _observation(ok))
    body = _body("anthropic", messages)
    body["messages"][-1]["content"][0]["is_error"] = is_error
    expected = "present" if not ok else ("not_applicable" if is_error is False else "unknown")
    assert _capture(tmp_path, "anthropic", "Work.", messages, body=body)["failure_feedback"] == expected
