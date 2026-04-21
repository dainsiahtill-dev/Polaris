"""Debug Strategy Engine - 调试策略引擎。

基于Superpowers的"Systematic Debugging"设计精华，为Polaris提供原生的系统化调试能力。

核心设计:
- 四阶段调试: 根因调查→模式分析→假设测试→实施
- 防御性编程四层验证
- 条件等待技术（解决时序问题）
"""

from __future__ import annotations

from enum import Enum, auto


class DebugPhase(Enum):
    """调试阶段（来自Superpowers的四阶段）。"""

    ROOT_CAUSE_INVESTIGATION = auto()  # 根因调查
    PATTERN_ANALYSIS = auto()  # 模式分析
    HYPOTHESIS_TESTING = auto()  # 假设测试
    IMPLEMENTATION = auto()  # 实施


class DebugStrategy(Enum):
    """调试策略类型。"""

    TRACE_BACKWARD = "trace_backward"  # 反向追溯
    PATTERN_MATCH = "pattern_match"  # 模式匹配
    BINARY_SEARCH = "binary_search"  # 二分定位
    CONDITIONAL_WAIT = "conditional_wait"  # 条件等待
    DEFENSE_IN_DEPTH = "defense_in_depth"  # 防御深度


class DefenseLayer(Enum):
    """防御性编程四层验证。"""

    INPUT_VALIDATION = auto()  # 输入验证层
    PRECONDITION_CHECK = auto()  # 前置条件层
    INVARIANT_ASSERTION = auto()  # 不变量断言层
    POSTCONDITION_VERIFY = auto()  # 后置条件验证层


class ErrorCategory(Enum):
    """错误分类（用于策略选择）。"""

    SYNTAX_ERROR = "syntax_error"
    RUNTIME_ERROR = "runtime_error"
    LOGIC_ERROR = "logic_error"
    TIMING_ERROR = "timing_error"
    RESOURCE_ERROR = "resource_error"
    PERMISSION_ERROR = "permission_error"
    NETWORK_ERROR = "network_error"
    UNKNOWN_ERROR = "unknown_error"


__all__ = [
    "DebugPhase",
    "DebugStrategy",
    "DefenseLayer",
    "ErrorCategory",
]
