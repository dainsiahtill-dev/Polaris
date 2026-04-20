"""Tests for Permission Condition Evaluator

单元测试：条件评估器的各种条件类型评估。
"""

import sys
from datetime import datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from polaris.cells.policy.permission.internal.condition_evaluator import (
    ConditionType,
    EvaluationContext,
    PermissionCondition,
    PermissionConditionEvaluator,
)


@pytest.fixture
def evaluator():
    """创建条件评估器实例"""
    return PermissionConditionEvaluator()


class TestFilePathCondition:
    """测试文件路径条件评估"""

    def test_glob_pattern_match(self, evaluator):
        """测试 glob 模式匹配"""
        condition = PermissionCondition(
            type=ConditionType.FILE_PATH,
            pattern="**/*.py",
        )
        context = EvaluationContext(
            action="read",
            target_path="src/fastapi_entrypoint.py",
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is True
        assert "matched" in result.reason

    def test_glob_pattern_no_match(self, evaluator):
        """测试 glob 模式不匹配"""
        condition = PermissionCondition(
            type=ConditionType.FILE_PATH,
            pattern="**/*.js",
        )
        context = EvaluationContext(
            action="read",
            target_path="src/fastapi_entrypoint.py",
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is False
        assert "did not match" in result.reason

    def test_regex_pattern_match(self, evaluator):
        """测试正则表达式匹配"""
        condition = PermissionCondition(
            type=ConditionType.FILE_PATH,
            pattern="regex:.*\\.py$",
        )
        context = EvaluationContext(
            action="read",
            target_path="src/fastapi_entrypoint.py",
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is True

    def test_explicit_glob_prefix(self, evaluator):
        """测试显式 glob: 前缀"""
        condition = PermissionCondition(
            type=ConditionType.FILE_PATH,
            pattern="glob:**/*.py",
        )
        context = EvaluationContext(
            action="read",
            target_path="src/fastapi_entrypoint.py",
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is True

    def test_no_target_path(self, evaluator):
        """测试无目标路径"""
        condition = PermissionCondition(
            type=ConditionType.FILE_PATH,
            pattern="**/*.py",
        )
        context = EvaluationContext(action="read")

        result = evaluator.evaluate(condition, context)

        assert result.matched is False
        assert "No target path" in result.reason

    def test_invalid_regex(self, evaluator):
        """测试无效正则表达式"""
        condition = PermissionCondition(
            type=ConditionType.FILE_PATH,
            pattern="regex:[invalid",
        )
        context = EvaluationContext(
            action="read",
            target_path="src/fastapi_entrypoint.py",
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is False
        assert "Invalid regex" in result.reason


class TestTimeRangeCondition:
    """测试时间范围条件评估"""

    def test_within_time_range(self, evaluator):
        """测试在时间范围内"""
        condition = PermissionCondition(
            type=ConditionType.TIME_RANGE,
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
        context = EvaluationContext(
            action="read",
            timestamp=datetime(2024, 1, 1, 12, 0),  # 12:00
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is True
        assert "within" in result.reason

    def test_outside_time_range(self, evaluator):
        """测试在时间范围外"""
        condition = PermissionCondition(
            type=ConditionType.TIME_RANGE,
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
        context = EvaluationContext(
            action="read",
            timestamp=datetime(2024, 1, 1, 20, 0),  # 20:00
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is False
        assert "outside" in result.reason

    def test_overnight_range(self, evaluator):
        """测试跨天时间范围（如 22:00 - 06:00）"""
        condition = PermissionCondition(
            type=ConditionType.TIME_RANGE,
            start_time=time(22, 0),
            end_time=time(6, 0),
        )
        # 23:00 应该在范围内
        context = EvaluationContext(
            action="read",
            timestamp=datetime(2024, 1, 1, 23, 0),
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is True

    def test_overnight_range_outside(self, evaluator):
        """测试跨天时间范围外"""
        condition = PermissionCondition(
            type=ConditionType.TIME_RANGE,
            start_time=time(22, 0),
            end_time=time(6, 0),
        )
        # 12:00 应该在范围外
        context = EvaluationContext(
            action="read",
            timestamp=datetime(2024, 1, 1, 12, 0),
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is False

    def test_default_time_range(self, evaluator):
        """测试默认时间范围（全天）"""
        condition = PermissionCondition(
            type=ConditionType.TIME_RANGE,
        )
        context = EvaluationContext(
            action="read",
            timestamp=datetime(2024, 1, 1, 12, 0),
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is True


class TestResourceLimitCondition:
    """测试资源限制条件评估"""

    def test_under_limit(self, evaluator):
        """测试资源使用低于限制"""
        condition = PermissionCondition(
            type=ConditionType.RESOURCE_LIMIT,
            resource_type="api_calls",
            limit=100,
        )
        context = EvaluationContext(
            action="read",
            resource_usage={"api_calls": 50},
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is True
        assert "under limit" in result.reason
        assert result.details["current"] == 50
        assert result.details["limit"] == 100

    def test_over_limit(self, evaluator):
        """测试资源使用超过限制"""
        condition = PermissionCondition(
            type=ConditionType.RESOURCE_LIMIT,
            resource_type="api_calls",
            limit=100,
        )
        context = EvaluationContext(
            action="read",
            resource_usage={"api_calls": 150},
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is False
        assert "over limit" in result.reason

    def test_at_limit(self, evaluator):
        """测试资源使用等于限制（边界情况）"""
        condition = PermissionCondition(
            type=ConditionType.RESOURCE_LIMIT,
            resource_type="api_calls",
            limit=100,
        )
        context = EvaluationContext(
            action="read",
            resource_usage={"api_calls": 100},
        )

        result = evaluator.evaluate(condition, context)

        # 等于限制时不满足条件（必须严格小于）
        assert result.matched is False

    def test_default_resource_type(self, evaluator):
        """测试默认资源类型"""
        condition = PermissionCondition(
            type=ConditionType.RESOURCE_LIMIT,
            limit=10,
        )
        context = EvaluationContext(
            action="read",
            resource_usage={"default": 5},
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is True


class TestCustomCondition:
    """测试自定义条件评估"""

    def test_custom_condition_requires_registered_evaluator(self, evaluator):
        """未注册的自定义评估器必须 fail-closed。"""
        condition = PermissionCondition(
            type=ConditionType.CUSTOM,
            custom_evaluator="my_evaluator",
            config={"key": "value"},
        )
        context = EvaluationContext(action="read")

        result = evaluator.evaluate(condition, context)

        assert result.matched is False
        assert "not registered" in result.reason
        assert result.details["evaluator"] == "my_evaluator"

    def test_custom_condition_registered_evaluator(self, evaluator):
        """注册后的自定义评估器可以显式返回结果。"""
        condition = PermissionCondition(
            type=ConditionType.CUSTOM,
            custom_evaluator="my_evaluator",
            config={"key": "value"},
        )
        context = EvaluationContext(action="read")

        evaluator.register_custom_evaluator(
            "my_evaluator",
            lambda _condition, _context: True,
        )

        result = evaluator.evaluate(condition, context)

        assert result.matched is True
        assert "returned True" in result.reason
        assert result.details["evaluator"] == "my_evaluator"


class TestEvaluateAll:
    """测试多条件综合评估"""

    def test_all_mode_all_match(self, evaluator):
        """测试 all 模式，所有条件满足"""
        conditions = [
            PermissionCondition(type=ConditionType.FILE_PATH, pattern="**/*.py"),
            PermissionCondition(type=ConditionType.TIME_RANGE, start_time=time(9, 0), end_time=time(18, 0)),
        ]
        context = EvaluationContext(
            action="read",
            target_path="src/fastapi_entrypoint.py",
            timestamp=datetime(2024, 1, 1, 12, 0),
        )

        result = evaluator.evaluate_all(conditions, context, match_mode="all")

        assert result.matched is True
        assert "All conditions matched" in result.reason

    def test_all_mode_one_fails(self, evaluator):
        """测试 all 模式，一个条件失败"""
        conditions = [
            PermissionCondition(type=ConditionType.FILE_PATH, pattern="**/*.py"),
            PermissionCondition(type=ConditionType.TIME_RANGE, start_time=time(9, 0), end_time=time(12, 0)),
        ]
        context = EvaluationContext(
            action="read",
            target_path="src/fastapi_entrypoint.py",
            timestamp=datetime(2024, 1, 1, 14, 0),  # 14:00 超出时间范围
        )

        result = evaluator.evaluate_all(conditions, context, match_mode="all")

        assert result.matched is False
        assert "Some conditions failed" in result.reason

    def test_any_mode_one_matches(self, evaluator):
        """测试 any 模式，一个条件满足"""
        conditions = [
            PermissionCondition(type=ConditionType.FILE_PATH, pattern="**/*.js"),  # 不匹配
            PermissionCondition(type=ConditionType.TIME_RANGE, start_time=time(9, 0), end_time=time(18, 0)),  # 匹配
        ]
        context = EvaluationContext(
            action="read",
            target_path="src/fastapi_entrypoint.py",
            timestamp=datetime(2024, 1, 1, 12, 0),
        )

        result = evaluator.evaluate_all(conditions, context, match_mode="any")

        assert result.matched is True
        assert "at least one condition matched" in result.reason.lower()

    def test_any_mode_none_match(self, evaluator):
        """测试 any 模式，没有条件满足"""
        conditions = [
            PermissionCondition(type=ConditionType.FILE_PATH, pattern="**/*.js"),
            PermissionCondition(type=ConditionType.TIME_RANGE, start_time=time(9, 0), end_time=time(12, 0)),
        ]
        context = EvaluationContext(
            action="read",
            target_path="src/fastapi_entrypoint.py",
            timestamp=datetime(2024, 1, 1, 14, 0),
        )

        result = evaluator.evaluate_all(conditions, context, match_mode="any")

        assert result.matched is False
        assert "No conditions matched" in result.reason

    def test_empty_conditions(self, evaluator):
        """测试空条件列表"""
        conditions = []
        context = EvaluationContext(action="read")

        result = evaluator.evaluate_all(conditions, context)

        assert result.matched is True
        assert "No conditions to evaluate" in result.reason


class TestPermissionConditionSerialization:
    """测试 PermissionCondition 序列化"""

    def test_to_dict(self):
        """测试转换为字典"""
        condition = PermissionCondition(
            type=ConditionType.FILE_PATH,
            pattern="**/*.py",
            limit=100,
        )

        data = condition.to_dict()

        assert data["type"] == "file_path"
        assert data["pattern"] == "**/*.py"
        assert data["limit"] == 100

    def test_from_dict(self):
        """测试从字典创建"""
        data = {
            "type": "time_range",
            "start_time": "09:00:00",
            "end_time": "18:00:00",
        }

        condition = PermissionCondition.from_dict(data)

        assert condition.type == ConditionType.TIME_RANGE
        assert condition.start_time == time(9, 0)
        assert condition.end_time == time(18, 0)

    def test_roundtrip(self):
        """测试序列化往返"""
        original = PermissionCondition(
            type=ConditionType.RESOURCE_LIMIT,
            resource_type="api_calls",
            limit=100,
        )

        data = original.to_dict()
        restored = PermissionCondition.from_dict(data)

        assert restored.type == original.type
        assert restored.resource_type == original.resource_type
        assert restored.limit == original.limit


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
