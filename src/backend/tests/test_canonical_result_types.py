"""Tests for the canonical Result[T, E] type in master_types.py.

Covers:
- Result[T, E] basic ok/err construction
- TaggedError: migration helper with code/message/category
- KernelError: canonical error type with category, code, message, context, retryable
- ErrorCategory enum values
- KernelOneError base exception
- Serialization (to_dict)
- Unwrap and map operations
- TaggedError <-> KernelError conversion
"""

from __future__ import annotations

import pytest
from polaris.kernelone.contracts.technical import (
    ErrorCategory,
    KernelError,
    KernelOneError,
    Result,
    TaggedError,
)

# ---------------------------------------------------------------------------
# Result[T, E] construction
# ---------------------------------------------------------------------------


class TestCanonicalResultConstruction:
    def test_ok_with_value(self) -> None:
        r: Result[int, TaggedError] = Result.ok(42)
        assert r.is_ok is True
        assert r.is_err is False
        assert r.value == 42
        assert r.error is None
        assert r.error_message == ""

    def test_ok_without_value(self) -> None:
        r: Result[None, TaggedError] = Result.ok(None)
        assert r.is_ok is True
        assert r.value is None

    def test_err_with_tagged_error(self) -> None:
        r: Result[int, TaggedError] = Result.err(
            TaggedError("NOT_FOUND", "Record not found")
        )
        assert r.is_ok is False
        assert r.is_err is True
        assert r.error is not None
        assert r.error.code == "NOT_FOUND"
        assert r.error.message == "Record not found"
        assert r.error.category == ErrorCategory.NOT_FOUND

    def test_err_with_kernel_error(self) -> None:
        r: Result[int, KernelError] = Result.err(
            KernelError(
                category=ErrorCategory.INTERNAL_ERROR,
                code="INTERNAL_ERROR",
                message="Something went wrong",
            )
        )
        assert r.is_ok is False
        assert r.error is not None
        assert isinstance(r.error, KernelError)
        assert r.error.category == ErrorCategory.INTERNAL_ERROR

    def test_err_with_message_only(self) -> None:
        r: Result[str, TaggedError] = Result.err(
            TaggedError("INVALID_ARGUMENT", "bad value"), message="Details here"
        )
        assert r.is_ok is False
        assert r.error_message == "Details here"

    def test_frozen_immutable(self) -> None:
        r: Result[int, TaggedError] = Result.ok(1)
        with pytest.raises(AttributeError):
            r.is_ok = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Result accessors
# ---------------------------------------------------------------------------


class TestCanonicalResultAccessors:
    def test_unwrap_on_ok(self) -> None:
        r: Result[str, TaggedError] = Result.ok("hello")
        assert r.unwrap() == "hello"

    def test_unwrap_on_none_ok(self) -> None:
        r: Result[None, TaggedError] = Result.ok(None)
        # Canonical unwrap() raises on None value (safe: avoids silent None bugs)
        with pytest.raises(KernelOneError, match="Result.unwrap"):
            r.unwrap()

    def test_unwrap_raises_on_err(self) -> None:
        r: Result[int, TaggedError] = Result.err(
            TaggedError("NOT_FOUND", "not found")
        )
        with pytest.raises(KernelOneError, match="Result.unwrap"):
            r.unwrap()

    def test_unwrap_raises_on_err_with_message(self) -> None:
        r: Result[int, TaggedError] = Result.err(
            TaggedError("NOT_FOUND", "record gone")
        )
        with pytest.raises(KernelOneError, match="record gone"):
            r.unwrap()

    def test_unwrap_or_returns_value_on_ok(self) -> None:
        r: Result[int, TaggedError] = Result.ok(5)
        assert r.unwrap_or(99) == 5

    def test_unwrap_or_returns_default_on_err(self) -> None:
        r: Result[int, TaggedError] = Result.err(
            TaggedError("UNKNOWN", "bad")
        )
        assert r.unwrap_or(99) == 99


# ---------------------------------------------------------------------------
# Result chaining
# ---------------------------------------------------------------------------


