"""Execution, tool, and code-generation errors.

Internal submodule of :mod:`polaris.kernelone.errors`.
Public symbols are re-exported from the package ``__init__``.
"""

from __future__ import annotations

from polaris.kernelone.errors._base import KernelOneError


class ExecutionError(KernelOneError):
    """Execution-related errors.

    Raised when an operation fails during execution.
    Retryability depends on the specific error type.

    Attributes:
        operation: The operation that failed.
        tool_name: The tool that was being executed, if applicable.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "EXECUTION_ERROR",
        operation: str = "",
        tool_name: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        self.operation = operation
        self.tool_name = tool_name
        if operation:
            self.details["operation"] = operation
        if tool_name:
            self.details["tool_name"] = tool_name


class ToolExecutionError(ExecutionError):
    """Tool execution failed.

    Raised when a tool call fails during execution.
    This can include file system errors, subprocess failures, etc.

    Attributes:
        exit_code: Exit code if the error came from a subprocess.
    """

    def __init__(
        self,
        message: str,
        *,
        tool_name: str = "",
        exit_code: int | None = None,
        retryable: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="TOOL_EXECUTION_ERROR",
            tool_name=tool_name,
            retryable=retryable,
            **kwargs,
        )
        self.exit_code = exit_code
        if exit_code is not None:
            self.details["exit_code"] = exit_code


class ShellDisallowedError(ExecutionError):
    """Shell command execution is disallowed.

    Raised when a shell command is attempted but policy prohibits it.
    """

    def __init__(
        self,
        message: str = "shell=True is not allowed in KernelOne",
        *,
        command: str = "",
        reason: str = "shell_execution_policy_violation",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="SHELL_DISALLOWED_ERROR",
            operation="shell_command",
            **kwargs,
        )
        if command:
            self.details["command"] = command
        self.details["reason"] = reason


class BudgetExceededError(ExecutionError):
    """Context budget exceeded.

    Raised when an operation would exceed available context budget.

    Attributes:
        limit: The hard limit that was exceeded.
        requested: The amount that was requested.
        current: Current usage before the operation.
    """

    def __init__(
        self,
        message: str,
        *,
        limit: int = 2000,
        requested: int = 0,
        current: int = 0,
        suggestion: str | None = None,
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(
            message,
            code="BUDGET_EXCEEDED_ERROR",
            operation="context_budget_check",
            **kwargs,
        )
        self.limit = limit
        self.requested = requested
        self.current = current
        self.suggestion = suggestion
        self.details.update(
            {
                "limit": limit,
                "requested": requested,
                "current": current,
            }
        )
        if suggestion:
            self.details["suggestion"] = suggestion


class ToolAuthorizationError(ExecutionError):
    """Tool authorization failed."""

    def __init__(
        self,
        message: str,
        *,
        tool_name: str = "",
        role: str = "",
        reason: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="TOOL_AUTHORIZATION_ERROR",
            tool_name=tool_name,
            **kwargs,
        )
        if role:
            self.details["role"] = role
        if reason:
            self.details["reason"] = reason


class ToolError(ToolExecutionError):
    """Legacy tool error (compatibility alias).

    This class is provided for backward compatibility.
    Prefer using ToolExecutionError for new code.
    """

    pass


class CodeGenerationError(ExecutionError):
    """Code generation error."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "CODE_GENERATION_ERROR",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code=code,
            operation="code_generation",
            **kwargs,
        )


class CodeGenerationPolicyViolationError(CodeGenerationError):
    """Code generation policy violation error."""

    def __init__(
        self,
        message: str,
        *,
        policy_rule: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="CODE_GENERATION_POLICY_VIOLATION_ERROR",
            **kwargs,
        )
        if policy_rule:
            self.details["policy_rule"] = policy_rule
