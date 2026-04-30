"""Context Benchmark Adapter Tests.

Test Strategy:
    - Unit Tests: 独立测试 adapter.evaluate()
    - Integration Tests: 测试与 metrics 模块集成
    - Error Handling Tests: 测试异常传播
    - DI Tests: 测试依赖注入行为

Example:
    pytest polaris/kernelone/benchmark/tests/test_context_adapter.py -v
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from polaris.kernelone.benchmark.adapters.context_adapter import (
    ContextBenchmarkAdapter,
    ContextCompilationError,
    ContextCompilationResult,
    ContextCompilerPort,
    MetricsCalculatorPort,
)
from polaris.kernelone.benchmark.unified_models import (
    JudgeConfig,
    ObservedBenchmarkRun,
    UnifiedBenchmarkCase,
)

if TYPE_CHECKING:
    from pathlib import Path

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def sample_case() -> UnifiedBenchmarkCase:
    """Sample benchmark case for testing."""
    return UnifiedBenchmarkCase(
        case_id="locate_bug_root_cause",
        role="director",
        title="Locate Bug Root Cause",
        prompt="Find the bug in polaris/kernelone/context/strategy_*.py",
        expected_evidence_path=(
            "polaris/kernelone/context/strategy_benchmark.py",
            "polaris/kernelone/context/strategy_scoring.py",
            "polaris/kernelone/context/strategy_receipts.py",
        ),
        judge=JudgeConfig(
            score_threshold=0.70,
            mode="context",
        ),
    )


@pytest.fixture
def temp_workspace(tmp_path: Path) -> str:
    """Temporary workspace path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return str(workspace)


# ------------------------------------------------------------------
# Test Cases
# ------------------------------------------------------------------


