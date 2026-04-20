"""Random reasoning tag generation for anti-injection protection.

Each session gets a unique random tag to prevent injection attacks
through reasoning/thinking content that could mimic protocol tags.
"""

from __future__ import annotations

import secrets
import string
import uuid
from dataclasses import dataclass
from typing import Final

# Standard tag prefixes used by various LLM providers
_STANDARD_PREFIXES: Final[tuple[str, ...]] = (
    "think",
    "thinking",
    "thought",
    "answer",
    "output",
    "reasoning",
    "reflection",
)


@dataclass(frozen=True)
class ReasoningTagSet:
    """Immutable set of reasoning tags for a session.

    Attributes:
        open_tag: Opening XML-like tag with random suffix.
        close_tag: Matching closing tag.
        prefix: The semantic prefix (e.g., "think", "reasoning").
        session_id: UUID4 session identifier.
        raw_suffix: The random alphanumeric suffix.
    """

    open_tag: str
    close_tag: str
    prefix: str
    session_id: str
    raw_suffix: str


_TAG_CHARS: Final[str] = string.ascii_lowercase + string.digits
_TAG_LENGTH: Final[int] = 8


def _generate_random_suffix(length: int = _TAG_LENGTH) -> str:
    """Generate a cryptographically random alphanumeric suffix.

    Args:
        length: Number of characters in the suffix. Defaults to 8.

    Returns:
        A random string of lowercase letters and digits.
    """
    return "".join(secrets.choice(_TAG_CHARS) for _ in range(length))


def _build_tags(prefix: str, suffix: str) -> tuple[str, str]:
    """Build open/close tag pair from prefix and suffix.

    Args:
        prefix: Semantic tag prefix (e.g., "think").
        suffix: Random suffix to make tags unique per session.

    Returns:
        Tuple of (open_tag, close_tag) with format <prefix:suffix> ... </prefix:suffix>.
    """
    open_tag = f"<{prefix}:{suffix}>"
    close_tag = f"</{prefix}:{suffix}>"
    return open_tag, close_tag


class ReasoningTagGenerator:
    """Generator for per-session random reasoning tags.

    Generates unique XML-like tags for each session to prevent injection
    attacks where malicious reasoning content could mimic protocol tags.

    Example:
        >>> gen = ReasoningTagGenerator(prefix="think")
        >>> tag_set = gen.generate()
        >>> print(tag_set.open_tag)  # e.g., "<think:a1b2c3d4>"
    """

    def __init__(
        self,
        prefix: str = "think",
        suffix_length: int = _TAG_LENGTH,
    ) -> None:
        """Initialize the tag generator.

        Args:
            prefix: Semantic tag prefix. Defaults to "think".
            suffix_length: Length of random suffix. Defaults to 8.
        """
        normalized = str(prefix or "think").strip().lower()
        if not normalized:
            normalized = "think"
        self._prefix: str = normalized
        self._suffix_length: int = max(1, int(suffix_length))

    @property
    def prefix(self) -> str:
        """The semantic prefix for generated tags."""
        return self._prefix

    def generate(self, session_id: str | None = None) -> ReasoningTagSet:
        """Generate a new set of reasoning tags for a session.

        Args:
            session_id: Optional session identifier. If not provided,
                a new UUID4 will be generated.

        Returns:
            ReasoningTagSet with unique open/close tags and session ID.
        """
        suffix = _generate_random_suffix(self._suffix_length)
        open_tag, close_tag = _build_tags(self._prefix, suffix)
        sid = str(session_id or uuid.uuid4())
        return ReasoningTagSet(
            open_tag=open_tag,
            close_tag=close_tag,
            prefix=self._prefix,
            session_id=sid,
            raw_suffix=suffix,
        )

    @classmethod
    def for_standard_prefix(
        cls,
        prefix: str,
        session_id: str | None = None,
    ) -> ReasoningTagSet:
        """Generate tags for a standard provider prefix.

        Args:
            prefix: One of the standard prefixes (think, thinking, etc.).
            session_id: Optional session identifier.

        Returns:
            ReasoningTagSet with unique tags for the session.

        Raises:
            ValueError: If prefix is not a known standard prefix.
        """
        normalized = str(prefix or "").strip().lower()
        if not normalized:
            raise ValueError("prefix cannot be empty")
        if normalized not in _STANDARD_PREFIXES:
            raise ValueError(f"unknown prefix '{prefix}', expected one of {_STANDARD_PREFIXES}")
        gen = cls(prefix=normalized)
        return gen.generate(session_id=session_id)

    @classmethod
    def standard_prefixes(cls) -> tuple[str, ...]:
        """Return the tuple of known standard prefixes."""
        return _STANDARD_PREFIXES


def generate_session_tag(
    prefix: str = "think",
    session_id: str | None = None,
    suffix_length: int = _TAG_LENGTH,
) -> ReasoningTagSet:
    """Convenience function to generate a single reasoning tag set.

    Args:
        prefix: Semantic tag prefix. Defaults to "think".
        session_id: Optional session identifier.
        suffix_length: Length of random suffix. Defaults to 8.

    Returns:
        ReasoningTagSet with unique open/close tags.
    """
    gen = ReasoningTagGenerator(prefix=prefix, suffix_length=suffix_length)
    return gen.generate(session_id=session_id)


__all__ = [
    "ReasoningTagGenerator",
    "ReasoningTagSet",
    "generate_session_tag",
]
