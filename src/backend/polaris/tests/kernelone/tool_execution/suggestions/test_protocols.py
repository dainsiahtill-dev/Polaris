"""Tests for SuggestionBuilder protocol."""

from __future__ import annotations

from typing import Any

from polaris.kernelone.tool_execution.suggestions.protocols import SuggestionBuilder


class ValidBuilder:
    """A valid implementation of SuggestionBuilder protocol."""

    name = "valid_builder"
    priority = 10

    def should_apply(self, error_result: dict[str, Any]) -> bool:
        return error_result.get("error") == "No matches found"

    def build(self, error_result: dict[str, Any], **kwargs: Any) -> str | None:
        if not self.should_apply(error_result):
            return None
        return f"Suggestion for {error_result.get('error')}"


class MinimalBuilder:
    """Minimal valid implementation with default priority."""

    name = "minimal"
    priority = 50

    def should_apply(self, error_result: dict[str, Any]) -> bool:
        return True

    def build(self, error_result: dict[str, Any], **kwargs: Any) -> str | None:
        return "minimal suggestion"


class NoProtocolBuilder:
    """A class that does not implement the protocol."""

    def __init__(self) -> None:
        pass


class TestSuggestionBuilderProtocol:
    """Tests for SuggestionBuilder protocol compliance."""

    def test_valid_builder_is_instance(self) -> None:
        builder = ValidBuilder()
        assert isinstance(builder, SuggestionBuilder)

    def test_minimal_builder_is_instance(self) -> None:
        builder = MinimalBuilder()
        assert isinstance(builder, SuggestionBuilder)

    def test_no_protocol_builder_is_not_instance(self) -> None:
        builder = NoProtocolBuilder()
        assert not isinstance(builder, SuggestionBuilder)

    def test_object_is_not_instance(self) -> None:
        assert not isinstance(object(), SuggestionBuilder)

    def test_none_is_not_instance(self) -> None:
        assert not isinstance(None, SuggestionBuilder)

    def test_dict_is_not_instance(self) -> None:
        assert not isinstance({}, SuggestionBuilder)


class TestSuggestionBuilderProperties:
    """Tests for SuggestionBuilder property access."""

    def test_name_property(self) -> None:
        builder = ValidBuilder()
        assert builder.name == "valid_builder"

    def test_priority_property(self) -> None:
        builder = ValidBuilder()
        assert builder.priority == 10

    def test_default_priority_value(self) -> None:
        builder = MinimalBuilder()
        assert builder.priority == 50

    def test_name_is_string(self) -> None:
        builder = ValidBuilder()
        assert isinstance(builder.name, str)

    def test_priority_is_int(self) -> None:
        builder = ValidBuilder()
        assert isinstance(builder.priority, int)


class TestSuggestionBuilderShouldApply:
    """Tests for should_apply method."""

    def test_should_apply_returns_true_for_matching_error(self) -> None:
        builder = ValidBuilder()
        error = {"error": "No matches found"}
        assert builder.should_apply(error) is True

    def test_should_apply_returns_false_for_non_matching_error(self) -> None:
        builder = ValidBuilder()
        error = {"error": "Permission denied"}
        assert builder.should_apply(error) is False

    def test_should_apply_with_empty_dict(self) -> None:
        builder = ValidBuilder()
        assert builder.should_apply({}) is False

    def test_should_apply_with_none_error_value(self) -> None:
        builder = ValidBuilder()
        error = {"error": None}
        assert builder.should_apply(error) is False

    def test_minimal_builder_always_true(self) -> None:
        builder = MinimalBuilder()
        assert builder.should_apply({"anything": True}) is True

    def test_should_apply_does_not_mutate_input(self) -> None:
        builder = ValidBuilder()
        error = {"error": "No matches found"}
        original = dict(error)
        builder.should_apply(error)
        assert error == original


class TestSuggestionBuilderBuild:
    """Tests for build method."""

    def test_build_returns_suggestion_for_matching_error(self) -> None:
        builder = ValidBuilder()
        error = {"error": "No matches found"}
        result = builder.build(error)
        assert result is not None
        assert result == "Suggestion for No matches found"

    def test_build_returns_none_for_non_matching_error(self) -> None:
        builder = ValidBuilder()
        error = {"error": "Permission denied"}
        result = builder.build(error)
        assert result is None

    def test_build_with_kwargs(self) -> None:
        builder = MinimalBuilder()
        error = {"error": "test"}
        result = builder.build(error, workspace=".", files=["a.py"])
        assert result == "minimal suggestion"

    def test_build_with_empty_dict(self) -> None:
        builder = ValidBuilder()
        result = builder.build({})
        assert result is None

    def test_build_returns_string_type(self) -> None:
        builder = MinimalBuilder()
        result = builder.build({})
        assert isinstance(result, str)


class TestSuggestionBuilderEdgeCases:
    """Edge case tests for SuggestionBuilder implementations."""

    def test_negative_priority_allowed(self) -> None:
        class NegativePriorityBuilder:
            name = "negative"
            priority = -10

            def should_apply(self, error_result: dict[str, Any]) -> bool:
                return True

            def build(self, error_result: dict[str, Any], **kwargs: Any) -> str | None:
                return ""

        builder = NegativePriorityBuilder()
        assert isinstance(builder, SuggestionBuilder)
        assert builder.priority == -10

    def test_zero_priority_allowed(self) -> None:
        class ZeroPriorityBuilder:
            name = "zero"
            priority = 0

            def should_apply(self, error_result: dict[str, Any]) -> bool:
                return True

            def build(self, error_result: dict[str, Any], **kwargs: Any) -> str | None:
                return ""

        builder = ZeroPriorityBuilder()
        assert isinstance(builder, SuggestionBuilder)
        assert builder.priority == 0

    def test_empty_name_allowed(self) -> None:
        class EmptyNameBuilder:
            name = ""
            priority = 1

            def should_apply(self, error_result: dict[str, Any]) -> bool:
                return True

            def build(self, error_result: dict[str, Any], **kwargs: Any) -> str | None:
                return ""

        builder = EmptyNameBuilder()
        assert isinstance(builder, SuggestionBuilder)
        assert builder.name == ""

    def test_build_returns_none_explicitly(self) -> None:
        class NoneBuilder:
            name = "none"
            priority = 1

            def should_apply(self, error_result: dict[str, Any]) -> bool:
                return True

            def build(self, error_result: dict[str, Any], **kwargs: Any) -> str | None:
                return None

        builder = NoneBuilder()
        result = builder.build({})
        assert result is None
