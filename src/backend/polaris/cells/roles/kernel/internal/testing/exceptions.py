"""Exceptions for testing infrastructure.

# -*- coding: utf-8 -*-
UTF-8 encoding verified: All text uses UTF-8
"""

from __future__ import annotations

from typing import ClassVar


class TestingInfrastructureError(Exception):
    """Base exception for testing infrastructure errors."""

    __test__: ClassVar[bool] = False


class FakeLLMExhaustedError(TestingInfrastructureError):
    """Raised when fake LLM runs out of pre-programmed responses."""

    def __init__(self, call_count: int) -> None:
        super().__init__(f"FakeLLM exhausted after {call_count} calls. No more responses programmed.")
        self.call_count = call_count


class FakeToolNotFoundError(TestingInfrastructureError):
    """Raised when a tool is invoked that hasn't been registered."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Tool '{tool_name}' not found in fake executor. Did you forget to register it?")
        self.tool_name = tool_name


class FakeToolExecutionError(TestingInfrastructureError):
    """Raised when a fake tool handler raises an exception."""

    def __init__(self, tool_name: str, original_error: Exception) -> None:
        super().__init__(f"Tool '{tool_name}' execution failed: {original_error}")
        self.tool_name = tool_name
        self.original_error = original_error


class HarnessConfigurationError(TestingInfrastructureError):
    """Raised when test harness is misconfigured."""

    pass


__all__ = [
    "FakeLLMExhaustedError",
    "FakeToolExecutionError",
    "FakeToolNotFoundError",
    "HarnessConfigurationError",
    "TestingInfrastructureError",
]
