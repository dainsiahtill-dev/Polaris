from __future__ import annotations

from polaris.cells.control_plane.run_ledger.public import (
    native_tool_call_count_from_metadata,
    native_tool_call_envelope_refs_from_metadata,
)
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    _batch_has_authoritative_success,
    _effect_receipts_from_batch_receipts,
)
from polaris.cells.roles.kernel.internal.transaction.tool_call_audit_refs import tool_invocation_audit_ref
from polaris.cells.roles.kernel.public.turn_contracts import ToolExecutionMode


def test_effect_receipts_from_batch_receipts_accepts_top_level_effect_receipts() -> None:
    original = {"operation": "write", "file": "src/generated.py"}

    receipts = _effect_receipts_from_batch_receipts(
        [
            {
                "batch_id": "batch-1",
                "turn_id": "turn-1",
                "effect_receipts": [
                    original,
                    "invalid",
                    None,
                    ["invalid"],
                ],
            }
        ]
    )

    assert receipts == [{"operation": "write", "file": "src/generated.py"}]
    assert receipts[0] is not original


def test_effect_receipts_from_batch_receipts_keeps_direct_and_nested_receipts() -> None:
    result_direct = {"source": "results-direct"}
    result_nested = {"source": "results-nested"}
    raw_direct = {"source": "raw-results-direct"}
    raw_nested = {"source": "raw-results-nested"}

    receipts = _effect_receipts_from_batch_receipts(
        [
            {
                "batch_id": "batch-1",
                "turn_id": "turn-1",
                "results": [
                    {"effect_receipt": result_direct},
                    {"result": {"effect_receipt": result_nested}},
                ],
                "raw_results": [
                    {"effect_receipt": raw_direct},
                    {"result": {"effect_receipt": raw_nested}},
                ],
            }
        ]
    )

    assert receipts == [
        {"source": "results-direct"},
        {"source": "results-nested"},
        {"source": "raw-results-direct"},
        {"source": "raw-results-nested"},
    ]
    assert receipts[0] is not result_direct
    assert receipts[1] is not result_nested
    assert receipts[2] is not raw_direct
    assert receipts[3] is not raw_nested


def test_effect_receipts_from_batch_receipts_filters_invalid_shapes() -> None:
    receipts = _effect_receipts_from_batch_receipts(
        [
            None,
            "invalid",
            {
                "batch_id": "batch-1",
                "turn_id": "turn-1",
                "effect_receipts": {"invalid": "shape"},
                "results": {"invalid": "shape"},
                "raw_results": [
                    None,
                    "invalid",
                    {"effect_receipt": "invalid"},
                    {"result": "invalid"},
                    {"result": {"effect_receipt": ["invalid"]}},
                ],
            },
        ]
    )

    assert receipts == []


def test_effect_receipts_from_batch_receipts_copies_reused_dict_objects() -> None:
    shared_receipt = {"operation": "write", "file": "src/shared.py"}

    receipts = _effect_receipts_from_batch_receipts(
        [
            {
                "batch_id": "batch-1",
                "turn_id": "turn-1",
                "effect_receipts": [shared_receipt],
                "results": [{"effect_receipt": shared_receipt}],
                "raw_results": [{"result": {"effect_receipt": shared_receipt}}],
            }
        ]
    )

    assert receipts == [shared_receipt, shared_receipt, shared_receipt]
    assert all(receipt is not shared_receipt for receipt in receipts)
    assert len({id(receipt) for receipt in receipts}) == len(receipts)


def test_batch_authoritative_success_requires_success_pending_or_effect_receipt() -> None:
    all_failed_receipts = [
        {
            "results": [
                {
                    "tool_name": "write_file",
                    "status": "error",
                    "error": "director_tool_execution_cancelled: session_not_active",
                }
            ],
            "raw_results": [
                {
                    "tool_name": "write_file",
                    "status": "error",
                    "error": "director_tool_execution_cancelled: session_not_active",
                }
            ],
            "effect_receipts": [],
            "pending_async_count": 0,
            "has_pending_async": False,
        }
    ]

    assert _batch_has_authoritative_success(all_failed_receipts) is False
    assert _batch_has_authoritative_success([{"results": [{"status": "success"}]}]) is True
    assert _batch_has_authoritative_success([{"effect_receipts": [{"file": "src/app.ts"}]}]) is True
    assert _batch_has_authoritative_success([{"pending_async_count": 1}]) is True


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
