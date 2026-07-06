"""Tests for the role-kernel tool-call envelope facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from polaris.cells.roles.kernel.internal import tool_call_envelope
from polaris.cells.roles.kernel.internal.llm_caller import tool_helpers
from polaris.cells.roles.kernel.internal.turn_engine.utils import normalize_stream_tool_call_payload


@dataclass
class _Response:
    native_tool_calls: list[dict[str, Any]]


def test_tool_helpers_reexport_canonical_facade_functions() -> None:
    assert tool_helpers.native_tool_calls_from_response is tool_call_envelope.native_tool_calls_from_response
    assert (
        tool_helpers.native_tool_call_envelopes_from_response
        is tool_call_envelope.native_tool_call_envelopes_from_response
    )
    assert tool_helpers.native_tool_call_name is tool_call_envelope.native_tool_call_name


def test_facade_prefers_metadata_envelopes_over_raw_response_calls() -> None:
    response = _Response(
        native_tool_calls=[
            {
                "id": "call-raw",
                "type": "function",
                "function": {"name": "write_file", "arguments": {"file": "a.py"}},
            }
        ]
    )
    metadata = {
        "native_tool_call_envelopes": [
            {
                "envelope_id": "env-metadata",
                "tool_name": "read_file",
                "call_id": "call-metadata",
            }
        ]
    }

    assert tool_call_envelope.native_tool_call_envelopes_from_response(response, metadata) == [
        {
            "envelope_id": "env-metadata",
            "tool_name": "read_file",
            "call_id": "call-metadata",
        }
    ]


def test_facade_wraps_raw_response_calls_when_metadata_has_no_envelopes() -> None:
    response = _Response(
        native_tool_calls=[
            {
                "id": "call-raw",
                "type": "function",
                "function": {"name": "write_file", "arguments": {"file": "a.py"}},
            }
        ]
    )

    envelopes = tool_call_envelope.native_tool_call_envelopes_from_response(
        response,
        {"tool_call_provider": "openai"},
    )

    assert len(envelopes) == 1
    assert envelopes[0]["tool_name"] == "write_file"
    assert envelopes[0]["call_id"] == "call-raw"
    assert envelopes[0]["provider"] == "openai"


def test_turn_engine_stream_wrapper_uses_facade_contract() -> None:
    payload, provider = normalize_stream_tool_call_payload(
        tool_name="write_file",
        tool_args={"file": "a.py"},
        call_id="call-stream",
        metadata={},
    )

    assert provider == "openai"
    assert payload == {
        "id": "call-stream",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": {"file": "a.py"},
        },
    }
