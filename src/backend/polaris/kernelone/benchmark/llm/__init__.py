"""LLM Evaluation Benchmark Framework.

This module provides comprehensive LLM evaluation capabilities including:
- Quality dimension metrics (accuracy, hallucination, coherence, etc.)
- Token consumption tracking and cost estimation
- Tool call accuracy benchmarking
- RAG context recall and relevance evaluation
- Pytest fixtures for easy testing

Example Usage
-------------

Token Tracking:
    >>> from polaris.kernelone.benchmark.llm import TokenTracker
    >>> tracker = TokenTracker()
    >>> record = tracker.track("claude-3-opus", {
    ...     "prompt_tokens": 1000,
    ...     "completion_tokens": 500,
    ...     "total_tokens": 1500,
    ... })
    >>> print(f"Cost: ${record.cost_estimate_usd:.4f}")

Quality Evaluation:
    >>> from polaris.kernelone.benchmark.llm import HeuristicJudge
    >>> judge = HeuristicJudge(
    ...     required_patterns=["answer", "result"],
    ...     forbidden_patterns=["undefined", "null"],
    ... )
    >>> metrics = judge.evaluate("The answer is 42.")

Tool Call Benchmark:
    >>> from polaris.kernelone.benchmark.llm import (
    ...     ToolCallTestCase,
    ...     ToolCallAccuracyBenchmark,
    ... )
    >>> cases = [
    ...     ToolCallTestCase(
    ...         case_id="search",
    ...         task_prompt="Find 'hello' in src/",
    ...         expected_tool="repo_rg",
    ...         expected_params={"pattern": "hello"},
    ...     ),
    ... ]
    >>> benchmark = ToolCallAccuracyBenchmark(cases)
    >>> result = await benchmark.run(agent)

RAG Evaluation:
    >>> from polaris.kernelone.benchmark.llm import RAGTestCase, RAGEvaluator
    >>> test_case = RAGTestCase(
    ...     case_id="config_lookup",
    ...     query="What is the database host?",
    ...     retrieved_context=["host=localhost"],
    ...     reference_context=["host=localhost"],
    ...     reference_answer="The database host is localhost",
    ...     generated_answer="The host is localhost",
    ... )
    >>> evaluator = RAGEvaluator()
    >>> metrics = evaluator.evaluate(test_case)
"""

from __future__ import annotations

# Evaluation module
from polaris.kernelone.benchmark.llm.evaluation import (
    BatchEvaluationResult,
    BatchEvaluator,
    HeuristicJudge,
    LLMAsJudge,
    LLMJudgePort,
    LLMQualityMetrics,
    QualityDimension,
)

# RAG metrics module
from polaris.kernelone.benchmark.llm.rag_metrics import (
    RAGEvaluator,
    RAGMetrics,
    RAGTestCase,
    RetrievalEvaluator,
    RetrievalMetrics,
    calculate_answer_relevance,
    calculate_context_relevance,
    calculate_hallucination_rate,
    calculate_missing_information,
    calculate_precision,
    calculate_recall,
    calculate_rouge_l,
)

# Token tracking module
from polaris.kernelone.benchmark.llm.token_tracker import (
    DEFAULT_PRICING,
    AggregatedUsageStats,
    BudgetAlert,
    BudgetTracker,
    TokenConsumptionRecord,
    TokenTracker,
)

# Tool accuracy module
from polaris.kernelone.benchmark.llm.tool_accuracy import (
    MockToolCallingAgent,
    ToolCallAccuracyBenchmark,
    ToolCallBenchmarkResult,
    ToolCallingAgentPort,
    ToolCallMetrics,
    ToolCallResult,
    ToolCallTestCase,
    get_standard_tool_test_cases,
)

__all__ = [
    "DEFAULT_PRICING",
    "AggregatedUsageStats",
    "BatchEvaluationResult",
    "BatchEvaluator",
    "BudgetAlert",
    "BudgetTracker",
    "HeuristicJudge",
    "LLMAsJudge",
    "LLMJudgePort",
    "LLMQualityMetrics",
    "MockToolCallingAgent",
    # Evaluation
    "QualityDimension",
    "RAGEvaluator",
    "RAGMetrics",
    # RAG metrics
    "RAGTestCase",
    "RetrievalEvaluator",
    "RetrievalMetrics",
    "TokenConsumptionRecord",
    # Token tracking
    "TokenTracker",
    "ToolCallAccuracyBenchmark",
    "ToolCallBenchmarkResult",
    "ToolCallMetrics",
    "ToolCallResult",
    # Tool accuracy
    "ToolCallTestCase",
    "ToolCallingAgentPort",
    "calculate_answer_relevance",
    "calculate_context_relevance",
    "calculate_hallucination_rate",
    "calculate_missing_information",
    "calculate_precision",
    "calculate_recall",
    "calculate_rouge_l",
    "get_standard_tool_test_cases",
]
