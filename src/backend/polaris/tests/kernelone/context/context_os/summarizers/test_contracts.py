"""Tests for summarizer contracts module."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.kernelone.context.context_os.summarizers.contracts import (
    SummarizationError,
    SummarizerInterface,
    SummaryStrategy,
)


class TestSummaryStrategyEnum:
    """Tests for SummaryStrategy enum."""

    def test_generative_member(self) -> None:
        assert SummaryStrategy.GENERATIVE.name == "GENERATIVE"

    def test_slm_member(self) -> None:
        assert SummaryStrategy.SLM.name == "SLM"

    def test_orchestrated_member(self) -> None:
        assert SummaryStrategy.ORCHESTRATED.name == "ORCHESTRATED"

    def test_structured_member(self) -> None:
        assert SummaryStrategy.STRUCTURED.name == "STRUCTURED"

    def test_extractive_member(self) -> None:
        assert SummaryStrategy.EXTRACTIVE.name == "EXTRACTIVE"

    def test_truncation_member(self) -> None:
        assert SummaryStrategy.TRUNCATION.name == "TRUNCATION"

    def test_member_count(self) -> None:
        assert len(SummaryStrategy) == 6

    def test_auto_values_unique(self) -> None:
        values = [s.value for s in SummaryStrategy]
        assert len(values) == len(set(values))

    def test_comparison_by_identity(self) -> None:
        assert SummaryStrategy.GENERATIVE is SummaryStrategy.GENERATIVE
        assert SummaryStrategy.GENERATIVE is not SummaryStrategy.SLM

    def test_enum_iteration(self) -> None:
        strategies = list(SummaryStrategy)
        assert SummaryStrategy.GENERATIVE in strategies
        assert SummaryStrategy.TRUNCATION in strategies


class TestSummarizationError:
    """Tests for SummarizationError exception."""

    def test_basic_exception(self) -> None:
        exc = SummarizationError("Failed to summarize")
        assert str(exc) == "Failed to summarize"
        assert isinstance(exc, Exception)

    def test_exception_with_strategy(self) -> None:
        exc = SummarizationError("Failed", strategy=SummaryStrategy.GENERATIVE)
        assert exc.strategy == SummaryStrategy.GENERATIVE

    def test_exception_with_none_strategy(self) -> None:
        exc = SummarizationError("Failed", strategy=None)
        assert exc.strategy is None

    def test_exception_without_strategy(self) -> None:
        exc = SummarizationError("Failed")
        assert exc.strategy is None

    def test_exception_can_be_raised(self) -> None:
        with pytest.raises(SummarizationError, match="test error"):
            raise SummarizationError("test error")

    def test_exception_with_strategy_can_be_raised(self) -> None:
        with pytest.raises(SummarizationError) as exc_info:
            raise SummarizationError("test", strategy=SummaryStrategy.SLM)
        assert exc_info.value.strategy == SummaryStrategy.SLM

    def test_exception_message_preserved(self) -> None:
        message = "This is a detailed error message"
        exc = SummarizationError(message)
        assert str(exc) == message

    def test_exception_is_runtime_error_subclass(self) -> None:
        assert issubclass(SummarizationError, Exception)


class TestSummarizerInterfaceProtocol:
    """Tests for SummarizerInterface protocol compliance."""

    def test_valid_implementation_is_instance(self) -> None:
        class ValidSummarizer:
            strategy = SummaryStrategy.EXTRACTIVE

            def summarize(self, content: str, max_tokens: int, content_type: str = "text") -> str:
                return content[:max_tokens]

            def estimate_output_tokens(self, input_tokens: int) -> int:
                return input_tokens // 2

            def is_available(self) -> bool:
                return True

        summarizer = ValidSummarizer()
        assert isinstance(summarizer, SummarizerInterface)

    def test_missing_summarize_not_instance(self) -> None:
        class MissingSummarize:
            strategy = SummaryStrategy.EXTRACTIVE

            def estimate_output_tokens(self, input_tokens: int) -> int:
                return 0

            def is_available(self) -> bool:
                return True

        summarizer = MissingSummarize()
        assert not isinstance(summarizer, SummarizerInterface)

    def test_missing_estimate_not_instance(self) -> None:
        class MissingEstimate:
            strategy = SummaryStrategy.EXTRACTIVE

            def summarize(self, content: str, max_tokens: int, content_type: str = "text") -> str:
                return ""

            def is_available(self) -> bool:
                return True

        summarizer = MissingEstimate()
        assert not isinstance(summarizer, SummarizerInterface)

    def test_missing_is_available_not_instance(self) -> None:
        class MissingAvailable:
            strategy = SummaryStrategy.EXTRACTIVE

            def summarize(self, content: str, max_tokens: int, content_type: str = "text") -> str:
                return ""

            def estimate_output_tokens(self, input_tokens: int) -> int:
                return 0

        summarizer = MissingAvailable()
        assert not isinstance(summarizer, SummarizerInterface)

    def test_missing_strategy_not_instance(self) -> None:
        class MissingStrategy:
            def summarize(self, content: str, max_tokens: int, content_type: str = "text") -> str:
                return ""

            def estimate_output_tokens(self, input_tokens: int) -> int:
                return 0

            def is_available(self) -> bool:
                return True

        summarizer = MissingStrategy()
        assert not isinstance(summarizer, SummarizerInterface)

    def test_plain_object_not_instance(self) -> None:
        assert not isinstance(object(), SummarizerInterface)

    def test_none_not_instance(self) -> None:
        assert not isinstance(None, SummarizerInterface)


class TestSummarizerInterfaceImplementation:
    """Tests with a concrete implementation."""

    @pytest.fixture
    def summarizer(self) -> Any:
        class TestSummarizer:
            strategy = SummaryStrategy.TRUNCATION

            def summarize(self, content: str, max_tokens: int, content_type: str = "text") -> str:
                if max_tokens <= 0:
                    raise SummarizationError("Invalid max_tokens", strategy=self.strategy)
                return content[:max_tokens]

            def estimate_output_tokens(self, input_tokens: int) -> int:
                return min(input_tokens, 100)

            def is_available(self) -> bool:
                return True

        return TestSummarizer()

    def test_summarize_returns_string(self, summarizer: Any) -> None:
        result = summarizer.summarize("hello world", 5)
        assert isinstance(result, str)
        assert result == "hello"

    def test_summarize_with_content_type(self, summarizer: Any) -> None:
        result = summarizer.summarize("code", 10, content_type="code")
        assert result == "code"

    def test_estimate_output_tokens(self, summarizer: Any) -> None:
        assert summarizer.estimate_output_tokens(200) == 100
        assert summarizer.estimate_output_tokens(50) == 50

    def test_is_available(self, summarizer: Any) -> None:
        assert summarizer.is_available() is True

    def test_strategy_attribute(self, summarizer: Any) -> None:
        assert summarizer.strategy == SummaryStrategy.TRUNCATION

    def test_summarize_raises_custom_error(self, summarizer: Any) -> None:
        with pytest.raises(SummarizationError):
            summarizer.summarize("test", 0)


class TestSummarizerInterfaceEdgeCases:
    """Edge case tests for SummarizerInterface implementations."""

    def test_empty_content(self) -> None:
        class EmptySummarizer:
            strategy = SummaryStrategy.TRUNCATION

            def summarize(self, content: str, max_tokens: int, content_type: str = "text") -> str:
                return ""

            def estimate_output_tokens(self, input_tokens: int) -> int:
                return 0

            def is_available(self) -> bool:
                return True

        s = EmptySummarizer()
        assert s.summarize("", 100) == ""
        assert s.estimate_output_tokens(1000) == 0

    def test_large_max_tokens(self) -> None:
        class LargeSummarizer:
            strategy = SummaryStrategy.EXTRACTIVE

            def summarize(self, content: str, max_tokens: int, content_type: str = "text") -> str:
                return content

            def estimate_output_tokens(self, input_tokens: int) -> int:
                return input_tokens

            def is_available(self) -> bool:
                return True

        s = LargeSummarizer()
        text = "x" * 10000
        assert s.summarize(text, 100000) == text

    def test_unavailable_summarizer(self) -> None:
        class UnavailableSummarizer:
            strategy = SummaryStrategy.GENERATIVE

            def summarize(self, content: str, max_tokens: int, content_type: str = "text") -> str:
                raise SummarizationError("Not available", strategy=self.strategy)

            def estimate_output_tokens(self, input_tokens: int) -> int:
                return 0

            def is_available(self) -> bool:
                return False

        s = UnavailableSummarizer()
        assert s.is_available() is False
        with pytest.raises(SummarizationError):
            s.summarize("test", 10)

    def test_strategy_is_class_attribute(self) -> None:
        class AttrSummarizer:
            strategy = SummaryStrategy.STRUCTURED

            def summarize(self, content: str, max_tokens: int, content_type: str = "text") -> str:
                return ""

            def estimate_output_tokens(self, input_tokens: int) -> int:
                return 0

            def is_available(self) -> bool:
                return True

        s = AttrSummarizer()
        assert s.strategy == SummaryStrategy.STRUCTURED
        assert AttrSummarizer.strategy == SummaryStrategy.STRUCTURED

    def test_protocol_runtime_checkable(self) -> None:
        # Verify runtime_checkable is working via isinstance checks
        class DummyImpl:
            strategy = SummaryStrategy.TRUNCATION

            def summarize(self, content: str, max_tokens: int, content_type: str = "text") -> str:
                return ""

            def estimate_output_tokens(self, input_tokens: int) -> int:
                return 0

            def is_available(self) -> bool:
                return True

        assert isinstance(DummyImpl(), SummarizerInterface)
