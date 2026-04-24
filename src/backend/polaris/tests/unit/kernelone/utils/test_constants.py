"""Tests for polaris.kernelone.utils.constants."""

from __future__ import annotations

from polaris.kernelone.utils.constants import (
    DEFAULT_AUDIT_RETENTION_DAYS,
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    DEFAULT_SESSION_TIMEOUT_SECONDS,
    EMPTY_SHA256,
    GENESIS_HASH,
)


class TestConstants:
    def test_genesis_hash(self) -> None:
        assert GENESIS_HASH == "0" * 64

    def test_empty_sha256_alias(self) -> None:
        assert EMPTY_SHA256 is GENESIS_HASH

    def test_audit_retention_days(self) -> None:
        assert DEFAULT_AUDIT_RETENTION_DAYS == 90

    def test_lock_timeout_seconds(self) -> None:
        assert DEFAULT_LOCK_TIMEOUT_SECONDS == 30

    def test_session_timeout_seconds(self) -> None:
        assert DEFAULT_SESSION_TIMEOUT_SECONDS == 3600
