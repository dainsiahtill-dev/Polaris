"""Tests for polaris.cells.roles.kernel.public.role_alias."""

from __future__ import annotations

from polaris.cells.roles.kernel.public.role_alias import (
    ROLE_ALIASES,
    normalize_role_alias,
)


class TestRoleAliasesConstant:
    """Tests for the ROLE_ALIASES constant."""

    def test_role_aliases_is_dict(self) -> None:
        assert isinstance(ROLE_ALIASES, dict)

    def test_role_aliases_has_docs_key(self) -> None:
        assert "docs" in ROLE_ALIASES
        assert ROLE_ALIASES["docs"] == "architect"

    def test_role_aliases_has_auditor_key(self) -> None:
        assert "auditor" in ROLE_ALIASES
        assert ROLE_ALIASES["auditor"] == "qa"

    def test_role_aliases_keys_are_lowercase(self) -> None:
        for key in ROLE_ALIASES:
            assert key == key.lower()

    def test_role_aliases_values_are_lowercase(self) -> None:
        for value in ROLE_ALIASES.values():
            assert value == value.lower()

    def test_role_aliases_length(self) -> None:
        assert len(ROLE_ALIASES) == 2


class TestNormalizeRoleAlias:
    """Tests for normalize_role_alias function."""

    def test_normalize_docs_alias(self) -> None:
        assert normalize_role_alias("docs") == "architect"

    def test_normalize_auditor_alias(self) -> None:
        assert normalize_role_alias("auditor") == "qa"

    def test_normalize_canonical_architect(self) -> None:
        assert normalize_role_alias("architect") == "architect"

    def test_normalize_canonical_qa(self) -> None:
        assert normalize_role_alias("qa") == "qa"

    def test_normalize_uppercase_docs(self) -> None:
        assert normalize_role_alias("DOCS") == "architect"

    def test_normalize_uppercase_auditor(self) -> None:
        assert normalize_role_alias("AUDITOR") == "qa"

    def test_normalize_mixed_case(self) -> None:
        assert normalize_role_alias("DoCs") == "architect"

    def test_normalize_with_whitespace(self) -> None:
        assert normalize_role_alias("  docs  ") == "architect"

    def test_normalize_unknown_role(self) -> None:
        assert normalize_role_alias("unknown") == "unknown"

    def test_normalize_unknown_uppercase(self) -> None:
        assert normalize_role_alias("UNKNOWN") == "unknown"

    def test_normalize_empty_string(self) -> None:
        assert normalize_role_alias("") == ""

    def test_normalize_none(self) -> None:
        assert normalize_role_alias(None) == ""  # type: ignore[arg-type]

    def test_normalize_whitespace_only(self) -> None:
        assert normalize_role_alias("   ") == ""

    def test_normalize_non_string_input(self) -> None:
        assert normalize_role_alias(123) == "123"  # type: ignore[arg-type]

    def test_normalize_special_characters(self) -> None:
        assert normalize_role_alias("docs!") == "docs!"

    def test_normalize_returns_lowercase(self) -> None:
        result = normalize_role_alias("ARCHITECT")
        assert result == "architect"
        assert result.islower()

    def test_normalize_strips_input(self) -> None:
        result = normalize_role_alias("  architect  ")
        assert result == "architect"
        assert " " not in result

    def test_role_aliases_is_immutable_reference(self) -> None:
        original = dict(ROLE_ALIASES)
        # Modifying a copy should not affect the original
        copy = dict(ROLE_ALIASES)
        copy["new"] = "value"
        assert original == ROLE_ALIASES


class TestModuleExports:
    """Tests for module __all__ exports."""

    def test_all_exports_present(self) -> None:
        from polaris.cells.roles.kernel.public import role_alias as mod

        assert hasattr(mod, "__all__")
        assert "ROLE_ALIASES" in mod.__all__
        assert "normalize_role_alias" in mod.__all__
        assert len(mod.__all__) == 2
