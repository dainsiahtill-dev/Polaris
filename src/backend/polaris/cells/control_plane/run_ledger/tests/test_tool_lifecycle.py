from __future__ import annotations

from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import (
    build_tool_call_lifecycle_receipt,
)


def test_tool_lifecycle_receipt_links_batch_and_effect_refs() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-response-hash",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "write_file",
                        "status": "success",
                        "effect_receipt": {
                            "operation": "write_file:create",
                            "file": "src/index.js",
                            "before_hash": "",
                            "after_hash": "after",
                        },
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["ok"] is True
    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["provider_response_hash"] == "provider-response-hash"
    assert receipt["batch_receipt_hash"]
    assert receipt["batch_receipt_refs"][0]["batch_id"] == "batch-1"
    assert receipt["effect_receipt_count"] == 1
    assert receipt["effect_receipt_refs"][0]["file"] == "src/index.js"
    assert receipt["effect_receipt_refs"][0]["tool_name"] == "write_file"


def test_tool_lifecycle_receipt_detects_missing_batch_receipt() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "blocked"
    assert receipt["failure_class"] == "MISSING_BATCH_RECEIPT"
