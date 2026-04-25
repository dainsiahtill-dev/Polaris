"""Tests for polaris.cells.audit.diagnosis.internal.toolkit.verify.

Covers chain verification, file integrity, and HMAC helpers.
"""

from __future__ import annotations

import hashlib
import hmac
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.audit.diagnosis.internal.toolkit.verify import (
    verify_chain,
    verify_file_integrity,
    verify_hmac_signature,
)


class TestVerifyFileIntegrity:
    """File integrity verification with SHA-256."""

    def test_file_not_found(self) -> None:
        result = verify_file_integrity("/nonexistent/path/file.txt")
        assert result["valid"] is False
        assert "not found" in result["error"].lower()

    def test_sha256_hash_without_expected(self, tmp_path: Path) -> None:
        file_path = tmp_path / "test.txt"
        content = b"hello world"
        file_path.write_bytes(content)

        result = verify_file_integrity(str(file_path))
        assert result["valid"] is True
        assert result["algorithm"] == "sha256"
        expected = hashlib.sha256(content).hexdigest()
        assert result["hash"] == expected

    def test_sha256_hash_with_correct_expected(self, tmp_path: Path) -> None:
        file_path = tmp_path / "test.txt"
        content = b"hello world"
        file_path.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()

        result = verify_file_integrity(str(file_path), expected_hash=expected)
        assert result["valid"] is True
        assert result["actual_hash"] == expected

    def test_sha256_hash_with_wrong_expected(self, tmp_path: Path) -> None:
        file_path = tmp_path / "test.txt"
        file_path.write_bytes(b"hello world")

        result = verify_file_integrity(str(file_path), expected_hash="wrong")
        assert result["valid"] is False
        assert result["expected_hash"] == "wrong"
        assert result["actual_hash"] != "wrong"

    def test_unsupported_algorithm_rejected(self, tmp_path: Path) -> None:
        file_path = tmp_path / "test.txt"
        file_path.write_bytes(b"data")

        result = verify_file_integrity(str(file_path), algorithm="md5")
        assert result["valid"] is False
        assert "unsupported" in result["error"].lower()

    def test_large_file_hashing(self, tmp_path: Path) -> None:
        file_path = tmp_path / "large.bin"
        content = b"x" * (1024 * 1024)
        file_path.write_bytes(content)

        result = verify_file_integrity(str(file_path))
        assert result["valid"] is True
        assert result["hash"] == hashlib.sha256(content).hexdigest()


class TestVerifyHmacSignature:
    """HMAC-SHA256 signature verification."""

    def test_valid_signature(self) -> None:
        payload = "hello"
        secret = "my-secret"
        expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        assert verify_hmac_signature(payload, expected, secret) is True

    def test_invalid_signature(self) -> None:
        assert verify_hmac_signature("hello", "wrong", "secret") is False

    def test_empty_payload(self) -> None:
        secret = "secret"
        expected = hmac.new(secret.encode("utf-8"), b"", hashlib.sha256).hexdigest()
        assert verify_hmac_signature("", expected, secret) is True

    def test_different_secrets_produce_different_signatures(self) -> None:
        payload = "data"
        sig1 = hmac.new(b"secret1", payload.encode("utf-8"), hashlib.sha256).hexdigest()
        sig2 = hmac.new(b"secret2", payload.encode("utf-8"), hashlib.sha256).hexdigest()
        assert verify_hmac_signature(payload, sig1, "secret1") is True
        assert verify_hmac_signature(payload, sig1, "secret2") is False
        assert verify_hmac_signature(payload, sig2, "secret2") is True


class TestVerifyChain:
    """Audit chain integrity verification delegation."""

    def test_missing_audit_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = verify_chain(tmpdir)
        assert result["chain_valid"] is False
        assert "not found" in result["error"].lower()

    @patch("polaris.cells.audit.diagnosis.internal.toolkit.verify.AuditUseCaseFacade")
    def test_delegates_to_facade(self, mock_facade_cls: Any) -> None:
        mock_facade = MagicMock()
        mock_facade.verify_chain.return_value = {
            "chain_valid": True,
            "first_event_hash": "abc",
            "last_event_hash": "def",
            "total_events": 10,
            "gap_count": 0,
            "verified_at": "2024-01-01T00:00:00Z",
            "invalid_events": [],
        }
        mock_facade_cls.return_value = mock_facade

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit"
            audit_dir.mkdir()
            result = verify_chain(tmpdir)

        assert result["chain_valid"] is True
        assert result["total_events"] == 10
        mock_facade.verify_chain.assert_called_once()

    @patch("polaris.cells.audit.diagnosis.internal.toolkit.verify.AuditUseCaseFacade")
    def test_facade_exception_raises_runtime_error(self, mock_facade_cls: Any) -> None:
        mock_facade = MagicMock()
        mock_facade.verify_chain.side_effect = RuntimeError("disk error")
        mock_facade_cls.return_value = mock_facade

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit"
            audit_dir.mkdir()
            with pytest.raises(RuntimeError, match="verification failed"):
                verify_chain(tmpdir)
