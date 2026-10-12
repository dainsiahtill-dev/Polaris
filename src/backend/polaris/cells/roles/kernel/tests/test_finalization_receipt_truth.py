"""Finalization must describe tool observations without inventing delivery."""

from types import SimpleNamespace

import pytest
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.finalization import FinalizationHandler


@pytest.mark.parametrize("write_status", ["failed", "success"])
def test_materialization_finalization_does_not_claim_delivery_after_failed_verifier(write_status: str) -> None:
    receipts = [
        {
            "results": [
                {
                    "tool_name": "edit_file",
                    "status": write_status,
                    "result": {"file": "src/app.py", "error": "stale content"}
                    if write_status == "failed"
                    else {
                        "file": "src/app.py",
                        "success": True,
                    },
                },
                {
                    "tool_name": "execute_command",
                    "status": "failed",
                    "result": {"exit_code": 1, "stderr": "AssertionError: actual behavior differs"},
                },
            ],
        },
    ]
    original = [
        {"role": "system", "content": "<role_definition>Director</role_definition>"},
        {"role": "user", "content": "Implement and verify the task"},
    ]
    messages = FinalizationHandler._build_finalization_context(
        original,
        receipts,
        SimpleNamespace(mode=DeliveryMode.MATERIALIZE_CHANGES),
    )
    content = messages[-1]["content"]
    assert "AssertionError: actual behavior differs" in content
    assert "execute_command (Error)" in content
    assert "请确认修改已完成" not in content
    assert messages[0] == original[0]
    if write_status == "failed":
        assert "edit_file (Error)" in content
        assert "stale content" in content


def test_successful_write_does_not_by_itself_prove_task_completion() -> None:
    messages = FinalizationHandler._build_finalization_context(
        [{"role": "user", "content": "Implement and run the behavior tests"}],
        [{"results": [{"tool_name": "write_file", "status": "success", "result": {"file": "src/app.py"}}]}],
        SimpleNamespace(mode=DeliveryMode.MATERIALIZE_CHANGES),
    )
    assert "src/app.py" in messages[-1]["content"]
    assert "请确认修改已完成" not in messages[-1]["content"]
