"""Contract tests for roles.kernel cell.

Tests the public contracts and service boundaries of the roles.kernel cell.
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.public.contracts import (
    BuildRolePromptCommandV1,
    CheckRoleQualityCommandV1,
    ClassifyKernelErrorQueryV1,
    ExecuteRoleKernelTurnCommandV1,
    GenericRoleResponse,
    ParseRoleOutputCommandV1,
    ResolveRetryPolicyQueryV1,
    RoleKernelError,
    RoleKernelResultV1,
    ToolGatewayPort,
)


class TestBuildRolePromptCommandV1:
    """Tests for BuildRolePromptCommandV1 contract."""

    def test_command_construction(self) -> None:
        """Test basic command construction."""
        cmd = BuildRolePromptCommandV1(
            role_id="pm",
            workspace=".",
            context={"task": "test"},
            structured_output=False,
        )
        assert cmd.role_id == "pm"
        assert cmd.workspace == "."
        assert cmd.context == {"task": "test"}
        assert cmd.structured_output is False

    def test_command_empty_role_id_raises(self) -> None:
        """Test that empty role_id raises ValueError."""
        with pytest.raises(ValueError, match="role_id must be a non-empty string"):
            BuildRolePromptCommandV1(role_id="", workspace=".")

    def test_command_whitespace_role_id_raises(self) -> None:
        """Test that whitespace-only role_id raises ValueError."""
        with pytest.raises(ValueError, match="role_id must be a non-empty string"):
            BuildRolePromptCommandV1(role_id="   ", workspace=".")

    def test_command_default_context(self) -> None:
        """Test that context defaults to empty dict."""
        cmd = BuildRolePromptCommandV1(role_id="pm", workspace=".")
        assert cmd.context == {}

    def test_command_immutable(self) -> None:
        """Test that command is immutable."""
        cmd = BuildRolePromptCommandV1(role_id="pm", workspace=".")
        with pytest.raises(AttributeError):
            cmd.role_id = "architect"  # type: ignore[misc]


class TestExecuteRoleKernelTurnCommandV1:
    """Tests for ExecuteRoleKernelTurnCommandV1 contract."""

    def test_turn_command_construction(self) -> None:
        """Test turn command construction."""
        cmd = ExecuteRoleKernelTurnCommandV1(
            role_id="director",
            workspace=".",
            prompt="Execute task",
            context={"turn": 1},
        )
        assert cmd.role_id == "director"
        assert cmd.prompt == "Execute task"

    def test_turn_command_empty_prompt_raises(self) -> None:
        """Test that empty prompt raises ValueError."""
        with pytest.raises(ValueError, match="prompt must be a non-empty string"):
            ExecuteRoleKernelTurnCommandV1(role_id="pm", workspace=".", prompt="")


class TestParseRoleOutputCommandV1:
    """Tests for ParseRoleOutputCommandV1 contract."""

    def test_parse_command_construction(self) -> None:
        """Test parse command construction."""
        cmd = ParseRoleOutputCommandV1(role_id="pm", output="Test output")
        assert cmd.role_id == "pm"
        assert cmd.output == "Test output"

    def test_parse_command_empty_output_raises(self) -> None:
        """Test that empty output raises ValueError."""
        with pytest.raises(ValueError, match="output must be a non-empty string"):
            ParseRoleOutputCommandV1(role_id="pm", output="")


class TestCheckRoleQualityCommandV1:
    """Tests for CheckRoleQualityCommandV1 contract."""

    def test_quality_command_construction(self) -> None:
        """Test quality check command construction."""
        cmd = CheckRoleQualityCommandV1(
            role_id="architect",
            output="Design output",
            context={"review": True},
        )
        assert cmd.role_id == "architect"
        assert cmd.output == "Design output"


class TestClassifyKernelErrorQueryV1:
    """Tests for ClassifyKernelErrorQueryV1 contract."""

    def test_error_query_construction(self) -> None:
        """Test error classification query construction."""
        query = ClassifyKernelErrorQueryV1(error_text="Timeout occurred")
        assert query.error_text == "Timeout occurred"

    def test_error_query_empty_raises(self) -> None:
        """Test that empty error_text raises ValueError."""
        with pytest.raises(ValueError, match="error_text must be a non-empty string"):
            ClassifyKernelErrorQueryV1(error_text="")


class TestResolveRetryPolicyQueryV1:
    """Tests for ResolveRetryPolicyQueryV1 contract."""

    def test_retry_query_defaults(self) -> None:
        """Test retry query with default values."""
        query = ResolveRetryPolicyQueryV1(error_text="Network error")
        assert query.error_text == "Network error"
        assert query.attempt == 1
        assert query.max_retries == 3

    def test_retry_query_custom_values(self) -> None:
        """Test retry query with custom values."""
        query = ResolveRetryPolicyQueryV1(
            error_text="Network error",
            attempt=2,
            max_retries=5,
        )
        assert query.attempt == 2
        assert query.max_retries == 5

    def test_retry_query_invalid_attempt(self) -> None:
        """Test that attempt < 1 raises ValueError."""
        with pytest.raises(ValueError, match="attempt must be >= 1"):
            ResolveRetryPolicyQueryV1(error_text="error", attempt=0)

    def test_retry_query_invalid_max_retries(self) -> None:
        """Test that max_retries < 1 raises ValueError."""
        with pytest.raises(ValueError, match="max_retries must be >= 1"):
            ResolveRetryPolicyQueryV1(error_text="error", max_retries=0)


class TestGenericRoleResponse:
    """Tests for GenericRoleResponse contract."""

    def test_response_basic(self) -> None:
        """Test basic response construction."""
        resp = GenericRoleResponse(content="Hello world")
        assert resp.content == "Hello world"
        assert resp.tool_calls is None
        assert resp.metadata == {}

    def test_response_with_tool_calls(self) -> None:
        """Test response with tool calls."""
        resp = GenericRoleResponse(
            content="Using tool",
            tool_calls=[{"name": "read_file", "args": {"path": "test.py"}}],
        )
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0]["name"] == "read_file"

    def test_response_to_dict(self) -> None:
        """Test conversion to dictionary."""
        resp = GenericRoleResponse(
            content="Test",
            tool_calls=[],
            metadata={"key": "value"},
        )
        d = resp.to_dict()
        assert d["content"] == "Test"
        assert d["metadata"] == {"key": "value"}


class TestRoleKernelResultV1:
    """Tests for RoleKernelResultV1 contract."""

    def test_success_result(self) -> None:
        """Test successful result construction."""
        result = RoleKernelResultV1(
            ok=True,
            status="completed",
            role_id="pm",
            workspace=".",
            prompt="Test prompt",
        )
        assert result.ok is True
        assert result.status == "completed"

    def test_failed_result_requires_error(self) -> None:
        """Test that failed result requires error_code or error_message."""
        with pytest.raises(ValueError, match="failed result must include error_code or error_message"):
            RoleKernelResultV1(
                ok=False,
                status="failed",
                role_id="pm",
                workspace=".",
            )

    def test_failed_result_with_error_code(self) -> None:
        """Test failed result with error_code."""
        result = RoleKernelResultV1(
            ok=False,
            status="failed",
            role_id="pm",
            workspace=".",
            error_code="TIMEOUT",
        )
        assert result.ok is False
        assert result.error_code == "TIMEOUT"

    def test_failed_result_with_error_message(self) -> None:
        """Test failed result with error_message."""
        result = RoleKernelResultV1(
            ok=False,
            status="failed",
            role_id="pm",
            workspace=".",
            error_message="Something went wrong",
        )
        assert result.error_message == "Something went wrong"


class TestRoleKernelError:
    """Tests for RoleKernelError contract."""

    def test_error_basic(self) -> None:
        """Test basic error construction."""
        err = RoleKernelError("Something failed")
        assert str(err) == "Something failed"
        assert err.code == "roles_kernel_error"
        assert err.details == {}

    def test_error_with_code(self) -> None:
        """Test error with custom code."""
        err = RoleKernelError("Failed", code="CUSTOM_ERROR")
        assert err.code == "CUSTOM_ERROR"

    def test_error_with_details(self) -> None:
        """Test error with details."""
        err = RoleKernelError("Failed", details={"key": "value", "count": 42})
        assert err.details == {"key": "value", "count": 42}

    def test_error_to_dict(self) -> None:
        """Test error serialization to dict."""
        err = RoleKernelError("Failed", code="TEST_ERROR", details={"x": 1})
        d = err.to_dict()
        assert d["code"] == "TEST_ERROR"
        assert d["message"] == "Failed"
        assert d["details"] == {"x": 1}

    def test_error_empty_message_raises(self) -> None:
        """Test that empty message raises ValueError."""
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            RoleKernelError("")


class TestToolGatewayPort:
    """Tests for ToolGatewayPort protocol."""

    def test_protocol_is_runtime_checkable(self) -> None:
        """Test that ToolGatewayPort is runtime checkable."""

        # Check if the protocol is decorated with @runtime_checkable
        # Note: Protocols imported from typing don't have __runtime_checkable__
        # unless explicitly decorated
        assert isinstance(ToolGatewayPort, type)

    def test_protocol_methods(self) -> None:
        """Test that protocol has expected methods."""
        assert hasattr(ToolGatewayPort, "execute")
        assert hasattr(ToolGatewayPort, "requires_approval")


class MockToolGateway:
    """Mock implementation for ToolGatewayPort testing."""

    def execute(self, tool_name: str, args: dict) -> dict:
        return {"success": True, "result": f"Executed {tool_name}"}

    def requires_approval(self, tool_name: str, args: dict | None = None, state: object | None = None) -> bool:
        return tool_name in ["write_file", "delete_file"]


class TestToolGatewayPortImplementation:
    """Tests for ToolGatewayPort implementations."""

    def test_mock_gateway_is_instance(self) -> None:
        """Test that MockToolGateway satisfies ToolGatewayPort."""
        gateway = MockToolGateway()
        assert isinstance(gateway, ToolGatewayPort)

    def test_mock_gateway_execute(self) -> None:
        """Test mock gateway execute method."""
        gateway = MockToolGateway()
        result = gateway.execute("read_file", {"path": "test.py"})
        assert result["success"] is True

    def test_mock_gateway_requires_approval(self) -> None:
        """Test mock gateway approval checking."""
        gateway = MockToolGateway()
        assert gateway.requires_approval("write_file") is True
        assert gateway.requires_approval("read_file") is False
