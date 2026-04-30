"""Tests for polaris.cells.roles.kernel.internal.kernel.suggestions.

Pure logic testing — no mocks required.
All state is in-memory; the module has no I/O, no network, no LLM calls.
"""

from __future__ import annotations

import pytest

from polaris.cells.roles.kernel.internal.kernel.suggestions import (
    ErrorSuggestionProvider,
    get_suggestion_provider,
    get_suggestions_for_error,
)


# ---------------------------------------------------------------------------
# ErrorSuggestionProvider.__init__
# ---------------------------------------------------------------------------

class TestErrorSuggestionProviderInit:
    def test_default_categories_present(self) -> None:
        provider = ErrorSuggestionProvider()
        categories = provider.get_all_categories()
        assert "timeout" in categories
        assert "rate_limit" in categories
        assert "network" in categories
        assert "auth" in categories
        assert "provider" in categories
        assert "unknown" in categories

    def test_default_suggestion_counts(self) -> None:
        provider = ErrorSuggestionProvider()
        assert len(provider.get_suggestions("timeout")) == 3
        assert len(provider.get_suggestions("rate_limit")) == 3
        assert len(provider.get_suggestions("network")) == 3
        assert len(provider.get_suggestions("auth")) == 3
        assert len(provider.get_suggestions("provider")) == 3
        assert len(provider.get_suggestions("unknown")) == 3


# ---------------------------------------------------------------------------
# ErrorSuggestionProvider.get_suggestions
# ---------------------------------------------------------------------------

class TestGetSuggestions:
    def test_timeout_returns_chinese_suggestions(self) -> None:
        provider = ErrorSuggestionProvider()
        suggestions = provider.get_suggestions("timeout")
        assert any("超时" in s for s in suggestions)

    def test_rate_limit_returns_chinese_suggestions(self) -> None:
        provider = ErrorSuggestionProvider()
        suggestions = provider.get_suggestions("rate_limit")
        assert any("速率限制" in s for s in suggestions)

    def test_network_returns_chinese_suggestions(self) -> None:
        provider = ErrorSuggestionProvider()
        suggestions = provider.get_suggestions("network")
        assert any("网络" in s for s in suggestions)

    def test_auth_returns_chinese_suggestions(self) -> None:
        provider = ErrorSuggestionProvider()
        suggestions = provider.get_suggestions("auth")
        assert any("认证" in s for s in suggestions)

    def test_provider_returns_chinese_suggestions(self) -> None:
        provider = ErrorSuggestionProvider()
        suggestions = provider.get_suggestions("provider")
        assert any("服务提供商" in s for s in suggestions)

    def test_unknown_category_falls_back(self) -> None:
        provider = ErrorSuggestionProvider()
        suggestions = provider.get_suggestions("nonexistent_category")
        assert suggestions == provider.get_suggestions("unknown")

    def test_unknown_returns_sensible_defaults(self) -> None:
        provider = ErrorSuggestionProvider()
        suggestions = provider.get_suggestions("unknown")
        assert any("网络连接" in s for s in suggestions)

    def test_empty_string_returns_unknown(self) -> None:
        provider = ErrorSuggestionProvider()
        assert provider.get_suggestions("") == provider.get_suggestions("unknown")


# ---------------------------------------------------------------------------
# ErrorSuggestionProvider.get_all_categories
# ---------------------------------------------------------------------------

class TestGetAllCategories:
    def test_returns_list(self) -> None:
        provider = ErrorSuggestionProvider()
        categories = provider.get_all_categories()
        assert isinstance(categories, list)
        assert len(categories) == 6

    def test_no_duplicates(self) -> None:
        provider = ErrorSuggestionProvider()
        categories = provider.get_all_categories()
        assert len(categories) == len(set(categories))

    def test_snapshot_independence(self) -> None:
        provider = ErrorSuggestionProvider()
        c1 = provider.get_all_categories()
        c2 = provider.get_all_categories()
        assert c1 == c2
        c1.append("tampered")
        assert "tampered" not in provider.get_all_categories()


# ---------------------------------------------------------------------------
# ErrorSuggestionProvider.add_suggestion_map
# ---------------------------------------------------------------------------

