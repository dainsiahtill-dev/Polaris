"""Error Category - 统一错误分类枚举

DEPRECATED: 请使用 polaris.kernelone.llm.engine 模块

定义 LLM 调用过程中可能遇到的错误类别。
"""

from __future__ import annotations

import inspect
import warnings
from enum import Enum

from polaris.kernelone.llm.engine import (
    KernelRepairCategory,
    NoRetryCategory,
    PlatformRetryCategory,
    get_retry_hint,
    is_kernel_repairable,
    is_platform_retryable,
    is_retryable,
    map_error_to_category,
    serialize_error,
)


def _should_emit_deprecation_warning() -> bool:
    """Emit deprecation warning only when imported from outside the kernel package.

    Internal callers (polaris.cells.roles.kernel.public and
    polaris.cells.roles.kernel.internal) get the re-export silently.
    """
    for frame_info in inspect.stack()[1:]:
        module_name = str(frame_info.frame.f_globals.get("__name__", ""))
        # Suppress if caller is inside the kernel package itself
        if module_name.startswith("polaris.cells.roles.kernel.public"):
            return False
        if module_name.startswith("polaris.cells.roles.kernel.internal"):
            return False
    # Emitted for all other callers (external consumers)
    return True


if _should_emit_deprecation_warning():
    warnings.warn(
        "polaris.cells.roles.kernel.public.service.error_category is deprecated. "
        "Use polaris.kernelone.llm.engine instead.",
        DeprecationWarning,
        stacklevel=2,
    )


# 保留旧枚举的别名以保持向后兼容
class _ErrorCategoryCompatibility(str, Enum):
    """错误类别枚举（兼容旧代码）

    DEPRECATED: 使用 PlatformRetryCategory, KernelRepairCategory, NoRetryCategory
    """

    # 网络相关
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"

    # 认证与授权
    AUTH = "auth"

    # 服务提供商
    PROVIDER = "provider"

    # 处理相关
    PARSE = "parse"
    QUALITY = "quality"
    TOOL = "tool"

    # 未知
    UNKNOWN = "unknown"


# 向后兼容的导出
ErrorCategory = _ErrorCategoryCompatibility

# 可自动重试的错误类别（兼容）
AUTO_RETRY_CATEGORIES = {
    PlatformRetryCategory.TIMEOUT,
    PlatformRetryCategory.RATE_LIMIT,
    PlatformRetryCategory.NETWORK_ERROR,
}

__all__ = [
    "AUTO_RETRY_CATEGORIES",
    "ErrorCategory",
    "KernelRepairCategory",
    "NoRetryCategory",
    "PlatformRetryCategory",
    "get_retry_hint",
    "is_kernel_repairable",
    "is_platform_retryable",
    "is_retryable",
    "map_error_to_category",
    "serialize_error",
]

# 有限重试的错误类别及最大重试次数
LIMITED_RETRY_CATEGORIES = {
    ErrorCategory.AUTH: 1,  # 认证失败最多重试1次
    ErrorCategory.PROVIDER: 2,  # 提供商错误最多重试2次
}

# 需要反馈修复的错误类别（错误信息发回给 LLM）
FEEDBACK_RETRY_CATEGORIES = {
    ErrorCategory.PARSE,
    ErrorCategory.QUALITY,
    ErrorCategory.TOOL,
}


def classify_error(error_str: str) -> ErrorCategory:
    """根据错误字符串分类错误类型

    Args:
        error_str: 错误信息字符串

    Returns:
        ErrorCategory 枚举值
    """
    error_lower = error_str.lower()

    if "timeout" in error_lower or "timed out" in error_lower:
        return ErrorCategory.TIMEOUT

    if "rate limit" in error_lower or "429" in error_lower or "too many requests" in error_lower:
        return ErrorCategory.RATE_LIMIT

    if "connection" in error_lower or "network" in error_lower or "dns" in error_lower or "socket" in error_lower:
        return ErrorCategory.NETWORK

    if (
        "auth" in error_lower
        or "api key" in error_lower
        or "unauthorized" in error_lower
        or "invalid token" in error_lower
    ):
        return ErrorCategory.AUTH

    if "model" in error_lower or "provider" in error_lower:
        return ErrorCategory.PROVIDER

    return ErrorCategory.UNKNOWN


def is_retryable_compat(category: ErrorCategory) -> bool:
    """判断错误是否可重试（兼容旧 API）

    Args:
        category: 错误类别

    Returns:
        是否可重试
    """
    if category in AUTO_RETRY_CATEGORIES:
        return True

    if category in LIMITED_RETRY_CATEGORIES:
        return True

    return category in FEEDBACK_RETRY_CATEGORIES


def get_max_retries(category: ErrorCategory, default: int = 3) -> int:
    """获取错误类别的最大重试次数

    Args:
        category: 错误类别
        default: 默认最大重试次数

    Returns:
        最大重试次数
    """
    if category in LIMITED_RETRY_CATEGORIES:
        return LIMITED_RETRY_CATEGORIES[category]

    if category in AUTO_RETRY_CATEGORIES:
        return default

    return 0
