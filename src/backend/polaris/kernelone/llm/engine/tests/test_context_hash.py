"""Tests for ``validate_context_hash`` and the underlying hash contract.

These tests pin the *lenient* whitespace behavior explicitly: the helper
strips leading/trailing whitespace before applying the strict
``CONTEXT_HASH_PATTERN`` fullmatch, and the canonical value it returns is
the stripped/lowercased form.  The contract is shared between the producer
(``AIExecutor._store_context_messages_sync``) and the consumer
(``GET /v2/context/{hash}``); any drift in the stripping rule is a
producer/consumer desync risk and must fail this suite.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.llm.engine.internal.context_hash import (
    CONTEXT_HASH_PATTERN,
    validate_context_hash,
)

# A canonical 24-char lowercase hex token used as a "valid" fixture.
VALID_HASH = "0123456789abcdef01234567"


class TestValidateContextHashAcceptance:
    """Cases the validator must accept."""

    def test_full_lowercase_24_hex_passes_through_unchanged(self) -> None:
        """A clean 24-char lowercase hex string round-trips byte-identical."""
        result = validate_context_hash(VALID_HASH)
        assert result == VALID_HASH
        assert len(result) == 24

    def test_leading_and_trailing_whitespace_is_accepted_and_stripped(self) -> None:
        """Leading + trailing whitespace is stripped; canonical form returned.

        This is the core lenient-UX behavior: a transport layer that
        appends a newline, or a copy/paste that picks up a leading space,
        must still resolve to the canonical key.  The function returns the
        stripped form (not the raw input) so the producer and consumer
        agree on the same disk key.
        """
        padded = "  " + VALID_HASH + " \n"
        result = validate_context_hash(padded)
        assert result == VALID_HASH
        # And the returned value must itself be a clean pattern match.
        assert CONTEXT_HASH_PATTERN.fullmatch(result) is not None

    def test_tabs_and_carriage_returns_are_stripped(self) -> None:
        """Tabs and ``\\r`` count as whitespace for the lenient strip step."""
        padded = "\t" + VALID_HASH + "\r"
        result = validate_context_hash(padded)
        assert result == VALID_HASH


class TestValidateContextHashRejection:
    """Cases the validator must reject with ``ValueError``."""

    def test_uppercase_is_rejected(self) -> None:
        """Uppercase hex is rejected — the contract is strict-lowercase.

        A producer that emits uppercase would land on a different disk key
        than the lowercase consumer expects, so we fail-closed at the
        boundary instead of silently lowercasing mid-string (the surrounding
        ``.strip()`` is whitespace-only).
        """
        with pytest.raises(ValueError):
            validate_context_hash(VALID_HASH.upper())

    def test_embedded_space_is_rejected(self) -> None:
        """Whitespace *inside* the hash is not stripped and fails the pattern."""
        # Build an embedded-space variant: 5 chars + space + 18 hex chars.
        embedded = VALID_HASH[:5] + " " + VALID_HASH[6:]
        with pytest.raises(ValueError):
            validate_context_hash(embedded)

    def test_embedded_null_is_rejected(self) -> None:
        """Embedded null bytes are not tolerated — they could mask injection."""
        with pytest.raises(ValueError):
            validate_context_hash(VALID_HASH + "\x00")

    def test_non_string_input_is_rejected(self) -> None:
        """Type-guard: only ``str`` is accepted."""
        with pytest.raises(ValueError):
            validate_context_hash(12345)  # type: ignore[arg-type]

    def test_too_short_is_rejected(self) -> None:
        """Length < 24 must fail even after stripping."""
        with pytest.raises(ValueError):
            validate_context_hash("  abc  ")

    def test_non_hex_character_is_rejected(self) -> None:
        """Non-hex characters fail the fullmatch even with valid length."""
        # Replace the last char with 'g' (not a hex digit).
        with pytest.raises(ValueError):
            validate_context_hash(VALID_HASH[:23] + "g")
