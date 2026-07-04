from __future__ import annotations

from polaris.cells.control_plane.run_ledger.public import (
    native_tool_call_count_from_metadata,
    native_tool_call_envelope_refs_from_metadata,
)
from polaris.cells.roles.kernel.internal.transaction.tool_call_audit_refs import tool_invocation_audit_ref
from polaris.cells.roles.kernel.public.turn_contracts import ToolExecutionMode


def test_metadata_native_tool_call_count_accepts_lifecycle_envelope_refs() -> None:
    metadata = {
        "native_tool_call_envelope_refs": (
            {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
            {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
        ),
        "native_tool_calls_count": 1,
    }

    assert native_tool_call_count_from_metadata(metadata, fallback=0) == 2
    assert [item["tool_name"] for item in native_tool_call_envelope_refs_from_metadata(metadata)] == [
        "write_file",
        "execute_command",
    ]


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

    assert native_tool_call_count_from_metadata(metadata, fallback=0) == 2
    assert [item["tool_name"] for item in native_tool_call_envelope_refs_from_metadata(metadata)] == [
        "write_file",
        "execute_command",
    ]


def test_metadata_native_tool_call_count_keeps_numeric_fallback_without_envelopes() -> None:
    assert native_tool_call_count_from_metadata({"native_tool_calls_count": 3}, fallback=1) == 3
    assert native_tool_call_count_from_metadata({}, fallback=2) == 2


def test_metadata_native_tool_call_envelopes_deduplicates_aliases() -> None:
    metadata = {
        "native_tool_call_envelopes": ["bad legacy projection"],
        "native_tool_call_envelope_refs": [
            {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
            {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
            {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
        ],
    }

    assert [item["tool_name"] for item in native_tool_call_envelope_refs_from_metadata(metadata)] == [
        "write_file",
        "execute_command",
    ]


def test_tool_invocation_audit_ref_preserves_decoded_invocation_evidence() -> None:
    invocation = {
        "call_id": "call-1",
        "tool_name": "write_file",
        "execution_mode": ToolExecutionMode.WRITE_SERIAL,
        "arguments": {"file": "src/main.py"},
    }

    assert tool_invocation_audit_ref(
        invocation,
        reason="decoded_tool_batch_without_authoritative_receipt",
    ) == {
        "reason": "decoded_tool_batch_without_authoritative_receipt",
        "tool_name": "write_file",
        "call_id": "call-1",
        "execution_mode": "write_serial",
        "target_file": "src/main.py",
    }


def test_tool_invocation_audit_ref_accepts_provider_native_call_shape() -> None:
    invocation = {
        "id": "call-native",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": '{"path": "src/generated.py", "content": "print(1)"}',
        },
    }

    assert tool_invocation_audit_ref(
        invocation,
        reason="finalization_tool_calls_blocked",
    ) == {
        "reason": "finalization_tool_calls_blocked",
        "tool_name": "write_file",
        "call_id": "call-native",
        "target_file": "src/generated.py",
    }