class TestCanonicalResultMap:
    def test_map_on_ok(self) -> None:
        r: Result[int, TaggedError] = Result.ok(3)
        doubled = r.map(lambda x: x * 2)
        assert doubled.is_ok
        assert doubled.value == 6

    def test_map_on_none_value(self) -> None:
        # Canonical map() skips fn when value is None (safe None propagation)
        r: Result[None, TaggedError] = Result.ok(None)
        mapped = r.map(lambda _: 42)
        assert mapped.is_ok
        assert mapped.value is None

    def test_map_on_err_passes_through(self) -> None:
        tag = TaggedError("NOT_FOUND", "gone")
        r: Result[int, TaggedError] = Result.err(tag)
        mapped = r.map(lambda x: x * 2)
        assert mapped.is_err
        assert mapped.error is tag
        assert mapped.error_message == "gone"

    def test_map_propagates_exception(self) -> None:
        r: Result[int, TaggedError] = Result.ok(1)

        def boom(_: int) -> int:
            raise ValueError("boom")

        mapped = r.map(boom)
        assert mapped.is_err
        assert "boom" in mapped.error_message

    def test_map_preserves_error_type(self) -> None:
        tag = TaggedError("INTERNAL", "oops")
        r: Result[int, TaggedError] = Result.err(tag)
        mapped = r.map(lambda x: x * 2)
        assert mapped.is_err
        # error_message falls back to TaggedError.message for ergonomics
        assert mapped.error_message == "oops"


# ---------------------------------------------------------------------------
# Result serialization
# ---------------------------------------------------------------------------


class TestCanonicalResultSerialization:
    def test_to_dict_ok(self) -> None:
        r: Result[str, TaggedError] = Result.ok("hello")
        d = r.to_dict()
        assert d["ok"] is True
        assert d["value"] == "hello"

    def test_to_dict_err(self) -> None:
        r: Result[int, TaggedError] = Result.err(
            TaggedError("NOT_FOUND", "not found")
        )
        d = r.to_dict()
        assert d["ok"] is False
        assert d["error"] is not None
        assert d["error_message"] == "not found"

    def test_round_trip_ok(self) -> None:
        original: Result[int, TaggedError] = Result.ok(123)
        restored = Result.from_dict(original.to_dict())  # type: ignore[arg-type]
        assert restored.is_ok
        assert restored.value == 123


# ---------------------------------------------------------------------------
# TaggedError
# ---------------------------------------------------------------------------


class TestTaggedError:
    def test_basic_construction(self) -> None:
        e = TaggedError("REVIEW_NOT_FOUND", "Review record not found")
        assert e.code == "REVIEW_NOT_FOUND"
        assert e.message == "Review record not found"
        assert e.category == ErrorCategory.NOT_FOUND

    def test_code_mapping_all_standard_codes(self) -> None:
        # Standard codes
        assert TaggedError("NOT_FOUND", "").category == ErrorCategory.NOT_FOUND
        assert TaggedError("ALREADY_EXISTS", "").category == ErrorCategory.ALREADY_EXISTS
        assert TaggedError("INVALID_ARGUMENT", "").category == ErrorCategory.INVALID_INPUT
        assert TaggedError("INTERNAL_ERROR", "").category == ErrorCategory.INTERNAL_ERROR
        assert TaggedError("PERMISSION_DENIED", "").category == ErrorCategory.PERMISSION_DENIED

    def test_unknown_code_maps_to_unknown_category(self) -> None:
        e = TaggedError("SOME_WEIRD_CODE", "whatever")
        assert e.category == ErrorCategory.UNKNOWN

    def test_to_kernel_error(self) -> None:
        e = TaggedError("REVIEW_NOT_FOUND", "Review gone")
        ke = e.to_kernel_error()
        assert isinstance(ke, KernelError)
        assert ke.category == ErrorCategory.NOT_FOUND
        assert ke.code == "REVIEW_NOT_FOUND"
        assert ke.message == "Review gone"
        assert ke.retryable is False

    def test_str_representation(self) -> None:
        e = TaggedError("NOT_FOUND", "gone")
        assert str(e) == "[NOT_FOUND] gone"

    def test_repr(self) -> None:
        e = TaggedError("FOO", "bar")
        assert repr(e) == "TaggedError('FOO', 'bar')"

    def test_equality(self) -> None:
        a = TaggedError("CODE", "msg")
        b = TaggedError("CODE", "msg")
        c = TaggedError("CODE", "other")
        d = TaggedError("OTHER", "msg")
        assert a == b
        assert a != c
        assert a != d

    def test_hashable(self) -> None:
        s = {TaggedError("X", "y"), TaggedError("X", "y")}
        assert len(s) == 1


