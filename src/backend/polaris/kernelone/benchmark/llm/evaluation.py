"""LLM Quality Evaluation Framework.

This module provides comprehensive LLM quality assessment capabilities including:
- Quality dimension metrics (accuracy, hallucination, coherence, etc.)
- LLM-as-a-Judge protocol
- Heuristic evaluation for deterministic assessment

Example
-------
    from polaris.kernelone.benchmark.llm.evaluation import (
        QualityDimension,
        LLMQualityMetrics,
        HeuristicJudge,
        LLMAsJudge,
    )

    judge = HeuristicJudge(
        required_patterns=["answer", "result"],
        forbidden_patterns=["undefined", "null"],
    )
    metrics = judge.evaluate("The answer is 42.")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence


# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------


class QualityDimension(Enum):
    """Quality dimensions for LLM output evaluation."""

    ACCURACY = "accuracy"
    HALLUCINATION_RATE = "hallucination_rate"
    FORMAT_COMPLIANCE = "format_compliance"
    RELEVANCE = "relevance"
    COHERENCE = "coherence"
    COMPLETENESS = "completeness"
    TOXICITY = "toxicity"


# ------------------------------------------------------------------
# Data Models
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class LLMQualityMetrics:
    """LLM quality metrics container.

    Attributes:
        accuracy_score: Score for factual correctness (0.0-1.0).
        hallucination_rate: Rate of hallucinated content (0.0-1.0, lower is better).
        format_compliance: Score for output format adherence (0.0-1.0).
        token_consumed: Total tokens consumed for this evaluation.
        latency_ms: Latency in milliseconds for the LLM call.
        quality_dimensions: Per-dimension scores map.
    """

    accuracy_score: float = 0.0
    hallucination_rate: float = 0.0
    format_compliance: float = 0.0
    relevance_score: float = 0.0
    coherence_score: float = 0.0
    token_consumed: int = 0
    latency_ms: float = 0.0
    quality_dimensions: dict[QualityDimension, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Clamp all scores to [0.0, 1.0]
        if self.accuracy_score < 0.0:
            object.__setattr__(self, "accuracy_score", 0.0)
        elif self.accuracy_score > 1.0:
            object.__setattr__(self, "accuracy_score", 1.0)

        if self.hallucination_rate < 0.0:
            object.__setattr__(self, "hallucination_rate", 0.0)
        elif self.hallucination_rate > 1.0:
            object.__setattr__(self, "hallucination_rate", 1.0)

        if self.format_compliance < 0.0:
            object.__setattr__(self, "format_compliance", 0.0)
        elif self.format_compliance > 1.0:
            object.__setattr__(self, "format_compliance", 1.0)

        if self.relevance_score < 0.0:
            object.__setattr__(self, "relevance_score", 0.0)
        elif self.relevance_score > 1.0:
            object.__setattr__(self, "relevance_score", 1.0)

        if self.coherence_score < 0.0:
            object.__setattr__(self, "coherence_score", 0.0)
        elif self.coherence_score > 1.0:
            object.__setattr__(self, "coherence_score", 1.0)

        if self.token_consumed < 0:
            object.__setattr__(self, "token_consumed", 0)

        if self.latency_ms < 0.0:
            object.__setattr__(self, "latency_ms", 0.0)

    @property
    def overall_score(self) -> float:
        """Calculate overall quality score as weighted average."""
        return (
            self.accuracy_score * 0.35
            + (1.0 - self.hallucination_rate) * 0.25
            + self.format_compliance * 0.15
            + self.relevance_score * 0.15
            + self.coherence_score * 0.10
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "accuracy_score": round(self.accuracy_score, 4),
            "hallucination_rate": round(self.hallucination_rate, 4),
            "format_compliance": round(self.format_compliance, 4),
            "relevance_score": round(self.relevance_score, 4),
            "coherence_score": round(self.coherence_score, 4),
            "token_consumed": self.token_consumed,
            "latency_ms": round(self.latency_ms, 2),
            "overall_score": round(self.overall_score, 4),
            "quality_dimensions": {k.value: round(v, 4) for k, v in self.quality_dimensions.items()},
        }


# ------------------------------------------------------------------
# Judge Protocol
# ------------------------------------------------------------------


class LLMJudgePort(Protocol):
    """Protocol for LLM-as-a-Judge evaluation.

    Implementations should use another LLM to judge the quality
    of a candidate response against a reference.
    """

    async def judge(
        self,
        prompt: str,
        reference: str,
        candidate: str,
        context: str | None = None,
    ) -> LLMQualityMetrics:
        """Judge candidate response against reference.

        Args:
            prompt: The original user prompt.
            reference: The reference/ground truth answer.
            candidate: The LLM response to evaluate.
            context: Optional additional context for evaluation.

        Returns:
            LLMQualityMetrics with evaluation results.
        """
        ...

    def evaluate(
        self,
        response: str,
        reference: str | None = None,
        context: str | None = None,
    ) -> LLMQualityMetrics:
        """Evaluate response (synchronous wrapper for convenience).

        Args:
            response: The LLM response to evaluate.
            reference: Optional reference for comparison.
            context: Optional context for evaluation.

        Returns:
            LLMQualityMetrics with evaluation results.
        """
        ...


# ------------------------------------------------------------------
# Heuristic Judge
# ------------------------------------------------------------------


class HeuristicJudge:
    """Heuristic-based evaluator for deterministic quality assessment.

    This judge uses pattern matching and statistical analysis to
    evaluate LLM responses without requiring an external LLM judge.

    Attributes:
        required_patterns: Patterns that should appear in the response.
        forbidden_patterns: Patterns that should NOT appear in response.
        max_response_length: Maximum acceptable response length in chars.
        min_response_length: Minimum acceptable response length in chars.
    """

    def __init__(
        self,
        required_patterns: Sequence[str] | None = None,
        forbidden_patterns: Sequence[str] | None = None,
        max_response_length: int = 50_000,
        min_response_length: int = 1,
    ) -> None:
        self._required_patterns = list(required_patterns) if required_patterns else []
        self._forbidden_patterns = list(forbidden_patterns) if forbidden_patterns else []
        self._max_response_length = max_response_length
        self._min_response_length = min_response_length

    @property
    def required_patterns(self) -> list[str]:
        """Get required patterns for evaluation."""
        return self._required_patterns

    @property
    def forbidden_patterns(self) -> list[str]:
        """Get forbidden patterns for evaluation."""
        return self._forbidden_patterns

    def evaluate(
        self,
        response: str,
        reference: str | None = None,
        context: str | None = None,
    ) -> LLMQualityMetrics:
        """Evaluate response using heuristic rules.

        Args:
            response: The LLM response to evaluate.
            reference: Optional reference for comparison.
            context: Optional context for relevance scoring.

        Returns:
            LLMQualityMetrics with evaluation results.
        """
        if not response:
            return LLMQualityMetrics(
                accuracy_score=0.0,
                hallucination_rate=1.0,
                format_compliance=0.0,
                quality_dimensions={},
            )

        # Calculate format compliance
        format_compliance = self._evaluate_format_compliance(response)

        # Calculate hallucination rate
        hallucination_rate = self._evaluate_hallucination(response)

        # Calculate relevance score (if reference provided)
        relevance_score = self._evaluate_relevance(response, reference, context)

        # Calculate coherence score
        coherence_score = self._evaluate_coherence(response)

        # Calculate accuracy score based on reference match
        accuracy_score = self._evaluate_accuracy(response, reference)

        # Build quality dimensions
        quality_dimensions: dict[QualityDimension, float] = {
            QualityDimension.FORMAT_COMPLIANCE: format_compliance,
            QualityDimension.HALLUCINATION_RATE: hallucination_rate,
            QualityDimension.RELEVANCE: relevance_score,
            QualityDimension.COHERENCE: coherence_score,
            QualityDimension.ACCURACY: accuracy_score,
        }

        return LLMQualityMetrics(
            accuracy_score=accuracy_score,
            hallucination_rate=hallucination_rate,
            format_compliance=format_compliance,
            relevance_score=relevance_score,
            coherence_score=coherence_score,
            quality_dimensions=quality_dimensions,
        )

    def _evaluate_format_compliance(self, response: str) -> float:
        """Evaluate format compliance based on required patterns."""
        if not self._required_patterns:
            return 1.0

        matches = sum(1 for pattern in self._required_patterns if pattern.lower() in response.lower())
        return matches / len(self._required_patterns)

    def _evaluate_hallucination(self, response: str) -> float:
        """Evaluate hallucination rate based on forbidden patterns."""
        if not self._forbidden_patterns:
            return 0.0

        matches = sum(1 for pattern in self._forbidden_patterns if pattern.lower() in response.lower())
        return matches / len(self._forbidden_patterns)

    def _evaluate_relevance(
        self,
        response: str,
        reference: str | None,
        context: str | None,
    ) -> float:
        """Evaluate relevance to prompt and context."""
        if not reference and not context:
            return 0.5  # Neutral score when no reference

        response_lower = response.lower()
        reference_lower = (reference or "").lower()
        context_lower = (context or "").lower()

        # Calculate word overlap with reference
        ref_words = set(reference_lower.split()) if reference else set()
        ctx_words = set(context_lower.split()) if context else set()
        response_words = set(response_lower.split())

        # Jaccard similarity
        overlap_count = len(response_words & ref_words) + len(response_words & ctx_words)
        total_count = len(response_words | ref_words | ctx_words)

        if total_count == 0:
            return 0.5

        return overlap_count / total_count

    def _evaluate_coherence(self, response: str) -> float:
        """Evaluate text coherence using structural analysis."""
        if not response or len(response) < 10:
            return 0.0

        score = 0.5  # Base score

        # Check for sentence structure (periods, question marks)
        sentences = re.split(r"[.!?]+", response)
        if len(sentences) > 1:
            score += 0.1

        # Check for paragraph structure
        paragraphs = response.split("\n\n")
        if len(paragraphs) > 1:
            score += 0.1

        # Penalize very long words (potential gibberish)
        words = response.split()
        long_words = sum(1 for w in words if len(w) > 20)
        if len(words) > 0:
            long_word_ratio = long_words / len(words)
            if long_word_ratio > 0.3:
                score -= 0.2

        # Penalize excessive repetition
        unique_ratio = len(set(words)) / len(words) if words else 0
        if unique_ratio < 0.3:
            score -= 0.2

        return max(0.0, min(1.0, score))

    def _evaluate_accuracy(self, response: str, reference: str | None) -> float:
        """Evaluate accuracy against reference answer."""
        if not reference:
            return 0.5  # Neutral when no reference

        response_lower = response.lower().strip()
        reference_lower = reference.lower().strip()

        # Exact match
        if response_lower == reference_lower:
            return 1.0

        # Check if response contains key parts of reference
        ref_words = set(reference_lower.split())
        resp_words = set(response_lower.split())
        overlap = len(ref_words & resp_words) / len(ref_words) if ref_words else 0

        return overlap

    def add_required_pattern(self, pattern: str) -> None:
        """Add a required pattern for evaluation."""
        if pattern and pattern not in self._required_patterns:
            self._required_patterns.append(pattern)

    def add_forbidden_pattern(self, pattern: str) -> None:
        """Add a forbidden pattern for evaluation."""
        if pattern and pattern not in self._forbidden_patterns:
            self._forbidden_patterns.append(pattern)


# ------------------------------------------------------------------
# LLM-as-a-Judge Implementation
# ------------------------------------------------------------------


class LLMAsJudge:
    """LLM-as-a-Judge implementation using an external LLM.

    This class uses an LLM to evaluate the quality of responses
    against a reference answer.

    Attributes:
        judge_model: The LLM model to use for judging.
        scoring_prompt_template: Template for the judgment prompt.
    """

    DEFAULT_PROMPT_TEMPLATE = """You are an expert evaluator. Evaluate the candidate response
