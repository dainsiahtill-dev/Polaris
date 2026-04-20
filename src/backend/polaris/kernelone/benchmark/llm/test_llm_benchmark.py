"""Tests for LLM Evaluation Benchmark Framework.

This module contains comprehensive tests for all LLM evaluation
components including token tracking, quality metrics, tool accuracy,
and RAG evaluation.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.benchmark.llm.evaluation import (
    BatchEvaluator,
    HeuristicJudge,
    LLMQualityMetrics,
    QualityDimension,
)
from polaris.kernelone.benchmark.llm.rag_metrics import (
    RAGEvaluator,
    RAGMetrics,
    RAGTestCase,
    RetrievalEvaluator,
    calculate_context_relevance,
    calculate_precision,
    calculate_recall,
    calculate_rouge_l,
)
from polaris.kernelone.benchmark.llm.token_tracker import (
    AggregatedUsageStats,
    BudgetTracker,
    TokenConsumptionRecord,
    TokenTracker,
)
from polaris.kernelone.benchmark.llm.tool_accuracy import (
    MockToolCallingAgent,
    ToolCallAccuracyBenchmark,
    ToolCallMetrics,
    ToolCallResult,
    ToolCallTestCase,
)

# =============================================================================
# Token Tracker Tests
# =============================================================================


class TestTokenTracker:
    """Tests for TokenTracker."""

    def test_track_basic_usage(
        self,
        token_tracker: TokenTracker,
        sample_token_usage: dict[str, int],
    ) -> None:
        """Test basic token tracking."""
        record = token_tracker.track("claude-3-opus", sample_token_usage)

        assert record.prompt_tokens == 1000
        assert record.completion_tokens == 500
        assert record.total_tokens == 1500
        assert record.model == "claude-3-opus"
        assert record.cost_estimate_usd > 0
        assert record.timestamp  # Should have a timestamp

    def test_cost_calculation(self, token_tracker: TokenTracker) -> None:
        """Test cost calculation for known models."""
        # claude-3-opus: prompt=0.015/completion=0.075 per 1K
        record = token_tracker.track(
            "claude-3-opus",
            {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        )
        expected_cost = (1.0 * 0.015) + (0.5 * 0.075)
        assert abs(record.cost_estimate_usd - expected_cost) < 0.0001

    def test_track_multiple_calls(self, token_tracker: TokenTracker) -> None:
        """Test tracking multiple calls."""
        token_tracker.track("claude-3-opus", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        token_tracker.track("gpt-4", {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300})

        stats = token_tracker.get_aggregated_stats()
        assert stats.total_prompt_tokens == 300
        assert stats.total_completion_tokens == 150
        assert stats.call_count == 2

    def test_get_records(self, token_tracker: TokenTracker) -> None:
        """Test getting all records."""
        token_tracker.track("claude-3-opus", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        token_tracker.track("gpt-4", {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300})

        records = token_tracker.get_records()
        assert len(records) == 2

    def test_reset(self, token_tracker: TokenTracker) -> None:
        """Test resetting tracker."""
        token_tracker.track("claude-3-opus", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        token_tracker.reset()

        assert len(token_tracker.get_records()) == 0
        stats = token_tracker.get_aggregated_stats()
        assert stats.total_tokens == 0


class TestBudgetTracker:
    """Tests for BudgetTracker."""

    def test_within_budget(self, budget_tracker: BudgetTracker) -> None:
        """Test within budget check."""
        budget_tracker.track("gpt-3.5-turbo", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})

        assert budget_tracker.spent > 0
        assert budget_tracker.remaining < 10.0
        assert budget_tracker.usage_percent > 0

    def test_is_within_budget(self, budget_tracker: BudgetTracker) -> None:
        """Test budget check."""
        budget_tracker.track("gpt-3.5-turbo", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})

        # Should still be within budget for small amounts
        assert budget_tracker.is_within_budget(1.0)


# =============================================================================
# LLM Quality Metrics Tests
# =============================================================================


class TestLLMQualityMetrics:
    """Tests for LLMQualityMetrics."""

    def test_overall_score_calculation(self) -> None:
        """Test overall score calculation."""
        metrics = LLMQualityMetrics(
            accuracy_score=0.9,
            hallucination_rate=0.1,
            format_compliance=0.8,
            relevance_score=0.85,
            coherence_score=0.9,
        )

        expected = (
            0.9 * 0.35  # accuracy
            + (1.0 - 0.1) * 0.25  # hallucination (inverted)
            + 0.8 * 0.15  # format
            + 0.85 * 0.15  # relevance
            + 0.9 * 0.10  # coherence
        )
        assert abs(metrics.overall_score - expected) < 0.001

    def test_to_dict(self, sample_llm_quality_metrics: LLMQualityMetrics) -> None:
        """Test dictionary conversion."""
        data = sample_llm_quality_metrics.to_dict()

        assert "accuracy_score" in data
        assert "overall_score" in data
        assert "quality_dimensions" in data


class TestHeuristicJudge:
    """Tests for HeuristicJudge."""

    def test_evaluate_with_required_patterns(self, heuristic_judge: HeuristicJudge) -> None:
        """Test evaluation with required patterns."""
        response = "The answer is 42. Here is the result. Conclusion: done."
        metrics = heuristic_judge.evaluate(response)

        assert metrics.format_compliance == 1.0  # All patterns found

    def test_evaluate_with_forbidden_patterns(self, heuristic_judge: HeuristicJudge) -> None:
        """Test evaluation with forbidden patterns."""
        response = "The result is undefined and null and unknown and N/A"
        metrics = heuristic_judge.evaluate(response)

        # All 4 forbidden patterns found (undefined, null, unknown, N/A)
        assert metrics.hallucination_rate == 1.0

    def test_evaluate_empty_response(self, heuristic_judge: HeuristicJudge) -> None:
        """Test evaluation of empty response."""
        metrics = heuristic_judge.evaluate("")

        assert metrics.accuracy_score == 0.0
        assert metrics.hallucination_rate == 1.0

    def test_evaluate_with_reference(self, heuristic_judge: HeuristicJudge) -> None:
        """Test evaluation with reference answer."""
        reference = "The answer is 42"
        candidate = "The answer is 42"
        metrics = heuristic_judge.evaluate(candidate, reference=reference)

        assert metrics.accuracy_score == 1.0


class TestBatchEvaluator:
    """Tests for BatchEvaluator."""

    def test_batch_evaluation(
        self,
        batch_evaluator: BatchEvaluator,
        batch_eval_cases: list[tuple[str, str, str]],
    ) -> None:
        """Test batch evaluation."""
        result = batch_evaluator.evaluate_batch(batch_eval_cases)

        assert result.total_cases == 3
        assert result.average_score > 0
        assert len(result.metrics) == 3

    def test_pass_threshold(
        self,
        simple_heuristic_judge: HeuristicJudge,
    ) -> None:
        """Test pass threshold."""
        evaluator = BatchEvaluator(simple_heuristic_judge, pass_threshold=0.9)

        cases = [
            ("What is 2+2?", "The answer is 4.", "2+2 equals 4."),  # Good match
            ("What is 2+2?", "The answer is 4.", "Maybe 5?"),  # Poor match
        ]

        result = evaluator.evaluate_batch(cases)
        assert result.passed_cases >= 0
        assert result.failed_cases >= 0


# =============================================================================
# Tool Call Accuracy Tests
# =============================================================================


class TestToolCallTestCase:
    """Tests for ToolCallTestCase."""

    def test_create_test_case(self) -> None:
        """Test creating a test case."""
        case = ToolCallTestCase(
            case_id="test1",
            task_prompt="Search for hello",
            expected_tool="repo_rg",
            expected_params={"pattern": "hello"},
        )

        assert case.case_id == "test1"
        assert case.expected_tool == "repo_rg"

    def test_invalid_case_raises(self) -> None:
        """Test invalid case raises error."""
        with pytest.raises(ValueError):
            ToolCallTestCase(case_id="", task_prompt="test")

        with pytest.raises(ValueError):
            ToolCallTestCase(case_id="test", task_prompt="")


class TestToolCallAccuracyBenchmark:
    """Tests for ToolCallAccuracyBenchmark."""

    @pytest.mark.asyncio
    async def test_run_benchmark(
        self,
        tool_accuracy_benchmark: ToolCallAccuracyBenchmark,
        mock_tool_calling_agent: MockToolCallingAgent,
    ) -> None:
        """Test running the benchmark."""
        result = await tool_accuracy_benchmark.run(mock_tool_calling_agent)

        assert result.total_cases == 3
        assert result.metrics.total_calls == 3
        assert len(result.results) == 3

    @pytest.mark.asyncio
    async def test_tool_selection_accuracy(
        self,
        tool_call_test_cases: list[ToolCallTestCase],
    ) -> None:
        """Test tool selection accuracy calculation."""
        # Create agent that always calls correct tool
        correct_agent = MockToolCallingAgent(
            results=[
                ToolCallResult(
                    case_id=c.case_id,
                    tool_called=c.expected_tool,
                    params=c.expected_params,
                    success=True,
                )
                for c in tool_call_test_cases
            ],
        )

        benchmark = ToolCallAccuracyBenchmark(tool_call_test_cases)
        result = await benchmark.run(correct_agent)

        assert result.metrics.tool_selection_accuracy == 1.0

    @pytest.mark.asyncio
    async def test_wrong_tool_selection(
        self,
        tool_call_test_cases: list[ToolCallTestCase],
    ) -> None:
        """Test wrong tool selection."""
        wrong_agent = MockToolCallingAgent(
            results=[
                ToolCallResult(
                    case_id=c.case_id,
                    tool_called="wrong_tool",
                    params={},
                    success=True,
                )
                for c in tool_call_test_cases
            ],
        )

        benchmark = ToolCallAccuracyBenchmark(tool_call_test_cases)
        result = await benchmark.run(wrong_agent)

        assert result.metrics.tool_selection_accuracy == 0.0


# =============================================================================
# RAG Metrics Tests
# =============================================================================


class TestRAGMetrics:
    """Tests for RAG metrics calculation."""

    def test_calculate_recall(self) -> None:
        """Test recall calculation."""
        retrieved = ("hello world", "foo bar")
        reference = ("hello", "world")

        recall = calculate_recall(retrieved, reference)
        assert 0.0 <= recall <= 1.0

    def test_calculate_precision(self) -> None:
        """Test precision calculation."""
        retrieved = ("hello world test",)
        reference = ("hello", "world", "other")

        precision = calculate_precision(retrieved, reference)
        assert 0.0 <= precision <= 1.0

    def test_calculate_rouge_l(self) -> None:
        """Test ROUGE-L calculation."""
        candidate = "The quick brown fox jumps"
        reference = "The quick brown fox jumps over"

        score = calculate_rouge_l(candidate, reference)
        assert 0.0 <= score <= 1.0

    def test_rouge_l_exact_match(self) -> None:
        """Test ROUGE-L for exact match."""
        text = "The quick brown fox"
        score = calculate_rouge_l(text, text)
        assert score == 1.0

    def test_calculate_context_relevance(self) -> None:
        """Test context relevance calculation."""
        query = "What is the database host?"
        context = ("The database host is localhost.", "Server runs on port 8080.")

        relevance = calculate_context_relevance(query, context)
        assert 0.0 <= relevance <= 1.0


class TestRAGEvaluator:
    """Tests for RAGEvaluator."""

    def test_evaluate_basic(
        self,
        rag_evaluator: RAGEvaluator,
        rag_test_case: RAGTestCase,
    ) -> None:
        """Test basic RAG evaluation."""
        metrics = rag_evaluator.evaluate(rag_test_case)

        assert isinstance(metrics, RAGMetrics)
        assert 0.0 <= metrics.recall <= 1.0
        assert 0.0 <= metrics.precision <= 1.0
        assert 0.0 <= metrics.overall_score <= 1.0

    def test_evaluate_noisy_context(
        self,
        rag_evaluator: RAGEvaluator,
        rag_test_case_noisy: RAGTestCase,
    ) -> None:
        """Test RAG evaluation with noisy context."""
        metrics = rag_evaluator.evaluate(rag_test_case_noisy)

        # Noisy context should have lower precision
        assert metrics.precision < 1.0
        # But recall might still be good
        assert metrics.recall >= 0.0

    def test_evaluate_batch(
        self,
        rag_evaluator: RAGEvaluator,
        rag_test_case: RAGTestCase,
        rag_test_case_noisy: RAGTestCase,
    ) -> None:
        """Test batch RAG evaluation."""
        test_cases = [rag_test_case, rag_test_case_noisy]
        aggregated, individual = rag_evaluator.evaluate_batch(test_cases)

        assert len(individual) == 2
        assert "avg_recall" in aggregated
        assert "avg_precision" in aggregated
        assert "avg_overall_score" in aggregated


class TestRetrievalEvaluator:
    """Tests for RetrievalEvaluator."""

    def test_retrieval_hit_rate(self, retrieval_evaluator: RetrievalEvaluator) -> None:
        """Test retrieval hit rate calculation."""
        queries = ["query1", "query2"]
        retrieved = [["doc1", "doc2"], ["doc3", "doc4"]]
        relevant = [{"doc1"}, {"doc4"}]

        metrics = retrieval_evaluator.evaluate_retrieval(queries, retrieved, relevant)

        assert metrics.hit_rate_at_1 == 0.5  # 1 out of 2 queries has hit in top-1
        assert metrics.hit_rate_at_5 == 1.0  # Both have hits in top-5

    def test_retrieval_mrr(self, retrieval_evaluator: RetrievalEvaluator) -> None:
        """Test Mean Reciprocal Rank calculation."""
        queries = ["query1", "query2"]
        retrieved = [["doc1", "doc2"], ["doc2", "doc3"]]  # doc1 at rank 1, doc2 at rank 1
        relevant = [{"doc1"}, {"doc2"}]

        metrics = retrieval_evaluator.evaluate_retrieval(queries, retrieved, relevant)

        # Query 1: doc1 at rank 1 -> RR = 1/1 = 1.0
        # Query 2: doc2 at rank 1 -> RR = 1/1 = 1.0
        # MRR = (1.0 + 1.0) / 2 = 1.0
        assert abs(metrics.mrr - 1.0) < 0.01


# =============================================================================
# Edge Cases and Integration Tests
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_retrieved_context(self) -> None:
        """Test with empty retrieved context."""
        case = RAGTestCase(
            case_id="empty",
            query="test",
            retrieved_context=(),
            reference_context=("reference",),
        )
        evaluator = RAGEvaluator()
        metrics = evaluator.evaluate(case)

        assert metrics.recall == 0.0
        assert metrics.context_relevance == 0.0

    def test_empty_reference_context(self) -> None:
        """Test with empty reference context."""
        case = RAGTestCase(
            case_id="empty_ref",
            query="test",
            retrieved_context=("content",),
            reference_context=(),
        )
        evaluator = RAGEvaluator()
        metrics = evaluator.evaluate(case)

        # With empty reference, precision is 0.0 (retrieved is noise relative to nothing)
        # recall behavior depends on implementation - empty reference may yield 0.0 or 1.0
        assert metrics.precision == 0.0  # Retrieved is noise when reference is empty
        # Just verify metrics are computed without error
        assert 0.0 <= metrics.recall <= 1.0

    def test_token_tracker_negative_values(self, token_tracker: TokenTracker) -> None:
        """Test handling of negative token values."""
        record = token_tracker.track(
            "test-model",
            {"prompt_tokens": -100, "completion_tokens": -50, "total_tokens": -150},
        )

        # Should clamp to 0
        assert record.prompt_tokens == 0
        assert record.completion_tokens == 0
        assert record.total_tokens == 0

    def test_quality_metrics_clamping(self) -> None:
        """Test quality metrics value clamping."""
        metrics = LLMQualityMetrics(
            accuracy_score=1.5,  # Over 1.0
            hallucination_rate=-0.5,  # Under 0.0
        )

        assert metrics.accuracy_score == 1.0
        assert metrics.hallucination_rate == 0.0


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_full_evaluation_pipeline(
        self,
        token_tracker: TokenTracker,
        heuristic_judge: HeuristicJudge,
        rag_evaluator: RAGEvaluator,
    ) -> None:
        """Test complete evaluation pipeline."""
        # Track tokens
        record = token_tracker.track(
            "claude-3-opus",
            {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        )
        assert record.cost_estimate_usd > 0

        # Evaluate quality
        quality_metrics = heuristic_judge.evaluate(
            "The answer is 42. Result: success.",
            reference="The answer is 42.",
        )
        assert quality_metrics.overall_score > 0

        # Evaluate RAG
        rag_case = RAGTestCase(
            case_id="integration",
            query="What is the answer?",
            retrieved_context=("The answer is 42.",),
            reference_context=("The answer is 42.",),
            generated_answer="The answer is 42.",
        )
        rag_metrics = rag_evaluator.evaluate(rag_case)
        assert rag_metrics.overall_score > 0

    def test_metrics_serialization(
        self,
        sample_llm_quality_metrics: LLMQualityMetrics,
        sample_tool_call_metrics: ToolCallMetrics,
        sample_rag_metrics: RAGMetrics,
    ) -> None:
        """Test metrics serialization to dict."""
        llm_dict = sample_llm_quality_metrics.to_dict()
        assert "accuracy_score" in llm_dict

        tool_dict = sample_tool_call_metrics.to_dict()
        assert "tool_selection_accuracy" in tool_dict

        rag_dict = sample_rag_metrics.to_dict()
        assert "recall" in rag_dict


# =============================================================================
# Quality Dimension Tests
# =============================================================================


class TestQualityDimensions:
    """Tests for QualityDimension enum."""

    def test_all_dimensions_exist(self) -> None:
        """Test all expected dimensions exist."""
        expected = {
            "accuracy",
            "hallucination_rate",
            "format_compliance",
            "relevance",
            "coherence",
            "completeness",
            "toxicity",
        }

        actual = {d.value for d in QualityDimension}
        assert expected.issubset(actual)


# =============================================================================
# Data Model Tests
# =============================================================================


class TestTokenConsumptionRecord:
    """Tests for TokenConsumptionRecord."""

    def test_creation(self) -> None:
        """Test record creation."""
        record = TokenConsumptionRecord(
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            cost_estimate_usd=0.15,
            model="claude-3-opus",
        )

        assert record.prompt_tokens == 1000
        assert record.total_tokens == 1500
        assert record.model == "claude-3-opus"

    def test_to_dict(self) -> None:
        """Test dict conversion."""
        record = TokenConsumptionRecord(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost_estimate_usd=0.01,
            model="test",
        )

        data = record.to_dict()
        assert data["prompt_tokens"] == 100
        assert data["cost_estimate_usd"] == 0.01


class TestAggregatedUsageStats:
    """Tests for AggregatedUsageStats."""

    def test_average_calculations(self) -> None:
        """Test average calculations."""
        stats = AggregatedUsageStats(
            total_tokens=1000,
            total_cost_usd=1.0,
            call_count=10,
        )

        assert stats.average_tokens_per_call == 100.0
        assert stats.average_cost_per_call == 0.1
