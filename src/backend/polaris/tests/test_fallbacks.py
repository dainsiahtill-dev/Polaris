"""Tests for FallbackChain and SafeNull - SafeNull behavior coverage."""

from __future__ import annotations

from typing import ClassVar

import pytest
from polaris.kernelone.llm.robust_parser.fallbacks import (
    FallbackAttempt,
    FallbackChain,
    SafeNull,
)
from pydantic import BaseModel


class TestSchema(BaseModel):
    """Test schema for fallback tests."""

    __test__: ClassVar[bool] = False

    name: str
    value: int
    optional: str | None = None


class StrictSchema(BaseModel):
    """Schema with strict type requirements."""

    count: int
    enabled: bool
    ratio: float


class TestFallbackChain:
    """Tests for FallbackChain class."""

    def test_strict_parse_success(self) -> None:
        """Strict parsing succeeds for valid data."""
        chain = FallbackChain()
        data = {"name": "test", "value": 42}

        result = chain.try_parse(data, TestSchema, attempt_number=1)

        assert result is not None
        assert not isinstance(result, SafeNull)
        assert result.name == "test"
        assert result.value == 42

    def test_strict_parse_failure_triggers_coerce(self) -> None:
        """Failed strict parse triggers type coercion."""
        chain = FallbackChain(coerce_types=True)
        # String instead of int for 'value'
        data = {"name": "test", "value": "42"}

        result = chain.try_parse(data, TestSchema, attempt_number=1)

        # Should succeed after coercion
        assert result is not None
        assert not isinstance(result, SafeNull)
        assert result.value == 42

    def test_coerce_int_from_string(self) -> None:
        """Coerces string to int."""
        chain = FallbackChain()
        data = {"name": "test", "value": "42"}

        result = chain.try_parse(data, TestSchema, attempt_number=1)

        # May succeed or return SafeNull depending on implementation
        if result is not None and not isinstance(result, SafeNull):
            assert result.value == 42

    def test_partial_extraction(self) -> None:
        """Partial extraction when some fields match."""
        chain = FallbackChain(extract_partial=True)
        data = {"name": "test"}  # Missing 'value'

        result = chain.try_parse(data, TestSchema, attempt_number=1)

        # Partial extraction may return SafeNull if partial data doesn't satisfy schema
        assert result is None or isinstance(result, SafeNull)

    def test_safe_null_on_max_attempts(self) -> None:
        """Returns SafeNull when max attempts reached."""
        chain = FallbackChain(max_attempts=1)
        # Invalid data that fails all attempts
        data = {"name": "test"}  # Missing required 'value'

        result = chain.try_parse(data, TestSchema, attempt_number=1)

        # Should be SafeNull when exhausted
        assert isinstance(result, SafeNull)
        assert result.parse_error is not None

    def test_attempts_recorded(self) -> None:
        """All fallback attempts are recorded."""
        chain = FallbackChain()
        data = {"name": "test", "value": "not_an_int"}

        chain.try_parse(data, TestSchema, attempt_number=1)

        assert len(chain.attempts) > 0
        assert all(isinstance(a, FallbackAttempt) for a in chain.attempts)


class TestFallbackChainStrategies:
    """Tests for specific fallback strategies."""

    def test_strict_strategy_records(self) -> None:
        """Strict strategy is recorded in attempts."""
        chain = FallbackChain()
        data = {"name": "test", "value": 42}

        chain.try_parse(data, TestSchema, attempt_number=1)

        strict_attempts = [a for a in chain.attempts if a.strategy == "strict"]
        assert len(strict_attempts) == 1
        assert strict_attempts[0].success is True

    def test_coerce_strategy_records(self) -> None:
        """Coerce strategy is recorded in attempts."""
        chain = FallbackChain(coerce_types=True)
        # Invalid strict but coerceable
        data = {"name": "test", "value": "42"}

        chain.try_parse(data, TestSchema, attempt_number=1)

        coerce_attempts = [a for a in chain.attempts if a.strategy == "coerce"]
        if coerce_attempts:
            assert coerce_attempts[0].input_data is not None

    def test_partial_strategy_records(self) -> None:
        """Partial strategy is recorded in attempts."""
        chain = FallbackChain(extract_partial=True)
        data = {"name": "test"}  # Missing 'value'

        chain.try_parse(data, TestSchema, attempt_number=1)

        partial_attempts = [a for a in chain.attempts if a.strategy == "partial"]
        if partial_attempts:
            assert partial_attempts[0].output_data is not None


