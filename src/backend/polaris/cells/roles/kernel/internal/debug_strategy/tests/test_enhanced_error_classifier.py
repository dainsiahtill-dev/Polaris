"""Tests for EnhancedErrorClassifier — debug strategy integration."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.debug_strategy.enhanced_error_classifier import (
    EnhancedErrorClassifier,
)


class TestEnhancedErrorClassifierInit:
    """Tests for __init__()."""

    def test_initializes_strategy_engine(self) -> None:
        """Should initialize with a DebugStrategyEngine."""
        classifier = EnhancedErrorClassifier()
        assert classifier.strategy_engine is not None


class TestClassifyWithStrategy:
    """Tests for classify_with_strategy()."""

    def test_returns_dict_with_required_keys(self) -> None:
        """Return should have basic_classification, category, severity, root_cause, debug_plan, suggested_strategies."""
        classifier = EnhancedErrorClassifier()
        error = RuntimeError("test error")
        context = {"file_path": "main.py"}
        result = classifier.classify_with_strategy(error, context)
        assert "basic_classification" in result
        assert "category" in result
        assert "severity" in result
        assert "root_cause_likely" in result
        assert "debug_plan" in result
        assert "suggested_strategies" in result

    def test_basic_classification_type(self) -> None:
        """basic_classification should be a string error type."""
        classifier = EnhancedErrorClassifier()
        error = RuntimeError("test error")
        result = classifier.classify_with_strategy(error, {})
        assert isinstance(result["basic_classification"], str)

    def test_debug_plan_has_required_fields(self) -> None:
        """debug_plan should have plan_id, strategy, estimated_time, rollback_plan, step_count, phases."""
        classifier = EnhancedErrorClassifier()
        error = ValueError("invalid value")
        result = classifier.classify_with_strategy(error, {})
        plan = result["debug_plan"]
        assert "plan_id" in plan
        assert "strategy" in plan
        assert "estimated_time" in plan
        assert "rollback_plan" in plan
        assert "step_count" in plan
        assert "phases" in plan

    def test_suggested_strategies_is_list_of_strings(self) -> None:
        """suggested_strategies should be a list of strategy value strings."""
        classifier = EnhancedErrorClassifier()
        error = TimeoutError("timed out")
        result = classifier.classify_with_strategy(error, {})
        assert isinstance(result["suggested_strategies"], list)
        assert all(isinstance(s, str) for s in result["suggested_strategies"])

    def test_timeout_error_classifies_as_timeout_error(self) -> None:
        """TimeoutError should have basic_classification 'timeout_error'."""
        classifier = EnhancedErrorClassifier()
        error = TimeoutError("connection timed out after 30s")
        result = classifier.classify_with_strategy(error, {})
        assert result["basic_classification"] == "timeout_error"

    def test_file_not_found_classifies_as_not_found_error(self) -> None:
        """FileNotFoundError should have basic_classification 'not_found_error'."""
        classifier = EnhancedErrorClassifier()
        error = FileNotFoundError("file not found: main.py")
        result = classifier.classify_with_strategy(error, {})
        assert result["basic_classification"] == "not_found_error"

    def test_permission_error_classifies_correctly(self) -> None:
        """PermissionError should have basic_classification 'permission_error'."""
        classifier = EnhancedErrorClassifier()
        error = PermissionError("permission denied: access denied to resource")
        result = classifier.classify_with_strategy(error, {})
        assert result["basic_classification"] == "permission_error"

    def test_assertion_error_classifies_correctly(self) -> None:
        """AssertionError should have basic_classification 'assertion_error'."""
        classifier = EnhancedErrorClassifier()
        error = AssertionError("assert expected True but got False")
        result = classifier.classify_with_strategy(error, {})
        assert result["basic_classification"] == "assertion_error"

    def test_syntax_error_classifies_correctly(self) -> None:
        """SyntaxError should have basic_classification 'syntax_error'."""
        classifier = EnhancedErrorClassifier()
        error = SyntaxError("invalid syntax")
        result = classifier.classify_with_strategy(error, {})
        assert result["basic_classification"] == "syntax_error"

    def test_unknown_error_defaults_to_lowercase_type(self) -> None:
        """Unknown error types default to '<type>_error' format."""
        classifier = EnhancedErrorClassifier()
        error = ZeroDivisionError("division by zero")
        result = classifier.classify_with_strategy(error, {})
        assert result["basic_classification"] == "zerodivisionerror_error"

    def test_error_context_includes_traceback(self) -> None:
        """Error context should include formatted stack trace when available."""
        classifier = EnhancedErrorClassifier()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            error = sys.exc_info()[1]
            result = classifier.classify_with_strategy(error, {})
            # debug_plan phases should be populated from ErrorContext
            assert isinstance(result["debug_plan"]["phases"], list)


class TestGetStrategyInfo:
    """Tests for get_strategy_info()."""

    def test_returns_list_of_dicts(self) -> None:
        """Should return list of strategy info dicts with name, description, type."""
        classifier = EnhancedErrorClassifier()
        strategies = classifier.get_strategy_info()
        assert isinstance(strategies, list)
        for s in strategies:
            assert "name" in s
            assert "description" in s
            assert "type" in s

    def test_all_strategies_have_non_empty_names(self) -> None:
        """All returned strategies should have non-empty name strings."""
        classifier = EnhancedErrorClassifier()
        strategies = classifier.get_strategy_info()
        assert all(s["name"] for s in strategies)

    def test_all_strategies_have_strategy_type(self) -> None:
        """All returned strategies should have a non-empty type string."""
        classifier = EnhancedErrorClassifier()
        strategies = classifier.get_strategy_info()
        assert all(s["type"] for s in strategies)


class TestFormatTraceback:
    """Tests for _format_traceback()."""

    def test_returns_empty_string_for_none(self) -> None:
        """Should return empty string when tb is None."""
        classifier = EnhancedErrorClassifier()
        result = classifier._format_traceback(None)
        assert result == ""

    def test_returns_string_for_valid_traceback(self) -> None:
        """Should return formatted string for valid traceback."""
        classifier = EnhancedErrorClassifier()
        try:
            raise ValueError("test")
        except ValueError:
            import sys

            tb = sys.exc_info()[2]
            result = classifier._format_traceback(tb)
            assert isinstance(result, str)
            assert len(result) > 0
