"""Tests for polaris.cells.roles.kernel.internal.testing.exceptions."""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.testing.exceptions import (
    FakeLLMExhaustedError,
    FakeToolExecutionError,
    FakeToolNotFoundError,
    HarnessConfigurationError,
    TestingInfrastructureError,
)


class TestTestingInfrastructureError:
    """Tests for TestingInfrastructureError base class."""

    def test_is_exception_subclass(self) -> None:
        assert issubclass(TestingInfrastructureError, Exception)

    def test_raise_with_message(self) -> None:
        with pytest.raises(TestingInfrastructureError) as exc_info:
            raise TestingInfrastructureError("base error")
        assert str(exc_info.value) == "base error"

    def test_raise_without_message(self) -> None:
        with pytest.raises(TestingInfrastructureError):
            raise TestingInfrastructureError()

    def test_can_catch_as_exception(self) -> None:
        caught = False
        try:
            raise TestingInfrastructureError("test")
        except TestingInfrastructureError as e:
            caught = True
            assert isinstance(e, TestingInfrastructureError)
        assert caught

    def test_is_base_for_other_errors(self) -> None:
        assert issubclass(FakeLLMExhaustedError, TestingInfrastructureError)
        assert issubclass(FakeToolNotFoundError, TestingInfrastructureError)
        assert issubclass(FakeToolExecutionError, TestingInfrastructureError)
        assert issubclass(HarnessConfigurationError, TestingInfrastructureError)

    def test_catch_subclass_as_base(self) -> None:
        with pytest.raises(TestingInfrastructureError):
            raise FakeLLMExhaustedError(5)


class TestFakeLLMExhaustedError:
    """Tests for FakeLLMExhaustedError."""

    def test_create_with_call_count(self) -> None:
        err = FakeLLMExhaustedError(call_count=5)
        assert err.call_count == 5
        assert "5" in str(err)
        assert "exhausted" in str(err).lower()

    def test_create_with_zero_call_count(self) -> None:
        err = FakeLLMExhaustedError(call_count=0)
        assert err.call_count == 0
        assert "0" in str(err)

    def test_create_with_large_call_count(self) -> None:
        err = FakeLLMExhaustedError(call_count=999999)
        assert err.call_count == 999999

    def test_message_contains_call_count(self) -> None:
        err = FakeLLMExhaustedError(call_count=42)
        assert "42" in str(err)
        assert "FakeLLM exhausted" in str(err)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(FakeLLMExhaustedError) as exc_info:
            raise FakeLLMExhaustedError(call_count=3)
        assert exc_info.value.call_count == 3

    def test_is_testing_infrastructure_error(self) -> None:
        err = FakeLLMExhaustedError(call_count=1)
        assert isinstance(err, TestingInfrastructureError)


class TestFakeToolNotFoundError:
    """Tests for FakeToolNotFoundError."""

    def test_create_with_tool_name(self) -> None:
        err = FakeToolNotFoundError(tool_name="search_code")
        assert err.tool_name == "search_code"
        assert "search_code" in str(err)

    def test_create_with_empty_tool_name(self) -> None:
        err = FakeToolNotFoundError(tool_name="")
        assert err.tool_name == ""

    def test_create_with_special_tool_name(self) -> None:
        err = FakeToolNotFoundError(tool_name="tool-with-dashes_and_123")
        assert err.tool_name == "tool-with-dashes_and_123"

    def test_message_contains_tool_name(self) -> None:
        err = FakeToolNotFoundError(tool_name="my_tool")
        assert "my_tool" in str(err)
        assert "not found" in str(err).lower()

    def test_raise_and_catch(self) -> None:
        with pytest.raises(FakeToolNotFoundError) as exc_info:
            raise FakeToolNotFoundError(tool_name="missing_tool")
        assert exc_info.value.tool_name == "missing_tool"

    def test_is_testing_infrastructure_error(self) -> None:
        err = FakeToolNotFoundError(tool_name="x")
        assert isinstance(err, TestingInfrastructureError)


class TestFakeToolExecutionError:
    """Tests for FakeToolExecutionError."""

    def test_create_with_tool_name_and_error(self) -> None:
        original = ValueError("something went wrong")
        err = FakeToolExecutionError(tool_name="read_file", original_error=original)
        assert err.tool_name == "read_file"
        assert err.original_error is original
        assert "something went wrong" in str(err)

    def test_create_with_different_error_types(self) -> None:
        for exc in [ValueError("val"), TypeError("type"), RuntimeError("run")]:
            err = FakeToolExecutionError(tool_name="t", original_error=exc)
            assert err.original_error is exc

    def test_message_contains_tool_name(self) -> None:
        err = FakeToolExecutionError(tool_name="write_file", original_error=Exception("fail"))
        assert "write_file" in str(err)

    def test_message_contains_original_error(self) -> None:
        err = FakeToolExecutionError(tool_name="t", original_error=ValueError("original"))
        assert "original" in str(err)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(FakeToolExecutionError) as exc_info:
            raise FakeToolExecutionError(tool_name="t", original_error=RuntimeError("boom"))
        assert exc_info.value.tool_name == "t"
        assert str(exc_info.value.original_error) == "boom"

    def test_is_testing_infrastructure_error(self) -> None:
        err = FakeToolExecutionError(tool_name="t", original_error=Exception("e"))
        assert isinstance(err, TestingInfrastructureError)


class TestHarnessConfigurationError:
    """Tests for HarnessConfigurationError."""

    def test_is_exception_subclass(self) -> None:
        assert issubclass(HarnessConfigurationError, Exception)

    def test_raise_with_message(self) -> None:
        with pytest.raises(HarnessConfigurationError) as exc_info:
            raise HarnessConfigurationError("misconfigured")
        assert str(exc_info.value) == "misconfigured"

    def test_raise_without_message(self) -> None:
        with pytest.raises(HarnessConfigurationError):
            raise HarnessConfigurationError()

    def test_is_testing_infrastructure_error(self) -> None:
        err = HarnessConfigurationError("test")
        assert isinstance(err, TestingInfrastructureError)

    def test_catch_as_base(self) -> None:
        with pytest.raises(TestingInfrastructureError):
            raise HarnessConfigurationError("test")


class TestExceptionHierarchy:
    """Tests for the complete exception hierarchy."""

    def test_all_inherit_from_testing_infrastructure_error(self) -> None:
        errors = [
            FakeLLMExhaustedError(1),
            FakeToolNotFoundError("x"),
            FakeToolExecutionError("x", Exception()),
            HarnessConfigurationError("x"),
        ]
        for err in errors:
            assert isinstance(err, TestingInfrastructureError)
            assert isinstance(err, Exception)

    def test_distinct_types(self) -> None:
        err1 = FakeLLMExhaustedError(1)
        err2 = FakeToolNotFoundError("x")
        assert type(err1) is not type(err2)


class TestModuleExports:
    """Tests for module __all__ exports."""

    def test_all_exports_present(self) -> None:
        from polaris.cells.roles.kernel.internal.testing import exceptions as mod

        assert hasattr(mod, "__all__")
        assert "FakeLLMExhaustedError" in mod.__all__
        assert "FakeToolExecutionError" in mod.__all__
        assert "FakeToolNotFoundError" in mod.__all__
        assert "HarnessConfigurationError" in mod.__all__
        assert "TestingInfrastructureError" in mod.__all__
        assert len(mod.__all__) == 5
