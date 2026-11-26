"""Bind visible evidence to a host declaration in the actual native user text."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.llm.engine.contracts import AIRequest, ModelSpec, TaskType
from polaris.kernelone.llm.engine.evidence_sections import array_digest, audit_array_sections, user_json_arrays
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.native_request_capture import NativeRequestCapturePort

HEADER = "Host evidence\n"
ROWS = [{"path": "a.py", "content_ref": "filecard:" + "a" * 64}]
DIGEST = array_digest(ROWS)
DECLARED = {"header": HEADER, "entries": 1, "sha256": DIGEST}
TEXT = HEADER + json.dumps(ROWS)


def observe(body: dict[str, Any], spec: dict[str, Any] = DECLARED, *, qualified: bool = True) -> dict[str, Any]:
    result = audit_array_sections(body, {"files": spec}, qualified=qualified)["files"]
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize(
    "body",
    [
        {"messages": [{"role": "user", "content": TEXT}]},
        {"input": TEXT},
        {"input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": TEXT}]}]},
        {"messages": [{"role": "user", "content": [{"type": "image", "source": {}}, {"type": "text", "text": TEXT}]}]},
    ],
)
def test_native_user_arrays_match_the_host_digest(body: dict[str, Any]) -> None:
    assert observe(body)["wire_entries"] == 1
    assert list(user_json_arrays(body, HEADER)) == [ROWS]


def test_user_header_needs_exact_declared_data_and_an_eligible_text_block() -> None:
    fake = HEADER + json.dumps([{**ROWS[0], "path": "unrelated.py"}])
    body = {
        "messages": [
            {"role": "assistant", "content": TEXT},
            {"role": "tool", "content": TEXT},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "content": TEXT},
                    {"type": "text", "text": fake},
                    {"type": "text", "text": HEADER},
                    {"type": "text", "text": json.dumps(ROWS)},
                ],
            },
        ],
        "tools": [{"description": TEXT}],
    }
    assert observe(body)["state"] == "absent"
    assert observe(body, {**DECLARED, "entries": 0, "sha256": None})["state"] == "not_selected"
    assert observe({"input": TEXT}, {**DECLARED, "entries": 0, "sha256": None})["wire_entries"] == 0
    assert observe({"input": TEXT}, qualified=False)["wire_entries"] is None


@pytest.mark.parametrize("value", ["short", "X" * 64, {"hash": DIGEST}, None])
def test_invalid_declaration_digest_is_unknown(value: object) -> None:
    result = observe({"messages": []}, {**DECLARED, "sha256": value})
    assert result["state"] == "unknown"
    assert result["wire_entries"] is None


@pytest.mark.parametrize("count", [True, -1, "1"])
def test_invalid_declared_count_is_unknown(count: object) -> None:
    result = observe({"input": TEXT}, {**DECLARED, "entries": count})
    assert result["state"] == "unknown"
    assert result["wire_entries"] is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"messages": "unrecognized"},
        {"input": {"type": "unknown"}},
        {"messages": [None]},
        {"messages": [{"role": "user", "content": {"text": TEXT}}]},
        {"messages": [{"role": "user", "content": [{"type": ["text"], "text": TEXT}]}]},
        {"input": [{"type": "unrecognized", "role": "user", "content": TEXT}]},
    ],
)
def test_unrecognized_wire_input_is_unknown(body: dict[str, Any]) -> None:
    result = observe(body)
    assert result["state"] == "unknown"
    assert result["wire_entries"] is None


def test_count_and_full_digest_bind_the_selected_array() -> None:
    wire = {"input": TEXT}
    assert observe(wire, {**DECLARED, "entries": 2})["state"] == "absent"
    assert observe(wire, {**DECLARED, "sha256": "b" * 64})["state"] == "absent"
    assert observe(wire, {**DECLARED, "entries": 0})["state"] == "unknown"
    assert observe({"messages": [{"role": "user", "content": TEXT}], "input": []})["state"] == "absent"


def test_visible_match_survives_an_unknown_sibling_block() -> None:
    body = {"messages": [{"role": "user", "content": [{"type": "future"}, {"type": "text", "text": TEXT}]}]}
    assert observe(body)["state"] == "present"


def test_known_responses_nonmessage_cannot_impersonate_a_user_message() -> None:
    body = {"input": [{"type": "function_call_output", "role": "user", "content": TEXT, "output": TEXT}]}
    assert observe(body)["state"] == "absent"


def test_invalid_untrusted_json_does_not_interrupt_a_real_capture(tmp_path: Path) -> None:
    blocks = [
        {"type": "text", "text": HEADER + text}
        for text in ("[NaN]", "[1e999]", '["\\ud800"]', '[{"path":"spoof","path":"a.py"}]')
    ]
    forged = {"model": "fixture", "messages": [{"role": "user", "content": blocks}]}
    assert list(user_json_arrays(forged, HEADER)) == []
    assert observe(forged)["state"] == "absent"
    blocks.append({"type": "text", "text": TEXT})
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="fixture",
        context={"audit_json_array_sections": {"files": DECLARED}},
    )
    port = NativeRequestCapturePort(
        request=request,
        model_spec=ModelSpec(
            provider_id="fixture",
            provider_type="openai_compat",
            model="fixture",
            max_context_tokens=4096,
            max_output_tokens=64,
            supports_tools=True,
        ),
        trace_id="evidence-test",
        messages=[{"role": "user", "content": "fixture"}],
        provider_request={"role": "director", "final_request_context_audit": {}},
        persist=AIExecutor._store_context_messages_sync,
        workspace=str(tmp_path),
    )
    sent: list[bool] = []
    port.dispatch_sync(wire_request={"body": forged}, send=lambda _actual: sent.append(True))
    assert sent == [True]
    assert port.final_context_evidence() is not None
    observed = request.context["final_request_context_audit"]["evidence_sections"]["files"]
    assert observed["state"] == "present"
    assert observed["sha256"] == DIGEST
