"""Tests for ToolGatewayPort Protocol and DI integration.

验证：
1. ToolGatewayPort Protocol 定义正确
2. Kernel 可通过 DI 注入 tool_gateway（需要kernel.py已修改）
3. Mock 测试覆盖 DI 正确性
4. 向后兼容：未注入时使用默认 RoleToolGateway
"""

from __future__ import annotations

from typing import Any

import pytest

# --- Mock Implementations for Testing ---


class MockToolGatewayForPort:
    """Mock implementation that satisfies ToolGatewayPort Protocol.

    Note: This class doesn't use @runtime_checkable decorator,
    but when used with isinstance() check on a Protocol that has
    @runtime_checkable, Python's duck-typing will verify the interface.
    """

    def __init__(self, results: dict[str, dict[str, Any]] | None = None) -> None:
        self._results = results or {}
        self._calls: list[tuple[str, dict]] = []
        self._approval_checks: list[tuple[str, dict | None, Any | None]] = []

    def execute(self, tool_name: str, args: dict) -> dict[str, Any]:
        self._calls.append((tool_name, args))
        return self._results.get(tool_name, {"success": True, "result": "mocked"})

    def requires_approval(
        self,
        tool_name: str,
        args: dict | None = None,
        state: Any | None = None,
    ) -> bool:
        self._approval_checks.append((tool_name, args, state))
        return False

    @property
    def calls(self) -> list[tuple[str, dict]]:
        return self._calls

    @property
    def approval_checks(self) -> list[tuple[str, dict | None, Any | None]]:
        return self._approval_checks


class MockToolGatewayWithApproval:
    """Mock implementation that requires approval for certain tools."""

    def __init__(self, approval_map: dict[str, bool] | None = None) -> None:
        self._approval_map = approval_map or {}
        self._calls: list[tuple[str, dict]] = []

    def execute(self, tool_name: str, args: dict) -> dict[str, Any]:
        self._calls.append((tool_name, args))
        return {
            "success": True,
            "result": f"executed_{tool_name}",
        }

    def requires_approval(
        self,
        tool_name: str,
        args: dict | None = None,
        state: Any | None = None,
    ) -> bool:
        return self._approval_map.get(tool_name, False)

    @property
    def calls(self) -> list[tuple[str, dict]]:
        return self._calls


class ToolGatewayNonCompliant:
    """Non-compliant implementation missing required methods."""

    def execute(self, tool_name: str) -> dict[str, Any]:
        # Missing 'args' parameter - not Protocol-compliant
        return {"success": True}


# --- Protocol Compliance Tests ---


class TestToolGatewayPortProtocol:
    """Test ToolGatewayPort Protocol exists and is properly defined."""

    def test_protocol_importable(self) -> None:
        """Verify ToolGatewayPort can be imported from contracts."""
        from polaris.cells.roles.kernel.public.contracts import ToolGatewayPort

        assert ToolGatewayPort is not None

    def test_protocol_has_execute_method(self) -> None:
        """Verify Protocol defines execute method."""
        from polaris.cells.roles.kernel.public.contracts import ToolGatewayPort

        # Protocol should have execute in its namespace
        assert hasattr(ToolGatewayPort, "execute")

    def test_protocol_has_requires_approval_method(self) -> None:
        """Verify Protocol defines requires_approval method."""
        from polaris.cells.roles.kernel.public.contracts import ToolGatewayPort

        # Protocol should have requires_approval in its namespace
        assert hasattr(ToolGatewayPort, "requires_approval")


class TestProtocolRuntimeCheckable:
    """Test Protocol is runtime_checkable."""

    def test_protocol_is_runtime_checkable(self) -> None:
        """Verify ToolGatewayPort is decorated with @runtime_checkable."""
        from polaris.cells.roles.kernel.public.contracts import ToolGatewayPort

        # A runtime_checkable Protocol should pass isinstance check
        # when the object has all required methods
        mock_gateway = MockToolGatewayForPort()
        assert isinstance(mock_gateway, ToolGatewayPort)

    def test_compliant_implementation_passes_runtime_check(self) -> None:
        """Verify compliant mock passes isinstance check."""
        from polaris.cells.roles.kernel.public.contracts import ToolGatewayPort

        gateway = MockToolGatewayWithApproval()
        assert isinstance(gateway, ToolGatewayPort)