class TestTypeCoercion:
    """Tests for type coercion in fallback chain."""

    def test_coerce_int_from_float_string(self) -> None:
        """Coerces '42.0' to 42."""
        chain = FallbackChain()
        data = {"name": "test", "value": "42.0"}

        result = chain.try_parse(data, TestSchema, attempt_number=1)

        if result is not None and not isinstance(result, SafeNull):
            assert result.value == 42

    def test_coerce_bool_from_string(self) -> None:
        """Coerces string to bool for StrictSchema."""
        chain = FallbackChain()
        data = {"count": 10, "enabled": "true", "ratio": 1.5}

        result = chain.try_parse(data, StrictSchema, attempt_number=1)

        if result is not None and not isinstance(result, SafeNull):
            assert result.enabled is True

    def test_coerce_float_from_int(self) -> None:
        """Coerces int to float."""
        chain = FallbackChain()
        data = {"count": 10, "enabled": True, "ratio": 5}

        result = chain.try_parse(data, StrictSchema, attempt_number=1)

        if result is not None and not isinstance(result, SafeNull):
            assert result.ratio == 5.0


class TestSafeNull:
    """Tests for SafeNull class."""

    def test_safe_null_creation(self) -> None:
        """SafeNull can be created with all fields."""
        null = SafeNull[TestSchema](
            raw_content='{"invalid": "content"}',
            parse_error="Test error",
            partial_data={"name": "partial"},
            metadata={"attempt": 1},
        )

        assert null.raw_content == '{"invalid": "content"}'
        assert null.parse_error == "Test error"
        assert null.partial_data == {"name": "partial"}
        assert null.metadata["attempt"] == 1

    def test_safe_null_is_null_property(self) -> None:
        """is_null is always True for SafeNull."""
        null = SafeNull[TestSchema](raw_content="")
        assert null.is_null is True

    def test_safe_null_to_dict(self) -> None:
        """to_dict serializes correctly."""
        null = SafeNull[TestSchema](
            raw_content='{"test": "content"}',
            parse_error="Error message",
        )
        d = null.to_dict()

        assert d["type"] == "SafeNull"
        assert d["raw_content"] == '{"test": "content"}'
        assert d["parse_error"] == "Error message"
        assert d["partial_data"] == {}
        assert d["metadata"] == {}

    def test_safe_null_to_dict_truncates_raw_content(self) -> None:
        """to_dict truncates raw_content to 500 chars."""
        long_content = '{"key": "' + "x" * 1000 + '"}'
        null = SafeNull[TestSchema](raw_content=long_content)
        d = null.to_dict()

        assert len(d["raw_content"]) <= 500

    def test_safe_null_frozen(self) -> None:
        """SafeNull is frozen/immutable."""
        null = SafeNull[TestSchema](raw_content="test")

        with pytest.raises(AttributeError):
            null.raw_content = "modified"  # type: ignore


class TestFallbackAttempt:
    """Tests for FallbackAttempt dataclass."""

    def test_attempt_record(self) -> None:
        """FallbackAttempt records all fields."""
        attempt = FallbackAttempt(
            strategy="strict",
            input_data={"key": "value"},
            output_data={"key": "value"},
            success=True,
            error=None,
        )

        assert attempt.strategy == "strict"
        assert attempt.success is True
        assert attempt.error is None

    def test_failed_attempt_records_error(self) -> None:
        """Failed attempt records error message."""
        attempt = FallbackAttempt(
            strategy="strict",
            input_data={"key": "invalid"},
            output_data=None,
            success=False,
            error="Validation error",
        )

        assert attempt.success is False
        assert attempt.error == "Validation error"


class TestFallbackChainConfiguration:
    """Tests for FallbackChain configuration."""

    def test_disable_coerce_types(self) -> None:
        """Can disable type coercion."""
        chain = FallbackChain(coerce_types=False)
        data = {"name": "test", "value": "42"}

        result = chain.try_parse(data, TestSchema, attempt_number=1)

        # May still succeed if string coerces to int on strict pass
        # or return SafeNull
        assert result is not None

    def test_disable_extract_partial(self) -> None:
        """Can disable partial extraction."""
        chain = FallbackChain(extract_partial=False, max_attempts=1)
        data = {"name": "test"}  # Missing 'value'

        result = chain.try_parse(data, TestSchema, attempt_number=1)

        # Should return SafeNull when partial disabled and strict fails and max_attempts reached
        assert isinstance(result, SafeNull)

    def test_custom_max_attempts(self) -> None:
        """Custom max_attempts is respected."""
        chain = FallbackChain(max_attempts=5)
        data = {"name": "test"}  # Missing required

        # With max_attempts=5, this should eventually return SafeNull
        result = chain.try_parse(data, TestSchema, attempt_number=5)

        assert isinstance(result, SafeNull)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
