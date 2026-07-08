from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.finalization import (
    _display_error_for_result,
)


def test_error_display_uses_only_matching_raw_result_text() -> None:
    result = {
        "call_id": "call-write-1",
        "tool_name": "write_file",
        "status": "failed",
        "result": {},
    }
    receipt = {
        "results": [result],
        "raw_results": [
            {
                "call_id": "call-write-1",
                "tool_name": "read_file",
                "error": "wrong tool error",
            },
            {
                "call_id": "call-write-1",
                "tool_name": "write_file",
                "error": "matching write error",
            },
        ],
    }

    assert _display_error_for_result(result, receipt) == "matching write error"


def test_error_display_does_not_consume_raw_effect_receipts() -> None:
    result = {
        "call_id": "call-write-1",
        "tool_name": "write_file",
        "status": "failed",
        "result": {},
    }
    receipt = {
        "results": [result],
        "raw_results": [
            {
                "call_id": "call-write-1",
                "tool_name": "write_file",
                "result": {
                    "effect_receipt": {
                        "operation": "write_file",
                        "path": "src/index.py",
                        "status": "success",
                    }
                },
            }
        ],
    }

    assert _display_error_for_result(result, receipt) == "unknown"
