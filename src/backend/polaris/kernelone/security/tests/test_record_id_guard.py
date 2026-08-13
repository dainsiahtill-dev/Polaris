"""Canonical storage record-id guard (path-traversal fail-closed).

These tests pin the single source of truth that every persisted-ledger
record id must funnel through. The guard existed as 8 byte-identical
copies across chief_engineer (risk/adr/incident/tech_debt/tech_radar)
and pm_planning (decision/milestone/raid) ledgers, differing only in the
error-message label. This regression ensures the consolidated SSoT keeps
the exact fail-closed semantics and surfaces the label for attribution.
"""

from __future__ import annotations

import pytest

from polaris.kernelone.security.record_id_guard import (
    SAFE_RECORD_ID_PATTERN,
    is_safe_record_id,
    validate_storage_record_id,
)


class TestValidateStorageRecordId:
    def test_accepts_safe_tokens(self) -> None:
        for token in ("risk_42", "adr-2026-001", "inc.v2", "ABC123", "a.b-c_d"):
            assert validate_storage_record_id(token) == token

    def test_strips_surrounding_whitespace(self) -> None:
        assert validate_storage_record_id("  risk_42  ") == "risk_42"

    def test_rejects_empty_after_strip(self) -> None:
        with pytest.raises(ValueError):
            validate_storage_record_id("   ")

    def test_rejects_none(self) -> None:
        with pytest.raises(ValueError):
            validate_storage_record_id(None)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "malicious",
        [
            "../etc/passwd",
            "..%2fetc",
            "foo/bar",
            "foo\\bar",
            "a/../b",
            "a;b",
            "a b",
            "a|b",
            "a$(b)",
            "a`b`",
            "",
        ],
    )
    def test_rejects_traversal_and_meta(self, malicious: str) -> None:
        with pytest.raises(ValueError):
            validate_storage_record_id(malicious)

    def test_label_appears_in_error_for_attribution(self) -> None:
        """Each ledger passes its own label so the error points at the right field."""
        with pytest.raises(ValueError, match="risk_id"):
            validate_storage_record_id("../x", label="risk_id")
        with pytest.raises(ValueError, match="adr_id"):
            validate_storage_record_id("../x", label="adr_id")
        # default label is generic
        with pytest.raises(ValueError, match="record id"):
            validate_storage_record_id("../x")


class TestIsSafeRecordId:
    def test_bool_predicate_mirrors_validator(self) -> None:
        assert is_safe_record_id("safe_id-1") is True
        assert is_safe_record_id("../escape") is False
        assert is_safe_record_id("") is False
        assert is_safe_record_id(None) is False  # type: ignore[arg-type]

    def test_pattern_is_fullmatch(self) -> None:
        # partial matches must NOT pass (the original used .match on ^...$)
        assert SAFE_RECORD_ID_PATTERN.match("safe\x00") is None
