"""Tests for polaris.cells.roles.kernel.internal.policy.conversation_state."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.policy.conversation_state import ConversationState


class TestConversationState:
    def test_defaults(self) -> None:
        cs = ConversationState()
        assert cs.role_id == ""
        assert cs.workspace == ""
        assert cs.metadata == {}

    def test_custom_values(self) -> None:
        cs = ConversationState(role_id="pm", workspace="/tmp/ws", metadata={"key": "val"})
        assert cs.role_id == "pm"
        assert cs.workspace == "/tmp/ws"
        assert cs.metadata == {"key": "val"}

    def test_get_role_id(self) -> None:
        cs = ConversationState(role_id="architect")
        assert cs.get_role_id() == "architect"

    def test_get_workspace(self) -> None:
        cs = ConversationState(workspace="/workspace")
        assert cs.get_workspace() == "/workspace"
