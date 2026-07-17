"""Tests for ToolGatewayPort Protocol and DI integration.

验证：
1. ToolGatewayPort Protocol 定义正确
2. Kernel 可通过 DI 注入 tool_gateway（需要kernel.py已修改）
3. Mock 测试覆盖 DI 正确性
4. 向后兼容：未注入时使用默认 RoleToolGateway
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from polaris.cells.roles.profile.public.service import RoleProfile, RoleToolPolicy


@contextmanager
def _isolated_tool_spec_registry_state() -> Iterator[None]:
    """Restore the ContextVar-backed registry after a late-registration test."""
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    token = ToolSpecRegistry._state_var.set(ToolSpecRegistry._get_state())
    try:
        yield
    finally:
        ToolSpecRegistry._state_var.reset(token)


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


# --- ToolGatewayPortAdapter Tests ---


class TestToolGatewayPortAdapter:
    """Test _ToolGatewayPortAdapter dependency-injection bridge."""

    def test_adapter_gateway_importable(self) -> None:
        """Verify _ToolGatewayPortAdapter can be imported."""
        try:
            from polaris.cells.roles.kernel.internal._tool_gateway_di import _ToolGatewayPortAdapter
        except ImportError as e:
            pytest.skip(f"Cannot import _ToolGatewayPortAdapter: {e}")

        assert _ToolGatewayPortAdapter is not None

    def test_adapter_gateway_is_role_tool_gateway_compatible(self) -> None:
        """Verify ToolGatewayPortAdapter exposes the RoleToolGateway method shape.

        Note: _ToolGatewayPortAdapter is NOT a subclass of RoleToolGateway,
        but provides the same method surface for injected ports.
        """
        try:
            from polaris.cells.roles.kernel.internal._tool_gateway_di import _ToolGatewayPortAdapter
        except ImportError as e:
            pytest.skip(f"Cannot import _ToolGatewayPortAdapter: {e}")

        mock_gateway = MockToolGatewayForPort()
        adapter = _ToolGatewayPortAdapter(mock_gateway)

        # Verify it has the required interface methods (duck typing)
        assert hasattr(adapter, "execute")
        assert hasattr(adapter, "execute_tool")
        assert hasattr(adapter, "check_tool_permission")
        assert hasattr(adapter, "reset_execution_count")
        assert hasattr(adapter, "requires_approval")
        assert hasattr(adapter, "close")

    def test_adapter_execute_calls_port(self) -> None:
        """Verify execute() calls injected port."""
        try:
            from polaris.cells.roles.kernel.internal._tool_gateway_di import _ToolGatewayPortAdapter
        except ImportError as e:
            pytest.skip(f"Cannot import _ToolGatewayPortAdapter: {e}")

        mock_gateway = MockToolGatewayForPort(
            results={
                "read_file": {"success": True, "result": "adapter result"},
            }
        )
        adapter = _ToolGatewayPortAdapter(mock_gateway)
        result = adapter.execute("read_file", {"path": "test.py"})
        assert result["success"] is True
        assert result["result"] == "adapter result"
        assert mock_gateway.calls[0] == ("read_file", {"path": "test.py"})

    def test_adapter_requires_approval_calls_port(self) -> None:
        """Verify requires_approval() calls injected port."""
        try:
            from polaris.cells.roles.kernel.internal._tool_gateway_di import _ToolGatewayPortAdapter
        except ImportError as e:
            pytest.skip(f"Cannot import _ToolGatewayPortAdapter: {e}")

        mock_gateway = MockToolGatewayWithApproval(approval_map={"write_file": True})
        adapter = _ToolGatewayPortAdapter(mock_gateway)
        assert adapter.requires_approval("write_file") is True
        assert adapter.requires_approval("read_file") is False

    def test_adapter_check_tool_permission(self) -> None:
        """Verify check_tool_permission checks permission through the injected port."""
        try:
            from polaris.cells.roles.kernel.internal._tool_gateway_di import _ToolGatewayPortAdapter
        except ImportError as e:
            pytest.skip(f"Cannot import _ToolGatewayPortAdapter: {e}")

        mock_gateway = MockToolGatewayWithApproval(approval_map={"write_file": True, "read_file": False})
        adapter = _ToolGatewayPortAdapter(mock_gateway)

        # Tool requiring approval
        allowed, reason = adapter.check_tool_permission("write_file")
        assert allowed is False
        assert "requires approval" in reason

        # Tool not requiring approval
        allowed, reason = adapter.check_tool_permission("read_file")
        assert allowed is True

    def test_adapter_reset_execution_count(self) -> None:
        """Verify reset_execution_count works."""
        try:
            from polaris.cells.roles.kernel.internal._tool_gateway_di import _ToolGatewayPortAdapter
        except ImportError as e:
            pytest.skip(f"Cannot import _ToolGatewayPortAdapter: {e}")

        mock_gateway = MockToolGatewayForPort()
        adapter = _ToolGatewayPortAdapter(mock_gateway)
        adapter.execute("read_file", {"path": "test.py"})
        assert adapter._execution_count == 1
        adapter.reset_execution_count()
        assert adapter._execution_count == 0


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


class TestSnapshotBoundGateway:
    def test_bound_permission_uses_only_the_supplied_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.cells.roles.kernel.internal import tool_gateway
        from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        profile = RoleProfile(
            role_id="director",
            display_name="Director",
            description="test profile",
            tool_policy=RoleToolPolicy(
                whitelist=["read_file"],
                blacklist=[],
                allow_code_write=False,
                allow_command_execution=False,
                allow_file_delete=False,
                max_tool_calls_per_turn=10,
            ),
        )
        gateway = RoleToolGateway(profile, workspace=".")
        snapshot = ToolSpecRegistry.capture_effective_spec("cat")

        def _unexpected_registry_access(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("bound gateway must not read the active registry")

        def _unexpected_normalization(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("bound gateway must not normalize arguments")

        monkeypatch.setattr(ToolSpecRegistry, "capture_effective_spec", _unexpected_registry_access)
        monkeypatch.setattr(tool_gateway, "normalize_tool_arguments_from_snapshot", _unexpected_normalization)

        allowed, reason = gateway.check_tool_permission_from_snapshot(
            raw_tool_name="cat",
            canonical_tool_name="read_file",
            normalized_tool_args={"path": "README.md"},
            tool_snapshot=snapshot,
        )

        assert allowed is True
        assert reason == "授权通过"

    def test_compatibility_entry_captures_and_normalizes_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.cells.roles.kernel.internal import tool_gateway
        from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        profile = RoleProfile(
            role_id="director",
            display_name="Director",
            description="test profile",
            tool_policy=RoleToolPolicy(
                whitelist=["write_file"],
                blacklist=[],
                allow_code_write=True,
                allow_command_execution=False,
                allow_file_delete=False,
                max_tool_calls_per_turn=10,
            ),
        )
        gateway = RoleToolGateway(profile, workspace=".")
        captured = ToolSpecRegistry.capture_effective_spec("write_file")
        captures = 0
        normalizations = 0

        def _capture_once(_raw_tool_name: str):
            nonlocal captures
            captures += 1
            return captured

        def _normalize_once(snapshot: object, arguments: object) -> dict[str, object]:
            nonlocal normalizations
            normalizations += 1
            assert snapshot is captured
            assert arguments == {"path": "main.py", "content": "x"}
            return {"file": "main.py", "content": "x"}

        monkeypatch.setattr(ToolSpecRegistry, "capture_effective_spec", _capture_once)
        monkeypatch.setattr(tool_gateway, "normalize_tool_arguments_from_snapshot", _normalize_once)

        allowed, reason = gateway.check_tool_permission("write_file", {"path": "main.py", "content": "x"})

        assert allowed is True
        assert reason == "授权通过"
        assert captures == 1
        assert normalizations == 1

    def test_late_registered_exec_snapshot_respects_command_guard(self) -> None:
        from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        with _isolated_tool_spec_registry_state():
            ToolSpecRegistry.register(
                "late_gateway_exec",
                {"category": "exec", "description": "late exec", "aliases": [], "arguments": []},
            )
            gateway = RoleToolGateway(
                RoleProfile(
                    role_id="director",
                    display_name="Director",
                    description="test profile",
                    tool_policy=RoleToolPolicy(
                        whitelist=["late_gateway_exec"],
                        blacklist=[],
                        allow_code_write=False,
                        allow_command_execution=False,
                        allow_file_delete=False,
                        max_tool_calls_per_turn=10,
                    ),
                ),
                workspace=".",
            )
            snapshot = ToolSpecRegistry.capture_effective_spec("late_gateway_exec")

            allowed, reason = gateway.check_tool_permission_from_snapshot(
                raw_tool_name="late_gateway_exec",
                canonical_tool_name="late_gateway_exec",
                normalized_tool_args={"command": "pwd"},
                tool_snapshot=snapshot,
            )

        assert allowed is False
        assert "角色无权执行命令" in reason

    def test_late_registered_write_and_delete_snapshots_enter_their_mutation_guards(self) -> None:
        from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        with _isolated_tool_spec_registry_state():
            ToolSpecRegistry.register(
                "late_gateway_write",
                {"category": "write", "description": "late write", "aliases": [], "arguments": []},
            )
            ToolSpecRegistry.register(
                "late_gateway_delete",
                {"category": "delete", "description": "late delete", "aliases": [], "arguments": []},
            )
            gateway = RoleToolGateway(
                RoleProfile(
                    role_id="director",
                    display_name="Director",
                    description="test profile",
                    tool_policy=RoleToolPolicy(
                        whitelist=["late_gateway_write", "late_gateway_delete"],
                        blacklist=[],
                        allow_code_write=False,
                        allow_command_execution=False,
                        allow_file_delete=False,
                        max_tool_calls_per_turn=10,
                    ),
                ),
                workspace=".",
            )
            write_snapshot = ToolSpecRegistry.capture_effective_spec("late_gateway_write")
            delete_snapshot = ToolSpecRegistry.capture_effective_spec("late_gateway_delete")

            write_allowed, write_reason = gateway.check_tool_permission_from_snapshot(
                raw_tool_name="late_gateway_write",
                canonical_tool_name="late_gateway_write",
                normalized_tool_args={"file": "main.py", "content": "x"},
                tool_snapshot=write_snapshot,
            )
            delete_allowed, delete_reason = gateway.check_tool_permission_from_snapshot(
                raw_tool_name="late_gateway_delete",
                canonical_tool_name="late_gateway_delete",
                normalized_tool_args={"file": "main.py"},
                tool_snapshot=delete_snapshot,
            )

        assert write_allowed is False
        assert "角色无权使用代码写入工具" in write_reason
        assert delete_allowed is False
        assert "角色无权删除文件" in delete_reason

    def test_bound_verdict_does_not_change_after_registry_state_switch(self) -> None:
        from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        with _isolated_tool_spec_registry_state():
            ToolSpecRegistry.register(
                "late_gateway_captured_exec",
                {"category": "exec", "description": "captured exec", "aliases": [], "arguments": []},
            )
            gateway = RoleToolGateway(
                RoleProfile(
                    role_id="director",
                    display_name="Director",
                    description="test profile",
                    tool_policy=RoleToolPolicy(
                        whitelist=["late_gateway_captured_exec"],
                        blacklist=[],
                        allow_code_write=False,
                        allow_command_execution=False,
                        allow_file_delete=False,
                        max_tool_calls_per_turn=10,
                    ),
                ),
                workspace=".",
            )
            snapshot = ToolSpecRegistry.capture_effective_spec("late_gateway_captured_exec")
            ToolSpecRegistry.register(
                "late_gateway_registry_switch",
                {"category": "read", "description": "registry switch", "aliases": [], "arguments": []},
            )

            allowed, reason = gateway.check_tool_permission_from_snapshot(
                raw_tool_name="late_gateway_captured_exec",
                canonical_tool_name="late_gateway_captured_exec",
                normalized_tool_args={"command": "pwd"},
                tool_snapshot=snapshot,
            )

        assert allowed is False
        assert "角色无权执行命令" in reason

    def test_inconsistent_snapshot_category_is_fail_closed(self) -> None:
        from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        with _isolated_tool_spec_registry_state():
            ToolSpecRegistry.register(
                "late_gateway_inconsistent",
                {
                    "category": "write",
                    "effect_type": "read",
                    "description": "inconsistent tool",
                    "aliases": [],
                    "arguments": [],
                },
            )
            gateway = RoleToolGateway(
                RoleProfile(
                    role_id="director",
                    display_name="Director",
                    description="test profile",
                    tool_policy=RoleToolPolicy(
                        whitelist=["late_gateway_inconsistent"],
                        blacklist=[],
                        allow_code_write=True,
                        allow_command_execution=True,
                        allow_file_delete=True,
                        max_tool_calls_per_turn=10,
                    ),
                ),
                workspace=".",
            )
            snapshot = ToolSpecRegistry.capture_effective_spec("late_gateway_inconsistent")

            allowed, reason = gateway.check_tool_permission_from_snapshot(
                raw_tool_name="late_gateway_inconsistent",
                canonical_tool_name="late_gateway_inconsistent",
                normalized_tool_args={"file": "main.py", "content": "x"},
                tool_snapshot=snapshot,
            )

        assert allowed is False
        assert "工具分类不一致" in reason

    @pytest.mark.parametrize("category", ["async", "unsupported"])
    def test_unsupported_snapshot_category_is_fail_closed(self, category: str) -> None:
        from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        tool_name = f"late_gateway_{category}"
        with _isolated_tool_spec_registry_state():
            ToolSpecRegistry.register(
                tool_name,
                {"category": category, "description": "unsupported tool", "aliases": [], "arguments": []},
            )
            gateway = RoleToolGateway(
                RoleProfile(
                    role_id="director",
                    display_name="Director",
                    description="test profile",
                    tool_policy=RoleToolPolicy(
                        whitelist=[tool_name],
                        blacklist=[],
                        allow_code_write=True,
                        allow_command_execution=True,
                        allow_file_delete=True,
                        max_tool_calls_per_turn=10,
                    ),
                ),
                workspace=".",
            )
            snapshot = ToolSpecRegistry.capture_effective_spec(tool_name)

            allowed, reason = gateway.check_tool_permission_from_snapshot(
                raw_tool_name=tool_name,
                canonical_tool_name=tool_name,
                normalized_tool_args={},
                tool_snapshot=snapshot,
            )

        assert allowed is False
        assert "工具分类不支持" in reason


# --- Default Gateway Path Tests ---


class TestDefaultGatewayPath:
    """Test existing RoleToolGateway creation without an injected adapter."""

    def test_kernel_without_di_injection_works(self) -> None:
        """Verify kernel works without an injected ToolGatewayPort."""
        try:
            from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
        except ImportError as e:
            pytest.skip(f"Cannot import RoleExecutionKernel: {e}")

        # Create kernel without DI - should work as before
        kernel = RoleExecutionKernel(workspace=".")
        assert kernel._tool_gateway is None
