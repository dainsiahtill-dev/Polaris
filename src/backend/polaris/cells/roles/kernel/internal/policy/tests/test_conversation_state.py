"""Tests for polaris.cells.roles.kernel.internal.policy.conversation_state."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.policy.conversation_state import (
    ConversationState,
)


class TestConversationStateCreation:
    """Tests for ConversationState creation."""

    def test_create_with_defaults(self) -> None:
        state = ConversationState()
        assert state.role_id == ""
        assert state.workspace == ""
        assert state.metadata == {}

    def test_create_with_role_id(self) -> None:
        state = ConversationState(role_id="architect")
        assert state.role_id == "architect"
        assert state.workspace == ""
        assert state.metadata == {}

    def test_create_with_workspace(self) -> None:
        state = ConversationState(workspace="/tmp/workspace")
        assert state.role_id == ""
        assert state.workspace == "/tmp/workspace"

    def test_create_with_metadata(self) -> None:
        state = ConversationState(metadata={"key": "value"})
        assert state.metadata == {"key": "value"}

    def test_create_with_all_fields(self) -> None:
        state = ConversationState(
            role_id="qa",
            workspace="/home/user/project",
            metadata={"version": 1, "tags": ["test"]},
        )
        assert state.role_id == "qa"
        assert state.workspace == "/home/user/project"
        assert state.metadata == {"version": 1, "tags": ["test"]}

    def test_create_mutable(self) -> None:
        state = ConversationState(role_id="director")
        state.role_id = "pm"
        assert state.role_id == "pm"

    def test_create_empty_metadata(self) -> None:
        state = ConversationState(metadata={})
        assert state.metadata == {}

    def test_create_nested_metadata(self) -> None:
        state = ConversationState(metadata={"nested": {"deep": "value"}})
        assert state.metadata["nested"]["deep"] == "value"


class TestConversationStateMethods:
    """Tests for ConversationState methods."""

    def test_get_role_id(self) -> None:
        state = ConversationState(role_id="architect")
        assert state.get_role_id() == "architect"

    def test_get_role_id_default(self) -> None:
        state = ConversationState()
        assert state.get_role_id() == ""

    def test_get_workspace(self) -> None:
        state = ConversationState(workspace="/tmp/ws")
        assert state.get_workspace() == "/tmp/ws"

    def test_get_workspace_default(self) -> None:
        state = ConversationState()
        assert state.get_workspace() == ""

    def test_methods_return_strings(self) -> None:
        state = ConversationState(role_id="r", workspace="w")
        assert isinstance(state.get_role_id(), str)
        assert isinstance(state.get_workspace(), str)


class TestConversationStateEdgeCases:
    """Tests for ConversationState edge cases."""

    def test_role_id_with_whitespace(self) -> None:
        state = ConversationState(role_id="  architect  ")
        assert state.role_id == "  architect  "
        assert state.get_role_id() == "  architect  "

    def test_empty_role_id(self) -> None:
        state = ConversationState(role_id="")
        assert state.role_id == ""
        assert state.get_role_id() == ""

    def test_empty_workspace(self) -> None:
        state = ConversationState(workspace="")
        assert state.workspace == ""
        assert state.get_workspace() == ""

    def test_none_in_metadata(self) -> None:
        state = ConversationState(metadata={"key": None})
        assert state.metadata["key"] is None

    def test_special_characters_in_role_id(self) -> None:
        state = ConversationState(role_id="role-with-dashes_123")
        assert state.role_id == "role-with-dashes_123"

    def test_unicode_workspace(self) -> None:
        state = ConversationState(workspace="/工作区/项目")
        assert state.workspace == "/工作区/项目"

    def test_metadata_isolated_per_instance(self) -> None:
        state1 = ConversationState(metadata={"a": 1})
        state2 = ConversationState(metadata={"a": 1})
        assert state1.metadata == state2.metadata
        state1.metadata["a"] = 99
        assert state2.metadata["a"] == 1


class TestConversationStateMutation:
    """Tests for ConversationState mutation behavior."""

    def test_modify_role_id_after_creation(self) -> None:
        state = ConversationState()
        state.role_id = "pm"
        assert state.role_id == "pm"

    def test_modify_workspace_after_creation(self) -> None:
        state = ConversationState()
        state.workspace = "/new/path"
        assert state.workspace == "/new/path"

    def test_modify_metadata_after_creation(self) -> None:
        state = ConversationState()
        state.metadata["new_key"] = "new_value"
        assert state.metadata["new_key"] == "new_value"

    def test_replace_metadata(self) -> None:
        state = ConversationState(metadata={"old": "value"})
        state.metadata = {"new": "value"}  # type: ignore[misc]
        assert state.metadata == {"new": "value"}


class TestConversationStateRepr:
    """Tests for ConversationState representation."""

    def test_repr_contains_class_name(self) -> None:
        state = ConversationState()
        assert "ConversationState" in repr(state)

    def test_repr_contains_role_id(self) -> None:
        state = ConversationState(role_id="architect")
        assert "architect" in repr(state)

    def test_repr_contains_workspace(self) -> None:
        state = ConversationState(workspace="/tmp")
        assert "/tmp" in repr(state)
