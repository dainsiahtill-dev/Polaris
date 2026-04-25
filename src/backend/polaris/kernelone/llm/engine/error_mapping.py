"""Cross-domain Error Mapping - 统一错误分类映射层

本模块提供跨域错误分类映射，统一 LLM 错误与编排错误。

职责：
1. 定义平台层可重试错误类别
2. 定义内核层可修复错误类别
3. 提供错误分类映射函数
4. 支持跨域序列化
"""

from enum import Enum, auto


class PlatformRetryCategory(Enum):
    """平台层可重试错误（传输层）

    这类错误在平台层进行重试，不进入内核修复循环。
    """

    TIMEOUT = auto()  # 请求超时
    NETWORK_ERROR = auto()  # 网络连接错误
    RATE_LIMIT = auto()  # 速率限制
    SERVICE_UNAVAILABLE = auto()  # 服务不可用
    GATEWAY_TIMEOUT = auto()  # 网关超时


class KernelRepairCategory(Enum):
    """内核层可修复错误（语义层）

    这类错误在内核层进行修复重试，需要 LLM 反馈调整。
    """

    PARSE_ERROR = auto()  # 输出解析失败
    SCHEMA_VALIDATION_ERROR = auto()  # Schema 验证失败
    TOOL_NOT_FOUND = auto()  # 工具未找到
    TOOL_ARGUMENT_ERROR = auto()  # 工具参数错误
    QUALITY_CHECK_FAILED = auto()  # 质量检查失败


class NoRetryCategory(Enum):
    """不可重试错误

    这类错误不应重试，需要人工介入或代码修复。
    """

    AUTHENTICATION_ERROR = auto()  # 认证失败
    PERMISSION_DENIED = auto()  # 权限拒绝
    CONTEXT_LENGTH_EXCEEDED = auto()  # 上下文超长
    TOOL_EXECUTION_ERROR = auto()  # 工具执行错误（服务端 5xx）
    UNKNOWN_ERROR = auto()  # 未识别错误


def map_error_to_category(error: Exception) -> tuple[Enum, bool, str | None]:
    """将异常映射到错误分类

    Args:
        error: 要分类的异常

    Returns:
        Tuple of (分类, 是否可重试, 重试提示)
    """
    error_str = str(error).lower()

    # 平台层可重试错误
    if any(kw in error_str for kw in ["timeout", "timed out", "deadline exceeded"]):
        return PlatformRetryCategory.TIMEOUT, True, "请求超时，请稍后重试"

    if any(kw in error_str for kw in ["rate limit", "rate_limit", "too many requests", "quota exceeded"]):
        return PlatformRetryCategory.RATE_LIMIT, True, "速率限制，请等待后重试"

    if any(kw in error_str for kw in ["network", "connection", "connect", "dns", "refused"]):
        return PlatformRetryCategory.NETWORK_ERROR, True, "网络错误，请检查连接后重试"

    if any(kw in error_str for kw in ["503", "service unavailable", "service temporarily unavailable"]):
        return PlatformRetryCategory.SERVICE_UNAVAILABLE, True, "服务暂时不可用，请稍后重试"

    if any(kw in error_str for kw in ["504", "gateway timeout"]):
        return PlatformRetryCategory.GATEWAY_TIMEOUT, True, "网关超时，请稍后重试"

    # 内核层可修复错误
    if any(kw in error_str for kw in ["parse", "json decode", "json.decoder", "expecting value"]):
        return KernelRepairCategory.PARSE_ERROR, False, "输出解析失败，请调整输出格式"

    if any(kw in error_str for kw in ["validation", "schema", "required field", "field required"]):
        return KernelRepairCategory.SCHEMA_VALIDATION_ERROR, False, "Schema 验证失败，请检查输出格式"

    if any(kw in error_str for kw in ["tool not found", "tool not exist", "unknown tool"]):
        return KernelRepairCategory.TOOL_NOT_FOUND, False, "工具未找到，请检查工具名称"

    if any(kw in error_str for kw in ["tool argument", "invalid argument", "param error"]):
        return KernelRepairCategory.TOOL_ARGUMENT_ERROR, False, "工具参数错误，请检查参数格式"

    if any(kw in error_str for kw in ["quality", "below threshold", "insufficient quality"]):
        return KernelRepairCategory.QUALITY_CHECK_FAILED, False, "质量检查未通过，请改进输出"

    # 不可重试错误
    if any(kw in error_str for kw in ["permission", "forbidden", "access denied", "permission denied"]):
        return NoRetryCategory.PERMISSION_DENIED, False, "权限不足，请检查权限配置"

    if any(kw in error_str for kw in ["auth", "authentication", "unauthorized", "api key", "invalid token", "401"]):
        return NoRetryCategory.AUTHENTICATION_ERROR, False, "认证失败，请检查 API 密钥"

    if any(kw in error_str for kw in ["context length", "max tokens", "too long", "maximum context"]):
        return NoRetryCategory.CONTEXT_LENGTH_EXCEEDED, False, "上下文超长，请缩短输入"

    # 默认未知错误
    return NoRetryCategory.UNKNOWN_ERROR, False, "发生未知错误"


def is_platform_retryable(category: Enum) -> bool:
    """判断是否可在平台层重试"""
    return isinstance(category, PlatformRetryCategory)


def is_kernel_repairable(category: Enum) -> bool:
    """判断是否可在内核层修复"""
    return isinstance(category, KernelRepairCategory)


def is_retryable(category: Enum) -> bool:
    """判断是否可重试（平台层或内核层）"""
    return is_platform_retryable(category) or is_kernel_repairable(category)


def get_retry_hint(category: Enum) -> str | None:
    """获取重试提示"""
    hints = {
        PlatformRetryCategory.TIMEOUT: "请求超时，请稍后重试",
        PlatformRetryCategory.NETWORK_ERROR: "网络错误，请检查连接后重试",
        PlatformRetryCategory.RATE_LIMIT: "速率限制，请等待后重试",
        PlatformRetryCategory.SERVICE_UNAVAILABLE: "服务暂时不可用，请稍后重试",
        PlatformRetryCategory.GATEWAY_TIMEOUT: "网关超时，请稍后重试",
        KernelRepairCategory.PARSE_ERROR: "输出解析失败，请调整输出格式",
        KernelRepairCategory.SCHEMA_VALIDATION_ERROR: "Schema 验证失败，请检查输出格式",
        KernelRepairCategory.TOOL_NOT_FOUND: "工具未找到，请检查工具名称",
        KernelRepairCategory.TOOL_ARGUMENT_ERROR: "工具参数错误，请检查参数格式",
        KernelRepairCategory.QUALITY_CHECK_FAILED: "质量检查未通过，请改进输出",
        NoRetryCategory.AUTHENTICATION_ERROR: "认证失败，请检查 API 密钥",
        NoRetryCategory.PERMISSION_DENIED: "权限不足，请检查权限配置",
        NoRetryCategory.CONTEXT_LENGTH_EXCEEDED: "上下文超长，请缩短输入",
        NoRetryCategory.TOOL_EXECUTION_ERROR: "工具执行错误，请检查工具实现",
        NoRetryCategory.UNKNOWN_ERROR: "发生未知错误",
    }
    return hints.get(category)


def serialize_error(category: Enum) -> dict:
    """序列化为统一错误结构"""
    return {
        "error_category": category.name,
        "retryable": is_retryable(category),
        "retry_hint": get_retry_hint(category),
    }
