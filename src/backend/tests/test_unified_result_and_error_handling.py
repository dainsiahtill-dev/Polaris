"""Tests for the unified Result type in kernelone.runtime.result.

Covers:
- Basic ok/err construction
- Chaining (map, and_then)
- Serialization round-trip (to_dict / from_dict)
- from_exception factory
- ErrorCodes constants
- QAAgent tool methods using Result pattern (isolated via duck-typing stubs)
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from polaris.kernelone.runtime.result import ErrorCodes, Result

# ---------------------------------------------------------------------------
# Result basic construction
# ---------------------------------------------------------------------------


class TestResultConstruction:
    def test_ok_with_value(self) -> None:
        r: Result[int] = Result.ok(42)
        assert r.is_ok is True
        assert r.is_err is False
        assert r.value == 42

    def test_ok_without_value(self) -> None:
        r: Result[None] = Result.ok(None)
        assert r.is_ok is True
        assert r.value is None

    def test_err_basic(self) -> None:
        r = Result.err("not found", code=ErrorCodes.NOT_FOUND)
        assert r.is_ok is False
        assert r.is_err is True
        assert r.error_message == "not found"
        assert r.error_code == ErrorCodes.NOT_FOUND

    def test_err_default_code(self) -> None:
        r = Result.err("something broke")
        assert r.error_code == "UNKNOWN_ERROR"

    def test_err_with_details(self) -> None:
        details = {"task_id": "abc-123", "worker": "w1"}
        r = Result.err("task failed", code="TASK_FAILED", details=details)
        assert r.error_details == details

    def test_err_details_are_empty_dict_by_default(self) -> None:
        r = Result.err("bad")
        assert r.error_details == {}


# ---------------------------------------------------------------------------
# Result accessors
# ---------------------------------------------------------------------------


class TestResultAccessors:
    def test_ok_value_on_ok(self) -> None:
        r: Result[str] = Result.ok("hello")
        assert r.ok_value == "hello"

    def test_ok_value_raises_on_err(self) -> None:
        r = Result.err("bad", code="E")
        with pytest.raises(RuntimeError, match="Cannot get ok_value from Err"):
            _ = r.ok_value

    def test_err_value_on_err(self) -> None:
        r = Result.err("not found", code="NF")
        code, message = r.err_value
        assert code == "NF"
        assert message == "not found"

    def test_err_value_raises_on_ok(self) -> None:
        r: Result[int] = Result.ok(1)
        with pytest.raises(RuntimeError, match="Cannot get err_value from Ok"):
            _ = r.err_value

    def test_unwrap_or_returns_value_on_ok(self) -> None:
        r: Result[int] = Result.ok(5)
        assert r.unwrap_or(99) == 5

    def test_unwrap_or_returns_default_on_err(self) -> None:
        r: Result[int] = Result.err("bad")
        assert r.unwrap_or(99) == 99

    def test_unwrap_or_else(self) -> None:
        r: Result[int] = Result.err("bad")
        assert r.unwrap_or_else(lambda: 42) == 42


# ---------------------------------------------------------------------------
# Result chaining
# ---------------------------------------------------------------------------


class TestResultChaining:
    def test_map_on_ok(self) -> None:
        r: Result[int] = Result.ok(3)
        doubled = r.map(lambda x: x * 2)
        assert doubled.is_ok
        assert doubled.value == 6

    def test_map_on_err_passes_through(self) -> None:
        r: Result[int] = Result.err("bad", code="E")
        mapped = r.map(lambda x: x * 2)
        assert mapped.is_err
        assert mapped.error_code == "E"
        assert mapped.error_message == "bad"

    def test_and_then_chains_on_ok(self) -> None:
        def safe_div(x: int) -> Result[float]:
            if x == 0:
                return Result.err("division by zero", code="MATH")
            return Result.ok(100.0 / x)

        r: Result[int] = Result.ok(4)
        result = r.and_then(safe_div)
        assert result.is_ok
        assert result.value == pytest.approx(25.0)

    def test_and_then_short_circuits_on_err(self) -> None:
        def should_not_be_called(_: int) -> Result[float]:
            raise AssertionError("Should not be called")

        r: Result[int] = Result.err("upstream error", code="UPSTREAM")
        result = r.and_then(should_not_be_called)
        assert result.is_err
        assert result.error_code == "UPSTREAM"

    def test_and_then_propagates_inner_error(self) -> None:
        def always_err(_: int) -> Result[float]:
            return Result.err("inner failure", code="INNER")

        r: Result[int] = Result.ok(10)
        result = r.and_then(always_err)
        assert result.is_err
        assert result.error_code == "INNER"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestResultSerialization:
    def test_to_dict_ok(self) -> None:
        r: Result[str] = Result.ok("hello")
        d = r.to_dict()
        assert d["ok"] is True
        assert d["value"] == "hello"
        assert "error" not in d

    def test_to_dict_err(self) -> None:
        r = Result.err("not found", code="NF", details={"id": 42})
        d = r.to_dict()
        assert d["ok"] is False
        assert d["error"] == "not found"
        assert d["error_code"] == "NF"
        assert d["error_details"] == {"id": 42}

    def test_to_dict_err_omits_empty_details(self) -> None:
        r = Result.err("bad")
        d = r.to_dict()
        assert "error_details" not in d

    def test_from_dict_ok(self) -> None:
        d: dict[str, Any] = {"ok": True, "value": 99}
        r = Result.from_dict(d)
        assert r.is_ok
        assert r.value == 99

    def test_from_dict_err(self) -> None:
        d: dict[str, Any] = {
            "ok": False,
            "error": "not found",
            "error_code": "NF",
            "error_details": {"item": "abc"},
        }
        r = Result.from_dict(d)
        assert r.is_err
        assert r.error_message == "not found"
        assert r.error_code == "NF"
        assert r.error_details == {"item": "abc"}

    def test_round_trip_ok(self) -> None:
        original: Result[int] = Result.ok(123)
        restored = Result.from_dict(original.to_dict())
        assert restored.is_ok
        assert restored.value == 123

    def test_round_trip_err(self) -> None:
        original = Result.err("broken", code="BRK", details={"x": 1})
        restored = Result.from_dict(original.to_dict())
        assert restored.is_err
        assert restored.error_message == "broken"
        assert restored.error_code == "BRK"
        assert restored.error_details == {"x": 1}


# ---------------------------------------------------------------------------
# from_exception factory
# ---------------------------------------------------------------------------


class TestFromException:
    def test_basic(self) -> None:
        exc = ValueError("bad value")
        r = Result.from_exception(exc, code="VAL_ERR")
        assert r.is_err
        assert r.error_message == "bad value"
        assert r.error_code == "VAL_ERR"
        assert r.error_details["exception_type"] == "ValueError"

    def test_default_code(self) -> None:
        r = Result.from_exception(RuntimeError("oops"))
        assert r.error_code == "EXCEPTION"

    def test_with_context(self) -> None:
        r = Result.from_exception(
            OSError("file not found"),
            code="IO_ERR",
            context={"path": "/tmp/test.txt"},
        )
        assert r.error_details["path"] == "/tmp/test.txt"
        assert r.error_details["exception_type"] == "OSError"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestResultLogging:
    def test_log_ok_does_not_raise(self, caplog: pytest.LogCaptureFixture) -> None:
        r: Result[str] = Result.ok("hello")
        with caplog.at_level(logging.DEBUG, logger="polaris.kernelone.runtime.result"):
            returned = r.log(level=logging.DEBUG)
        assert returned is r

    def test_log_err_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        r = Result.err("bad thing", code="BAD")
        with caplog.at_level(logging.WARNING, logger="polaris.kernelone.runtime.result"):
            r.log(include_details=False)
        assert any("bad thing" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# ErrorCodes constants
# ---------------------------------------------------------------------------


class TestErrorCodes:
    def test_standard_codes_exist(self) -> None:
        assert ErrorCodes.NOT_FOUND == "NOT_FOUND"
        assert ErrorCodes.INTERNAL_ERROR == "INTERNAL_ERROR"
        assert ErrorCodes.UNKNOWN_ERROR == "UNKNOWN_ERROR"
        assert ErrorCodes.REVIEW_NOT_FOUND == "REVIEW_NOT_FOUND"
        assert ErrorCodes.AGENT_NOT_FOUND == "AGENT_NOT_FOUND"
        assert ErrorCodes.PROTOCOL_ERROR == "PROTOCOL_ERROR"

    def test_all_codes_are_strings(self) -> None:
        for attr in dir(ErrorCodes):
            if attr.startswith("_"):
                continue
            value = getattr(ErrorCodes, attr)
            if callable(value):
                continue
            assert isinstance(value, str), f"ErrorCodes.{attr} should be str"


# ---------------------------------------------------------------------------
# QAAgent _mark_review using Result (integration-level unit tests)
# ---------------------------------------------------------------------------


class StubMemory:
    def append_history(self, _: Any) -> None:
        pass

    def save_snapshot(self, _: Any) -> None:
        pass


class StubToolbox:
    def register(self, *args: Any, **kwargs: Any) -> None:
        pass


class StubMessageQueue:
    def receive(self, **kwargs: Any) -> None:
        return None

    def send(self, _: Any) -> None:
        pass

    def pending_count(self) -> int:
        return 0


class MinimalQAAgentForTest:
    """Minimal stand-in to test _mark_review logic without full runtime deps."""

    def __init__(self) -> None:
        self.agent_name = "QA"
        self._reviews: dict[str, Any] = {}
        self.memory = StubMemory()
        self.toolbox = StubToolbox()
        self.message_queue = StubMessageQueue()

    def _new_review_id(self) -> str:
        return "review-test01"

    def _persist_reviews_snapshot(self) -> None:
        pass

    # Import _mark_review logic inline to test it without full deps
    def _mark_review(self, review_id: str, status: str, *, feedback: str = "") -> Result[Any]:
        review = self._reviews.get(review_id)
        if not review:
            return Result.err(
                f"Review {review_id} not found",
                code=ErrorCodes.REVIEW_NOT_FOUND,
            )
        review["status"] = status
        review["feedback"] = feedback
        return Result.ok(review)


class TestMarkReviewResultPattern:
    def test_mark_review_ok(self) -> None:
        agent = MinimalQAAgentForTest()
        agent._reviews["r1"] = {"review_id": "r1", "status": "pending", "feedback": ""}
        result = agent._mark_review("r1", "approved", feedback="Looks good")
        assert result.is_ok
        assert result.ok_value["status"] == "approved"

    def test_mark_review_not_found_returns_err(self) -> None:
        agent = MinimalQAAgentForTest()
        result = agent._mark_review("nonexistent", "approved")
        assert result.is_err
        assert result.error_code == ErrorCodes.REVIEW_NOT_FOUND
        assert "nonexistent" in result.error_message

    def test_err_result_is_not_exception(self) -> None:
        """Verify that error handling does NOT raise an exception."""
        agent = MinimalQAAgentForTest()
        try:
            result = agent._mark_review("bad-id", "approved")
        except Exception as e:
            pytest.fail(f"_mark_review raised unexpectedly: {e}")
        assert result.is_err
