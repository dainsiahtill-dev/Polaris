"""Error Diagnosis Engine - 智能错误诊断工具.

设计目标：
1. 给定错误消息，自动定位根因
2. 基于模式库搜索相关代码
3. 提供上下文和修复建议

用法：
    from polaris.kernelone.audit.diagnosis import diagnose_error

    result = diagnose_error(
        error_message="object of type 'method' has no len()",
        traceback_stack=["file.py:100: in func", "..."]
    )
    print(result.root_cause)      # 根因描述
    print(result.suspicious_locs) # 可疑位置列表
    print(result.fix_suggestion)  # 修复建议
"""

from __future__ import annotations

import re
import traceback as tb_module
from dataclasses import dataclass, field

__all__ = [
    "DiagnosisResult",
    "ErrorPattern",
    "PatternRegistry",
    "diagnose_error",
]


# ============================================================================
# 错误模式定义
# ============================================================================


@dataclass
class ErrorPattern:
    """错误模式定义"""

    name: str
    regex: str
    description: str
    root_cause_template: str
    fix_suggestion_template: str
    search_keywords: list[str]
    priority: int = 0  # 优先级，数字越大优先级越高


# 内置错误模式库
BUILTIN_PATTERNS: list[ErrorPattern] = [
    ErrorPattern(
        name="len_on_invalid_type",
        regex=r"object of type '(.+)' has no len",
        description="对不支持 len() 的对象调用 len()",
        root_cause_template=(
            "在代码中对类型为 '{value_type}' 的对象调用了 len()。"
            "常见原因：\n"
            "  1. 错误地引用了类方法而不是实例属性（如 AIStreamEvent.reasoning 而非 event.reasoning）\n"
            "  2. 变量被错误地赋值为方法引用\n"
            "  3. API 返回值类型与预期不符"
        ),
        fix_suggestion_template=(
            "修复方案：\n"
            "  1. 检查变量赋值，确保赋值的是值而非方法引用\n"
            "  2. 使用 isinstance() 检查类型\n"
            "  3. 检查 dataclass factory 方法是否被错误使用\n"
            "  4. 添加类型守卫：len(obj) if hasattr(obj, '__len__') else 0"
        ),
        search_keywords=["len(", ".reasoning", ".chunk", "event.reasoning", "event.chunk"],
        priority=10,
    ),
    ErrorPattern(
        name="method_not_callable",
        regex=r"'(.+)' object is not callable",
        description="将不可调用对象当作函数调用",
        root_cause_template="将类型为 '{value_type}' 的对象当作函数调用了",
        fix_suggestion_template="检查是否错误地引用了类属性而不是实例",
        search_keywords=["()", "invoke(", "call("],
        priority=8,
    ),
    ErrorPattern(
        name="attribute_error",
        regex=r"'(.+)' object has no attribute '(.+)'",
        description="对象没有指定的属性",
        root_cause_template="类型为 '{value_type}' 的对象没有属性 '{attribute}'",
        fix_suggestion_template="检查属性名是否正确，或对象类型是否正确",
        search_keywords=[".to_dict", ".value", ".content"],
        priority=5,
    ),
    ErrorPattern(
        name="type_error_format",
        regex=r"can only concatenate str \(not '(.+)'\) to str",
        description="字符串拼接类型错误",
        root_cause_template="尝试将 '{wrong_type}' 与字符串拼接",
        fix_suggestion_template="使用 str() 转换或检查变量类型",
        search_keywords=['"', "+"],
        priority=3,
    ),
]


# ============================================================================
# 诊断结果
# ============================================================================


@dataclass
class SuspiciousLocation:
    """可疑代码位置"""

    file_path: str
    line_number: int
    line_content: str
    match_reason: str
    confidence: float  # 0.0 - 1.0


@dataclass
class DiagnosisResult:
    """诊断结果"""

    error_pattern: ErrorPattern | None
    extracted_info: dict[str, str]
    suspicious_locations: list[SuspiciousLocation] = field(default_factory=list)
    root_cause: str = ""
    fix_suggestion: str = ""
    diagnosis_time_ms: float = 0.0

    def has_result(self) -> bool:
        return self.error_pattern is not None and len(self.suspicious_locations) > 0


# ============================================================================
# 诊断引擎
# ============================================================================


class PatternRegistry:
    """错误模式注册表"""

    def __init__(self) -> None:
        self._patterns: list[ErrorPattern] = []
        for p in BUILTIN_PATTERNS:
            self.register(p)

    def register(self, pattern: ErrorPattern) -> None:
        self._patterns.append(pattern)
        self._patterns.sort(key=lambda p: p.priority, reverse=True)

    def match(self, error_message: str) -> tuple[ErrorPattern | None, dict[str, str]]:
        """匹配错误消息，返回模式和提取的信息"""
        for pattern in self._patterns:
            match = re.search(pattern.regex, error_message, re.IGNORECASE)
            if match:
                groups = match.groups() if match.groups() else ()
                info = {}
                # 提取命名组或位置组
                if match.groupdict():
                    info = dict(match.groupdict())
                else:
                    for i, g in enumerate(groups):
                        info[f"group_{i}"] = g
                return pattern, info
        return None, {}


def diagnose_error(
    error_message: str,
    *,
    traceback_stack: list[str] | None = None,
    workspace_root: str = ".",
    max_results: int = 5,
) -> DiagnosisResult:
    """诊断错误，定位根因。

    Args:
        error_message: 错误消息
        traceback_stack: 调用栈信息（可选）
        workspace_root: 工作区根目录
        max_results: 最大返回结果数

    Returns:
        DiagnosisResult: 诊断结果
    """
    import time

    start = time.time()
    registry = PatternRegistry()

    # 1. 匹配错误模式
    pattern, extracted_info = registry.match(error_message)

    if not pattern:
        return DiagnosisResult(
            error_pattern=None,
            extracted_info={},
            root_cause="未知错误模式",
            fix_suggestion="无法自动诊断，请人工分析错误消息",
            diagnosis_time_ms=(time.time() - start) * 1000,
        )

    # 2. 收集调用栈中的可疑位置
    suspicious = []

    if traceback_stack:
        for frame in traceback_stack:
            for keyword in pattern.search_keywords:
                if keyword in frame:
                    suspicious.append(
                        SuspiciousLocation(
                            file_path=frame,
                            line_number=0,
                            line_content=frame,
                            match_reason=f"包含关键字: {keyword}",
                            confidence=0.8,
                        )
                    )

    # 3. 生成根因描述
    root_cause = pattern.root_cause_template
    for key, value in extracted_info.items():
        root_cause = root_cause.replace(f"{{{key}}}", str(value))

    fix_suggestion = pattern.fix_suggestion_template
    for key, value in extracted_info.items():
        fix_suggestion = fix_suggestion.replace(f"{{{key}}}", str(value))

    return DiagnosisResult(
        error_pattern=pattern,
        extracted_info=extracted_info,
        suspicious_locations=suspicious[:max_results],
        root_cause=root_cause,
        fix_suggestion=fix_suggestion,
        diagnosis_time_ms=(time.time() - start) * 1000,
    )


def diagnose_from_exception(exc: BaseException, **kwargs) -> DiagnosisResult:
    """从异常对象诊断错误"""
    return diagnose_error(
        error_message=str(exc),
        traceback_stack=tb_module.format_tb(exc.__traceback__),
        **kwargs,
    )
