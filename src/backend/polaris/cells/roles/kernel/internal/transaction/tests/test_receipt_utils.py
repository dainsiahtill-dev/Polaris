from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.receipt_utils import (
    merge_batch_receipts,
    normalize_batch_receipt,
)


def test_merge_batch_receipts_preserves_top_level_effect_receipts() -> None:
    merged = merge_batch_receipts(
        [
            {
                "batch_id": "batch-1",
                "turn_id": "turn-1",
                "results": [{"tool_name": "write_file", "status": "success"}],
                "raw_results": [{"tool_name": "write_file", "status": "success"}],
                "effect_receipts": [{"effect_id": "effect-1", "status": "success"}],
                "success_count": 1,
            },
            {
                "batch_id": "batch-2",
                "turn_id": "turn-1",
                "results": [{"tool_name": "execute_command", "status": "success"}],
                "effect_receipts": [{"effect_id": "effect-2", "status": "success"}],
                "success_count": 1,
            },
        ]
    )

    assert merged is not None
    assert merged["effect_receipts"] == [
        {"effect_id": "effect-1", "status": "success"},
        {"effect_id": "effect-2", "status": "success"},
    ]
    assert [row["tool_name"] for row in merged["results"]] == [
        "write_file",
        "execute_command",
    ]
    assert "effect_receipt" not in merged["results"][0]


def test_merge_batch_receipts_without_effect_receipts_keeps_result_shape() -> None:
    merged = merge_batch_receipts(
        [
            {
                "batch_id": "batch-1",
                "turn_id": "turn-1",
                "results": [{"tool_name": "read_file", "status": "success"}],
                "raw_results": [{"tool_name": "read_file", "status": "success"}],
                "success_count": 1,
                "failure_count": 0,
            },
            {
                "batch_id": "batch-2",
                "turn_id": "turn-1",
                "results": [{"tool_name": "write_file", "status": "failed"}],
                "raw_results": [{"tool_name": "write_file", "status": "failed"}],
                "success_count": 0,
                "failure_count": 1,
            },
        ]
    )

    assert merged == {
        "batch_id": "batch-1",
        "turn_id": "turn-1",
        "results": [
            {"tool_name": "read_file", "status": "success"},
            {"tool_name": "write_file", "status": "failed"},
        ],
        "raw_results": [
            {"tool_name": "read_file", "status": "success"},
            {"tool_name": "write_file", "status": "failed"},
        ],
        "effect_receipts": [],
        "success_count": 1,
        "failure_count": 1,
        "pending_async_count": 0,
        "has_pending_async": False,
    }


def test_normalize_batch_receipt_filters_invalid_effect_receipt_shapes() -> None:
    normalized = normalize_batch_receipt(
        {
            "batch_id": "batch-1",
            "turn_id": "turn-1",
            "effect_receipts": [
                {"effect_id": "effect-1", "status": "success"},
                "not-a-receipt",
                {},
                object(),
            ],
        }
    )

    assert normalized["effect_receipts"] == [{"effect_id": "effect-1", "status": "success"}]
    assert normalize_batch_receipt({"effect_receipts": {"bad": "shape"}})["effect_receipts"] == []