# ---------------------------------------------------------------------------
# KernelError
# ---------------------------------------------------------------------------


class TestKernelError:
    def test_basic_construction(self) -> None:
        ke = KernelError(
            category=ErrorCategory.NOT_FOUND,
            code="ITEM_NOT_FOUND",
            message="The requested item does not exist",
        )
        assert ke.category == ErrorCategory.NOT_FOUND
        assert ke.code == "ITEM_NOT_FOUND"
        assert ke.message == "The requested item does not exist"
        assert ke.context == {}
        assert ke.retryable is False
        assert ke.source == ""

    def test_all_fields(self) -> None:
        ke = KernelError(
            category=ErrorCategory.UNAVAILABLE,
            code="SERVICE_DOWN",
            message="Service unavailable",
            context={"host": "db-1", "port": 5432},
            retryable=True,
            source="kernelone.db",
        )
        assert ke.category == ErrorCategory.UNAVAILABLE
        assert ke.context == {"host": "db-1", "port": 5432}
        assert ke.retryable is True
        assert ke.source == "kernelone.db"

    def test_to_dict(self) -> None:
        ke = KernelError(
            category=ErrorCategory.INVALID_INPUT,
            code="BAD_INPUT",
            message="Invalid input",
        )
        d = ke.to_dict()
        assert d["category"] == "invalid_input"
        assert d["code"] == "BAD_INPUT"
        assert d["message"] == "Invalid input"
        assert d["retryable"] is False
        assert d["source"] == ""

    def test_frozen_immutable(self) -> None:
        ke = KernelError(category=ErrorCategory.UNKNOWN, message="test")
        with pytest.raises(AttributeError):
            ke.code = "CHANGED"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ErrorCategory
# ---------------------------------------------------------------------------


class TestErrorCategory:
    def test_standard_values_exist(self) -> None:
        assert ErrorCategory.UNKNOWN.value == "unknown"
        assert ErrorCategory.INVALID_INPUT.value == "invalid_input"
        assert ErrorCategory.NOT_FOUND.value == "not_found"
        assert ErrorCategory.ALREADY_EXISTS.value == "already_exists"
        assert ErrorCategory.INTERNAL_ERROR.value == "internal_error"
        assert ErrorCategory.UNAVAILABLE.value == "unavailable"

    def test_all_are_strings(self) -> None:
        for cat in ErrorCategory:
            assert isinstance(cat.value, str)


# ---------------------------------------------------------------------------
# KernelOneError exception
# ---------------------------------------------------------------------------


class TestKernelOneError:
    def test_raises_with_message(self) -> None:
        with pytest.raises(KernelOneError) as exc_info:
            raise KernelOneError("Something failed")
        assert str(exc_info.value) == "Something failed"
        assert exc_info.value.code == "KERNEL_ERROR"

    def test_with_code(self) -> None:
        with pytest.raises(KernelOneError) as exc_info:
            raise KernelOneError("Bad thing", code="CUSTOM_CODE")
        assert exc_info.value.code == "CUSTOM_CODE"


# ---------------------------------------------------------------------------
# Backward-compat shim: result.py Result still works (kept for test compat)
# ---------------------------------------------------------------------------


class TestLegacyResultStillImportable:
    """Verify the legacy Result in result.py is still accessible.

    This test exists because tests/test_unified_result_and_error_handling.py
    directly imports from result.py. Once that test is migrated, this class
    can be removed and the legacy result.py file can be deleted.
    """

    def test_legacy_result_still_importable_via_runtime(self) -> None:
        # The runtime/__init__.py re-exports canonical Result; verify no circular dep
        from polaris.kernelone.runtime import Result as RuntimeResult

        # Should be the canonical Result[T, E], not the legacy Result[T]
        r: RuntimeResult[str, TaggedError] = RuntimeResult.ok("ok")
        assert r.is_ok

    def test_legacy_result_direct_import_still_works(self) -> None:
        # Direct import from result.py should still work (for test compat)
        from polaris.kernelone.runtime.result import Result as LegacyResult

        r: LegacyResult[int] = LegacyResult.ok(1)
        assert r.is_ok
        assert r.value == 1

    def test_errorcodes_still_importable(self) -> None:
        from polaris.kernelone.runtime import ErrorCodes

        assert ErrorCodes.NOT_FOUND == "NOT_FOUND"
        assert ErrorCodes.REVIEW_NOT_FOUND == "REVIEW_NOT_FOUND"
