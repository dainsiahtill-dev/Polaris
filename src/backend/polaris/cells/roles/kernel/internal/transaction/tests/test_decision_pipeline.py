from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.transaction.decision_pipeline import (
    _native_tool_call_count,
    _provider_response_hash,
)


def test_native_tool_call_count_prefers_metadata_envelopes() -> None:
    response = SimpleNamespace(content="", model="gpt-test", native_tool_calls=[])
    metadata = {
        "native_tool_call_envelopes": [
            {"envelope_id": "tool-envelope-1"},
            {"envelope_id": "tool-envelope-2"},
        ],
    }

    assert _native_tool_call_count(response, metadata) == 2


def test_native_tool_call_count_falls_back_to_raw_calls() -> None:
    response = SimpleNamespace(
        content="",
        model="gpt-test",
        native_tool_calls=[{"function": {"name": "write_file"}}],
    )

    assert _native_tool_call_count(response, {}) == 1


def test_provider_response_hash_includes_metadata_envelopes() -> None:
    response = SimpleNamespace(content="", model="gpt-test", native_tool_calls=[])

    without_envelope = _provider_response_hash(response, {})
    with_envelope = _provider_response_hash(
        response,
        {"native_tool_call_envelopes": [{"envelope_id": "tool-envelope-1"}]},
    )

    assert with_envelope != without_envelope