based on the following criteria:

Original Prompt: {prompt}

Reference Answer: {reference}

Candidate Response: {candidate}

Evaluate the following dimensions (score 0.0 to 1.0):
1. Accuracy: How factually correct is the response compared to the reference?
2. Hallucination Rate: How much incorrect or fabricated information does it contain?
3. Format Compliance: Does it follow the expected format?
4. Relevance: How relevant is the response to the original prompt?
5. Coherence: How coherent and well-structured is the response?

Provide your evaluation as a JSON object:
{{
    "accuracy_score": <float>,
    "hallucination_rate": <float>,
    "format_compliance": <float>,
    "relevance_score": <float>,
    "coherence_score": <float>
}}
"""

    def __init__(
        self,
        judge_llm: Any,  # LLM client compatible interface
        prompt_template: str | None = None,
    ) -> None:
        self._judge_llm = judge_llm
        self._prompt_template = prompt_template or self.DEFAULT_PROMPT_TEMPLATE

    async def judge(
        self,
        prompt: str,
        reference: str,
        candidate: str,
        context: str | None = None,
    ) -> LLMQualityMetrics:
        """Judge candidate response using LLM.

        Args:
            prompt: The original user prompt.
            reference: The reference/ground truth answer.
            candidate: The LLM response to evaluate.
            context: Optional additional context.

        Returns:
            LLMQualityMetrics with evaluation results.
        """
        import json
        import time

        start_time = time.time()

        evaluation_prompt = self._prompt_template.format(
            prompt=prompt,
            reference=reference,
            candidate=candidate,
            context=context or "",
        )

        # Call the judge LLM
        response = await self._judge_llm.generate(evaluation_prompt)

        # Parse the JSON response
        try:
            # Try to extract JSON from the response
            json_match = re.search(r"\{[\s\S]*\}", response)
            scores = json.loads(json_match.group()) if json_match else json.loads(response)
        except json.JSONDecodeError:
            # Fallback to heuristic if JSON parsing fails
            return LLMQualityMetrics(
                accuracy_score=0.5,
                hallucination_rate=0.5,
                format_compliance=0.5,
            )

        latency_ms = (time.time() - start_time) * 1000

        return LLMQualityMetrics(
            accuracy_score=float(scores.get("accuracy_score", 0.5)),
            hallucination_rate=float(scores.get("hallucination_rate", 0.5)),
            format_compliance=float(scores.get("format_compliance", 0.5)),
            relevance_score=float(scores.get("relevance_score", 0.5)),
            coherence_score=float(scores.get("coherence_score", 0.5)),
            latency_ms=latency_ms,
        )

    def evaluate(
        self,
        response: str,
        reference: str | None = None,
        context: str | None = None,
    ) -> LLMQualityMetrics:
        """Synchronous evaluate method (returns neutral metrics).

        Note: This is a placeholder for synchronous evaluation.
        For actual LLM-based evaluation, use judge() instead.

        Args:
            response: The LLM response to evaluate.
            reference: Optional reference for comparison.
            context: Optional context for evaluation.

        Returns:
            LLMQualityMetrics with neutral scores.
        """
        # Return neutral metrics for sync evaluation
        return LLMQualityMetrics(
            accuracy_score=0.5,
            hallucination_rate=0.5,
            format_compliance=0.5,
        )


# ------------------------------------------------------------------
# Batch Evaluation
# ------------------------------------------------------------------


@dataclass
class BatchEvaluationResult:
    """Results from batch evaluation of multiple responses."""

    total_cases: int
    passed_cases: int
    failed_cases: int
    average_score: float
    metrics: list[LLMQualityMetrics]
    failures: list[str]

    @property
    def pass_rate(self) -> float:
        """Calculate pass rate."""
        if self.total_cases == 0:
            return 0.0
        return self.passed_cases / self.total_cases


class BatchEvaluator:
    """Batch evaluator for multiple LLM responses."""

    def __init__(
        self,
        judge: HeuristicJudge | LLMJudgePort,
        pass_threshold: float = 0.7,
    ) -> None:
        self._judge = judge
        self._pass_threshold = pass_threshold

    def evaluate_batch(
        self,
        cases: list[tuple[str, str, str]],  # (prompt, reference, candidate)
    ) -> BatchEvaluationResult:
        """Evaluate a batch of responses.

        Args:
            cases: List of (prompt, reference, candidate) tuples.

        Returns:
            BatchEvaluationResult with aggregated results.
        """
        metrics: list[LLMQualityMetrics] = []
        failures: list[str] = []
        passed = 0

        for i, (prompt, reference, candidate) in enumerate(cases):
            result = self._judge.evaluate(
                response=candidate,
                reference=reference,
                context=prompt,
            )
            metrics.append(result)

            if result.overall_score >= self._pass_threshold:
                passed += 1
            else:
                failures.append(f"Case {i}: score={result.overall_score:.3f}")

        avg_score = sum(m.overall_score for m in metrics) / len(metrics) if metrics else 0.0

        return BatchEvaluationResult(
            total_cases=len(cases),
            passed_cases=passed,
            failed_cases=len(cases) - passed,
            average_score=avg_score,
            metrics=metrics,
            failures=failures,
        )
