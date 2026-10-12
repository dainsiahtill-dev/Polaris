"""A failed tool observation must reach the next role request through ContextOS."""

import pytest
from polaris.cells.roles.kernel.internal.kernel.commit_protocol import _build_turn_history_and_events
from polaris.cells.roles.kernel.internal.kernel.role_result_projection import tool_results_from_batch_receipt
from polaris.cells.roles.profile.public.service import RoleTurnRequest


@pytest.mark.parametrize("payload", [None, {"file": "input.py"}])
def test_failed_tool_diagnostic_survives_projection_and_turn_history(payload: object) -> None:
    raw = {
        "call_id": "failed-read",
        "tool_name": "read_file",
        "status": "error",
        "result": payload,
        "error": "file_not_found: input.py",
        "arguments": {"file": "input.py"},
    }
    projected = tool_results_from_batch_receipt({"results": [raw]})
    assert projected[0].get("error") == raw["error"]
    history, events = _build_turn_history_and_events(
        turn_id="read-turn",
        request=RoleTurnRequest(message="Implement the scoped task"),
        visible_content="",
        thinking=None,
        tool_results=projected,
    )
    assert any(role == "tool" and raw["error"] in content for role, content in history)
    tool_event = next(event for event in events if event["role"] == "tool")
    assert raw["error"] in tool_event["content"]
    assert tool_event["status"] == "error"
    assert tool_event["arguments"] == {"file": "input.py"}
