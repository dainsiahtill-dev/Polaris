"""Tests for polaris.kernelone.audit.audit_field."""

from __future__ import annotations

from polaris.kernelone.audit.audit_field import (
    AuditFieldError,
    SafeValueResult,
    TypeSafeDict,
    TypeSafeList,
    audit_len,
    audit_repr,
    audit_str,
    safe_value,
)


class TestAuditFieldError:
    def test_to_dict(self) -> None:
        err = AuditFieldError(
            "bad type",
            field_path="root.field",
            value_type=int,
            original_error=ValueError("orig"),
            stack_summary="file.py:1",
        )
        d = err.to_dict()
        assert d["field_path"] == "root.field"
        assert d["value_type"] == "int"
        assert "orig" in d["original_error"]
        assert "file.py:1" in d["stack_summary"]

    def test_repr(self) -> None:
        err = AuditFieldError("msg", field_path="f", value_type=str)
        r = repr(err)
        assert "f" in r
        assert "str" in r


class TestSafeValueResult:
    def test_unwrap_safe(self) -> None:
        result = SafeValueResult(value=42, is_safe=True, original_type=int)
        assert result.unwrap() == 42
        assert result.unwrap(default=0) == 42

    def test_unwrap_unsafe(self) -> None:
        result = SafeValueResult(value=42, is_safe=False, original_type=int, error=AuditFieldError("x"))
        assert result.unwrap(default=99) == 99

    def test_unwrap_or_raise_safe(self) -> None:
        result = SafeValueResult(value=42, is_safe=True, original_type=int)
        assert result.unwrap_or_raise() == 42

    def test_unwrap_or_raise_unsafe(self) -> None:
        err = AuditFieldError("x")
        result = SafeValueResult(value=42, is_safe=False, original_type=int, error=err)
        with pytest.raises(AuditFieldError):
            result.unwrap_or_raise()


class TestAuditLen:
    def test_none_returns_default(self) -> None:
        assert audit_len(None) == 0
        assert audit_len(None, default=5) == 5

    def test_string(self) -> None:
        assert audit_len("hello") == 5

    def test_list(self) -> None:
        assert audit_len([1, 2, 3]) == 3

    def test_dict(self) -> None:
        assert audit_len({"a": 1, "b": 2}) == 2

    def test_set(self) -> None:
        assert audit_len({1, 2, 3}) == 3

    def test_unsupported_type_fallback(self) -> None:
        assert audit_len(42) == 2  # len(str(42)) == 2

    def test_generator_fallback(self) -> None:
        def gen():
            yield 1
            yield 2

        assert audit_len(gen()) == 2


class TestAuditStr:
    def test_none_returns_default(self) -> None:
        assert audit_str(None) == ""
        assert audit_str(None, default="n/a") == "n/a"

    def test_basic(self) -> None:
        assert audit_str(123) == "123"

    def test_max_length(self) -> None:
        long_str = "a" * 100
        result = audit_str(long_str, max_length=10)
        assert result.endswith("...")
        assert len(result) == 13


class TestAuditRepr:
    def test_none_returns_default(self) -> None:
        assert audit_repr(None) == ""

    def test_basic(self) -> None:
        assert audit_repr([1, 2]) == "[1, 2]"


class TestSafeValue:
    def test_primitives(self) -> None:
        assert safe_value(None) is None
        assert safe_value(True) is True
        assert safe_value(42) == 42
        assert safe_value(3.14) == 3.14
        assert safe_value("hello") == "hello"

    def test_bytes(self) -> None:
        assert safe_value(b"hello") == "hello"
        assert safe_value(b"\xff\xfe") == "\xff\xfe"

    def test_list(self) -> None:
        result = safe_value([1, "two", None])
        assert result == [1, "two", None]

    def test_tuple(self) -> None:
        result = safe_value((1, "two"))
        assert isinstance(result, tuple)
        assert result == (1, "two")

    def test_set(self) -> None:
        result = safe_value({1, 2})
        assert isinstance(result, list)
        assert sorted(result) == [1, 2]

    def test_dict(self) -> None:
        result = safe_value({"a": 1, "b": [2, 3]})
        assert result == {"a": 1, "b": [2, 3]}

    def test_callable_blocked(self) -> None:
        def my_func():
            pass

        result = safe_value(my_func)
        assert isinstance(result, dict)
        assert result.get("__audit_method__") is True
        assert result.get("name") == "my_func"

    def test_callable_allowed(self) -> None:
        def my_func():
            pass

        result = safe_value(my_func, allow_methods=True)
        assert result is my_func

    def test_object_with_to_dict(self) -> None:
        class Obj:
            def to_dict(self):
                return {"x": 1}

        result = safe_value(Obj())
        assert result == {"x": 1}

    def test_object_with_dict(self) -> None:
        class Obj:
            def __init__(self) -> None:
                self.x = 1

        result = safe_value(Obj())
        assert result == {"x": 1}

    def test_fallback_str(self) -> None:
        class Obj:
            def __str__(self):
                return "custom"

        result = safe_value(Obj())
        assert result == "custom"

    def test_recursive_failure_handled(self) -> None:
        class Bad:
            def __iter__(self):
                raise RuntimeError("boom")

        result = safe_value([Bad()])
        assert isinstance(result, list)
        assert result[0].get("__audit_error__") is True


class TestTypeSafeDict:
    def test_len(self) -> None:
        d = TypeSafeDict({"items": [1, 2, 3], "empty": None})
        assert d.len("items") == 3
        assert d.len("empty") == 0
        assert d.len("missing") == 0

    def test_get_str(self) -> None:
        d = TypeSafeDict({"num": 42})
        assert d.get_str("num") == "42"
        assert d.get_str("missing") == ""

    def test_safe(self) -> None:
        d = TypeSafeDict({"nested": {"a": 1}})
        assert d.safe("nested") == {"a": 1}


class TestTypeSafeList:
    def test_safe_get(self) -> None:
        lst = TypeSafeList([1, 2, 3])
        assert lst.safe_get(0) == 1
        assert lst.safe_get(10, default="x") == "x"

    def test_safe_map(self) -> None:
        lst = TypeSafeList([1, 2, 3])
        result = lst.safe_map(lambda x: x * 2)
        assert result == [2, 4, 6]

    def test_safe_map_with_errors(self) -> None:
        lst = TypeSafeList([1, "bad", 3])
        result = lst.safe_map(lambda x: x * 2, default=-1)
        assert result[0] == 2
        assert result[2] == 6


import pytest  # noqa: E402
