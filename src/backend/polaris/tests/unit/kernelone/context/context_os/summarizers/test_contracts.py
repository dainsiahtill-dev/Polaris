"""Tests for polaris.kernelone.context.context_os.summarizers.contracts."""

from __future__ import annotations

from polaris.kernelone.context.context_os.summarizers.contracts import (
    SummarizationError,
    SummarizerInterface,
    SummaryStrategy,
)


class TestSummaryStrategy:
    def test_members(self) -> None:
        assert SummaryStrategy.GENERATIVE.name == "GENERATIVE"
        assert SummaryStrategy.SLM.name == "SLM"
        assert SummaryStrategy.ORCHESTRATED.name == "ORCHESTRATED"
        assert SummaryStrategy.STRUCTURED.name == "STRUCTURED"
        assert SummaryStrategy.EXTRACTIVE.name == "EXTRACTIVE"
        assert SummaryStrategy.TRUNCATION.name == "TRUNCATION"


class TestSummarizationError:
    def test_with_strategy(self) -> None:
        exc = SummarizationError("failed", strategy=SummaryStrategy.GENERATIVE)
        assert str(exc) == "failed"
        assert exc.strategy == SummaryStrategy.GENERATIVE

    def test_without_strategy(self) -> None:
        exc = SummarizationError("failed")
        assert exc.strategy is None


class TestSummarizerInterface:
    def test_is_protocol(self) -> None:
        assert hasattr(SummarizerInterface, "__subclasshook__")