class TestMockToolGatewayExecution:
    """Test MockToolGatewayForPort execution behavior."""

    def test_execute_returns_mock_result(self) -> None:
        """Verify execute returns configured mock result."""
        gateway = MockToolGatewayForPort(
            results={
                "read_file": {"success": True, "result": "file content"},
            }
        )
        result = gateway.execute("read_file", {"path": "test.py"})
        assert result["success"] is True
        assert result["result"] == "file content"

    def test_execute_records_calls(self) -> None:
        """Verify execute records all calls."""
        gateway = MockToolGatewayForPort()
        gateway.execute("write_file", {"path": "test.py", "content": "hello"})
        gateway.execute("read_file", {"path": "test.py"})
        assert len(gateway.calls) == 2
        assert gateway.calls[0] == ("write_file", {"path": "test.py", "content": "hello"})
        assert gateway.calls[1] == ("read_file", {"path": "test.py"})

    def test_requires_approval_returns_false_by_default(self) -> None:
        """Verify requires_approval returns False by default."""
        gateway = MockToolGatewayForPort()
        assert gateway.requires_approval("write_file") is False

    def test_requires_approval_records_checks(self) -> None:
        """Verify requires_approval records all checks."""
        gateway = MockToolGatewayForPort()
        gateway.requires_approval("write_file", {"path": "test.py"})
        gateway.requires_approval("read_file", state={"user": "test"})
        assert len(gateway.approval_checks) == 2


class TestMockToolGatewayWithApproval:
    """Test MockToolGatewayWithApproval behavior."""

    def test_requires_approval_respects_map(self) -> None:
        """Verify requires_approval respects configured approval map."""
        gateway = MockToolGatewayWithApproval(
            approval_map={
                "write_file": True,
                "delete_file": True,
                "read_file": False,
            }
        )
        assert gateway.requires_approval("write_file") is True
        assert gateway.requires_approval("delete_file") is True
        assert gateway.requires_approval("read_file") is False
        assert gateway.requires_approval("unknown_tool") is False

    def test_execute_returns_tool_name_in_result(self) -> None:
        """Verify execute includes tool name in result."""
        gateway = MockToolGatewayWithApproval()
        result = gateway.execute("search_code", {"query": "test"})
        assert result["result"] == "executed_search_code"


# --- Kernel DI Integration Tests ---


class TestKernelToolGatewayDI:
    """Test RoleExecutionKernel tool_gateway DI integration.

    Note: These tests verify the DI interface exists and works correctly.
    Full integration tests with RoleExecutionKernel require mocking
    additional dependencies (registry, LLM caller, etc.).
    """

    def test_kernel_accepts_tool_gateway_parameter(self) -> None:
        """Verify RoleExecutionKernel accepts tool_gateway parameter."""
        try:
            from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
        except ImportError as e:
            pytest.skip(f"Cannot import RoleExecutionKernel: {e}")

        mock_gateway = MockToolGatewayForPort()
        # This should not raise - kernel accepts tool_gateway param
        kernel = RoleExecutionKernel(
            workspace=".",
            tool_gateway=mock_gateway,
        )
        assert kernel._tool_gateway is mock_gateway

    def test_kernel_accepts_none_tool_gateway(self) -> None:
        """Verify RoleExecutionKernel accepts None tool_gateway (backward compat)."""
        try:
            from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
        except ImportError as e:
            pytest.skip(f"Cannot import RoleExecutionKernel: {e}")

        kernel = RoleExecutionKernel(
            workspace=".",
            tool_gateway=None,
        )
        assert kernel._tool_gateway is None

    def test_kernel_without_tool_gateway_defaults_to_none(self) -> None:
        """Verify RoleExecutionKernel defaults tool_gateway to None."""
        try:
            from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
        except ImportError as e:
            pytest.skip(f"Cannot import RoleExecutionKernel: {e}")

        kernel = RoleExecutionKernel(workspace=".")
        assert kernel._tool_gateway is None


# --- DelegatingToolGateway Tests ---