class TestAddSuggestionMap:
    def test_add_new_category(self) -> None:
        provider = ErrorSuggestionProvider()
        provider.add_suggestion_map("quota_exceeded", ["Quota exceeded suggestion 1", "Suggestion 2"])
        assert "quota_exceeded" in provider.get_all_categories()
        assert len(provider.get_suggestions("quota_exceeded")) == 2

    def test_add_overwrites_existing(self) -> None:
        provider = ErrorSuggestionProvider()
        provider.add_suggestion_map("timeout", ["new single suggestion"])
        assert provider.get_suggestions("timeout") == ["new single suggestion"]

    def test_add_empty_category_ignored(self) -> None:
        provider = ErrorSuggestionProvider()
        provider.add_suggestion_map("", ["suggestion"])
        assert "" not in provider.get_all_categories()

    def test_add_empty_suggestions_ignored(self) -> None:
        provider = ErrorSuggestionProvider()
        provider.add_suggestion_map("new_cat", [])
        assert "new_cat" not in provider.get_all_categories()

    def test_add_none_category_ignored(self) -> None:
        provider = ErrorSuggestionProvider()
        provider.add_suggestion_map(None, ["suggestion"])  # type: ignore[arg-type]
        assert "None" not in provider.get_all_categories()

    def test_add_none_suggestions_ignored(self) -> None:
        provider = ErrorSuggestionProvider()
        provider.add_suggestion_map("new_cat2", None)  # type: ignore[arg-type]
        assert "new_cat2" not in provider.get_all_categories()

    def test_add_copies_list(self) -> None:
        provider = ErrorSuggestionProvider()
        original = ["a", "b"]
        provider.add_suggestion_map("copy_test", original)
        original.append("c")
        assert len(provider.get_suggestions("copy_test")) == 2


# ---------------------------------------------------------------------------
# ErrorSuggestionProvider.to_dict
# ---------------------------------------------------------------------------

class TestToDict:
    def test_returns_dict(self) -> None:
        provider = ErrorSuggestionProvider()
        d = provider.to_dict()
        assert isinstance(d, dict)
        assert len(d) == 6

    def test_contains_expected_keys(self) -> None:
        provider = ErrorSuggestionProvider()
        d = provider.to_dict()
        assert "timeout" in d
        assert "rate_limit" in d
        assert "network" in d
        assert "auth" in d
        assert "provider" in d
        assert "unknown" in d

    def test_isolated_snapshot(self) -> None:
        provider = ErrorSuggestionProvider()
        d = provider.to_dict()
        d["timeout"].append("tampered")
        assert "tampered" not in provider.get_suggestions("timeout")

    def test_reflects_additions(self) -> None:
        provider = ErrorSuggestionProvider()
        provider.add_suggestion_map("extra", ["extra_suggestion"])
        d = provider.to_dict()
        assert "extra" in d
        assert d["extra"] == ["extra_suggestion"]


# ---------------------------------------------------------------------------
# get_suggestion_provider singleton
# ---------------------------------------------------------------------------

class TestGetSuggestionProvider:
    def test_returns_provider(self) -> None:
        provider = get_suggestion_provider()
        assert isinstance(provider, ErrorSuggestionProvider)

    def test_singleton_identity(self) -> None:
        p1 = get_suggestion_provider()
        p2 = get_suggestion_provider()
        assert p1 is p2

    def test_singleton_state_persists(self) -> None:
        p1 = get_suggestion_provider()
        p1.add_suggestion_map("singleton_test", ["st1"])
        p2 = get_suggestion_provider()
        assert "singleton_test" in p2.get_all_categories()


# ---------------------------------------------------------------------------
# get_suggestions_for_error
# ---------------------------------------------------------------------------

class TestGetSuggestionsForError:
    def test_delegates_to_provider(self) -> None:
        result = get_suggestions_for_error("timeout")
        assert isinstance(result, list)
        assert len(result) == 3

    def test_unknown_category_fallback(self) -> None:
        result = get_suggestions_for_error("does_not_exist")
        assert result == get_suggestions_for_error("unknown")

    def test_empty_string_fallback(self) -> None:
        result = get_suggestions_for_error("")
        assert result == get_suggestions_for_error("unknown")
