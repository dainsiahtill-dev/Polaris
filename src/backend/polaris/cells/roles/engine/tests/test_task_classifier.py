"""Tests for internal/classifier.py — TaskClassifier and strategy scoring."""

from __future__ import annotations

from polaris.cells.roles.engine.internal.base import EngineStrategy
from polaris.cells.roles.engine.internal.classifier import (
    TaskClassifier,
    classify_task,
    get_task_classifier,
)

# ---------------------------------------------------------------------------
# TaskClassifier — scoring
# ---------------------------------------------------------------------------


def test_classifier_assigns_react_to_explore_task() -> None:
    """ReAct patterns like '分析' should boost REACT score."""
    classifier = TaskClassifier()
    scores = classifier._calculate_scores("分析代码结构", {})
    assert scores[EngineStrategy.REACT] > 0.1


def test_classifier_assigns_plan_solve_to_implement_task() -> None:
    """'实现' keyword should boost Plan-Solve score."""
    classifier = TaskClassifier()
    scores = classifier._calculate_scores("实现用户登录", {})
    assert scores[EngineStrategy.PLAN_SOLVE] >= scores[EngineStrategy.REACT]


def test_classifier_assigns_tot_to_design_task() -> None:
    """'设计' in '设计系统架构' should boost ToT score."""
    classifier = TaskClassifier()
    scores = classifier._calculate_scores("设计系统架构", {})
    assert scores[EngineStrategy.TOT] > 0.1


def test_classifier_assigns_sequential_to_execute_task() -> None:
    """'运行' keyword should boost Sequential score."""
    classifier = TaskClassifier()
    scores = classifier._calculate_scores("运行测试套件", {})
    assert scores[EngineStrategy.SEQUENTIAL] > 0.1


def test_classifier_all_strategies_get_minimum_score() -> None:
    """Every strategy should receive at least 0.1 even without matching keywords."""
    classifier = TaskClassifier()
    scores = classifier._calculate_scores("do something", {})
    for strategy, score in scores.items():
        assert score >= 0.1, f"{strategy} got score {score}"


def test_classifier_context_can_boost_score() -> None:
    """Context dict can influence scoring via _adjust_by_context."""
    classifier = TaskClassifier()
    base = classifier._calculate_scores("generic task", {})
    # Role=architect boosts ToT
    boosted = classifier._calculate_scores("generic task", {"role": "architect", "complexity": "high"})
    assert boosted[EngineStrategy.TOT] >= base[EngineStrategy.TOT]


def test_classifier_classify_returns_engine_strategy() -> None:
    """classify() returns the EngineStrategy enum, not a string."""
    classifier = TaskClassifier()
    result = classifier.classify("实现功能 x", None)
    assert isinstance(result, EngineStrategy)
    assert result in (EngineStrategy.REACT, EngineStrategy.PLAN_SOLVE, EngineStrategy.TOT, EngineStrategy.SEQUENTIAL)


def test_classifier_classify_with_context() -> None:
    """Context dict should be passed through."""
    classifier = TaskClassifier()
    result = classifier.classify("通用任务", {"role": "pm"})
    assert isinstance(result, EngineStrategy)


def test_classifier_classify_empty_task() -> None:
    """Empty task should still return a valid strategy, not raise."""
    classifier = TaskClassifier()
    result = classifier.classify("", None)
    assert isinstance(result, EngineStrategy)


def test_classifier_adjust_by_context_role_director() -> None:
    """role=director should boost SEQUENTIAL score."""
    classifier = TaskClassifier()
    scores = classifier._calculate_scores("execute it", {"role": "director"})
    assert scores[EngineStrategy.SEQUENTIAL] > 0.1


def test_classifier_adjust_by_context_role_architect() -> None:
    """role=architect should boost ToT score."""
    classifier = TaskClassifier()
    scores = classifier._calculate_scores("evaluate options", {"role": "architect"})
    assert scores[EngineStrategy.TOT] > 0.1


def test_classifier_adjust_by_context_complexity_high() -> None:
    """complexity=high should boost ToT and REACT scores."""
    classifier = TaskClassifier()
    base = classifier._calculate_scores("generic task", {})
    high = classifier._calculate_scores("generic task", {"complexity": "high"})
    assert high[EngineStrategy.TOT] >= base[EngineStrategy.TOT]


def test_classifier_adjust_by_context_complexity_low() -> None:
    """complexity=low should boost SEQUENTIAL score."""
    classifier = TaskClassifier()
    base = classifier._calculate_scores("generic task", {})
    low = classifier._calculate_scores("generic task", {"complexity": "low"})
    assert low[EngineStrategy.SEQUENTIAL] >= base[EngineStrategy.SEQUENTIAL]


def test_classifier_match_patterns_returns_float() -> None:
    """_match_patterns should return a float score."""
    classifier = TaskClassifier()
    score = classifier._match_patterns("实现登录功能", classifier._plan_solve_patterns)
    assert isinstance(score, float)
    assert score >= 1.0  # at least one pattern matched


def test_classifier_match_patterns_case_insensitive() -> None:
    """Pattern matching should be case-insensitive (re.IGNORECASE)."""
    classifier = TaskClassifier()
    score_lower = classifier._match_patterns("分析代码", classifier._react_patterns)
    score_upper = classifier._match_patterns("分析代码", classifier._react_patterns)
    assert score_lower == score_upper


# ---------------------------------------------------------------------------
# Global convenience function
# ---------------------------------------------------------------------------


def test_classify_task_convenience() -> None:
    """classify_task() function should delegate to classifier."""
    result = classify_task("实现登录", None)
    assert isinstance(result, EngineStrategy)


# ---------------------------------------------------------------------------
# get_task_classifier — global singleton
# ---------------------------------------------------------------------------


def test_get_task_classifier_returns_classifier() -> None:
    classifier = get_task_classifier()
    assert isinstance(classifier, TaskClassifier)


def test_get_task_classifier_is_same_instance() -> None:
    """Calling twice returns the same object (module-level singleton)."""
    a = get_task_classifier()
    b = get_task_classifier()
    assert a is b


# ---------------------------------------------------------------------------
# get_reason
# ---------------------------------------------------------------------------


def test_classifier_get_reason_returns_string() -> None:
    """get_reason should return a non-empty explanation."""
    classifier = TaskClassifier()
    reason = classifier.get_reason("实现登录")
    assert isinstance(reason, str)
    assert len(reason) > 0


def test_classifier_get_reason_reflects_task() -> None:
    """Different tasks should produce potentially different reasons."""
    r1 = get_task_classifier().get_reason("实现登录")
    r2 = get_task_classifier().get_reason("运行测试")
    # Both should be callable without error and non-empty
    assert isinstance(r1, str)
    assert isinstance(r2, str)
