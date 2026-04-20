"""Pytest Fixtures for LLM Evaluation Benchmarks.

This module provides pytest fixtures for LLM evaluation including:
- Token trackers with predefined pricing
- Judge instances (heuristic and LLM)
- Test case factories
- Mock LLM agents

Example
-------
    import pytest
    from polaris.kernelone.benchmark.llm.fixtures import (
        token_tracker,
        heuristic_judge,
        rag_test_case,
        tool_call_test_case,
    )

    def test_llm_quality(token_tracker):
        record = token_tracker.track("claude-3-opus", {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
        })
        assert record.cost_estimate_usd > 0

    def test_tool_accuracy(tool_call_test_case):
        assert tool_call_test_case.expected_tool
"""

from __future__ import annotations

import pytest
from polaris.kernelone.benchmark.llm.evaluation import (
    BatchEvaluator,
    HeuristicJudge,
    LLMQualityMetrics,
)
from polaris.kernelone.benchmark.llm.rag_metrics import (
    RAGEvaluator,
    RAGMetrics,
    RAGTestCase,
    RetrievalEvaluator,
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

# ------------------------------------------------------------------
# Token Tracker Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def default_pricing() -> dict[str, tuple[float, float]]:
    """Default model pricing fixture."""
    return {
        "claude-3-opus": (0.015, 0.075),
        "claude-3-sonnet": (0.003, 0.015),
        "claude-3.5-sonnet": (0.003, 0.015),
        "gpt-4": (0.03, 0.06),
        "gpt-4-turbo": (0.01, 0.03),
        "gpt-4o": (0.005, 0.015),
        "gpt-3.5-turbo": (0.0005, 0.0015),
    }


@pytest.fixture
def token_tracker(default_pricing: dict[str, tuple[float, float]]) -> TokenTracker:
    """Token tracker fixture with default pricing."""
    return TokenTracker(pricing=default_pricing)


@pytest.fixture
def sample_token_usage() -> dict[str, int]:
    """Sample token usage fixture."""
    return {
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "total_tokens": 1500,
    }


@pytest.fixture
def budget_tracker(
    default_pricing: dict[str, tuple[float, float]],
) -> BudgetTracker:
    """Budget tracker fixture with $10 limit."""
    return BudgetTracker(
        budget_limit_usd=10.0,
        pricing=default_pricing,
        warning_threshold=0.8,
    )


# ------------------------------------------------------------------
# Judge Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def heuristic_judge() -> HeuristicJudge:
    """Heuristic judge fixture with standard patterns."""
    return HeuristicJudge(
        required_patterns=["answer", "result", "conclusion"],
        forbidden_patterns=["undefined", "null", "N/A", "unknown"],
        max_response_length=10000,
        min_response_length=10,
    )


@pytest.fixture
def simple_heuristic_judge() -> HeuristicJudge:
    """Simple heuristic judge without patterns."""
    return HeuristicJudge()


@pytest.fixture
def batch_evaluator(heuristic_judge: HeuristicJudge) -> BatchEvaluator:
    """Batch evaluator fixture."""
    return BatchEvaluator(judge=heuristic_judge, pass_threshold=0.7)


# ------------------------------------------------------------------
# RAG Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def rag_evaluator() -> RAGEvaluator:
    """RAG evaluator fixture."""
    return RAGEvaluator(use_rouge=True, use_bert_score=False)


@pytest.fixture
def rag_test_case() -> RAGTestCase:
    """Standard RAG test case fixture."""
    return RAGTestCase(
        case_id="test_config_lookup",
        query="What is the database host configuration?",
        retrieved_context=(
            "Database config: host=localhost, port=5432",
            "Connection string: postgresql://localhost:5432/db",
        ),
        reference_context=("Database config: host=localhost, port=5432",),
        reference_answer="The database host is localhost on port 5432",
        generated_answer="The database host is localhost with port 5432",
    )


@pytest.fixture
def rag_test_case_noisy() -> RAGTestCase:
    """RAG test case with noisy context."""
    return RAGTestCase(
        case_id="test_noisy_context",
        query="How do I configure the cache?",
        retrieved_context=(
            "Cache settings: enabled=true, ttl=3600",
            "Random info: The weather is nice today",
            "More noise: 123456789",
        ),
        reference_context=("Cache settings: enabled=true, ttl=3600",),
        reference_answer="Cache is enabled with TTL of 3600 seconds",
        generated_answer="Cache is enabled with 3600 second TTL",
    )


@pytest.fixture
def retrieval_evaluator() -> RetrievalEvaluator:
    """Retrieval evaluator fixture."""
    return RetrievalEvaluator()


# ------------------------------------------------------------------
# Tool Call Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def tool_call_test_case() -> ToolCallTestCase:
    """Standard tool call test case fixture."""
    return ToolCallTestCase(
        case_id="test_search_file",
        task_prompt="Search for 'hello' in src/",
        expected_tool="repo_rg",
        expected_params={"pattern": "hello", "path": "src/"},
        description="Test file search capability",
    )


@pytest.fixture
def tool_call_test_cases() -> list[ToolCallTestCase]:
    """List of tool call test cases fixture."""
    return [
        ToolCallTestCase(
            case_id="test_read_config",
            task_prompt="Read the config file",
            expected_tool="repo_read_head",
            expected_params={"path": "config.json"},
            description="Test config reading",
        ),
        ToolCallTestCase(
            case_id="test_search_imports",
            task_prompt="Find all imports",
            expected_tool="repo_rg",
            expected_params={"pattern": "import"},
            description="Test import search",
        ),
        ToolCallTestCase(
            case_id="test_list_dir",
            task_prompt="List src directory",
            expected_tool="repo_tree",
            expected_params={"path": "src"},
            description="Test directory listing",
        ),
    ]


@pytest.fixture
def mock_tool_calling_agent() -> MockToolCallingAgent:
    """Mock agent fixture with successful results."""
    return MockToolCallingAgent(
        results=[
            ToolCallResult(
                case_id="test_read_config",
                tool_called="repo_read_head",
                params={"path": "config.json"},
                success=True,
            ),
            ToolCallResult(
                case_id="test_search_imports",
                tool_called="repo_rg",
                params={"pattern": "import"},
                success=True,
            ),
            ToolCallResult(
                case_id="test_list_dir",
                tool_called="repo_tree",
                params={"path": "src"},
                success=True,
            ),
        ],
    )


@pytest.fixture
def tool_accuracy_benchmark(
    tool_call_test_cases: list[ToolCallTestCase],
) -> ToolCallAccuracyBenchmark:
    """Tool accuracy benchmark fixture."""
    return ToolCallAccuracyBenchmark(test_cases=tool_call_test_cases)


# ------------------------------------------------------------------
# Sample Data Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def sample_llm_response() -> str:
    """Sample LLM response fixture."""
    return (
        "Based on my analysis, the answer is 42. "
        "The configuration shows host=localhost and port=5432. "
        "In conclusion, this setup works correctly."
    )


@pytest.fixture
def sample_reference_answer() -> str:
    """Sample reference answer fixture."""
    return "The answer is 42. The configuration should use host=localhost with port=5432."


@pytest.fixture
def sample_prompt() -> str:
    """Sample user prompt fixture."""
    return "What is the answer to the ultimate question?"


@pytest.fixture
def batch_eval_cases() -> list[tuple[str, str, str]]:
    """Batch evaluation cases fixture."""
    return [
        (
            "What is 2+2?",
            "The answer is 4.",
            "2+2 equals 4.",
        ),
        (
            "What is the capital of France?",
            "Paris is the capital of France.",
            "The capital city of France is Paris.",
        ),
        (
            "Explain photosynthesis.",
            "Photosynthesis converts light to energy.",
            "Photosynthesis is the process by which plants convert sunlight into chemical energy.",
        ),
    ]


# ------------------------------------------------------------------
# Metrics Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def sample_llm_quality_metrics() -> LLMQualityMetrics:
    """Sample LLM quality metrics fixture."""
    return LLMQualityMetrics(
        accuracy_score=0.85,
        hallucination_rate=0.05,
        format_compliance=0.90,
        relevance_score=0.88,
        coherence_score=0.92,
        token_consumed=1500,
        latency_ms=250.0,
    )


@pytest.fixture
def sample_tool_call_metrics() -> ToolCallMetrics:
    """Sample tool call metrics fixture."""
    return ToolCallMetrics(
        tool_selection_accuracy=0.95,
        param_extraction_accuracy=0.88,
        total_calls=100,
        successful_calls=95,
    )


@pytest.fixture
def sample_rag_metrics() -> RAGMetrics:
    """Sample RAG metrics fixture."""
    return RAGMetrics(
        recall=0.85,
        precision=0.90,
        f1=0.87,
        answer_accuracy=0.88,
        context_relevance=0.85,
        answer_relevance=0.90,
        hallucination_rate=0.05,
    )


@pytest.fixture
def sample_token_record(default_pricing: dict[str, tuple[float, float]]) -> TokenConsumptionRecord:
    """Sample token consumption record fixture."""
    tracker = TokenTracker(pricing=default_pricing)
    return tracker.track(
        "claude-3-opus",
        {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
        },
    )


@pytest.fixture
def sample_aggregated_stats() -> AggregatedUsageStats:
    """Sample aggregated usage stats fixture."""
    return AggregatedUsageStats(
        total_prompt_tokens=10000,
        total_completion_tokens=5000,
        total_tokens=15000,
        total_cost_usd=0.525,
        call_count=10,
        model_breakdown={
            "claude-3-opus": {
                "prompt_tokens": 5000,
                "completion_tokens": 2500,
                "total_tokens": 7500,
                "cost_usd": 0.3375,
                "call_count": 5,
            },
            "gpt-4": {
                "prompt_tokens": 5000,
                "completion_tokens": 2500,
                "total_tokens": 7500,
                "cost_usd": 0.1875,
                "call_count": 5,
            },
        },
    )


# ------------------------------------------------------------------
# Parametrized Fixtures
# ------------------------------------------------------------------


def pytest_generate_tests(metafunc) -> None:
    """Generate parametrized test cases."""
    if "quality_dimension" in metafunc.fixturenames:
        metafunc.parametrize(
            "quality_dimension",
            ["accuracy", "hallucination_rate", "format_compliance", "relevance", "coherence"],
        )

    if "tool_call_scenario" in metafunc.fixturenames:
        metafunc.parametrize(
            "tool_call_scenario",
            [
                "success",
                "wrong_tool",
                "wrong_params",
                "both_wrong",
            ],
        )
