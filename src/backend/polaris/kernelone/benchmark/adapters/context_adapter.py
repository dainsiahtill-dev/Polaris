"""Context Benchmark Adapter.

This adapter bridges the unified benchmark interface to the
context selection evaluation system.

Design Patterns:
    - Facade Pattern: 封装 context compilation pipeline
    - Strategy Pattern: ContextCompilerPort 可插拔
    - Dependency Injection: 可 mock，可降级

Architecture:
    Case ──→ Adapter ──→ Context Compiler (DI)
                    │
                    └──→ Metrics Calculator (DI)
                            │
                            └──→ infrastructure.accel.eval.metrics
                                    ├── recall_at_k()
                                    ├── reciprocal_rank()
                                    └── symbol_hit_rate()

Example:
    adapter = ContextBenchmarkAdapter()
    result = adapter.evaluate(case, workspace)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias, runtime_checkable

from polaris.kernelone.benchmark.unified_models import (
    ObservedBenchmarkRun,
    UnifiedBenchmarkCase,
)
from polaris.kernelone.errors import ContextCompilationError
from typing_extensions import Protocol

MetricsResult: TypeAlias = dict[str, Any]


# ------------------------------------------------------------------
# Protocols (Dependency Injection Interfaces)
# ------------------------------------------------------------------


@runtime_checkable
class ContextCompilerPort(Protocol):
    """Protocol for context compilers (Strategy Pattern).

    Allows pluggable context compilation strategies for testing
    and future extensibility.
    """

    __slots__ = ()

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        """Compile context for a task.

        Args:
            task: Task description.
            workspace: Workspace path.
            max_files: Maximum files to select.

        Returns:
            ContextCompilationResult with selected files.
        """
        ...


@dataclass(frozen=True, kw_only=True)
class ContextCompilationResult:
    """Result of context compilation."""

    selected_files: tuple[str, ...]
    confidence_scores: tuple[float, ...] = ()


@runtime_checkable
class MetricsCalculatorPort(Protocol):
    """Protocol for metrics calculators (Strategy Pattern)."""

    __slots__ = ()

    def calculate(
        self,
        expected: list[str],
        predicted: list[str],
        case_id: str,
    ) -> MetricsResult:
        """Calculate context selection metrics."""
        ...


# ------------------------------------------------------------------
# Default Implementations
# ------------------------------------------------------------------


class _DefaultMetricsCalculator:
    """Default metrics calculator using infrastructure.accel.eval.metrics.

    This class bridges the isolated metrics module to the unified
    benchmark framework via lazy import.
    """

    __slots__ = ("_metrics_module",)

    def __init__(self) -> None:
        self._metrics_module = self._load_metrics_module()

    def _load_metrics_module(self) -> Any | None:
        """Lazy load isolated metrics module.

        Returns:
            The metrics module if available, None otherwise.
        """
        try:
            from polaris.infrastructure.accel import eval as metrics_module

            return metrics_module
        except ImportError:
            return None

    def calculate(
        self,
        expected: list[str],
        predicted: list[str],
        case_id: str,
    ) -> MetricsResult:
        """Calculate metrics using infrastructure.accel.eval.metrics.

        Args:
            expected: Expected file paths from case.
            predicted: Predicted file paths from compiler.
            case_id: Benchmark case ID.

        Returns:
            Metrics dictionary with scores.
        """
        if self._metrics_module is None:
            return self._stub_metrics(expected, predicted, case_id)

        metrics = self._metrics_module
        r5 = metrics.recall_at_k(expected, predicted, k=5)
        r10 = metrics.recall_at_k(expected, predicted, k=10)
        mrr = metrics.reciprocal_rank(expected, predicted)

        # Weighted score: recall@10 (0.7) + MRR (0.3)
        score = round(r10 * 0.7 + mrr * 0.3, 6)

        return {
            "case_id": case_id,
            "expected_files": expected,
            "predicted_files": predicted,
            "score": score,
            "recall_at_5": r5,
            "recall_at_10": r10,
            "mrr": mrr,
        }

    def _stub_metrics(
        self,
        expected: list[str],
        predicted: list[str],
        case_id: str,
    ) -> MetricsResult:
        """Return stub metrics when module unavailable."""
        return {
            "case_id": case_id,
            "expected_files": expected,
            "predicted_files": predicted,
            "score": 0.0,
            "recall_at_5": 0.0,
            "recall_at_10": 0.0,
            "mrr": 0.0,
        }


class _DefaultContextCompiler:
    """Default context compiler using kernelone.context pipeline."""

    __slots__ = ()

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        """Compile using kernelone context pipeline.

        Args:
            task: Task description.
            workspace: Workspace path.
            max_files: Maximum files to select.

        Returns:
            ContextCompilationResult with selected files.
        """
        try:
            from polaris.kernelone.context.compilation.pipeline import (
                compile_context_for_task,
            )

            result = compile_context_for_task(
                task=task,
                workspace=workspace,
                max_files=max_files,
            )
            return ContextCompilationResult(
                selected_files=tuple(result.get("files", [])),
                confidence_scores=tuple(result.get("scores", [])),
            )
        except ImportError:
            return ContextCompilationResult(selected_files=())


class _StubContextCompiler:
    """Stub compiler when context pipeline unavailable."""

    __slots__ = ()

    def compile(
        self,
        task: str,
        workspace: str,
        max_files: int,
    ) -> ContextCompilationResult:
        """Return empty result."""
        return ContextCompilationResult(selected_files=())


# ------------------------------------------------------------------
# Adapter
# ------------------------------------------------------------------


class ContextBenchmarkAdapter:
    """Context Benchmark Adapter.

    Evaluates context selection quality by comparing predicted context
    against expected evidence paths defined in the case.

    Architecture:
        Case ──→ Adapter ──→ Context Compiler (DI)
                        │
                        └──→ Metrics Calculator (DI)
                                │
                                └──→ ObservedBenchmarkRun

    DI Usage:
        adapter = ContextBenchmarkAdapter(
            compiler=MyCustomCompiler(),
            metrics_calculator=MyCustomMetrics(),
        )
    """

    def __init__(
        self,
        compiler: ContextCompilerPort | None = None,
        metrics_calculator: MetricsCalculatorPort | None = None,
    ) -> None:
        """Initialize adapter with optional DI.

        Args:
            compiler: Context compiler. Uses default if None.
            metrics_calculator: Metrics calculator. Uses default if None.
        """
        self._compiler = compiler
        self._metrics = metrics_calculator or _DefaultMetricsCalculator()
        self._evaluations: dict[str, MetricsResult] = {}

    def evaluate(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
    ) -> ObservedBenchmarkRun:
        """Evaluate context selection for a case.

        Args:
            case: Benchmark case with expected_evidence_path.
            workspace: Workspace path.

        Returns:
            ObservedBenchmarkRun with evaluation metrics.

        Raises:
            ContextCompilationError: If context compilation fails.
        """
        compiler = self._get_compiler()
        expected = list(case.expected_evidence_path)

        # Compile predicted context
        predicted_context = self._compile_context(
            case=case,
            workspace=workspace,
            compiler=compiler,
        )

        # Calculate metrics using DI metrics calculator
        evaluation = self._metrics.calculate(
            expected=expected,
            predicted=predicted_context,
            case_id=case.case_id,
        )

        self._evaluations[case.case_id] = evaluation

        return ObservedBenchmarkRun(
            case_id=case.case_id,
            role=case.role,
            workspace=workspace,
            output=self._format_output(evaluation),
            thinking=f"context_selection_score: {evaluation.get('score', 0.0):.4f}",
            tool_calls=(),
            event_count=0,
        )

    def _get_compiler(self) -> ContextCompilerPort:
        """Get compiler via DI or default."""
        if self._compiler is not None:
            return self._compiler

        try:
            from polaris.kernelone.context.compilation import pipeline as context_pipeline

            has_compile_entry = hasattr(context_pipeline, "compile_context_for_task")
            if not has_compile_entry:
                return _StubContextCompiler()

            return _DefaultContextCompiler()
        except ImportError:
            return _StubContextCompiler()

    def _compile_context(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
        compiler: ContextCompilerPort,
    ) -> list[str]:
        """Compile context for task.

        Args:
            case: Benchmark case.
            workspace: Workspace path.
            compiler: Context compiler.

        Returns:
            List of predicted file paths.

        Raises:
            ContextCompilationError: If compilation fails.
        """
        try:
            result = compiler.compile(
                task=case.prompt,
                workspace=workspace,
                max_files=case.budget_conditions.max_tokens,
            )
            return list(result.selected_files)
        except (RuntimeError, ValueError) as exc:
            raise ContextCompilationError(f"Failed to compile context for {case.case_id}: {exc}") from exc

    def _format_output(self, evaluation: MetricsResult) -> str:
        """Format evaluation as output text."""
        return (
            f"context_evaluation:{evaluation['case_id']} | "
            f"score={evaluation['score']:.4f} | "
            f"r@10={evaluation['recall_at_10']:.4f} | "
            f"mrr={evaluation['mrr']:.4f}"
        )

    def get_evaluation(self, case_id: str) -> MetricsResult | None:
        """Get cached evaluation result."""
        return self._evaluations.get(case_id)

    def clear_evaluations(self) -> None:
        """Clear all cached evaluations."""
        self._evaluations.clear()
