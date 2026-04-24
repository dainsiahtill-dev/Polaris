"""Tests for polaris.cells.roles.kernel.internal.testing.exceptions."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.testing.exceptions import (
    FakeLLMExhaustedError,
    FakeToolExecutionError,
    FakeToolNotFoundError,
    HarnessConfigurationError,
    TestingInfrastructureError,
)


class TestTestingInfrastructureError:
    def test_is_exception(self) -> None:
        assert issubclass(TestingInfrastructureError, Exception)


class TestFakeLLMExhaustedError:
    def test_message(self) -> None:
        err = FakeLLMExhaustedError(5)
        assert "5" in str(err)
        assert err.call_count == 5

    def test_is_testing_error(self) -> None:
        assert issubclass(FakeLLMExhaustedError, TestingInfrastructureError)


class TestFakeToolNotFoundError:
    def test_message(self) -> None:
        err = FakeToolNotFoundError("my_tool")
        assert "my_tool" in str(err)
        assert err.tool_name == "my_tool"


class TestFakeToolExecutionError:
    def test_message(self) -> None:
        original = RuntimeError("boom")
        err = FakeToolExecutionError("my_tool", original)
        assert "my_tool" in str(err)
        assert err.tool_name == "my_tool"
        assert err.original_error is original


class TestHarnessConfigurationError:
    def test_is_testing_error(self) -> None:
        assert issubclass(HarnessConfigurationError, TestingInfrastructureError)
