"""Message normalization for LLM conversation history.

Ensures alternating role patterns, auto-fills empty messages,
and validates the last non-system message legality for provider protocols.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

# Valid role values
_VALID_ROLES: Final[frozenset[str]] = frozenset(
    {
        "system",
        "user",
        "assistant",
        "developer",
        "tool",
        "function",
    }
)

# Roles that are valid as the first message
_FIRST_MESSAGE_ROLES: Final[frozenset[str]] = frozenset(
    {
        "system",
        "user",
    }
)

# Roles that are valid as the last non-system message
_LAST_MESSAGE_ROLES: Final[frozenset[str]] = frozenset(
    {
        "user",
        "assistant",
    }
)


@dataclass(frozen=True)
class MessageNormalizerConfig:
    """Configuration for message normalization.

    Attributes:
        allow_sequential_same_role: Allow same roles to appear sequentially.
        strip_system_after_first: Strip system messages after the first.
        fill_empty_assistant: Auto-fill empty assistant messages.
        validate_last_message: Validate the last message is a user/assistant.
    """

    allow_sequential_same_role: bool = False
    strip_system_after_first: bool = False
    fill_empty_assistant: bool = True
    validate_last_message: bool = True


@dataclass(frozen=True)
class NormalizationResult:
    """Result of normalizing a message or conversation.

    Attributes:
        messages: The normalized messages.
        errors: Any errors encountered during normalization.
        warnings: Any warnings encountered.
        changes_made: Number of changes made to the messages.
    """

    messages: tuple[dict[str, Any], ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    changes_made: int


class MessageNormalizer:
    """Normalizer for conversation messages.

    Ensures messages conform to provider protocols by:
    1. Validating alternating role patterns
    2. Auto-filling empty required messages
    3. Validating the last non-system message legality
    4. Stripping duplicate system messages

    Example:
        >>> normalizer = MessageNormalizer()
        >>> messages = [
        ...     {"role": "user", "content": "Hello"},
        ...     {"role": "assistant", "content": ""},  # Empty!
        ... ]
        >>> result = normalizer.normalize(messages)
        >>> print(result.messages[1]["content"])
        [No response provided.]
    """

    def __init__(self, config: MessageNormalizerConfig | None = None) -> None:
        """Initialize the normalizer.

        Args:
            config: Optional configuration. Uses defaults if not provided.
        """
        self._config = config or MessageNormalizerConfig()

    @property
    def config(self) -> MessageNormalizerConfig:
        """The current configuration."""
        return self._config

    def normalize(
        self,
        messages: list[dict[str, Any]],
    ) -> NormalizationResult:
        """Normalize a list of conversation messages.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.

        Returns:
            NormalizationResult with normalized messages.
        """
        if not messages:
            return NormalizationResult(
                messages=(),
                errors=(),
                warnings=(),
                changes_made=0,
            )

        result = list(self._ensure_list(messages))
        errors: list[str] = []
        warnings: list[str] = []
        changes = 0

        # Step 1: Validate and normalize roles
        result, role_errors, role_changes = self._normalize_roles(result)
        errors.extend(role_errors)
        changes += role_changes

        # Step 2: Strip duplicate system messages
        if self._config.strip_system_after_first:
            result, sys_changes = self._strip_duplicate_systems(result)
            changes += sys_changes

        # Step 3: Fill empty messages
        result, fill_errors, fill_changes = self._fill_empty_messages(result)
        errors.extend(fill_errors)
        changes += fill_changes

        # Step 4: Validate last non-system message
        if self._config.validate_last_message:
            last_error = self._validate_last_message(result)
            if last_error:
                errors.append(last_error)

        return NormalizationResult(
            messages=tuple(result),
            errors=tuple(errors),
            warnings=tuple(warnings),
            changes_made=changes,
        )

    def _ensure_list(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Ensure messages are valid dicts."""
        result: list[dict[str, Any]] = []
        for _i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                msg = {"role": "user", "content": str(msg)}
            result.append(dict(msg))
        return result

    def _normalize_roles(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str], int]:
        """Normalize message roles."""
        errors: list[str] = []
        changes = 0
        result = list(messages)

        for i, msg in enumerate(result):
            role = str(msg.get("role", "")).lower().strip()
            if not role:
                errors.append(f"message[{i}] missing role, assuming 'user'")
                msg["role"] = "user"
                changes += 1
            elif role not in _VALID_ROLES:
                errors.append(f"message[{i}] invalid role '{role}', assuming 'user'")
                msg["role"] = "user"
                changes += 1
            else:
                msg["role"] = role

        # Check for sequential same roles
        if not self._config.allow_sequential_same_role:
            for i in range(1, len(result)):
                prev_role = result[i - 1].get("role")
                curr_role = result[i].get("role")
                if prev_role == curr_role and prev_role not in {"system", "tool", "function"}:
                    (f"message[{i - 1}] and message[{i}] both have role '{prev_role}'")
                    # Merge adjacent same-role messages
                    prev_content = result[i - 1].get("content", "")
                    curr_content = result[i].get("content", "")
                    if isinstance(prev_content, str) and isinstance(curr_content, str):
                        result[i - 1]["content"] = prev_content + "\n" + curr_content
                        result.pop(i)
                        changes += 1
                        errors.append(f"merged adjacent messages at index {i - 1} and {i}")
                        break

        return result, errors, changes

    def _strip_duplicate_systems(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Strip duplicate system messages after the first."""
        changes = 0
        result: list[dict[str, Any]] = []
        first_system_seen = False

        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                if not first_system_seen:
                    result.append(msg)
                    first_system_seen = True
                else:
                    changes += 1  # Skipped duplicate
            else:
                result.append(msg)

        return result, changes

    def _fill_empty_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str], int]:
        """Fill empty content in required messages."""
        errors: list[str] = []
        changes = 0
        result = list(messages)

        for i, msg in enumerate(result):
            role = msg.get("role", "")
            content = msg.get("content")

            # Check for empty content
            if content is None or (isinstance(content, str) and not content.strip()):
                if role == "assistant" and self._config.fill_empty_assistant:
                    msg["content"] = "[No response provided.]"
                    changes += 1
                    errors.append(f"message[{i}] assistant had empty content, filled with default")
                elif role == "user" and self._config.fill_empty_assistant:
                    msg["content"] = "[User query omitted.]"
                    changes += 1
                    errors.append(f"message[{i}] user had empty content, filled with default")

        return result, errors, changes

    def _validate_last_message(
        self,
        messages: list[dict[str, Any]],
    ) -> str | None:
        """Validate the last non-system message is user or assistant.

        Returns:
            Error message if invalid, None if valid.
        """
        # Find last non-system message
        last_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") != "system":
                last_idx = i
                break

        if last_idx < 0:
            return "conversation has no non-system messages"

        last_role = messages[last_idx].get("role", "")
        if last_role not in _LAST_MESSAGE_ROLES:
            return (
                f"message[{last_idx}] has role '{last_role}' which is not valid "
                f"as the last message (expected {_LAST_MESSAGE_ROLES})"
            )

        return None

    def validate_alternating_roles(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[bool, list[str]]:
        """Check if messages follow alternating role pattern.

        Args:
            messages: List of message dicts.

        Returns:
            Tuple of (is_valid, list of violation messages).
        """
        violations: list[str] = []
        allowed_pairs = {
            ("system", "user"),
            ("system", "assistant"),
            ("user", "assistant"),
            ("assistant", "user"),
            ("tool", "assistant"),
            ("function", "assistant"),
        }

        for i in range(1, len(messages)):
            prev_role = messages[i - 1].get("role", "")
            curr_role = messages[i].get("role", "")

            # Skip if same role is allowed
            if prev_role == curr_role:
                if prev_role in {"system", "tool", "function"}:
                    continue
                if self._config.allow_sequential_same_role:
                    continue

            # Check if pair is valid
            if (prev_role, curr_role) not in allowed_pairs:
                violations.append(f"invalid role transition at index {i - 1}->{i}: '{prev_role}' -> '{curr_role}'")

        return len(violations) == 0, violations


def normalize_messages(
    messages: list[dict[str, Any]],
    config: MessageNormalizerConfig | None = None,
) -> NormalizationResult:
    """Convenience function to normalize messages.

    Args:
        messages: List of message dicts.
        config: Optional configuration.

    Returns:
        NormalizationResult with normalized messages.
    """
    normalizer = MessageNormalizer(config)
    return normalizer.normalize(messages)


def validate_message_structure(msg: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate a single message structure.

    Args:
        msg: A message dict.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not isinstance(msg, dict):
        return False, "message must be a dict"

    role = str(msg.get("role", "")).lower().strip()
    if not role:
        return False, "message missing 'role' field"
    if role not in _VALID_ROLES:
        return False, f"invalid role '{role}'"

    # Content can be string, list, or None
    content = msg.get("content")
    if content is not None and not isinstance(content, (str, list)):
        return False, f"content must be str or list, got {type(content).__name__}"

    return True, None


def validate_conversation_structure(
    messages: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    """Validate the structure of an entire conversation.

    Args:
        messages: List of message dicts.

    Returns:
        Tuple of (is_valid, list of error messages).
    """
    errors: list[str] = []

    # Check for empty conversation
    if not messages:
        errors.append("conversation is empty")
        return False, errors

    # Validate first message
    first_role = str(messages[0].get("role", "")).lower().strip()
    if first_role not in _FIRST_MESSAGE_ROLES:
        errors.append(f"first message has role '{first_role}', expected one of {_FIRST_MESSAGE_ROLES}")

    # Validate each message structure
    for i, msg in enumerate(messages):
        is_valid, error = validate_message_structure(msg)
        if not is_valid:
            errors.append(f"message[{i}]: {error}")

    # Validate alternating roles
    normalizer = MessageNormalizer()
    is_valid, violations = normalizer.validate_alternating_roles(messages)
    errors.extend(violations)

    return len(errors) == 0, errors


def auto_fix_conversation(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Auto-fix common conversation issues.

    This is a convenience function that:
    1. Adds a system message if conversation starts with user
    2. Fills empty assistant messages
    3. Ensures last message is user or assistant

    Args:
        messages: List of message dicts.

    Returns:
        Fixed list of message dicts.
    """
    if not messages:
        return [{"role": "system", "content": "You are a helpful assistant."}]

    result = list(messages)

    # Add system message if needed
    if result[0].get("role") != "system":
        result.insert(0, {"role": "system", "content": "You are a helpful assistant."})

    # Fill empty assistant messages
    for i, msg in enumerate(result):
        content = msg.get("content")
        if msg.get("role") == "assistant" and (content is None or (isinstance(content, str) and not content.strip())):
            result[i] = dict(msg)
            result[i]["content"] = "[No response provided.]"

    # Ensure last message is user or assistant
    if result:
        last_idx = len(result) - 1
        while last_idx > 0 and result[last_idx].get("role") == "system":
            last_idx -= 1

        last_role = result[last_idx].get("role", "")
        if last_role not in _LAST_MESSAGE_ROLES:
            # Add a placeholder user message
            result.append({"role": "user", "content": "[Continue]"})

    return result


__all__ = [
    "MessageNormalizer",
    "MessageNormalizerConfig",
    "NormalizationResult",
    "auto_fix_conversation",
    "normalize_messages",
    "validate_conversation_structure",
    "validate_message_structure",
]
