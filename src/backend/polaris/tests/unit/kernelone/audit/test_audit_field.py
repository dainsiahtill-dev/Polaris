"""Unit tests for polaris.kernelone.audit.audit_field."""

from __future__ import annotations

from typing import Any

from polaris.kernelone.audit.audit_field import (
    AuditFieldError,
    TypeSafeDict,
    TypeSafeList,
    audit_len,
    audit_repr,
    audit_str,
    safe_value,
)


class _HasDict:
    def __init__(self, value: int) -> None:
        self.value = value


class _HasToDict:
    def to_dict(self) -> dict[str, Any]:
        return {"nested": True}


class _BadStr:
    def __str__(self) -> str:
        raise RuntimeError("bad str")

    def __repr__(self) -> str:
        raise RuntimeError("bad repr")


class TestAuditLen:
    def test_none_returns_default(self) -> None:
        assert audit_len(None, default=42) == 42

    def test_str_length(self) -> None:
        assert audit_len("hello") == 5

    def test_list_length(self) -> None:
        assert audit_len([1, 2, 3]) == 3

    def test_dict_length(self) -> None:
        assert audit_len({"a": 1, "b": 2}) == 2

    def test_set_length(self) -> None:
        assert audit_len({1, 2, 3}) == 3

    def test_generator_fallback(self) -> None:
        gen = (x for x in range(7))
        assert audit_len(gen) == 7

    def test_unsupported_uses_str_len(self) -> None:
        class NoLen:
            def __str__(self) -> str:
                return "four"

        assert audit_len(NoLen()) == 4

    def test_unsupported_returns_default(self) -> None:
        class NoLenNoStr:
            pass

        assert audit_len(NoLenNoStr(), default=99) == 99

    def test_bytes_length(self) -> None:
        assert audit_len(b"abc") == 3


class TestAuditStr:
    def test_none_returns_default(self) -> None:
        assert audit_str(None, default="n/a") == "n/a"

    def test_basic_str(self) -> None:
        assert audit_str(123) == "123"

    def test_max_length_truncation(self) -> None:
        long_str = "a" * 100
        result = audit_str(long_str, max_length=10)
        assert result == "a" * 10 + "..."

    def test_bad_str_conversion(self) -> None:
        obj = _BadStr()
        result = audit_str(obj)
        assert "conversion failed" in result


class TestAuditRepr:
    def test_none_returns_default(self) -> None:
        assert audit_repr(None, default="n/a") == "n/a"

    def test_basic_repr(self) -> None:
        assert audit_repr([1, 2]) == "[1, 2]"

    def test_bad_repr_fallback(self) -> None:
        obj = _BadStr()
        result = audit_repr(obj)
        assert "repr failed" in result


class TestSafeValue:
    def test_primitives_passthrough(self) -> None:
        assert safe_value(None) is None
        assert safe_value(True) is True
        assert safe_value(42) == 42
        assert safe_value(3.14) == 3.14
        assert safe_value("hello") == "hello"

    def test_bytes_decode(self) -> None:
        assert safe_value(b"hello") == "hello"

    def test_bytes_decode_error(self) -> None:
        bad_bytes = b"\xff\xfe"
        result = safe_value(bad_bytes)
        assert "decode failed" in result

    def test_list_recursive(self) -> None:
        result = safe_value([1, "two", [3]])
        assert result == [1, "two", [3]]

    def test_tuple_becomes_list(self) -> None:
        result = safe_value((1, 2))
        assert result == [1, 2]

    def test_set_becomes_list(self) -> None:
        result = safe_value({1, 2})
        assert sorted(result) == [1, 2]

    def test_dict_recursive(self) -> None:
        result = safe_value({"a": 1, "b": [2]})
        assert result == {"a": 1, "b": [2]}

    def test_callable_blocked(self) -> None:
        def my_func() -> None:
            pass

        result = safe_value(my_func)
        assert result.get("__audit_method__") is True
        assert result.get("name") == "my_func"

    def test_callable_allowed(self) -> None:
        def my_func() -> None:
            pass

        result = safe_value(my_func, allow_methods=True)
        assert result is my_func

    def test_to_dict_object(self) -> None:
        obj = _HasToDict()
        result = safe_value(obj)
        assert result == {"nested": True}

    def test_dict_attr_object(self) -> None:
        obj = _HasDict(value=7)
        result = safe_value(obj)
        assert result == {"value": 7}

    def test_fallback_str(self) -> None:
        class Plain:
            def __str__(self) -> str:
                return "plain_obj"

        result = safe_value(Plain())
        assert result == "plain_obj"

    def test_field_path_propagation(self) -> None:
        result = safe_value({"key": lambda: None}, field_path="root")
        assert result["key"]["__audit_method__"] is True


class TestAuditFieldError:
    def test_to_dict(self) -> None:
        err = AuditFieldError("boom", field_path="x.y", value_type=int)
        d = err.to_dict()
        assert d["error"] == "boom"
        assert d["field_path"] == "x.y"
        assert d["value_type"] == "int"

    def test_repr(self) -> None:
        err = AuditFieldError("msg", field_path="a.b", value_type=str)
        assert "a.b" in repr(err)
        assert "str" in repr(err)


class TestTypeSafeDict:
    def test_len(self) -> None:
        d = TypeSafeDict({"items": [1, 2, 3]})
        assert d.len("items") == 3
        assert d.len("missing") == 0

    def test_get_str(self) -> None:
        d = TypeSafeDict({"name": 42})
        assert d.get_str("name") == "42"
        assert d.get_str("missing") == ""

    def test_safe(self) -> None:
        d = TypeSafeDict({"nested": {"a": 1}})
        assert d.safe("nested") == {"a": 1}


class TestTypeSafeList:
    def test_safe_get(self) -> None:
        lst = TypeSafeList([1, 2, 3])
        assert lst.safe_get(0) == 1
        assert lst.safe_get(100) is None

    def test_safe_map(self) -> None:
        lst = TypeSafeList([1, 2, 3])
        result = lst.safe_map(lambda x: x * 2)
        assert result == [2, 4, 6]

    def test_safe_map_skips_errors(self) -> None:
        lst = TypeSafeList([1, "bad", 3])
        result = lst.safe_map(lambda x: x + 1, default=-1)
        assert result[0] == 2
        assert result[1] == -1
        assert result[2] == 4