class TestContextBenchmarkAdapter:
    """Adapter evaluation logic tests."""

    def test_evaluate_returns_observed_run(
        self,
        sample_case: UnifiedBenchmarkCase,
        temp_workspace: str,
    ) -> None:
        """evaluate() returns ObservedBenchmarkRun with metrics."""
        adapter = ContextBenchmarkAdapter(
            compiler=_FakeCompiler(selected_files=["src/scoring.py", "src/receipts.py"]),
            metrics_calculator=_FakeMetricsCalculator(),
        )

        result = adapter.evaluate(sample_case, temp_workspace)

        assert isinstance(result, ObservedBenchmarkRun)
        assert result.case_id == sample_case.case_id
        assert result.role == sample_case.role
        assert "context_evaluation" in result.output
        assert "score=" in result.output

    def test_evaluate_with_full_recall(
        self,
        sample_case: UnifiedBenchmarkCase,
        temp_workspace: str,
    ) -> None:
        """100% recall returns score close to 1.0."""
        adapter = ContextBenchmarkAdapter(
            compiler=_FakeCompiler(selected_files=list(sample_case.expected_evidence_path)),
            metrics_calculator=_FakeMetricsCalculator(),
        )

        adapter.evaluate(sample_case, temp_workspace)
        eval_data = adapter.get_evaluation(sample_case.case_id)

        assert eval_data is not None
        assert eval_data["recall_at_10"] == 1.0
        assert eval_data["mrr"] == 1.0
        assert eval_data["score"] >= 0.95

    def test_evaluate_with_partial_recall(
        self,
        sample_case: UnifiedBenchmarkCase,
        temp_workspace: str,
    ) -> None:
        """Partial recall returns intermediate score."""
        adapter = ContextBenchmarkAdapter(
            compiler=_FakeCompiler(selected_files=["polaris/kernelone/context/strategy_benchmark.py"]),
            metrics_calculator=_FakeMetricsCalculator(),
        )

        adapter.evaluate(sample_case, temp_workspace)
        eval_data = adapter.get_evaluation(sample_case.case_id)

        assert eval_data is not None
        assert 0.0 < eval_data["score"] < 1.0
        assert 0.0 < eval_data["recall_at_10"] < 1.0

    def test_evaluate_with_empty_prediction(
        self,
        sample_case: UnifiedBenchmarkCase,
        temp_workspace: str,
    ) -> None:
        """Empty prediction returns zero score."""
        adapter = ContextBenchmarkAdapter(
            compiler=_FakeCompiler(selected_files=[]),
            metrics_calculator=_FakeMetricsCalculator(),
        )

        adapter.evaluate(sample_case, temp_workspace)
        eval_data = adapter.get_evaluation(sample_case.case_id)

        assert eval_data is not None
        assert eval_data["score"] == 0.0
        assert eval_data["recall_at_5"] == 0.0
        assert eval_data["recall_at_10"] == 0.0
        assert eval_data["mrr"] == 0.0

    def test_evaluate_compilation_error_propagates(
        self,
        sample_case: UnifiedBenchmarkCase,
        temp_workspace: str,
    ) -> None:
        """ContextCompilationError propagates on failure."""
        adapter = ContextBenchmarkAdapter(
            compiler=_FailingCompiler(),
            metrics_calculator=_FakeMetricsCalculator(),
        )

        with pytest.raises(ContextCompilationError):
            adapter.evaluate(sample_case, temp_workspace)

    def test_clear_evaluations(
        self,
        sample_case: UnifiedBenchmarkCase,
        temp_workspace: str,
    ) -> None:
        """clear_evaluations() removes all cached results."""
        adapter = ContextBenchmarkAdapter(
            compiler=_FakeCompiler(selected_files=[]),
            metrics_calculator=_FakeMetricsCalculator(),
        )
        adapter.evaluate(sample_case, temp_workspace)

        assert adapter.get_evaluation(sample_case.case_id) is not None

        adapter.clear_evaluations()

        assert adapter.get_evaluation(sample_case.case_id) is None

    def test_di_with_custom_compiler(
        self,
        sample_case: UnifiedBenchmarkCase,
        temp_workspace: str,
    ) -> None:
        """DI accepts custom compiler implementation."""
        custom_compiler = _CustomFileListCompiler(files=["custom/file.py"])
        adapter = ContextBenchmarkAdapter(compiler=custom_compiler)

        result = adapter.evaluate(sample_case, temp_workspace)

        assert result.case_id == sample_case.case_id
        eval_data = adapter.get_evaluation(sample_case.case_id)
        assert eval_data is not None
        assert "custom/file.py" in eval_data["predicted_files"]

    def test_di_with_custom_metrics_calculator(
        self,
        sample_case: UnifiedBenchmarkCase,
        temp_workspace: str,
    ) -> None:
        """DI accepts custom metrics calculator."""
        adapter = ContextBenchmarkAdapter(
            compiler=_FakeCompiler(selected_files=list(sample_case.expected_evidence_path)),
            metrics_calculator=_FixedScoreMetricsCalculator(fixed_score=0.88),
        )

        adapter.evaluate(sample_case, temp_workspace)
        eval_data = adapter.get_evaluation(sample_case.case_id)

        assert eval_data is not None
        assert eval_data["score"] == 0.88

    def test_evaluate_stores_evaluation_cache(
        self,
        sample_case: UnifiedBenchmarkCase,
        temp_workspace: str,
    ) -> None:
        """evaluate() stores result in internal cache."""
        adapter = ContextBenchmarkAdapter(
            compiler=_FakeCompiler(selected_files=[]),
            metrics_calculator=_FakeMetricsCalculator(),
        )

        adapter.evaluate(sample_case, temp_workspace)

        cached = adapter.get_evaluation(sample_case.case_id)
        assert cached is not None
        assert cached["case_id"] == sample_case.case_id

    def test_evaluate_multiple_cases_separate_cache(
        self,
        sample_case: UnifiedBenchmarkCase,
        temp_workspace: str,
    ) -> None:
        """Multiple evaluations maintain separate cache entries."""
        adapter = ContextBenchmarkAdapter(
            compiler=_FakeCompiler(selected_files=[]),
            metrics_calculator=_FakeMetricsCalculator(),
        )

        case2 = UnifiedBenchmarkCase(
            case_id="second_case",
            role="director",
            title="Second Case",
            prompt="Another task",
            judge=JudgeConfig(score_threshold=0.70, mode="context"),
        )

        adapter.evaluate(sample_case, temp_workspace)
        adapter.evaluate(case2, temp_workspace)

        assert adapter.get_evaluation(sample_case.case_id) is not None
        assert adapter.get_evaluation(case2.case_id) is not None
        assert len(adapter._evaluations) == 2

    def test_stub_compiler_on_import_failure(
        self,
        sample_case: UnifiedBenchmarkCase,
        temp_workspace: str,
    ) -> None:
        """Stub compiler used when context pipeline unavailable."""
        adapter = ContextBenchmarkAdapter(
            compiler=None,  # Will use stub
            metrics_calculator=_FakeMetricsCalculator(),
        )

        # Should not raise, returns empty
        result = adapter.evaluate(sample_case, temp_workspace)
        assert result.case_id == sample_case.case_id


