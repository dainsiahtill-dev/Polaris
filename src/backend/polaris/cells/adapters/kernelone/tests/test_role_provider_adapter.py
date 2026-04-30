"""Tests for polaris.cells.adapters.kernelone.role_provider_adapter."""

from __future__ import annotations

from polaris.cells.adapters.kernelone.role_provider_adapter import (
    RoleProviderAdapter,
)
from polaris.kernelone.ports.role_provider import IRoleProvider


class TestRoleProviderAdapterCreation:
    """Tests for RoleProviderAdapter creation."""

    def test_can_instantiate(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter is not None
        assert isinstance(adapter, RoleProviderAdapter)

    def test_implements_interface(self) -> None:
        adapter = RoleProviderAdapter()
        assert isinstance(adapter, IRoleProvider)

    def test_is_abstract_base_subclass(self) -> None:
        assert issubclass(RoleProviderAdapter, IRoleProvider)


class TestNormalizeRoleAlias:
    """Tests for normalize_role_alias method."""

    def test_normalize_docs_alias(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias("docs") == "architect"

    def test_normalize_auditor_alias(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias("auditor") == "qa"

    def test_normalize_canonical_architect(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias("architect") == "architect"

    def test_normalize_canonical_qa(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias("qa") == "qa"

    def test_normalize_uppercase(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias("DOCS") == "architect"

    def test_normalize_mixed_case(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias("DoCs") == "architect"

    def test_normalize_with_whitespace(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias("  docs  ") == "architect"

    def test_normalize_unknown_role(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias("unknown") == "unknown"

    def test_normalize_empty_string(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias("") == ""

    def test_normalize_none(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias(None) == ""  # type: ignore[arg-type]

    def test_normalize_returns_lowercase(self) -> None:
        adapter = RoleProviderAdapter()
        result = adapter.normalize_role_alias("ARCHITECT")
        assert result == "architect"
        assert result.islower()

    def test_normalize_non_string_input(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias(123) == "123"  # type: ignore[arg-type]


class TestGetRoleAliases:
    """Tests for get_role_aliases method."""

    def test_returns_dict(self) -> None:
        adapter = RoleProviderAdapter()
        aliases = adapter.get_role_aliases()
        assert isinstance(aliases, dict)

    def test_contains_docs_mapping(self) -> None:
        adapter = RoleProviderAdapter()
        aliases = adapter.get_role_aliases()
        assert "docs" in aliases
        assert aliases["docs"] == "architect"

    def test_contains_auditor_mapping(self) -> None:
        adapter = RoleProviderAdapter()
        aliases = adapter.get_role_aliases()
        assert "auditor" in aliases
        assert aliases["auditor"] == "qa"

    def test_returns_copy(self) -> None:
        adapter = RoleProviderAdapter()
        aliases1 = adapter.get_role_aliases()
        aliases2 = adapter.get_role_aliases()
        assert aliases1 is not aliases2
        assert aliases1 == aliases2

    def test_modifying_returned_dict_does_not_affect_adapter(self) -> None:
        adapter = RoleProviderAdapter()
        aliases = adapter.get_role_aliases()
        aliases["new_alias"] = "new_role"
        aliases2 = adapter.get_role_aliases()
        assert "new_alias" not in aliases2

    def test_length(self) -> None:
        adapter = RoleProviderAdapter()
        aliases = adapter.get_role_aliases()
        assert len(aliases) == 2

    def test_keys_are_lowercase(self) -> None:
        adapter = RoleProviderAdapter()
        aliases = adapter.get_role_aliases()
        for key in aliases:
            assert key == key.lower()

    def test_values_are_lowercase(self) -> None:
        adapter = RoleProviderAdapter()
        aliases = adapter.get_role_aliases()
        for value in aliases.values():
            assert value == value.lower()


class TestAdapterConsistency:
    """Tests for consistency between methods."""

    def test_aliases_match_normalization(self) -> None:
        adapter = RoleProviderAdapter()
        aliases = adapter.get_role_aliases()
        for alias, canonical in aliases.items():
            assert adapter.normalize_role_alias(alias) == canonical

    def test_consistency_across_instances(self) -> None:
        adapter1 = RoleProviderAdapter()
        adapter2 = RoleProviderAdapter()
        assert adapter1.get_role_aliases() == adapter2.get_role_aliases()
        assert adapter1.normalize_role_alias("docs") == adapter2.normalize_role_alias("docs")


class TestAdapterDocstringExamples:
    """Tests for docstring examples."""

    def test_docstring_example_auditor(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias("auditor") == "qa"

    def test_docstring_example_architect_uppercase(self) -> None:
        adapter = RoleProviderAdapter()
        assert adapter.normalize_role_alias("ARCHITECT") == "architect"

    def test_docstring_example_get_aliases(self) -> None:
        adapter = RoleProviderAdapter()
        aliases = adapter.get_role_aliases()
        assert aliases == {"docs": "architect", "auditor": "qa"}
