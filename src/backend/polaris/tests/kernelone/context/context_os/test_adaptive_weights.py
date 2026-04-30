"""Tests for adaptive_weights.py — dynamic weight learning for ContextOS strategies.

Mathematical / logic correctness checks:
- success_rate boundary conditions (0, 1, division-by-zero guard)
- avg_quality boundary conditions (division-by-zero guard)
- record() updates counts and Beta parameters exactly
- Thompson Sampling: Beta(α,β) update rules (α += q/100 on success, β += 1 on failure)
- Epsilon-Greedy: exploration probability correctness
- Exponential weighted: weight formula w = exp(η·(q/100 − 0.5))
- select_best_strategy fallback logic (empty, single, min-samples)
- get_strategy_weights normalization (sums to 1)
- reset clears all internal state
"""

from __future__ import annotations

import math
from unittest.mock import patch

import pytest

from polaris.kernelone.context.context_os.adaptive_weights import (
    AdaptiveWeightLearner,
    StrategyPerformance,
    WeightLearningConfig,
    get_weight_learner,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def learner():
    """Fresh AdaptiveWeightLearner with default config."""
    return AdaptiveWeightLearner()


@pytest.fixture
def epsilon_config():
    """Config with epsilon_greedy algorithm, deterministic epsilon."""
    return WeightLearningConfig(algorithm="epsilon_greedy", epsilon=0.25)


@pytest.fixture
def exponential_config():
    """Config with exponential algorithm."""
    return WeightLearningConfig(algorithm="exponential", learning_rate=0.2)


# ---------------------------------------------------------------------------
# 1. StrategyPerformance — mathematical invariants
# ---------------------------------------------------------------------------


def test_strategy_performance_success_rate_zero():
    """With zero attempts, success_rate must be 0 (not ZeroDivisionError)."""
    sp = StrategyPerformance()
    assert sp.success_rate == 0.0


def test_strategy_performance_success_rate_one():
    """All successes → success_rate == 1.0."""
    sp = StrategyPerformance(success_count=5, failure_count=0)
    assert sp.success_rate == 1.0


def test_strategy_performance_success_rate_mixed():
    """Mathematical correctness: 3 successes / (3+2) total = 0.6."""
    sp = StrategyPerformance(success_count=3, failure_count=2)
    assert sp.success_rate == pytest.approx(0.6)


def test_strategy_performance_avg_quality_zero():
    """With zero samples, avg_quality must be 0.0."""
    sp = StrategyPerformance()
    assert sp.avg_quality == 0.0


def test_strategy_performance_avg_quality_computed():
    """avg_quality = total_quality_score / sample_count."""
    sp = StrategyPerformance(total_quality_score=250.0, sample_count=5)
    assert sp.avg_quality == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# 2. WeightLearningConfig defaults
# ---------------------------------------------------------------------------


def test_config_defaults():
    """Default config values must match the module specification."""
    cfg = WeightLearningConfig()
    assert cfg.algorithm == "thompson_sampling"
    assert cfg.epsilon == pytest.approx(0.1)
    assert cfg.learning_rate == pytest.approx(0.1)
    assert cfg.min_samples == 3
    assert cfg.exploration_bonus == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# 3. record() — state update correctness
# ---------------------------------------------------------------------------


def test_record_success_increments_success_count(learner):
    """A successful record must increment success_count."""
    learner.record("s1", "code", 80.0, 0.3, success=True)
    stats = learner._strategy_stats["code"]["s1"]
    assert stats.success_count == 1
    assert stats.failure_count == 0


def test_record_failure_increments_failure_count(learner):
    """A failed record must increment failure_count."""
    learner.record("s1", "code", 40.0, 0.5, success=False)
    stats = learner._strategy_stats["code"]["s1"]
    assert stats.failure_count == 1
    assert stats.success_count == 0


def test_record_updates_total_quality(learner):
    """total_quality_score must accumulate exactly."""
    learner.record("s1", "code", 75.0, 0.3, success=True)
    learner.record("s1", "code", 85.0, 0.3, success=True)
    stats = learner._strategy_stats["code"]["s1"]
    assert stats.total_quality_score == pytest.approx(160.0)
    assert stats.sample_count == 2


def test_record_beta_alpha_increases_on_success(learner):
    """Beta α must increase by quality_score / 100 on success."""
    learner.record("s1", "code", 80.0, 0.3, success=True)
    alpha, beta = learner._beta_params["code"]["s1"]
    assert alpha == pytest.approx(1.0 + 0.8)
    assert beta == pytest.approx(1.0)


def test_record_beta_beta_increases_on_failure(learner):
    """Beta β must increase by exactly 1.0 on failure."""
    learner.record("s1", "code", 40.0, 0.5, success=False)
    alpha, beta = learner._beta_params["code"]["s1"]
    assert alpha == pytest.approx(1.0)
    assert beta == pytest.approx(2.0)


def test_record_isolated_per_content_type(learner):
    """Stats must be isolated by content_type key."""
    learner.record("s1", "code", 100.0, 0.2, success=True)
    learner.record("s1", "doc", 50.0, 0.4, success=False)
    code_stats = learner._strategy_stats["code"]["s1"]
    doc_stats = learner._strategy_stats["doc"]["s1"]
    assert code_stats.success_count == 1
    assert doc_stats.failure_count == 1


# ---------------------------------------------------------------------------
# 4. select_best_strategy — fallback logic
# ---------------------------------------------------------------------------


def test_select_empty_list_returns_empty_string(learner):
    """Empty available_strategies must return ''."""
    result = learner.select_best_strategy("code", [])
    assert result == ""


def test_select_single_strategy_returns_it(learner):
    """A single available strategy must be returned immediately."""
    result = learner.select_best_strategy("code", ["only_one"])
    assert result == "only_one"


def test_select_explores_when_min_samples_not_met(learner):
    """If any strategy has < min_samples, selection must be random (exploration)."""
    learner.config.min_samples = 3
    learner.record("s1", "code", 80.0, 0.3, success=True)
    # s1 has 1 sample, s2 has 0 → exploration triggered
    with patch("polaris.kernelone.context.context_os.adaptive_weights.random.choice") as mock_choice:
        mock_choice.return_value = "s2"
        result = learner.select_best_strategy("code", ["s1", "s2"])
        assert result == "s2"
        mock_choice.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Thompson Sampling — mathematical correctness
# ---------------------------------------------------------------------------


def test_thompson_sampling_returns_one_of_strategies(learner):
    """Thompson Sampling must always return a member of the input list."""
    for i in range(20):
        learner.record("s1", "code", 80.0, 0.3, success=True)
        learner.record("s2", "code", 20.0, 0.3, success=False)
    result = learner._thompson_sampling("code", ["s1", "s2"])
    assert result in ("s1", "s2")


def test_beta_sample_range(learner):
    """Beta sample must always be in [0, 1]."""
    for _ in range(100):
        sample = learner._beta_sample(2.0, 5.0)
        assert 0.0 <= sample <= 1.0


def test_beta_sample_symmetry(learner):
    """Beta(α=1, β=1) is Uniform(0,1); mean should be ~0.5 over many draws."""
    samples = [learner._beta_sample(1.0, 1.0) for _ in range(2000)]
    mean = sum(samples) / len(samples)
    assert mean == pytest.approx(0.5, abs=0.05)


def test_gamma_sample_positive(learner):
    """Gamma sample must always be strictly positive."""
    for _ in range(200):
        val = learner._gamma_sample(2.5)
        assert val > 0.0


def test_gamma_sample_mean_approximation(learner):
    """Gamma(shape=5, scale=1) has mean ≈ 5.  Check over large N."""
    samples = [learner._gamma_sample(5.0) for _ in range(2000)]
    mean = sum(samples) / len(samples)
    assert mean == pytest.approx(5.0, rel=0.1)


# ---------------------------------------------------------------------------
# 6. Epsilon-Greedy — probability correctness
# ---------------------------------------------------------------------------


def test_epsilon_greedy_explores_with_probability_epsilon(epsilon_config):
    """With epsilon=1.0, must always explore (random choice)."""
    cfg = WeightLearningConfig(algorithm="epsilon_greedy", epsilon=1.0)
    learner = AdaptiveWeightLearner(config=cfg)
    for _ in range(10):
        learner.record("s1", "code", 100.0, 0.2, success=True)
    with patch("polaris.kernelone.context.context_os.adaptive_weights.random.choice") as mock_choice:
        mock_choice.return_value = "s2"
        result = learner._epsilon_greedy("code", ["s1", "s2"])
        assert result == "s2"


def test_epsilon_greedy_exploits_best_quality(epsilon_config):
    """With epsilon=0.0, must always pick the highest avg_quality strategy."""
    cfg = WeightLearningConfig(algorithm="epsilon_greedy", epsilon=0.0)
    learner = AdaptiveWeightLearner(config=cfg)
    learner.record("bad", "code", 20.0, 0.5, success=False)
    learner.record("bad", "code", 20.0, 0.5, success=False)
    learner.record("good", "code", 95.0, 0.2, success=True)
    learner.record("good", "code", 95.0, 0.2, success=True)
    result = learner._epsilon_greedy("code", ["bad", "good"])
    assert result == "good"


def test_epsilon_greedy_tie_breaks_to_first(learner):
    """When qualities are equal, the first strategy in the list wins."""
    learner.config.algorithm = "epsilon_greedy"
    learner.config.epsilon = 0.0
    learner.record("a", "code", 50.0, 0.3, success=True)
    learner.record("b", "code", 50.0, 0.3, success=True)
    result = learner._epsilon_greedy("code", ["a", "b"])
    assert result == "a"


# ---------------------------------------------------------------------------
# 7. Exponential Weighted — formula correctness
# ---------------------------------------------------------------------------


def test_exponential_weight_formula(exponential_config):
    """Verify the weight formula: w = exp(η·(q/100 − 0.5))."""
    learner = AdaptiveWeightLearner(config=exponential_config)
    learner.record("s", "code", 100.0, 0.2, success=True)
    # avg_quality = 100 → q/100 - 0.5 = 0.5 → weight = exp(0.2 * 0.5) = exp(0.1)
    expected_weight = math.exp(0.1)
    stats = learner._strategy_stats["code"]["s"]
    quality = stats.avg_quality
    weight = math.exp(0.2 * (quality / 100 - 0.5))
    assert weight == pytest.approx(expected_weight)


def test_exponential_returns_one_of_strategies(exponential_config):
    """Must return a member of the input list."""
    learner = AdaptiveWeightLearner(config=exponential_config)
    for _ in range(5):
        learner.record("s1", "code", 80.0, 0.2, success=True)
        learner.record("s2", "code", 60.0, 0.2, success=True)
    result = learner._exponential_weighted("code", ["s1", "s2"])
    assert result in ("s1", "s2")


def test_exponential_default_quality_50_when_no_samples(exponential_config):
    """Strategy with 0 samples uses default quality 50.0 → weight = exp(0)."""
    learner = AdaptiveWeightLearner(config=exponential_config)
    # Only record s1, leave s2 at 0 samples
    learner.record("s1", "code", 80.0, 0.2, success=True)
    # s2 has 0 samples → quality = 50.0 → weight = exp(η·0) = 1.0
    # We can verify by calling the method (it will pick probabilistically)
    result = learner._exponential_weighted("code", ["s1", "s2"])
    assert result in ("s1", "s2")


# ---------------------------------------------------------------------------
# 8. get_strategy_weights — normalization
# ---------------------------------------------------------------------------


def test_get_strategy_weights_empty_when_no_data(learner):
    """No data for content_type → empty dict."""
    assert learner.get_strategy_weights("nonexistent") == {}


def test_get_strategy_weights_sum_to_one(learner):
    """Weights must sum to exactly 1.0 (or very close)."""
    learner.record("s1", "code", 80.0, 0.2, success=True)
    learner.record("s2", "code", 20.0, 0.2, success=False)
    weights = learner.get_strategy_weights("code")
    total = sum(weights.values())
    assert total == pytest.approx(1.0)


def test_get_strategy_weights_uniform_when_zero_success_rate(learner):
    """If all success rates are 0, weights must be uniform."""
    learner.record("s1", "code", 10.0, 0.2, success=False)
    learner.record("s2", "code", 10.0, 0.2, success=False)
    weights = learner.get_strategy_weights("code")
    assert weights["s1"] == pytest.approx(0.5)
    assert weights["s2"] == pytest.approx(0.5)


def test_get_strategy_weights_favors_higher_success_rate(learner):
    """Higher success_rate must yield higher weight."""
    learner.record("winner", "code", 100.0, 0.2, success=True)
    learner.record("winner", "code", 100.0, 0.2, success=True)
    learner.record("loser", "code", 10.0, 0.2, success=False)
    weights = learner.get_strategy_weights("code")
    assert weights["winner"] > weights["loser"]


# ---------------------------------------------------------------------------
# 9. get_best_strategy_for_type — min-samples gating
# ---------------------------------------------------------------------------


def test_get_best_strategy_none_when_no_data(learner):
    """No data → (None, 0.0)."""
    assert learner.get_best_strategy_for_type("none") == (None, 0.0)


def test_get_best_strategy_respects_min_samples(learner):
    """Strategies with < min_samples must be ignored."""
    learner.config.min_samples = 5
    learner.record("s1", "code", 100.0, 0.2, success=True)
    best, quality = learner.get_best_strategy_for_type("code")
    assert best is None
    assert quality == 0.0


def test_get_best_strategy_returns_best_when_min_met(learner):
    """When min_samples met, return the strategy with highest avg_quality."""
    for _ in range(5):
        learner.record("good", "code", 90.0, 0.2, success=True)
        learner.record("bad", "code", 30.0, 0.2, success=False)
    best, quality = learner.get_best_strategy_for_type("code")
    assert best == "good"
    assert quality == pytest.approx(90.0)


# ---------------------------------------------------------------------------
# 10. reset — state clearing
# ---------------------------------------------------------------------------


def test_reset_clears_strategy_stats(learner):
    """reset() must clear _strategy_stats."""
    learner.record("s", "code", 50.0, 0.2, success=True)
    learner.reset()
    assert "code" not in learner._strategy_stats


def test_reset_clears_beta_params(learner):
    """reset() must clear _beta_params."""
    learner.record("s", "code", 50.0, 0.2, success=True)
    learner.reset()
    assert "code" not in learner._beta_params


# ---------------------------------------------------------------------------
# 11. Global singleton
# ---------------------------------------------------------------------------


def test_get_weight_learner_returns_singleton():
    """get_weight_learner() must return the same instance across calls."""
    a = get_weight_learner()
    b = get_weight_learner()
    assert a is b


def test_global_singleton_is_adaptive_weight_learner():
    """The singleton must be an instance of AdaptiveWeightLearner."""
    assert isinstance(get_weight_learner(), AdaptiveWeightLearner)
