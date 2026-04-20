"""Pytest configuration for LLM Evaluation Benchmark tests.

This module exposes fixtures from fixtures.py to pytest.
"""

from __future__ import annotations

# Re-export all fixtures from fixtures.py
from polaris.kernelone.benchmark.llm.fixtures import (
    batch_eval_cases,
    batch_evaluator,
    budget_tracker,
    # Token Tracker
    default_pricing,
    # Judge
    heuristic_judge,
    mock_tool_calling_agent,
    # RAG
    rag_evaluator,
    rag_test_case,
    rag_test_case_noisy,
    retrieval_evaluator,
    sample_aggregated_stats,
    # Metrics
    sample_llm_quality_metrics,
    # Sample Data
    sample_llm_response,
    sample_prompt,
    sample_rag_metrics,
    sample_reference_answer,
    sample_token_record,
    sample_token_usage,
    sample_tool_call_metrics,
    simple_heuristic_judge,
    token_tracker,
    tool_accuracy_benchmark,
    # Tool Call
    tool_call_test_case,
    tool_call_test_cases,
)

# Make fixtures available to pytest
__all__ = [
    "batch_eval_cases",
    "batch_evaluator",
    "budget_tracker",
    # Token Tracker
    "default_pricing",
    # Judge
    "heuristic_judge",
    "mock_tool_calling_agent",
    # RAG
    "rag_evaluator",
    "rag_test_case",
    "rag_test_case_noisy",
    "retrieval_evaluator",
    "sample_aggregated_stats",
    # Metrics
    "sample_llm_quality_metrics",
    # Sample Data
    "sample_llm_response",
    "sample_prompt",
    "sample_rag_metrics",
    "sample_reference_answer",
    "sample_token_record",
    "sample_token_usage",
    "sample_tool_call_metrics",
    "simple_heuristic_judge",
    "token_tracker",
    "tool_accuracy_benchmark",
    # Tool Call
    "tool_call_test_case",
    "tool_call_test_cases",
]
