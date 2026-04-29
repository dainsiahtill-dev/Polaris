"""Tests for polaris.kernelone.exceptions module re-exports and lazy loading."""

from __future__ import annotations

from polaris.kernelone import exceptions


class TestExceptionsReexports:
    def test_kernel_one_error_available(self) -> None:
        assert hasattr(exceptions, "KernelOneError")

    def test_configuration_error_available(self) -> None:
        assert hasattr(exceptions, "ConfigurationError")

    def test_validation_error_available(self) -> None:
        assert hasattr(exceptions, "ValidationError")

    def test_execution_error_available(self) -> None:
        assert hasattr(exceptions, "ExecutionError")

    def test_resource_error_available(self) -> None:
        assert hasattr(exceptions, "ResourceError")

    def test_communication_error_available(self) -> None:
        assert hasattr(exceptions, "CommunicationError")

    def test_state_error_available(self) -> None:
        assert hasattr(exceptions, "StateError")

    def test_cell_error_available(self) -> None:
        assert hasattr(exceptions, "CellError")

    def test_audit_error_available(self) -> None:
        assert hasattr(exceptions, "AuditError")

    def test_event_error_available(self) -> None:
        assert hasattr(exceptions, "EventError")

    def test_llm_error_available(self) -> None:
        assert hasattr(exceptions, "LLMError")

    def test_tool_error_available(self) -> None:
        assert hasattr(exceptions, "ToolError")

    def test_kernel_error_backward_compat(self) -> None:
        assert hasattr(exceptions, "KernelError")
        from polaris.kernelone.errors import KernelOneError

        assert exceptions.KernelError is KernelOneError

    def test_llm_exception_alias(self) -> None:
        assert hasattr(exceptions, "LLMException")

    def test_emit_result_available(self) -> None:
        assert hasattr(exceptions, "EmitResult")


class TestExceptionHierarchy:
    def test_all_inherit_from_kernel_one_error(self) -> None:
        from polaris.kernelone.errors import KernelOneError

        for name in [
            "ConfigurationError",
            "ValidationError",
            "ExecutionError",
            "ResourceError",
            "CommunicationError",
            "StateError",
            "CellError",
            "AuditError",
            "EventError",
        ]:
            cls = getattr(exceptions, name)
            assert issubclass(cls, KernelOneError), f"{name} should inherit from KernelOneError"

    def test_kernel_one_error_inherits_exception(self) -> None:
        assert issubclass(exceptions.KernelOneError, Exception)

    def test_llm_error_inherits_kernel_one_error(self) -> None:
        assert issubclass(exceptions.LLMError, exceptions.KernelOneError)


class TestExceptionInstantiation:
    def test_can_instantiate_all_common_exceptions(self) -> None:
        for name in [
            "KernelOneError",
            "ConfigurationError",
            "ValidationError",
            "ExecutionError",
            "ResourceError",
            "CommunicationError",
            "StateError",
            "CellError",
        ]:
            cls = getattr(exceptions, name)
            instance = cls("test message")
            assert str(instance) == "test message"

    def test_kernel_one_error_to_dict(self) -> None:
        err = exceptions.KernelOneError("hello", code="TEST", details={"a": 1})
        d = err.to_dict()
        assert d["message"] == "hello"
        assert d["code"] == "TEST"
        assert d["details"] == {"a": 1}

    def test_tool_execution_error_details(self) -> None:
        err = exceptions.ToolExecutionError("fail", tool_name="git", exit_code=1)
        assert err.tool_name == "git"
        assert err.exit_code == 1
