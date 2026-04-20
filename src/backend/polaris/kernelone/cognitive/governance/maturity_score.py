"""Cognitive Maturity Score - Overall maturity assessment combining all metrics."""

from __future__ import annotations

from dataclasses import dataclass

from polaris.kernelone.cognitive.governance.evolution_metrics import EvolutionMetrics
from polaris.kernelone.cognitive.governance.truthfulness import TruthfulnessMetrics
from polaris.kernelone.cognitive.governance.understanding import UnderstandingMetrics


@dataclass(frozen=True)
class CognitiveMaturityScore:
    """Overall cognitive maturity score combining all metrics.

    Maturity Levels (from roadmap):
    | Level | Score | Description |
    |-------|-------|-------------|
    | Tool | 0-20 | 仅执行指令，无理解 |
    | Aware | 21-40 | 意图理解，浅层推理 |
    | Reflective | 41-60 | 反思能力，元认知 |
    | Adaptive | 61-80 | 动态调整，持续演化 |
    | Evolutionary | 81-100 | 主动进化，价值观驱动 |

    Weights:
    - Truthfulness: 0.35 (Law L1)
    - Understanding: 0.35 (Law L2)
    - Evolution: 0.30 (Law L3)
    """

    # Component scores (0-100)
    truthfulness_score: float = 0.0
    understanding_score: float = 0.0
    evolution_score: float = 0.0

    # Component weights
    TRUTHFULNESS_WEIGHT: float = 0.35
    UNDERSTANDING_WEIGHT: float = 0.35
    EVOLUTION_WEIGHT: float = 0.30

    @property
    def overall_score(self) -> float:
        """Calculate weighted overall maturity score.

        Returns:
            Score from 0-100
        """
        return (
            self.truthfulness_score * self.TRUTHFULNESS_WEIGHT
            + self.understanding_score * self.UNDERSTANDING_WEIGHT
            + self.evolution_score * self.EVOLUTION_WEIGHT
        )

    @property
    def maturity_level(self) -> str:
        """Get maturity level label.

        Returns:
            One of: Tool, Aware, Reflective, Adaptive, Evolutionary

        Maturity levels per roadmap:
        - Tool: 0-20
        - Aware: 21-40
        - Reflective: 41-60
        - Adaptive: 61-80
        - Evolutionary: 81-100
        """
        score = self.overall_score

        if score >= 81:
            return "Evolutionary"
        elif score >= 61:
            return "Adaptive"
        elif score >= 41:
            return "Reflective"
        elif score >= 21:
            return "Aware"
        return "Tool"

    @property
    def maturity_description(self) -> str:
        """Get detailed maturity level description.

        Returns:
            Human-readable description of current maturity level
        """
        level = self.maturity_level

        descriptions = {
            "Tool": ("仅执行指令，无理解能力。按照既定指令执行任务，不理解深层意图。无自主决策、反思或学习能力。"),
            "Aware": (
                "具备意图理解和浅层推理能力。能够识别用户意图，进行基本的问题分析。开始形成元认知意识，但仍需显著提升。"
            ),
            "Reflective": (
                "具备完整反思能力和元认知监控。能够批判性思考，识别自身认知偏差。开始建立价值对齐和谨慎执行机制。"
            ),
            "Adaptive": (
                "具备动态调整和持续演化能力。"
                "能够从错误中学习，识别并修正重复性偏差。"
                "主动更新信念系统，持续优化决策质量。"
            ),
            "Evolutionary": (
                "达到主动进化和价值观驱动的高级阶段。"
                "认知行为受内在价值观引导，非被动响应。"
                "持续追求真理（Truthfulness > Consistency），"
                "理解先于执行（Understanding > Execution），"
                "进化优先于正确性（Evolution > Correctness）。"
            ),
        }

        return descriptions.get(level, "Unknown")

    @property
    def is_calibrated(self) -> bool:
        """Whether the score has been calibrated through actual measurement."""
        return self.overall_score > 0

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "truthfulness_score": self.truthfulness_score,
            "understanding_score": self.understanding_score,
            "evolution_score": self.evolution_score,
            "overall_score": self.overall_score,
            "maturity_level": self.maturity_level,
            "maturity_description": self.maturity_description,
            "is_calibrated": self.is_calibrated,
        }

    @staticmethod
    def from_metrics(
        truthfulness: TruthfulnessMetrics,
        understanding: UnderstandingMetrics,
        evolution: EvolutionMetrics,
    ) -> CognitiveMaturityScore:
        """Create maturity score from component metrics.

        Args:
            truthfulness: Truthfulness metrics
            understanding: Understanding metrics
            evolution: Evolution metrics

        Returns:
            CognitiveMaturityScore with calculated scores
        """
        # Convert truthfulness admission rate to 0-100 scale
        truthfulness_score = truthfulness.truthfulness_admission_rate * 100

        # Convert understanding score to 0-100 scale
        understanding_score = understanding.calculate_understanding_score() * 100

        # Convert evolution effectiveness to 0-100 scale
        evolution_score = evolution.calculate_learning_effectiveness() * 100

        return CognitiveMaturityScore(
            truthfulness_score=truthfulness_score,
            understanding_score=understanding_score,
            evolution_score=evolution_score,
        )

    @staticmethod
    def default() -> CognitiveMaturityScore:
        """Create uncalibrated default score -- all zeros.

        Returns:
            CognitiveMaturityScore with all scores at 0 (uncalibrated).
        """
        return CognitiveMaturityScore(
            truthfulness_score=0.0,
            understanding_score=0.0,
            evolution_score=0.0,
        )

    def with_trend(self, previous_score: CognitiveMaturityScore) -> dict:
        """Calculate trend compared to previous score.

        Args:
            previous_score: Previous maturity score

        Returns:
            Dictionary with trend information
        """
        overall_change = self.overall_score - previous_score.overall_score

        return {
            "current_score": self.overall_score,
            "previous_score": previous_score.overall_score,
            "change": overall_change,
            "trend": "improving" if overall_change > 2 else ("degrading" if overall_change < -2 else "stable"),
            "truthfulness_change": self.truthfulness_score - previous_score.truthfulness_score,
            "understanding_change": self.understanding_score - previous_score.understanding_score,
            "evolution_change": self.evolution_score - previous_score.evolution_score,
        }
