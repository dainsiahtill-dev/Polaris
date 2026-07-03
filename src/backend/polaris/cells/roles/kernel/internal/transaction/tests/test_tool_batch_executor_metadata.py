from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    _metadata_native_tool_call_count,
)


def test_metadata_native_tool_call_count_accepts_lifecycle_envelope_refs() -> None:
    metadata = {
        "native_tool_call_envelope_refs": (
            {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
            {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
        ),
        "native_tool_calls_count": 1,
    }

    assert _metadata_native_tool_call_count(metadata, fallback=0) == 2


def test_metadata_native_tool_call_count_accepts_lifecycle_receipt_envelope_refs() -> None:
    metadata = {
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_call_envelope_refs": (
                {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
                {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
            ),
        },
        "native_tool_calls_count": 1,
    }

    assert _metadata_native_tool_call_count(metadata, fallback=0) == 2


def test_metadata_native_tool_call_count_keeps_numeric_fallback_without_envelopes() -> None:
    assert _metadata_native_tool_call_count({"native_tool_calls_count": 3}, fallback=1) == 3
    assert _metadata_native_tool_call_count({}, fallback=2) == 2