class TestContextCompilationResult:
    """ContextCompilationResult dataclass tests."""

    def test_result_with_files_only(self) -> None:
        """Result with selected files only."""
        result = ContextCompilationResult(selected_files=("a.py", "b.py"))
        assert result.selected_files == ("a.py", "b.py")
        assert result.confidence_scores == ()

    def test_result_with_scores(self) -> None:
        """Result with confidence scores."""
        result = ContextCompilationResult(
            selected_files=("a.py", "b.py"),
            confidence_scores=(0.95, 0.88),
        )
        assert result.selected_files == ("a.py", "b.py")
        assert result.confidence_scores == (0.95, 0.88)

    def test_result_immutable(self) -> None:
        """Result is immutable (frozen dataclass)."""
        result = ContextCompilationResult(selected_files=("a.py",))

        with pytest.raises(AttributeError):
            result.selected_files = ("b.py",)


class TestContextCompilationError:
    """ContextCompilationError tests."""

    def test_error_message(self) -> None:
        """Error preserves message."""
        error = ContextCompilationError("compilation failed")
        assert str(error) == "compilation failed"

    def test_error_is_exception(self) -> None:
        """Error is a proper Exception subclass."""
        error = ContextCompilationError("test")
        assert isinstance(error, Exception)


# ------------------------------------------------------------------
# Test Fakes (Dependency Injection Stubs)
# ------------------------------------------------------------------


class _FakeCompiler(ContextCompilerPort):
    """Fake compiler for tests."""

    __slots__ = ("_files",)

    def __init__(self, selected_files: list[str]) -> None:
        self._files = selected_files

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        return ContextCompilationResult(selected_files=tuple(self._files))


class _FailingCompiler(ContextCompilerPort):
    """Compiler that always fails for error handling tests."""

    __slots__ = ()

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        msg = "Simulated compilation failure"
        raise ContextCompilationError(msg)


class _CustomFileListCompiler(ContextCompilerPort):
    """Custom compiler that returns predefined file list."""

    __slots__ = ("_files",)

    def __init__(self, files: list[str]) -> None:
        self._files = files

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        return ContextCompilationResult(selected_files=tuple(self._files))


class _FakeMetricsCalculator(MetricsCalculatorPort):
    """Fake metrics calculator for tests.

    Returns deterministic metrics based on overlap.
    """

    __slots__ = ()

    def calculate(
        self,
        expected: list[str],
        predicted: list[str],
        case_id: str,
    ) -> dict[str, Any]:
        """Calculate metrics with perfect recall calculation."""
        expected_set = set(expected)
        predicted_set = set(predicted)

        if not expected_set:
            return {
                "case_id": case_id,
                "expected_files": expected,
                "predicted_files": predicted,
                "score": 0.0,
                "recall_at_5": 0.0,
                "recall_at_10": 0.0,
                "mrr": 0.0,
            }

        overlap = len(expected_set & predicted_set)
        recall = overlap / len(expected_set)

        # MRR = 1/rank of first match, or 0 if no match
        mrr = 0.0
        for idx, item in enumerate(predicted, start=1):
            if item in expected_set:
                mrr = 1.0 / idx
                break

        # Weighted score: recall@10 (0.7) + MRR (0.3)
        score = round(recall * 0.7 + mrr * 0.3, 6)

        return {
            "case_id": case_id,
            "expected_files": expected,
            "predicted_files": predicted,
            "score": score,
            "recall_at_5": recall,
            "recall_at_10": recall,
            "mrr": mrr,
        }


class _FixedScoreMetricsCalculator(MetricsCalculatorPort):
    """Metrics calculator that returns a fixed score for testing."""

    __slots__ = ("_score",)

    def __init__(self, fixed_score: float) -> None:
        self._score = fixed_score

    def calculate(
        self,
        expected: list[str],
        predicted: list[str],
        case_id: str,
    ) -> dict[str, Any]:
        """Return fixed score."""
        return {
            "case_id": case_id,
            "expected_files": expected,
            "predicted_files": predicted,
            "score": self._score,
            "recall_at_5": self._score,
            "recall_at_10": self._score,
            "mrr": self._score,
        }
