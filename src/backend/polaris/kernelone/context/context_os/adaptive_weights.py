"""Adaptive Weight Learning - 动态权重学习

ADR-0067: ContextOS 2.0 摘要策略选型

基于反馈的动态权重调整，优化压缩策略选择。

特点:
- Thompson Sampling: 基于贝叶斯更新的策略选择
- Epsilon-Greedy: 探索与利用的平衡
- 指数加权平均: 基于历史性能的权重更新
"""

from __future__ import annotations

import logging
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class StrategyPerformance:
    """策略性能统计"""

    success_count: int = 0
    failure_count: int = 0
    total_quality_score: float = 0.0
    avg_compression_ratio: float = 0.0
    sample_count: int = 0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / max(total, 1)

    @property
    def avg_quality(self) -> float:
        return self.total_quality_score / max(self.sample_count, 1)


@dataclass
class WeightLearningConfig:
    """权重学习配置"""

    algorithm: str = "thompson_sampling"  # thompson_sampling | epsilon_greedy | exponential
    epsilon: float = 0.1  # epsilon-greedy 的探索概率
    learning_rate: float = 0.1  # 指数加权的学习率
    min_samples: int = 3  # 最少样本数才开始选择
    exploration_bonus: float = 0.1  # 探索奖励


class AdaptiveWeightLearner:
    """自适应权重学习器

    根据历史表现动态调整策略权重。

    Example:
        ```python
        learner = AdaptiveWeightLearner()

        # 记录结果
        learner.record(
            strategy="tree-sitter",
            content_type="code",
            quality_score=85.0,
            compression_ratio=0.3,
            success=True,
        )

        # 获取最佳策略
        best = learner.select_best_strategy(
            content_type="code",
            available_strategies=["tree-sitter", "sumy", "truncation"],
        )
        ```
    """

    def __init__(self, config: WeightLearningConfig | None = None) -> None:
        """初始化权重学习器

        Args:
            config: 学习配置
        """
        self.config = config or WeightLearningConfig()
        self._strategy_stats: dict[str, dict[str, StrategyPerformance]] = defaultdict(
            lambda: defaultdict(StrategyPerformance)
        )
        self._beta_params: dict[str, dict[str, tuple[float, float]]] = defaultdict(
            lambda: defaultdict(lambda: (1.0, 1.0))
        )

    def record(
        self,
        strategy: str,
        content_type: str,
        quality_score: float,
        compression_ratio: float,
        success: bool,
    ) -> None:
        """记录策略表现

        Args:
            strategy: 策略名称
            content_type: 内容类型
            quality_score: 质量评分 (0-100)
            compression_ratio: 压缩比 (0-1)
            success: 是否成功
        """
        stats = self._strategy_stats[content_type][strategy]

        if success:
            stats.success_count += 1
        else:
            stats.failure_count += 1

        stats.total_quality_score += quality_score
        stats.sample_count += 1

        # 更新 Beta 分布参数 (用于 Thompson Sampling)
        alpha, beta = self._beta_params[content_type][strategy]
        if success:
            alpha += quality_score / 100.0  # 成功增加 alpha
        else:
            beta += 1.0  # 失败增加 beta
        self._beta_params[content_type][strategy] = (alpha, beta)

        logger.debug(
            f"Weight learning: {strategy} for {content_type} - "
            f"success_rate={stats.success_rate:.2f}, avg_quality={stats.avg_quality:.1f}"
        )

    def select_best_strategy(
        self,
        content_type: str,
        available_strategies: list[str],
    ) -> str:
        """选择最佳策略

        根据配置的学习算法选择最佳策略。

        Args:
            content_type: 内容类型
            available_strategies: 可用的策略列表

        Returns:
            最佳策略名称
        """
        if not available_strategies:
            return ""

        if len(available_strategies) == 1:
            return available_strategies[0]

        # 检查是否需要探索
        for strategy in available_strategies:
            stats = self._strategy_stats[content_type][strategy]
            if stats.sample_count < self.config.min_samples:
                # 样本不足，随机选择以探索
                return random.choice(available_strategies)

        if self.config.algorithm == "thompson_sampling":
            return self._thompson_sampling(content_type, available_strategies)
        elif self.config.algorithm == "epsilon_greedy":
            return self._epsilon_greedy(content_type, available_strategies)
        else:  # exponential
            return self._exponential_weighted(content_type, available_strategies)

    def _thompson_sampling(self, content_type: str, strategies: list[str]) -> str:
        """Thompson Sampling 策略选择

        从 Beta 分布采样，选择最高采样的策略。
        """
        best_strategy = strategies[0]
        best_sample = -1.0

        for strategy in strategies:
            alpha, beta = self._beta_params[content_type][strategy]
            # 从 Beta 分布采样
            sample = self._beta_sample(alpha, beta)
            if sample > best_sample:
                best_sample = sample
                best_strategy = strategy

        return best_strategy

    def _beta_sample(self, alpha: float, beta: float) -> float:
        """从 Beta 分布采样 (使用近似方法)"""
        # 使用 Gamma 分布生成 Beta 分布样本
        gamma_a = self._gamma_sample(alpha)
        gamma_b = self._gamma_sample(beta)
        return gamma_a / (gamma_a + gamma_b)

    def _gamma_sample(self, shape: float) -> float:
        """从 Gamma 分布采样 (使用 Marsaglia 和 Tsang 的方法)"""
        if shape < 1:
            return self._gamma_sample(shape + 1) * random.random() ** (1 / shape)

        d = shape - 1 / 3
        c = 1 / math.sqrt(9 * d)
        while True:
            x = random.gauss(0, 1)
            v = 1 + c * x
            if v > 0:
                v = d * v**3
                u = random.random()
                if u < 1 - 0.0331 * (x**2) * (x**2):
                    return v
                if math.log(u) < 0.5 * x**2 + d * (1 - v + math.log(v)):
                    return v

    def _epsilon_greedy(self, content_type: str, strategies: list[str]) -> str:
        """Epsilon-Greedy 策略选择

        以 epsilon 概率随机探索，否则选择最佳策略。
        """
        if random.random() < self.config.epsilon:
            return random.choice(strategies)

        # 选择平均质量最高的策略
        best_strategy = strategies[0]
        best_quality = -1.0

        for strategy in strategies:
            stats = self._strategy_stats[content_type][strategy]
            if stats.avg_quality > best_quality:
                best_quality = stats.avg_quality
                best_strategy = strategy

        return best_strategy

    def _exponential_weighted(self, content_type: str, strategies: list[str]) -> str:
        """指数加权平均策略选择

        根据历史表现使用指数加权计算权重。
        """
        weights: dict[str, float] = {}
        total_weight = 0.0

        for strategy in strategies:
            stats = self._strategy_stats[content_type][strategy]
            # 指数加权: w = exp(learning_rate * quality)
            quality = stats.avg_quality if stats.sample_count > 0 else 50.0
            weight = math.exp(self.config.learning_rate * (quality / 100 - 0.5))
            weights[strategy] = weight
            total_weight += weight

        # 根据权重随机选择
        r = random.random() * total_weight
        cumulative = 0.0
        for strategy in strategies:
            cumulative += weights[strategy]
            if r <= cumulative:
                return strategy

        return strategies[0]

    def get_strategy_weights(self, content_type: str) -> dict[str, float]:
        """获取策略权重

        Args:
            content_type: 内容类型

        Returns:
            策略权重字典
        """
        if content_type not in self._strategy_stats:
            return {}

        weights = {}
        total_success_rate = 0.0

        # 计算总成功率
        for stats in self._strategy_stats[content_type].values():
            total_success_rate += stats.success_rate

        if total_success_rate == 0:
            # 平均分配
            n = len(self._strategy_stats[content_type])
            for strategy in self._strategy_stats[content_type]:
                weights[strategy] = 1.0 / max(n, 1)
            return weights

        # 归一化权重
        for strategy, stats in self._strategy_stats[content_type].items():
            weights[strategy] = stats.success_rate / total_success_rate

        return weights

    def get_best_strategy_for_type(self, content_type: str) -> tuple[str | None, float]:
        """获取指定内容类型的最佳策略

        Args:
            content_type: 内容类型

        Returns:
            (最佳策略名称, 平均质量分数)
        """
        if content_type not in self._strategy_stats:
            return None, 0.0

        best_strategy = None
        best_quality = -1.0

        for strategy, stats in self._strategy_stats[content_type].items():
            if stats.sample_count >= self.config.min_samples and stats.avg_quality > best_quality:
                best_quality = stats.avg_quality
                best_strategy = strategy

        return best_strategy, best_quality

    def reset(self) -> None:
        """重置学习器状态"""
        self._strategy_stats.clear()
        self._beta_params.clear()


# 全局学习器实例
_learner: AdaptiveWeightLearner | None = None


def get_weight_learner() -> AdaptiveWeightLearner:
    """获取全局权重学习器实例"""
    global _learner
    if _learner is None:
        _learner = AdaptiveWeightLearner()
    return _learner
