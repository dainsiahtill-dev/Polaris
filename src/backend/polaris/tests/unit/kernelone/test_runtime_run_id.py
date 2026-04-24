"""Tests for polaris.kernelone.runtime.run_id."""

from __future__ import annotations

import pytest
from polaris.kernelone.runtime.run_id import (
    ensure_valid_run_id,
    normalize_run_id,
    validate_run_id,
)


class TestNormalizeRunId:
    def test_basic(self) -> None:
        assert normalize_run_id("abc-123") == "abc-123"

    def test_none_returns_empty(self) -> None:
        assert normalize_run_id(None) == ""

    def test_strips_whitespace(self) -> None:
        assert normalize_run_id("  abc  ") == "abc"


class TestValidateRunId:
    def test_valid_with_dash(self) -> None:
        assert validate_run_id("run-001") is True

    def test_valid_with_underscore(self) -> None:
        assert validate_run_id("run_001") is True

    def test_invalid_with_dot(self) -> None:
        # dot is allowed by regex but not counted as delimiter
        assert validate_run_id("run.001") is False

    def test_empty(self) -> None:
        assert validate_run_id("") is False

    def test_none(self) -> None:
        assert validate_run_id(None) is False

    def test_no_delimiter(self) -> None:
        assert validate_run_id("abc123") is False

    def test_path_traversal_dotdot(self) -> None:
        assert validate_run_id("../run") is False

    def test_path_separator_slash(self) -> None:
        assert validate_run_id("a/b") is False

    def test_path_separator_backslash(self) -> None:
        assert validate_run_id("a\\b") is False

    def test_too_short(self) -> None:
        assert validate_run_id("a") is False

    def test_too_long(self) -> None:
        assert validate_run_id("a" * 130) is False


class TestEnsureValidRunId:
    def test_returns_valid(self) -> None:
        assert ensure_valid_run_id("run-001") == "run-001"

    def test_raises_on_invalid(self) -> None:
        with pytest.raises(ValueError):
            ensure_valid_run_id("invalid")
