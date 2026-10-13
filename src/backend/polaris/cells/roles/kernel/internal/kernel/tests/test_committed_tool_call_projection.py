"""Native history uses the sealed invocation, never receipt argument guesses."""

from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.role_result_projection import tool_calls_from_batch_receipt


def _outcome() -> dict[str, Any]:
    return {
        "execution": {
            "invocations": [{"call_id": "read-1", "tool_name": "read_file", "arguments": {"file": "answer.py"}}],
            "receipt": {
                "batch_id": "batch-1",
                "results": [{"call_id": "read-1", "tool_name": "read_file", "status": "success"}],
            },
        }
    }


def test_receipt_without_args_projects_exact_sealed_invocation() -> None:
    receipt: dict[str, Any] = {"batch_id": "batch-1", "results": [{"call_id": "read-1", "tool_name": "read_file", "status": "success"}]}
    assert tool_calls_from_batch_receipt(receipt, turn_outcome=_outcome()) == [
        {"tool": "read_file", "args": {"file": "answer.py"}, "call_id": "read-1"},
    ]


@pytest.mark.parametrize("field,value", [("call_id", "foreign"), ("tool_name", "write_file"), ("status", "error")])
def test_receipt_drift_cannot_change_sealed_native_history(field: str, value: str) -> None:
    receipt: dict[str, Any] = {"batch_id": "batch-1", "results": [{"call_id": "read-1", "tool_name": "read_file", "status": "success"}]}
    receipt["results"][0][field] = value
    with pytest.raises(RuntimeError, match="committed_tool_receipt_invocation_mismatch"):
        tool_calls_from_batch_receipt(receipt, turn_outcome=_outcome())


def test_receipt_without_args_or_sealed_invocation_cannot_guess_empty_args() -> None:
    receipt = {"results": [{"call_id": "read-1", "tool_name": "read_file"}]}
    with pytest.raises(RuntimeError, match="committed_tool_receipt_arguments_missing"):
        tool_calls_from_batch_receipt(receipt)
