"""Unit tests for RoleProviderAdapter."""

from __future__ import annotations

import pytest

from polaris.cells.adapters.kernelone.role_provider_adapter import RoleProviderAdapter
from polaris.kernelone.ports.role_provider import IRoleProvider


class TestRoleProviderAdapter:
    """Tests for RoleProviderAdapter."""

    @pytest.fixture
    def adapter(self) -> RoleProviderAdapter:
        return RoleProviderAdapter()

    def test_is_instance_of_irole_provider(self, adapter: RoleProviderAdapter) -> None:
        assert isinstance(adapter, IRoleProvider)

    def test_normalize_role_alias_canonical(self, adapter: RoleProviderAdapter) -> None:
        assert adapter.normalize_role_alias("pm") == "pm"
        assert adapter.normalize_role_alias("architect") == "architect"

    def test_normalize_role_alias_alias(self, adapter: RoleProviderAdapter) -> None:
        assert adapter.normalize_role_alias("docs") == "architect"
        assert adapter.normalize_role_alias("auditor") == "qa"

    def test_normalize_role_alias_case_insensitive(self, adapter: RoleProviderAdapter) -> None:
        assert adapter.normalize_role_alias("DOCS") == "architect"
        assert adapter.normalize_role_alias("Auditor") == "qa"
        assert adapter.normalize_role_alias("  PM  ") == "pm"

    def test_normalize_role_alias_unknown(self, adapter: RoleProviderAdapter) -> None:
        assert adapter.normalize_role_alias("unknown") == "unknown"

    def test_get_role_aliases(self, adapter: RoleProviderAdapter) -> None:
        aliases = adapter.get_role_aliases()
        assert isinstance(aliases, dict)
        assert aliases.get("docs") == "architect"
        assert aliases.get("auditor") == "qa"
