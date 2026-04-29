"""Tests for polaris.kernelone.utils.constants - values and package imports."""

from __future__ import annotations

from polaris.kernelone.utils import (
    DEFAULT_AUDIT_RETENTION_DAYS,
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    DEFAULT_SESSION_TIMEOUT_SECONDS,
    EMPTY_SHA256,
    GENESIS_HASH,
)
from polaris.kernelone.utils.constants import (
    DEFAULT_AUDIT_RETENTION_DAYS as DIRECT_AUDIT_DAYS,
    DEFAULT_LOCK_TIMEOUT_SECONDS as DIRECT_LOCK_SECONDS,
    DEFAULT_SESSION_TIMEOUT_SECONDS as DIRECT_SESSION_SECONDS,
    EMPTY_SHA256 as DIRECT_EMPTY_SHA256,
    GENESIS_HASH as DIRECT_GENESIS_HASH,
)


class TestConstantValues:
    def test_genesis_hash_length(self) -> None:
        assert len(GENESIS_HASH) == 64

    def test_genesis_hash_content(self) -> None:
        assert GENESIS_HASH == "0" * 64

    def test_empty_sha256_is_genesis(self) -> None:
        assert EMPTY_SHA256 is GENESIS_HASH

    def test_audit_retention_positive(self) -> None:
        assert DEFAULT_AUDIT_RETENTION_DAYS > 0

    def test_lock_timeout_positive(self) -> None:
        assert DEFAULT_LOCK_TIMEOUT_SECONDS > 0

    def test_session_timeout_positive(self) -> None:
        assert DEFAULT_SESSION_TIMEOUT_SECONDS > 0


class TestPackageReexports:
    def test_package_imports_match_direct(self) -> None:
        assert GENESIS_HASH == DIRECT_GENESIS_HASH
        assert EMPTY_SHA256 == DIRECT_EMPTY_SHA256
        assert DEFAULT_AUDIT_RETENTION_DAYS == DIRECT_AUDIT_DAYS
        assert DEFAULT_LOCK_TIMEOUT_SECONDS == DIRECT_LOCK_SECONDS
        assert DEFAULT_SESSION_TIMEOUT_SECONDS == DIRECT_SESSION_SECONDS

    def test_all_constants_are_expected_types(self) -> None:
        assert isinstance(GENESIS_HASH, str)
        assert isinstance(EMPTY_SHA256, str)
        assert isinstance(DEFAULT_AUDIT_RETENTION_DAYS, int)
        assert isinstance(DEFAULT_LOCK_TIMEOUT_SECONDS, int)
        assert isinstance(DEFAULT_SESSION_TIMEOUT_SECONDS, int)

    def test_genesis_hash_is_hex_ish(self) -> None:
        assert all(c == "0" for c in GENESIS_HASH)

    def test_session_timeout_is_one_hour(self) -> None:
        assert DEFAULT_SESSION_TIMEOUT_SECONDS == 3600
