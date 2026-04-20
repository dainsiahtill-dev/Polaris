"""Tests for message normalizer."""

import pytest
from polaris.kernelone.llm.tools.message_normalizer import (
    MessageNormalizer,
    MessageNormalizerConfig,
    NormalizationResult,
    auto_fix_conversation,
    normalize_messages,
    validate_conversation_structure,
    validate_message_structure,
)


class TestValidateMessageStructure:
    """Tests for validate_message_structure function."""

    def test_valid_string_message(self) -> None:
        """Should validate a proper string message."""
        msg = {"role": "user", "content": "Hello"}
        is_valid, error = validate_message_structure(msg)
        assert is_valid is True
        assert error is None

    def test_valid_list_content(self) -> None:
        """Should validate a message with list content."""
        msg = {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]}
        is_valid, _error = validate_message_structure(msg)
        assert is_valid is True

    def test_missing_role(self) -> None:
        """Should reject message without role."""
        msg = {"content": "Hello"}
        is_valid, error = validate_message_structure(msg)
        assert is_valid is False
        assert error is not None and "role" in error.lower()

    def test_invalid_role(self) -> None:
        """Should reject message with invalid role."""
        msg = {"role": "invalid_role", "content": "Hello"}
        is_valid, error = validate_message_structure(msg)
        assert is_valid is False
        assert error is not None and "invalid role" in error.lower()

    def test_invalid_content_type(self) -> None:
        """Should reject message with invalid content type."""
        msg = {"role": "user", "content": 12345}  # Should be str or list
        is_valid, error = validate_message_structure(msg)
        assert is_valid is False
        assert error is not None and "content" in error.lower()


class TestValidateConversationStructure:
    """Tests for validate_conversation_structure function."""

    def test_valid_conversation(self) -> None:
        """Should validate a proper conversation."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        is_valid, errors = validate_conversation_structure(messages)
        assert is_valid is True
        assert len(errors) == 0

    def test_empty_conversation(self) -> None:
        """Should reject empty conversation."""
        is_valid, errors = validate_conversation_structure([])
        assert is_valid is False
        assert "empty" in errors[0].lower()

    def test_invalid_first_role(self) -> None:
        """Should reject conversation starting with assistant."""
        messages = [
            {"role": "assistant", "content": "Hello"},
        ]
        is_valid, errors = validate_conversation_structure(messages)
        assert is_valid is False
        assert any("first message" in e.lower() for e in errors)


class TestMessageNormalizer:
    """Tests for MessageNormalizer class."""

    def test_normalize_valid_conversation(self) -> None:
        """Should normalize a valid conversation without changes."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        normalizer = MessageNormalizer()
        result = normalizer.normalize(messages)

        assert len(result.messages) == 3
        assert result.errors == ()
        assert result.changes_made == 0

    def test_normalize_missing_role(self) -> None:
        """Should fill in missing role."""
        messages = [
            {"content": "Hello"},  # Missing role
        ]
        normalizer = MessageNormalizer()
        result = normalizer.normalize(messages)

        assert result.messages[0]["role"] == "user"
        assert len(result.errors) > 0

    def test_normalize_empty_assistant(self) -> None:
        """Should fill empty assistant message."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": ""},  # Empty
        ]
        normalizer = MessageNormalizer()
        result = normalizer.normalize(messages)

        assert "[No response provided.]" in result.messages[1]["content"]
        assert result.changes_made > 0

    def test_normalize_strip_duplicate_systems(self) -> None:
        """Should strip duplicate system messages."""
        messages = [
            {"role": "system", "content": "System 1"},
            {"role": "user", "content": "Hello"},
            {"role": "system", "content": "System 2"},  # Duplicate
        ]
        config = MessageNormalizerConfig(strip_system_after_first=True)
        normalizer = MessageNormalizer(config)
        result = normalizer.normalize(messages)

        system_count = sum(1 for m in result.messages if m["role"] == "system")
        assert system_count == 1

    def test_validate_alternating_roles(self) -> None:
        """Should validate alternating role pattern."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        normalizer = MessageNormalizer()
        is_valid, violations = normalizer.validate_alternating_roles(messages)
        assert is_valid is True
        assert len(violations) == 0

    def test_detect_invalid_role_transition(self) -> None:
        """Should detect invalid role transitions."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Again?"},  # Invalid: user after user
        ]
        normalizer = MessageNormalizer(MessageNormalizerConfig(allow_sequential_same_role=False))
        is_valid, _violations = normalizer.validate_alternating_roles(messages)
        assert is_valid is False


class TestNormalizeMessages:
    """Tests for normalize_messages convenience function."""

    def test_basic_normalization(self) -> None:
        """Should normalize messages with defaults."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": ""},
        ]
        result = normalize_messages(messages)

        assert len(result.messages) == 2
        assert "Assistant" in result.messages[1]["content"] or result.errors


class TestAutoFixConversation:
    """Tests for auto_fix_conversation function."""

    def test_add_system_message_if_missing(self) -> None:
        """Should add system message if conversation starts with user."""
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        result = auto_fix_conversation(messages)

        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"

    def test_preserve_existing_system(self) -> None:
        """Should preserve existing system message."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        result = auto_fix_conversation(messages)

        assert result[0]["role"] == "system"
        assert len(result) == 2

    def test_fill_empty_assistant(self) -> None:
        """Should fill empty assistant messages."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": ""},
        ]
        result = auto_fix_conversation(messages)

        assert result[2]["content"] != ""

    def test_empty_conversation(self) -> None:
        """Should create default conversation for empty list."""
        result = auto_fix_conversation([])
        assert len(result) == 1
        assert result[0]["role"] == "system"


class TestNormalizationResult:
    """Tests for NormalizationResult dataclass."""

    def test_result_fields(self) -> None:
        """Should have correct fields."""
        result = NormalizationResult(
            messages=({"role": "user", "content": "Hello"},),
            errors=("some error",),
            warnings=("some warning",),
            changes_made=1,
        )

        assert len(result.messages) == 1
        assert len(result.errors) == 1
        assert len(result.warnings) == 1
        assert result.changes_made == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
