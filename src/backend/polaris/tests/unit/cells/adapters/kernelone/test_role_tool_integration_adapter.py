"""Unit tests for RoleToolIntegrationAdapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from polaris.cells.adapters.kernelone.role_tool_integration_adapter import (
    RoleToolIntegrationAdapter,
)
from polaris.kernelone.ports.role_tool_integration import IRoleToolIntegration


class TestRoleToolIntegrationAdapter:
    """Tests for RoleToolIntegrationAdapter."""

    @pytest.fixture
    def adapter(self) -> RoleToolIntegrationAdapter:
        return RoleToolIntegrationAdapter()

    def test_is_instance_of_irole_tool_integration(self, adapter: RoleToolIntegrationAdapter) -> None:
        assert isinstance(adapter, IRoleToolIntegration)

    def test_get_supported_roles(self, adapter: RoleToolIntegrationAdapter) -> None:
        roles = adapter.get_supported_roles()
        assert isinstance(roles, tuple)
        assert "pm" in roles
        assert "architect" in roles
        assert "chief_engineer" in roles
        assert "director" in roles
        assert "qa" in roles
        assert "scout" in roles

    @patch("polaris.cells.llm.tool_runtime.internal.role_integrations.ROLE_TOOL_INTEGRATIONS")
    def test_get_role_integration_success(self, mock_registry: MagicMock) -> None:
        adapter = RoleToolIntegrationAdapter()
        mock_cls = MagicMock(return_value="integration_instance")
        mock_registry.__getitem__ = MagicMock(return_value=mock_cls)
        result = adapter.get_role_integration("pm", "/workspace")
        mock_registry.__getitem__.assert_called_once_with("pm")
        mock_cls.assert_called_once_with("/workspace")
        assert result == "integration_instance"

    def test_get_role_integration_unknown_role(self, adapter: RoleToolIntegrationAdapter) -> None:
        with pytest.raises(ValueError, match="Unknown role"):
            adapter.get_role_integration("unknown", "/workspace")

    @patch("polaris.cells.llm.tool_runtime.internal.role_integrations.ROLE_TOOL_INTEGRATIONS")
    def test_enhance_role_prompt(self, mock_registry: MagicMock) -> None:
        adapter = RoleToolIntegrationAdapter()
        mock_integration = MagicMock()
        mock_integration.get_system_prompt.return_value = "system prompt"
        mock_cls = MagicMock(return_value=mock_integration)
        mock_registry.__getitem__ = MagicMock(return_value=mock_cls)
        result = adapter.enhance_role_prompt("pm", "base prompt")
        assert "system prompt" in result
        assert "base prompt" in result

    def test_enhance_role_prompt_unknown_role(self, adapter: RoleToolIntegrationAdapter) -> None:
        with pytest.raises(ValueError, match="Unknown role"):
            adapter.enhance_role_prompt("unknown", "base")
