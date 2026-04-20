"""Integration tests for Role Adapter Factory.

Tests cover adapter factory creation, role registration, and integration patterns.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestAdapterFactoryCreation:
    """Tests for adapter factory creation and registration."""

    def test_factory_creates_pm_adapter(self) -> None:
        """Test factory creates PM adapter correctly."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        adapter = create_role_adapter(role_id="pm", workspace=".")
        assert adapter is not None
        assert adapter.role_id == "pm"

    def test_factory_creates_director_adapter(self) -> None:
        """Test factory creates Director adapter correctly."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        adapter = create_role_adapter(role_id="director", workspace=".")
        assert adapter is not None
        assert adapter.role_id == "director"

    def test_factory_creates_architect_adapter(self) -> None:
        """Test factory creates Architect adapter correctly."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        adapter = create_role_adapter(role_id="architect", workspace=".")
        assert adapter is not None
        assert adapter.role_id == "architect"

    def test_factory_creates_qa_adapter(self) -> None:
        """Test factory creates QA adapter correctly."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        adapter = create_role_adapter(role_id="qa", workspace=".")
        assert adapter is not None
        assert adapter.role_id == "qa"

    def test_factory_normalizes_role_id(self) -> None:
        """Test factory normalizes role ID to lowercase."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        adapter = create_role_adapter(role_id="PM", workspace=".")
        assert adapter.role_id == "pm"

    def test_factory_rejects_empty_role(self) -> None:
        """Test factory rejects empty role ID."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        with pytest.raises(ValueError, match="role"):
            create_role_adapter(role_id="", workspace=".")

    def test_factory_rejects_whitespace_role(self) -> None:
        """Test factory rejects whitespace role ID."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        with pytest.raises(ValueError, match="role"):
            create_role_adapter(role_id="   ", workspace=".")


class TestSupportedRoles:
    """Tests for supported roles functionality."""

    def test_get_supported_roles(self) -> None:
        """Test getting list of supported roles."""
        from polaris.cells.roles.adapters.public.service import get_supported_roles

        roles = get_supported_roles()
        assert isinstance(roles, list)
        assert "pm" in roles
        assert "director" in roles

    def test_pm_in_supported_roles(self) -> None:
        """Test PM is in supported roles."""
        from polaris.cells.roles.adapters.public.service import get_supported_roles

        roles = get_supported_roles()
        assert "pm" in roles


class TestAdapterFactoryIntegration:
    """Integration tests for adapter factory."""

    def test_adapter_has_task_runtime(self) -> None:
        """Test adapter has task runtime property."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        adapter = create_role_adapter(role_id="pm", workspace=".")
        # TaskRuntime should be lazily loaded
        runtime = adapter.task_runtime
        assert runtime is not None

    def test_adapter_get_capabilities(self) -> None:
        """Test adapter returns capabilities."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        adapter = create_role_adapter(role_id="director", workspace=".")
        caps = adapter.get_capabilities()
        assert isinstance(caps, list)
        assert "execute_task" in caps

    def test_multiple_adapters_independent(self) -> None:
        """Test multiple adapters are independent instances."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        adapter1 = create_role_adapter(role_id="pm", workspace=".")
        adapter2 = create_role_adapter(role_id="pm", workspace=".")
        assert adapter1 is not adapter2


class TestAdapterWorkflowIntegration:
    """Tests for adapter workflow integration."""

    def test_adapter_registers_with_factory(self) -> None:
        """Test adapter can register with factory port."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        # Create mock factory port
        mock_factory = MagicMock()

        adapter = create_role_adapter(role_id="qa", workspace=".")
        adapter._register_with_factory(mock_factory)

        # Verify registration was called
        mock_factory.register.assert_called_once_with("qa", adapter)

    def test_build_env_includes_polaris_workspace(self) -> None:
        """Test adapter build_env includes POLARIS_WORKSPACE."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        adapter = create_role_adapter(role_id="architect", workspace="/test/path")
        env = adapter._build_env()

        assert "POLARIS_WORKSPACE" in env
        assert env["POLARIS_WORKSPACE"] == "/test/path"

    def test_build_env_with_overrides(self) -> None:
        """Test adapter build_env respects overrides."""
        from polaris.cells.roles.adapters.public.service import create_role_adapter

        adapter = create_role_adapter(role_id="architect", workspace=".")
        env = adapter._build_env({"CUSTOM_VAR": "custom_value"})

        assert "CUSTOM_VAR" in env
        assert env["CUSTOM_VAR"] == "custom_value"
