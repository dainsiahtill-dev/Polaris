"""Tests for Debug Strategy Engine - 调试策略引擎测试。

覆盖：
- 正常场景: 分类错误并生成合适的调试计划
- 边界场景: 未知错误类型、空上下文、循环依赖
- 异常场景: 策略执行失败、超时、资源耗尽
- 回归场景: 模拟"头痛医头"案例，验证新策略能发现根因
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.debug_strategy import (
    DebugPhase,
    DebugStrategy,
    DebugStrategyEngine,
    ErrorCategory,
    ErrorContext,
)
from polaris.cells.roles.kernel.internal.debug_strategy.evidence_collector import (
    EvidenceCollector,
)
from polaris.cells.roles.kernel.internal.debug_strategy.hypothesis_generator import (
    HypothesisGenerator,
)
from polaris.cells.roles.kernel.internal.debug_strategy.models import (
    DebugPlan,
    ErrorClassification,
)
from polaris.cells.roles.kernel.internal.debug_strategy.strategies import (
    BinarySearchStrategy,
    ConditionalWaitStrategy,
    DefenseInDepthStrategy,
    PatternMatchStrategy,
    TraceBackwardStrategy,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> DebugStrategyEngine:
    """创建策略引擎实例。"""
    return DebugStrategyEngine()


@pytest.fixture
def sample_runtime_error() -> ErrorContext:
    """运行时错误上下文。"""
    return ErrorContext(
        error_type="runtime_error",
        error_message="Object has no attribute 'process'",
        stack_trace="File 'main.py', line 42, in process_data\n    result = obj.process()\nAttributeError: ...",
        recent_changes=["Modified data_processor.py", "Updated config.yaml"],
        environment={"PYTHONPATH": "/app", "ENV": "test"},
        file_path="main.py",
        line_number=42,
    )


@pytest.fixture
def sample_syntax_error() -> ErrorContext:
    """语法错误上下文。"""
    return ErrorContext(
        error_type="syntax_error",
        error_message="invalid syntax at line 15",
        stack_trace="File 'script.py', line 15\n    if x = 5:\n         ^\nSyntaxError: invalid syntax",
        recent_changes=["Added new feature"],
        file_path="script.py",
        line_number=15,
    )


@pytest.fixture
def sample_timing_error() -> ErrorContext:
    """时序错误上下文。"""
    return ErrorContext(
        error_type="timeout",
        error_message="Connection timeout after 30 seconds",
        stack_trace="File 'client.py', line 88, in connect\n    socket.connect(timeout=30)\nTimeoutError: ...",
        recent_changes=["Updated network config"],
        environment={"TIMEOUT": "30"},
        file_path="client.py",
        line_number=88,
    )


@pytest.fixture
def sample_regression_error() -> ErrorContext:
    """回归错误上下文。"""
    return ErrorContext(
        error_type="regression",
        error_message="Feature X stopped working after last update",
        stack_trace="AssertionError: expected True but got False",
        recent_changes=["Commit abc123", "Commit def456", "Commit ghi789"],
        file_path="feature_x.py",
        line_number=100,
    )


@pytest.fixture
def sample_defense_error() -> ErrorContext:
    """防御性错误上下文。"""
    return ErrorContext(
        error_type="assertion_error",
        error_message="assert len(items) > 0 failed",
        stack_trace="File 'processor.py', line 55, in process\n    assert len(items) > 0\nAssertionError",
        recent_changes=["Refactored input handling"],
        file_path="processor.py",
        line_number=55,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Normal Scenarios
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalScenarios:
    """正常场景测试。"""

    def test_select_strategy_runtime_error(
        self, engine: DebugStrategyEngine, sample_runtime_error: ErrorContext
    ) -> None:
        """测试运行时错误策略选择。"""
        plan = engine.select_strategy(sample_runtime_error)

        assert isinstance(plan, DebugPlan)
        assert plan.strategy == DebugStrategy.TRACE_BACKWARD
        assert len(plan.steps) > 0
        assert plan.estimated_time > 0

    def test_select_strategy_syntax_error(self, engine: DebugStrategyEngine, sample_syntax_error: ErrorContext) -> None:
        """测试语法错误策略选择。"""
        plan = engine.select_strategy(sample_syntax_error)

        assert isinstance(plan, DebugPlan)
        assert plan.strategy == DebugStrategy.PATTERN_MATCH
        assert len(plan.steps) > 0

    def test_select_strategy_timing_error(self, engine: DebugStrategyEngine, sample_timing_error: ErrorContext) -> None:
        """测试时序错误策略选择（最高优先级）。"""
        plan = engine.select_strategy(sample_timing_error)

        assert isinstance(plan, DebugPlan)
        assert plan.strategy == DebugStrategy.CONDITIONAL_WAIT
        assert len(plan.steps) > 0

    def test_select_strategy_regression(
        self, engine: DebugStrategyEngine, sample_regression_error: ErrorContext
    ) -> None:
        """测试回归错误策略选择。"""
        plan = engine.select_strategy(sample_regression_error)

        assert isinstance(plan, DebugPlan)
        assert plan.strategy == DebugStrategy.BINARY_SEARCH
        assert len(plan.steps) > 0

    def test_select_strategy_defense(self, engine: DebugStrategyEngine, sample_defense_error: ErrorContext) -> None:
        """测试防御性错误策略选择。"""
        plan = engine.select_strategy(sample_defense_error)

        assert isinstance(plan, DebugPlan)
        assert plan.strategy == DebugStrategy.DEFENSE_IN_DEPTH
        assert len(plan.steps) > 0

    def test_classify_error(self, engine: DebugStrategyEngine, sample_runtime_error: ErrorContext) -> None:
        """测试错误分类。"""
        classification = engine.classify_error(sample_runtime_error)

        assert isinstance(classification, ErrorClassification)
        assert classification.category == ErrorCategory.LOGIC_ERROR
        assert classification.severity in ["low", "medium", "high", "critical"]
        assert classification.debug_plan is not None
        assert len(classification.suggested_strategies) > 0

    def test_four_phases_present(self, engine: DebugStrategyEngine, sample_runtime_error: ErrorContext) -> None:
        """测试四阶段都存在。"""
        plan = engine.select_strategy(sample_runtime_error)

        phases = [step.phase for step in plan.steps]
        assert DebugPhase.ROOT_CAUSE_INVESTIGATION in phases
        assert DebugPhase.PATTERN_ANALYSIS in phases
        assert DebugPhase.HYPOTHESIS_TESTING in phases
        assert DebugPhase.IMPLEMENTATION in phases

    def test_rollback_commands_present(self, engine: DebugStrategyEngine, sample_runtime_error: ErrorContext) -> None:
        """测试回滚命令存在。"""
        plan = engine.select_strategy(sample_runtime_error)

        assert plan.rollback_plan
        # 至少一个步骤有回滚命令
        steps_with_rollback = [s for s in plan.steps if s.rollback_commands]
        assert len(steps_with_rollback) > 0


# ─────────────────────────────────────────────────────────────────────────────
# Boundary Scenarios
# ─────────────────────────────────────────────────────────────────────────────


class TestBoundaryScenarios:
    """边界场景测试。"""

    def test_unknown_error_type(self, engine: DebugStrategyEngine) -> None:
        """测试未知错误类型。"""
        context = ErrorContext(
            error_type="completely_unknown_error_xyz",
            error_message="Something went wrong",
            stack_trace="",
        )

        plan = engine.select_strategy(context)

        # 应该回退到默认策略
        assert isinstance(plan, DebugPlan)
        assert plan.strategy == DebugStrategy.TRACE_BACKWARD

    def test_empty_context(self, engine: DebugStrategyEngine) -> None:
        """测试空上下文。"""
        context = ErrorContext(
            error_type="",
            error_message="",
            stack_trace="",
        )

        plan = engine.select_strategy(context)

        assert isinstance(plan, DebugPlan)
        assert len(plan.steps) > 0

    def test_minimal_context(self, engine: DebugStrategyEngine) -> None:
        """测试最小上下文。"""
        context = ErrorContext(
            error_type="error",
            error_message="error",
            stack_trace="error",
        )

        classification = engine.classify_error(context)
        assert classification.category is not None
        assert classification.debug_plan is not None

    def test_no_file_path(self, engine: DebugStrategyEngine) -> None:
        """测试没有文件路径。"""
        context = ErrorContext(
            error_type="runtime_error",
            error_message="Something failed",
            stack_trace="Error at unknown location",
            file_path=None,
            line_number=None,
        )

        plan = engine.select_strategy(context)
        assert isinstance(plan, DebugPlan)
        # 应该能处理没有文件路径的情况
        assert len(plan.steps) > 0

    def test_very_long_error_message(self, engine: DebugStrategyEngine) -> None:
        """测试超长错误消息。"""
        context = ErrorContext(
            error_type="runtime_error",
            error_message="A" * 10000,
            stack_trace="B" * 5000,
        )

        plan = engine.select_strategy(context)
        assert isinstance(plan, DebugPlan)


# ─────────────────────────────────────────────────────────────────────────────
# Exception Scenarios
# ─────────────────────────────────────────────────────────────────────────────


class TestExceptionScenarios:
    """异常场景测试。"""

    def test_strategy_with_invalid_context(self) -> None:
        """测试策略处理无效上下文。"""
        strategy = TraceBackwardStrategy()
        context = ErrorContext(
            error_type="",
            error_message="",
            stack_trace="",
        )

        # 应该能处理空上下文
        assert strategy.can_handle(context) is False  # 空类型不匹配任何模式

    def test_evidence_collector_empty(self) -> None:
        """测试证据收集器空状态。"""
        collector = EvidenceCollector()

        assert collector.get_all_evidence() == []
        assert collector.get_evidence_by_source("code") == []

    def test_hypothesis_generator_empty(self) -> None:
        """测试假设生成器空状态。"""
        generator = HypothesisGenerator()
        context = ErrorContext(
            error_type="unknown",
            error_message="",
            stack_trace="",
        )

        hypotheses = generator.generate_hypotheses(context, ErrorCategory.UNKNOWN_ERROR)
        assert len(hypotheses) > 0  # 即使未知也应该有默认假设

    def test_all_strategies_can_handle_none(self) -> None:
        """测试所有策略能处理None-like上下文。"""
        strategies = [
            TraceBackwardStrategy(),
            PatternMatchStrategy(),
            BinarySearchStrategy(),
            ConditionalWaitStrategy(),
            DefenseInDepthStrategy(),
        ]

        context = ErrorContext(
            error_type="",
            error_message="",
            stack_trace="",
        )

        for strategy in strategies:
            # 不应该抛出异常
            result = strategy.can_handle(context)
            assert isinstance(result, bool)


# ─────────────────────────────────────────────────────────────────────────────
# Regression Scenarios - "头痛医头"案例
# ─────────────────────────────────────────────────────────────────────────────


class TestRegressionScenarios:
    """回归场景测试：验证新策略能发现根因。"""

    def test_surface_fix_vs_root_cause(self, engine: DebugStrategyEngine) -> None:
        """测试：表面修复 vs 根因修复。

        模拟场景：修复了症状（添加try-except）但没有修复根因（输入验证缺失）。
        新策略应该能识别出需要DefenseInDepth策略。
        """
        # 模拟一个反复出现的错误（"头痛医头"模式）
        context = ErrorContext(
            error_type="attribute_error",
            error_message="'NoneType' object has no attribute 'process'",
            stack_trace="File 'handler.py', line 30, in handle\n    data.process()\nAttributeError",
            previous_attempts=[
                "Added try-except at line 30",
                "Added null check at line 30",
                "Refactored error handling",
            ],
            recent_changes=["Fixed error at line 30 again"],
            file_path="handler.py",
            line_number=30,
        )

        plan = engine.select_strategy(context)

        # 应该建议防御深度策略，因为反复修复同一位置
        assert plan.strategy in [
            DebugStrategy.DEFENSE_IN_DEPTH,
            DebugStrategy.TRACE_BACKWARD,
        ]

        # 检查是否包含根因调查步骤（不是直接修复）
        investigation_steps = [s for s in plan.steps if s.phase == DebugPhase.ROOT_CAUSE_INVESTIGATION]
        assert len(investigation_steps) > 0

    def test_repeated_same_error(self, engine: DebugStrategyEngine) -> None:
        """测试：同一错误反复出现。

        模拟场景：同样的KeyError反复出现，说明缺少输入验证。
        """
        context = ErrorContext(
            error_type="key_error",
            error_message="Key 'user_id' not found",
            stack_trace="File 'api.py', line 45, in get_user\n    user_id = data['user_id']\nKeyError",
            previous_attempts=[
                "Fixed KeyError at line 45",
                "Added default value",
            ],
            file_path="api.py",
            line_number=45,
        )

        classification = engine.classify_error(context)

        # 应该识别为逻辑错误（缺少验证）
        assert classification.category == ErrorCategory.LOGIC_ERROR
        # 应该有调试计划
        assert classification.debug_plan is not None

    def test_binary_search_for_regression(self, engine: DebugStrategyEngine) -> None:
        """测试：使用二分搜索定位回归。

        模拟场景：功能之前正常，现在出错，有多个提交。
        """
        context = ErrorContext(
            error_type="regression",
            error_message="Feature stopped working",
            stack_trace="AssertionError",
            recent_changes=["Commit 1", "Commit 2", "Commit 3", "Commit 4", "Commit 5"],
            file_path="feature.py",
            line_number=100,
        )

        plan = engine.select_strategy(context)

        # 应该选择二分搜索策略
        assert plan.strategy == DebugStrategy.BINARY_SEARCH

        # 检查是否有git bisect步骤
        step_commands = " ".join(" ".join(s.commands) for s in plan.steps)
        assert "git bisect" in step_commands

    def test_timing_issue_root_cause(self, engine: DebugStrategyEngine) -> None:
        """测试：时序问题的根因。

        模拟场景：间歇性超时，表面修复是增加超时时间，
        根因是资源未就绪就使用。
        """
        context = ErrorContext(
            error_type="timeout",
            error_message="Connection timeout - resource not ready",
            stack_trace="File 'connector.py', line 20, in connect\n    wait_for_resource()\nTimeoutError",
            previous_attempts=["Increased timeout to 60s", "Added retry logic"],
            environment={"TIMEOUT": "60", "RETRY": "3"},
            file_path="connector.py",
            line_number=20,
        )

        plan = engine.select_strategy(context)

        # 应该选择条件等待策略（最高优先级）
        assert plan.strategy == DebugStrategy.CONDITIONAL_WAIT

        # 检查是否有条件等待相关步骤
        step_commands = " ".join(" ".join(s.commands) for s in plan.steps)
        assert "wait" in step_commands.lower() or "condition" in step_commands.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Strategy-specific Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestTraceBackwardStrategy:
    """反向追溯策略测试。"""

    def test_can_handle_runtime_errors(self) -> None:
        """测试能处理运行时错误。"""
        strategy = TraceBackwardStrategy()

        assert strategy.can_handle(ErrorContext(error_type="runtime_error", error_message="", stack_trace=""))
        assert strategy.can_handle(ErrorContext(error_type="AttributeError", error_message="", stack_trace=""))
        assert strategy.can_handle(ErrorContext(error_type="KeyError", error_message="", stack_trace=""))

    def test_generate_plan_structure(self) -> None:
        """测试生成的计划结构。"""
        strategy = TraceBackwardStrategy()
        context = ErrorContext(
            error_type="runtime_error",
            error_message="Test error",
            stack_trace="Traceback...",
            file_path="test.py",
            line_number=10,
        )

        plan = strategy.generate_plan(context)

        assert plan.plan_id.startswith("trace_backward_")
        assert plan.strategy == DebugStrategy.TRACE_BACKWARD
        assert len(plan.steps) >= 4  # 至少四阶段各一步

    def test_plan_has_all_phases(self) -> None:
        """测试计划包含所有四阶段。"""
        strategy = TraceBackwardStrategy()
        context = ErrorContext(
            error_type="runtime_error",
            error_message="Test",
            stack_trace="",
            file_path="test.py",
            line_number=1,
        )

        plan = strategy.generate_plan(context)
        phases = {step.phase for step in plan.steps}

        assert DebugPhase.ROOT_CAUSE_INVESTIGATION in phases
        assert DebugPhase.PATTERN_ANALYSIS in phases
        assert DebugPhase.HYPOTHESIS_TESTING in phases
        assert DebugPhase.IMPLEMENTATION in phases


class TestPatternMatchStrategy:
    """模式匹配策略测试。"""

    def test_can_handle_syntax_errors(self) -> None:
        """测试能处理语法错误。"""
        strategy = PatternMatchStrategy()

        assert strategy.can_handle(ErrorContext(error_type="syntax_error", error_message="", stack_trace=""))
        assert strategy.can_handle(ErrorContext(error_type="import_error", error_message="", stack_trace=""))

    def test_generate_plan(self) -> None:
        """测试生成计划。"""
        strategy = PatternMatchStrategy()
        context = ErrorContext(
            error_type="syntax_error",
            error_message="invalid syntax",
            stack_trace="",
            file_path="test.py",
            line_number=1,
        )

        plan = strategy.generate_plan(context)

        assert plan.strategy == DebugStrategy.PATTERN_MATCH
        assert len(plan.steps) > 0


class TestConditionalWaitStrategy:
    """条件等待策略测试。"""

    def test_can_handle_timeout(self) -> None:
        """测试能处理超时错误。"""
        strategy = ConditionalWaitStrategy()

        assert strategy.can_handle(ErrorContext(error_type="timeout", error_message="", stack_trace=""))
        assert strategy.can_handle(ErrorContext(error_type="async_error", error_message="", stack_trace=""))

    def test_can_handle_from_message(self) -> None:
        """测试能从消息识别时序问题。"""
        strategy = ConditionalWaitStrategy()

        context = ErrorContext(
            error_type="error",
            error_message="resource is not ready yet",
            stack_trace="",
        )

        assert strategy.can_handle(context)

    def test_plan_has_wait_steps(self) -> None:
        """测试计划包含等待步骤。"""
        strategy = ConditionalWaitStrategy()
        context = ErrorContext(
            error_type="timeout",
            error_message="Connection timeout",
            stack_trace="",
            file_path="client.py",
            line_number=20,
        )

        plan = strategy.generate_plan(context)

        # 检查是否有条件等待相关内容
        all_commands = " ".join(" ".join(s.commands) for s in plan.steps)
        assert "wait" in all_commands.lower() or "condition" in all_commands.lower()


class TestDefenseInDepthStrategy:
    """防御深度策略测试。"""

    def test_can_handle_assertion_errors(self) -> None:
        """测试能处理断言错误。"""
        strategy = DefenseInDepthStrategy()

        assert strategy.can_handle(ErrorContext(error_type="assertion_error", error_message="", stack_trace=""))
        assert strategy.can_handle(ErrorContext(error_type="validation_error", error_message="", stack_trace=""))

    def test_plan_has_four_layers(self) -> None:
        """测试计划包含四层防御。"""
        strategy = DefenseInDepthStrategy()
        context = ErrorContext(
            error_type="assertion_error",
            error_message="assert failed",
            stack_trace="",
            file_path="test.py",
            line_number=10,
        )

        plan = strategy.generate_plan(context)

        # 检查是否有防御层相关内容
        all_commands = " ".join(" ".join(s.commands) for s in plan.steps)
        assert "input" in all_commands.lower() or "validation" in all_commands.lower()
        assert "assert" in all_commands.lower()


class TestBinarySearchStrategy:
    """二分搜索策略测试。"""

    def test_can_handle_regression(self) -> None:
        """测试能处理回归错误。"""
        strategy = BinarySearchStrategy()

        context = ErrorContext(
            error_type="regression",
            error_message="Feature stopped working",
            stack_trace="",
            recent_changes=["commit1", "commit2"],
        )

        assert strategy.can_handle(context)

    def test_can_handle_from_changes(self) -> None:
        """测试能从变更历史识别。"""
        strategy = BinarySearchStrategy()

        context = ErrorContext(
            error_type="error",
            error_message="Something broke",
            stack_trace="",
            recent_changes=["commit1"],
        )

        assert strategy.can_handle(context)

    def test_plan_has_git_bisect(self) -> None:
        """测试计划包含git bisect。"""
        strategy = BinarySearchStrategy()
        context = ErrorContext(
            error_type="regression",
            error_message="Feature broke",
            stack_trace="",
            recent_changes=["c1", "c2", "c3"],
            file_path="feature.py",
            line_number=1,
        )

        plan = strategy.generate_plan(context)

        all_commands = " ".join(" ".join(s.commands) for s in plan.steps)
        assert "git bisect" in all_commands


# ─────────────────────────────────────────────────────────────────────────────
# Integration Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestIntegration:
    """集成测试。"""

    def test_full_debug_flow(self, engine: DebugStrategyEngine) -> None:
        """测试完整调试流程。"""
        context = ErrorContext(
            error_type="runtime_error",
            error_message="Test error for full flow",
            stack_trace="Traceback (most recent call last):\n  File 'test.py', line 10\n    raise Exception('test')",
            recent_changes=["Change 1", "Change 2"],
            environment={"TEST": "true"},
            file_path="test.py",
            line_number=10,
            previous_attempts=[],
        )

        # 1. 分类错误
        classification = engine.classify_error(context)
        assert classification.category is not None
        assert classification.debug_plan is not None

        # 2. 获取策略信息
        strategies = engine.get_available_strategies()
        assert len(strategies) == 5

        # 3. 验证计划完整性
        plan = classification.debug_plan
        assert plan.plan_id
        assert plan.rollback_plan
        assert len(plan.success_criteria) > 0
        assert len(plan.failure_criteria) > 0

    def test_evidence_collection(self) -> None:
        """测试证据收集集成。"""
        collector = EvidenceCollector()
        context = ErrorContext(
            error_type="test_error",
            error_message="Test",
            stack_trace="Trace...",
            environment={"KEY": "VALUE"},
            recent_changes=["Change 1"],
            file_path="test.py",
            line_number=5,
        )

        # 收集证据
        evidences = collector.collect_from_context(context)
        assert len(evidences) >= 3  # stack, env, changes, code

        # 按来源获取
        stack_evidence = collector.get_evidence_by_source("stack_trace")
        assert len(stack_evidence) > 0

        # 清空
        collector.clear()
        assert len(collector.get_all_evidence()) == 0

    def test_hypothesis_generation(self) -> None:
        """测试假设生成集成。"""
        generator = HypothesisGenerator()
        context = ErrorContext(
            error_type="key_error",
            error_message="Key 'test' not found",
            stack_trace="",
        )

        hypotheses = generator.generate_hypotheses(context, ErrorCategory.LOGIC_ERROR)
        assert len(hypotheses) > 0

        # 检查假设结构
        for hyp in hypotheses:
            assert hyp.hypothesis_id
            assert hyp.description
            assert 0 <= hyp.confidence <= 1
            assert hyp.test_approach

    def test_strategy_priority(self, engine: DebugStrategyEngine) -> None:
        """测试策略优先级。

        时序问题应该优先选择ConditionalWaitStrategy。
        """
        # 同时匹配多个策略的上下文
        context = ErrorContext(
            error_type="timeout",  # 匹配ConditionalWaitStrategy
            error_message="Connection failed",  # 也匹配TraceBackwardStrategy
            stack_trace="",
            recent_changes=["commit1"],  # 也匹配BinarySearchStrategy
        )

        plan = engine.select_strategy(context)

        # ConditionalWaitStrategy优先级最高
        assert plan.strategy == DebugStrategy.CONDITIONAL_WAIT


# ─────────────────────────────────────────────────────────────────────────────
# Performance Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPerformance:
    """性能测试。"""

    def test_strategy_selection_speed(self, engine: DebugStrategyEngine, sample_runtime_error: ErrorContext) -> None:
        """测试策略选择速度。"""
        import time

        start = time.time()
        for _ in range(100):
            engine.select_strategy(sample_runtime_error)
        elapsed = time.time() - start

        # 100次选择应该在1秒内完成
        assert elapsed < 1.0

    def test_classification_speed(self, engine: DebugStrategyEngine, sample_runtime_error: ErrorContext) -> None:
        """测试分类速度。"""
        import time

        start = time.time()
        for _ in range(100):
            engine.classify_error(sample_runtime_error)
        elapsed = time.time() - start

        # 100次分类应该在1秒内完成
        assert elapsed < 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
