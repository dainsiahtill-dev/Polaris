"""Tests for polaris.cells.roles.kernel.public.role_alias."""

from __future__ import annotations

from polaris.cells.roles.kernel.public.role_alias import ROLE_ALIASES, normalize_role_alias


class TestRoleAliases:
    def test_alias_docs_to_architect(self) -> None:
        assert normalize_role_alias("docs") == "architect"

    def test_alias_auditor_to_qa(self) -> None:
        assert normalize_role_alias("auditor") == "qa"

    def test_no_alias_returns_lowercase(self) -> None:
        assert normalize_role_alias("PM") == "pm"
        assert normalize_role_alias("  Architect  ") == "architect"

    def test_canonical_role_unchanged(self) -> None:
        assert normalize_role_alias("architect") == "architect"
        assert normalize_role_alias("qa") == "qa"

    def test_none_returns_empty(self) -> None:
        assert normalize_role_alias(None) == ""  # type: ignore[arg-type]

    def test_empty_returns_empty(self) -> None:
        assert normalize_role_alias("") == ""

    def test_role_aliases_dict_content(self) -> None:
        assert "docs" in ROLE_ALIASES
        assert "auditor" in ROLE_ALIASES
        assert ROLE_ALIASES["docs"] == "architect"
        assert ROLE_ALIASES["auditor"] == "qa"