class TestDelegatingToolGateway:
    """Test _DelegatingToolGateway wrapper for DI compatibility."""

    def test_delegating_gateway_importable(self) -> None:
        """Verify _DelegatingToolGateway can be imported."""
        try:
            from polaris.cells.roles.kernel.internal._tool_gateway_di import _DelegatingToolGateway
        except ImportError as e:
            pytest.skip(f"Cannot import _DelegatingToolGateway: {e}")

        assert _DelegatingToolGateway is not None

    def test_delegating_gateway_is_role_tool_gateway_compatible(self) -> None:
        """Verify DelegatingToolGateway is compatible with RoleToolGateway interface.

        Note: _DelegatingToolGateway is NOT a subclass of RoleToolGateway,
        but provides the same interface for DI compatibility.
        """
        try:
            from polaris.cells.roles.kernel.internal._tool_gateway_di import _DelegatingToolGateway
        except ImportError as e:
            pytest.skip(f"Cannot import _DelegatingToolGateway: {e}")

        mock_gateway = MockToolGatewayForPort()
        delegating = _DelegatingToolGateway(mock_gateway)

        # Verify it has the required interface methods (duck typing)
        assert hasattr(delegating, "execute")
        assert hasattr(delegating, "execute_tool")
        assert hasattr(delegating, "check_tool_permission")
        assert hasattr(delegating, "reset_execution_count")
        assert hasattr(delegating, "requires_approval")
        assert hasattr(delegating, "close")

    def test_delegating_execute_delegates_to_port(self) -> None:
        """Verify execute() delegates to injected port."""
        try:
            from polaris.cells.roles.kernel.internal._tool_gateway_di import _DelegatingToolGateway
        except ImportError as e:
            pytest.skip(f"Cannot import _DelegatingToolGateway: {e}")

        mock_gateway = MockToolGatewayForPort(
            results={
                "read_file": {"success": True, "result": "delegated result"},
            }
        )
        delegating = _DelegatingToolGateway(mock_gateway)
        result = delegating.execute("read_file", {"path": "test.py"})
        assert result["success"] is True
        assert result["result"] == "delegated result"
        assert mock_gateway.calls[0] == ("read_file", {"path": "test.py"})

    def test_delegating_requires_approval_delegates(self) -> None:
        """Verify requires_approval() delegates to injected port."""
        try:
            from polaris.cells.roles.kernel.internal._tool_gateway_di import _DelegatingToolGateway
        except ImportError as e:
            pytest.skip(f"Cannot import _DelegatingToolGateway: {e}")

        mock_gateway = MockToolGatewayWithApproval(approval_map={"write_file": True})
        delegating = _DelegatingToolGateway(mock_gateway)
        assert delegating.requires_approval("write_file") is True
        assert delegating.requires_approval("read_file") is False

    def test_delegating_check_tool_permission(self) -> None:
        """Verify check_tool_permission delegates correctly."""
        try:
            from polaris.cells.roles.kernel.internal._tool_gateway_di import _DelegatingToolGateway
        except ImportError as e:
            pytest.skip(f"Cannot import _DelegatingToolGateway: {e}")

        mock_gateway = MockToolGatewayWithApproval(approval_map={"write_file": True, "read_file": False})
        delegating = _DelegatingToolGateway(mock_gateway)

        # Tool requiring approval
        allowed, reason = delegating.check_tool_permission("write_file")
        assert allowed is False
        assert "requires approval" in reason

        # Tool not requiring approval
        allowed, reason = delegating.check_tool_permission("read_file")
        assert allowed is True

    def test_delegating_reset_execution_count(self) -> None:
        """Verify reset_execution_count works."""
        try:
            from polaris.cells.roles.kernel.internal._tool_gateway_di import _DelegatingToolGateway
        except ImportError as e:
            pytest.skip(f"Cannot import _DelegatingToolGateway: {e}")

        mock_gateway = MockToolGatewayForPort()
        delegating = _DelegatingToolGateway(mock_gateway)
        delegating.execute("read_file", {"path": "test.py"})
        assert delegating._execution_count == 1
        delegating.reset_execution_count()
        assert delegating._execution_count == 0


# --- Service Export Tests ---


class TestServiceExports:
    """Test ToolGatewayPort is exported from service module."""

    def test_tool_gateway_port_exported_from_service(self) -> None:
        """Verify ToolGatewayPort can be imported from service."""
        try:
            from polaris.cells.roles.kernel.public.service import ToolGatewayPort
        except ImportError as e:
            pytest.skip(f"Cannot import from service: {e}")

        assert ToolGatewayPort is not None


# --- Backward Compatibility Tests ---


class TestBackwardCompatibility:
    """Test backward compatibility with existing RoleToolGateway usage."""

    def test_kernel_without_di_injection_works(self) -> None:
        """Verify kernel works without DI (existing behavior)."""
        try:
            from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
        except ImportError as e:
            pytest.skip(f"Cannot import RoleExecutionKernel: {e}")

        # Create kernel without DI - should work as before
        kernel = RoleExecutionKernel(workspace=".")
        assert kernel._tool_gateway is None
