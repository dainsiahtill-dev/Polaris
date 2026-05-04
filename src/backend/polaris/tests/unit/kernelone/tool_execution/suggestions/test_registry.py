"""Tests for polaris.kernelone.tool_execution.suggestions.registry."""

from __future__ import annotations

import pytest
from polaris.kernelone.tool_execution.suggestions.protocols import SuggestionBuilder
from polaris.kernelone.tool_execution.suggestions import registry


class _FakeBuilder(SuggestionBuilder):
    """Fake builder for testing."""

    def __init__(self, name: str, priority: int, should_apply: bool = True, raises: bool = False) -> None:
        self._name = name
        self._priority = priority
        self._should_apply = should_apply
        self._raises = raises

    @property
    def name(self) -> str:
        return self._name

    @property
    def priority(self) -> int:
        return self._priority

    def should_apply(self, error_result: dict) -> bool:
        return self._should_apply

    def build(self, error_result: dict, **kwargs) -> str | None:
        if self._raises:
            raise RuntimeError("builder failure")
        return f"suggestion from {self._name}"


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear registry before each test."""
    with registry._REGISTRY_LOCK:
        registry._BUILDER_REGISTRY.clear()
        registry._SORTED_BUILDERS = None
    yield
    with registry._REGISTRY_LOCK:
        registry._BUILDER_REGISTRY.clear()
        registry._SORTED_BUILDERS = None


class TestRegisterBuilder:
    def test_register_single_builder(self) -> None:
        builder = _FakeBuilder("test", 10)
        registry.register_builder(builder)
        assert "test" in registry._BUILDER_REGISTRY

    def test_register_overwrites_same_name(self) -> None:
        builder1 = _FakeBuilder("test", 10)
        builder2 = _FakeBuilder("test", 20)
        registry.register_builder(builder1)
        registry.register_builder(builder2)
        assert registry._BUILDER_REGISTRY["test"] is builder2

    def test_register_invalidates_cache(self) -> None:
        builder1 = _FakeBuilder("a", 10)
        registry.register_builder(builder1)
        # Prime cache
        sorted_builders = registry._get_sorted_builders()
        assert len(sorted_builders) == 1
        # Add another builder
        builder2 = _FakeBuilder("b", 5)
        registry.register_builder(builder2)
        # Cache should be invalidated
        sorted_builders2 = registry._get_sorted_builders()
        assert len(sorted_builders2) == 2


class TestGetSortedBuilders:
    def test_returns_tuple(self) -> None:
        registry.register_builder(_FakeBuilder("a", 10))
        result = registry._get_sorted_builders()
        assert isinstance(result, tuple)

    def test_tuple_is_immutable(self) -> None:
        registry.register_builder(_FakeBuilder("a", 10))
        result = registry._get_sorted_builders()
        with pytest.raises(TypeError):
            result[0] = _FakeBuilder("x", 1)

    def test_sorted_by_priority_ascending(self) -> None:
        builder_high = _FakeBuilder("high", 100)
        builder_low = _FakeBuilder("low", 1)
        builder_mid = _FakeBuilder("mid", 50)
        registry.register_builder(builder_high)
        registry.register_builder(builder_low)
        registry.register_builder(builder_mid)
        sorted_builders = registry._get_sorted_builders()
        priorities = [b.priority for b in sorted_builders]
        assert priorities == [1, 50, 100]

    def test_cache_consistency_across_calls(self) -> None:
        builder = _FakeBuilder("a", 10)
        registry.register_builder(builder)
        r1 = registry._get_sorted_builders()
        r2 = registry._get_sorted_builders()
        # Should return equivalent tuples
        assert r1 == r2
        # But not the same object (tuple is immutable so this is always true)
        assert r1 is not r2 or len(r1) == 0


class TestBuildSuggestion:
    def test_no_builders_returns_none(self) -> None:
        result = registry.build_suggestion({"error": "something"})
        assert result is None

    def test_first_applicable_builder_wins(self) -> None:
        registry.register_builder(_FakeBuilder("first", 1, should_apply=True))
        registry.register_builder(_FakeBuilder("second", 2, should_apply=True))
        result = registry.build_suggestion({"error": "x"})
        assert result == "suggestion from first"

    def test_skips_non_applicable_builders(self) -> None:
        registry.register_builder(_FakeBuilder("skip", 1, should_apply=False))
        registry.register_builder(_FakeBuilder("apply", 2, should_apply=True))
        result = registry.build_suggestion({"error": "x"})
        assert result == "suggestion from apply"

    def test_isolates_builder_exceptions(self) -> None:
        registry.register_builder(_FakeBuilder("bad", 1, should_apply=True, raises=True))
        registry.register_builder(_FakeBuilder("good", 2, should_apply=True))
        result = registry.build_suggestion({"error": "x"})
        assert result == "suggestion from good"

    def test_isolates_attribute_error(self) -> None:
        class BrokenBuilder:
            name = "broken"
            priority = 1

            def should_apply(self, error_result):
                raise AttributeError("no such attr")

            def build(self, error_result, **kwargs):
                return "never"

        registry.register_builder(BrokenBuilder())  # type: ignore[arg-type]
        registry.register_builder(_FakeBuilder("good", 2, should_apply=True))
        result = registry.build_suggestion({"error": "x"})
        assert result == "suggestion from good"


class TestRegisterDefaultBuilders:
    def test_lazy_registration(self) -> None:
        # After clearing, registry should be empty
        assert len(registry._BUILDER_REGISTRY) == 0
        # Calling build_suggestion triggers lazy registration
        registry.build_suggestion({"error": "x"})
        # Default builders should now be registered
        assert len(registry._BUILDER_REGISTRY) >= 2

    def test_idempotent_registration(self) -> None:
        registry.build_suggestion({"error": "x"})
        count_after_first = len(registry._BUILDER_REGISTRY)
        registry.build_suggestion({"error": "x"})
        count_after_second = len(registry._BUILDER_REGISTRY)
        assert count_after_first == count_after_second
