"""Final decision-message truth must match the physical Provider tool schema."""

from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.transaction.decision_message_builder import (
    build_decision_messages,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode


def test_decision_prompt_projects_exact_current_turn_tool_schema() -> None:
    context = [
        {
            "role": "system",
            "content": "Allowed tools: read_file, write_file, execute_command",
        },
        {
            "role": "user",
            "content": "Create main.go and verify that the project builds successfully.",
        },
    ]
    tools = [{"type": "function", "function": {"name": "write_file"}}]

    messages = build_decision_messages(context, tools)
    rendered = "\n".join(str(message.get("content") or "") for message in messages)

    assert "CURRENT TURN PHYSICAL TOOL SCHEMA: write_file" in rendered
    assert "supersedes broader role-capability lists" in rendered
    assert "Tools not listed here are unavailable in this Provider request" in rendered
    assert "Include an available verification step" not in rendered
    assert "No verification tool is exposed in this physical request" in rendered


def test_materialize_prompt_does_not_reinvent_unavailable_verification_tool() -> None:
    context = [
        {
            "role": "user",
            "content": "Create main.go and verify that the project builds successfully.",
        }
    ]
    tools = [{"type": "function", "function": {"name": "write_file"}}]
    ledger = SimpleNamespace(delivery_contract=SimpleNamespace(mode=DeliveryMode.MATERIALIZE_CHANGES))

    messages = build_decision_messages(context, tools, ledger=ledger)  # type: ignore[arg-type]
    rendered = "\n".join(str(message.get("content") or "") for message in messages)

    assert "verification tools for this turn have been emitted" not in rendered
    assert "Verification remains mandatory in a later governed continuation or quality phase" in rendered
